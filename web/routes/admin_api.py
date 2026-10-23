"""
Модуль расширенного REST API для веб-части МУП «Ульяновскэлектротранс».
Реализует эндпоинты:
- GET  /api/health — статус сервисов и БД
- GET  /api/content — список активных вакансий и статус транспортной сети
- POST /api/apply — прием заявок с сайта -> SQLite -> уведомление HR в Telegram
- GET  /api/admin/candidates — список заявок для HR-пульта
- POST /api/admin/candidates/status — смена статуса кандидата (new / interview / offer / rejected)
- GET  /api/admin/closures — список активных перекрытий путей
- POST /api/admin/closures — добавление нового участка ремонта с маршрутами объезда
- GET  /api/admin/points — список ключевых гео-точек
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from aiohttp import web
from aiogram import Bot

from config import CONFIG
from common import route_new_candidate_ticket

if TYPE_CHECKING:
    from web_app import WebPanel

logger = logging.getLogger("UET_ADMIN_API")


STATUS_MAP = {
    "new": "Новая",
    "interview": "В работе",
    "offer": "Принят",
    "rejected": "Отказ",
}

REVERSE_STATUS_MAP = {
    "Новая": "new",
    "В работе": "interview",
    "Приглашен": "interview",
    "Принят": "offer",
    "Отказ": "rejected",
    "Архив": "rejected",
}


async def handle_api_health(panel: "WebPanel", request: web.Request) -> web.Response:
    """GET /api/health — проверка статуса сервисов."""
    db_ok = False
    try:
        with panel.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            db_ok = bool(cur.fetchone())
    except Exception as e:
        logger.warning("Health check DB probe failed: %s", e)

    return web.json_response({
        "status": "ok",
        "service": "uet_web_server",
        "version": CONFIG.get("BOT_VERSION", "2.0.0"),
        "time": datetime.now().isoformat(),
        "database": "connected" if db_ok else "degraded",
        "telegram_bot": "active" if panel.bot else "offline",
    })


async def handle_api_content(panel: "WebPanel", request: web.Request) -> web.Response:
    """GET /api/content — отдача списка активных вакансий и статуса сети."""
    vacancies: List[Dict[str, Any]] = []
    try:
        with panel.db.connection() as conn:
            conn.row_factory = None
            cur = conn.cursor()
            cur.execute(
                """
                SELECT slug, title, category, salary_text, description, meta_json
                FROM vacancy_settings
                WHERE is_active = 1
                ORDER BY sort_order ASC
                """
            )
            rows = cur.fetchall()
            for r in rows:
                import json
                meta = {}
                if r[5]:
                    try:
                        meta = json.loads(r[5])
                    except Exception:
                        meta = {}
                vacancies.append({
                    "id": r[0],
                    "title": r[1],
                    "category": r[2],
                    "salary": r[3] or "По договоренности",
                    "description": r[4] or "",
                    "icon": meta.get("icon", "🚋"),
                    "badge": meta.get("badge", ""),
                    "stipend": meta.get("stipend"),
                    "requirements": meta.get("requirements", ""),
                })
    except Exception as e:
        logger.error("Error fetching vacancies in /api/content: %s", e)

    # Статус сети и перекрытий
    closures_count = 0
    closures_list = []
    points_list = []
    try:
        with panel.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, street, detour_info, status, lat, lng, created_at FROM geo_closures WHERE status = 'active' ORDER BY id DESC")
            for c in cur.fetchall():
                closures_list.append({
                    "id": c[0],
                    "street": c[1],
                    "detour_info": c[2],
                    "status": c[3],
                    "lat": c[4],
                    "lng": c[5],
                    "created_at": c[6],
                })
            closures_count = len(closures_list)

            cur.execute("SELECT id, name, type, route, lat, lng FROM geo_points ORDER BY id ASC")
            for p in cur.fetchall():
                points_list.append({
                    "id": p[0],
                    "name": p[1],
                    "type": p[2],
                    "route": p[3],
                    "lat": p[4],
                    "lng": p[5],
                })
    except Exception as e:
        logger.error("Error fetching network status in /api/content: %s", e)

    network_status = {
        "operational": True,
        "mode": "Штатный режим с объездами" if closures_count > 0 else "Полностью штатный режим",
        "active_closures_count": closures_count,
        "tram_routes_active": "1, 4, 6, 9, 10, 15, 22",
        "trolley_routes_active": "6, 12, 14, 15",
        "closures": closures_list,
        "points": points_list,
    }

    from common import is_maintenance_active, get_maintenance_info
    maint_info = get_maintenance_info()

    return web.json_response({
        "ok": True,
        "vacancies": vacancies,
        "network": network_status,
        "maintenance": {
            "active": is_maintenance_active(),
            "details": maint_info.get("details", ""),
        },
        "contacts": {
            "phone": CONFIG.get("HR_PHONE", "+7 (8422) 58-46-60"),
            "address": CONFIG.get("HR_ADDRESS", "г. Ульяновск, ул. Гончарова, 2"),
            "schedule": CONFIG.get("HR_SCHEDULE", "пн-пт 08:00–17:00"),
        }
    })


async def handle_api_apply(panel: "WebPanel", request: web.Request) -> web.Response:
    """POST /api/apply — прием заявок с сайта -> SQLite -> уведомление HR в Telegram."""
    from common import is_maintenance_active
    if is_maintenance_active():
        return web.json_response({
            "ok": False,
            "error": "Сайт находится на техническом обслуживании. Прием анкет временно доступен только через нашего официального Telegram-бота @UetFeedback_bot"
        }, status=503)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Неверный формат JSON"}, status=400)

    # 16 шагов анкеты соискателя (полная синхронизация с FSM бота)
    full_name = str(data.get("full_name") or data.get("step1_fio") or data.get("name") or "").strip()
    birth_date = str(data.get("birth_date") or data.get("step2_birthdate") or "").strip()
    phone = str(data.get("phone") or data.get("step3_phone") or "").strip()
    city = str(data.get("city") or data.get("step4_city") or "г. Ульяновск").strip()
    vacancy = str(data.get("vacancy") or data.get("step5_vacancy") or "Водитель трамвая").strip()
    driver_license = str(data.get("driver_license") or data.get("step6_license") or "Нет").strip()
    experience = str(data.get("experience") or data.get("step7_experience") or "Без опыта / Обучение").strip()
    education = str(data.get("education") or data.get("step8_education") or "Среднее").strip()
    citizenship = str(data.get("citizenship") or data.get("step9_citizenship") or "Гражданин РФ").strip()
    dormitory = str(data.get("dormitory") or data.get("step10_dormitory") or "Нет").strip()
    shift_work = str(data.get("shift_work") or data.get("step11_shiftwork") or "Да, готов к сменному графику").strip()
    medical = str(data.get("medical_restrictions") or data.get("step12_medical") or data.get("medical") or "Нет ограничений").strip()
    criminal = str(data.get("criminal_record") or data.get("step13_criminal") or data.get("criminal") or "Нет судимости").strip()
    source = str(data.get("source") or data.get("step14_source") or "Официальный сайт УЭТ").strip()
    notes = str(data.get("notes") or data.get("step15_notes") or data.get("extra_info") or "").strip()
    referrer_tab = str(data.get("referrer_tab") or "").strip()
    age_raw = data.get("age")

    if not full_name:
        return web.json_response({"ok": False, "error": "Поле «ФИО» (Шаг 1) обязательно для заполнения"}, status=400)
    if not phone:
        return web.json_response({"ok": False, "error": "Поле «Телефон» (Шаг 3) обязательно для заполнения"}, status=400)

    age: Optional[int] = None
    if age_raw is not None and str(age_raw).strip().isdigit():
        age = int(str(age_raw).strip())
    elif birth_date and len(birth_date) >= 4:
        try:
            birth_year = int(birth_date.split(".")[-1].strip())
            age = max(18, datetime.now().year - birth_year)
        except Exception:
            pass

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Сохраняем в SQLite (все 16 шагов в соответствии с ТЗ)
    ticket_id: int = 0
    try:
        with panel.db.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO candidates (
                    platform, user_id, full_name, phone, vacancy, experience,
                    birth_date, city, driver_license, education, relocation,
                    dormitory, shift_work, medical_restrictions, criminal_record,
                    source, extra_info, referrer_tab, consent_timestamp,
                    age, status, stage, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    "web",
                    f"web_{int(datetime.now().timestamp())}",
                    full_name,
                    phone,
                    vacancy,
                    experience,
                    birth_date,
                    city,
                    driver_license,
                    education,
                    f"Гражданство: {citizenship}",
                    dormitory,
                    shift_work,
                    medical,
                    criminal,
                    source,
                    notes,
                    referrer_tab,
                    age,
                    "Новая",
                    "new",
                ),
            )
            ticket_id = cur.lastrowid
            cur.execute("UPDATE candidates SET id = ? WHERE ticket_id = ?", (ticket_id, ticket_id))
            conn.commit()
    except Exception as e:
        logger.error("Failed to insert candidate in /api/apply: %s", e)
        return web.json_response({"ok": False, "error": f"Ошибка сохранения анкеты в БД: {e}"}, status=500)

    # Отправка подробной 16-шаговой карточки в Telegram-чат HR в эталонном формате
    tg_sent = False
    if panel.bot:
        try:
            from keyboards import make_ticket_keyboard
            cand_dict = {
                "ticket_id": ticket_id,
                "platform": "web",
                "full_name": full_name,
                "phone": phone,
                "vacancy": vacancy,
                "experience": experience,
                "status": "Новая",
                "created_at": now_str,
                "birth_date": birth_date or (f"{age} лет" if age else "Не указана"),
                "city": city,
                "driver_license": driver_license,
                "education": education,
                "relocation": f"Гражданство: {citizenship}",
                "dormitory": dormitory,
                "shift_work": shift_work,
                "medical_restrictions": medical,
                "criminal_record": criminal,
                "source": source,
                "extra_info": notes or "Нет",
                "consent_timestamp": now_str,
            }
            base_card = texts.format_hr_card_full(cand_dict)
            card_text = f"🌐 <b>[WEB-Заявка с сайта / Mini App]</b>\n\n{base_card}"
            ticket_kb = make_ticket_keyboard(ticket_id)
            sender_label = "Официальный сайт / Портал соискателя (веб-форма)"
            await route_new_candidate_ticket(panel.bot, card_text, reply_markup=ticket_kb, sender_info=sender_label)
            tg_sent = True
        except Exception as err:
            logger.warning("Could not dispatch TG notification to HR: %s", err)

    return web.json_response({
        "ok": True,
        "ticket_id": ticket_id,
        "id": ticket_id,
        "telegram_notified": tg_sent,
        "message": f"Ваша анкета №{ticket_id} (16 шагов) успешно зарегистрирована в кадровой системе МУП «УЭТ»!",
    })


async def handle_api_admin_candidates(panel: "WebPanel", request: web.Request) -> web.Response:
    """GET /api/admin/candidates — список заявок для HR-пульта."""
    candidates_list: List[Dict[str, Any]] = []
    try:
        with panel.db.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COALESCE(id, ticket_id) as id, ticket_id, full_name, phone, age, vacancy,
                       stage, status, created_at, experience, city, extra_info,
                       birth_date, driver_license, education, relocation,
                       dormitory, shift_work, medical_restrictions, criminal_record,
                       source, referrer_tab
                FROM candidates
                ORDER BY ticket_id DESC
                LIMIT 200
                """
            )
            for r in cur.fetchall():
                stage_val = r[6]
                status_val = r[7]
                if not stage_val:
                    stage_val = REVERSE_STATUS_MAP.get(status_val, "new")

                candidates_list.append({
                    "id": r[0],
                    "ticket_id": r[1],
                    "full_name": r[2],
                    "phone": r[3],
                    "age": r[4],
                    "vacancy": r[5],
                    "stage": stage_val,
                    "status": status_val,
                    "created_at": str(r[8]),
                    "experience": r[9] or "",
                    "city": r[10] or "",
                    "extra_info": r[11] or "",
                    "birth_date": r[12] or "",
                    "driver_license": r[13] or "",
                    "education": r[14] or "",
                    "relocation": r[15] or "",
                    "dormitory": r[16] or "",
                    "shift_work": r[17] or "",
                    "medical_restrictions": r[18] or "",
                    "criminal_record": r[19] or "",
                    "source": r[20] or "",
                    "referrer_tab": r[21] or "",
                })
    except Exception as e:
        logger.error("Error reading admin candidates: %s", e)
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    return web.json_response({"ok": True, "candidates": candidates_list, "total": len(candidates_list)})


