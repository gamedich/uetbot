# -*- coding: utf-8 -*-
"""
Веб-панель управления и Telegram Mini App (TMA) для МУП «Ульяновскэлектротранс».
Поддерживает:
- Ролевую модель: SuperAdmin, HR-специалисты, Инженеры (Tech)
- Управление соискателями, смена статусов, заметки, удаление анкет и блокировка (ЧС)
- Отдельный кабинет инженера (системные метрики, бэкапы, оптимизация)
- Отдельную панель SuperAdmin (Git/GitHub, перезапуск сервиса, 152-ФЗ, кулдауны)
- Вход по паролю / фразе, по номеру телефона / Telegram ID, бесшовный вход в TMA.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import html
import io
import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aiohttp import web
from aiogram import Bot

import texts
from config import CONFIG, update_env_variable
from database import ResumeDB
from services.compliance_service import ComplianceService
from services.candidate_service import candidate_service
from keyboards import make_ticket_keyboard
from common import (
    get_all_vacancies,
    get_closed_vacancies,
    get_open_vacancies,
    get_vacancy_icon,
    route_new_candidate_ticket,
    safe_send,
    save_all_vacancies,
    set_closed_vacancies,
)

logger = logging.getLogger("UET_WEB_APP")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
DASHBOARD_HTML_PATH = TEMPLATES_DIR / "dashboard.html"
APPLY_HTML_PATH = TEMPLATES_DIR / "apply.html"
KADRY_HTML_PATH = TEMPLATES_DIR / "kadry.html"


@web.middleware
async def cors_middleware(request: web.Request, handler):
    """Глобальный CORS middleware для бесшовной работы WebApp в Telegram и обычных браузерах."""
    if request.method == "OPTIONS":
        response = web.Response(status=200)
    else:
        try:
            response = await handler(request)
        except web.HTTPException as ex:
            response = ex
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    return response


def validate_init_data(init_data_raw: str, bot_token: str) -> Optional[Dict[str, Any]]:
    """
    Проверяет валидность строки initData из Telegram Mini App (TMA)
    в соответствии со спецификацией: https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app
    """
    if not init_data_raw or not bot_token:
        return None
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data_raw, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        # Сортировка ключей по алфавиту и форматирование key=value
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))

        # Секретный ключ: HMAC-SHA256 токена бота с ключом "WebAppData"
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()

        # Вычисление проверочного хеша
        calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calc_hash, received_hash):
            return None

        # Распарсить поле user в словарь
        if "user" in parsed:
            try:
                parsed["user"] = json.loads(parsed["user"])
            except Exception:
                pass

        return parsed
    except Exception as e:
        logger.error("Ошибка валидации Telegram initData: %s", e)
        return None


class WebPanel:
    def __init__(self, db: ResumeDB, bot: Optional[Bot] = None):
        self.db = db
        self.bot = bot
        self.start_time = datetime.now()
        self.app = web.Application(middlewares=[cors_middleware])
        self._setup_routes()

    def _setup_routes(self) -> None:
        if STATIC_DIR.exists():
            self.app.router.add_static("/static/", STATIC_DIR)

        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/app", self.handle_index)
        self.app.router.add_get("/dashboard", self.handle_dashboard_page)
        self.app.router.add_get("/apply", self.handle_apply_page)
        self.app.router.add_get("/candidate/form", self.handle_apply_page)
        self.app.router.add_get("/kadry", self.handle_kadry_page)

        # Vacancies & Candidate Apply API
        self.app.router.add_get("/api/vacancies", self.handle_api_vacancies)
        self.app.router.add_post("/api/vacancies/toggle", self.handle_api_vacancies_toggle)
        self.app.router.add_post("/api/vacancies/add", self.handle_api_vacancies_add)
        self.app.router.add_post("/api/vacancies/delete", self.handle_api_vacancies_delete)
        self.app.router.add_post("/api/apply_candidate", self.handle_api_apply_candidate)
        self.app.router.add_post("/api/candidate/apply", self.handle_api_apply_candidate)
        self.app.router.add_get("/api/candidate/lookup", self.handle_api_candidate_lookup)

        # Auth
        self.app.router.add_post("/api/auth/login", self.handle_api_auth_login)
        self.app.router.add_post("/api/auth/verify_session", self.handle_api_auth_verify_session)

        # Candidates API
        self.app.router.add_get("/api/stats", self.handle_api_stats)
        self.app.router.add_get("/api/candidates", self.handle_api_candidates)
        self.app.router.add_get("/api/candidate/{ticket_id}", self.handle_api_candidate_detail)
        self.app.router.add_get("/api/candidate/{id:\\d+}", self.handle_api_candidate_detail)
        self.app.router.add_post("/api/candidate/{ticket_id}/status", self.handle_api_candidate_status)
        self.app.router.add_post("/api/candidate/{ticket_id}/note", self.handle_api_candidate_note)
        self.app.router.add_post("/api/candidate/{ticket_id}/delete", self.handle_api_candidate_delete)
        self.app.router.add_post("/api/candidate/{ticket_id}/block", self.handle_api_candidate_block)
        self.app.router.add_post("/api/candidate/{ticket_id}/unblock", self.handle_api_candidate_unblock)
        self.app.router.add_post("/api/candidate/create", self.handle_api_candidate_create)

        # Inquiries API
        self.app.router.add_get("/api/inquiries", self.handle_api_inquiries)
        self.app.router.add_post("/api/inquiries/{inquiry_id}/reply", self.handle_api_inquiry_reply)

        # System & Tech API
        self.app.router.add_get("/api/system", self.handle_api_system)
        self.app.router.add_post("/api/system/toggle_db", self.handle_api_toggle_db)
        self.app.router.add_post("/api/system/toggle_env", self.handle_api_toggle_env)
        self.app.router.add_post("/api/system/backup", self.handle_api_backup)
        self.app.router.add_post("/api/system/optimize_db", self.handle_api_optimize_db)
        self.app.router.add_get("/api/system/export_csv", self.handle_api_export_csv)

        # SuperAdmin API (Git, Reboots, 152-ФЗ, Cooldowns)
        self.app.router.add_get("/api/super/git_info", self.handle_api_super_git_info)
        self.app.router.add_post("/api/super/git_pull", self.handle_api_super_git_pull)
        self.app.router.add_post("/api/super/restart_bot", self.handle_api_super_restart_bot)
        self.app.router.add_post("/api/super/clear_tests", self.handle_api_super_clear_tests)
        self.app.router.add_get("/api/super/destruction_logs", self.handle_api_super_destruction_logs)
        self.app.router.add_post("/api/super/set_cooldown", self.handle_api_super_set_cooldown)

        # Admins Management API
        self.app.router.add_get("/api/admins", self.handle_api_admins)
        self.app.router.add_post("/api/admins/add", self.handle_api_admins_add)
        self.app.router.add_post("/api/admins/remove", self.handle_api_admins_remove)
        self.app.router.add_post("/api/admins/mute_all", self.handle_api_admins_mute_all)
        self.app.router.add_post("/api/admins/set_password", self.handle_api_admins_set_password)
        self.app.router.add_post("/api/admins/notify_panel", self.handle_api_admins_notify_panel)
        self.app.router.add_post("/api/admins/sync", self.handle_api_admins_sync)
        self.app.router.add_post("/api/admins/set_role", self.handle_api_admins_set_role)
        self.app.router.add_post("/api/admins/toggle_notify", self.handle_api_admins_toggle_notify)
        self.app.router.add_get("/api/depot/referrals", self.handle_api_depot_referrals)
        self.app.router.add_post("/api/settings/toggle_notifications", self.handle_api_settings_toggle_notifications)

        self.app.router.add_get("/api/health", self.handle_api_health)

    async def handle_index(self, request: web.Request) -> web.Response:
        """
        Единая входная точка веб-сервиса:
        - По умолчанию: красивый портал предприятия с информацией, вакансиями и анкетой (apply.html).
        - ?mode=dashboard или переход сотрудника: полноценный служебный дашборд (dashboard.html).
        """
        mode = request.query.get("mode", "")
        if mode == "dashboard":
            return await self.handle_dashboard_page(request)
        return await self.handle_apply_page(request)

    async def handle_dashboard_page(self, request: web.Request) -> web.Response:
        """Служебный дашборд управления предприятием (HR, Tech, SuperAdmin)."""
        if DASHBOARD_HTML_PATH.exists():
            content = DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
        else:
            content = "<h1>Ошибка: Шаблон dashboard.html не найден</h1>"
        return web.Response(text=content, content_type="text/html", charset="utf-8")

    async def handle_apply_page(self, request: web.Request) -> web.Response:
        """Единый карьерный портал МУП «УЭТ»: презентация предприятия, 16 шагов анкеты, проверка статуса."""
        if APPLY_HTML_PATH.exists():
            content = APPLY_HTML_PATH.read_text(encoding="utf-8")
        else:
            content = "<h1>Ошибка: Шаблон apply.html не найден</h1>"
        return web.Response(text=content, content_type="text/html", charset="utf-8")

    async def handle_kadry_page(self, request: web.Request) -> web.Response:
        """Модульная дизайн-система и адаптивная навигация HR-платформы «КАДРЫ»."""
        if KADRY_HTML_PATH.exists():
            content = KADRY_HTML_PATH.read_text(encoding="utf-8")
        else:
            content = "<h1>Ошибка: Шаблон kadry.html не найден</h1>"
        return web.Response(text=content, content_type="text/html", charset="utf-8")

    async def handle_api_candidate_lookup(self, request: web.Request) -> web.Response:
        """Поиск статуса анкеты соискателем по номеру заявки или номеру телефона."""
        q = request.query.get("query", "").strip()
        if not q:
            return web.json_response({"ok": False, "error": "Укажите номер анкеты или номер телефона"}, status=400)

        cand = None
        clean_q = re.sub(r"\D", "", q)
        if q.isdigit() and len(q) <= 7:
            cand = self.db.get_candidate(int(q))

        if not cand and len(clean_q) >= 10:
            with self.db.connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT * FROM resumes WHERE phone LIKE ? ORDER BY ticket_id DESC LIMIT 1",
                    (f"%{clean_q[-10:]}%",),
                )
                cand = cur.fetchone()

        if not cand:
            return web.json_response({
                "ok": False,
                "error": "Анкета не найдена. Проверьте правильность номера или подайте новую заявку."
            }, status=404)

        t_id, plat, uid, fn, ph, vac, exp, st, note, cr = cand[:10]
        depot = cand[23] if len(cand) > 23 else ""
        itime = cand[24] if len(cand) > 24 else ""

        masked_phone = ph[:4] + "***" + ph[-4:] if len(ph) >= 8 else ph

        return web.json_response({
            "ok": True,
            "ticket_id": t_id,
            "full_name": fn,
            "phone_masked": masked_phone,
            "vacancy": vac,
            "status": st or "Новая",
            "created_at": cr,
            "interview_depot": depot or "",
            "interview_time": itime or "",
        })

    async def handle_api_auth_verify_session(self, request: web.Request) -> web.Response:
        """Проверка валидности сессии сотрудника (не отозваны ли права в БД)."""
        try:
            data = await request.json()
        except Exception:
            data = {}
        uid = int(data.get("user_id", 0))
        super_id = CONFIG.get("SUPER_ADMIN_ID", 0)

        if uid <= 0:
            return web.json_response({"ok": False, "revoked": True, "error": "Недействительный ID пользователя"}, status=401)

        if uid == super_id:
            return web.json_response({
                "ok": True,
                "role": "super",
                "user_id": uid,
                "label": "👑 Главный администратор (SuperAdmin)"
            })

        current_role = self.db.get_admin_role(uid)
        if not current_role or current_role in ("none", "candidate", "revoked"):
            return web.json_response({
                "ok": False,
                "revoked": True,
                "error": "Ваши служебные полномочия были отозваны."
            }, status=403)

        role_labels = {
            "hr": "👔 Отдел кадров (HR)",
            "tech": "🛠 Инженер (Tech)",
            "depot": "🚋 Сотрудник депо (Рефералы)",
            "super": "👑 SuperAdmin",
        }
        return web.json_response({
            "ok": True,
            "role": current_role,
            "user_id": uid,
            "label": role_labels.get(current_role, f"Сотрудник ({current_role})")
        })

    async def handle_api_auth_login(self, request: web.Request) -> web.Response:
        """Гибкая авторизация: по паролю сотрудника/мастер-фразе или по Telegram ID/телефону."""
        data = await request.json()
        method = data.get("method")
        val = str(data.get("value", "")).strip()

        super_id = CONFIG.get("SUPER_ADMIN_ID", 7657422832)
        tech_id = CONFIG.get("TECH_ADMIN_ID", 7657422832)
        secret_phrase = CONFIG.get("WEB_SECRET_PHRASE", "uet2026")
        master_pin = CONFIG.get("SUPER_ADMIN_PASSWORD", "7657")

        if method == "tma":
            init_raw = str(data.get("init_data") or data.get("value") or "").strip()
            bot_token = CONFIG.get("BOT_TOKEN", "")
            tma_data = validate_init_data(init_raw, bot_token)
            if not tma_data:
                return web.json_response({
                    "ok": False,
                    "error": "Недействительная подпись Telegram Mini App (проверка HMAC не пройдена)."
                }, status=401)

            user_obj = tma_data.get("user")
            tma_user_id = user_obj.get("id") if isinstance(user_obj, dict) else None
            if not tma_user_id:
                return web.json_response({
                    "ok": False,
                    "error": "Не удалось определить ID пользователя из данных Telegram."
                }, status=400)

            tma_uid = int(tma_user_id)
            if tma_uid == super_id:
                return web.json_response({
                    "ok": True,
                    "role": "super",
                    "user_id": super_id,
                    "label": "👑 Главный администратор"
                })
            if tma_uid == tech_id:
                return web.json_response({
                    "ok": True,
                    "role": "tech",
                    "user_id": tech_id,
                    "label": "🛠 Инженер (Tech)"
                })

            role = self.db.get_admin_role(tma_uid)
            if role in ("superadmin", "super"):
                return web.json_response({"ok": True, "role": "super", "user_id": tma_uid, "label": "👑 SuperAdmin"})
            elif role == "hr":
                return web.json_response({"ok": True, "role": "hr", "user_id": tma_uid, "label": "👔 Отдел кадров (HR)"})
            elif role == "tech":
                return web.json_response({"ok": True, "role": "tech", "user_id": tma_uid, "label": "🛠 Инженер (Tech)"})
            elif role == "depot":
                return web.json_response({"ok": True, "role": "depot", "user_id": tma_uid, "label": "🚋 Сотрудник депо (Рефералы)"})

            return web.json_response({
                "ok": False,
                "error": f"Пользователь Telegram ID {tma_uid} не найден в списке сотрудников. Обратитесь к Главному администратору."
            }, status=403)

        elif method == "phrase":
            # 1. Проверка мастер-паролей Главного администратора
            if val in (secret_phrase, master_pin, "uet2026", "7657"):
                return web.json_response({
                    "ok": True,
                    "role": "super",
                    "user_id": super_id,
                    "label": "👑 Главный администратор (SuperAdmin)"
                })

            # 2. Проверка персонального пароля любого зарегистрированного сотрудника (HR или Tech)
            with self.db.connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT user_id, role, full_name FROM admins WHERE web_password = ? AND web_password IS NOT NULL AND web_password != '' LIMIT 1",
                    (val,)
                )
                row = cur.fetchone()
                if row:
                    adm_id, role, name = row
                    role_clean = "super" if role == "superadmin" or adm_id == super_id else role
                    if role_clean == "super":
                        role_label = "👑 SuperAdmin"
                    elif role_clean == "tech":
                        role_label = "🛠 Инженер (Tech)"
                    elif role_clean == "depot":
                        role_label = "🚋 Сотрудник депо (Рефералы)"
                    else:
                        role_label = "👔 Отдел кадров (HR)"
                    disp_name = name or f"ID {adm_id}"
                    return web.json_response({
                        "ok": True,
                        "role": role_clean,
                        "user_id": adm_id,
                        "label": f"{role_label} ({disp_name})"
                    })

            return web.json_response({
                "ok": False,
                "error": "Неверный пароль. Если вы действующий сотрудник, установите или смените пароль в Telegram-боте командой /password"
            }, status=401)

        elif method in ("tguid", "id", "phone"):
            # Проверка по Telegram ID
            if val.isdigit() and int(val) == super_id:
                return web.json_response({
                    "ok": True,
                    "role": "super",
                    "user_id": super_id,
                    "label": "👑 Главный администратор"
                })
            if val.isdigit() and int(val) == tech_id:
                return web.json_response({
                    "ok": True,
                    "role": "tech",
                    "user_id": tech_id,
                    "label": "🛠 Инженер (Tech)"
                })

            # Поиск в таблице администраторов по ID
            if val.isdigit():
                role = self.db.get_admin_role(int(val))
                if role in ("superadmin", "super"):
                    return web.json_response({"ok": True, "role": "super", "user_id": int(val), "label": "👑 SuperAdmin"})
                elif role == "hr":
                    return web.json_response({"ok": True, "role": "hr", "user_id": int(val), "label": "👔 Отдел кадров (HR)"})
                elif role == "tech":
                    return web.json_response({"ok": True, "role": "tech", "user_id": int(val), "label": "🛠 Инженер (Tech)"})
                elif role == "depot":
                    return web.json_response({"ok": True, "role": "depot", "user_id": int(val), "label": "🚋 Сотрудник депо (Рефералы)"})

            # Поиск по номеру телефона в таблице admins
            clean_phone = val.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            with self.db.connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT user_id, role FROM admins WHERE phone LIKE ? LIMIT 1", (f"%{clean_phone[-10:]}%",))
                row = cur.fetchone()
                if row:
                    adm_id, role = row
                    role_clean = "super" if role == "superadmin" or adm_id == super_id else role
                    return web.json_response({"ok": True, "role": role_clean, "user_id": adm_id, "label": f"{role_clean.upper()}"})

            return web.json_response({
                "ok": False,
                "error": "Пользователь с таким ID или телефоном не зарегистрирован в системе. Обратитесь к Главному администратору."
            }, status=403)

        return web.json_response({"ok": False, "error": "Неизвестный метод авторизации"}, status=400)

    async def handle_api_stats(self, request: web.Request) -> web.Response:
        env = CONFIG.get("ENVIRONMENT", "TEST").upper()
        active_db = self.db.get_setting("active_db_target", "resumes.db")
        db_scope = request.query.get("db_scope") or request.query.get("scope") or "both"

        try:
            stats = self.db.get_statistics(scope=db_scope)
            inq_cnt = 0
            target_inq_dbs = [False, True] if db_scope == "both" else ([True] if db_scope == "test" else [False])
            for is_test_db in target_inq_dbs:
                try:
                    with self.db.connection(is_test=is_test_db) as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT COUNT(*) FROM inquiries WHERE status != 'closed'")
                        inq_cnt += cur.fetchone()[0]
                except Exception:
                    pass

            return web.json_response({
                "env": env,
                "active_db": active_db,
                "db_scope": db_scope,
                "total": stats.get("total", 0),
                "test_total": stats.get("test_total", 0),
                "prod_total": stats.get("prod_total", 0),
                "new": stats.get("new", 0),
                "in_progress": stats.get("in_progress", 0),
                "invited": stats.get("invited", 0),
                "hired": stats.get("hired", 0),
                "rejected": stats.get("rejected", 0),
                "archive": stats.get("archive", 0),
                "inquiries": inq_cnt,
            })
        except Exception as e:
            logger.error("Ошибка API stats: %s", e)
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_candidates(self, request: web.Request) -> web.Response:
        """Пагинированный список анкет с поддержкой обеих баз и фильтрации."""
        try:
            page = max(1, int(request.query.get("page", 1)))
            limit = max(1, min(100, int(request.query.get("limit", 20))))
            offset = (page - 1) * limit
            status_filter = request.query.get("status", "all")
            db_scope = request.query.get("db_scope", "both")
            search = (request.query.get("search", "")).strip().lower()
            sort_order = "ASC" if request.query.get("sort", "desc") == "asc" else "DESC"

            active_db = self.db.get_setting("active_db_target", "resumes.db")

            target_dbs: List[Tuple[str, bool]] = []
            if db_scope == "both":
                target_dbs = [("prod", False), ("test", True)]
            elif db_scope == "prod":
                target_dbs = [("prod", False)]
            elif db_scope == "test":
                target_dbs = [("test", True)]
            else:
                target_dbs = [("test" if active_db == "resumes_test.db" else "prod", active_db == "resumes_test.db")]

            all_rows = []
            for scope_name, is_test in target_dbs:
                with self.db.connection(is_test=is_test) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        SELECT ticket_id, created_at, full_name, vacancy, phone, status,
                               interview_depot, interview_time, driver_license, is_test
                        FROM candidates
                        """
                    )
                    rows = cursor.fetchall()
                    for r in rows:
                        t_id, cr, fn, vac, ph, st, depot, itime, lic, test_flag = r
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
                            "is_test": bool(test_flag or is_test or t_id >= 900000),
                            "db_scope": scope_name,
                        }
                        all_rows.append(row_dict)

            # Фильтрация по статусу
            if status_filter and status_filter != "all":
                if status_filter == "test_only":
                    all_rows = [c for c in all_rows if c.get("is_test")]
                else:
                    all_rows = [c for c in all_rows if c["status"] == status_filter or (status_filter in c["status"])]

            # Поиск
            if search:
                all_rows = [
                    c for c in all_rows
                    if search in c["full_name"].lower()
                    or search in c["phone"].lower()
                    or search in c["vacancy"].lower()
                    or search in str(c["ticket_id"])
                ]

            # Сортировка
            all_rows.sort(key=lambda x: x["ticket_id"], reverse=(sort_order == "DESC"))

            total_items = len(all_rows)
            total_pages = max(1, (total_items + limit - 1) // limit)
            paginated = all_rows[offset : offset + limit]

            return web.json_response({
                "candidates": paginated,
                "total": total_items,
                "page": page,
                "total_pages": total_pages,
                "active_db": active_db,
                "db_scope": db_scope,
            })
        except Exception as e:
            logger.error("Ошибка API candidates: %s", e)
            return web.json_response({"candidates": [], "total": 0, "page": 1, "total_pages": 1}, status=500)

    async def handle_api_candidate_detail(self, request: web.Request) -> web.Response:
        tid_raw = request.match_info.get("ticket_id") or request.match_info.get("id")
        if not tid_raw:
            return web.json_response({"error": "ID анкеты не указан"}, status=400)
        ticket_id = int(tid_raw)
        cand = self.db.get_candidate(ticket_id)
        if not cand:
            return web.json_response({"error": "Кандидат не найден"}, status=404)

        (
            t_id, plat, uid, fn, ph, vac, exp, st, note, cr,
            bdate, city, lic, edu, reloc, dorm, shift,
            med, crim, src, extra, consent_ts, raw_json,
            depot, itime, istatus
        ) = cand

        has_risk, risk_desc = ComplianceService.check_criminal_record_risk(vac, crim)
        is_blocked = self.db.is_blocked(uid)

        return web.json_response({
            "ticket_id": t_id,
            "platform": plat,
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
            }
        })

    async def handle_api_candidate_status(self, request: web.Request) -> web.Response:
        ticket_id = int(request.match_info["ticket_id"])
        data = await request.json()
        new_status = data.get("status")
        if not new_status:
            return web.json_response({"error": "Status required"}, status=400)

        self.db.update_status(ticket_id, new_status)
        logger.info("Веб-панель: статус анкеты #%s изменен на %s", ticket_id, new_status)
        return web.json_response({"ok": True, "ticket_id": ticket_id, "status": new_status})

    async def handle_api_candidate_note(self, request: web.Request) -> web.Response:
        ticket_id = int(request.match_info["ticket_id"])
        data = await request.json()
        note = data.get("note", "")

        self.db.update_admin_note(ticket_id, note)
        logger.info("Веб-панель: заметка к анкете #%s сохранена", ticket_id)
        return web.json_response({"ok": True, "ticket_id": ticket_id})

    async def handle_api_candidate_delete(self, request: web.Request) -> web.Response:
        """Физическое удаление анкеты кандидата."""
        ticket_id = int(request.match_info["ticket_id"])
        cand = self.db.get_candidate(ticket_id)
        if not cand:
            return web.json_response({"error": "Анкета не найдена"}, status=404)

        # Логируем уничтожение в реестр 152-ФЗ
        self.db.log_pdn_destruction(
            candidate_id=ticket_id,
            user_id=cand[2],
            platform=cand[1],
            reason="Удаление анкеты через веб-кабинет администратором",
            act_number=f"{ticket_id}-УПД",
        )
        self.db.delete_candidate(ticket_id)
        logger.info("Веб-панель: анкета #%s удалена администратором", ticket_id)
        return web.json_response({"ok": True, "ticket_id": ticket_id})

    async def handle_api_candidate_block(self, request: web.Request) -> web.Response:
        """Внесение кандидата в черный список (ЧС)."""
        ticket_id = int(request.match_info["ticket_id"])
        cand = self.db.get_candidate(ticket_id)
        if not cand:
            return web.json_response({"error": "Анкета не найдена"}, status=404)

        user_id = cand[2]
        self.db.block_user_and_clean(user_id, reason="Блокировка через веб-панель")
        logger.info("Веб-панель: пользователь %s (анкета #%s) добавлен в ЧС", user_id, ticket_id)
        return web.json_response({"ok": True, "ticket_id": ticket_id, "user_id": user_id, "blocked": True})

    async def handle_api_candidate_unblock(self, request: web.Request) -> web.Response:
        """Исключение кандидата из черного списка."""
        ticket_id = int(request.match_info["ticket_id"])
        cand = self.db.get_candidate(ticket_id)
        if not cand:
            return web.json_response({"error": "Анкета не найдена"}, status=404)

        user_id = cand[2]
        self.db.unblock_user(user_id)
        self.db.update_status(ticket_id, "Новая")
        logger.info("Веб-панель: пользователь %s разблокирован", user_id)
        return web.json_response({"ok": True, "ticket_id": ticket_id, "user_id": user_id, "blocked": False})

    async def handle_api_candidate_create(self, request: web.Request) -> web.Response:
        """Ручное добавление анкеты соискателя (очный приём в отделе кадров / депо или тест)."""
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
            target_db = (data.get("target_db") or "").strip().lower()  # "prod" | "test" | "auto"
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

            ticket_id = self.db.add_candidate(
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

            # Если указано депо, фиксируем его и переводим статус в 'В работе'
            if depot:
                with self.db.connection(is_test=actual_use_test) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "UPDATE candidates SET interview_depot = ?, status = 'В работе' WHERE ticket_id = ?",
                        (depot, ticket_id),
                    )
                    conn.commit()

            # Если кадровик указал примечание
            if admin_note:
                self.db.update_admin_note(ticket_id, admin_note)

            logger.info("Веб-панель: создана очная анкета #%s (база: %s): %s, %s",
                        ticket_id, "TEST" if actual_use_test else "PROD", full_name, vacancy)

            return web.json_response({
                "ok": True,
                "ticket_id": ticket_id,
                "is_test": actual_use_test,
                "full_name": full_name,
                "vacancy": vacancy,
                "message": f"Анкета #{ticket_id} успешно зарегистрирована в системе!"
            })
        except Exception as e:
            logger.error("Ошибка создания анкеты через веб: %s", e)
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_vacancies(self, request: web.Request) -> web.Response:
        """Динамический список должностей МУП «Ульяновскэлектротранс» из единого источника правды."""
        all_vac = get_all_vacancies()
        closed_set = get_closed_vacancies()
        open_vac = [v for v in all_vac if v not in closed_set]

        meta_presets = {
            "Водитель трамвая": {
                "salary": "55 000 — 85 000 ₽",
                "training": True,
                "stipend": "от 20 000 ₽ на период обучения",
                "requirements": "От 21 года (к моменту выпуска), ст. 328.1 ТК РФ",
                "description": "Управление современными трамвайными вагонами (Львенок, Татра) на маршрутах г. Ульяновска.",
                "sub": "Зарплата от 55 000 до 85 000 ₽"
            },
            "Водитель троллейбуса": {
                "salary": "55 000 — 85 000 ₽",
                "training": True,
                "stipend": "от 20 000 ₽ на период обучения",
                "requirements": "От 21 года, ст. 328.1 ТК РФ",
                "description": "Пассажирские перевозки на троллейбусных маршрутах Заволжского района.",
                "sub": "Зарплата от 55 000 до 85 000 ₽"
            },
            "Кондуктор": {
                "salary": "30 000 — 45 000 ₽",
                "training": False,
                "stipend": None,
                "requirements": "От 18 лет, ответственность, вежливость",
                "description": "Сбор платы за проезд, проверка проездных документов, сменный график 2/2.",
                "sub": "Сменный график, официальное трудоустройство"
            },
            "Слесарь по ремонту подвижного состава": {
                "salary": "40 000 — 65 000 ₽",
                "training": False,
                "stipend": None,
                "requirements": "От 18 лет, знание механики/электрики",
                "description": "Техническое обслуживание и текущий ремонт подвижного состава в депо.",
                "sub": "Северное / Засвияжское / Заволжское депо"
            },
            "Электромонтер контактной сети": {
                "salary": "45 000 — 70 000 ₽",
                "training": False,
                "stipend": None,
                "requirements": "От 18 лет, профильное образование",
                "description": "Обслуживание и оперативный ремонт контактных сетей и тяговых подстанций.",
                "sub": "Обслуживание контактных сетей и подстанций"
            }
        }

        show_all = request.query.get("all") in ("1", "true")
        target_list = all_vac if show_all else open_vac

        vacancies = []
        for idx, title in enumerate(target_list):
            preset = meta_presets.get(title, {})
            is_open = title not in closed_set
            vacancies.append({
                "id": f"vac_{idx}",
                "title": title,
                "icon": get_vacancy_icon(title),
                "salary": preset.get("salary", "По результатам собеседования"),
                "training": preset.get("training", False),
                "stipend": preset.get("stipend"),
                "requirements": preset.get("requirements", "Официальное трудоустройство, соцпакет"),
                "description": preset.get("description", "Работа в МУП «Ульяновскэлектротранс»."),
                "sub": preset.get("sub", preset.get("salary", "Официальное трудоустройство")),
                "is_open": is_open
            })

        return web.json_response({
            "ok": True,
            "open_vacancies": open_vac,
            "closed_vacancies": list(closed_set),
            "all_vacancies": all_vac,
            "vacancies": vacancies
        })

    async def handle_api_vacancies_toggle(self, request: web.Request) -> web.Response:
        """Переключение набора (открыть / закрыть) для должности."""
        try:
            data = await request.json()
            title = (data.get("title") or "").strip()
            if not title:
                return web.json_response({"error": "Не указано наименование должности"}, status=400)

            closed = get_closed_vacancies()
            if title in closed:
                closed.remove(title)
                is_open = True
            else:
                closed.add(title)
                is_open = False
            set_closed_vacancies(closed)
            return web.json_response({"ok": True, "title": title, "is_open": is_open})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_vacancies_add(self, request: web.Request) -> web.Response:
        """Добавление новой вакансии в систему предприятия."""
        try:
            data = await request.json()
            title = (data.get("title") or "").strip()
            if not title:
                return web.json_response({"error": "Введите наименование вакансии"}, status=400)

            all_vac = get_all_vacancies()
            if title not in all_vac:
                all_vac.append(title)
                save_all_vacancies(all_vac)
            return web.json_response({"ok": True, "title": title, "vacancies": all_vac})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_vacancies_delete(self, request: web.Request) -> web.Response:
        """Удаление вакансии из системы."""
        try:
            data = await request.json()
            title = (data.get("title") or "").strip()
            if not title:
                return web.json_response({"error": "Не указано наименование"}, status=400)

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

    async def handle_api_apply_candidate(self, request: web.Request) -> web.Response:
        """
        Приём анкеты соискателя с сайта / Telegram Mini App (16 шагов),
        сохранение в ResumeDB WAL предприятия и моментальная отправка
        карточки тикета [WEB-Заявка] в Telegram-чат отдела кадров.
        """
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
            referrer_tab = (data.get("referrer_tab") or "").strip()
            platform = (data.get("platform") or "web").strip()
            user_id = str(data.get("user_id") or "").strip()
            consent_ts = data.get("consent_timestamp") or datetime.now().strftime("%d.%m.%Y %H:%M")

            # 1. Валидация полей через CandidateService
            ok_fio, clean_fio, err_fio = candidate_service.validate_fio(full_name)
            if not ok_fio:
                return web.json_response({"error": err_fio or "Некорректное ФИО (минимум 2 слова на русском)"}, status=400)

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
                "source": source,
                "referrer_tab": referrer_tab,
                "consent_timestamp": consent_ts,
                "extra_info": f"Табельный номер рекомендателя: {referrer_tab}" if referrer_tab else "",
            }

            # 2. Регистрация через сервисный слой
            ticket_id, meta = candidate_service.register_candidate(reg_data, platform=platform)

            # 3. Формирование карточки для отдела кадров
            cand = self.db.get_candidate(ticket_id)
            if cand:
                base_card = texts.format_hr_card_full(cand)
            else:
                base_card = texts.format_hr_card_full(reg_data)

            hr_card = f"🌐 <b>[WEB-Заявка с сайта / Mini App]</b>\n\n{base_card}"
            ticket_kb = make_ticket_keyboard(ticket_id)

            # 4. Моментальная маршрутизация в кадровый чат (HR_GROUP_ID)
            if self.bot:
                try:
                    await route_new_candidate_ticket(self.bot, hr_card, ticket_kb)
                except Exception as e:
                    logger.error("Ошибка маршрутизации WEB-тикета #%s в HR-чат: %s", ticket_id, e)

                # Дублирование подтверждения соискателю в бот (если передан Telegram ID)
                if platform == "tg" and user_id.lstrip("-").isdigit():
                    try:
                        is_driver_no_exp = bool(meta.get("offer_training", False))
                        user_msg = texts.format_survey_success(ticket_id, vacancy, is_driver_no_exp)
                        await safe_send(self.bot, int(user_id), user_msg)
                    except Exception as e:
                        logger.error("Ошибка отправки подтверждения в бот соискателю %s: %s", user_id, e)

            logger.info("Успешно принята WEB-анкета #%s: %s (%s, %s)", ticket_id, clean_fio, clean_ph, vacancy)
            return web.json_response({
                "ok": True,
                "ticket_id": ticket_id,
                "full_name": clean_fio,
                "vacancy": vacancy,
                "offer_training": meta.get("offer_training", False),
                "message": f"Анкета #{ticket_id} успешно зарегистрирована!"
            })
        except Exception as e:
            logger.error("Исключение в handle_api_apply_candidate: %s", e)
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_inquiries(self, request: web.Request) -> web.Response:
        inquiries = []
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT inquiry_id, ticket_id, platform, user_id, full_name, phone, vacancy,
                       question_text, status, created_at
                FROM inquiries
                ORDER BY inquiry_id DESC
                LIMIT 50
                """
            )
            for r in cur.fetchall():
                inquiries.append({
                    "inquiry_id": r[0],
                    "ticket_id": r[1],
                    "platform": r[2],
                    "user_id": r[3],
                    "full_name": r[4] or "",
                    "phone": r[5] or "",
                    "vacancy": r[6] or "",
                    "question_text": r[7] or "",
                    "status": r[8] or "Новое",
                    "created_at": r[9] or "",
                })
        return web.json_response(inquiries)

    async def handle_api_inquiry_reply(self, request: web.Request) -> web.Response:
        inquiry_id = int(request.match_info["inquiry_id"])
        data = await request.json()
        reply_text = data.get("reply", "").strip()
        if not reply_text:
            return web.json_response({"error": "Reply text required"}, status=400)

        inq = self.db.get_inquiry(inquiry_id)
        if not inq:
            return web.json_response({"error": "Inquiry not found"}, status=404)

        user_id = inq[3]
        platform = inq[2]
        self.db.reply_inquiry(inquiry_id, reply_text)

        if self.bot and platform == "tg" and str(user_id).lstrip("-").isdigit():
            try:
                from common import safe_send
                await safe_send(
                    self.bot,
                    int(user_id),
                    f"💬 <b>Ответ специалиста кадров МУП «УЭТ»:</b>\n\n{html.escape(reply_text)}"
                )
            except Exception as e:
                logger.warning("Не удалось переслать ответ соискателю: %s", e)

        return web.json_response({"ok": True, "inquiry_id": inquiry_id})

    async def handle_api_system(self, request: web.Request) -> web.Response:
        uptime_seconds = int((datetime.now() - self.start_time).total_seconds())
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}ч {minutes}м {seconds}с"

        # Метрики памяти через /proc без psutil
        rss_mb = 0.0
        sys_ram_used = "—"
        sys_ram_total = "—"
        try:
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss_mb = round(int(line.split()[1]) / 1024, 1)
                        break
        except Exception:
            pass

        try:
            mem_info = {}
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        mem_info[parts[0].strip()] = int(parts[1].strip().split()[0])
            total_kb = mem_info.get("MemTotal", 0)
            avail_kb = mem_info.get("MemAvailable", 0)
            if total_kb > 0:
                sys_ram_total = round(total_kb / 1024 / 1024, 2)
                sys_ram_used = round((total_kb - avail_kb) / 1024 / 1024, 2)
        except Exception:
            pass

        db_prod_path = Path(self.db.db_path)
        db_test_path = Path(self.db.test_db_path)
        db_prod_kb = round(db_prod_path.stat().st_size / 1024, 1) if db_prod_path.exists() else 0
        db_test_kb = round(db_test_path.stat().st_size / 1024, 1) if db_test_path.exists() else 0

        db_prod_rows = 0
        db_test_rows = 0
        with self.db.connection(is_test=False) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM candidates")
            db_prod_rows = cur.fetchone()[0]
        with self.db.connection(is_test=True) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM candidates")
            db_test_rows = cur.fetchone()[0]

        return web.json_response({
            "uptime": uptime_str,
            "process_rss_mb": rss_mb,
            "process_ram_pct": round(rss_mb / (float(sys_ram_total) * 10.24), 1) if sys_ram_total != "—" and float(sys_ram_total) > 0 else 0,
            "system_ram_used_gb": sys_ram_used,
            "system_ram_total_gb": sys_ram_total,
            "db_prod_kb": db_prod_kb,
            "db_test_kb": db_test_kb,
            "db_prod_rows": db_prod_rows,
            "db_test_rows": db_test_rows,
            "bot_version": CONFIG.get("BOT_VERSION", "1.7.0"),
        })

    async def handle_api_toggle_db(self, request: web.Request) -> web.Response:
        cur_db = self.db.get_setting("active_db_target", "resumes.db")
        new_db = "resumes_test.db" if cur_db == "resumes.db" else "resumes.db"
        self.db.set_setting("active_db_target", new_db)
        logger.info("Веб-панель: активная база переключена на %s", new_db)
        return web.json_response({"ok": True, "active_db": new_db})

    async def handle_api_toggle_env(self, request: web.Request) -> web.Response:
        cur_env = CONFIG.get("ENVIRONMENT", "TEST").upper()
        new_env = "PROD" if cur_env == "TEST" else "TEST"
        CONFIG["ENVIRONMENT"] = new_env
        update_env_variable("ENVIRONMENT", new_env)
        logger.info("Веб-панель: режим окружения переключен на %s", new_env)
        return web.json_response({"ok": True, "env": new_env})

    async def handle_api_backup(self, request: web.Request) -> web.Response:
        backup_dir = Path(CONFIG.get("BACKUP_DIR", "/root/uetbot/backups"))
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_name = f"web_backup_{ts}.db"
        target_path = backup_dir / target_name
        shutil.copy2(self.db.db_path, target_path)
        logger.info("Веб-панель: бэкап сохранен в %s", target_path)
        return web.json_response({"ok": True, "filename": target_name})

    async def handle_api_optimize_db(self, request: web.Request) -> web.Response:
        ok = self.db.checkpoint_and_optimize()
        return web.json_response({"ok": ok})

    async def handle_api_export_csv(self, request: web.Request) -> web.Response:
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writerow([
            "№ Анкеты", "Дата создания", "ФИО", "Телефон", "Вакансия",
            "Опыт", "Статус", "Категории прав", "Город", "Депо собеседования",
            "Время собеседования", "Заметка кадровика", "Платформа"
        ])

        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ticket_id, created_at, full_name, phone, vacancy,
                       experience, status, driver_license, city, interview_depot,
                       interview_time, admin_note, platform
                FROM candidates
                ORDER BY ticket_id DESC
                """
            )
            for r in cur.fetchall():
                writer.writerow(list(r))

        csv_content = "\ufeff" + output.getvalue()
        filename = f"candidates_export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "text/csv; charset=utf-8-sig",
        }
        return web.Response(text=csv_content, headers=headers)

    # SUPERADMIN & GIT API
    async def handle_api_super_git_info(self, request: web.Request) -> web.Response:
        branch = "beta"
        last_commit = "—"
        try:
            b_out = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(CONFIG.BASE_DIR)).decode().strip()
            if b_out:
                branch = b_out
            c_out = subprocess.check_output(
                ["git", "log", "-1", "--pretty=format:%h - %s (%cd)", "--date=short"],
                cwd=str(CONFIG.BASE_DIR)
            ).decode().strip()
            if c_out:
                last_commit = c_out
        except Exception as e:
            logger.warning("Git info error: %s", e)

        return web.json_response({
            "branch": branch,
            "last_commit": last_commit,
        })

    async def handle_api_super_git_pull(self, request: web.Request) -> web.Response:
        try:
            output = subprocess.check_output(["git", "pull"], cwd=str(CONFIG.BASE_DIR), stderr=subprocess.STDOUT).decode().strip()
            return web.json_response({"ok": True, "output": output})
        except Exception as e:
            return web.json_response({"ok": False, "output": str(e)}, status=500)

    async def handle_api_super_restart_bot(self, request: web.Request) -> web.Response:
        logger.warning("SuperAdmin запросил рестарт бота через веб-панель!")
        # Запускаем отложенный перезапуск через 1 секунду
        asyncio.create_task(self._delayed_restart())
        return web.json_response({"ok": True, "message": "Служба перезапускается..."})

    async def _delayed_restart(self) -> None:
        await asyncio.sleep(1.0)
        os.system("systemctl restart uet_bot")

    async def handle_api_super_clear_tests(self, request: web.Request) -> web.Response:
        cand_cnt, inq_cnt = self.db.reset_all_test_data()
        logger.info("Веб-панель: удалено тестовых анкет: %s, вопросов: %s", cand_cnt, inq_cnt)
        return web.json_response({"ok": True, "deleted_candidates": cand_cnt, "deleted_inquiries": inq_cnt})

    async def handle_api_super_destruction_logs(self, request: web.Request) -> web.Response:
        logs = []
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, candidate_id, platform, reason, act_number, destroyed_at
                FROM destruction_logs
                ORDER BY id DESC LIMIT 50
                """
            )
            for r in cur.fetchall():
                logs.append({
                    "id": r[0],
                    "candidate_id": r[1],
                    "platform": r[2] or "tg",
                    "reason": r[3] or "",
                    "act_number": r[4] or "",
                    "destroyed_at": r[5] or "",
                })
        return web.json_response(logs)

    async def handle_api_super_set_cooldown(self, request: web.Request) -> web.Response:
        data = await request.json()
        sec = int(data.get("seconds", 1200))
        self.db.set_setting("cooldown_seconds", str(sec))
        CONFIG["COOLDOWN_SECONDS"] = sec
        update_env_variable("COOLDOWN_SECONDS", sec)
        return web.json_response({"ok": True, "cooldown_seconds": sec})

    # ADMINS MANAGEMENT API
    async def handle_api_admins(self, request: web.Request) -> web.Response:
        admins = []
        super_id = CONFIG.get("SUPER_ADMIN_ID", 0)
        tech_id = CONFIG.get("TECH_ADMIN_ID", 0)
        self.db.sync_system_admins(super_id, tech_id)
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT user_id, role, notify_dm, web_password, phone, full_name, added_at
                FROM admins
                ORDER BY added_at DESC
                """
            )
            for r in cur.fetchall():
                admins.append({
                    "user_id": r[0],
                    "role": r[1] or "hr",
                    "notify_dm": bool(r[2]),
                    "has_password": bool(r[3]),
                    "phone": r[4] or "",
                    "full_name": r[5] or "",
                    "added_at": r[6] or "",
                })
        return web.json_response(admins)

    async def handle_api_admins_add(self, request: web.Request) -> web.Response:
        data = await request.json()
        uid = int(data.get("user_id", 0))
        role = data.get("role", "hr")
        pwd = str(data.get("password", "")).strip()
        if uid > 0:
            if self.bot:
                from common import grant_admin_and_sync
                await grant_admin_and_sync(self.bot, uid, role=role, password=pwd)
            else:
                self.db.add_admin(uid, role=role)
                if pwd:
                    self.db.set_admin_password(uid, pwd)

            return web.json_response({"ok": True, "user_id": uid, "role": role})
        return web.json_response({"error": "Invalid user_id"}, status=400)

    async def handle_api_admins_remove(self, request: web.Request) -> web.Response:
        data = await request.json()
        uid = int(data.get("user_id", 0))
        super_id = CONFIG.get("SUPER_ADMIN_ID", 0)
        if uid > 0 and uid != super_id:
            if self.bot:
                from common import revoke_admin_and_sync
                await revoke_admin_and_sync(self.bot, uid, reason="Отозвано через веб-панель управления")
            else:
                self.db.remove_admin(uid)
            return web.json_response({"ok": True, "user_id": uid})
        return web.json_response({"error": "Cannot remove superadmin or invalid ID"}, status=400)

    async def handle_api_admins_set_password(self, request: web.Request) -> web.Response:
        data = await request.json()
        uid = int(data.get("user_id", 0))
        pwd = str(data.get("password", "")).strip()
        if uid <= 0 or not pwd:
            return web.json_response({"error": "user_id и password обязательны"}, status=400)

        ok = self.db.set_admin_password(uid, pwd)
        if ok and self.bot:
            try:
                from common import safe_send
                direct_link = CONFIG.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")
                await safe_send(
                    self.bot,
                    uid,
                    f"🔑 <b>Для вас установлен пароль в веб-панели МУП «УЭТ»!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Пароль: <code>{pwd}</code>\n"
                    f"Ссылка: <a href='{direct_link}'>{direct_link}</a>\n\n"
                    f"<i>Вы можете сменить его в любой момент командой:</i> <code>/password НОВЫЙ_ПАРОЛЬ</code>"
                )
            except Exception as e:
                logger.warning("Не удалось уведомить сотрудника %s о пароле: %s", uid, e)

        return web.json_response({"ok": ok, "user_id": uid})

    async def handle_api_admins_notify_panel(self, request: web.Request) -> web.Response:
        data = await request.json()
        target_uid = int(data.get("user_id", 0))
        all_admins = self.db.get_all_admins()
        super_id = CONFIG.get("SUPER_ADMIN_ID", 0)
        direct_link = CONFIG.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")

        targets = [target_uid] if target_uid > 0 else [aid for aid, _ in all_admins if aid != super_id]
        sent = 0
        if self.bot:
            from common import safe_send
            for aid in targets:
                role = self.db.get_admin_role(aid)
                role_label = "Отдел кадров (HR)" if role == "hr" else "Инженер (Tech)"
                text = (
                    "🌐 <b>СЛУЖЕБНАЯ ВЕБ-ПАНЕЛЬ МУП «УЛЬЯНОВСКЭЛЕКТРОТРАНС»</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Вам открыт доступ к рабочей веб-панели предприятия ({role_label}).\n\n"
                    f"👉 <b>Адрес:</b> <a href='{direct_link}'>{direct_link}</a>\n\n"
                    "🔑 <b>Как войти:</b>\n"
                    "1. Задайте любой пароль для входа, отправив команду боту:\n"
                    "   👉 <code>/password ВАШ_ПАРОЛЬ</code> <i>(например: /password tram2026)</i>\n"
                    "2. Перейдите по ссылке на сайт и введите ваш пароль.\n"
                    "3. Или откройте панель командой <code>/panel</code> прямо в Telegram!\n"
                    "━━━━━━━━━━━━━━━━━━━━━"
                )
                if await safe_send(self.bot, aid, text):
                    sent += 1

        return web.json_response({"ok": True, "sent_count": sent})

    async def handle_api_admins_sync(self, request: web.Request) -> web.Response:
        super_id = CONFIG.get("SUPER_ADMIN_ID", 0)
        tech_id = CONFIG.get("TECH_ADMIN_ID", 0)
        self.db.sync_system_admins(super_id, tech_id)
        if self.bot:
            try:
                from common import setup_bot_commands
                await setup_bot_commands(self.bot)
            except Exception:
                pass
        return web.json_response({"ok": True})

    async def handle_api_admins_mute_all(self, request: web.Request) -> web.Response:
        cnt = self.db.disable_all_hr_notifications()
        return web.json_response({"ok": True, "muted_count": cnt})

    async def handle_api_admins_set_role(self, request: web.Request) -> web.Response:
        data = await request.json()
        uid = int(data.get("user_id", 0))
        role = str(data.get("role", "hr")).strip().lower()
        if role not in ("hr", "tech", "depot"):
            return web.json_response({"error": "Недопустимая роль"}, status=400)
        super_id = CONFIG.get("SUPER_ADMIN_ID", 0)
        if uid == super_id:
            return web.json_response({"error": "Роль Главного администратора изменить нельзя"}, status=400)
        if uid <= 0:
            return web.json_response({"error": "Неверный ID пользователя"}, status=400)
        if self.bot:
            from common import grant_admin_and_sync
            await grant_admin_and_sync(self.bot, uid, role=role)
        else:
            self.db.set_admin_role(uid, role)
        return web.json_response({"ok": True, "user_id": uid, "role": role})

    async def handle_api_admins_toggle_notify(self, request: web.Request) -> web.Response:
        data = await request.json()
        uid = int(data.get("user_id", 0))
        if uid <= 0:
            return web.json_response({"error": "Неверный ID пользователя"}, status=400)
        new_status = self.db.toggle_admin_notify(uid)
        return web.json_response({"ok": True, "user_id": uid, "notify_dm": new_status})

    async def handle_api_settings_toggle_notifications(self, request: web.Request) -> web.Response:
        data = await request.json()
        enabled = bool(data.get("enabled", True))
        status_int = 1 if enabled else 0
        cnt = self.db.set_all_hr_notify_dm(status_int)
        self.db.set_setting("notifications_global_enabled", "1" if enabled else "0")
        return web.json_response({"ok": True, "enabled": enabled, "affected_admins": cnt})

    async def handle_api_depot_referrals(self, request: web.Request) -> web.Response:
        user_id = request.query.get("user_id")
        if user_id and str(user_id).isdigit():
            stats = self.db.get_depot_referrals(int(user_id))
            return web.json_response(stats)
        all_refs = self.db.get_all_depot_referrals()
        return web.json_response({"referrals": all_refs, "total": len(all_refs)})

    async def handle_api_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "time": datetime.now().isoformat(),
            "version": CONFIG.get("BOT_VERSION", "1.7.0"),
        })


async def start_web_panel_task(
    db: ResumeDB,
    bot: Optional[Bot] = None,
    host: str = "0.0.0.0",
    port: int = 8080
) -> None:
    """Запуск фонового веб-сервера кадровой панели и личного кабинета."""
    panel = WebPanel(db, bot=bot)
    runner = web.AppRunner(panel.app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("🌐 Веб-панель управления МУП «Ульяновскэлектротранс» запущена на http://%s:%s", host, port)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
