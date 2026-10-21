"""
Маршрутизаторы работы с кандидатами, вакансиями и воронкой 16 шагов МУП «УЭТ».
"""

from __future__ import annotations

import html
import logging
import os
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, List, Tuple

from aiohttp import web

import texts
from common import (
    CONFIG,
    get_all_vacancies,
    get_closed_vacancies,
    get_vacancy_icon,
    is_maintenance_active,
    route_new_candidate_ticket,
    safe_send,
    save_all_vacancies,
    send_response_to_candidate,
    set_closed_vacancies,
)
from keyboards import make_ticket_keyboard
from services.candidate_service import candidate_service
from services.compliance_service import ComplianceService

if TYPE_CHECKING:
    from web_app import WebPanel

logger = logging.getLogger("UET_WEB_CANDIDATES")


async def handle_api_candidate_lookup(panel: WebPanel, request: web.Request) -> web.Response:
    """Поиск статуса анкеты соискателем по номеру заявки, номеру телефона или ФИО (в обоих контурах: PROD и TEST)."""
    q = request.query.get("query") or request.query.get("ticket_id") or request.query.get("phone") or ""
    if not q and request.method == "POST":
        try:
            body = await request.json()
            q = str(body.get("query") or body.get("ticket_id") or body.get("phone") or "")
        except Exception:
            pass
    q = str(q).strip()
    if not q:
        return web.json_response({"ok": False, "error": "Укажите номер анкеты или номер телефона"}, status=400)

    cand = None
    cleaned_num = q.replace("#", "").replace("№", "").replace("73-", "").replace("в-", "").replace("В-", "").strip()
    clean_digits = re.sub(r"\D", "", q)

    if cleaned_num.isdigit() and len(cleaned_num) <= 8:
        try:
            cand = panel.db.get_candidate(int(cleaned_num))
        except Exception as e:
            logger.warning("Ошибка get_candidate по ID %s: %s", cleaned_num, e)

    if not cand:
        try:
            with panel.db.connection() as conn:
                cur = conn.cursor()
                if len(clean_digits) >= 10:
                    cur.execute(
                        """
                        SELECT ticket_id, platform, user_id, full_name, phone, vacancy, experience, status, admin_note, created_at,
                               birth_date, city, driver_license, education, relocation, dormitory, shift_work,
                               medical_restrictions, criminal_record, source, extra_info, consent_timestamp, raw_data_json,
                               interview_depot, interview_time, interview_status
                        FROM candidates WHERE phone LIKE ? ORDER BY ticket_id DESC LIMIT 1
                        """,
                        (f"%{clean_digits[-10:]}%",),
                    )
                    row = cur.fetchone()
                    if row:
                        cand = row

                if not cand and clean_digits:
                    cur.execute(
                        """
                        SELECT ticket_id, platform, user_id, full_name, phone, vacancy, experience, status, admin_note, created_at,
                               birth_date, city, driver_license, education, relocation, dormitory, shift_work,
                               medical_restrictions, criminal_record, source, extra_info, consent_timestamp, raw_data_json,
                               interview_depot, interview_time, interview_status
                        FROM candidates WHERE ticket_id = ? ORDER BY ticket_id DESC LIMIT 1
                        """,
                        (clean_digits,),
                    )
                    row = cur.fetchone()
                    if row:
                        cand = row

                if not cand and len(q) >= 3 and not q.isdigit():
                    cur.execute(
                        """
                        SELECT ticket_id, platform, user_id, full_name, phone, vacancy, experience, status, admin_note, created_at,
                               birth_date, city, driver_license, education, relocation, dormitory, shift_work,
                               medical_restrictions, criminal_record, source, extra_info, consent_timestamp, raw_data_json,
                               interview_depot, interview_time, interview_status
                        FROM candidates WHERE full_name LIKE ? ORDER BY ticket_id DESC LIMIT 1
                        """,
                        (f"%{q}%",),
                    )
                    row = cur.fetchone()
                    if row:
                        cand = row
        except Exception as err:
            logger.warning("Ошибка поиска в базе: %s", err)

    if not cand:
        return web.json_response(
            {"ok": False, "error": "Анкета не найдена. Проверьте правильность номера или подайте новую заявку."},
            status=404,
        )

    t_id, plat, uid, fn, ph, vac, exp, st, note, cr = cand[:10]
    depot = cand[23] if len(cand) > 23 else ""
    itime = cand[24] if len(cand) > 24 else ""

    masked_phone = ph[:4] + "***" + ph[-4:] if len(ph) >= 8 else ph

    return web.json_response(
        {
            "ok": True,
            "ticket_id": t_id,
            "full_name": fn,
            "phone_masked": masked_phone,
            "vacancy": vac,
            "status": st or "Новая",
            "created_at": cr,
            "interview_depot": depot or "",
            "interview_time": itime or "",
        }
    )


