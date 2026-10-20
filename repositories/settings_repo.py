import logging
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("UET_HR_BOT")


class SettingsMixin:
    """Миксин безопасности, чёрного списка (ЧС), системных настроек и PRAGMA-оптимизации базы."""

    def block_user_and_clean(self, user_id: Any, reason: str = "Спам") -> bool:
        """Внесение в ЧС с каскадным аннулированием анкет, диалогов и обращений."""
        uid_str = str(user_id)
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT OR REPLACE INTO blacklist (user_id, reason) VALUES (?, ?)",
                    (uid_str, reason),
                )
                cursor.execute("DELETE FROM active_dialogs WHERE user_id = ?", (uid_str,))
                cursor.execute(
                    "UPDATE candidates SET status = 'Отказ (ЧС)', updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (uid_str,),
                )
                cursor.execute("UPDATE inquiries SET status = 'Закрыто (ЧС)' WHERE user_id = ?", (uid_str,))
                return True
            except Exception as e:
                logger.error(f"Ошибка блокировки пользователя {uid_str}: {e}")
                return False

    def block_user(self, user_id: Any, reason: str = "Спам") -> bool:
        """Точечное добавление пользователя в чёрный список."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT OR REPLACE INTO blacklist (user_id, reason) VALUES (?, ?)",
                    (str(user_id), reason),
                )
                return True
            except Exception as e:
                logger.error(f"Ошибка ЧС: {e}")
                return False

    def unblock_user(self, user_id: Any) -> bool:
        """Исключение пользователя из чёрного списка."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("DELETE FROM blacklist WHERE user_id = ?", (str(user_id),))
            return cursor.rowcount > 0

    def get_blacklist(self) -> List[Tuple[str, str, str]]:
        """Список всех заблокированных пользователей."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, reason, blocked_at FROM blacklist ORDER BY blocked_at DESC")
            return cursor.fetchall()

    def is_blocked(self, user_id: Any, super_admin_id: Optional[int] = None) -> bool:
        """Проверка нахождения пользователя в чёрном списке."""
        if not user_id:
            return False
        if super_admin_id is not None and str(user_id) == str(super_admin_id):
            return False
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (str(user_id),))
            return cursor.fetchone() is not None

    def check_health(self) -> bool:
        """Проверка целостности и доступности базы данных."""
        try:
            with self.connection() as conn:  # type: ignore
                conn.execute("SELECT 1 FROM candidates LIMIT 1")
                conn.execute("SELECT 1 FROM inquiries LIMIT 1")
                return True
        except Exception as e:
            logger.error(f"Сбой проверки базы данных: {e}")
            return False

    def get_setting(self, key: str, default: str = "") -> str:
        """Получение значения настройки из БД."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return str(row[0]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        """Сохранение системной настройки в БД."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO system_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                (key, str(value), str(value)),
            )

    def get_all_settings(self) -> Dict[str, str]:
        """Получение всех базовых настроек без динамических переопределений текстов."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM system_settings WHERE key NOT LIKE 'text_override:%'")
            return {row[0]: row[1] for row in cursor.fetchall()}

    def get_all_text_overrides(self) -> Dict[str, str]:
        """Получение всех переопределений текстов из веб-панели."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM system_settings WHERE key LIKE 'text_override:%'")
            return {row[0].replace("text_override:", ""): row[1] for row in cursor.fetchall()}

    def get_dynamic_text(self, text_key: str, default_val: str = "") -> str:
        """Возвращает динамически переопределённый текст или дефолтное значение."""
        override = self.get_setting(f"text_override:{text_key}", "")
        return override if override else default_val

    def set_dynamic_text(self, text_key: str, value: str) -> None:
        """Запись переопределения текста."""
        self.set_setting(f"text_override:{text_key}", value)

    def checkpoint_and_optimize(self) -> bool:
        """Сброс WAL-журнала на диск и оптимизация страниц памяти."""
        try:
            with self.connection() as conn:  # type: ignore
                try:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
                except sqlite3.OperationalError:
                    pass
                conn.execute("PRAGMA optimize;")
            return True
        except Exception as e:
            logger.error(f"Ошибка оптимизации SQLite: {e}")
            return False
