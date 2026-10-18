"""
Общий модуль состояния, конфигурации, отказоустойчивости и служебных функций
бота МУП «Ульяновскэлектротранс».
Реализует безопасную отправку сообщений (Flood control, safe_send),
персистентные хранилища FSM (SQLite WAL) и ролевые фильтры.
"""

from __future__ import annotations

import asyncio
import collections
import html
import io
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple, Union

from aiogram import BaseMiddleware, Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import BaseFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    BufferedInputFile,
    MenuButtonDefault,
    MenuButtonWebApp,
    WebAppInfo,
)
from aiohttp import ClientError, ClientSession, ClientTimeout

from config import CONFIG, update_env_variable, validate_config
from database import ResumeDB
from texts import clean_html

# Безопасный вывод кодировок для консоли Windows
if sys.platform.startswith("win"):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
    except Exception:
        pass

# Настройка системного логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(name)s] - %(message)s",
)
logger = logging.getLogger("UET_HR_BOT")


# ==============================================================================
# ОПЕРАТИВНЫЙ БУФЕР ЛОГОВ (/logs)
# ==============================================================================


class MemoryLogHandler(logging.Handler):
    """Кольцевой буфер оперативных логов для удаленной диагностики через Telegram (/logs)."""

    def __init__(self, capacity: int = 120) -> None:
        super().__init__()
        self.buffer: collections.deque[str] = collections.deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.buffer.append(msg)
        except Exception:
            pass

    def get_logs(self, n: int = 30) -> List[str]:
        """Возвращает последние n строк логов."""
        return list(self.buffer)[-n:]


memory_log_handler = MemoryLogHandler(capacity=120)
memory_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(memory_log_handler)


# ==============================================================================
# ПЕРСИСТЕНТНОЕ ХРАНИЛИЩЕ FSM НА БАЗЕ SQLITE WAL
# ==============================================================================


class SQLiteFSMStorage(BaseStorage):
    """
    Персистентное хранилище состояний и данных FSM aiogram 3 в SQLite WAL.
    Гарантирует сохранение прогресса заполнения анкет при перезапуске службы.
    """

    def __init__(self, database: ResumeDB) -> None:
        self.db = database

    def _get_key_str(self, key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.destiny}"

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        state_str = state.state if isinstance(state, State) else state
        k = self._get_key_str(key)
        await asyncio.to_thread(self.db.set_fsm_state, k, state_str)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        k = self._get_key_str(key)
        return await asyncio.to_thread(self.db.get_fsm_state, k)

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        k = self._get_key_str(key)
        await asyncio.to_thread(self.db.set_fsm_data, k, data)

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        k = self._get_key_str(key)
        return await asyncio.to_thread(self.db.get_fsm_data, k)

    async def close(self) -> None:
        pass


class PersistentSessions(dict):
    """Персистентный кэш сессий соискателей VK / MAX с авто-сохранением в SQLite."""

    def __init__(self, database: ResumeDB) -> None:
        super().__init__()
        self.db = database
        try:
            for plat, uid, data in self.db.get_all_external_sessions():
                super().__setitem__((plat, str(uid)), data)
        except Exception:
            pass

    def __setitem__(self, key: Tuple[str, str], value: Dict[str, Any]) -> None:
        super().__setitem__(key, value)
        try:
            plat, uid = key
            self.db.set_external_session(plat, str(uid), value)
        except Exception:
            pass

    def __delitem__(self, key: Tuple[str, str]) -> None:
        if key in self:
            super().__delitem__(key)
        try:
            plat, uid = key
            self.db.delete_external_session(plat, str(uid))
        except Exception:
            pass

    def pop(self, key: Tuple[str, str], default: Any = None) -> Any:
        try:
            plat, uid = key
            self.db.delete_external_session(plat, str(uid))
        except Exception:
            pass
        return super().pop(key, default)


from aiogram.client.session.aiohttp import AiohttpSession