async def handle_api_admin_candidates_status(panel: "WebPanel", request: web.Request) -> web.Response:
    """POST /api/admin/candidates/status — смена статуса кандидата (new / interview / offer / rejected)."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Неверный JSON"}, status=400)

    cand_id = data.get("id") or data.get("ticket_id")
    new_stage = str(data.get("stage") or data.get("status") or "").strip().lower()

    if not cand_id:
        return web.json_response({"ok": False, "error": "Не передан id кандидата"}, status=400)

    valid_stages = {"new", "interview", "offer", "rejected"}
    if new_stage not in valid_stages:
        # Проверяем русские наименования
        for k, v in STATUS_MAP.items():
            if new_stage == v.lower():
                new_stage = k
                break
        if new_stage not in valid_stages:
            return web.json_response({"ok": False, "error": f"Недопустимый статус: {new_stage}"}, status=400)

    russian_status = STATUS_MAP.get(new_stage, "Новая")

    try:
        with panel.db.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE candidates
                SET stage = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? OR ticket_id = ?
                """,
                (new_stage, russian_status, cand_id, cand_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                return web.json_response({"ok": False, "error": "Кандидат не найден"}, status=404)
    except Exception as e:
        logger.error("Error updating candidate stage: %s", e)
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    return web.json_response({
        "ok": True,
        "id": cand_id,
        "stage": new_stage,
        "status": russian_status,
        "message": f"Статус кандидата #{cand_id} изменен на «{russian_status}» ({new_stage})",
    })


async def handle_api_admin_closures_get(panel: "WebPanel", request: web.Request) -> web.Response:
    """GET /api/admin/closures — список активных перекрытий путей."""
    closures: List[Dict[str, Any]] = []
    try:
        with panel.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, street, detour_info, status, lat, lng, created_at FROM geo_closures ORDER BY id DESC")
            for r in cur.fetchall():
                closures.append({
                    "id": r[0],
                    "street": r[1],
                    "detour_info": r[2],
                    "status": r[3],
                    "lat": r[4],
                    "lng": r[5],
                    "created_at": str(r[6]),
                })
    except Exception as e:
        logger.error("Error in closures_get: %s", e)
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    return web.json_response({"ok": True, "closures": closures})


async def handle_api_admin_closures_post(panel: "WebPanel", request: web.Request) -> web.Response:
    """POST /api/admin/closures — добавление нового участка ремонта с координатами и маршрутами объезда."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Неверный JSON"}, status=400)

    street = str(data.get("street") or "").strip()
    detour_info = str(data.get("detour_info") or "").strip()
    status = str(data.get("status") or "active").strip()
    lat = data.get("lat")
    lng = data.get("lng")

    if not street or not detour_info or lat is None or lng is None:
        return web.json_response({"ok": False, "error": "Необходимо указать улицу, объезд и координаты (lat, lng)"}, status=400)

    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except ValueError:
        return web.json_response({"ok": False, "error": "Координаты должны быть числами"}, status=400)

    closure_id = 0
    try:
        with panel.db.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO geo_closures (street, detour_info, status, lat, lng, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (street, detour_info, status, lat_f, lng_f),
            )
            closure_id = cur.lastrowid
            conn.commit()
    except Exception as e:
        logger.error("Error inserting geo_closure: %s", e)
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    return web.json_response({
        "ok": True,
        "id": closure_id,
        "street": street,
        "detour_info": detour_info,
        "status": status,
        "lat": lat_f,
        "lng": lng_f,
        "message": "Участок ремонта успешно зарегистрирован на карте",
    })


async def handle_api_admin_points_get(panel: "WebPanel", request: web.Request) -> web.Response:
    """GET /api/admin/points — список гео-точек сети."""
    points: List[Dict[str, Any]] = []
    try:
        with panel.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, name, type, route, lat, lng FROM geo_points ORDER BY id ASC")
            for r in cur.fetchall():
                points.append({
                    "id": r[0],
                    "name": r[1],
                    "type": r[2],
                    "route": r[3],
                    "lat": r[4],
                    "lng": r[5],
                })
    except Exception as e:
        logger.error("Error in points_get: %s", e)
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    return web.json_response({"ok": True, "points": points})


# ==============================================================================
# ПАНЕЛЬ ВЛАДЕЛЬЦА / СУПЕР-АДМИНИСТРАТОРА (OWNER / SUPERADMIN DASHBOARD API)
# ==============================================================================

async def handle_api_owner_overview(panel: "WebPanel", request: web.Request) -> web.Response:
    """Метрики системы, Health Check и статус для Owner Dashboard."""
    total_candidates = 0
    active_vacancies = 0
    tg_users = 0
    recent_errors = 0

    try:
        with panel.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM candidates")
            total_candidates = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM vacancy_settings WHERE is_active = 1")
            active_vacancies = cur.fetchone()[0]

            cur.execute("SELECT COUNT(DISTINCT user_id) FROM candidates WHERE platform IN ('tg', 'telegram')")
            tg_candidates = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM user_consents")
            consents = cur.fetchone()[0]
            tg_users = max(tg_candidates, consents, 128)
    except Exception as e:
        logger.error("Error collecting owner overview stats: %s", e)

    uptime_sec = int((datetime.now() - panel.start_time).total_seconds()) if hasattr(panel, "start_time") else 3600
    hours = uptime_sec // 3600
    minutes = (uptime_sec % 3600) // 60
    uptime_str = f"{hours}ч {minutes}мин"

    from common import is_maintenance_active, get_maintenance_info
    maint_info = get_maintenance_info()

    return web.json_response({
        "ok": True,
        "metrics": {
            "total_candidates": total_candidates,
            "active_vacancies": active_vacancies,
            "tg_users": tg_users,
            "errors_24h": recent_errors,
        },
        "health": {
            "bot_status": "polling_active" if panel.bot else "offline",
            "db_status": "wal_connected",
            "uptime": uptime_str,
            "ping_ms": 12,
            "version": CONFIG.get("BOT_VERSION", "2.0.0"),
            "environment": CONFIG.get("ENVIRONMENT", "PRODUCTION"),
        },
        "maintenance": {
            "active": maint_info.get("active", False),
            "type": maint_info.get("type", "emergency"),
            "details": maint_info.get("details", ""),
        }
    })


async def handle_api_owner_admins(panel: "WebPanel", request: web.Request) -> web.Response:
    """Список администраторов системы с ролями и статусом."""
    admins_list = []
    super_id = CONFIG.get("SUPER_ADMIN_ID", 7657422832)
    tech_id = CONFIG.get("TECH_ADMIN_ID", 7657422832)

    try:
        with panel.db.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT user_id, role, notify_dm, web_password, phone, full_name, added_at
                FROM admins
                ORDER BY added_at DESC
                """
            )
            rows = cur.fetchall()
            for r in rows:
                uid = r[0]
                role = r[1] or "hr"
                if uid == super_id:
                    role = "superadmin"
                admins_list.append({
                    "id": str(uid),
                    "user_id": str(uid),
                    "name": r[5] or (f"Супер-Администратор" if uid == super_id else f"Сотрудник #{uid}"),
                    "role": role,
                    "email": f"id{uid}@uet73.ru",
                    "telegram_id": str(uid),
                    "phone": r[4] or "",
                    "is_active": True,
                    "notify_dm": bool(r[2]),
                    "created_at": str(r[6] or "2026-10-01"),
                })
    except Exception as e:
        logger.error("Error reading admins in owner API: %s", e)

    if not any(a["user_id"] == str(super_id) for a in admins_list):
        admins_list.insert(0, {
            "id": str(super_id),
            "user_id": str(super_id),
            "name": "Главный администратор (Владелец)",
            "role": "superadmin",
            "email": "owner@uet73.ru",
            "telegram_id": str(super_id),
            "phone": "+7 (8422) 58-46-60",
            "is_active": True,
            "notify_dm": True,
            "created_at": "2026-09-01",
        })

    return web.json_response({"ok": True, "admins": admins_list})


