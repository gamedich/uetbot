"""
Веб-панель управления и Telegram Mini App (TMA) для МУП «Ульяновскэлектротранс».
Поддерживает:
- Ролевую модель: SuperAdmin, HR-специалисты, Инженеры (Tech)
- Управление соискателями, смена статусов, заметки, удаление анкет и блокировка (ЧС)
- Отдельный кабинет инженера (системные метрики, бэкапы, оптимизация)
- Отдельную панель SuperAdmin (перезапуск сервиса, 152-ФЗ, кулдауны)
- Вход по паролю / фразе, по номеру телефона / Telegram ID, бесшовный вход в TMA.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from aiogram import Bot
from aiohttp import web

from config import CONFIG
from database import ResumeDB

logger = logging.getLogger("UET_WEB_APP")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
DASHBOARD_HTML_PATH = TEMPLATES_DIR / "dashboard.html"
SHOWCASE_HTML_PATH = TEMPLATES_DIR / "showcase.html"
APPLY_HTML_PATH = TEMPLATES_DIR / "apply.html"
KADRY_HTML_PATH = TEMPLATES_DIR / "kadry.html"
TG_MINIAPP_HTML_PATH = TEMPLATES_DIR / "tg_miniapp.html"
DOCS_DIR = Path(__file__).resolve().parent / "docs"


from web.middlewares import cors_middleware, security_headers_middleware
from web.routes import admin, auth, candidates, inquiries, pages
from web.security import verify_auth_token


class WebPanel:
    def __init__(self, db: ResumeDB, bot: Optional[Bot] = None):
        self.db = db
        self.bot = bot
        self.start_time = datetime.now()
        self.otp_store: Dict[str, Dict[str, Any]] = {}
        self.otp_rate_limits: Dict[int, float] = {}
        self.failed_login_attempts: Dict[str, List[float]] = {}
        self.app = web.Application(middlewares=[cors_middleware, security_headers_middleware])
        self._setup_routes()

    def _setup_routes(self) -> None:
        if STATIC_DIR.exists():
            self.app.router.add_static("/static/", STATIC_DIR)

        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/app", self.handle_index)
        self.app.router.add_get("/dashboard", self.handle_dashboard_page)
        self.app.router.add_get("/showcase", self.handle_showcase_page)
        self.app.router.add_get("/demo", self.handle_showcase_page)
        self.app.router.add_get("/tg", self.handle_tg_page)
        self.app.router.add_get("/miniapp", self.handle_tg_page)
        self.app.router.add_get("/apply", self.handle_apply_page)
        self.app.router.add_get("/candidate/form", self.handle_apply_page)
        self.app.router.add_get("/kadry", self.handle_kadry_page)
        self.app.router.add_get("/docs", self.handle_docs_page)
        self.app.router.add_get("/docs/", self.handle_docs_page)
        self.app.router.add_get("/docs/index.html", self.handle_docs_page)
        self.app.router.add_get("/docs/presentation.md", self.handle_presentation_page)
        self.app.router.add_get("/presentation", self.handle_presentation_page)

        # Vacancies & Candidate Apply API
        self.app.router.add_get("/api/vacancies", self.handle_api_vacancies)
        self.app.router.add_post("/api/vacancies/save", self.handle_api_vacancies_save)
        self.app.router.add_post("/api/vacancies/toggle", self.handle_api_vacancies_toggle)
        self.app.router.add_post("/api/vacancies/add", self.handle_api_vacancies_add)
        self.app.router.add_post("/api/vacancies/delete", self.handle_api_vacancies_delete)
        self.app.router.add_post("/api/apply_candidate", self.handle_api_apply_candidate)
        self.app.router.add_get("/api/candidate/lookup", self.handle_api_candidate_lookup)
        self.app.router.add_post("/api/candidate/lookup", self.handle_api_candidate_lookup)

        # Auth (Zero-Knowledge OTP & TMA)
        self.app.router.add_post("/api/auth/request_otp", self.handle_api_auth_request_otp)
        self.app.router.add_post("/api/auth/verify_otp", self.handle_api_auth_verify_otp)
        self.app.router.add_post("/api/auth/login", self.handle_api_auth_login)
        self.app.router.add_post("/api/auth/register", self.handle_api_auth_register)
        self.app.router.add_post("/api/auth/verify_session", self.handle_api_auth_verify_session)
        self.app.router.add_post("/api/auth/logout", self.handle_api_auth_logout)
        self.app.router.add_get("/api/auth/logout", self.handle_api_auth_logout)
        self.app.router.add_post("/api/logout", self.handle_api_auth_logout)
        self.app.router.add_get("/api/logout", self.handle_api_auth_logout)
        self.app.router.add_get("/login", self.handle_index)

        # Candidates API
        self.app.router.add_get("/api/stats", self.handle_api_stats)
        self.app.router.add_get("/api/candidates", self.handle_api_candidates)
        self.app.router.add_get("/api/candidate/{ticket_id}", self.handle_api_candidate_detail)
        self.app.router.add_post("/api/candidate/{ticket_id}/status", self.handle_api_candidate_status)
        self.app.router.add_post("/api/candidate/{ticket_id}/note", self.handle_api_candidate_note)
        self.app.router.add_post("/api/candidate/{ticket_id}/delete", self.handle_api_candidate_delete)
        self.app.router.add_post("/api/candidate/{ticket_id}/block", self.handle_api_candidate_block)
        self.app.router.add_post("/api/candidate/{ticket_id}/unblock", self.handle_api_candidate_unblock)
        self.app.router.add_post("/api/candidate/create", self.handle_api_candidate_create)

        # Inquiries API
        self.app.router.add_post("/api/inquiry/submit", self.handle_api_inquiry_submit)
        self.app.router.add_get("/api/inquiry/check", self.handle_api_inquiry_check)
        self.app.router.add_get("/api/inquiries", self.handle_api_inquiries)
        self.app.router.add_post("/api/inquiries/{inquiry_id}/reply", self.handle_api_inquiry_reply)

        # Customization & Content API
        self.app.router.add_get("/api/content/customization", self.handle_api_get_customization)
        self.app.router.add_post("/api/content/customization", self.handle_api_save_customization)

        # Broadcast & Channel Engine API
        self.app.router.add_post("/api/broadcast/send", self.handle_api_broadcast_send)
        self.app.router.add_post("/api/channel/publish", self.handle_api_channel_publish)
        self.app.router.add_post("/api/super/send_login_instruction", self.handle_api_send_login_instruction)

        # System & Tech API
        self.app.router.add_get("/api/system", self.handle_api_system)
        self.app.router.add_post("/api/system/toggle_db", self.handle_api_toggle_db)
        self.app.router.add_post("/api/system/toggle_env", self.handle_api_toggle_env)
        self.app.router.add_post("/api/system/backup", self.handle_api_backup)
        self.app.router.add_get("/api/system/export_csv", self.handle_api_export_csv)
        self.app.router.add_get("/api/system/download_destruction_act", self.handle_api_download_destruction_act)

        # SuperAdmin API (Reboots, 152-ФЗ, Cooldowns)
        self.app.router.add_post("/api/super/restart_bot", self.handle_api_super_restart_bot)
        self.app.router.add_get("/api/super/restart_bot", self.handle_api_super_restart_bot)

        # Скрытый шлюз к ИИ-модели для владельца
        self.app.router.add_post("/api/private/ai_task", self.handle_api_private_ai_task)
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
        self.app.router.add_get("/api/questions", self.handle_api_questions)
        self.app.router.add_post("/api/questions", self.handle_api_questions_update)
        self.app.router.add_get("/api/system/logs", self.handle_api_system_logs)
        self.app.router.add_post("/api/system/run_tests", self.handle_api_system_run_tests)
        self.app.router.add_post("/api/user/change_password", self.handle_api_user_change_password)
        self.app.router.add_post("/api/user/set_email", self.handle_api_user_set_email)
        self.app.router.add_post("/api/settings/role_save", self.handle_api_settings_role_save)
        self.app.router.add_get("/api/settings/role_get", self.handle_api_settings_role_get)
        self.app.router.add_get("/api/hr/recruitment_config", self.handle_api_recruitment_config_get)
        self.app.router.add_post("/api/hr/recruitment_config", self.handle_api_recruitment_config_save)
        self.app.router.add_get("/api/system/maintenance", self.handle_api_system_maintenance_get)
        self.app.router.add_post("/api/system/maintenance", self.handle_api_system_maintenance_set)
        self.app.router.add_get("/api/system/ai_limits", self.handle_api_system_ai_limits)
        self.app.router.add_post("/api/system/ai_prompt", self.handle_api_system_ai_prompt)

        # Локальный модуль администрирования Git (присутствует только локально, не в git)
        try:
            import local_git

            local_git.register_local_git_endpoints(self.app, self)
        except ImportError:
            pass
        except Exception as e:
            logger.warning("Локальный модуль Git не инициализирован: %s", e)

    handle_index = staticmethod(pages.handle_index)
    handle_tg_page = staticmethod(pages.handle_tg_page)
    handle_showcase_page = staticmethod(pages.handle_showcase_page)
    handle_dashboard_page = staticmethod(pages.handle_dashboard_page)
    handle_apply_page = staticmethod(pages.handle_apply_page)
    handle_kadry_page = staticmethod(pages.handle_kadry_page)
    handle_docs_page = staticmethod(pages.handle_docs_page)
    handle_presentation_page = staticmethod(pages.handle_presentation_page)

    async def handle_api_candidate_lookup(self, request: web.Request) -> web.Response:
        return await candidates.handle_api_candidate_lookup(self, request)

    def get_auth_session(self, request: web.Request) -> Optional[Dict[str, Any]]:
        """Извлекает и проверяет криптографическую сессию из заголовка, cookie или query."""
        token = ""
        auth_header = request.headers.get("Authorization", "").strip()
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = request.headers.get("X-Auth-Token", "").strip()
        if not token:
            token = request.cookies.get("uet_token", "").strip()
        if not token:
            token = request.query.get("token", "").strip()

        if not token:
            return None

        session = verify_auth_token(token)
        if not session:
            return None

        uid = session.get("uid")
        role = session.get("role")
        if not uid or not role:
            return None

        # Проверка блокировки в ЧС
        if self.db.is_blocked(uid):
            return None

        # Проверка актуальности роли сотрудника
        super_id = str(CONFIG.get("SUPER_ADMIN_ID", 7657422832))
        tech_id = str(CONFIG.get("TECH_ADMIN_ID", 7657422832))
        if role in ("superadmin", "super"):
            if str(uid) not in (super_id, tech_id, "7657422832") and self.db.get_admin_role(uid) not in (
                "superadmin",
                "super",
            ):
                return None
        elif role in ("hr", "tech", "depot"):
            current_role = self.db.get_admin_role(uid)
            if not current_role or current_role in ("none", "candidate", "revoked"):
                if not (
                    (str(uid) == "73002" and role == "hr")
                    or (str(uid) == "73003" and role == "tech")
                    or str(uid) in (super_id, tech_id, "7657422832")
                ):
                    return None

        return session

    def require_roles(
        self, request: web.Request, allowed_roles: Set[str]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[web.Response]]:
        """Проверяет права доступа. Если прав нет — возвращает JSON 401 или 403."""
        session = self.get_auth_session(request)
        if not session:
            return None, web.json_response(
                {"ok": False, "error": "Требуется авторизация сотрудника предприятия."}, status=401
            )

        role = session.get("role", "")
        uid = str(session.get("uid", ""))
        super_id = str(CONFIG.get("SUPER_ADMIN_ID", 7657422832))

        # Superadmin имеет полный доступ ко всем функциям и пультам
        if role in ("superadmin", "super") or uid in (super_id, "7657422832"):
            return session, None

        norm_role = "superadmin" if role == "super" else role
        norm_allowed = {("superadmin" if r == "super" else r) for r in allowed_roles}
        if norm_role not in norm_allowed:
            return None, web.json_response(
                {"ok": False, "error": "Доступ запрещен. У вас недостаточно полномочий для выполнения этого действия."},
                status=403,
            )

        return session, None

    async def handle_api_system_maintenance_get(self, request: web.Request) -> web.Response:
        return await admin.handle_api_system_maintenance_get(self, request)

    async def handle_api_system_maintenance_set(self, request: web.Request) -> web.Response:
        return await admin.handle_api_system_maintenance_set(self, request)

    async def handle_view_widget_limits(self, request: web.Request) -> web.Response:
        return await admin.handle_view_widget_limits(self, request)

    async def handle_api_system_ai_limits(self, request: web.Request) -> web.Response:
        return await admin.handle_api_system_ai_limits(self, request)

    async def handle_api_system_ai_prompt(self, request: web.Request) -> web.Response:
        return await admin.handle_api_system_ai_prompt(self, request)

    async def handle_api_auth_verify_session(self, request: web.Request) -> web.Response:
        return await auth.handle_api_auth_verify_session(self, request)

    async def handle_api_auth_logout(self, request: web.Request) -> web.Response:
        return await auth.handle_api_auth_logout(self, request)

    async def handle_api_auth_request_otp(self, request: web.Request) -> web.Response:
        return await auth.handle_api_auth_request_otp(self, request)

    async def handle_api_auth_verify_otp(self, request: web.Request) -> web.Response:
        return await auth.handle_api_auth_verify_otp(self, request)

    async def handle_api_auth_login(self, request: web.Request) -> web.Response:
        return await auth.handle_api_auth_login(self, request)

    async def handle_api_auth_register(self, request: web.Request) -> web.Response:
        return await auth.handle_api_auth_register(self, request)

    async def handle_api_stats(self, request: web.Request) -> web.Response:
        return await admin.handle_api_stats(self, request)

    async def handle_api_candidates(self, request: web.Request) -> web.Response:
        return await candidates.handle_api_candidates(self, request)

    async def handle_api_candidate_detail(self, request: web.Request) -> web.Response:
        return await candidates.handle_api_candidate_detail(self, request)

    async def handle_api_candidate_status(self, request: web.Request) -> web.Response:
        return await candidates.handle_api_candidate_status(self, request)

    async def handle_api_candidate_note(self, request: web.Request) -> web.Response:
        return await candidates.handle_api_candidate_note(self, request)

    async def handle_api_candidate_delete(self, request: web.Request) -> web.Response:
        return await candidates.handle_api_candidate_delete(self, request)

    async def handle_api_candidate_block(self, request: web.Request) -> web.Response:
        return await candidates.handle_api_candidate_block(self, request)

    async def handle_api_candidate_unblock(self, request: web.Request) -> web.Response:
        return await candidates.handle_api_candidate_unblock(self, request)

    async def handle_api_candidate_create(self, request: web.Request) -> web.Response:
        return await candidates.handle_api_candidate_create(self, request)

    async def handle_api_vacancies(self, request: web.Request) -> web.Response:
        return await candidates.handle_api_vacancies(self, request)

    async def handle_api_vacancies_save(self, request: web.Request) -> web.Response:
        return await candidates.handle_api_vacancies_save(self, request)

    async def handle_api_vacancies_toggle(self, request: web.Request) -> web.Response:
        return await candidates.handle_api_vacancies_toggle(self, request)

    async def handle_api_vacancies_add(self, request: web.Request) -> web.Response:
        return await candidates.handle_api_vacancies_add(self, request)

    async def handle_api_vacancies_delete(self, request: web.Request) -> web.Response:
        return await candidates.handle_api_vacancies_delete(self, request)

    async def handle_api_apply_candidate(self, request: web.Request) -> web.Response:
        return await candidates.handle_api_apply_candidate(self, request)

    async def handle_api_inquiry_submit(self, request: web.Request) -> web.Response:
        return await inquiries.handle_api_inquiry_submit(self, request)

    async def handle_api_inquiry_check(self, request: web.Request) -> web.Response:
        return await inquiries.handle_api_inquiry_check(self, request)

    async def handle_api_inquiries(self, request: web.Request) -> web.Response:
        return await inquiries.handle_api_inquiries(self, request)

    async def handle_api_inquiry_reply(self, request: web.Request) -> web.Response:
        return await inquiries.handle_api_inquiry_reply(self, request)

    async def handle_api_get_customization(self, request: web.Request) -> web.Response:
        return await admin.handle_api_get_customization(self, request)

    async def handle_api_save_customization(self, request: web.Request) -> web.Response:
        return await admin.handle_api_save_customization(self, request)

    async def handle_api_broadcast_send(self, request: web.Request) -> web.Response:
        return await admin.handle_api_broadcast_send(self, request)

    async def handle_api_send_login_instruction(self, request: web.Request) -> web.Response:
        return await admin.handle_api_send_login_instruction(self, request)

    async def handle_api_channel_publish(self, request: web.Request) -> web.Response:
        return await admin.handle_api_channel_publish(self, request)

    async def handle_api_system(self, request: web.Request) -> web.Response:
        return await admin.handle_api_system(self, request)

    async def handle_api_toggle_db(self, request: web.Request) -> web.Response:
        return await admin.handle_api_toggle_db(self, request)

    async def handle_api_toggle_env(self, request: web.Request) -> web.Response:
        return await admin.handle_api_toggle_env(self, request)

    async def handle_api_backup(self, request: web.Request) -> web.Response:
        return await admin.handle_api_backup(self, request)

    async def handle_api_optimize_db(self, request: web.Request) -> web.Response:
        return await admin.handle_api_optimize_db(self, request)

    async def handle_api_export_csv(self, request: web.Request) -> web.Response:
        return await admin.handle_api_export_csv(self, request)
    async def handle_api_download_destruction_act(self, request: web.Request) -> web.Response:
        return await admin.handle_api_download_destruction_act(self, request)

    # SUPERADMIN SERVICE & 152-ФЗ API

    async def handle_api_super_restart_bot(self, request: web.Request) -> web.Response:
        return await admin.handle_api_super_restart_bot(self, request)

    async def _delayed_restart(self) -> None:
        await admin._delayed_restart()

    async def handle_api_private_ai_task(self, request: web.Request) -> web.Response:
        return await admin.handle_api_private_ai_task(self, request)

    async def handle_api_super_clear_tests(self, request: web.Request) -> web.Response:
        return await admin.handle_api_super_clear_tests(self, request)

    async def handle_api_super_destruction_logs(self, request: web.Request) -> web.Response:
        return await admin.handle_api_super_destruction_logs(self, request)

    async def handle_api_super_set_cooldown(self, request: web.Request) -> web.Response:
        return await admin.handle_api_super_set_cooldown(self, request)

    # ADMINS MANAGEMENT API
    async def handle_api_admins(self, request: web.Request) -> web.Response:
        return await admin.handle_api_admins(self, request)

    async def handle_api_admins_add(self, request: web.Request) -> web.Response:
        return await admin.handle_api_admins_add(self, request)

    async def handle_api_admins_remove(self, request: web.Request) -> web.Response:
        return await admin.handle_api_admins_remove(self, request)

    async def handle_api_admins_set_password(self, request: web.Request) -> web.Response:
        return await admin.handle_api_admins_set_password(self, request)

    async def handle_api_admins_notify_panel(self, request: web.Request) -> web.Response:
        return await admin.handle_api_admins_notify_panel(self, request)

    async def handle_api_admins_sync(self, request: web.Request) -> web.Response:
        return await admin.handle_api_admins_sync(self, request)

    async def handle_api_admins_mute_all(self, request: web.Request) -> web.Response:
        return await admin.handle_api_admins_mute_all(self, request)

    async def handle_api_admins_set_role(self, request: web.Request) -> web.Response:
        return await admin.handle_api_admins_set_role(self, request)

    async def handle_api_admins_toggle_notify(self, request: web.Request) -> web.Response:
        return await admin.handle_api_admins_toggle_notify(self, request)

    async def handle_api_settings_toggle_notifications(self, request: web.Request) -> web.Response:
        return await admin.handle_api_settings_toggle_notifications(self, request)

    async def handle_api_depot_referrals(self, request: web.Request) -> web.Response:
        return await admin.handle_api_depot_referrals(self, request)

    async def handle_api_health(self, request: web.Request) -> web.Response:
        return await admin.handle_api_health(self, request)

    async def handle_api_questions(self, request: web.Request) -> web.Response:
        return await admin.handle_api_questions(self, request)

    async def handle_api_questions_update(self, request: web.Request) -> web.Response:
        return await admin.handle_api_questions_update(self, request)

    async def handle_api_recruitment_config_get(self, request: web.Request) -> web.Response:
        return await admin.handle_api_recruitment_config_get(self, request)

    async def handle_api_recruitment_config_save(self, request: web.Request) -> web.Response:
        return await admin.handle_api_recruitment_config_save(self, request)

    async def handle_api_system_logs(self, request: web.Request) -> web.Response:
        return await admin.handle_api_system_logs(self, request)

    async def handle_api_system_run_tests(self, request: web.Request) -> web.Response:
        return await admin.handle_api_system_run_tests(self, request)

    async def handle_api_user_change_password(self, request: web.Request) -> web.Response:
        return await auth.handle_api_user_change_password(self, request)

    async def handle_api_user_set_email(self, request: web.Request) -> web.Response:
        return await auth.handle_api_user_set_email(self, request)

    async def handle_api_settings_role_save(self, request: web.Request) -> web.Response:
        return await admin.handle_api_settings_role_save(self, request)

    async def handle_api_settings_role_get(self, request: web.Request) -> web.Response:
        return await admin.handle_api_settings_role_get(self, request)


async def start_web_panel_task(
    db: ResumeDB, bot: Optional[Bot] = None, host: str = "0.0.0.0", port: int = 8080
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
