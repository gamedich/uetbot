# -*- coding: utf-8 -*-
"""
Модуль конфигурации МУП «Ульяновскэлектротранс»
Безопасная загрузка переменных окружения из .env файла
Соответствие требованиям безопасности и 152-ФЗ РФ
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Загрузка локального .env
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

def _get_int(key: str, default: int = 0) -> int:
    val = os.getenv(key, "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default

def _get_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")

CONFIG = {
    
    # Режим среды (TEST / PROD)
    "ENVIRONMENT": os.getenv("ENVIRONMENT", "TEST").strip().upper(),

    # Токены мессенджеров
    "TG_BOT_TOKEN": os.getenv("TG_BOT_TOKEN", "").strip(),
    "VK_GROUP_TOKEN": os.getenv("VK_GROUP_TOKEN", "").strip(),
    "VK_GROUP_ID": os.getenv("VK_GROUP_ID", "").strip(),
    "MAX_BOT_TOKEN": os.getenv("MAX_BOT_TOKEN", "").strip(),
    "MAX_API_BASE": os.getenv("MAX_API_BASE", "https://api.myteam.mail.ru/bot/v1").strip(),

    # Права доступа (Супер-админ и кадровые чаты)
    "SUPER_ADMIN_ID": _get_int("SUPER_ADMIN_ID", 0),
    "TECH_ADMIN_ID": _get_int("TECH_ADMIN_ID", 0),
    "HR_GROUP_ID": _get_int("HR_GROUP_ID", 0),
    "TARGET_CHATS": [],

    # Контакты кадровой службы МУП «УЭТ»
    "HR_PHONE": os.getenv("HR_PHONE", "+7 (8422) 58-46-60").strip(),
    "HR_ADDRESS": os.getenv("HR_ADDRESS", "г. Ульяновск, ул. Гончарова, 2").strip(),
    "HR_SCHEDULE": os.getenv("HR_SCHEDULE", "пн-пт 08:00–17:00 (перерыв 12:00–13:00)").strip(),
    "COOLDOWN_SECONDS": _get_int("COOLDOWN_SECONDS", 1200),  # 20 минут антифлуд

    # Системные флаги
    "MAINTENANCE_MODE": _get_bool("MAINTENANCE_MODE", False),
    "ANONYMIZE_LOGS": _get_bool("ANONYMIZE_LOGS", True),
}

# Инициализация списка целевых чатов
if CONFIG["HR_GROUP_ID"]:
    CONFIG["TARGET_CHATS"].append(CONFIG["HR_GROUP_ID"])
if CONFIG["SUPER_ADMIN_ID"] and CONFIG["SUPER_ADMIN_ID"] not in CONFIG["TARGET_CHATS"]:
    CONFIG["TARGET_CHATS"].append(CONFIG["SUPER_ADMIN_ID"])

def validate_config():
    """Строгая проверка конфигурации перед запуском бота"""
    errors = []
    if not CONFIG["TG_BOT_TOKEN"]:
        errors.append("TG_BOT_TOKEN не задан! Укажите токен бота в файле .env")
    if not CONFIG["SUPER_ADMIN_ID"]:
        errors.append("SUPER_ADMIN_ID не задан! Укажите ваш Telegram ID в файле .env")
    
    if errors:
        msg = "\n❌ КРИТИЧЕСКИЕ ОШИБКИ КОНФИГУРАЦИИ:\n" + "\n".join(f"  • {e}" for e in errors)
        msg += "\n\n💡 Создайте файл .env на основе шаблона .env.example и заполните обязательные параметры."
        raise ValueError(msg)
