# -*- coding: utf-8 -*-
"""
Общий модуль состояния, конфигурации, отказоустойчивости и служебных функций
бота МУП «Ульяновскэлектротранс».
Реализует безопасную отправку сообщений (Flood control, safe_send),
персистентные хранилища FSM (SQLite WAL) и ролевые фильтры.
"""

from __future__ import annotations

import asyncio
import collections
import io
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

from aiohttp import ClientError, ClientSession, ClientTimeout
from aiogram import BaseMiddleware, Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import BaseFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import BaseStorage, StorageKey, StateType
from aiogram.types import BotCommand, BufferedInputFile

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


# ==============================================================================
# ИНИЦИАЛИЗАЦИЯ ЯДРА, БОТА И ДИСПЕТЧЕРА
# ==============================================================================

db = ResumeDB()
bot = Bot(
    token=CONFIG.get("TG_BOT_TOKEN", ""),
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
    "Кондуктор",
    "Слесарь по ремонту подвижного состава",
    "Электромонтер контактной сети",
]

BASE_DIR: Path = Path(__file__).resolve().parent


def get_all_vacancies() -> List[str]:
    """Возвращает список всех актуальных вакансий предприятия."""
    try:
        data_dir = BASE_DIR / "data"
        vac_file = data_dir / "vacancies.json"
        if vac_file.exists():
            with open(vac_file, "r", encoding="utf-8") as f:
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

    if (super_id and user_id == super_id) or is_tech_admin(user_id):
        return True, None

    if is_hr_admin(user_id):
        return True, None

    if hr_group and chat_id == hr_group:
        return True, None

    return False, "🚫 <b>Доступ ограничен.</b> Кадровая панель доступна только сотрудникам отдела кадров МУП «Ульяновскэлектротранс»."


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
        with open("/proc/self/status", "r", encoding="utf-8") as f:
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

