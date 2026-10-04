# -*- coding: utf-8 -*-
"""
Модуль конфигурации МУП «Ульяновскэлектротранс»
Безопасная загрузка переменных окружения из .env файла.
Соответствие требованиям информационной безопасности и 152-ФЗ РФ.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List
from dotenv import load_dotenv

# Базовые директории проекта
BASE_DIR: Path = Path(__file__).resolve().parent
ENV_PATH: Path = BASE_DIR / ".env"

# Автоматическая загрузка .env
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()


def _get_str(key: str, default: str = "") -> str:
    """Безопасное получение строковой переменной окружения."""
    val = os.getenv(key)
    return val.strip() if val is not None else default


def _get_int(key: str, default: int = 0) -> int:
    """Безопасное приведение к целому числу."""
    val = os.getenv(key, "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _get_bool(key: str, default: bool = False) -> bool:
    """Безопасное приведение к логическому типу."""
    val = os.getenv(key, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on", "y", "enable", "enabled")


def mask_secret(secret: str, unmasked_start: int = 4, unmasked_end: int = 4) -> str:
    """Маскирует конфиденциальные токены для безопасного вывода в логах и дашборде."""
    if not secret:
        return "[НЕ ЗАДАН]"
    if len(secret) <= (unmasked_start + unmasked_end):
        return "***"
    return f"{secret[:unmasked_start]}...{secret[-unmasked_end:]}"


# Директории для БД, логов и резервных копий
DATA_DIR: Path = BASE_DIR / "data"
LOGS_DIR: Path = BASE_DIR / "logs"
BACKUP_DIR: Path = BASE_DIR / "backups"

# Гарантированное создание служебных каталогов
for directory in (DATA_DIR, LOGS_DIR, BACKUP_DIR):
    directory.mkdir(parents=True, exist_ok=True)


class AppConfig(dict):
    """
    Класс конфигурации приложения с поддержкой:
    1. Обратной совместимости со словарем CONFIG["KEY"] и CONFIG.get("KEY").
    2. Прямого доступа по атрибутам CONFIG.KEY.
    3. Мутаций состояния на лету (ENVIRONMENT, MAINTENANCE_MODE, COOLDOWN_SECONDS).
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"Параметр конфигурации '{name}' не существует.")

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


# Список целевых чатов для уведомлений
initial_target_chats: List[int] = []

# Инициализация глобального словаря настроек
CONFIG: Dict[str, Any] = AppConfig({
    # --- Среда выполнения ---
    "ENVIRONMENT": _get_str("ENVIRONMENT", "TEST").upper(),
    
    # --- Пути файловой системы ---
    "BASE_DIR": str(BASE_DIR),
    "DATA_DIR": str(DATA_DIR),
    "LOGS_DIR": str(LOGS_DIR),
    "BACKUP_DIR": str(BACKUP_DIR),
    "DB_PATH": _get_str("DB_PATH", str(DATA_DIR / "database.db")),
    "LOG_FILE": _get_str("LOG_FILE", str(LOGS_DIR / "bot.log")),

    # --- Токены внешних шлюзов ---
    "TG_BOT_TOKEN": _get_str("TG_BOT_TOKEN", ""),
    "VK_GROUP_TOKEN": _get_str("VK_GROUP_TOKEN", ""),
    "VK_GROUP_ID": _get_str("VK_GROUP_ID", ""),
    "MAX_BOT_TOKEN": _get_str("MAX_BOT_TOKEN", ""),
    "MAX_API_BASE": _get_str("MAX_API_BASE", "https://api.myteam.mail.ru/bot/v1").rstrip("/"),

    # --- Права доступа и маршрутизация ---
    "SUPER_ADMIN_ID": _get_int("SUPER_ADMIN_ID", 0),
    "TECH_ADMIN_ID": _get_int("TECH_ADMIN_ID", 0),
    "HR_GROUP_ID": _get_int("HR_GROUP_ID", 0),
    "TARGET_CHATS": initial_target_chats,

    # --- Контакты и регламент кадровой службы МУП «УЭТ» ---
    "HR_PHONE": _get_str("HR_PHONE", "+7 (8422) 58-46-60"),
    "HR_ADDRESS": _get_str("HR_ADDRESS", "г. Ульяновск, ул. Гончарова, 2"),
    "HR_SCHEDULE": _get_str("HR_SCHEDULE", "пн-пт 08:00–17:00 (перерыв 12:00–13:00)"),
    "COOLDOWN_SECONDS": _get_int("COOLDOWN_SECONDS", 1200),  # 20 минут антифлуд

    # --- Системные режимы и 152-ФЗ ---
    "MAINTENANCE_MODE": _get_bool("MAINTENANCE_MODE", False),
    "ANONYMIZE_LOGS": _get_bool("ANONYMIZE_LOGS", True),
    "ALLOW_TEST_SUBMISSIONS_IN_PROD": _get_bool("ALLOW_TEST_SUBMISSIONS_IN_PROD", False),
})

