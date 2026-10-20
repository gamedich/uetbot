import hashlib
import logging
import secrets
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("UET_HR_BOT")


class AdminMixin:
    """Миксин управления администраторами, правами доступа, паролями и рефералкой депо."""

    def add_admin(self, user_id: int, role: str = "hr") -> bool:
        """Добавление или обновление прав доступа сотрудника."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO admins (user_id, role, notify_dm)
                    VALUES (?, ?, 1)
                    ON CONFLICT(user_id) DO UPDATE SET role = ?
                    """,
                    (user_id, role, role),
                )
                return True
            except Exception as e:
                logger.error(f"Ошибка добавления администратора {user_id}: {e}")
                return False

    def set_admin_role(self, user_id: int, role: str) -> bool:
        """Обновление роли сотрудника (hr, tech, depot)."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("UPDATE admins SET role = ? WHERE user_id = ?", (role, user_id))
            return cursor.rowcount > 0

    def remove_admin(self, user_id: int) -> bool:
        """Отзыв прав доступа сотрудника."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            return cursor.rowcount > 0

    def get_all_admins(self) -> List[Tuple[int, str]]:
        """Получение списка всех сотрудников с назначенными ролями."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, role FROM admins ORDER BY added_at ASC")
            return cursor.fetchall()

    def get_admin_role(self, user_id: int) -> Optional[str]:
        """Получение роли конкретного пользователя."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT role FROM admins WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return str(row[0]) if row else None

    @staticmethod
    def hash_password(password: str) -> str:
        """Безопасное хеширование пароля через scrypt с уникальной солью (152-ФЗ)."""
        if not password:
            return ""
        salt = secrets.token_hex(16)
        h = hashlib.scrypt(password.encode("utf-8"), salt=salt.encode("utf-8"), n=16384, r=8, p=1)
        return f"{salt}${h.hex()}"

    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        """Сверка пароля с хешем scrypt (поддерживает плавную миграцию старых plaintext-паролей)."""
        if not password or not hashed:
            return False
        if "$" in hashed:
            try:
                salt, h = hashed.split("$", 1)
                check = hashlib.scrypt(password.encode("utf-8"), salt=salt.encode("utf-8"), n=16384, r=8, p=1)
                return secrets.compare_digest(check.hex(), h)
            except Exception as e:
                logger.error(f"Ошибка проверки хеша пароля: {e}")
                return False
        # Для обратной совместимости со старыми нехешированными паролями
        return secrets.compare_digest(password, hashed)

    def set_admin_password(self, user_id: int, password: str) -> bool:
        """Установка персонального пароля сотрудника или соискателя (сохраняется только хеш scrypt)."""
        hashed_pwd = self.hash_password(password.strip())
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("UPDATE admins SET web_password = ? WHERE user_id = ?", (hashed_pwd, user_id))
            if cursor.rowcount == 0:
                cursor.execute(
                    "INSERT INTO admins (user_id, role, notify_dm, web_password) VALUES (?, 'applicant', 1, ?)",
                    (user_id, hashed_pwd),
                )
            conn.commit()
            return True

    def get_admin_password(self, user_id: int) -> Optional[str]:
        """Получение хеша пароля веб-панели сотрудника."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT web_password FROM admins WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return str(row[0]) if row and row[0] else None

    def verify_admin_password(self, user_id: int, input_password: str) -> bool:
        """Проверка пароля администратора/соискателя с автомиграцией в хеш при необходимости."""
        saved_hash = self.get_admin_password(user_id)
        if not saved_hash:
            return False
        if self.verify_password(input_password, saved_hash):
            # Если пароль хранился в открытом виде (без знака '$'), автоматически мигрируем на scrypt
            if "$" not in saved_hash:
                self.set_admin_password(user_id, input_password)
            return True
        return False

    def set_admin_email(self, user_id: int, email: str) -> bool:
        """Привязка электронной почты сотрудника или соискателя."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("UPDATE admins SET email = ? WHERE user_id = ?", (email.strip(), user_id))
            if cursor.rowcount == 0:
                cursor.execute(
                    "INSERT INTO admins (user_id, role, notify_dm, email) VALUES (?, 'applicant', 1, ?)",
                    (user_id, email.strip()),
                )
            conn.commit()
            return True

    def get_admin_email(self, user_id: int) -> str:
        """Получение электронной почты сотрудника или соискателя."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT email FROM admins WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return str(row[0]) if row and row[0] else ""

    def set_admin_phone(self, user_id: int, phone: str) -> bool:
        """Привязка номера телефона сотрудника."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("UPDATE admins SET phone = ? WHERE user_id = ?", (phone.strip(), user_id))
            return cursor.rowcount > 0

    def set_admin_full_name(self, user_id: int, full_name: str) -> bool:
        """Сохранение ФИО или никнейма сотрудника."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("UPDATE admins SET full_name = ? WHERE user_id = ?", (full_name.strip(), user_id))
            return cursor.rowcount > 0

    def set_admin_username(self, user_id: int, username: str) -> bool:
        """Сохранение или привязка Telegram юзернейма сотрудника (без @ или с @)."""
        clean_user = username.strip().lstrip("@").lower()
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("UPDATE admins SET username = ? WHERE user_id = ?", (clean_user, user_id))
            if cursor.rowcount == 0:
                cursor.execute(
                    "INSERT INTO admins (user_id, role, notify_dm, username) VALUES (?, 'applicant', 1, ?)",
                    (user_id, clean_user),
                )
            conn.commit()
            return True

    def get_admin_username(self, user_id: int) -> str:
        """Получение юзернейма сотрудника."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT username FROM admins WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return str(row[0]) if row and row[0] else ""

    def get_admin_details(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Полная карточка сотрудника из базы."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, role, notify_dm, web_password, phone, full_name, added_at, email, username FROM admins WHERE user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "user_id": row[0],
                "role": row[1],
                "notify_dm": bool(row[2]),
                "has_password": bool(row[3]),
                "web_password": row[3] or "",
                "phone": row[4] or "",
                "full_name": row[5] or "",
                "added_at": row[6] or "",
                "email": row[7] or "",
                "username": row[8] or "",
            }

    def sync_system_admins(self, super_id: int, tech_id: int) -> None:
        """Синхронизирует системных администраторов из .env в таблицу admins."""
        if super_id and super_id > 0:
            self.add_admin(super_id, role="superadmin")
        if tech_id and tech_id > 0 and tech_id != super_id:
            self.add_admin(tech_id, role="tech")

    def get_admin_notify_status(self, user_id: int) -> bool:
        """Проверка статуса дублирования анкет в личные сообщения."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT notify_dm FROM admins WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return bool(row[0]) if row else True

    def set_admin_notify_status(self, user_id: int, enabled: bool) -> bool:
        """Явная установка статуса дублирования в ЛС."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            val = 1 if enabled else 0
            cursor.execute("UPDATE admins SET notify_dm = ? WHERE user_id = ?", (val, user_id))
            return cursor.rowcount > 0

    def toggle_admin_notify(self, user_id: int) -> bool:
        """Переключение персонального статуса ЛС-уведомлений."""
        current = self.get_admin_notify_status(user_id)
        new_status = 0 if current else 1
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("UPDATE admins SET notify_dm = ? WHERE user_id = ?", (new_status, user_id))
        return bool(new_status)

    def set_all_hr_notify_dm(self, notify_status: int = 1) -> int:
        """Пакетное переключение доставки в ЛС для всех кадровиков."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("UPDATE admins SET notify_dm = ? WHERE role = 'hr'", (notify_status,))
            return cursor.rowcount

    def get_admins_with_dm_enabled(self, role: str = "hr") -> List[int]:
        """Получение списка ID сотрудников с активными уведомлениями в ЛС."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM admins WHERE notify_dm = 1 AND role = ?", (role,))
            return [row[0] for row in cursor.fetchall()]

    def get_hr_admins_with_dm_enabled(self) -> List[int]:
        return self.get_admins_with_dm_enabled(role="hr")

    def get_depot_referrals(self, referrer_tab: Union[int, str]) -> Dict[str, Any]:
        """Возвращает кандидатов и статистику начислений по реферальной программе депо."""
        ref_clean = str(referrer_tab).replace("ref_", "").strip()
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT ticket_id, full_name, vacancy, status, created_at, interview_status
                FROM candidates
                WHERE referrer_tab = ? OR referrer_tab = ? OR referrer_tab = ?
                ORDER BY ticket_id DESC
                """,
                (ref_clean, f"ref_{ref_clean}", str(referrer_tab)),
            )
            rows = cursor.fetchall()

        candidates = []
        hired_count = 0
        in_progress_count = 0
        for r in rows:
            c = {
                "ticket_id": r[0],
                "full_name": r[1],
                "vacancy": r[2] or "Не указано",
                "status": r[3] or "Новая",
                "created_at": r[4] or "",
                "interview_status": r[5] or "pending",
            }
            if r[3] == "Трудоустроен":
                hired_count += 1
            elif r[3] in ("Новая", "В работе", "Приглашен"):
                in_progress_count += 1
            candidates.append(c)

        bonus_total = hired_count * 10000
        return {
            "referrer_code": ref_clean,
            "total_referred": len(candidates),
            "in_progress": in_progress_count,
            "hired": hired_count,
            "bonus_total": bonus_total,
            "candidates": candidates,
        }

    def get_all_depot_referrals(self) -> List[Dict[str, Any]]:
        """Возвращает сводный реестр всех кандидатов с реферальными отметками депо."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT ticket_id, full_name, vacancy, status, created_at, referrer_tab, interview_status
                FROM candidates
                WHERE referrer_tab IS NOT NULL AND referrer_tab != ''
                ORDER BY ticket_id DESC
                """
            )
            rows = cursor.fetchall()
            return [
                {
                    "ticket_id": r[0],
                    "full_name": r[1],
                    "vacancy": r[2] or "Не указано",
                    "status": r[3] or "Новая",
                    "created_at": r[4] or "",
                    "referrer_tab": r[5],
                    "interview_status": r[6] or "pending",
                }
                for r in rows
            ]
