import collections
# -*- coding: utf-8 -*-
"""
Общий модуль состояния, конфигурации и служебных функций
бота МУП «Ульяновскэлектротранс».
"""
import asyncio
import io
import logging
import os
import re
import sys
import time
from datetime import datetime
from typing import Dict, Tuple, Any
import json
from aiogram.types import BotCommand, BotCommandScopeChat

from aiohttp import ClientError, ClientSession, ClientTimeout
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand
from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramAPIError
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

try:
    from config import CONFIG, validate_config
except ImportError:
    from config import CONFIG
    def validate_config():
        pass

from database import ResumeDB

import json
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

# 1. Состояние для ввода названия вакансии
# Добавьте в AdminManageState:
class AdminManageState(StatesGroup):
    waiting_hr_id = State()
    waiting_tech_id = State()
    waiting_vacancy_name = State()

# 2. Функции динамического списка вакансий в БД
DEFAULT_VACANCIES = [
    "Водитель трамвая",
    "Водитель троллейбуса",
    "Кондуктор",
    "Слесарь по ремонту подвижного состава",
    "Электромонтер контактной сети",
]

def get_all_vacancies() -> list[str]:
    """Возвращает актуальные вакансии из БД."""
    raw = db.get_setting("all_vacancies", "")
    if raw:
        try:
            val = json.loads(raw)
            if isinstance(val, list) and val:
                return val
        except Exception:
            pass
    return list(DEFAULT_VACANCIES)

def save_all_vacancies(vacs: list[str]):
    db.set_setting("all_vacancies", json.dumps(vacs, ensure_ascii=False))


# 3. Синхронизация меню [/] по ролям и мгновенная очистка при отзыве прав
async def sync_user_commands(bot_instance: Bot, user_id: int):
    """Обновляет или очищает персональное меню команд Telegram для пользователя."""
    try:
        super_id = CONFIG.get("SUPER_ADMIN_ID")
        tech_id = CONFIG.get("TECH_ADMIN_ID")
        role = db.get_admin_role(user_id)

        scope = BotCommandScopeChat(chat_id=user_id)

        if user_id == super_id or role == "superadmin":
            cmds = [
                BotCommand(command="start", description="Главное меню / Перезапуск"),
                BotCommand(command="hr", description="Кадровая панель"),
                BotCommand(command="tech", description="Инженерный мониторинг"),
                BotCommand(command="tests", description="Панель тестов"),
                BotCommand(command="admins", description="Управление доступом"),
                BotCommand(command="backups", description="Бэкапы базы данных"),
                BotCommand(command="logs", description="Логи системы"),
                BotCommand(command="blacklist", description="Черный список"),
                BotCommand(command="id", description="Узнать свой ID"),
                BotCommand(command="help", description="Полная справка"),
                BotCommand(command="cancel", description="Отмена ввода"),
            ]
            await bot_instance.set_my_commands(cmds, scope=scope)
        elif user_id == tech_id or role == "tech":
            cmds = [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="tech", description="Инженерный мониторинг"),
                BotCommand(command="tests", description="Панель тестов"),
                BotCommand(command="logs", description="Логи"),
                BotCommand(command="backup", description="Сделать бэкап"),
                BotCommand(command="backups", description="Управление бэкапами"),
                BotCommand(command="id", description="Мой ID"),
                BotCommand(command="help", description="Справка"),
                BotCommand(command="cancel", description="Отмена"),
            ]
            await bot_instance.set_my_commands(cmds, scope=scope)
        elif role == "hr":
            cmds = [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="hr", description="Кадровая панель анкет"),
                BotCommand(command="blacklist", description="Черный список"),
                BotCommand(command="stop", description="Завершить диалог"),
                BotCommand(command="id", description="Мой ID"),
                BotCommand(command="help", description="Справка"),
                BotCommand(command="cancel", description="Отмена"),
            ]
            await bot_instance.set_my_commands(cmds, scope=scope)
        else:
            # Права отозваны или обычный соискатель: удаляем чат-команды!
            # Telegram мгновенно возвращает стандартное меню соискателя
            await bot_instance.delete_my_commands(scope=scope)
    except Exception as e:
        logger.debug(f"Ошибка синхронизации команд {user_id}: {e}")