async def handle_api_candidates(panel: WebPanel, request: web.Request) -> web.Response:
    """Пагинированный список анкет с поддержкой обеих баз и фильтрации."""
    _, err = panel.require_roles(request, {"hr", "tech", "depot", "superadmin", "super"})
    if err:
        return err
    try:
        page = max(1, int(request.query.get("page", 1)))
        limit = max(1, min(100, int(request.query.get("limit", 20))))
        offset = (page - 1) * limit
        status_filter = request.query.get("status", "all")
        db_scope = request.query.get("db_scope", "both")
        search = (request.query.get("search", "")).strip().lower()
        sort_order = "ASC" if request.query.get("sort", "desc") == "asc" else "DESC"

        all_rows = []
        with panel.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT ticket_id, created_at, full_name, vacancy, phone, status,
                       interview_depot, interview_time, driver_license, is_test,
                       birth_date, criminal_record, experience, platform
                FROM candidates
                """
            )
            rows = cursor.fetchall()
            for r in rows:
                (
                    t_id, cr, fn, vac, ph, st, depot, itime, lic, test_flag,
                    bdate, crim, exp, plat,
                ) = r
                is_candidate_test = bool(test_flag or (t_id and t_id >= 900000))
                scope_name = "test" if is_candidate_test else "prod"
                if db_scope == "prod" and is_candidate_test:
                    continue
                if db_scope == "test" and not is_candidate_test:
                    continue
                score_info = candidate_service.calculate_applicant_score({
                    "vacancy": vac or "",
                    "driver_license": lic or "",
                    "birth_date": bdate or "",
                    "criminal_record": crim or "",
                    "experience": exp or "",
                })
                row_dict = {
                    "ticket_id": t_id,
                    "created_at": cr or "",
                    "full_name": fn or "",
                    "vacancy": vac or "",
                    "phone": ph or "",
                    "status": st or "Новая",
                    "interview_depot": depot or "",
                    "interview_time": itime or "",
                    "driver_license": lic or "",
                    "platform": plat or "web",
                    "birth_date": bdate or "",
                    "experience": exp or "",
                    "is_test": is_candidate_test,
                    "db_scope": scope_name,
                    "fit_score": score_info,
                }
                all_rows.append(row_dict)

        if status_filter and status_filter != "all":
            if status_filter == "test_only":
                all_rows = [c for c in all_rows if c.get("is_test")]
            else:
                all_rows = [c for c in all_rows if c["status"] == status_filter or (status_filter in c["status"])]

        if search:
            all_rows = [
                c
                for c in all_rows
                if search in c["full_name"].lower()
                or search in c["phone"].lower()
                or search in c["vacancy"].lower()
                or search in str(c["ticket_id"])
            ]

        all_rows.sort(key=lambda x: x["ticket_id"], reverse=(sort_order == "DESC"))

        total_items = len(all_rows)
        total_pages = max(1, (total_items + limit - 1) // limit)
        paginated = all_rows[offset : offset + limit]

        active_db = os.path.basename(getattr(panel.db, "db_path", "resumes.db"))
        return web.json_response(
            {
                "candidates": paginated,
                "total": total_items,
                "page": page,
                "total_pages": total_pages,
                "active_db": active_db,
                "db_scope": db_scope,
            }
        )
    except Exception as e:
        logger.error("Ошибка API candidates: %s", e)
        return web.json_response({"candidates": [], "total": 0, "page": 1, "total_pages": 1}, status=500)


async def handle_api_candidate_detail(panel: WebPanel, request: web.Request) -> web.Response:
    """Полная карточка кандидата со всеми 16 пунктами и проверками 152-ФЗ."""
    session = panel.get_auth_session(request)
    if not session:
        return web.json_response({"error": "Требуется авторизация"}, status=401)

    tid_raw = request.match_info.get("ticket_id") or request.match_info.get("id")
    if not tid_raw:
        return web.json_response({"error": "ID анкеты не указан"}, status=400)
    ticket_id = int(tid_raw)

    if session.get("role") in {"applicant", "candidate"}:
        user_uid = str(session.get("uid", ""))
        if user_uid != str(ticket_id):
            cand_check = panel.db.get_candidate(ticket_id)
            if not cand_check or str(cand_check[2]) != user_uid:
                return web.json_response({"error": "Доступ к чужой анкете запрещен."}, status=403)

    cand = panel.db.get_candidate(ticket_id)
    if not cand:
        return web.json_response({"error": "Кандидат не найден"}, status=404)

    (
        t_id, plat, uid, fn, ph, vac, exp, st, note, cr,
        bdate, city, lic, edu, reloc, dorm, shift, med, crim, src, extra,
        consent_ts, raw_json, depot, itime, istatus,
    ) = cand

    has_risk, risk_desc = ComplianceService.check_criminal_record_risk(vac, crim)
    is_blocked = panel.db.is_blocked(uid)
    score_info = candidate_service.calculate_applicant_score({
        "vacancy": vac or "",
        "driver_license": lic or "",
        "birth_date": bdate or "",
        "criminal_record": crim or "",
        "shift_work": shift or "",
        "medical_restrictions": med or "",
        "experience": exp or "",
    })

    return web.json_response(
        {
            "ticket_id": t_id,
            "platform": plat,
            "fit_score": score_info,
            "user_id": uid,
            "full_name": fn,
            "phone": ph,
            "vacancy": vac,
            "experience": exp,
            "status": st,
            "admin_note": note or "",
            "created_at": cr,
            "birth_date": bdate or "",
            "city": city or "",
            "driver_license": lic or "",
            "education": edu or "",
            "relocation": reloc or "",
            "dormitory": dorm or "",
            "shift_work": shift or "",
            "medical_restrictions": med or "",
            "criminal_record": crim or "",
            "source": src or "",
            "extra_info": extra or "",
            "interview_depot": depot or "",
            "interview_time": itime or "",
            "interview_status": istatus or "pending",
            "is_blocked": is_blocked,
            "compliance": {
                "has_risk": has_risk,
                "text": risk_desc,
            },
        }
    )


async def handle_api_candidate_status(panel: WebPanel, request: web.Request) -> web.Response:
    """Смена статуса анкеты и уведомление соискателя в Telegram/мессенджер."""
    _, err = panel.require_roles(request, {"hr", "tech", "superadmin", "super"})
    if err:
        return err
    ticket_id = int(request.match_info["ticket_id"])
    data = await request.json()
    new_status = data.get("status")
    if not new_status:
        return web.json_response({"error": "Status required"}, status=400)

    cand = panel.db.get_candidate(ticket_id)
    panel.db.update_status(ticket_id, new_status)
    logger.info("Веб-панель: статус анкеты #%s изменен на %s", ticket_id, new_status)

    if cand:
        try:
            platform, user_id, full_name = cand[1], cand[2], cand[3]
            user_msg = None
            if new_status == "В работе":
                user_msg = texts.format_status_in_progress(full_name)
            elif new_status == "Приглашен":
                user_msg = texts.format_status_invited(full_name)
            elif new_status == "Отказ":
                user_msg = texts.format_status_rejected(full_name)
            elif new_status != "Архив":
                user_msg = (
                    f"📋 <b>Здравствуйте, {html.escape(full_name)}!</b>\n\n"
                    f"Статус вашей анкеты #{ticket_id} на должность «{html.escape(cand[5] or '')}» изменен на: <b>{html.escape(new_status)}</b>."
                )
            if user_msg:
                await send_response_to_candidate(platform, str(user_id), user_msg)
        except Exception as e:
            logger.warning("Не удалось отправить кандидату #%s уведомление о смене статуса из веб-панели: %s", ticket_id, e)

    return web.json_response({"ok": True, "ticket_id": ticket_id, "status": new_status})


async def handle_api_candidate_note(panel: WebPanel, request: web.Request) -> web.Response:
    """Сохранение кадровой заметки к анкете соискателя."""
    _, err = panel.require_roles(request, {"hr", "tech", "superadmin", "super"})
    if err:
        return err
    ticket_id = int(request.match_info["ticket_id"])
    data = await request.json()
    note = data.get("note", "")

    panel.db.update_admin_note(ticket_id, note)
    logger.info("Веб-панель: заметка к анкете #%s сохранена", ticket_id)
    return web.json_response({"ok": True, "ticket_id": ticket_id})


async def handle_api_candidate_delete(panel: WebPanel, request: web.Request) -> web.Response:
    """Физическое удаление анкеты кандидата с фиксацией в журнале уничтожения ПДн (152-ФЗ)."""
    _, err = panel.require_roles(request, {"hr", "tech", "superadmin", "super"})
    if err:
        return err
    ticket_id = int(request.match_info["ticket_id"])
    cand = panel.db.get_candidate(ticket_id)
    if not cand:
        return web.json_response({"error": "Анкета не найдена"}, status=404)

    panel.db.log_pdn_destruction(
        candidate_id=ticket_id,
        user_id=cand[2],
        platform=cand[1],
        reason="Удаление анкеты через веб-кабинет администратором",
        act_number=f"{ticket_id}-УПД",
    )
    panel.db.delete_candidate(ticket_id)
    logger.info("Веб-панель: анкета #%s удалена администратором", ticket_id)
    return web.json_response({"ok": True, "ticket_id": ticket_id})


async def handle_api_candidate_block(panel: WebPanel, request: web.Request) -> web.Response:
    """Внесение кандидата в черный список (ЧС)."""
    _, err = panel.require_roles(request, {"hr", "tech", "superadmin", "super"})
    if err:
        return err
    ticket_id = int(request.match_info["ticket_id"])
    cand = panel.db.get_candidate(ticket_id)
    if not cand:
        return web.json_response({"error": "Анкета не найдена"}, status=404)

    user_id = cand[2]
    panel.db.block_user_and_clean(user_id, reason="Блокировка через веб-панель")
    logger.info("Веб-панель: пользователь %s (анкета #%s) добавлен в ЧС", user_id, ticket_id)
    return web.json_response({"ok": True, "ticket_id": ticket_id, "user_id": user_id, "blocked": True})


async def handle_api_candidate_unblock(panel: WebPanel, request: web.Request) -> web.Response:
    """Исключение кандидата из черного списка."""
    _, err = panel.require_roles(request, {"hr", "tech", "superadmin", "super"})
    if err:
        return err
    ticket_id = int(request.match_info["ticket_id"])
    cand = panel.db.get_candidate(ticket_id)
    if not cand:
        return web.json_response({"error": "Анкета не найдена"}, status=404)

    user_id = cand[2]
    panel.db.unblock_user(user_id)
    panel.db.update_status(ticket_id, "Новая")
    logger.info("Веб-панель: пользователь %s разблокирован", user_id)
    return web.json_response({"ok": True, "ticket_id": ticket_id, "user_id": user_id, "blocked": False})


async def handle_api_candidate_create(panel: WebPanel, request: web.Request) -> web.Response:
    """Ручное добавление анкеты соискателя (очный приём в отделе кадров / депо)."""
    _, err = panel.require_roles(request, {"hr", "tech", "superadmin", "super"})
    if err:
        return err
    try:
        data = await request.json()
        full_name = (data.get("full_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        vacancy = (data.get("vacancy") or "Водитель трамвая").strip()
        depot = (data.get("depot") or "").strip()
        experience = (data.get("experience") or "Без опыта").strip()
        driver_license = (data.get("driver_license") or "Нет прав").strip()
        birth_date = (data.get("birth_date") or "").strip()
        city = (data.get("city") or "Ульяновск").strip()
        education = (data.get("education") or "Среднее").strip()
        shift_work = (data.get("shift_work") or "Да").strip()
        dormitory = (data.get("dormitory") or "Нет").strip()
        admin_note = (data.get("admin_note") or "").strip()
        target_db = (data.get("target_db") or "").strip().lower()
        is_test_flag = bool(data.get("is_test", False)) or (target_db == "test")

        if not full_name:
            return web.json_response({"error": "ФИО соискателя обязательно для заполнения"}, status=400)
        if not phone:
            return web.json_response({"error": "Телефон соискателя обязателен для заполнения"}, status=400)

        now_ts = int(time.time())
        user_id = f"office_{now_ts}"
        consent_ts = datetime.now().strftime("%d.%m.%Y %H:%M")

        actual_use_test = is_test_flag
        if target_db == "prod":
            actual_use_test = False
        elif target_db == "test":
            actual_use_test = True

        ticket_id = panel.db.add_candidate(
            platform="office",
            user_id=user_id,
            full_name=full_name,
            phone=phone,
            vacancy=vacancy,
            experience=experience,
            is_test=actual_use_test,
            consent_timestamp=consent_ts,
            birth_date=birth_date,
            city=city,
            driver_license=driver_license,
            education=education,
            dormitory=dormitory,
            shift_work=shift_work,
            source="Очный приём (Депо)",
            extra_info=f"Депо: {depot}" if depot else "",
            target_db="test" if actual_use_test else "prod",
        )

        if depot:
            with panel.db.connection(is_test=actual_use_test) as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE candidates SET interview_depot = ?, status = 'В работе' WHERE ticket_id = ?",
                    (depot, ticket_id),
                )
                conn.commit()

        if admin_note:
            panel.db.update_admin_note(ticket_id, admin_note)

        logger.info(
            "Веб-панель: создана очная анкета #%s (база: %s): %s, %s",
            ticket_id,
            "TEST" if actual_use_test else "PROD",
            full_name,
            vacancy,
        )

        return web.json_response(
            {
                "ok": True,
                "ticket_id": ticket_id,
                "is_test": actual_use_test,
                "full_name": full_name,
                "vacancy": vacancy,
                "message": f"Анкета #{ticket_id} успешно зарегистрирована в системе!",
            }
        )
    except Exception as e:
        logger.error("Ошибка создания анкеты через веб: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_api_vacancies(panel: WebPanel, request: web.Request) -> web.Response:
    """Динамический список должностей МУП «Ульяновскэлектротранс» из SQLite vacancy_settings."""
    show_all = request.query.get("all") in ("1", "true")

    db_vacs = []
    try:
        if hasattr(panel.db, "get_vacancies"):
            db_vacs = panel.db.get_vacancies(only_active=False)
    except Exception as e:
        logger.warning("Ошибка получения вакансий из vacancy_settings: %s", e)

    if not db_vacs:
        all_vac = get_all_vacancies()
        closed_set = get_closed_vacancies()
        target_list = all_vac if show_all else [v for v in all_vac if v not in closed_set]
        vacancies = []
        for idx, title in enumerate(target_list):
            vacancies.append({
                "id": f"vac_{idx}",
                "slug": f"vac_{idx}",
                "title": title,
                "icon": get_vacancy_icon(title),
                "salary": "По результатам собеседования",
                "salary_text": "По результатам собеседования",
                "requirements": "Официальное трудоустройство, соцпакет",
                "description": "Работа в МУП «Ульяновскэлектротранс».",
                "sub": "Официальное трудоустройство",
                "is_active": 0 if title in closed_set else 1,
                "is_open": title not in closed_set,
                "sort_order": idx + 1,
                "meta": {},
            })
        open_vac = [v for v in all_vac if v not in closed_set]
        return web.json_response({
            "ok": True,
            "open_vacancies": open_vac,
            "closed_vacancies": list(closed_set),
            "all_vacancies": all_vac,
            "vacancies": vacancies,
        })

    db_vacs.sort(key=lambda x: (x.get("sort_order", 0), x.get("slug", "")))

    all_titles = [v["title"] for v in db_vacs]
    open_titles = [v["title"] for v in db_vacs if v.get("is_active")]
    closed_titles = [v["title"] for v in db_vacs if not v.get("is_active")]

    vacancies = []
    for idx, v in enumerate(db_vacs):
        is_active = bool(v.get("is_active", 1))
        if not show_all and not is_active:
            continue
        meta = v.get("meta") or {}
        vacancies.append({
            "id": v["slug"],
            "slug": v["slug"],
            "title": v["title"],
            "category": v.get("category", "general"),
            "icon": meta.get("icon") or get_vacancy_icon(v["title"]),
            "salary": v.get("salary_text") or "По согласованию",
            "salary_text": v.get("salary_text") or "По согласованию",
            "training": meta.get("training", False),
            "stipend": meta.get("stipend"),
            "requirements": meta.get("requirements", "Официальное трудоустройство, полный соцпакет"),
            "description": v.get("description") or "Работа на маршрутах и объектах предприятия.",
            "sub": v.get("salary_text") or "Официальное трудоустройство",
            "is_active": 1 if is_active else 0,
            "is_open": is_active,
            "active": is_active,
            "sort_order": v.get("sort_order", idx + 1),
            "meta": meta,
        })

    return web.json_response({
        "ok": True,
        "open_vacancies": open_titles,
        "closed_vacancies": closed_titles,
        "all_vacancies": all_titles,
        "vacancies": vacancies,
    })


async def handle_api_vacancies_save(panel: WebPanel, request: web.Request) -> web.Response:
    _, err = panel.require_roles(request, {"hr", "tech", "superadmin", "super"})
    if err:
        return err
    try:
        data = await request.json()
        raw_list = data.get("vacancies", [])
        if not isinstance(raw_list, list):
            return web.json_response({"error": "vacancies must be a list"}, status=400)

        clean_vacancies = [str(v).strip() for v in raw_list if str(v).strip()]
        if not clean_vacancies:
            return web.json_response({"error": "Список вакансий не может быть пустым"}, status=400)

        save_all_vacancies(clean_vacancies)
        closed = get_closed_vacancies()
        new_closed = {v for v in closed if v in clean_vacancies}
        set_closed_vacancies(new_closed)

        return web.json_response({"ok": True, "vacancies": clean_vacancies, "closed": list(new_closed)})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_api_vacancies_toggle(panel: WebPanel, request: web.Request) -> web.Response:
    _, err = panel.require_roles(request, {"hr", "tech", "superadmin", "super"})
    if err:
        return err
    try:
        data = await request.json()
        title = str(data.get("title", "")).strip()
        active = data.get("active")
        if not title:
            return web.json_response({"error": "title is required"}, status=400)

        closed = get_closed_vacancies()
        if active is True:
            closed.discard(title)
        elif active is False:
            closed.add(title)
        else:
            if title in closed:
                closed.remove(title)
            else:
                closed.add(title)

        set_closed_vacancies(closed)
        return web.json_response({"ok": True, "title": title, "is_closed": title in closed})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_api_vacancies_add(panel: WebPanel, request: web.Request) -> web.Response:
    _, err = panel.require_roles(request, {"hr", "tech", "superadmin", "super"})
    if err:
        return err
    try:
        data = await request.json()
        title = str(data.get("title", "")).strip()
        if not title:
            return web.json_response({"error": "title is required"}, status=400)

        all_vac = get_all_vacancies()
        if title not in all_vac:
            all_vac.append(title)
            save_all_vacancies(all_vac)
        return web.json_response({"ok": True, "title": title, "vacancies": all_vac})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_api_vacancies_delete(panel: WebPanel, request: web.Request) -> web.Response:
    _, err = panel.require_roles(request, {"hr", "tech", "superadmin", "super"})
    if err:
        return err
    try:
        data = await request.json()
        title = str(data.get("title", "")).strip()
        if not title:
            return web.json_response({"error": "title is required"}, status=400)

        all_vac = get_all_vacancies()
        if title in all_vac:
            all_vac.remove(title)
            save_all_vacancies(all_vac)
            closed = get_closed_vacancies()
            if title in closed:
                closed.remove(title)
                set_closed_vacancies(closed)
        return web.json_response({"ok": True, "title": title, "vacancies": all_vac})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_api_apply_candidate(panel: WebPanel, request: web.Request) -> web.Response:
    """
    Приём анкеты соискателя с сайта / Telegram Mini App (16 шагов),
    сохранение в ResumeDB WAL предприятия и моментальная отправка
    карточки тикета [WEB-Заявка] в Telegram-чат отдела кадров.
    """
    if is_maintenance_active():
        return web.json_response(
            {
                "ok": False,
                "error": "Ведутся плановые технические работы. Подача анкет временно приостановлена. "
                f"Пожалуйста, обратитесь в отдел кадров: {CONFIG.get('HR_PHONE', '8 (8422) 58-46-60')}.",
            },
            status=503,
        )

    env_mode = CONFIG.get("ENVIRONMENT", "TEST")
    session = panel.get_auth_session(request)
    if session and session.get("role") in ("superadmin", "super", "tech", "hr", "admin") and env_mode == "PROD":
        return web.json_response(
            {
                "ok": False,
                "error": "В боевом режиме (PROD) подача анкеты со служебного административного аккаунта заблокирована.",
            },
            status=403,
        )

    try:
        data = await request.json()
        full_name = (data.get("full_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        vacancy = (data.get("vacancy") or "Водитель трамвая").strip()
        birth_date = (data.get("birth_date") or "").strip()
        city = (data.get("city") or "г. Ульяновск").strip()
        driver_license = (data.get("driver_license") or "Нет").strip()
        experience = (data.get("experience") or "Без опыта").strip()
        education = (data.get("education") or "Среднее").strip()
        relocation = (data.get("relocation") or "Уже проживаю в Ульяновске").strip()
        dormitory = (data.get("dormitory") or "Нет").strip()
        shift_work = (data.get("shift_work") or "Да, готов к сменному графику").strip()
        medical = (data.get("medical_restrictions") or data.get("medical") or "Нет ограничений").strip()
        criminal = (data.get("criminal_record") or data.get("criminal") or "Нет судимости").strip()
        source = (data.get("source") or "Сайт предприятия").strip()
        citizenship = (data.get("citizenship") or "Гражданин РФ").strip()
        referrer_tab = (data.get("referrer_tab") or "").strip()
        platform = (data.get("platform") or "web").strip()
        user_id = str(data.get("user_id") or "").strip()
        consent_ts = data.get("consent_timestamp") or datetime.now().strftime("%d.%m.%Y %H:%M")

        ok_fio, clean_fio, err_fio = candidate_service.validate_fio(full_name)
        if not ok_fio:
            return web.json_response(
                {"error": err_fio or "Некорректное ФИО (минимум 2 слова на русском)"}, status=400
            )

        ok_ph, clean_ph, err_ph = candidate_service.validate_phone(phone)
        if not ok_ph:
            return web.json_response({"error": err_ph or "Некорректный номер телефона"}, status=400)

        if birth_date:
            ok_bdate, clean_bdate, err_bdate = candidate_service.validate_birth_date(birth_date)
            if not ok_bdate:
                return web.json_response({"error": err_bdate or "Некорректная дата рождения (18+)"}, status=400)
            birth_date = clean_bdate

        if not user_id:
            user_id = f"web_{int(time.time())}"

        reg_data = {
            "user_id": user_id,
            "full_name": clean_fio,
            "phone": clean_ph,
            "vacancy": vacancy,
            "birth_date": birth_date,
            "city": city,
            "driver_license": driver_license,
            "experience": experience,
            "education": education,
            "relocation": relocation,
            "dormitory": dormitory,
            "shift_work": shift_work,
            "medical_restrictions": medical,
            "criminal_record": criminal,
            "citizenship": citizenship,
            "source": source,
            "referrer_tab": referrer_tab,
            "consent_timestamp": consent_ts,
            "extra_info": f"Табельный номер рекомендателя: {referrer_tab}" if referrer_tab else "",
        }

        ticket_id, meta = candidate_service.register_candidate(reg_data, platform=platform)

        cand = panel.db.get_candidate(ticket_id)
        if cand:
            base_card = texts.format_hr_card_full(cand)
        else:
            base_card = texts.format_hr_card_full(reg_data)

        hr_card = f"🌐 <b>[WEB-Заявка с сайта / Mini App]</b>\n\n{base_card}"
        ticket_kb = make_ticket_keyboard(ticket_id)

        if panel.bot:
            try:
                sender_label = "Официальный сайт / Портал соискателя (веб-форма)"
                await route_new_candidate_ticket(panel.bot, hr_card, ticket_kb, sender_info=sender_label)
            except Exception as e:
                logger.error("Ошибка маршрутизации WEB-тикета #%s в HR-чат: %s", ticket_id, e)

            if platform == "tg" and user_id.lstrip("-").isdigit():
                try:
                    is_driver_no_exp = bool(meta.get("offer_training", False))
                    user_msg = texts.format_survey_success(ticket_id, vacancy, is_driver_no_exp)
                    await safe_send(panel.bot, int(user_id), user_msg)
                except Exception as e:
                    logger.error("Ошибка отправки подтверждения в бот соискателю %s: %s", user_id, e)

        logger.info("Успешно принята WEB-анкета #%s: %s (%s, %s)", ticket_id, clean_fio, clean_ph, vacancy)
        return web.json_response(
            {
                "ok": True,
                "ticket_id": ticket_id,
                "full_name": clean_fio,
                "vacancy": vacancy,
                "offer_training": meta.get("offer_training", False),
                "message": f"Анкета #{ticket_id} успешно зарегистрирована!",
            }
        )
    except Exception as e:
        logger.error("Исключение в handle_api_apply_candidate: %s", e)
        return web.json_response({"error": str(e)}, status=500)
