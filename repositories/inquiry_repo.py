import logging
from typing import Any, Optional, Tuple

logger = logging.getLogger("UET_HR_BOT")


class InquiryMixin:
    """Миксин обработки вопросов соискателей, антифлуда и сессий живого диалога с кадровиком."""

    def check_inquiry_cooldown(self, user_id: str, cooldown_seconds: int = 1200) -> Tuple[bool, int]:
        """Проверка тайм-аута антифлуда между вопросами соискателя."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT (strftime('%s', 'now') - strftime('%s', last_inquiry_at))
                FROM user_cooldowns WHERE user_id = ?
                """,
                (str(user_id),),
            )
            row = cursor.fetchone()
            if not row or row[0] is None:
                return True, 0
            elapsed = row[0]
            if elapsed < cooldown_seconds:
                return False, cooldown_seconds - elapsed
            return True, 0

    def add_inquiry(
        self,
        platform: str,
        user_id: str,
        question_text: str,
        ticket_id: Optional[int] = None,
        full_name: str = "",
        phone: str = "",
        vacancy: str = "",
        is_test: bool = False,
        consent_timestamp: Optional[str] = None,
        email: str = "",
        reply_channel: str = "tg",
    ) -> int:
        is_test_val = 1 if is_test else 0
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO inquiries (
                    ticket_id, platform, user_id, full_name, phone, vacancy, question_text, is_test, consent_timestamp, email, reply_channel
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket_id,
                    platform,
                    str(user_id),
                    full_name,
                    phone,
                    vacancy,
                    question_text,
                    is_test_val,
                    consent_timestamp or "",
                    email or "",
                    reply_channel or "tg",
                ),
            )
            cursor.execute(
                """
                INSERT OR REPLACE INTO user_cooldowns (user_id, last_inquiry_at)
                VALUES (?, CURRENT_TIMESTAMP)
                """,
                (str(user_id),),
            )
            conn.commit()
            return cursor.lastrowid

    def get_inquiry(self, inquiry_id: int) -> Optional[Tuple[Any, ...]]:
        """Получение данных обращения по ID."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT inquiry_id, ticket_id, platform, user_id, full_name, phone, vacancy,
                       question_text, status, admin_reply, created_at, closed_at, email, reply_channel
                FROM inquiries WHERE inquiry_id = ?
                """,
                (inquiry_id,),
            )
            return cursor.fetchone()

    def reply_inquiry(self, inquiry_id: int, reply_text: str) -> bool:
        """Сохранение ответа кадровика на вопрос соискателя."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE inquiries SET admin_reply = ?, status = 'replied' WHERE inquiry_id = ?",
                (reply_text, inquiry_id),
            )
            return cursor.rowcount > 0

    def close_inquiry(self, inquiry_id: int) -> bool:
        """Закрытие тикета обращения."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE inquiries SET status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE inquiry_id = ?",
                (inquiry_id,),
            )
            return cursor.rowcount > 0

    def get_inquiries_count(self) -> int:
        """Количество активных обращений соискателей."""
        try:
            with self.connection() as conn:  # type: ignore
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM inquiries WHERE status != 'closed'")
                row = cursor.fetchone()
                return int(row[0]) if row else 0
        except Exception as e:
            logger.error("Ошибка подсчёта обращений: %s", e)
            return 0

    def start_direct_dialog(
        self,
        user_id: str,
        operator_id: int,
        ticket_id: Optional[int] = None,
        full_name: str = "",
        platform: str = "tg",
    ) -> bool:
        """Открытие сессии прямого моста между соискателем и кадровой службой."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO active_dialogs (user_id, operator_id, ticket_id, full_name, platform, started_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (str(user_id), operator_id, ticket_id, full_name, platform),
            )
            return True

    def get_dialog_by_user(self, user_id: str) -> Optional[Tuple[Any, ...]]:
        """Проверка, находится ли кандидат в прямом диалоге."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, operator_id, ticket_id, full_name, platform FROM active_dialogs WHERE user_id = ?",
                (str(user_id),),
            )
            return cursor.fetchone()

    def get_dialog_by_operator(self, operator_id: int) -> Optional[Tuple[Any, ...]]:
        """Проверка, ведёт ли оператор/чат активный диалог."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, operator_id, ticket_id, full_name, platform FROM active_dialogs WHERE operator_id = ?",
                (operator_id,),
            )
            return cursor.fetchone()

    def end_direct_dialog(self, user_id: Optional[str] = None, operator_id: Optional[int] = None) -> bool:
        """Завершение прямого диалога со стороны пользователя или оператора."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            if user_id:
                cursor.execute("DELETE FROM active_dialogs WHERE user_id = ?", (str(user_id),))
            elif operator_id:
                cursor.execute("DELETE FROM active_dialogs WHERE operator_id = ?", (operator_id,))
            return cursor.rowcount > 0