async def safe_answer(message: types.Message, text: str, **kwargs) -> bool:
    """
    Безопасный ответ на сообщение с перехватом Flood control (TelegramRetryAfter),
    автоматическим усечением длины (>4000) и фоллбэком без HTML при синтаксических ошибках.
    """
    if not text:
        return False
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
            # Фоллбэк: снятие HTML тегов при синтаксической ошибке разметки
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
    chat_id: int,
    text: str,
    reply_markup: Optional[Union[types.InlineKeyboardMarkup, types.ReplyKeyboardMarkup]] = None,
) -> bool:
    """
    Безопасная отправка в произвольный чат с полной защитой от FloodWait,
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
                parse_mode="HTML",
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
                    await bot_instance.send_message(chat_id=chat_id, text=short_t, reply_markup=reply_markup)
                    return True
                except Exception:
                    return False
            try:
                clean_t = re.sub(r"<[^>]+>", "", text)
                await bot_instance.send_message(chat_id=chat_id, text=clean_t, reply_markup=reply_markup)
                return True
            except Exception as e2:
                logger.error("Fallback без HTML также не удался в чат %s: %s", chat_id, e2)
                return False
        except (TelegramNetworkError, ClientError, asyncio.TimeoutError):
            await asyncio.sleep(1.2)
        except Exception as e:
            logger.error("Не удалось отправить сообщение в чат %s: %s", chat_id, e)
            SYSTEM_METRICS["errors_count"] += 1
            return False
    return False


# ==============================================================================
# МАРШРУТИЗАЦИЯ АНКЕТ И ОБРАЩЕНИЙ В КАДРОВУЮ СЛУЖБУ
# ==============================================================================

async def route_new_candidate_ticket(
    bot_instance: Bot,
    card_text: str,
    reply_markup: Optional[types.InlineKeyboardMarkup],
) -> None:
    """
    Маршрутизация новой анкеты соискателя:
    1. Гарантированная отправка в кадровый чат (HR_GROUP_ID).
    2. Дублирование уполномоченным кадровикам с включенными уведомлениями в ЛС.
    3. В режиме PROD разработчик изолирован от ПДн соискателей (ст. 6 152-ФЗ).
    """
    hr_group = CONFIG.get("HR_GROUP_ID", 0)
    super_id = CONFIG.get("SUPER_ADMIN_ID", 0)
    delivered = False

    # 1. Отправка в кадровый суперчат
    if hr_group and hr_group != 0:
        ok = await safe_send(bot_instance, hr_group, card_text, reply_markup=reply_markup)
        if ok:
            delivered = True
            logger.info("Анкета успешно доставлена в кадровый чат %s", hr_group)
        else:
            logger.warning("Сбой доставки анкеты в кадровый чат %s", hr_group)
    else:
        logger.warning("Кадровый чат HR_GROUP_ID не привязан! Маршрутизация в ЛС сотрудникам.")

    # 2. Определение списка сотрудников отдела кадров
    target_users: set[int] = set()
    for adm in db.get_hr_admins_with_dm_enabled():
        if adm > 0:
            target_users.add(adm)

    env_mode = (CONFIG.get("ENVIRONMENT") or "TEST").upper()
    is_prod = env_mode in ("PROD", "PRODUCTION")

    if not is_prod:
        if super_id and super_id > 0:
            if not hr_group or not delivered or db.get_admin_notify_status(super_id):
                target_users.add(super_id)
    else:
        # В PROD владелец получает копию только при аварии доставки в группу
        if not delivered and super_id and super_id > 0 and not any(target_users):
            target_users.add(super_id)

    # Исключаем системных инженеров из получения персональных данных соискателей
    for adm_id, role in db.get_all_admins():
        if role == "tech" and adm_id in target_users and adm_id != super_id:
            target_users.remove(adm_id)

    for uid in target_users:
        if uid != hr_group:
            sent_ok = await safe_send(bot_instance, uid, card_text, reply_markup=reply_markup)
            if sent_ok:
                delivered = True
                logger.info("Анкета доставлена в ЛС кадровику %s", uid)

    if not delivered:
        logger.critical("КРИТИЧЕСКИЙ СБОЙ: Анкета не доставлена! Проверьте SUPER_ADMIN_ID и HR_GROUP_ID.")


async def route_new_inquiry_ticket(
    bot_instance: Bot,
    card_text: str,
    reply_markup: Optional[types.InlineKeyboardMarkup],
) -> None:
    """Маршрутизация вопроса соискателя (/ask) в кадровую службу."""
    hr_group = CONFIG.get("HR_GROUP_ID", 0)
    super_id = CONFIG.get("SUPER_ADMIN_ID", 0)
    delivered = False

    if hr_group and hr_group != 0:
        ok = await safe_send(bot_instance, hr_group, card_text, reply_markup=reply_markup)
        if ok:
            delivered = True

    target_users: set[int] = set()
    for adm in db.get_hr_admins_with_dm_enabled():
        if adm > 0:
            target_users.add(adm)

    env_mode = (CONFIG.get("ENVIRONMENT") or "TEST").upper()
    is_prod = env_mode in ("PROD", "PRODUCTION")

    if not is_prod:
        if super_id and super_id > 0:
            if not hr_group or not delivered or db.get_admin_notify_status(super_id):
                target_users.add(super_id)
    else:
        if not delivered and super_id and super_id > 0 and not any(target_users):
            target_users.add(super_id)

    for adm_id, role in db.get_all_admins():
        if role == "tech" and adm_id in target_users and adm_id != super_id:
            target_users.remove(adm_id)

    for uid in target_users:
        if uid != hr_group:
            await safe_send(bot_instance, uid, card_text, reply_markup=reply_markup)


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
    """Установка глобального меню команд Telegram."""
    commands = [
        BotCommand(command="start", description="Главное меню / Перезапуск"),
        BotCommand(command="apply", description="Заполнить анкету на работу (16 шагов)"),
        BotCommand(command="my", description="Моя анкета / Статус рассмотрения"),
        BotCommand(command="mydata", description="Выгрузка персональных данных (152-ФЗ)"),
        BotCommand(command="revoke", description="Отозвать согласие и удалить анкету"),
        BotCommand(command="training", description="Бесплатное обучение на водителя"),
        BotCommand(command="ask", description="Задать вопрос специалисту кадров"),
        BotCommand(command="faq", description="Частые вопросы (зарплата, жилье)"),
        BotCommand(command="contacts", description="Контакты и телефоны депо"),
        BotCommand(command="support", description="Экстренная связь с техподдержкой"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
        BotCommand(command="help", description="Справочник команд бота"),
    ]
    try:
        await bot_instance.set_my_commands(commands)
    except Exception as e:
        logger.warning("Не удалось установить команды меню Telegram: %s", e)


async def sync_user_commands(
    bot_instance: Optional[Bot] = None,
    user_id: Optional[int] = None,
    role: Optional[str] = None,
    **kwargs,
) -> bool:
    """Синхронизирует команды бота в интерфейсе пользователя."""
    try:
        target_bot = bot_instance or bot
        if target_bot:
            await setup_bot_commands(target_bot)
            return True
    except Exception as e:
        logger.warning("Ошибка sync_user_commands: %s", e)
    return False