class NativeProxiedSession(AiohttpSession):
    """Кастомная сессия aiohttp с нативной поддержкой HTTP-туннеля для Telegram API."""

    def __init__(self, proxy_url: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.proxy_url = proxy_url

    async def create_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession()
        return self._session

    async def make_request(
        self,
        bot: Bot,
        method: Any,
        timeout: Optional[int] = None,
    ) -> Any:
        session = await self.create_session()
        url = self.api.api_url(token=bot.token, method=method.__api_method__)
        form = self.build_form_data(bot=bot, method=method)
        try:
            async with session.post(
                url,
                data=form,
                proxy=self.proxy_url,
                timeout=self.timeout if timeout is None else timeout,
            ) as resp:
                raw_result = await resp.text()
        except (ClientError, asyncio.TimeoutError) as proxy_err:
            if self.proxy_url:
                logger.warning(
                    "Сбой прокси %s (%s). Попытка прямого соединения с Telegram API...",
                    self.proxy_url,
                    proxy_err,
                )
                try:
                    async with session.post(
                        url,
                        data=form,
                        proxy=None,
                        timeout=self.timeout if timeout is None else timeout,
                    ) as resp:
                        raw_result = await resp.text()
                except Exception as direct_err:
                    raise TelegramNetworkError(
                        method=method,
                        message=f"Proxy error: {proxy_err} | Direct error: {direct_err}",
                    ) from direct_err
            else:
                raise TelegramNetworkError(
                    method=method, message=f"{type(proxy_err).__name__}: {proxy_err}"
                ) from proxy_err
        response = self.check_response(
            bot=bot,
            method=method,
            status_code=resp.status,
            content=raw_result,
        )
        return response.result


# ==============================================================================
# ИНИЦИАЛИЗАЦИЯ ЯДРА, БОТА И ДИСПЕТЧЕРА
# ==============================================================================

_db_path = CONFIG.get("DB_PATH", "data/resumes.db")
db = ResumeDB(db_path=_db_path)

_proxy_url = (
    CONFIG.get("PROXY_URL") or os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or os.getenv("HTTP_PROXY") or None
)
if _proxy_url:
    logger.info("Для Telegram Bot API активирован HTTP-прокси: %s", _proxy_url)
    _bot_session: Optional[AiohttpSession] = NativeProxiedSession(proxy_url=_proxy_url)
else:
    _bot_session = None

_bot_token_candidate = CONFIG.get("TG_BOT_TOKEN")
if not _bot_token_candidate or ":" not in _bot_token_candidate:
    # Валидный по формату aiogram фиктивный токен для CI и модульного тестирования
    _bot_token_candidate = f"{'1' * 9}:{'a' * 35}"

bot = Bot(
    token=_bot_token_candidate,
    session=_bot_session,
    default=DefaultBotProperties(parse_mode="HTML"),
)
dp = Dispatcher(storage=SQLiteFSMStorage(db))

START_TIME: float = time.time()
SYSTEM_METRICS: Dict[str, Any] = {
    "tg_online": True,
    "vk_online": False,
    "max_online": False,
    "errors_count": 0,
}

# Восстановление сохраненного режима из БД
saved_env = db.get_setting("environment", "")
if saved_env:
    CONFIG["ENVIRONMENT"] = saved_env

saved_maint = db.get_setting("maintenance_mode", "")
if saved_maint:
    CONFIG["MAINTENANCE_MODE"] = str(saved_maint).strip() in ("1", "true", "True")


def is_maintenance_active() -> bool:
    """Проверяет глобальный режим ТО предприятия (из БД или из CONFIG)."""
    try:
        val = db.get_setting("maintenance_mode", None)
        if val is not None:
            active = str(val).strip() in ("1", "true", "True")
            CONFIG["MAINTENANCE_MODE"] = active
            return active
    except Exception:
        pass
    return bool(CONFIG.get("MAINTENANCE_MODE", False))


def get_maintenance_info() -> Dict[str, Any]:
    """Возвращает информацию о текущем режиме ТО: статус, тип (planned/emergency), детали."""
    active = is_maintenance_active()
    maint_type = db.get_setting("maintenance_type", "emergency")
    maint_details = db.get_setting("maintenance_details", "")
    return {
        "active": active,
        "type": maint_type,
        "details": maint_details,
    }


def set_maintenance_active(active: bool, maint_type: str = "emergency", details: str = "") -> bool:
    """Устанавливает глобальный режим ТО, его тип (planned/emergency) и детали."""
    CONFIG["MAINTENANCE_MODE"] = bool(active)
    try:
        db.set_setting("maintenance_mode", "1" if active else "0")
        if active:
            db.set_setting("maintenance_type", str(maint_type))
            if details:
                db.set_setting("maintenance_details", str(details))
        else:
            db.set_setting("maintenance_details", "")
    except Exception as e:
        logger.error(f"Не удалось сохранить режим ТО в БД: {e}")
    return CONFIG["MAINTENANCE_MODE"]


async def broadcast_maintenance_alert(
    bot_instance: Bot,
    action: str,  # "planned", "emergency", "resolved"
    details: str = "",
) -> Dict[str, int]:
    """
    Рассылает служебные оповещения о режиме ТО (плановое, внеплановое, завершение):
    - Всем администраторам (HR, Tech, SuperAdmin)
    - В целевые группы (кадровый чат)
    - Соискателям, оставившим контакты/заявки в системе
    """
    targets: Set[int] = set()

    # 1. Администраторы
    try:
        for adm_id, _ in db.get_all_admins():
            if str(adm_id).lstrip("-").isdigit():
                targets.add(int(adm_id))
    except Exception as e:
        logger.warning(f"Ошибка получения админов для рассылки ТО: {e}")

    super_id = CONFIG.get("SUPER_ADMIN_ID")
    if super_id:
        targets.add(int(super_id))

    # 2. Кадровый чат / группа
    hr_group = CONFIG.get("HR_GROUP_ID")
    if hr_group:
        targets.add(int(hr_group))

    # 3. Активные пользователи Telegram из БД
    try:
        with db.connection() as conn:
            cur = conn.cursor()
            rows = cur.execute("""
                SELECT DISTINCT user_id FROM candidates WHERE platform = 'tg'
                UNION
                SELECT DISTINCT user_id FROM inquiries WHERE platform = 'tg'
                UNION
                SELECT DISTINCT user_id FROM user_consents
            """).fetchall()
            for r in rows:
                uid_str = str(r[0]).strip()
                if uid_str.lstrip("-").isdigit():
                    targets.add(int(uid_str))
    except Exception as e:
        logger.warning(f"Ошибка сбора пользователей для рассылки ТО: {e}")

    # Формирование текста оповещения
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    hr_phone = CONFIG.get("HR_PHONE", "8 (8422) 58-46-60")

    if action == "planned":
        time_hint = (
            f"\n⏱ <b>Ориентировочное время:</b> {details}"
            if details
            else "\n⏱ <b>Ориентировочное время:</b> ~15-30 минут"
        )
        msg = (
            "⚙️ <b>ПЛАНОВОЕ ТЕХНИЧЕСКОЕ ОБСЛУЖИВАНИЕ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Уважаемые соискатели и коллеги!\n\n"
            "Цифровая система МУП «Ульяновскэлектротранс» уведомляет о проведении <b>плановых регламентных работ</b>.\n"
            f"{time_hint}\n\n"
            "⚠️ В этот период приём новых анкет в боте временно приостановлен.\n"
            f"📞 <i>Отдел кадров:</i> <code>{hr_phone}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕒 <i>Время уведомления: {now_str}</i>"
        )
    elif action == "emergency":
        reason_hint = f"\n⚠️ <b>Причина / статус:</b> {details}" if details else ""
        msg = (
            "🚨 <b>ВНЕПЛАНОВОЕ ТЕХНИЧЕСКОЕ ОБСЛУЖИВАНИЕ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Внимание! Сервисы МУП «Ульяновскэлектротранс» переведены в режим <b>срочного технического обслуживания</b>.\n"
            f"{reason_hint}\n\n"
            "Инженеры уже проводят необходимые восстановительные работы. Приём анкет временно на паузе.\n"
            f"📞 <i>Контакты диспетчерской / ОК:</i> <code>{hr_phone}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕒 <i>Время перехода в ТО: {now_str}</i>"
        )
    else:  # resolved / finished
        msg = (
            "✅ <b>ТЕХНИЧЕСКИЕ РАБОТЫ ЗАВЕРШЕНЫ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Сервисы МУП «Ульяновскэлектротранс» полностью функционируют в <b>штатном режиме</b>.\n\n"
            "🟢 Приём анкет и обращений соискателей возобновлён в полном объёме!\n"
            "Для подачи анкеты или проверки статуса нажмите /start\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕒 <i>Возобновлено: {now_str}</i>"
        )

    sent = 0
    failed = 0
    for tid in targets:
        try:
            ok = await safe_send(bot_instance, tid, msg, parse_mode="HTML")
            if ok:
                sent += 1
            else:
                failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # Защита от флуда Telegram API

    logger.info(f"[MAINTENANCE_BROADCAST] Рассылка '{action}' завершена: доставлено {sent}, ошибок {failed}")
    return {"sent": sent, "failed": failed, "total": len(targets)}


saved_group = db.get_setting("hr_group_id", "")
if saved_group and saved_group.lstrip("-").isdigit():
    CONFIG["HR_GROUP_ID"] = int(saved_group)
    if CONFIG["HR_GROUP_ID"] not in CONFIG["TARGET_CHATS"]:
        CONFIG["TARGET_CHATS"].append(CONFIG["HR_GROUP_ID"])

super_admin = CONFIG.get("SUPER_ADMIN_ID", 0)
if super_admin:
    db.unblock_user(super_admin)
    db.add_admin(super_admin, role="superadmin")

for saved_admin, role in db.get_all_admins():
    if role == "hr" and saved_admin not in CONFIG["TARGET_CHATS"]:
        CONFIG["TARGET_CHATS"].append(saved_admin)

EXTERNAL_SESSIONS = PersistentSessions(db)


# ==============================================================================
# МАШИНА СОСТОЯНИЙ (FSM STATES GROUPS)
# ==============================================================================


class AdminManageState(StatesGroup):
    waiting_hr_id = State()
    waiting_tech_id = State()
    waiting_vacancy_name = State()


class CandidateForm(StatesGroup):
    waiting_consent = State()
    full_name = State()
    birth_date = State()
    phone = State()
    city = State()
    city_manual = State()
    vacancy = State()
    custom_vacancy = State()
    has_license = State()
    license_categories = State()
    license_categories_manual = State()
    has_experience = State()
    experience = State()
    education_level = State()
    education_manual = State()
    education_facility = State()
    relocation = State()
    relocation_manual = State()
    dormitory = State()
    dormitory_manual = State()
    schedule = State()
    schedule_manual = State()
    health = State()
    health_manual = State()
    criminal = State()
    criminal_manual = State()
    source = State()
    source_manual = State()
    extra_info = State()
    confirm_review = State()
    edit_field_select = State()
    edit_field_input = State()


class RevokeConsentForm(StatesGroup):
    waiting_confirm = State()


class InquiryForm(StatesGroup):
    waiting_consent = State()
    waiting_question = State()


class CustomInviteForm(StatesGroup):
    waiting_datetime = State()


class CandidateDirectMsgForm(StatesGroup):
    waiting_text = State()


class HRReplyForm(StatesGroup):
    waiting_reply = State()


class CandidateNoteForm(StatesGroup):
    waiting_note = State()


class SupportForm(StatesGroup):
    waiting_message = State()


# ==============================================================================
# ДИНАМИЧЕСКИЙ СПИСОК ВАКАНСИЙ
# ==============================================================================

VACANCIES: List[str] = [
    "Водитель трамвая",
    "Водитель троллейбуса",
    "Слесарь по ремонту подвижного состава",
    "Электромонтер контактной сети",
    "Монтер пути",
    "Кондуктор",
]

BASE_DIR: Path = Path(__file__).resolve().parent


def get_all_vacancies() -> List[str]:
    """Возвращает список всех актуальных вакансий предприятия."""
    try:
        data_dir = BASE_DIR / "data"
        vac_file = data_dir / "vacancies.json"
        if vac_file.exists():
            with open(vac_file, encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list) and loaded:
                    return loaded
    except Exception as e:
        logger.warning("Не удалось загрузить vacancies.json: %s", e)
    return list(VACANCIES)


def save_all_vacancies(vacancies: List[str]) -> bool:
    """Сохраняет обновленный список вакансий в data/vacancies.json."""
    try:
        data_dir = BASE_DIR / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        vac_file = data_dir / "vacancies.json"
        with open(vac_file, "w", encoding="utf-8") as f:
            json.dump(vacancies, f, ensure_ascii=False, indent=2)
        global VACANCIES
        VACANCIES = list(vacancies)
        return True
    except Exception as e:
        logger.error("Не удалось сохранить vacancies.json: %s", e)
        return False


get_vacancies = get_all_vacancies
save_vacancies = save_all_vacancies


def get_closed_vacancies() -> Set[str]:
    """Возвращает набор временно закрытых должностей (по которым набор приостановлен)."""
    try:
        val = db.get_setting("closed_vacancies", "[]")
        return set(json.loads(val))
    except Exception:
        return set()


def set_closed_vacancies(closed: Set[str]) -> bool:
    """Сохраняет список закрытых должностей в настройках БД."""
    try:
        db.set_setting("closed_vacancies", json.dumps(list(closed), ensure_ascii=False))
        return True
    except Exception as e:
        logger.error("Ошибка сохранения closed_vacancies: %s", e)
        return False


def get_open_vacancies() -> List[str]:
    """Возвращает список только тех вакансий, приём по которым в данный момент открыт."""
    all_vac = get_all_vacancies()
    closed = get_closed_vacancies()
    return [v for v in all_vac if v not in closed]


def get_vacancy_icon(name: str) -> str:
    """Возвращает подходящую иконку (эмодзи) для вакансии по её наименованию."""
    if not name:
        return "💼"
    n = name.lower()
    if "трамва" in n:
        return "🚋"
    if "троллей" in n:
        return "🚎"
    if "кондуктор" in n:
        return "🎫"
    if "слесар" in n or "ремонт" in n:
        return "🔧"
    if "электр" in n:
        return "⚡"
    if "пути" in n or "рельс" in n:
        return "🛤"
    if "маляр" in n or "кузов" in n:
        return "🎨"
    if "водитель" in n:
        return "🚌"
    if "диспетчер" in n:
        return "📞"
    if "инженер" in n:
        return "📐"
    return "💼"


# ==============================================================================
# ПРОВЕРКА ПРАВ И РОЛЕВАЯ МОДЕЛЬ
# ==============================================================================


def is_tech_admin(user_id: int) -> bool:
    """Проверка прав технического инженера."""
    if user_id == CONFIG.get("SUPER_ADMIN_ID") or user_id == CONFIG.get("TECH_ADMIN_ID"):
        return True
    role = db.get_admin_role(user_id)
    return role in ("superadmin", "tech")


def is_hr_admin(user_id: int) -> bool:
    """Проверка прав сотрудника кадровой службы."""
    if user_id == CONFIG.get("SUPER_ADMIN_ID"):
        return True
    role = db.get_admin_role(user_id)
    return role in ("superadmin", "hr")


def is_depot_admin(user_id: int) -> bool:
    """Проверка прав сотрудника депо (реферальная программа)."""
    if user_id == CONFIG.get("SUPER_ADMIN_ID"):
        return True
    role = db.get_admin_role(user_id)
    return role in ("superadmin", "depot")


def is_any_staff(user_id: int) -> bool:
    """Проверка прав любого авторизованного сотрудника предприятия."""
    if user_id == CONFIG.get("SUPER_ADMIN_ID") or user_id == CONFIG.get("TECH_ADMIN_ID"):
        return True
    role = db.get_admin_role(user_id)
    return role in ("superadmin", "super", "hr", "tech", "depot")


def check_hr_access_or_block(user_id: int, chat_id: int) -> Tuple[bool, Optional[str]]:
    """
    Централизованная проверка доступа к кадровой информации:
    1. Главный администратор (SUPER_ADMIN_ID) и технический администратор.
    2. Авторизованный сотрудник отдела кадров (is_hr_admin).
    3. Официальная кадровая группа (HR_GROUP_ID).
    4. Посторонним пользователям доступ блокируется.
    """
    super_id = CONFIG.get("SUPER_ADMIN_ID")
    hr_group = CONFIG.get("HR_GROUP_ID", 0)

    if super_id and user_id == super_id:
        return True, None

    if is_hr_admin(user_id):
        return True, None

    if hr_group and chat_id == hr_group:
        return True, None

    return (
        False,
        "🚫 <b>Доступ ограничен.</b> Кадровая панель доступна только сотрудникам отдела кадров МУП «Ульяновскэлектротранс».",
    )


# ==============================================================================
# MIDDLEWARES И ФИЛЬТРЫ БЕЗОПАСНОСТИ
# ==============================================================================


class HRAccessMiddleware(BaseMiddleware):
    """
    Централизованный Middleware авторизации HR-роутера:
    Гарантирует доступ только уполномоченным кадровикам и изолирует Live-Chat.
    """

    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return None

        # Изоляция прямого диалога: если соискатель в активном чате, пропускаем
        if db.get_dialog_by_user(str(user.id)):
            return await handler(event, data)

        chat = data.get("event_chat")
        chat_id = chat.id if chat else user.id

        allowed, err_text = check_hr_access_or_block(user.id, chat_id)
        if not allowed:
            if isinstance(event, types.CallbackQuery):
                await event.answer("🚫 Действие доступно только сотрудникам отдела кадров!", show_alert=True)
            elif isinstance(event, types.Message):
                await safe_answer(event, err_text or "🚫 Доступ ограничен.", parse_mode="HTML")
            return None

        data["is_super_admin"] = user.id == CONFIG.get("SUPER_ADMIN_ID")
        data["is_tech_admin"] = is_tech_admin(user.id)
        data["is_hr_admin"] = is_hr_admin(user.id)

        return await handler(event, data)


class TechAccessMiddleware(BaseMiddleware):
    """
    Централизованный Middleware авторизации инженерного роутера (/tech):
    Ограничивает доступ к служебной панели только системным инженерам и владельцу.
    """

    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return None

        super_id = CONFIG.get("SUPER_ADMIN_ID")
        user_id = user.id

        is_tech = (
            (super_id and user_id == super_id)
            or is_tech_admin(user_id)
            or (CONFIG.get("ENVIRONMENT") == "TEST" and is_hr_admin(user_id))
        )
        if not is_tech:
            if isinstance(event, types.CallbackQuery):
                await event.answer("⛔ Доступ к инженерной панели ограничен!", show_alert=True)
            elif isinstance(event, types.Message):
                await safe_answer(
                    event,
                    "⛔ <b>Доступ ограничен.</b> Инженерная панель доступна только техническим администраторам.",
                    parse_mode="HTML",
                )
            return None

        data["is_super_admin"] = user.id == super_id
        data["is_tech_admin"] = is_tech_admin(user_id)
        return await handler(event, data)


class IsSuperAdminFilter(BaseFilter):
    """Фильтр для операций, доступных исключительно главному администратору."""

    async def __call__(self, event: types.TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        if not user:
            return False
        return user.id == CONFIG.get("SUPER_ADMIN_ID")


class IsHRFilter(BaseFilter):
    """Фильтр проверки прав сотрудника отдела кадров."""

    async def __call__(self, event: types.TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        if not user:
            return False
        chat = getattr(event, "chat", None) or (
            event.message.chat if hasattr(event, "message") and event.message else None
        )
        chat_id = chat.id if chat else user.id
        allowed, _ = check_hr_access_or_block(user.id, chat_id)
        return allowed


# ==============================================================================
# СИСТЕМНЫЕ МЕТРИКИ И УПРАВЛЕНИЕ РЕЖИМАМИ
# ==============================================================================


def get_uptime() -> str:
    """Возвращает форматированный аптайм процесса."""
    elapsed = int(time.time() - START_TIME)
    hours, rem = divmod(elapsed, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}ч {minutes}м {seconds}с"


def get_process_memory_mb() -> float:
    """Возвращает объем фактически занятой процессом оперативной памяти (RSS) в МБ."""
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 2)
    except Exception:
        pass
    try:
        import resource

        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 2)
    except Exception:
        return 0.0


def update_env_mode(new_mode: str) -> bool:
    """Переключение режима работы (PROD / TEST) с фиксацией в .env и SQLite."""
    mode_str = new_mode.upper()
    update_env_variable("ENVIRONMENT", mode_str)
    db.set_setting("environment", mode_str)
    CONFIG["ENVIRONMENT"] = mode_str
    return True


# ==============================================================================
# ОТКАЗОУСТОЙЧИВАЯ ОТПРАВКА СООБЩЕНИЙ (SAFE_SEND / SAFE_ANSWER)
# ==============================================================================


async def safe_answer(
    event: Union[types.Message, types.CallbackQuery],
    text: str,
    alert: bool = False,
    **kwargs,
) -> bool:
    """
    Универсальный безопасный ответ для types.Message и types.CallbackQuery.
    - Message: отправляет reply-сообщение с flood-control и HTML-фоллбэком.
    - CallbackQuery + alert=True: показывает popup-уведомление (show_alert).
    - CallbackQuery + alert=False: отвечает текстовым сообщением в чат.
    """
    if not text:
        return False

    # === CallbackQuery ===
    if isinstance(event, types.CallbackQuery):
        if alert:
            try:
                await event.answer(text[:200], show_alert=True)
                return True
            except Exception as e:
                logger.warning("safe_answer(alert): %s", e)
                return False
        # Ответ сообщением в чат через callback.message
        if event.message:
            return await safe_answer(event.message, text, **kwargs)
        return False

    # === Message ===
    message = event
    if len(text) > 4000:
        text = text[:3900] + "\n... (сообщение сокращено)"

    for _ in range(3):
        try:
            await message.answer(text, **kwargs)
            SYSTEM_METRICS["tg_online"] = True
            return True
        except TelegramRetryAfter as e:
            logger.warning("Flood control в safe_answer: сон %s сек", e.retry_after)
            await asyncio.sleep(e.retry_after)
        except TelegramBadRequest as e:
            msg_lower = (e.message or "").lower()
            if "too long" in msg_lower:
                short_t = text[:2000] + "\n... (сообщение сокращено)"
                try:
                    await message.answer(short_t, **kwargs)
                    return True
                except Exception:
                    return False
            # Фоллбэк: снятие HTML-тегов при синтаксической ошибке разметки
            try:
                clean_t = re.sub(r"<[^>]+>", "", text)
                await message.answer(clean_t)
                return True
            except Exception:
                return False
        except (TelegramNetworkError, ClientError, asyncio.TimeoutError):
            await asyncio.sleep(1.2)
        except Exception as e:
            logger.error("Ошибка safe_answer: %s", e)
            SYSTEM_METRICS["errors_count"] += 1
            return False
    return False


async def safe_send(
    bot_instance: Bot,
    chat_id: Union[int, str],
    text: str,
    reply_markup: Optional[Union[types.InlineKeyboardMarkup, types.ReplyKeyboardMarkup]] = None,
    parse_mode: Optional[str] = "HTML",
) -> bool:
    """
    Безопасная отправка в произвольный чат/канал с полной защитой от FloodWait,
    блокировок бота пользователем и сетевых таймаутов.
    """
    if not chat_id or not text:
        return False
    if len(text) > 4000:
        text = text[:3900] + "\n... (сообщение сокращено)"

    for _ in range(3):
        try:
            await bot_instance.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            SYSTEM_METRICS["tg_online"] = True
            return True
        except TelegramRetryAfter as e:
            logger.warning("Flood limit exceeded в safe_send для чата %s: сон %s сек", chat_id, e.retry_after)
            await asyncio.sleep(e.retry_after)
        except TelegramForbiddenError as e:
            logger.info("Бот заблокирован пользователем %s: %s", chat_id, e.message)
            return False
        except TelegramBadRequest as e:
            msg_lower = (e.message or "").lower()
            if "chat not found" in msg_lower:
                logger.warning("Чат %s не найден: %s", chat_id, e.message)
                return False
            if "too long" in msg_lower:
                short_t = text[:2000] + "\n... (сообщение сокращено)"
                try:
                    await bot_instance.send_message(chat_id=chat_id, text=short_t, reply_markup=reply_markup, parse_mode=parse_mode)
                    return True
                except Exception:
                    pass
            logger.warning("TelegramBadRequest для %s: %s", chat_id, e.message)
            return False
        except Exception as e:
            logger.warning("Ошибка safe_send для чата %s: %s", chat_id, e)
            await asyncio.sleep(0.5)
    return False


async def broadcast_message(
    bot_instance: Bot,
    text: str,
    target_mode: str = "me",  # "me", "channel", "hr", "all"
    title: str = "",
    reply_markup: Optional[Any] = None,
    channel_target: Optional[Union[int, str]] = None,
) -> Dict[str, Any]:
    """
    Универсальный движок рассылки сообщений со встроенным тестовым режимом.
    - 'me': рассылка строго главному администратору (SUPER_ADMIN_ID).
    - 'channel': публикация в официальный канал предприятия (или указанный чат).
    - 'hr': рассылка в кадровый чат и сотрудникам отдела кадров.
    - 'all': полномасштабная рассылка соискателям из БД, админам и в кадровый чат.
    """
    targets: List[Union[int, str]] = []
    raw_super = CONFIG.get("SUPER_ADMIN_ID")
    super_id = int(raw_super) if raw_super and str(raw_super).isdigit() else 7657422832

    raw_hr = CONFIG.get("HR_GROUP_ID")
    hr_group = int(raw_hr) if raw_hr and str(raw_hr).lstrip("-").isdigit() else None

    if target_mode == "me":
        targets.append(super_id)
    elif target_mode == "channel":
        c_list = []
        if channel_target:
            c_list.append(channel_target)
        env_channel = CONFIG.get("BROADCAST_CHANNEL_ID")
        if env_channel:
            c_list.append(env_channel)
        c_list.extend(["@helpuet73", "-1004402944926"])
        for c in c_list:
            if c and c not in targets:
                targets.append(c)
    elif target_mode == "hr":
        if hr_group:
            targets.append(hr_group)
        targets.append(super_id)
        try:
            for adm_id, _ in db.get_all_admins():
                if str(adm_id).lstrip("-").isdigit() and int(adm_id) not in targets:
                    targets.append(int(adm_id))
        except Exception as e:
            logger.warning(f"Ошибка получения админов для рассылки HR: {e}")
    else:  # "all"
        targets.append(super_id)
        if hr_group and hr_group not in targets:
            targets.append(hr_group)
        try:
            for adm_id, _ in db.get_all_admins():
                if str(adm_id).lstrip("-").isdigit() and int(adm_id) not in targets:
                    targets.append(int(adm_id))
        except Exception:
            pass
        # Собираем пользователей из обеих баз
        for is_test in (False, True):
            try:
                with db._get_connection(is_test=is_test) as conn:
                    cur = conn.cursor()
                    rows = cur.execute("""
                        SELECT DISTINCT user_id FROM candidates WHERE platform = 'tg'
                        UNION
                        SELECT DISTINCT user_id FROM inquiries WHERE platform = 'tg'
                        UNION
                        SELECT DISTINCT user_id FROM user_consents
                    """).fetchall()
                    for r in rows:
                        uid_str = str(r[0]).strip()
                        if uid_str.lstrip("-").isdigit():
                            uid_int = int(uid_str)
                            if uid_int not in targets:
                                targets.append(uid_int)
            except Exception as e:
                logger.warning(f"Ошибка сбора пользователей для рассылки: {e}")

    # Оформление текста
    formatted_msg = text
    if title:
        formatted_msg = f"📢 <b>{html.escape(title)}</b>\n━━━━━━━━━━━━━━━━━━━━━\n{text}"

    sent = 0
    failed = 0
    recipients = []
    for tid in targets:
        try:
            ok = await safe_send(bot_instance, tid, formatted_msg, reply_markup=reply_markup, parse_mode="HTML")
            if ok:
                sent += 1
                recipients.append({"id": str(tid), "status": "ok"})
                if target_mode == "channel":
                    # Если отправка в один из вариантов канала успешна, прерываем цикл перебора каналов
                    break
            else:
                failed += 1
                recipients.append({"id": str(tid), "status": "failed"})
        except Exception as e:
            failed += 1
            recipients.append({"id": str(tid), "status": "error", "error": str(e)})
        await asyncio.sleep(0.04)

    logger.info(f"[BROADCAST] Режим '{target_mode}': доставлено {sent}, ошибок {failed}, всего {len(targets)}")
    return {
        "sent": sent,
        "failed": failed,
        "total": len(targets),
        "target_mode": target_mode,
        "recipients": recipients[:20]
    }



# ==============================================================================
# МАРШРУТИЗАЦИЯ АНКЕТ И ОБРАЩЕНИЙ В КАДРОВУЮ СЛУЖБУ
# ==============================================================================

# ==============================================================================
# ОБЩАЯ ЛОГИКА МАРШРУТИЗАЦИИ ТИКЕТОВ В КАДРОВУЮ СЛУЖБУ
# ==============================================================================


async def _route_ticket_to_hr(
    bot_instance: Bot,
    card_text: str,
    reply_markup: Optional[types.InlineKeyboardMarkup],
    *,
    include_pdn: bool = True,
) -> None:
    """
    Общая приватная функция доставки тикета/обращения в кадровую службу.
    include_pdn=True  — для анкет (персональные данные, защита 152-ФЗ).
    include_pdn=False — для обращений (/ask), полный доступ для всех кадровиков.
    """
    hr_group = CONFIG.get("HR_GROUP_ID", 0)
    super_id = CONFIG.get("SUPER_ADMIN_ID", 0)
    delivered = False

    # 1. Отправка в кадровый чат
    if hr_group:
        ok = await safe_send(bot_instance, hr_group, card_text, reply_markup=reply_markup)
        if ok:
            delivered = True
            logger.info("Тикет успешно доставлен в кадровый чат %s", hr_group)
        else:
            logger.warning("Сбой доставки тикета в кадровый чат %s", hr_group)
    else:
        logger.warning("Кадровый чат HR_GROUP_ID не привязан! Маршрутизация в ЛС сотрудникам.")

    # 2. Составляем список получателей (кадровики с DM-уведомлениями)
    target_users: set[int] = {adm for adm in db.get_hr_admins_with_dm_enabled() if adm > 0}

    env_mode = (CONFIG.get("ENVIRONMENT") or "TEST").upper()
    is_prod = env_mode in ("PROD", "PRODUCTION")

    if not is_prod:
        if super_id and super_id > 0:
            if not hr_group or not delivered or db.get_admin_notify_status(super_id):
                target_users.add(super_id)
    else:
        # В PROD владелец получает копию только при аварии доставки
        if not delivered and super_id and super_id > 0 and not any(target_users):
            target_users.add(super_id)

    # 3. Для анкет с ПДн: изолируем технических админов (ст. 6 152-ФЗ)
    if include_pdn:
        for adm_id, role in db.get_all_admins():
            if role == "tech" and adm_id in target_users and adm_id != super_id:
                target_users.discard(adm_id)

    # 4. Разослаем в ЛС
    for uid in target_users:
        if uid != hr_group:
            sent_ok = await safe_send(bot_instance, uid, card_text, reply_markup=reply_markup)
            if sent_ok:
                delivered = True
                if include_pdn:
                    logger.info("Анкета доставлена в ЛС кадровику %s", uid)

    if not delivered:
        logger.critical("КРИТИЧЕСКИЙ СБОЙ: Тикет не доставлен! Проверьте SUPER_ADMIN_ID и HR_GROUP_ID.")


async def route_new_candidate_ticket(
    bot_instance: Bot,
    card_text: str,
    reply_markup: Optional[types.InlineKeyboardMarkup],
    sender_info: Optional[str] = None,
) -> None:
    """
    Маршрутизация новой анкеты соискателя:
    1. Добавление приписки об источнике/отправителе (sender_info).
    2. Гарантированная отправка в кадровый чат (HR_GROUP_ID).
    3. Дублирование уполномоченным кадровикам с включенными уведомлениями в ЛС.
    4. В режиме PROD разработчик изолирован от ПДн соискателей (ст. 6 152-ФЗ).
    """
    final_text = card_text
    if sender_info:
        final_text = f"{card_text}\n\n📨 <b>Отправлено через:</b> <i>{sender_info}</i>"
    await _route_ticket_to_hr(bot_instance, final_text, reply_markup, include_pdn=True)


async def route_new_inquiry_ticket(
    bot_instance: Bot,
    card_text: str,
    reply_markup: Optional[types.InlineKeyboardMarkup],
) -> None:
    """Маршрутизация вопроса соискателя (/ask) в кадровую службу."""
    await _route_ticket_to_hr(bot_instance, card_text, reply_markup, include_pdn=False)


# ==============================================================================
# МНОГОКАНАЛЬНАЯ ДОСТАВКА СООБЩЕНИЙ СОИСКАТЕЛЯМ (TG / VK / MAX)
# ==============================================================================


async def send_response_to_candidate(
    platform: str,
    user_id: str,
    message_text: str,
    keyboard: Optional[Any] = None,
) -> bool:
    """
    Универсальная отправка ответа соискателю в целевой мессенджер (TG / VK / MAX)
    с автоматической очисткой HTML-разметки через clean_html() для внешних шлюзов.
    """
    if platform == "tg":
        try:
            return await safe_send(bot, int(user_id), message_text, reply_markup=keyboard)
        except Exception as e:
            logger.error("Ошибка отправки кандидату TG (%s): %s", user_id, e)
            return False

    # Строгое SSOT форматирование без HTML для VK и МАКС
    plain_text = clean_html(message_text)

    timeout = ClientTimeout(total=15)
    async with ClientSession(timeout=timeout) as session:
        for _ in range(3):
            try:
                if platform == "vk":
                    if not CONFIG.get("VK_GROUP_TOKEN"):
                        return False
                    url = "https://api.vk.com/method/messages.send"
                    params: Dict[str, Any] = {
                        "user_id": int(user_id),
                        "message": plain_text,
                        "random_id": 0,
                        "v": "5.131",
                        "access_token": CONFIG["VK_GROUP_TOKEN"],
                    }
                    if keyboard:
                        params["keyboard"] = keyboard
                    async with session.get(url, params=params) as resp:
                        data = await resp.json()
                        if "error" in data:
                            logger.error("VK API error sending to %s: %s", user_id, data["error"])
                            return False
                        return resp.status == 200

                elif platform == "max":
                    if not CONFIG.get("MAX_BOT_TOKEN"):
                        return False
                    url = f"{CONFIG['MAX_API_BASE'].rstrip('/')}/messages/sendText"
                    params = {
                        "token": CONFIG["MAX_BOT_TOKEN"],
                        "chatId": str(user_id),
                        "text": plain_text,
                    }
                    async with session.get(url, params=params) as resp:
                        return resp.status == 200

            except (ClientError, asyncio.TimeoutError):
                await asyncio.sleep(1.5)
            except Exception as e:
                logger.error("Сбой отправки кандидату (%s): %s", platform, e)
                SYSTEM_METRICS["errors_count"] += 1
                return False
    return False


async def send_photo_to_candidate(user_id: str, photo_bytes: bytes, caption: str = "") -> bool:
    """
    Потоковая отправка фото соискателю строго через оперативную память (RAM)
    без сохранения временных файлов на жесткий диск (ст. 19 152-ФЗ).
    """
    dlg = db.get_dialog_by_user(user_id)
    platform = "tg"
    if dlg and dlg[2]:
        cand = db.get_candidate(dlg[2])
        if cand and cand[1]:
            platform = cand[1]

    if platform == "tg":
        try:
            p_file = BufferedInputFile(photo_bytes, filename="document_photo.jpg")
            await bot.send_photo(chat_id=int(user_id), photo=p_file, caption=caption, parse_mode="HTML")
            return True
        except Exception as e:
            logger.error("Сбой отправки фото в TG (%s): %s", user_id, e)
            return False

    elif platform == "vk":
        try:
            from gateways.vk_gateway import VKPhotoService, send_vk_message

            timeout = ClientTimeout(total=20)
            async with ClientSession(timeout=timeout) as session:
                service = VKPhotoService(CONFIG.get("VK_GROUP_TOKEN", ""), session)
                attachment = await service.upload_photo_to_vk_chat(
                    peer_id=int(user_id),
                    image_bytes=io.BytesIO(photo_bytes),
                )
                return await send_vk_message(
                    session,
                    CONFIG.get("VK_GROUP_TOKEN", ""),
                    int(user_id),
                    message=clean_html(caption),
                    attachment=attachment,
                )
        except Exception as e:
            logger.error("Сбой отправки фото в VK (%s): %s", user_id, e)
            return False

    return False


# ==============================================================================
# СИНХРОНИЗАЦИЯ КОМАНД МЕНЮ TELEGRAM
# ==============================================================================


async def setup_bot_commands(bot_instance: Bot) -> None:
    """Установка контекстных выпадающих меню команд Telegram (по ролям и чатам)."""
    # 1. Меню по умолчанию для соискателей в личном диалоге (чистый лаконичный список)
    candidate_commands = [
        BotCommand(command="start", description="🏠 Главное меню / Перезапуск"),
        BotCommand(command="apply", description="📝 Заполнить анкету на работу"),
        BotCommand(command="my", description="📊 Моя анкета / Статус"),
        BotCommand(command="training", description="🚋 Обучение"),
        BotCommand(command="ask", description="💬 Задать вопрос специалисту кадров"),
        BotCommand(command="contacts", description="📞 Контакты депо и администрации"),
        BotCommand(command="privacy", description="🔒 Защита данных (152-ФЗ)"),
        BotCommand(command="revoke", description="🗑 Отозвать согласие и удалить данные"),
        BotCommand(command="help", description="ℹ️ Справка"),
    ]
    try:
        await bot_instance.set_my_commands(candidate_commands, scope=BotCommandScopeDefault())
    except Exception as e:
        logger.warning("Не удалось установить дефолтные команды меню Telegram: %s", e)

    # 2. Меню для групп и супергрупп (кадровый чат)
    group_commands = [
        BotCommand(command="hr", description="📋 Кадровая панель управления"),
        BotCommand(command="set_group", description="📌 Привязать чат для анкет"),
        BotCommand(command="sync", description="🔄 Синхронизировать кадровиков"),
        BotCommand(command="kick", description="🚫 Исключить сотрудника (в ответ)"),
        BotCommand(command="help", description="ℹ️ Справка по командам чата"),
    ]
    try:
        await bot_instance.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
    except Exception as e:
        logger.warning("Не удалось установить команды для групп: %s", e)

    # 3. Полное меню для SuperAdmin (Владелец)
    super_commands = [
        BotCommand(command="owner", description="👑 Пульт владельца (UET Kit)"),
        BotCommand(command="tests", description="🧪 Тестовая лаборатория и симуляции"),
        BotCommand(command="panel", description="🌐 Веб-панель управления"),
        BotCommand(command="to", description="🛠 Вкл/Выкл режим ТО"),
        BotCommand(command="password", description="🔑 Пароль от веб-панели"),
        BotCommand(command="hr", description="📋 Кадровая панель (соискатели)"),
        BotCommand(command="tech", description="⚙️ Панель инженера (ОЗУ, БД, логи)"),
        BotCommand(command="admins", description="👥 Управление доступом и ролями"),
        BotCommand(command="export", description="📊 Выгрузка анкет в Excel"),
        BotCommand(command="start", description="🏠 Главное меню"),
    ]
    super_id = CONFIG.get("SUPER_ADMIN_ID", 0)
    if super_id and super_id > 0:
        try:
            await bot_instance.set_my_commands(super_commands, scope=BotCommandScopeChat(chat_id=super_id))
        except Exception as e:
            logger.debug("Не удалось установить команды для SuperAdmin %s: %s", super_id, e)

    # 4. Меню строго для инженера (Tech)
    tech_commands = [
        BotCommand(command="panel", description="🌐 Веб-панель инженера"),
        BotCommand(command="to", description="🛠 Вкл/Выкл режим ТО"),
        BotCommand(command="password", description="🔑 Пароль от веб-панели"),
        BotCommand(command="tech", description="⚙️ Панель инженера (ОЗУ, БД, логи)"),
        BotCommand(command="start", description="🏠 Главное меню"),
    ]
    tech_id = CONFIG.get("TECH_ADMIN_ID", 0)
    all_tech_ids = {tech_id} if tech_id and tech_id != super_id else set()
    try:
        for adm_id, role in db.get_all_admins():
            if role == "tech" and adm_id > 0 and adm_id != super_id:
                all_tech_ids.add(adm_id)
        for t_id in all_tech_ids:
            try:
                await bot_instance.set_my_commands(tech_commands, scope=BotCommandScopeChat(chat_id=t_id))
            except Exception:
                pass
    except Exception as e:
        logger.warning("Ошибка установки команд Tech-пользователей: %s", e)

    # 5. Меню для кадровиков (HR) в личном диалоге
    hr_commands = [
        BotCommand(command="panel", description="🌐 Веб-панель соискателей"),
        BotCommand(command="password", description="🔑 Пароль от веб-панели"),
        BotCommand(command="hr", description="📋 Кадровая панель (соискатели)"),
        BotCommand(command="export", description="📊 Выгрузка анкет в Excel"),
        BotCommand(command="start", description="🏠 Главное меню"),
    ]
    try:
        for adm_id, role in db.get_all_admins():
            if role == "hr" and adm_id > 0 and adm_id != super_id and adm_id not in all_tech_ids:
                try:
                    await bot_instance.set_my_commands(hr_commands, scope=BotCommandScopeChat(chat_id=adm_id))
                except Exception:
                    pass
    except Exception as e:
        logger.warning("Ошибка установки команд HR-пользователей: %s", e)

    # 6. Установка нативной кнопки Telegram Mini App (Menu Button) рядом с полем ввода
    web_url = CONFIG.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")
    if web_url and web_url.startswith("https://"):
        if super_id and super_id > 0:
            try:
                await bot_instance.set_chat_menu_button(
                    chat_id=super_id,
                    menu_button=MenuButtonWebApp(text="🌐 Панель", web_app=WebAppInfo(url=web_url)),
                )
            except Exception as e:
                logger.debug("Не удалось установить MenuButtonWebApp для SuperAdmin: %s", e)
        for t_id in all_tech_ids:
            try:
                await bot_instance.set_chat_menu_button(
                    chat_id=t_id,
                    menu_button=MenuButtonWebApp(text="🛠 Панель", web_app=WebAppInfo(url=web_url)),
                )
            except Exception:
                pass
        for adm_id, role in db.get_all_admins():
            if role == "hr" and adm_id > 0 and adm_id != super_id and adm_id not in all_tech_ids:
                try:
                    await bot_instance.set_chat_menu_button(
                        chat_id=adm_id,
                        menu_button=MenuButtonWebApp(text="📋 Панель", web_app=WebAppInfo(url=web_url)),
                    )
                except Exception:
                    pass


async def sync_user_commands(
    bot_instance: Optional[Bot] = None,
    user_id: Optional[int] = None,
    role: Optional[str] = None,
    **kwargs,
) -> bool:
    """
    Синхронизирует выпадающее меню команд Telegram (/) и нативную кнопку Mini App
    в строгом соответствии с фактической ролью пользователя. При отзыве прав
    полностью удаляет персональный скоуп команд и возвращает соискательское меню.
    """
    try:
        target_bot = bot_instance or bot
        if not target_bot:
            return False

        web_url = CONFIG.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")

        if user_id:
            super_id = CONFIG.get("SUPER_ADMIN_ID", 0)
            tech_id = CONFIG.get("TECH_ADMIN_ID", 0)

            actual_role = role
            if actual_role is None:
                if user_id == super_id:
                    actual_role = "superadmin"
                elif user_id == tech_id:
                    actual_role = "tech"
                else:
                    actual_role = db.get_admin_role(user_id) or "none"

            if actual_role in ("none", "revoked", "candidate", ""):
                # 1. ПРАВА ОТОЗВАНЫ: удаляем персональный скоуп команд и сбрасываем кнопку
                try:
                    await target_bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=user_id))
                except Exception as e:
                    logger.debug("delete_my_commands for %s: %s", user_id, e)

                try:
                    await target_bot.set_chat_menu_button(
                        chat_id=user_id,
                        menu_button=MenuButtonDefault(),
                    )
                except Exception as e:
                    logger.debug("set_chat_menu_button default for %s: %s", user_id, e)
            else:
                # 2. ПРАВА АКТИВНЫ: устанавливаем ролевое меню
                target_commands = []
                btn_text = "🌐 Панель"
                if actual_role == "superadmin":
                    target_commands = [
                        BotCommand(command="panel", description="🌐 Веб-панель управления"),
                        BotCommand(command="to", description="🛠 Вкл/Выкл режим ТО"),
                        BotCommand(command="password", description="🔑 Пароль от веб-панели"),
                        BotCommand(command="hr", description="📋 Кадровая панель (соискатели)"),
                        BotCommand(command="tech", description="⚙️ Панель инженера (ОЗУ, БД, логи)"),
                        BotCommand(command="admins", description="👥 Управление доступом"),
                        BotCommand(command="export", description="📊 Выгрузка анкет в Excel"),
                        BotCommand(command="start", description="🏠 Главное меню"),
                    ]
                    btn_text = "🌐 Панель"
                elif actual_role == "tech":
                    target_commands = [
                        BotCommand(command="panel", description="🌐 Веб-панель инженера"),
                        BotCommand(command="to", description="🛠 Вкл/Выкл режим ТО"),
                        BotCommand(command="password", description="🔑 Пароль от веб-панели"),
                        BotCommand(command="tech", description="⚙️ Панель инженера (ОЗУ, БД, логи)"),
                        BotCommand(command="start", description="🏠 Главное меню"),
                    ]
                    btn_text = "🛠 Панель"
                elif actual_role == "hr":
                    target_commands = [
                        BotCommand(command="panel", description="🌐 Веб-панель соискателей"),
                        BotCommand(command="password", description="🔑 Пароль от веб-панели"),
                        BotCommand(command="hr", description="📋 Кадровая панель (соискатели)"),
                        BotCommand(command="export", description="📊 Выгрузка анкет в Excel"),
                        BotCommand(command="start", description="🏠 Главное меню"),
                    ]
                    btn_text = "📋 Панель"
                elif actual_role == "depot":
                    target_commands = [
                        BotCommand(command="start", description="🏠 Главное меню"),
                        BotCommand(command="help", description="ℹ️ Справка депо"),
                    ]
                    btn_text = "🚋 Кабинет"

                if target_commands:
                    try:
                        await target_bot.set_my_commands(target_commands, scope=BotCommandScopeChat(chat_id=user_id))
                    except Exception as e:
                        logger.warning("set_my_commands for %s: %s", user_id, e)

                if web_url and web_url.startswith("https://"):
                    try:
                        await target_bot.set_chat_menu_button(
                            chat_id=user_id,
                            menu_button=MenuButtonWebApp(text=btn_text, web_app=WebAppInfo(url=web_url)),
                        )
                    except Exception as e:
                        logger.debug("set_chat_menu_button for %s: %s", user_id, e)

        await setup_bot_commands(target_bot)
        return True
    except Exception as e:
        logger.warning("Ошибка sync_user_commands: %s", e)
        return False


