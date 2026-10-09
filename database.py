# -*- coding: utf-8 -*-
"""
Модуль базы данных SQLite для МУП «Ульяновскэлектротранс».
Поддерживает высоконагруженный режим WAL (Write-Ahead Logging),
пул транзакций без утечек соединений, индексирование, кулдауны,
историю заявок, изоляцию кадровых данных и требования 152-ФЗ РФ.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

try:
    import aiosqlite
except ImportError:
    aiosqlite = None

logger = logging.getLogger("UET_DATABASE")


class ResumeDB:
    def __init__(self, db_path: str = "data/resumes.db", test_db_path: str = "data/resumes_test.db"):
        self.db_path = db_path
        self.test_db_path = test_db_path

        # Автоматически создаем папку data/, если её нет
        for p in (self.db_path, self.test_db_path):
            parent = os.path.dirname(p)
            if parent:
                os.makedirs(parent, exist_ok=True)

        self._init_and_migrate_db(is_test=False)
        self._init_and_migrate_db(is_test=True)
    def connection(self, is_test: bool = False) -> sqlite3.Connection:
        """Алиас для вызовов self.connection()"""
        return self._get_connection(is_test=is_test)

    def _get_connection(self, is_test: bool = False) -> sqlite3.Connection:
        """Создание соединения с оптимизациями для многопоточного доступа и WAL"""
        target = self.test_db_path if is_test else self.db_path
        conn = sqlite3.connect(target, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA cache_size=-64000;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        return conn
    def disable_all_hr_notifications(self) -> int:
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE admins SET notify_dm = 0 WHERE role = 'hr'")
            conn.commit()
            return cursor.rowcount
    async def get_async_connection(self, is_test: bool = False):
        """Создание асинхронного соединения через aiosqlite."""
        if aiosqlite is None:
            raise RuntimeError("Пакет aiosqlite не установлен. Установите: pip install aiosqlite")
        target = self.test_db_path if is_test else self.db_path
        conn = await aiosqlite.connect(target, timeout=30.0)
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA busy_timeout=30000;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("PRAGMA cache_size=-64000;")
        await conn.execute("PRAGMA temp_store=MEMORY;")
        return conn

    def _init_and_migrate_db(self, is_test: bool = False):
        """Создание таблиц, миграция структуры и установка высокоскоростных индексов"""
        with self._get_connection(is_test=is_test) as conn:
            cursor = conn.cursor()

            # 1. Таблица соискателей
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS candidates (
                    ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    vacancy TEXT NOT NULL,
                    experience TEXT NOT NULL,
                    status TEXT DEFAULT 'Новая',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    admin_note TEXT DEFAULT '',
                    updated_at TIMESTAMP DEFAULT NULL
                )
                """
            )

            # Миграции полей кандидатов
            cursor.execute("PRAGMA table_info(candidates)")
            cand_cols = [row[1] for row in cursor.fetchall()]
            if "admin_note" not in cand_cols:
                cursor.execute("ALTER TABLE candidates ADD COLUMN admin_note TEXT DEFAULT ''")
            if "updated_at" not in cand_cols:
                cursor.execute("ALTER TABLE candidates ADD COLUMN updated_at TIMESTAMP DEFAULT NULL")
            if "is_test" not in cand_cols:
                cursor.execute("ALTER TABLE candidates ADD COLUMN is_test INTEGER DEFAULT 0")
            if "consent_timestamp" not in cand_cols:
                cursor.execute("ALTER TABLE candidates ADD COLUMN consent_timestamp TEXT DEFAULT ''")

            new_columns = [
                ("birth_date", "TEXT DEFAULT ''"),
                ("city", "TEXT DEFAULT ''"),
                ("driver_license", "TEXT DEFAULT ''"),
                ("education", "TEXT DEFAULT ''"),
                ("relocation", "TEXT DEFAULT ''"),
                ("dormitory", "TEXT DEFAULT ''"),
                ("shift_work", "TEXT DEFAULT ''"),
                ("medical_restrictions", "TEXT DEFAULT ''"),
                ("criminal_record", "TEXT DEFAULT ''"),
                ("source", "TEXT DEFAULT ''"),
                ("extra_info", "TEXT DEFAULT ''"),
                ("raw_data_json", "TEXT DEFAULT '{}'"),
            ]
            for col_name, col_def in new_columns:
                if col_name not in cand_cols:
                    cursor.execute(f"ALTER TABLE candidates ADD COLUMN {col_name} {col_def}")

            # Если инициализируем тестовую базу — стартуем нумерацию с 900000
            if is_test:
                cursor.execute("INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES ('candidates', 900000)")
                cursor.execute("INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES ('inquiries', 900000)")
                conn.commit()
            # Сдвиг номеров для тестовой базы на 900000+
            if is_test:
             cursor.execute("DELETE FROM sqlite_sequence WHERE name = 'candidates'")
             cursor.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('candidates', 900000)")
             cursor.execute("DELETE FROM sqlite_sequence WHERE name = 'inquiries'")
             cursor.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('inquiries', 900000)")
             conn.commit()
            # 2. Таблица администраторов
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    role TEXT DEFAULT 'hr',
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notify_dm INTEGER DEFAULT 1
                )
                """
            )

            # 3. Черный список (защита от спама)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS blacklist (
                    user_id TEXT PRIMARY KEY,
                    reason TEXT DEFAULT 'Спам',
                    blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # 4. Служебные настройки
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )

            # 5. Таблица обращений соискателей (вопросы по анкете)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS inquiries (
                    inquiry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER DEFAULT NULL,
                    platform TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    full_name TEXT DEFAULT '',
                    phone TEXT DEFAULT '',
                    vacancy TEXT DEFAULT '',
                    question_text TEXT NOT NULL,
                    status TEXT DEFAULT 'open',
                    admin_reply TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    closed_at TIMESTAMP DEFAULT NULL
                )
                """
            )

            # Миграции полей обращений
            cursor.execute("PRAGMA table_info(inquiries)")
            inq_cols = [row[1] for row in cursor.fetchall()]
            if "is_test" not in inq_cols:
                cursor.execute("ALTER TABLE inquiries ADD COLUMN is_test INTEGER DEFAULT 0")
            if "consent_timestamp" not in inq_cols:
                cursor.execute("ALTER TABLE inquiries ADD COLUMN consent_timestamp TEXT DEFAULT ''")

            # 6. Таблица кулдауна обращений (антифлуд)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS user_cooldowns (
                    user_id TEXT PRIMARY KEY,
                    last_inquiry_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            
            # 7. Таблица активных прямых диалогов (живой мост Кадровик <-> Кандидат)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS active_dialogs (
                    user_id TEXT PRIMARY KEY,
                    operator_id INTEGER NOT NULL,
                    ticket_id INTEGER DEFAULT NULL,
                    full_name TEXT DEFAULT '',
                    platform TEXT DEFAULT 'tg',
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            cursor.execute("PRAGMA table_info(active_dialogs)")
            dlg_cols = [row[1] for row in cursor.fetchall()]
            if "platform" not in dlg_cols:
                cursor.execute("ALTER TABLE active_dialogs ADD COLUMN platform TEXT DEFAULT 'tg'")

            # 8. Таблица согласий на обработку персональных данных (152-ФЗ)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS user_consents (
                    user_id TEXT PRIMARY KEY,
                    consent_timestamp TEXT NOT NULL
                )
                """
            )

            # Индексы для ускорения поиска на больших объемах
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cand_user_plat ON candidates(user_id, platform)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cand_status ON candidates(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_inq_user ON inquiries(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_inq_status ON inquiries(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_dialog_op ON active_dialogs(operator_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_admins_role_dm ON admins(role, notify_dm)")

            # 9. Таблица персистентных FSM состояний (aiogram 3)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS fsm_storage (
                    storage_key TEXT PRIMARY KEY,
                    state TEXT,
                    data TEXT DEFAULT "{}",
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # 10. Таблица сессий внешних мессенджеров (VK / MAX)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS external_sessions (
                    platform TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    data TEXT DEFAULT "{}",
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (platform, user_id)
                )
                """
            )
            
            conn.commit()
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

    # =========================================================================
    # 1. УПРАВЛЕНИЕ АНКЕТАМИ СОИСКАТЕЛЕЙ
    # =========================================================================

    def add_candidate(
        self, platform: str, user_id: str, full_name: str, phone: str, vacancy: str, experience: str,
        is_test: bool = False, consent_timestamp: Optional[str] = None,
        birth_date: str = "", city: str = "", driver_license: str = "", education: str = "",
        relocation: str = "", dormitory: str = "", shift_work: str = "",
        medical_restrictions: str = "", criminal_record: str = "", source: str = "", extra_info: str = "",
        raw_data_json: str = "{}"
    ) -> int:
        # Автоматически проверяем переключатель активной базы
        target_setting = self.get_setting("active_db_target", "resumes.db")
        use_test = is_test or (target_setting == "resumes_test.db")

        with self._get_connection(is_test=use_test) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO candidates (
                    platform, user_id, full_name, phone, vacancy, experience, is_test, consent_timestamp,
                    birth_date, city, driver_license, education, relocation, dormitory, shift_work,
                    medical_restrictions, criminal_record, source, extra_info, raw_data_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    platform, str(user_id), full_name, phone, vacancy, experience, 1 if use_test else 0, consent_timestamp or "",
                    birth_date, city, driver_license, education, relocation, dormitory, shift_work,
                    medical_restrictions, criminal_record, source, extra_info, raw_data_json
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
        with self.connection() as conn:
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
                return int(t_id)
            return None

    def get_candidate_dict_by_user(self, user_id: str, platform: str = "tg") -> Optional[Dict[str, Any]]:
        """Получение словаря данных анкеты для команды выгрузки /mydata (ст. 14 152-ФЗ)."""
        with self.connection() as conn:
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

        # Номера #900000+ по регламенту лежат в тестовой базе
        is_test = (ticket_id >= 900000)
        
        # 1. Запрос в целевую базу
        with self._get_connection(is_test=is_test) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT ticket_id, platform, user_id, full_name, phone, vacancy, experience, status, admin_note, created_at,
                       birth_date, city, driver_license, education, relocation, dormitory, shift_work,
                       medical_restrictions, criminal_record, source, extra_info, consent_timestamp, raw_data_json
                FROM candidates WHERE ticket_id = ?
                """,
                (ticket_id,)
            )
            row = cursor.fetchone()
            if row:
                return row

        # 2. Резервный поиск в альтернативной базе
        with self._get_connection(is_test=not is_test) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT ticket_id, platform, user_id, full_name, phone, vacancy, experience, status, admin_note, created_at,
                       birth_date, city, driver_license, education, relocation, dormitory, shift_work,
                       medical_restrictions, criminal_record, source, extra_info, consent_timestamp, raw_data_json
                FROM candidates WHERE ticket_id = ?
                """,
                (ticket_id,)
            )
            return cursor.fetchone()

    def get_candidate_by_user_id(self, user_id: str, platform: str = "tg", is_test: Optional[bool] = None) -> Optional[Tuple]:
        """Поиск последней анкеты пользователя с бесшовным поиском в активной и резервной базах."""
        if is_test is True or is_test is False:
            with self._get_connection(is_test=is_test) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT ticket_id, platform, user_id, full_name, phone, vacancy, experience, status, admin_note, created_at,
                           birth_date, city, driver_license, education, relocation, dormitory, shift_work,
                           medical_restrictions, criminal_record, source, extra_info, consent_timestamp, raw_data_json
                    FROM candidates WHERE user_id = ? AND platform = ? ORDER BY ticket_id DESC LIMIT 1
                    """,
                    (str(user_id), platform),
                )
                return cursor.fetchone()

        # Если режим не указан — опрашиваем сначала активную целевую базу
        target = self.get_setting("active_db_target", "resumes.db")
        prefer_test = (target == "resumes_test.db")
        
        row = self.get_candidate_by_user_id(user_id, platform=platform, is_test=prefer_test)
        if not row:
            # Fallback во вторую базу (если анкета была подана в другом режиме)
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
        with self.connection() as conn:
            cursor = conn.cursor()
            # 1. Проверяем наличие активной анкеты
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
                return False, "unprocessed", {
                    "ticket_id": t_id,
                    "status": st,
                    "created_at": cr,
                    "full_name": fn,
                    "vacancy": vc,
                }

            # 2. Проверяем период кулдауна 90 дней после отказа
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
                    return False, "rejected_cooldown", {
                        "ticket_id": t_id,
                        "status": st,
                        "refuse_date": refuse_dt.strftime("%d.%m.%Y"),
                        "available_date": available_dt.strftime("%d.%m.%Y"),
                        "days_left": days_left,
                        "full_name": fn,
                        "vacancy": vc,
                    }

            return True, "ok", None

    def delete_candidate(self, ticket_id: int) -> bool:
        """Физическое удаление анкеты кандидата и связанных диалогов."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM candidates WHERE ticket_id = ?", (ticket_id,))
            deleted = cursor.rowcount > 0
            cursor.execute("DELETE FROM active_dialogs WHERE ticket_id = ?", (ticket_id,))
            cursor.execute("DELETE FROM inquiries WHERE ticket_id = ?", (ticket_id,))
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
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM candidates WHERE user_id = ? AND platform = ?", (uid, platform))
            deleted = cursor.rowcount > 0
            cursor.execute("DELETE FROM active_dialogs WHERE user_id = ?", (uid,))
            cursor.execute("DELETE FROM inquiries WHERE user_id = ? AND platform = ?", (uid, platform))
            cursor.execute("DELETE FROM user_cooldowns WHERE user_id = ?", (uid,))
            return deleted

    def reset_all_test_data(self) -> Tuple[int, int]:
        """Очистка всех тестовых анкет и обращений из базы данных."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM candidates 
                WHERE is_test = 1 OR status LIKE '%Тест%' 
                   OR full_name LIKE '%Тест%' OR full_name LIKE '%тест%' 
                   OR ticket_id >= 900000
                """
            )
            cand_cnt = cursor.rowcount
            cursor.execute(
                """
                DELETE FROM inquiries 
                WHERE is_test = 1 OR question_text LIKE '%Тест%' 
                   OR full_name LIKE '%Тест%' OR full_name LIKE '%тест%'
                """
            )
            inq_cnt = cursor.rowcount
            cursor.execute("DELETE FROM active_dialogs WHERE full_name LIKE '%Тест%' OR full_name LIKE '%тест%'")
            return cand_cnt, inq_cnt

    def purge_all_candidates_for_debug(self) -> Tuple[int, int]:
        """Полная очистка всех анкет (только для среды разработки/отладки)."""
        with self.connection() as conn:
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
        with self.connection() as conn:
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
        """
        Безопасное получение списка анкет со 100% параметризацией запроса (защита от SQLi).
        """
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
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            return cursor.fetchall()

    def update_status(self, ticket_id: int, new_status: str):
        is_test = (ticket_id >= 900000)
        with self._get_connection(is_test=is_test) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE candidates SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE ticket_id = ?", (new_status, ticket_id))
            conn.commit()

    def update_admin_note(self, ticket_id: int, note: str):
        is_test = (ticket_id >= 900000)
        with self._get_connection(is_test=is_test) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE candidates SET admin_note = ?, updated_at = CURRENT_TIMESTAMP WHERE ticket_id = ?", (note, ticket_id))
            conn.commit()
    def get_statistics(self) -> Dict[str, int]:
        """Агрегированный подсчёт кадровой воронки предприятия."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM candidates")
            total = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM candidates WHERE status = 'Новая'")
            new_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM candidates WHERE status = 'В работе'")
            in_progress_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM candidates WHERE status LIKE 'Приглашен%'")
            invited_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM candidates WHERE status LIKE 'Отказ%'")
            rejected_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM candidates WHERE status = 'Архив'")
            archive_count = cursor.fetchone()[0]

            return {
                "total": total,
                "new": new_count,
                "in_progress": in_progress_count,
                "invited": invited_count,
                "rejected": rejected_count,
                "archive": archive_count,
            }

    # =========================================================================
    # 2. СОГЛАСИЯ И ПЕРСОНАЛЬНЫЕ ДАННЫЕ (152-ФЗ РФ)
    # =========================================================================

    def get_user_consent(self, user_id: Any) -> Optional[str]:
        """Возвращает дату и время зафиксированного согласия 152-ФЗ."""
        uid = str(user_id)
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT consent_timestamp FROM user_consents WHERE user_id = ?", (uid,))
            row = cursor.fetchone()
            if row and row[0]:
                return row[0]

            # Резервный поиск по истории анкет
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
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO user_consents (user_id, consent_timestamp) VALUES (?, ?)",
                (str(user_id), ts),
            )
        return ts

    def revoke_user_consent(self, user_id: Any) -> bool:
        """Отзыв согласия и удаление отметки по ст. 21 152-ФЗ."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_consents WHERE user_id = ?", (str(user_id),))
            return cursor.rowcount > 0

    def log_pdn_destruction(
        self, candidate_id: int, user_id: str, platform: str, reason: str, act_number: str = ""
    ) -> int:
        """Фиксация уничтожения ПДн в электронном журнале (Приказ Роскомнадзора № 179)."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        act_num = act_number or f"{candidate_id}-УПД"
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO destruction_logs (candidate_id, user_id, platform, reason, destroyed_at, act_number)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (candidate_id, str(user_id), platform, reason, now_str, act_num),
            )
            return int(cursor.lastrowid)

    def get_destruction_logs(self, limit: int = 50) -> List[Tuple[Any, ...]]:
        """Возвращает журнал уничтожения ПДн для проверок Роскомнадзора."""
        with self.connection() as conn:
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

    def cleanup_expired_candidates(self, max_days: int = 180) -> List[int]:
        """
        Автоматическое уничтожение анкет с истёкшим сроком хранения (6 месяцев).
        Требование ч. 7 ст. 5 и ст. 21 Федерального закона № 152-ФЗ РФ.
        """
        cutoff = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d %H:%M:%S")
        purged_ids: List[int] = []

        with self.connection() as conn:
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
            self.checkpoint_and_optimize()
        return purged_ids

    # =========================================================================
    # 3. УПРАВЛЕНИЕ АДМИНИСТРАТОРАМИ И УВЕДОМЛЕНИЯМИ
    # =========================================================================

    def add_admin(self, user_id: int, role: str = "hr") -> bool:
        """Добавление или обновление прав доступа сотрудника."""
        with self.connection() as conn:
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

    def remove_admin(self, user_id: int) -> bool:
        """Отзыв прав доступа сотрудника."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            return cursor.rowcount > 0

    def get_all_admins(self) -> List[Tuple[int, str]]:
        """Получение списка всех сотрудников с назначенными ролями."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, role FROM admins ORDER BY added_at ASC")
            return cursor.fetchall()

    def get_admin_role(self, user_id: int) -> Optional[str]:
        """Получение роли конкретного пользователя."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT role FROM admins WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return str(row[0]) if row else None

    def get_admin_notify_status(self, user_id: int) -> bool:
        """Проверка статуса дублирования анкет в личные сообщения."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT notify_dm FROM admins WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return bool(row[0]) if row else True

    def toggle_admin_notify(self, user_id: int) -> bool:
        """Переключение персонального статуса ЛС-уведомлений."""
        current = self.get_admin_notify_status(user_id)
        new_status = 0 if current else 1
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE admins SET notify_dm = ? WHERE user_id = ?", (new_status, user_id))
        return bool(new_status)

    def set_all_hr_notify_dm(self, notify_status: int = 1) -> int:
        """Пакетное переключение доставки в ЛС для всех кадровиков."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE admins SET notify_dm = ? WHERE role = 'hr'", (notify_status,))
            return cursor.rowcount

    def get_admins_with_dm_enabled(self, role: str = "hr") -> List[int]:
        """Получение списка ID сотрудников с активными уведомлениями в ЛС."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM admins WHERE notify_dm = 1 AND role = ?", (role,))
            return [row[0] for row in cursor.fetchall()]

    def get_hr_admins_with_dm_enabled(self) -> List[int]:
        return self.get_admins_with_dm_enabled(role="hr")

    # =========================================================================
    # 4. АНТИФЛУД И ОБРАЩЕНИЯ СОИСКАТЕЛЕЙ
    # =========================================================================

    def check_inquiry_cooldown(self, user_id: str, cooldown_seconds: int = 1200) -> Tuple[bool, int]:
        """Проверка тайм-аута антифлуда между вопросами соискателя."""
        with self.connection() as conn:
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
        consent_timestamp: Optional[str] = None
    ) -> int:
        target_setting = self.get_setting("active_db_target", "resumes.db")
        use_test = is_test or (target_setting == "resumes_test.db")

        with self._get_connection(is_test=use_test) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO inquiries (
                    ticket_id, platform, user_id, full_name, phone, vacancy, question_text, is_test, consent_timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket_id, platform, str(user_id), full_name, phone, vacancy, question_text,
                    1 if use_test else 0, consent_timestamp or ""
                )
            )
            conn.commit()
            return cursor.lastrowid

    def get_inquiry(self, inquiry_id: int) -> Optional[Tuple[Any, ...]]:
        """Получение данных обращения по ID."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT inquiry_id, ticket_id, platform, user_id, full_name, phone, vacancy,
                       question_text, status, admin_reply, created_at, closed_at
                FROM inquiries WHERE inquiry_id = ?
                """,
                (inquiry_id,),
            )
            return cursor.fetchone()

    def reply_inquiry(self, inquiry_id: int, reply_text: str) -> bool:
        """Сохранение ответа кадровика на вопрос соискателя."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE inquiries SET admin_reply = ?, status = 'replied' WHERE inquiry_id = ?",
                (reply_text, inquiry_id),
            )
            return cursor.rowcount > 0

    def close_inquiry(self, inquiry_id: int) -> bool:
        """Закрытие тикета обращения."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE inquiries SET status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE inquiry_id = ?",
                (inquiry_id,),
            )
            return cursor.rowcount > 0

    # =========================================================================
    # 5. ПРЯМОЙ ДИАЛОГ (ЖИВОЙ ЧАТ КАДРОВИК <-> СОИСКАТЕЛЬ)
    # =========================================================================

    def start_direct_dialog(
        self,
        user_id: str,
        operator_id: int,
        ticket_id: Optional[int] = None,
        full_name: str = "",
        platform: str = "tg",
    ) -> bool:
        """Открытие сессии прямого моста между соискателем и кадровой службой."""
        with self.connection() as conn:
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
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, operator_id, ticket_id, full_name, platform FROM active_dialogs WHERE user_id = ?",
                (str(user_id),),
            )
            return cursor.fetchone()

    def get_dialog_by_operator(self, operator_id: int) -> Optional[Tuple[Any, ...]]:
        """Проверка, ведёт ли оператор/чат активный диалог."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, operator_id, ticket_id, full_name, platform FROM active_dialogs WHERE operator_id = ?",
                (operator_id,),
            )
            return cursor.fetchone()

    def end_direct_dialog(self, user_id: Optional[str] = None, operator_id: Optional[int] = None) -> bool:
        """Завершение прямого диалога со стороны пользователя или оператора."""
        with self.connection() as conn:
            cursor = conn.cursor()
            if user_id:
                cursor.execute("DELETE FROM active_dialogs WHERE user_id = ?", (str(user_id),))
            elif operator_id:
                cursor.execute("DELETE FROM active_dialogs WHERE operator_id = ?", (operator_id,))
            return cursor.rowcount > 0

    # =========================================================================
    # 6. БЕЗОПАСНОСТЬ, ЧЕРНЫЙ СПИСОК И НАСТРОЙКИ
    # =========================================================================

    def block_user_and_clean(self, user_id: Any, reason: str = "Спам") -> bool:
        """Внесение в ЧС с каскадным аннулированием анкет, диалогов и обращений."""
        uid_str = str(user_id)
        with self.connection() as conn:
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
        with self.connection() as conn:
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
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM blacklist WHERE user_id = ?", (str(user_id),))
            return cursor.rowcount > 0

    def get_blacklist(self) -> List[Tuple[str, str, str]]:
        """Список всех заблокированных пользователей."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, reason, blocked_at FROM blacklist ORDER BY blocked_at DESC")
            return cursor.fetchall()

    def is_blocked(self, user_id: Any, super_admin_id: Optional[int] = None) -> bool:
        """Проверка нахождения пользователя в чёрном списке."""
        if not user_id:
            return False
        if super_admin_id is not None and str(user_id) == str(super_admin_id):
            return False
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (str(user_id),))
            return cursor.fetchone() is not None

    def check_health(self) -> bool:
        """Проверка целостности и доступности базы данных."""
        try:
            with self.connection() as conn:
                conn.execute("SELECT 1 FROM candidates LIMIT 1")
                conn.execute("SELECT 1 FROM inquiries LIMIT 1")
                return True
        except Exception as e:
            logger.error(f"Сбой проверки базы данных: {e}")
            return False

    def get_setting(self, key: str, default: str = "") -> str:
        """Получение значения настройки из БД."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return str(row[0]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        """Сохранение системной настройки в БД."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO system_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = ?",
                (key, str(value), str(value)),
            )

    def get_all_settings(self) -> Dict[str, str]:
        """Получение всех базовых настроек без динамических переопределений текстов."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM system_settings WHERE key NOT LIKE 'text_override:%'")
            return {row[0]: row[1] for row in cursor.fetchall()}

    def get_all_text_overrides(self) -> Dict[str, str]:
        """Получение всех переопределений текстов из веб-панели."""
        with self.connection() as conn:
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
            with self.connection() as conn:
                try:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
                except sqlite3.OperationalError:
                    pass
                conn.execute("PRAGMA optimize;")
            return True
        except Exception as e:
            logger.error(f"Ошибка оптимизации SQLite: {e}")
            return False

    # =========================================================================
    # 7. РЕЗЕРВНОЕ КОПИРОВАНИЕ И ВОССТАНОВЛЕНИЕ (BACKUPS)
    # =========================================================================

    def backup_database(self, backup_dir: str = "backups") -> str:
        """Создание горячей резервной копии через SQLite Online Backup API."""
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"resumes_backup_{timestamp}.db"
        backup_path = os.path.join(backup_dir, backup_filename)

        counter = 1
        while os.path.exists(backup_path):
            backup_filename = f"resumes_backup_{timestamp}_{counter}.db"
            backup_path = os.path.join(backup_dir, backup_filename)
            counter += 1

        with self.connection() as src_conn:
            with sqlite3.connect(backup_path) as dst_conn:
                src_conn.backup(dst_conn)

        return backup_path

    def list_backups(self, backup_dir: str = "backups") -> List[Dict[str, Any]]:
        """Список всех резервных копий с метаданными размера и даты создания."""
        if not os.path.exists(backup_dir):
            return []
        files = []
        for fn in os.listdir(backup_dir):
            if fn.endswith(".db"):
                fp = os.path.join(backup_dir, fn)
                st = os.stat(fp)
                files.append(
                    {
                        "filename": fn,
                        "path": fp,
                        "size_bytes": st.st_size,
                        "size_kb": round(st.st_size / 1024, 1),
                        "created_at": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
        files.sort(key=lambda x: (x["created_at"], x["filename"]), reverse=True)
        return files

    def restore_database(self, backup_filename: str, backup_dir: str = "backups") -> Tuple[bool, str]:
        """Горячее восстановление базы данных из бэкапа со страховочной копией."""
        clean_name = os.path.basename(backup_filename)
        backup_path = os.path.join(backup_dir, clean_name)
        if not os.path.exists(backup_path):
            return False, f"Файл бэкапа {clean_name} не найден в {backup_dir}."

        pre_restore_path = self.backup_database(backup_dir)
        pre_restore_name = os.path.basename(pre_restore_path)

        try:
            with sqlite3.connect(backup_path) as src_conn:
                with self.connection() as dst_conn:
                    src_conn.backup(dst_conn)
            return True, (
                f"База успешно восстановлена из <code>{clean_name}</code>.\n"
                f"Страховочная копия создана: <code>{pre_restore_name}</code>"
            )
        except Exception as e:
            logger.error(f"Сбой восстановления БД: {e}")
            return False, f"Ошибка восстановления базы: {e}"

    def delete_backup(self, backup_filename: str, backup_dir: str = "backups") -> Tuple[bool, str]:
        """Удаление файла бэкапа."""
        clean_name = os.path.basename(backup_filename)
        backup_path = os.path.join(backup_dir, clean_name)
        if not os.path.exists(backup_path):
            return False, "Файл не найден."
        try:
            os.remove(backup_path)
            return True, f"Файл <code>{clean_name}</code> успешно удален."
        except Exception as e:
            return False, f"Ошибка при удалении: {e}"

    def cleanup_old_backups(self, keep_count: int = 5, backup_dir: str = "backups") -> Tuple[int, List[str]]:
        """Ротация архивов: сохраняет последние `keep_count` копий, старые удаляет."""
        backups = self.list_backups(backup_dir)
        if len(backups) <= keep_count:
            return 0, []
        to_delete = backups[keep_count:]
        deleted_names = []
        for b in to_delete:
            try:
                os.remove(b["path"])
                deleted_names.append(b["filename"])
            except Exception:
                pass
        return len(deleted_names), deleted_names

    # =========================================================================
    # 8. FSM И ПЕРСИСТЕНТНЫЕ СЕССИИ (VK / MAX / TG)
    # =========================================================================

    def set_fsm_state(self, key: str, state: Optional[str]) -> None:
        """Сохранение состояния FSM в постоянное хранилище."""
        with self.connection() as conn:
            cursor = conn.cursor()
            if state is None:
                cursor.execute(
                    "UPDATE fsm_storage SET state = NULL, updated_at = CURRENT_TIMESTAMP WHERE storage_key = ?",
                    (key,),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO fsm_storage (storage_key, state, data, updated_at)
                    VALUES (?, ?, '{}', CURRENT_TIMESTAMP)
                    ON CONFLICT(storage_key) DO UPDATE SET state = ?, updated_at = CURRENT_TIMESTAMP
                    """,
                    (key, state, state),
                )

    def get_fsm_state(self, key: str) -> Optional[str]:
        """Чтение состояния FSM."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT state FROM fsm_storage WHERE storage_key = ?", (key,))
            row = cursor.fetchone()
            return str(row[0]) if row and row[0] is not None else None

    def set_fsm_data(self, key: str, data: Dict[str, Any]) -> None:
        """Сохранение полезной нагрузки FSM."""
        raw_json = json.dumps(data or {}, ensure_ascii=False)
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO fsm_storage (storage_key, state, data, updated_at)
                VALUES (?, NULL, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(storage_key) DO UPDATE SET data = ?, updated_at = CURRENT_TIMESTAMP
                """,
                (key, raw_json, raw_json),
            )

    def get_fsm_data(self, key: str) -> Dict[str, Any]:
        """Чтение полезной нагрузки FSM."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT data FROM fsm_storage WHERE storage_key = ?", (key,))
            row = cursor.fetchone()
            if row and row[0]:
                try:
                    return json.loads(row[0])
                except Exception:
                    return {}
            return {}

    def set_external_session(self, platform: str, user_id: str, data: Dict[str, Any]) -> None:
        """Сохранение сессии внешнего мессенджера (VK/MAX)."""
        raw_json = json.dumps(data or {}, ensure_ascii=False)
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO external_sessions (platform, user_id, data, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(platform, user_id) DO UPDATE SET data = ?, updated_at = CURRENT_TIMESTAMP
                """,
                (platform, str(user_id), raw_json, raw_json),
            )

    def delete_external_session(self, platform: str, user_id: str) -> None:
        """Удаление завершённой внешней сессии."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM external_sessions WHERE platform = ? AND user_id = ?",
                (platform, str(user_id)),
            )

    def get_all_external_sessions(self) -> List[Tuple[str, str, Dict[str, Any]]]:
        """Загрузка всех незавершённых сессий внешних мессенджеров при старте."""
        sessions: List[Tuple[str, str, Dict[str, Any]]] = []
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT platform, user_id, data FROM external_sessions")
            for plat, uid, raw_json in cursor.fetchall():
                try:
                    d = json.loads(raw_json) if raw_json else {}
                except Exception:
                    d = {}
                sessions.append((plat, uid, d))
        return sessions

    # =========================================================================
    # 9. АСИНХРОННЫЕ ОБЕРТКИ ДЛЯ НЕБЛОКИРУЮЩЕГО ВЫЗОВА В ХЭНДЛЕРАХ
    # =========================================================================

    async def async_add_candidate(self, *args, **kwargs) -> int:
        return await asyncio.to_thread(self.add_candidate, *args, **kwargs)

    async def async_get_candidate(self, ticket_id: int) -> Optional[Tuple[Any, ...]]:
        return await asyncio.to_thread(self.get_candidate, ticket_id)

    async def async_get_candidate_by_user_id(
        self, user_id: str, platform: str = "tg"
    ) -> Optional[Tuple[Any, ...]]:
        return await asyncio.to_thread(self.get_candidate_by_user_id, user_id, platform)

    async def async_check_candidate_can_apply(
        self, user_id: str, platform: str = "tg"
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return await asyncio.to_thread(self.check_candidate_can_apply, user_id, platform)

    async def async_get_recent_candidates(self, *args, **kwargs) -> List[Tuple[Any, ...]]:
        return await asyncio.to_thread(self.get_recent_candidates, *args, **kwargs)

    async def async_get_statistics(self) -> Dict[str, int]:
        return await asyncio.to_thread(self.get_statistics)

    async def async_delete_candidate_152fz(
        self, ticket_id: int
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        return await asyncio.to_thread(self.delete_candidate_152fz, ticket_id)

    async def async_update_status(self, ticket_id: int, new_status: str) -> None:
        await asyncio.to_thread(self.update_status, ticket_id, new_status)

    async def async_add_inquiry(self, *args, **kwargs) -> int:
        return await asyncio.to_thread(self.add_inquiry, *args, **kwargs)