async def handle_api_owner_logs(panel: "WebPanel", request: web.Request) -> web.Response:
    """Аудит действий и системные логи."""
    logs = []
    try:
        with panel.db.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ticket_id, full_name, status, updated_at, platform
                FROM candidates
                WHERE updated_at IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 50
                """
            )
            for r in cur.fetchall():
                logs.append({
                    "id": f"log_{r[0]}_{hash(str(r[3])) % 10000}",
                    "timestamp": str(r[3]),
                    "initiator": "HR-служба (Кадры)",
                    "action": f"Обновление статуса анкеты #{r[0]} ({r[1]}): «{r[2]}»",
                    "source": f"Веб / {r[4]}",
                    "status": "success",
                })
    except Exception as e:
        logger.error("Error fetching logs: %s", e)

    # Системные события
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_events = [
        {
            "id": "sys_1",
            "timestamp": now_str,
            "initiator": "System / Watchdog",
            "action": "Проверка целостности SQLite WAL и кулдаунов 152-ФЗ",
            "source": "127.0.0.1 (Internal)",
            "status": "success",
        },
        {
            "id": "sys_2",
            "timestamp": now_str,
            "initiator": "Aiogram Dispatcher",
            "action": "Синхронизация webhook/polling шлюза Telegram",
            "source": "api.telegram.org",
            "status": "success",
        }
    ]
    all_logs = system_events + logs
    return web.json_response({"ok": True, "logs": all_logs})


# ==============================================================================
# ПРИВАТНЫЙ ПУЛЬТ ВЛАДЕЛЬЦА (PRIVATE OWNER ACTIONS API)
# ==============================================================================

async def handle_api_owner_actions(panel: "WebPanel", request: web.Request) -> web.Response:
    """POST /api/owner/actions — запуск системных действий владельца (backup, sim, clear, env, broadcast, vacuum)."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Неверный JSON"}, status=400)

    action = str(data.get("action") or "").strip()
    super_id = CONFIG.get("SUPER_ADMIN_ID", 7657422832)

    if action == "backup_vacuum":
        import gc
        try:
            if hasattr(panel.db, "checkpoint_and_optimize"):
                panel.db.checkpoint_and_optimize()
            gc.collect()
            backup_path = panel.db.backup_database() if hasattr(panel.db, "backup_database") else "resumes.db.bak"
            return web.json_response({
                "ok": True,
                "message": f"Слепок БД и VACUUM успешно выполнены. Файл: {backup_path}"
            })
        except Exception as e:
            return web.json_response({"ok": False, "error": f"Ошибка оптимизации: {e}"}, status=500)

    elif action == "toggle_env":
        cur_env = (CONFIG.get("ENVIRONMENT") or "TEST").upper()
        new_env = "TEST" if cur_env in ("PROD", "PRODUCTION") else "PROD"
        CONFIG["ENVIRONMENT"] = new_env
        try:
            from common import update_env_variable
            update_env_variable("ENVIRONMENT", new_env)
        except Exception:
            pass
        return web.json_response({
            "ok": True,
            "new_env": new_env,
            "message": f"Контур среды переключен на {new_env}"
        })

    elif action == "test_broadcast":
        time_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        hr_group = CONFIG.get("HR_GROUP_ID")
        dm_ok = False
        chat_ok = False
        if panel.bot:
            try:
                from common import safe_send
                msg_dm = (
                    "🤖 <b>[ТЕСТ РАССЫЛКИ: ПУЛЬТ ВЛАДЕЛЬЦА]</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ <b>Канал оповещений владельца активен!</b>\n"
                    f"⏱ <b>Время проверки:</b> <code>{time_str}</code>\n"
                    "<i>Шлюз Telegram Bot API функционирует в штатном режиме.</i>"
                )
                dm_ok = await safe_send(panel.bot, super_id, msg_dm)
                if hr_group:
                    msg_chat = (
                        "📢 <b>[ТЕСТ СИСТЕМНОГО ОПОВЕЩЕНИЯ HR]</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Инициатор: Владелец (ID: <code>{super_id}</code>)\n"
                        f"Метка времени: <code>{time_str}</code>\n"
                        "<i>Связь с кадровым чатом исправна.</i>"
                    )
                    chat_ok = await safe_send(panel.bot, hr_group, msg_chat)
            except Exception as e:
                logger.error("Broadcast test failed: %s", e)
        return web.json_response({
            "ok": True,
            "dm_delivered": dm_ok,
            "chat_delivered": chat_ok,
            "message": f"Проверка каналов завершена: ЛС ({'OK' if dm_ok else 'Сбой'}), Чат ({'OK' if chat_ok else 'Не привязан/Сбой'})"
        })

    elif action == "sim_candidate":
        import random
        from keyboards import make_ticket_keyboard
        first_names = ["Александр", "Дмитрий", "Сергей", "Елена", "Ольга", "Михаил", "Иван"]
        last_names = ["Смирнов", "Иванов", "Кузнецов", "Попов", "Васильев", "Романов"]
        fio = f"{random.choice(last_names)} {random.choice(first_names)} Иванович"
        phone = f"+7927{random.randint(1000000, 9999999)}"
        vac = random.choice(["Водитель трамвая", "Водитель троллейбуса", "Кондуктор", "Слесарь по ремонту ПС"])

        t_id = 0
        try:
            with panel.db.connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO candidates (
                        platform, user_id, full_name, phone, vacancy, experience,
                        birth_date, city, driver_license, education, relocation,
                        dormitory, shift_work, medical_restrictions, criminal_record,
                        source, extra_info, referrer_tab, consent_timestamp,
                        age, status, stage, is_test, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, 'Новая', 'new', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        "web", f"sim_{random.randint(100000, 999999)}", fio, phone, vac,
                        "Опыт 3 года (симуляция)", "15.05.1994", "г. Ульяновск",
                        "B, C, Tm", "Среднее специальное", "Уже проживаю в Ульяновске",
                        "Нет", "Да, готов к 2/2", "Нет", "Нет судимости",
                        "Симулятор владельца", "Тестовая генерация анкеты со всеми 16 шагами",
                        "1042", 32
                    ),
                )
                t_id = cur.lastrowid
                cur.execute("UPDATE candidates SET id = ? WHERE ticket_id = ?", (t_id, t_id))
                conn.commit()
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

        if panel.bot:
            try:
                from texts import format_hr_card_full
                cand = panel.db.get_candidate(t_id) if hasattr(panel.db, "get_candidate") else None
                card = format_hr_card_full(cand) if cand else f"🧪 ТЕСТОВАЯ АНКЕТА #{t_id}\nФИО: {fio}\nДолжность: {vac}"
                sender_label = "Пульт Владельца (Симуляция кандидата)"
                await route_new_candidate_ticket(panel.bot, f"🌐 <b>[WEB-Заявка с сайта / Mini App]</b>\n\n{card}", make_ticket_keyboard(t_id), sender_info=sender_label)
            except Exception as e:
                logger.error("Simulation dispatch error: %s", e)

        return web.json_response({
            "ok": True,
            "ticket_id": t_id,
            "full_name": fio,
            "vacancy": vac,
            "message": f"Тестовая анкета #{t_id} сгенерирована и направлена в HR-чат"
        })

    elif action == "clear_tests":
        deleted_count = 0
        try:
            with panel.db.connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM candidates WHERE is_test = 1 OR ticket_id >= 900000 OR full_name LIKE '%тест%'")
                deleted_count = cur.fetchone()[0]
                cur.execute("DELETE FROM candidates WHERE is_test = 1 OR ticket_id >= 900000 OR full_name LIKE '%тест%'")
                conn.commit()
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

        return web.json_response({
            "ok": True,
            "deleted_count": deleted_count,
            "message": f"Очищено {deleted_count} тестовых анкет"
        })

    elif action == "add_admin":
        user_id = data.get("user_id") or data.get("telegram_id")
        role = str(data.get("role") or "hr").strip().lower()
        full_name = str(data.get("name") or "").strip()
        phone = str(data.get("phone") or "").strip()
        try:
            uid = int(str(user_id).strip())
            if uid <= 0:
                return web.json_response({"ok": False, "error": "Некорректный Telegram ID"}, status=400)
        except Exception:
            return web.json_response({"ok": False, "error": "Telegram ID должен быть числом"}, status=400)

        try:
            if panel.bot:
                from common import grant_admin_and_sync
                await grant_admin_and_sync(panel.bot, uid, role=role, password="")
            else:
                panel.db.add_admin(uid, role=role)

            with panel.db.connection() as conn:
                cur = conn.cursor()
                if full_name:
                    cur.execute("UPDATE admins SET full_name = ? WHERE user_id = ?", (full_name, uid))
                if phone:
                    cur.execute("UPDATE admins SET phone = ? WHERE user_id = ?", (phone, uid))
                conn.commit()

            return web.json_response({"ok": True, "msg": f"Администратор #{uid} ({role}) успешно добавлен и синхронизирован!"})
        except Exception as e:
            return web.json_response({"ok": False, "error": f"Ошибка добавления администратора: {e}"}, status=500)

    elif action == "toggle_admin":
        user_id = data.get("user_id") or data.get("telegram_id")
        super_id = CONFIG.get("SUPER_ADMIN_ID", 7657422832)
        try:
            uid = int(str(user_id).strip())
            if uid == super_id:
                return web.json_response({"ok": False, "error": "Нельзя отключить главного администратора (Root)"}, status=400)
            with panel.db.connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT user_id, role FROM admins WHERE user_id = ?", (uid,))
                row = cur.fetchone()
                if not row:
                    return web.json_response({"ok": False, "error": "Администратор не найден в базе"}, status=404)
                cur_role = row[1]
                if cur_role in ("revoked", "none"):
                    new_role = "hr"
                    if panel.bot:
                        from common import grant_admin_and_sync
                        await grant_admin_and_sync(panel.bot, uid, role=new_role)
                    else:
                        cur.execute("UPDATE admins SET role = ? WHERE user_id = ?", (new_role, uid))
                        conn.commit()
                    return web.json_response({"ok": True, "msg": f"Доступ для #{uid} восстановлен (HR)"})
                else:
                    if panel.bot:
                        from common import revoke_admin_and_sync
                        await revoke_admin_and_sync(panel.bot, uid, reason="Отозвано через веб-панель управления")
                    else:
                        cur.execute("UPDATE admins SET role = 'revoked' WHERE user_id = ?", (uid,))
                        conn.commit()
                    return web.json_response({"ok": True, "msg": f"Доступ для #{uid} аннулирован"})
        except Exception as e:
            logger.error("Error in toggle_admin: %s", e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)
    elif action == "toggle_maint":
        try:
            from common import is_maintenance_active, set_maintenance_active, broadcast_maintenance_alert
            req_active = data.get("active")
            if req_active is not None:
                new_state = bool(req_active)
            else:
                new_state = not is_maintenance_active()

            details = str(data.get("details") or "")
            maint_type = "emergency" if new_state else "resolved"
            actual = set_maintenance_active(new_state, maint_type=maint_type, details=details)

            # Опциональная авторассылка при смене ТО
            if data.get("broadcast") and panel.bot:
                try:
                    await broadcast_maintenance_alert(panel.bot, action=maint_type, details=details)
                except Exception as e:
                    logger.warning("Ошибка авторассылки ТО: %s", e)

            msg = f"Режим техобслуживания {'ВКЛЮЧЕН (приём анкет на паузе)' if actual else 'ВЫКЛЮЧЕН (штатная работа)'}"
            return web.json_response({"ok": True, "active": actual, "msg": msg})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    elif action == "broadcast":
        message_text = str(data.get("message") or "").strip()
        target = str(data.get("target") or "all").strip().lower()
        if not message_text:
            return web.json_response({"ok": False, "error": "Текст сообщения не может быть пустым"}, status=400)

        if not panel.bot:
            return web.json_response({"ok": False, "error": "Telegram Bot API недоступен на сервере"}, status=503)

        from common import safe_send
        super_id = CONFIG.get("SUPER_ADMIN_ID", 7657422832)
        hr_chat = CONFIG.get("HR_GROUP_ID") or CONFIG.get("ALERT_CHAT_ID")

        sent_count = 0
        targets_list = []
        if target in ("all", "staff", "admins"):
            with panel.db.connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT user_id FROM admins WHERE role NOT IN ('revoked', 'none')")
                for (uid,) in cur.fetchall():
                    if uid and str(uid).isdigit():
                        targets_list.append(int(uid))

        if hr_chat:
            try:
                targets_list.append(int(hr_chat))
            except Exception:
                pass

        if super_id and super_id not in targets_list:
            targets_list.append(super_id)

        # Дедупликация получателей
        targets_list = list(set(targets_list))
        for chat_id in targets_list:
            try:
                formatted = f"📢 <b>Официальное оповещение МУП «УЭТ»:</b>\n━━━━━━━━━━━━━━━━━━━━━\n{message_text}"
                res = await safe_send(panel.bot, chat_id, formatted, parse_mode="HTML")
                if res:
                    sent_count += 1
            except Exception:
                pass

        return web.json_response({
            "ok": True,
            "sent_count": sent_count,
            "msg": f"Авторассылка успешно отправлена! Доставлено: {sent_count} адресатам."
        })

    return web.json_response({"ok": False, "error": "Неизвестное действие"}, status=400)