async def revoke_admin_and_sync(bot_instance: Bot, user_id: int, reason: str = "") -> bool:
    """
    Полный цикл отзыва прав доступа:
    1. Удаление роли из SQLite
    2. Уведомление пользователя в ЛС
    3. Сброс выпадающего меню команд Telegram (/) и возврат к соискательскому
    4. Сброс нативной кнопки меню к MenuButtonDefault
    """
    db.remove_admin(user_id)
    revoke_text = (
        "ℹ️ <b>Уведомление об изменении прав доступа:</b>\n\n"
        "Ваши служебные полномочия в боте <b>МУП «Ульяновскэлектротранс»</b> были отозваны.\n"
        "Доступ к кадровой панели и служебным командам закрыт."
    )
    if reason:
        revoke_text += f"\n<i>Причина: {reason}</i>"

    try:
        from keyboards import make_candidate_main_keyboard

        await safe_send(
            bot_instance,
            user_id,
            revoke_text,
            reply_markup=make_candidate_main_keyboard(user_id=user_id),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Не удалось уведомить пользователя %s об отзыве прав: %s", user_id, e)

    await sync_user_commands(bot_instance, user_id=user_id, role="none")
    return True


async def grant_admin_and_sync(bot_instance: Bot, user_id: int, role: str, password: str = "") -> bool:
    """
    Полный цикл назначения роли сотрудника:
    1. Сохранение роли в SQLite (+ пароль при наличии)
    2. Отправка уведомления в ЛС
    3. Установка персонального меню команд (/) под новую роль
    4. Установка нативной кнопки Mini App
    """
    db.add_admin(user_id, role=role)
    if password:
        db.set_admin_password(user_id, password)

    web_url = CONFIG.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")
    role_names = {
        "hr": "Отдел кадров (HR)",
        "tech": "Технический администратор (Tech)",
        "depot": "Сотрудник депо (Depot & Рефералы)",
    }
    role_label = role_names.get(role, role)
    pwd_info = (
        f"🔑 <b>Пароль для веб-панели:</b> <code>{password}</code>\n"
        if password
        else "🔑 Для веб-панели задайте пароль командой: <code>/password ВАШ_ПАРОЛЬ</code>\n"
    )

    notice = (
        f"🎉 <b>Вам назначены права доступа в МУП «УЭТ»!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>Роль:</b> {role_label}\n"
        f"🌐 <b>Веб-панель:</b> <a href='{web_url}'>{web_url}</a>\n"
        f"{pwd_info}"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Служебные команды в выпадающем меню (/) и кнопка веб-панели автоматически активированы.</i>"
    )

    try:
        from keyboards import make_candidate_main_keyboard

        await safe_send(
            bot_instance,
            user_id,
            notice,
            reply_markup=make_candidate_main_keyboard(user_id=user_id),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Не удалось уведомить пользователя %s о назначении прав: %s", user_id, e)

    await sync_user_commands(bot_instance, user_id=user_id, role=role)
    return True
