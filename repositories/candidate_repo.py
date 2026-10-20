import asyncio
from datetime import datetime, timedelta
import logging
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("UET_HR_BOT")


class EmployeeMixin:
    """Миксин личного кабинета сотрудников депо и реферальной программы."""

    def get_employee_by_tg(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """Ищет сотрудника по Telegram ID в активной базе"""
        with self._get_connection() as conn:  # type: ignore
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM employees WHERE telegram_id = ? AND is_active = 1", (telegram_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_employee_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Ищет сотрудника по последним 10 цифрам номера телефона"""
        clean = re.sub(r"\D", "", phone)[-10:]
        with self._get_connection() as conn:  # type: ignore
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM employees WHERE phone LIKE ? AND is_active = 1", (f"%{clean}",))
            row = cur.fetchone()
            return dict(row) if row else None

    def link_employee_tg(self, tab_number: str, telegram_id: int) -> bool:
        """Привязывает Telegram ID к табельному номеру сотрудника"""
        with self._get_connection() as conn:  # type: ignore
            cur = conn.cursor()
            cur.execute(
                "UPDATE employees SET telegram_id = ?, updated_at = CURRENT_TIMESTAMP WHERE tab_number = ?",
                (telegram_id, str(tab_number).strip()),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_employee_referrals(self, tab_number: str) -> List[Dict[str, Any]]:
        """Получает список соискателей, пришедших по ссылке этого сотрудника"""
        with self._get_connection() as conn:  # type: ignore
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ticket_id, full_name, vacancy, status, created_at
                FROM candidates
                WHERE referrer_tab = ?
                ORDER BY ticket_id DESC
                """,
                (str(tab_number).strip(),),
            )
            return [dict(r) for r in cur.fetchall()]

    def add_doc_request(self, tab_number: str, full_name: str, req_type: str) -> int:
        """Создает заявку на получение справки"""
        with self._get_connection() as conn:  # type: ignore
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO employee_requests (tab_number, full_name, req_type) VALUES (?, ?, ?)",
                (str(tab_number).strip(), full_name.strip(), req_type.strip()),
            )
            conn.commit()
            return cur.lastrowid

    @staticmethod
    def _parse_ts(ts_str: Optional[str]) -> datetime:
        """Безопасный парсинг временных меток разных форматов."""
        if not ts_str:
            return datetime.now()
        clean_str = str(ts_str).replace("T", " ").split(".")[0]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                return datetime.strptime(clean_str, fmt)
            except ValueError:
                continue
        return datetime.now()


class CandidateMixin:
    """Миксин управления анкетами соискателей, кадровой воронкой и требованиями 152-ФЗ РФ."""

    def add_candidate(
        self,
        platform: str,
        user_id: str,
        full_name: str,
        phone: str,
        vacancy: str,
        experience: str,
        is_test: bool = False,
        consent_timestamp: Optional[str] = None,
        birth_date: str = "",
        city: str = "",
        driver_license: str = "",
        education: str = "",
        relocation: str = "",
        dormitory: str = "",
        shift_work: str = "",
        medical_restrictions: str = "",
        criminal_record: str = "",
        source: str = "",
        extra_info: str = "",
        raw_data_json: str = "{}",
        referrer_tab: str = "",
        target_db: str = "",
    ) -> int:
        target_setting = self.get_setting("active_db_target", "resumes.db")  # type: ignore
        if target_db == "prod":
            use_test = False
        elif target_db == "test":
            use_test = True
        else:
            use_test = is_test or (target_setting == "resumes_test.db")

        with self._get_connection(is_test=use_test) as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO candidates (
                    platform, user_id, full_name, phone, vacancy, experience, is_test, consent_timestamp,
                    birth_date, city, driver_license, education, relocation, dormitory, shift_work,
                    medical_restrictions, criminal_record, source, extra_info, raw_data_json, referrer_tab, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    platform,
                    str(user_id),
                    full_name,
                    phone,
                    vacancy,
                    experience,
                    1 if use_test else 0,
                    consent_timestamp or "",
                    birth_date,
                    city,
                    driver_license,
                    education,
                    relocation,
                    dormitory,
                    shift_work,
                    medical_restrictions,
                    criminal_record,
                    source,
                    extra_info,
                    raw_data_json,
                    str(referrer_tab).strip() if referrer_tab else None,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def delete_candidate_by_user(self, user_id: str, platform: str = "tg") -> Optional[int]:
        """
        Каскадное удаление данных пользователя при отзыве согласия на обработку ПДн (ст. 21 152-ФЗ).
        Очищает анкету, связанные диалоги, обращения и согласие.
        """
        uid = str(user_id)
        deleted_id = None
        for is_test in (False, True):
            with self._get_connection(is_test=is_test) as conn:  # type: ignore
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT ticket_id FROM candidates WHERE user_id = ? AND platform = ? ORDER BY ticket_id DESC LIMIT 1",
                    (uid, platform),
                )
                row = cursor.fetchone()
                if row:
                    t_id = row[0]
                    cursor.execute("DELETE FROM candidates WHERE ticket_id = ?", (t_id,))
                    cursor.execute("DELETE FROM active_dialogs WHERE user_id = ?", (uid,))
                    cursor.execute("DELETE FROM inquiries WHERE user_id = ? AND platform = ?", (uid, platform))
                    cursor.execute("DELETE FROM user_consents WHERE user_id = ?", (uid,))
                    cursor.execute("DELETE FROM user_cooldowns WHERE user_id = ?", (uid,))
                    deleted_id = int(t_id)
        return deleted_id

    def get_candidate_dict_by_user(self, user_id: str, platform: str = "tg") -> Optional[Dict[str, Any]]:
        """Получение словаря данных анкеты для команды выгрузки /mydata (ст. 14 152-ФЗ)."""
        with self.connection() as conn:  # type: ignore
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM candidates WHERE user_id = ? AND platform = ? ORDER BY ticket_id DESC LIMIT 1",
                (str(user_id), platform),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_candidate(self, ticket_id: int) -> Optional[Tuple]:
        """Поиск анкеты по ID с автоматической маршрутизацией по диапазону номеров."""
        if not ticket_id or ticket_id <= 0:
            return None

        is_test = ticket_id >= 900000

        # 1. Запрос в целевую базу
        with self._get_connection(is_test=is_test) as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT ticket_id, platform, user_id, full_name, phone, vacancy, experience, status, admin_note, created_at,
                       birth_date, city, driver_license, education, relocation, dormitory, shift_work,
                       medical_restrictions, criminal_record, source, extra_info, consent_timestamp, raw_data_json,
                       interview_depot, interview_time, interview_status
                FROM candidates WHERE ticket_id = ?
                """,
                (ticket_id,),
            )
            row = cursor.fetchone()
            if row:
                return row

        # 2. Резервный поиск в альтернативной базе
        with self._get_connection(is_test=not is_test) as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT ticket_id, platform, user_id, full_name, phone, vacancy, experience, status, admin_note, created_at,
                       birth_date, city, driver_license, education, relocation, dormitory, shift_work,
                       medical_restrictions, criminal_record, source, extra_info, consent_timestamp, raw_data_json,
                       interview_depot, interview_time, interview_status
                FROM candidates WHERE ticket_id = ?
                """,
                (ticket_id,),
            )
            return cursor.fetchone()

    def get_candidate_by_user_id(
        self, user_id: str, platform: str = "tg", is_test: Optional[bool] = None
    ) -> Optional[Tuple]:
        """Поиск последней анкеты пользователя с бесшовным поиском в активной и резервной базах."""
        if is_test is True or is_test is False:
            with self._get_connection(is_test=is_test) as conn:  # type: ignore
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT ticket_id, platform, user_id, full_name, phone, vacancy, experience, status, admin_note, created_at,
                           birth_date, city, driver_license, education, relocation, dormitory, shift_work,
                           medical_restrictions, criminal_record, source, extra_info, consent_timestamp, raw_data_json,
                           interview_depot, interview_time, interview_status
                    FROM candidates WHERE user_id = ? AND platform = ? ORDER BY ticket_id DESC LIMIT 1
                    """,
                    (str(user_id), platform),
                )
                return cursor.fetchone()

        target = self.get_setting("active_db_target", "resumes.db")  # type: ignore
        prefer_test = target == "resumes_test.db"

        row = self.get_candidate_by_user_id(user_id, platform=platform, is_test=prefer_test)
        if not row:
            row = self.get_candidate_by_user_id(user_id, platform=platform, is_test=not prefer_test)
        return row

    def check_candidate_can_apply(
        self, user_id: str, platform: str = "tg"
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Проверка возможности подачи анкеты согласно регламенту предприятия:
        1. Активная анкета на рассмотрении (Новая, В работе, Приглашен).
        2. Кулдаун 90 дней (3 месяца) после отказа.
        """
        uid_str = str(user_id)
        for is_test in (False, True):
            with self._get_connection(is_test=is_test) as conn:  # type: ignore
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT ticket_id, status, created_at, full_name, vacancy
                    FROM candidates
                    WHERE user_id = ? AND platform = ? AND (status IN ('Новая', 'В работе', 'Приглашен') OR status LIKE 'Приглашен%')
                    ORDER BY ticket_id DESC LIMIT 1
                    """,
                    (uid_str, platform),
                )
                active_row = cursor.fetchone()
                if active_row:
                    t_id, st, cr, fn, vc = active_row
                    return (
                        False,
                        "unprocessed",
                        {
                            "ticket_id": t_id,
                            "status": st,
                            "created_at": cr,
                            "full_name": fn,
                            "vacancy": vc,
                        },
                    )

                cursor.execute(
                    """
                    SELECT ticket_id, status, created_at, updated_at, full_name, vacancy
                    FROM candidates
                    WHERE user_id = ? AND platform = ? AND (status = 'Отказ' OR status LIKE 'Отказ%')
                    ORDER BY ticket_id DESC LIMIT 1
                    """,
                    (uid_str, platform),
                )
                refuse_row = cursor.fetchone()
                if refuse_row:
                    t_id, st, cr, up, fn, vc = refuse_row
                    refuse_ts = up or cr
                    refuse_dt = self._parse_ts(refuse_ts)
                    now = datetime.now()
                    cooldown_days = 90
                    diff_seconds = (now - refuse_dt).total_seconds()
                    diff_days = int(diff_seconds // 86400)

                    if diff_days < cooldown_days:
                        available_dt = refuse_dt + timedelta(days=cooldown_days)
                        remaining_seconds = max(0.0, (available_dt - now).total_seconds())
                        days_left = max(1, int(remaining_seconds // 86400) + 1)
                        return (
                            False,
                            "rejected_cooldown",
                            {
                                "ticket_id": t_id,
                                "status": st,
                                "refuse_date": refuse_dt.strftime("%d.%m.%Y"),
                                "available_date": available_dt.strftime("%d.%m.%Y"),
                                "days_left": days_left,
                                "full_name": fn,
                                "vacancy": vc,
                            },
                        )

        return True, "ok", None

    def delete_candidate(self, ticket_id: int) -> bool:
        """Физическое удаление анкеты кандидата и связанных диалогов из базы (с поддержкой обеих БД)."""
        is_test = ticket_id >= 900000
        deleted = False
        for flag in (is_test, not is_test):
            with self._get_connection(is_test=flag) as conn:  # type: ignore
                cursor = conn.cursor()
                cursor.execute("DELETE FROM candidates WHERE ticket_id = ?", (ticket_id,))
                if cursor.rowcount > 0:
                    deleted = True
                cursor.execute("DELETE FROM active_dialogs WHERE ticket_id = ?", (ticket_id,))
                cursor.execute("DELETE FROM inquiries WHERE ticket_id = ?", (ticket_id,))
                conn.commit()
        return deleted

    def delete_candidate_152fz(self, ticket_id: int) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Удаление анкеты с возвратом метаданных для оформления акта уничтожения."""
        cand = self.get_candidate(ticket_id)
        if not cand:
            return False, None
        cand_info = {
            "ticket_id": cand[0],
            "platform": cand[1],
            "user_id": str(cand[2]),
            "full_name": cand[3],
            "phone": cand[4],
            "vacancy": cand[5],
        }
        self.delete_candidate(ticket_id)
        return True, cand_info

    def reset_candidate_for_test(self, user_id: str, platform: str = "tg") -> bool:
        """Полная очистка тестовых записей администратора без задевания боевых анкет."""
        uid = str(user_id)
        deleted = False
        for flag in (True, False):
            with self._get_connection(is_test=flag) as conn:  # type: ignore
                cursor = conn.cursor()
                cursor.execute("DELETE FROM candidates WHERE user_id = ? AND platform = ?", (uid, platform))
                if cursor.rowcount > 0:
                    deleted = True
                cursor.execute("DELETE FROM active_dialogs WHERE user_id = ?", (uid,))
                cursor.execute("DELETE FROM inquiries WHERE user_id = ? AND platform = ?", (uid, platform))
                cursor.execute("DELETE FROM user_cooldowns WHERE user_id = ?", (uid,))
                conn.commit()
        return deleted

    def reset_all_test_data(self) -> Tuple[int, int]:
        """Очистка всех тестовых анкет и обращений из обеих баз данных."""
        cand_cnt = 0
        inq_cnt = 0
        for flag in (True, False):
            with self._get_connection(is_test=flag) as conn:  # type: ignore
                cursor = conn.cursor()
                cursor.execute(
                    """
                    DELETE FROM candidates
                    WHERE is_test = 1 OR status LIKE '%Тест%'
                       OR full_name LIKE '%Тест%' OR full_name LIKE '%тест%'
                       OR ticket_id >= 900000
                    """
                )
                cand_cnt += cursor.rowcount
                cursor.execute(
                    """
                    DELETE FROM inquiries
                    WHERE is_test = 1 OR question_text LIKE '%Тест%'
                       OR full_name LIKE '%Тест%' OR full_name LIKE '%тест%'
                       OR inquiry_id >= 900000
                    """
                )
                inq_cnt += cursor.rowcount
                cursor.execute("DELETE FROM active_dialogs WHERE full_name LIKE '%Тест%' OR full_name LIKE '%тест%'")
                conn.commit()
        return cand_cnt, inq_cnt

    def purge_all_candidates_for_debug(self) -> Tuple[int, int]:
        """Полная очистка всех анкет (только для среды разработки/отладки)."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("DELETE FROM candidates")
            cand_cnt = cursor.rowcount
            cursor.execute("DELETE FROM inquiries")
            inq_cnt = cursor.rowcount
            cursor.execute("DELETE FROM active_dialogs")
            cursor.execute("DELETE FROM user_cooldowns")
            cursor.execute("UPDATE sqlite_sequence SET seq = 0 WHERE name IN ('candidates', 'inquiries')")
            return cand_cnt, inq_cnt

    def get_all_candidates_for_export(self) -> List[Tuple[Any, ...]]:
        """Получение всех анкет для выгрузки в CSV/Excel."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT ticket_id, platform, user_id, full_name, phone, vacancy, experience, status, admin_note, created_at
                FROM candidates
                ORDER BY ticket_id DESC
                """
            )
            return cursor.fetchall()

    def get_recent_candidates(
        self,
        limit: int = 10,
        filter_status: Optional[str] = None,
        only_new: bool = False,
        is_archive: bool = False,
    ) -> List[Tuple[Any, ...]]:
        """Безопасное получение списка анкет со 100% параметризацией запроса (защита от SQLi)."""
        params: List[Any] = []
        if only_new:
            where_sql = "status = ?"
            params.append("Новая")
        elif is_archive or filter_status == "Архив":
            where_sql = "status = ?"
            params.append("Архив")
        elif filter_status:
            where_sql = "status = ?"
            params.append(filter_status)
        else:
            where_sql = "status != ?"
            params.append("Архив")

        params.append(limit)
        query = (
            f"SELECT ticket_id, full_name, vacancy, status, created_at, platform "
            f"FROM candidates WHERE {where_sql} ORDER BY ticket_id DESC LIMIT ?"
        )
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            return cursor.fetchall()

    def update_status(self, ticket_id: int, new_status: str):
        is_test = ticket_id >= 900000
        with self._get_connection(is_test=is_test) as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE candidates SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE ticket_id = ?",
                (new_status, ticket_id),
            )
            conn.commit()

    def update_admin_note(self, ticket_id: int, note: str):
        is_test = ticket_id >= 900000
        with self._get_connection(is_test=is_test) as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE candidates SET admin_note = ?, updated_at = CURRENT_TIMESTAMP WHERE ticket_id = ?",
                (note, ticket_id),
            )
            conn.commit()

    def set_interview_details(self, ticket_id: int, depot: str, slot_time: str) -> bool:
        """Сохранение выбранного филиала депо и времени собеседования."""
        is_test = ticket_id >= 900000
        with self._get_connection(is_test=is_test) as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE candidates
                SET interview_depot = ?, interview_time = ?, interview_status = 'pending', status = 'Приглашен', updated_at = CURRENT_TIMESTAMP
                WHERE ticket_id = ?
                """,
                (depot, slot_time, ticket_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def update_interview_status(self, ticket_id: int, status: str) -> bool:
        """Обновление статуса явки на собеседование (confirmed, reschedule и т.д.)."""
        is_test = ticket_id >= 900000
        with self._get_connection(is_test=is_test) as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE candidates SET interview_status = ?, updated_at = CURRENT_TIMESTAMP WHERE ticket_id = ?",
                (status, ticket_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_statistics(self, scope: str = "both") -> Dict[str, int]:
        """Агрегированный подсчёт кадровой воронки предприятия с поддержкой обеих баз (PROD и TEST)."""
        if scope == "both":
            target_dbs = [False, True]
        elif scope == "test":
            target_dbs = [True]
        elif scope == "prod":
            target_dbs = [False]
        else:
            active_db = self.get_setting("active_db_target", "resumes.db")  # type: ignore
            target_dbs = [active_db == "resumes_test.db"]

        res = {
            "total": 0,
            "new": 0,
            "in_progress": 0,
            "invited": 0,
            "hired": 0,
            "rejected": 0,
            "archive": 0,
            "test_total": 0,
            "prod_total": 0,
        }

        for is_test_db in target_dbs:
            try:
                with self.connection(is_test=is_test_db) as conn:  # type: ignore
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM candidates")
                    cnt = cursor.fetchone()[0]
                    res["total"] += cnt

                    cursor.execute("SELECT COUNT(*) FROM candidates WHERE is_test = 1 OR ticket_id >= 900000")
                    test_cnt = cursor.fetchone()[0]
                    if is_test_db:
                        res["test_total"] += cnt
                        res["prod_total"] += max(0, cnt - test_cnt)
                    else:
                        res["test_total"] += test_cnt
                        res["prod_total"] += max(0, cnt - test_cnt)

                    cursor.execute("SELECT COUNT(*) FROM candidates WHERE status = 'Новая'")
                    res["new"] += cursor.fetchone()[0]

                    cursor.execute("SELECT COUNT(*) FROM candidates WHERE status = 'В работе'")
                    res["in_progress"] += cursor.fetchone()[0]

                    cursor.execute("SELECT COUNT(*) FROM candidates WHERE status LIKE 'Приглашен%'")
                    res["invited"] += cursor.fetchone()[0]

                    cursor.execute("SELECT COUNT(*) FROM candidates WHERE status LIKE 'Принят%'")
                    res["hired"] += cursor.fetchone()[0]

                    cursor.execute("SELECT COUNT(*) FROM candidates WHERE status LIKE 'Отказ%'")
                    res["rejected"] += cursor.fetchone()[0]

                    cursor.execute("SELECT COUNT(*) FROM candidates WHERE status = 'Архив'")
                    res["archive"] += cursor.fetchone()[0]
            except Exception as e:
                logger.error("Ошибка подсчёта статистики в базе (is_test=%s): %s", is_test_db, e)

        return res

    get_stats = get_statistics

    def get_user_consent(self, user_id: Any) -> Optional[str]:
        """Возвращает дату и время зафиксированного согласия 152-ФЗ."""
        uid = str(user_id)
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT consent_timestamp FROM user_consents WHERE user_id = ?", (uid,))
            row = cursor.fetchone()
            if row and row[0]:
                return row[0]

            cursor.execute(
                "SELECT consent_timestamp FROM candidates WHERE user_id = ? AND consent_timestamp != '' ORDER BY ticket_id DESC LIMIT 1",
                (uid,),
            )
            row2 = cursor.fetchone()
            if row2 and row2[0]:
                cursor.execute(
                    "INSERT OR REPLACE INTO user_consents (user_id, consent_timestamp) VALUES (?, ?)",
                    (uid, row2[0]),
                )
                return row2[0]
            return None

    def set_user_consent(self, user_id: Any, timestamp: Optional[str] = None) -> str:
        """Фиксация факта получения согласия субъекта ПДн."""
        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO user_consents (user_id, consent_timestamp) VALUES (?, ?)",
                (str(user_id), ts),
            )
        return ts

    def revoke_user_consent(self, user_id: Any) -> bool:
        """Отзыв согласия и удаление отметки по ст. 21 152-ФЗ."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_consents WHERE user_id = ?", (str(user_id),))
            return cursor.rowcount > 0

    def log_pdn_destruction(
        self,
        candidate_id: int,
        user_id: str,
        platform: str,
        reason: str,
        act_number: str = "",
        operator: str = "Система",
    ) -> int:
        """Фиксация уничтожения ПДн в электронном журнале (Приказ Роскомнадзора № 179)."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        act_num = act_number or f"{candidate_id}-УПД"
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO destruction_logs (candidate_id, user_id, platform, reason, destroyed_at, act_number, operator)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (candidate_id, str(user_id), platform, reason, now_str, act_num, operator),
            )
            return int(cursor.lastrowid)

    def get_destruction_logs(self, limit: int = 50) -> List[Tuple[Any, ...]]:
        """Возвращает журнал уничтожения ПДн для проверок Роскомнадзора."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, candidate_id, user_id, platform, reason, destroyed_at, act_number, operator
                FROM destruction_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            return cursor.fetchall()

    def cleanup_expired_candidates(self, max_days: int = 1095) -> List[int]:
        """
        Автоматическое уничтожение анкет с истёкшим сроком хранения (152-ФЗ).
        """
        cutoff = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d %H:%M:%S")
        purged_ids: List[int] = []

        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT ticket_id, user_id, platform, full_name, phone
                FROM candidates
                WHERE created_at < ?
                """,
                (cutoff,),
            )
            expired = cursor.fetchall()

        for row in expired:
            t_id, u_id, plat, fio, ph = row
            self.log_pdn_destruction(
                candidate_id=t_id,
                user_id=u_id,
                platform=plat,
                reason="Истечение 6-месячного срока хранения (ст. 5, 21 152-ФЗ)",
                act_number=f"{t_id}-УПД",
            )
            self.delete_candidate(t_id)
            purged_ids.append(t_id)

        if purged_ids:
            self.checkpoint_and_optimize()  # type: ignore
        return purged_ids

    # Асинхронные фасады
    async def async_add_candidate(self, *args, **kwargs) -> int:
        return await asyncio.to_thread(self.add_candidate, *args, **kwargs)

    async def async_get_candidate(self, ticket_id: int) -> Optional[Tuple[Any, ...]]:
        return await asyncio.to_thread(self.get_candidate, ticket_id)

    async def async_get_candidate_by_user_id(self, user_id: str, platform: str = "tg") -> Optional[Tuple[Any, ...]]:
        return await asyncio.to_thread(self.get_candidate_by_user_id, user_id, platform)

    async def async_check_candidate_can_apply(
        self, user_id: str, platform: str = "tg"
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return await asyncio.to_thread(self.check_candidate_can_apply, user_id, platform)

    async def async_get_recent_candidates(self, *args, **kwargs) -> List[Tuple[Any, ...]]:
        return await asyncio.to_thread(self.get_recent_candidates, *args, **kwargs)

    async def async_get_statistics(self) -> Dict[str, int]:
        return await asyncio.to_thread(self.get_statistics)

    async def async_delete_candidate_152fz(self, ticket_id: int) -> Tuple[bool, Optional[Dict[str, Any]]]:
        return await asyncio.to_thread(self.delete_candidate_152fz, ticket_id)

    async def async_update_status(self, ticket_id: int, new_status: str) -> None:
        await asyncio.to_thread(self.update_status, ticket_id, new_status)

    async def async_add_inquiry(self, *args, **kwargs) -> int:
        return await asyncio.to_thread(self.add_inquiry, *args, **kwargs)
