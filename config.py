# -*- coding: utf-8 -*-
"""
Модуль конфигурации МУП «Ульяновскэлектротранс».
Безопасная загрузка переменных окружения из .env файла.
Соответствие требованиям информационной безопасности и 152-ФЗ РФ.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

logger = logging.getLogger("UET_CONFIG")

# Базовые директории проекта
BASE_DIR: Path = Path(__file__).resolve().parent
ENV_PATH: Path = BASE_DIR / ".env"

# Автоматическая загрузка .env
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()


# ==============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ БЕЗОПАСНОГО ПАРСИНГА
# ==============================================================================

def _clean_val(val: Optional[str]) -> str:
    """Очищает значение от внешних кавычек и инлайн-комментариев."""
    if val is None:
        return ""
    val = val.strip()
    # Удаляем инлайн-комментарии, если они не внутри кавычек
    if " #" in val and not (val.startswith(('"', "'")) and val.endswith(('"', "'"))):
        val = val.split(" #", 1)[0].strip()
    # Снимаем внешние кавычки
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1].strip()
    return val


def _get_str(key: str, default: str = "") -> str:
    """Безопасное получение строковой переменной окружения."""
    val = os.getenv(key)
    cleaned = _clean_val(val)
    return cleaned if cleaned else default


def _get_int(key: str, default: int = 0) -> int:
    """Безопасное приведение к целому числу (с поддержкой отрицательных ID групп)."""
    raw = _clean_val(os.getenv(key, ""))
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Не удалось преобразовать переменную %s='%s' в int. Дефолт: %s", key, raw, default)
        return default


def _get_bool(key: str, default: bool = False) -> bool:
    """Безопасное приведение к логическому типу."""
    raw = _clean_val(os.getenv(key, "")).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on", "y", "enable", "enabled")


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


# ==============================================================================
# КЛАСС КОНФИГУРАЦИИ ПРИЛОЖЕНИЯ
# ==============================================================================

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

# Определение пути по умолчанию к базе SQLite (унификация resumes.db)
_default_db = (
    str(BASE_DIR / "resumes.db")
    if (BASE_DIR / "resumes.db").exists()
    else str(DATA_DIR / "resumes.db")
)
resolved_db_path = _get_str("DB_PATH", _default_db)
os.environ["DB_PATH"] = resolved_db_path


# Инициализация глобального словаря настроек
CONFIG: AppConfig = AppConfig({
    # --- Среда выполнения и версия ---
    "ENVIRONMENT": _get_str("ENVIRONMENT", "TEST").upper(),
    "BOT_VERSION": _get_str("BOT_VERSION", "1.3.0"),

    # --- Пути файловой системы ---
    "BASE_DIR": str(BASE_DIR),
    "DATA_DIR": str(DATA_DIR),
    "LOGS_DIR": str(LOGS_DIR),
    "BACKUP_DIR": str(BACKUP_DIR),
    "DB_PATH": resolved_db_path,
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

    # --- Реквизиты оператора ПДн (152-ФЗ РФ) ---
    "OPERATOR_NAME": "МУП «Ульяновскэлектротранс»",
    "OPERATOR_INN": "7325000960",
    "OPERATOR_OGRN": "1027301160350",
    "DATA_RETENTION_DAYS": 180,  # 6 месяцев срок хранения по закону

    # --- Контакты и регламент кадровой службы МУП «УЭТ» ---
    "HR_PHONE": _get_str("HR_PHONE", "+7 (8422) 58-46-60"),
    "HR_ADDRESS": _get_str("HR_ADDRESS", "г. Ульяновск, ул. Гончарова, 2"),
    "HR_SCHEDULE": _get_str("HR_SCHEDULE", "пн-пт 08:00–17:00 (перерыв 12:00–13:00)"),
    "COOLDOWN_SECONDS": _get_int("COOLDOWN_SECONDS", 1200),  # 20 минут антифлуд

    # --- Системные режимы и 152-ФЗ ---
    "MAINTENANCE_MODE": _get_bool("MAINTENANCE_MODE", False),
    "ANONYMIZE_LOGS": _get_bool("ANONYMIZE_LOGS", True),
    "ALLOW_TEST_SUBMISSIONS_IN_PROD": _get_bool("ALLOW_TEST_SUBMISSIONS_IN_PROD", False),

    # --- Telegram Mini App / Веб-панель ---
    "WEB_APP_URL": _get_str("WEB_APP_URL", ""),
    "WEB_APP_HOST": _get_str("WEB_APP_HOST", "0.0.0.0"),
    "WEB_APP_PORT": _get_int("WEB_APP_PORT", 8080),
})

# Заполнение списка чатов для гарантированной доставки уведомлений
if CONFIG["HR_GROUP_ID"] and CONFIG["HR_GROUP_ID"] not in CONFIG["TARGET_CHATS"]:
    CONFIG["TARGET_CHATS"].append(CONFIG["HR_GROUP_ID"])

if CONFIG["SUPER_ADMIN_ID"] and CONFIG["SUPER_ADMIN_ID"] not in CONFIG["TARGET_CHATS"]:
    CONFIG["TARGET_CHATS"].append(CONFIG["SUPER_ADMIN_ID"])


# ==============================================================================
# СЕРВИСНЫЕ МЕТОДЫ УПРАВЛЕНИЯ КОНФИГУРАЦИЕЙ
# ==============================================================================

def load_config() -> AppConfig:
    """Фабричная функция загрузки конфигурации."""
    return CONFIG


def update_env_variable(key: str, value: Any) -> bool:
    """
    Атомарное обновление переменной в файле .env без повреждения структуры и комментариев.
    Синхронизирует значение как на диске, так и в словаре CONFIG и os.environ.
    """
    val_str = str(value).strip()
    target_path = ENV_PATH if ENV_PATH.exists() else (BASE_DIR / ".env")

    try:
        content = ""
        if target_path.exists():
            with open(target_path, "r", encoding="utf-8") as f:
                content = f.read()

        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, content, flags=re.MULTILINE):
            new_content = re.sub(pattern, f"{key}={val_str}", content, flags=re.MULTILINE)
        else:
            delimiter = "\n" if (content and not content.endswith("\n")) else ""
            new_content = f"{content}{delimiter}{key}={val_str}\n"

        with open(target_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        # Обновляем runtime-состояние
        os.environ[key] = val_str
        CONFIG[key] = int(val_str) if val_str.lstrip("-").isdigit() else val_str
        logger.info("Параметр .env '%s' успешно обновлен на '%s'", key, val_str)
        return True
    except Exception as e:
        logger.error("Ошибка обновления переменной .env '%s': %s", key, e)
        return False


def validate_config() -> None:
    """
    Строгая валидация обязательных параметров перед запуском диспетчера.
    Предотвращает запуск бота с невалидными критическими параметрами.
    """
    critical_errors: List[str] = []

    token = CONFIG.get("TG_BOT_TOKEN", "")
    if not token:
        critical_errors.append("TG_BOT_TOKEN не задан! Укажите токен бота в файле .env")
    elif ":" not in token:
        critical_errors.append("TG_BOT_TOKEN имеет неверный формат Telegram Bot API токена")

    super_id = CONFIG.get("SUPER_ADMIN_ID", 0)
    if not super_id or super_id <= 0:
        critical_errors.append("SUPER_ADMIN_ID не задан! Укажите цифровой Telegram ID владельца в .env")

    if critical_errors:
        err_report = (
            "\n❌ КРИТИЧЕСКИЕ ОШИБКИ КОНФИГУРАЦИИ:\n"
            + "\n".join(f"  • {e}" for e in critical_errors)
            + "\n\n💡 Проверьте файл .env и перезапустите службу бота."
        )
        raise ValueError(err_report)


def get_safe_config_summary() -> Dict[str, Any]:
    """Возвращает безопасный срез настроек без раскрытия секретных токенов."""
    return {
        "ENVIRONMENT": CONFIG["ENVIRONMENT"],
        "BOT_VERSION": CONFIG["BOT_VERSION"],
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