# Безопасный вывод кодировок для консоли Windows
if sys.platform.startswith("win"):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("UET_HR_BOT")

class MemoryLogHandler(logging.Handler):
    """Кольцевой буфер оперативных логов для удаленной диагностики через Telegram (/logs)."""
    def __init__(self, capacity=120):
        super().__init__()
        self.buffer = collections.deque(maxlen=capacity)

    def emit(self, record):
        try:
            msg = self.format(record)
            self.buffer.append(msg)
        except Exception:
            pass

    def get_logs(self, n=30) -> list:
        return list(self.buffer)[-n:]

memory_log_handler = MemoryLogHandler(capacity=120)
memory_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(memory_log_handler)

db = ResumeDB()
bot = Bot(
    token=CONFIG.get("TG_BOT_TOKEN", ""),
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher(storage=MemoryStorage())

START_TIME = time.time()
SYSTEM_METRICS = {
    "tg_online": True,
    "vk_online": False,
    "max_online": False,
    "errors_count": 0,
}

saved_env = db.get_setting("environment", "")
if saved_env:
    CONFIG["ENVIRONMENT"] = saved_env
saved_maint = db.get_setting("maintenance_mode", "")
if saved_maint:
    CONFIG["MAINTENANCE_MODE"] = (saved_maint == "1")    

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

# ==================== FSM СОСТОЯНИЯ ====================
class CandidateForm(StatesGroup):
    waiting_consent = State()
    full_name = State()
    phone = State()
    vacancy = State()
    experience = State()

class InquiryForm(StatesGroup):
    waiting_consent = State()
    waiting_question = State()

class CustomInviteForm(StatesGroup):
    waiting_datetime = State()

class CandidateDirectMsgForm(StatesGroup):
    waiting_text = State()

class HRReplyForm(StatesGroup):
    waiting_reply = State()

VACANCIES = [
    "Водитель трамвая",
    "Водитель троллейбуса",
    "Кондуктор",
    "Слесарь по ремонту подвижного состава",
    "Электромонтер контактной сети",
]

EXTERNAL_SESSIONS: Dict[Tuple[str, str], Dict[str, Any]] = {}

def is_tech_admin(user_id: int) -> bool:
    if user_id == CONFIG.get("SUPER_ADMIN_ID") or user_id == CONFIG.get("TECH_ADMIN_ID"):
        return True
    role = db.get_admin_role(user_id)
    return role in ["superadmin", "tech"]

def is_hr_admin(user_id: int) -> bool:
    if user_id == CONFIG.get("SUPER_ADMIN_ID"):
        return True
    role = db.get_admin_role(user_id)
    return role in ["superadmin", "hr"]

def get_uptime() -> str:
    elapsed = int(time.time() - START_TIME)
    hours, rem = divmod(elapsed, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}ч {minutes}м {seconds}с"

def update_env_mode(new_mode: str) -> bool:
    env_file = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_file):
        env_file = ".env"
    try:
        if os.path.exists(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                content = f.read()
            if re.search(r"^ENVIRONMENT=.*$", content, flags=re.MULTILINE):
                content = re.sub(r"^ENVIRONMENT=.*$", f"ENVIRONMENT={new_mode.upper()}", content, flags=re.MULTILINE)
            else:
                content += f"\nENVIRONMENT={new_mode.upper()}\n"
            with open(env_file, "w", encoding="utf-8") as f:
                f.write(content)
        else:
            with open(env_file, "w", encoding="utf-8") as f:
                f.write(f"ENVIRONMENT={new_mode.upper()}\n")
    except Exception as e:
        logger.error(f"Предупреждение при записи .env: {e}")

    CONFIG["ENVIRONMENT"] = new_mode.upper()
    db.set_setting("environment", new_mode.upper())
    return True


async def safe_answer(message: types.Message, text: str, **kwargs) -> bool:
    if not text:
        return False
    if len(text) > 4000:
        text = text[:3900] + "\n... (сообщение сокращено)"
    for _ in range(3):
        try:
            await message.answer(text, **kwargs)
            SYSTEM_METRICS["tg_online"] = True
            return True
        except TelegramBadRequest as e:
            msg_lower = (e.message or "").lower()
            if "too long" in msg_lower:
                short_t = text[:2000] + "\n... (сообщение сокращено)"
                try:
                    await message.answer(short_t, **kwargs)
                    return True
                except Exception:
                    return False
            try:
                clean_t = re.sub(r"<[^>]+>", "", text)
                await message.answer(clean_t)
                return True
            except Exception:
                return False
        except (TelegramNetworkError, ClientError, asyncio.TimeoutError):
            await asyncio.sleep(1.2)
        except Exception as e:
            logger.error(f"Ошибка safe_answer: {e}")
            SYSTEM_METRICS["errors_count"] += 1
            return False
    return False

async def safe_send(bot_instance: Bot, chat_id: int, text: str, reply_markup=None) -> bool:
    if not chat_id or not text:
        return False
    if len(text) > 4000:
        text = text[:3900] + "\n... (сообщение сокращено)"
    for attempt in range(3):
        try:
            await bot_instance.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            SYSTEM_METRICS["tg_online"] = True
            return True
        except TelegramForbiddenError as e:
            logger.warning(f"Бот не может написать пользователю {chat_id} (диалог в ЛС не начат или бот заблокирован): {e.message}")
            return False
        except TelegramBadRequest as e:
            msg_lower = (e.message or "").lower()
            if "chat not found" in msg_lower:
                logger.warning(f"Чат {chat_id} не найден: {e.message}")
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
                logger.error(f"Fallback без HTML также не удался: {e2}")
                return False
        except (TelegramNetworkError, ClientError, asyncio.TimeoutError):
            await asyncio.sleep(1.2)
        except Exception as e:
            logger.error(f"Не удалось отправить в чат {chat_id}: {e}")
            SYSTEM_METRICS["errors_count"] += 1
            return False
    return False

async def route_new_candidate_ticket(bot_instance: Bot, card_text: str, reply_markup):
    hr_group = CONFIG.get("HR_GROUP_ID", 0)
    super_id = CONFIG.get("SUPER_ADMIN_ID", 0)
    delivered = False

    if hr_group and hr_group != 0 and hr_group != -1005203042447:
        ok = await safe_send(bot_instance, hr_group, card_text, reply_markup=reply_markup)
        if ok:
            delivered = True
            logger.info(f"Анкета доставлена в кадровый чат {hr_group}")
        else:
            logger.warning(f"Не удалось отправить анкету в кадровый чат {hr_group}")
    else:
        logger.warning("Кадровая группа не привязана! Анкеты отправляются в ЛС администраторам.")

    target_users = set()
    for adm in db.get_hr_admins_with_dm_enabled():
        if adm > 0 and adm != 123456789:
            target_users.add(adm)

    env_mode = (CONFIG.get("ENVIRONMENT") or "TEST").upper()
    is_prod = env_mode in ("PROD", "PRODUCTION")

    if not is_prod:
        if super_id and super_id > 0:
            if not hr_group or not delivered or db.get_admin_notify_status(super_id):
                target_users.add(super_id)
    else:
        # В режиме PROD разработчик изолирован от ПДн анкет в ЛС (ст. 6 152-ФЗ)
        if not delivered and super_id and super_id > 0 and not any(target_users):
            target_users.add(super_id)

    for adm_id, role in db.get_all_admins():
        if role == "tech" and adm_id in target_users and adm_id != super_id:
            target_users.remove(adm_id)

    for uid in target_users:
        if uid != hr_group:
            sent_ok = await safe_send(bot_instance, uid, card_text, reply_markup=reply_markup)
            if sent_ok:
                delivered = True
                logger.info(f"Анкета доставлена в ЛС администратора {uid}")

    if not delivered:
        logger.error("КРИТИЧЕСКОЕ: Анкета не была доставлена ни в группу, ни в ЛС! Проверьте SUPER_ADMIN_ID и HR_GROUP_ID.")

async def route_new_inquiry_ticket(bot_instance: Bot, card_text: str, reply_markup):
    hr_group = CONFIG.get("HR_GROUP_ID", 0)
    super_id = CONFIG.get("SUPER_ADMIN_ID", 0)
    delivered = False

    if hr_group and hr_group != 0 and hr_group != -1005203042447:
        ok = await safe_send(bot_instance, hr_group, card_text, reply_markup=reply_markup)
        if ok:
            delivered = True
    else:
        logger.warning("Кадровая группа не привязана! Вопрос отправляется в ЛС администраторам.")

    target_users = set()
    for adm in db.get_hr_admins_with_dm_enabled():
        if adm > 0 and adm != 123456789:
            target_users.add(adm)

    env_mode = (CONFIG.get("ENVIRONMENT") or "TEST").upper()
    is_prod = env_mode in ("PROD", "PRODUCTION")

    if not is_prod:
        if super_id and super_id > 0:
            if not hr_group or not delivered or db.get_admin_notify_status(super_id):
                target_users.add(super_id)
    else:
        # В режиме PROD разработчик изолирован от ПДн анкет в ЛС (ст. 6 152-ФЗ)
        if not delivered and super_id and super_id > 0 and not any(target_users):
            target_users.add(super_id)

    for adm_id, role in db.get_all_admins():
        if role == "tech" and adm_id in target_users and adm_id != super_id:
            target_users.remove(adm_id)

    for uid in target_users:
        if uid != hr_group:
            await safe_send(bot_instance, uid, card_text, reply_markup=reply_markup)

async def send_response_to_candidate(platform: str, user_id: str, message_text: str, keyboard=None) -> bool:
    if platform == "tg":
        try:
            return await safe_send(bot, int(user_id), message_text, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Ошибка отправки кандидату TG ({user_id}): {e}")
            return False

    clean_text = (
        message_text.replace("<b>", "").replace("</b>", "")
        .replace("<code>", "").replace("</code>", "")
        .replace("<i>", "").replace("</i>", "")
    )
    async with ClientSession() as session:
        for _ in range(3):
            try:
                if platform == "vk":
                    if not CONFIG.get("VK_GROUP_TOKEN"):
                        return False
                    url = "https://api.vk.com/method/messages.send"
                    params = {
                        "user_id": int(user_id),
                        "message": clean_text,
                        "random_id": 0,
                        "v": "5.131",
                        "access_token": CONFIG["VK_GROUP_TOKEN"],
                    }
                    if keyboard:
                        params["keyboard"] = keyboard
                    async with session.get(url, params=params, timeout=ClientTimeout(total=15)) as resp:
                        data = await resp.json()
                        if "error" in data:
                            logger.error(f"VK API error sending to {user_id}: {data['error']}")
                            return False
                        return resp.status == 200

                elif platform == "max":
                    if not CONFIG.get("MAX_BOT_TOKEN"):
                        return False
                    url = f"{CONFIG['MAX_API_BASE'].rstrip('/')}/messages/sendText"
                    params = {
                        "token": CONFIG["MAX_BOT_TOKEN"],
                        "chatId": str(user_id),
                        "text": clean_text,
                    }
                    async with session.get(url, params=params, timeout=ClientTimeout(total=15)) as resp:
                        return resp.status == 200

            except (ClientError, asyncio.TimeoutError):
                await asyncio.sleep(1.5)
            except Exception as e:
                logger.error(f"Сбой отправки кандидату ({platform}): {e}")
                SYSTEM_METRICS["errors_count"] += 1
                return False
    return False

async def setup_bot_commands(bot_instance: Bot):
    commands = [
        BotCommand(command="start", description="Главное меню / Перезапуск бота"),
        BotCommand(command="apply", description="Подать анкету на работу"),
        BotCommand(command="my", description="Моя анкета / Статус заявки"),
        BotCommand(command="ask", description="Задать вопрос отделу кадров"),
        BotCommand(command="faq", description="Частые вопросы и ответы (FAQ)"),
        BotCommand(command="contacts", description="Контакты и телефоны депо"),
        BotCommand(command="privacy", description="Политика обработки данных (152-ФЗ)"),
        BotCommand(command="id", description="Узнать свой Telegram ID"),
        BotCommand(command="cancel", description="Отменить текущий опрос или ввод"),
        BotCommand(command="hr", description="Кадровая панель (резюме, набор, вакансии)"),
    ]
    try:
        await bot_instance.set_my_commands(commands)
    except Exception as e:
        logger.warning(f"Не удалось установить команды меню Telegram: {e}")

async def send_photo_to_candidate(user_id: str, photo_bytes: bytes, caption: str = "") -> bool:
    dlg = db.get_dialog_by_user(user_id)
    platform = "tg"
    if dlg and dlg[2]:
        cand = db.get_candidate(dlg[2])
        if cand:
            platform = cand[1]

    if platform == "tg":
        try:
            from aiogram.types import BufferedInputFile
            p_file = BufferedInputFile(photo_bytes, filename="photo.jpg")
            await bot.send_photo(chat_id=int(user_id), photo=p_file, caption=caption, parse_mode="HTML")
            return True
        except Exception as e:
            logger.error(f"Сбой отправки фото в TG: {e}")
            return False
    elif platform == "vk":
        try:
            from gateways.vk_gateway import VKPhotoService, send_vk_message
            async with ClientSession() as session:
                service = VKPhotoService(CONFIG.get("VK_GROUP_TOKEN", ""), session)
                attachment = await service.upload_photo_to_vk_chat(peer_id=int(user_id), image_bytes=io.BytesIO(photo_bytes))
                return await send_vk_message(
                    session, CONFIG.get("VK_GROUP_TOKEN", ""),
                    int(user_id), message=caption, attachment=attachment
                )
        except Exception as e:
            logger.error(f"Сбой отправки фото в VK: {e}")
            return False
    return False
# динамические вакансии 
DEFAULT_VACANCIES = [
    "Водитель трамвая",
    "Водитель троллейбуса",
    "Кондуктор",
    "Слесарь по ремонту подвижного состава",
    "Электромонтер контактной сети",
]

def get_all_vacancies() -> list[str]:
    raw = db.get_setting("all_vacancies", "")
    if raw:
        try:
            val = json.loads(raw)
            if isinstance(val, list) and val:
                return val
        except Exception:
            pass
    return list(DEFAULT_VACANCIES)

def save_all_vacancies(vacs: list[str]):
    db.set_setting("all_vacancies", json.dumps(vacs, ensure_ascii=False))

async def sync_user_commands(bot_instance: Bot, user_id: int):
    """Синхронизирует быстрое меню команд [/] и очищает его при отзыве прав."""
    try:
        super_id = CONFIG.get("SUPER_ADMIN_ID")
        tech_id = CONFIG.get("TECH_ADMIN_ID")
        role = db.get_admin_role(user_id)
        scope = BotCommandScopeChat(chat_id=user_id)

        if user_id == super_id or role == "superadmin":
            cmds = [
                BotCommand(command="start", description="Главное меню / Перезапуск"),
                BotCommand(command="hr", description="Кадровая панель анкет"),
                BotCommand(command="tech", description="Инженерный мониторинг"),
                BotCommand(command="tests", description="Панель тестов"),
                BotCommand(command="admins", description="Управление доступом"),
                BotCommand(command="export", description="Выгрузка базы в Excel"),
                BotCommand(command="backups", description="Бэкапы базы данных"),
                BotCommand(command="logs", description="Системные логи"),
                BotCommand(command="blacklist", description="Черный список"),
                BotCommand(command="help", description="Справка по командам"),
                BotCommand(command="cancel", description="Отмена ввода"),
            ]
            await bot_instance.set_my_commands(cmds, scope=scope)
        elif user_id == tech_id or role == "tech":
            cmds = [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="tech", description="Инженерный мониторинг"),
                BotCommand(command="tests", description="Панель тестов"),
                BotCommand(command="logs", description="Логи"),
                BotCommand(command="backup", description="Сделать бэкап"),
                BotCommand(command="backups", description="Управление бэкапами"),
                BotCommand(command="help", description="Справка"),
                BotCommand(command="cancel", description="Отмена"),
            ]
            await bot_instance.set_my_commands(cmds, scope=scope)
        elif role == "hr":
            cmds = [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="hr", description="Кадровая панель"),
                BotCommand(command="export", description="Выгрузка в Excel"),
                BotCommand(command="blacklist", description="Черный список"),
                BotCommand(command="stop", description="Завершить диалог"),
                BotCommand(command="help", description="Справка"),
                BotCommand(command="cancel", description="Отмена"),
            ]
            await bot_instance.set_my_commands(cmds, scope=scope)
        else:
            await bot_instance.delete_my_commands(scope=scope)
    except Exception:
        pass