# Заполнение списка чатов для гарантированной доставки уведомлений
if CONFIG["HR_GROUP_ID"] and CONFIG["HR_GROUP_ID"] not in CONFIG["TARGET_CHATS"]:
    CONFIG["TARGET_CHATS"].append(CONFIG["HR_GROUP_ID"])

if CONFIG["SUPER_ADMIN_ID"] and CONFIG["SUPER_ADMIN_ID"] not in CONFIG["TARGET_CHATS"]:
    CONFIG["TARGET_CHATS"].append(CONFIG["SUPER_ADMIN_ID"])


def validate_config() -> None:
    """
    Строгая валидация обязательных параметров перед запуском диспетчера.
    Предотвращает запуск бота с невалидными критическими параметрами.
    """
    critical_errors: List[str] = []

    if not CONFIG["TG_BOT_TOKEN"]:
        critical_errors.append("TG_BOT_TOKEN не задан! Укажите токен бота в файле .env")
    elif ":" not in CONFIG["TG_BOT_TOKEN"]:
        critical_errors.append("TG_BOT_TOKEN имеет неверный формат Telegram Bot API токена")

    if not CONFIG["SUPER_ADMIN_ID"]:
        critical_errors.append("SUPER_ADMIN_ID не задан! Укажите цифровой Telegram ID владельца в .env")

    if critical_errors:
        err_report = "\n❌ КРИТИЧЕСКИЕ ОШИБКИ КОНФИГУРАЦИИ:\n" + "\n".join(f"  • {e}" for e in critical_errors)
        err_report += "\n\n💡 Проверьте файл .env и перезапустите службу бота."
        raise ValueError(err_report)


def get_safe_config_summary() -> Dict[str, Any]:
    """Возвращает безопасный срез настроек без раскрытия секретных токенов."""
    return {
        "ENVIRONMENT": CONFIG["ENVIRONMENT"],
        "SUPER_ADMIN_ID": CONFIG["SUPER_ADMIN_ID"],
        "TECH_ADMIN_ID": CONFIG["TECH_ADMIN_ID"],
        "HR_GROUP_ID": CONFIG["HR_GROUP_ID"],
        "TARGET_CHATS_COUNT": len(CONFIG["TARGET_CHATS"]),
        "TG_BOT_TOKEN": mask_secret(CONFIG["TG_BOT_TOKEN"]),
        "VK_CONFIGURED": bool(CONFIG["VK_GROUP_TOKEN"] and CONFIG["VK_GROUP_ID"]),
        "MAX_CONFIGURED": bool(CONFIG["MAX_BOT_TOKEN"]),
        "MAINTENANCE_MODE": CONFIG["MAINTENANCE_MODE"],
        "COOLDOWN_SECONDS": CONFIG["COOLDOWN_SECONDS"],
        "DB_PATH": CONFIG["DB_PATH"],
    }