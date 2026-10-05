# -*- coding: utf-8 -*-
"""
Модуль базы данных SQLite для МУП «Ульяновскэлектротранс»
Поддерживает высоконагруженный режим WAL (Write-Ahead Logging),
индексирование, кулдауны, историю заявок и изоляцию кадровых данных.
"""

import os
import asyncio
import json
import sqlite3
import logging
try:
    import aiosqlite
except ImportError:
    aiosqlite = None
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

class ResumeDB:
    def __init__(self, db_path: str = "resumes.db"):
        if (db_path == "resumes.db" or not db_path) and os.getenv("DB_PATH"):
            db_path = os.getenv("DB_PATH")
        self.db_path = db_path
        self._init_and_migrate_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Создание соединения с оптимизациями для многопоточного доступа и WAL"""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.OperationalError:
            conn.execute("PRAGMA journal_mode=DELETE;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA cache_size=-2000;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        return conn

    async def get_async_connection(self):
        """Создание асинхронного соединения через aiosqlite."""
        if aiosqlite is None:
            raise RuntimeError("Пакет aiosqlite не установлен. Установите: pip install aiosqlite")
        conn = await aiosqlite.connect(self.db_path, timeout=30.0)
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA busy_timeout=30000;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("PRAGMA cache_size=-2000;")
        await conn.execute("PRAGMA temp_store=MEMORY;")
        return conn

    def _init_and_migrate_db(self):
        """Создание таблиц, миграция структуры и установка высокоскоростных индексов"""
        with self._get_connection() as conn:
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
        if not ts_str:
            return datetime.now()
        clean_str = str(ts_str).replace("T", " ").split(".")[0]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(clean_str, fmt)
            except ValueError:
                continue
        return datetime.now()

    # ==================== УПРАВЛЕНИЕ АНКЕТАМИ СОИСКАТЕЛЕЙ ====================
    def add_candidate(
        self, platform: str, user_id: str, full_name: str, phone: str, vacancy: str, experience: str,
        is_test: bool = False, consent_timestamp: Optional[str] = None,
        birth_date: str = "", city: str = "", driver_license: str = "", education: str = "",
        relocation: str = "", dormitory: str = "", shift_work: str = "",
        medical_restrictions: str = "", criminal_record: str = "", source: str = "", extra_info: str = "",
        raw_data_json: str = "{}"
    ) -> int:
        with self._get_connection() as conn:
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
                    platform, str(user_id), full_name, phone, vacancy, experience, 1 if is_test else 0, consent_timestamp or "",
                    birth_date, city, driver_license, education, relocation, dormitory, shift_work,
                    medical_restrictions, criminal_record, source, extra_info, raw_data_json
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def delete_candidate_by_user(self, user_id: str, platform: str = "tg") -> Optional[int]:
        """Удаление анкеты пользователя при отзыве согласия на обработку ПДн (ст. 21 152-ФЗ)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT ticket_id FROM candidates WHERE user_id = ? AND platform = ? ORDER BY ticket_id DESC LIMIT 1",
                (str(user_id), platform)
            )
            row = cursor.fetchone()
            if row:
                t_id = row[0]
                cursor.execute("DELETE FROM candidates WHERE ticket_id = ?", (t_id,))
                conn.commit()
                return t_id
            return None

    def get_candidate_dict_by_user(self, user_id: str, platform: str = "tg") -> Optional[Dict[str, Any]]:
        """Получение словаря данных анкеты для команды /mydata (ст. 14 152-ФЗ)."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM candidates WHERE user_id = ? AND platform = ? ORDER BY ticket_id DESC LIMIT 1",
                (str(user_id), platform)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    async def async_add_candidate(self, *args, **kwargs) -> int:
        """Неблокирующее асинхронное добавление анкеты."""
        return await asyncio.to_thread(self.add_candidate, *args, **kwargs)

    async def async_get_candidate(self, ticket_id: int) -> Optional[Tuple]:
        """Неблокирующее получение анкеты по ID."""
        return await asyncio.to_thread(self.get_candidate, ticket_id)

    async def async_get_candidate_by_user_id(self, user_id: str, platform: str = "tg") -> Optional[Tuple]:
        """Неблокирующее получение анкеты по user_id."""
        return await asyncio.to_thread(self.get_candidate_by_user_id, user_id, platform)

    async def async_check_candidate_can_apply(self, user_id: str, platform: str = "tg"):
        """Неблокирующая проверка права подачи анкеты."""
        return await asyncio.to_thread(self.check_candidate_can_apply, user_id, platform)

    async def async_get_recent_candidates(self, *args, **kwargs) -> List[Tuple]:
        """Неблокирующее получение списка последних анкет."""
        return await asyncio.to_thread(self.get_recent_candidates, *args, **kwargs)

    async def async_get_statistics(self) -> Dict[str, int]:
        """Неблокирующий расчет статистики отдела кадров."""
        return await asyncio.to_thread(self.get_statistics)

    async def async_delete_candidate_152fz(self, ticket_id: int) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Неблокирующее удаление анкеты по 152-ФЗ."""
        return await asyncio.to_thread(self.delete_candidate_152fz, ticket_id)

    async def async_update_status(self, ticket_id: int, new_status: str):
        """Неблокирующее обновление статуса анкеты."""
        return await asyncio.to_thread(self.update_status, ticket_id, new_status)

    def get_candidate(self, ticket_id: int) -> Optional[Tuple]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT ticket_id, platform, user_id, full_name, phone, vacancy, experience, status, admin_note, created_at,
                       birth_date, city, driver_license, education, relocation, dormitory, shift_work,
                       medical_restrictions, criminal_record, source, extra_info, consent_timestamp, raw_data_json
                FROM candidates WHERE ticket_id = ?
                """,
                (ticket_id,),
            )
            return cursor.fetchone()

    def get_candidate_by_user_id(self, user_id: str, platform: str = "tg") -> Optional[Tuple]:
        with self._get_connection() as conn:
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

    def check_candidate_can_apply(self, user_id: str, platform: str = "tg") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        uid_str = str(user_id)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. Проверяем, есть ли активная анкета на рассмотрении (Новая, В работе, Приглашен)
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

            # 2. Проверяем, есть ли недавний отказ за последние 90 дней (3 месяца)
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
                diff_days = (now - refuse_dt).days
                cooldown_days = 90
                if diff_days < cooldown_days:
                    available_dt = refuse_dt + timedelta(days=cooldown_days)
                    days_left = max(1, (available_dt - now).days + 1)
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
        """Физическое и полное удаление анкеты кандидата и всех связанных данных."""
        deleted = False
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM candidates WHERE ticket_id = ?", (ticket_id,))
            if cursor.rowcount > 0:
                deleted = True
            cursor.execute("DELETE FROM active_dialogs WHERE ticket_id = ?", (ticket_id,))
            cursor.execute("DELETE FROM inquiries WHERE ticket_id = ?", (ticket_id,))
            conn.commit()

        # Также подчищаем из старой тестовой базы если она есть на диске
        test_db_path = self.db_path.replace("resumes.db", "resumes_test.db")
        if os.path.exists(test_db_path):
            try:
                with sqlite3.connect(test_db_path) as t_conn:
                    t_cur = t_conn.cursor()
                    t_cur.execute("DELETE FROM candidates WHERE ticket_id = ?", (ticket_id,))
                    if t_cur.rowcount > 0:
                        deleted = True
                    t_cur.execute("DELETE FROM active_dialogs WHERE ticket_id = ?", (ticket_id,))
                    t_cur.execute("DELETE FROM inquiries WHERE ticket_id = ?", (ticket_id,))
                    t_conn.commit()
            except Exception:
                pass

        return deleted

    def delete_candidate_152fz(self, ticket_id: int) -> Tuple[bool, Optional[Dict[str, Any]]]:
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
        """Полное удаление анкет пользователя для чистого тестирования с нуля."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM candidates WHERE user_id = ? AND platform = ?", (str(user_id), platform))
            deleted = cursor.rowcount > 0
            cursor.execute("DELETE FROM active_dialogs WHERE user_id = ?", (str(user_id),))
            cursor.execute("DELETE FROM inquiries WHERE user_id = ? AND platform = ?", (str(user_id), platform))
            cursor.execute("DELETE FROM user_cooldowns WHERE user_id = ?", (str(user_id),))
            conn.commit()

        test_db_path = self.db_path.replace("resumes.db", "resumes_test.db")
        if os.path.exists(test_db_path):
            try:
                with sqlite3.connect(test_db_path) as t_conn:
                    t_cur = t_conn.cursor()
                    t_cur.execute("DELETE FROM candidates WHERE user_id = ? AND platform = ?", (str(user_id), platform))
                    t_cur.execute("DELETE FROM active_dialogs WHERE user_id = ?", (str(user_id),))
                    t_cur.execute("DELETE FROM inquiries WHERE user_id = ? AND platform = ?", (str(user_id), platform))
                    t_cur.execute("DELETE FROM user_cooldowns WHERE user_id = ?", (str(user_id),))
                    t_conn.commit()
            except Exception:
                pass

        return deleted

    def reset_all_test_data(self) -> Tuple[int, int]:
        """Полная очистка тестовых анкет и обращений из базы данных."""
        cand_cnt = 0
        inq_cnt = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM candidates WHERE is_test = 1 OR status LIKE '%Тест%' OR full_name LIKE '%Тест%' OR full_name LIKE '%тест%' OR ticket_id >= 900000")
            cand_cnt = cursor.rowcount
            cursor.execute("DELETE FROM inquiries WHERE is_test = 1 OR question_text LIKE '%Тест%' OR full_name LIKE '%Тест%' OR full_name LIKE '%тест%'")
            inq_cnt = cursor.rowcount
            cursor.execute("DELETE FROM active_dialogs WHERE full_name LIKE '%Тест%' OR full_name LIKE '%тест%'")
            conn.commit()

        test_db_path = self.db_path.replace("resumes.db", "resumes_test.db")
        if os.path.exists(test_db_path):
            try:
                with sqlite3.connect(test_db_path) as t_conn:
                    t_cur = t_conn.cursor()
                    t_cur.execute("DELETE FROM candidates")
                    cand_cnt += t_cur.rowcount
                    t_cur.execute("DELETE FROM inquiries")
                    inq_cnt += t_cur.rowcount
                    t_cur.execute("DELETE FROM active_dialogs")
                    t_conn.commit()
            except Exception:
                pass

        return cand_cnt, inq_cnt

    def purge_all_candidates_for_debug(self) -> Tuple[int, int]:
        """Полная очистка ВСЕХ анкет из базы данных (для отладки)."""
        cand_cnt = 0
        inq_cnt = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM candidates")
            cand_cnt = cursor.rowcount
            cursor.execute("DELETE FROM inquiries")
            inq_cnt = cursor.rowcount
            cursor.execute("DELETE FROM active_dialogs")
            cursor.execute("DELETE FROM user_cooldowns")
            cursor.execute("UPDATE sqlite_sequence SET seq = 0 WHERE name IN ('candidates', 'inquiries')")
            conn.commit()

        test_db_path = self.db_path.replace("resumes.db", "resumes_test.db")
        if os.path.exists(test_db_path):
            try:
                with sqlite3.connect(test_db_path) as t_conn:
                    t_cur = t_conn.cursor()
                    t_cur.execute("DELETE FROM candidates")
                    t_cur.execute("DELETE FROM inquiries")
                    t_cur.execute("DELETE FROM active_dialogs")
                    t_conn.commit()
            except Exception:
                pass

        return cand_cnt, inq_cnt

    
    def get_all_candidates_for_export(self) -> List[Tuple]:
        """Получение всех анкет для выгрузки в CSV/Excel."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT ticket_id, platform, user_id, full_name, phone, vacancy, experience, status, admin_note, created_at
                FROM candidates
                ORDER BY ticket_id DESC
                """
            )
            return cursor.fetchall()

    def get_recent_candidates(self, limit: int = 10, filter_status: Optional[str] = None, only_new: bool = False, is_archive: bool = False) -> List[Tuple]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if only_new:
                filter_clause = "status = 'Новая'"
            elif is_archive or filter_status == "Архив":
                filter_clause = "status = 'Архив'"
            elif filter_status:
                filter_clause = f"status = '{filter_status}'"
            else:
                # Все активные резюме (исключаем архивные, чтобы не захламлять рабочую панель)
                filter_clause = "status != 'Архив'"
            query = f"SELECT ticket_id, full_name, vacancy, status, created_at, platform FROM candidates WHERE {filter_clause} ORDER BY ticket_id DESC LIMIT ?"
            cursor.execute(query, (limit,))
            return cursor.fetchall()

    def update_admin_note(self, ticket_id: int, note: str):
        """Обновление служебной заметки кадровика по анкете."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE candidates SET admin_note = ?, updated_at = CURRENT_TIMESTAMP WHERE ticket_id = ?",
                (note, ticket_id),
            )
            conn.commit()

    def update_status(self, ticket_id: int, new_status: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE candidates SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE ticket_id = ?",
                (new_status, ticket_id),
            )
            conn.commit()

    def get_statistics(self) -> Dict[str, int]:
        with self._get_connection() as conn:
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

    # ==================== СОГЛАСИЯ 152-ФЗ ====================
    def get_user_consent(self, user_id: Any) -> Optional[str]:
        """Возвращает timestamp зафиксированного согласия 152-ФЗ или None."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT consent_timestamp FROM user_consents WHERE user_id = ?", (str(user_id),))
            row = cursor.fetchone()
            if row and row[0]:
                return row[0]
            cursor.execute("SELECT consent_timestamp FROM candidates WHERE user_id = ? AND consent_timestamp != '' ORDER BY ticket_id DESC LIMIT 1", (str(user_id),))
            row2 = cursor.fetchone()
            if row2 and row2[0]:
                cursor.execute("INSERT OR REPLACE INTO user_consents (user_id, consent_timestamp) VALUES (?, ?)", (str(user_id), row2[0]))
                conn.commit()
                return row2[0]
            cursor.execute("SELECT consent_timestamp FROM inquiries WHERE user_id = ? AND consent_timestamp != '' ORDER BY inquiry_id DESC LIMIT 1", (str(user_id),))
            row3 = cursor.fetchone()
            if row3 and row3[0]:
                cursor.execute("INSERT OR REPLACE INTO user_consents (user_id, consent_timestamp) VALUES (?, ?)", (str(user_id), row3[0]))
                conn.commit()
                return row3[0]
            return None

    def set_user_consent(self, user_id: Any, timestamp: Optional[str] = None) -> str:
        """Сохраняет согласие пользователя 152-ФЗ в базе данных."""
        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO user_consents (user_id, consent_timestamp) VALUES (?, ?)", (str(user_id), ts))
            conn.commit()
        return ts

    def revoke_user_consent(self, user_id: Any) -> bool:
        """Отзывает согласие при полном удалении данных по ст. 21 152-ФЗ."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_consents WHERE user_id = ?", (str(user_id),))
            conn.commit()
            return cursor.rowcount > 0

    # ==================== УПРАВЛЕНИЕ АДМИНИСТРАТОРАМИ ====================
    def add_admin(self, user_id: int, role: str = 'hr') -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("INSERT INTO admins (user_id, role, notify_dm) VALUES (?, ?, 1) ON CONFLICT(user_id) DO UPDATE SET role = ?", (user_id, role, role))
                conn.commit()
                return True
            except Exception:
                return False

    def remove_admin(self, user_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            conn.commit()
            return cursor.rowcount > 0

    def get_all_admins(self) -> List[Tuple[int, str]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, role FROM admins ORDER BY added_at ASC")
            return cursor.fetchall()

    def get_admin_notify_status(self, user_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT notify_dm FROM admins WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return bool(row[0]) if row else True

    def toggle_admin_notify(self, user_id: int) -> bool:
        current = self.get_admin_notify_status(user_id)
        new_status = 0 if current else 1
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE admins SET notify_dm = ? WHERE user_id = ?", (new_status, user_id))
            conn.commit()
        return bool(new_status)

    def get_admins_with_dm_enabled(self, role: str = 'hr') -> List[int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM admins WHERE notify_dm = 1 AND role = ?", (role,))
            return [row[0] for row in cursor.fetchall()]

    def get_hr_admins_with_dm_enabled(self) -> List[int]:
        return self.get_admins_with_dm_enabled(role='hr')

    # ==================== АНТИФЛУД И ОБРАЩЕНИЯ СОИСКАТЕЛЕЙ ====================
    def check_inquiry_cooldown(self, user_id: str, cooldown_seconds: int = 1200) -> Tuple[bool, int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT (strftime('%s', 'now') - strftime('%s', last_inquiry_at))
                FROM user_cooldowns WHERE user_id = ?
                """,
                (str(user_id),)
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
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO inquiries (ticket_id, platform, user_id, full_name, phone, vacancy, question_text, status, is_test, consent_timestamp, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, CURRENT_TIMESTAMP)
                """,
                (ticket_id, platform, str(user_id), full_name, phone, vacancy, question_text, 1 if is_test else 0, consent_timestamp or "")
            )
            inquiry_id = cursor.lastrowid
            cursor.execute(
                """
                INSERT INTO user_cooldowns (user_id, last_inquiry_at)
                VALUES (?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET last_inquiry_at = CURRENT_TIMESTAMP
                """,
                (str(user_id),)
            )
            conn.commit()
            return inquiry_id

    async def async_add_inquiry(self, *args, **kwargs) -> int:
        """Неблокирующее асинхронное добавление вопроса."""
        return await asyncio.to_thread(self.add_inquiry, *args, **kwargs)

    def get_inquiry(self, inquiry_id: int) -> Optional[Tuple]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT inquiry_id, ticket_id, platform, user_id, full_name, phone, vacancy, question_text, status, admin_reply, created_at, closed_at
                FROM inquiries WHERE inquiry_id = ?
                """,
                (inquiry_id,)
            )
            return cursor.fetchone()

    def reply_inquiry(self, inquiry_id: int, reply_text: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE inquiries SET admin_reply = ?, status = 'replied' WHERE inquiry_id = ?",
                (reply_text, inquiry_id)
            )
            conn.commit()
            return cursor.rowcount > 0

    def close_inquiry(self, inquiry_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE inquiries SET status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE inquiry_id = ?",
                (inquiry_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    # ==================== ПРЯМОЙ ДИАЛОГ (ЖИВОЙ ЧАТ) ====================
    def start_direct_dialog(self, user_id: str, operator_id: int, ticket_id: Optional[int] = None, full_name: str = "", platform: str = "tg") -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO active_dialogs (user_id, operator_id, ticket_id, full_name, platform, started_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (str(user_id), operator_id, ticket_id, full_name, platform)
            )
            conn.commit()
            return True

    def get_dialog_by_user(self, user_id: str) -> Optional[Tuple]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, operator_id, ticket_id, full_name, platform FROM active_dialogs WHERE user_id = ?", (str(user_id),))
            return cursor.fetchone()

    def get_dialog_by_operator(self, operator_id: int) -> Optional[Tuple]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, operator_id, ticket_id, full_name, platform FROM active_dialogs WHERE operator_id = ?", (operator_id,))
            return cursor.fetchone()

    def end_direct_dialog(self, user_id: Optional[str] = None, operator_id: Optional[int] = None) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if user_id:
                cursor.execute("DELETE FROM active_dialogs WHERE user_id = ?", (str(user_id),))
            elif operator_id:
                cursor.execute("DELETE FROM active_dialogs WHERE operator_id = ?", (operator_id,))
            conn.commit()
            return cursor.rowcount > 0

    # ==================== СИСТЕМНЫЕ МЕТОДЫ ====================
    def block_user_and_clean(self, user_id: Any, reason: str = "Спам") -> bool:
        """Вносит пользователя в ЧС, аннулирует активные анкеты, закрывает диалоги и обращения."""
        uid_str = str(user_id)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("INSERT OR REPLACE INTO blacklist (user_id, reason) VALUES (?, ?)", (uid_str, reason))
                cursor.execute("DELETE FROM active_dialogs WHERE user_id = ?", (uid_str,))
                cursor.execute("UPDATE candidates SET status = 'Отказ (ЧС)', updated_at = CURRENT_TIMESTAMP WHERE user_id = ?", (uid_str,))
                cursor.execute("UPDATE inquiries SET status = 'Закрыто (ЧС)' WHERE user_id = ?", (uid_str,))
                conn.commit()
                return True
            except Exception:
                return False

    def block_user(self, user_id: Any, reason: str = "Спам") -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("INSERT OR REPLACE INTO blacklist (user_id, reason) VALUES (?, ?)", (str(user_id), reason))
                conn.commit()
                return True
            except Exception:
                return False

    def unblock_user(self, user_id: Any) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM blacklist WHERE user_id = ?", (str(user_id),))
            conn.commit()
            return cursor.rowcount > 0

    def get_blacklist(self) -> List[Tuple[str, str, str]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, reason, blocked_at FROM blacklist ORDER BY blocked_at DESC")
            return cursor.fetchall()

    def is_blocked(self, user_id: Any, super_admin_id: Optional[int] = None) -> bool:
        if super_admin_id is not None and str(user_id) == str(super_admin_id):
            return False
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (str(user_id),))
            return cursor.fetchone() is not None

    def check_health(self) -> bool:
        try:
            with self._get_connection() as conn:
                conn.execute("SELECT 1 FROM candidates LIMIT 1")
                conn.execute("SELECT 1 FROM inquiries LIMIT 1")
                return True
        except Exception:
            return False

    def get_setting(self, key: str, default: str = "") -> str:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else default

    def set_setting(self, key: str, value: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)", (key, str(value)))
            conn.commit()
    def get_all_settings(self) -> Dict[str, str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM system_settings WHERE key NOT LIKE 'text_override:%'")
            return {row[0]: row[1] for row in cursor.fetchall()}

    def get_all_text_overrides(self) -> Dict[str, str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM system_settings WHERE key LIKE 'text_override:%'")
            return {row[0].replace('text_override:', ''): row[1] for row in cursor.fetchall()}

    def get_dynamic_text(self, text_key: str, default_val: str = '') -> str:
        override = self.get_setting(f'text_override:{text_key}', '')
        return override if override else default_val

    def set_dynamic_text(self, text_key: str, value: str):
        self.set_setting(f'text_override:{text_key}', value)



    def get_admin_role(self, user_id: int) -> Optional[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT role FROM admins WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return row[0] if row else None

    
    def checkpoint_and_optimize(self) -> bool:
        """Оптимизация базы данных: сброс WAL-журнала и дефрагментация страниц памяти."""
        try:
            with self._get_connection() as conn:
                try:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
                except sqlite3.OperationalError:
                    pass
                conn.execute("PRAGMA optimize;")
            return True
        except Exception as e:
            logging.error(f"Ошибка оптимизации SQLite: {e}")
            return False

    def backup_database(self, backup_dir: str = "backups") -> str:
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"resumes_backup_{timestamp}.db"
        backup_path = os.path.join(backup_dir, backup_filename)
        counter = 1
        while os.path.exists(backup_path):
            backup_filename = f"resumes_backup_{timestamp}_{counter}.db"
            backup_path = os.path.join(backup_dir, backup_filename)
            counter += 1

        with self._get_connection() as src_conn:
            with sqlite3.connect(backup_path) as dst_conn:
                src_conn.backup(dst_conn)

        return backup_path

    def list_backups(self, backup_dir: str = "backups") -> List[Dict[str, Any]]:
        """Возвращает список всех созданных бэкапов с датой и размером."""
        if not os.path.exists(backup_dir):
            return []
        files = []
        for fn in os.listdir(backup_dir):
            if fn.endswith(".db"):
                fp = os.path.join(backup_dir, fn)
                st = os.stat(fp)
                files.append({
                    "filename": fn,
                    "path": fp,
                    "size_bytes": st.st_size,
                    "size_kb": round(st.st_size / 1024, 1),
                    "created_at": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                })
        files.sort(key=lambda x: (x["created_at"], x["filename"]), reverse=True)
        return files

    def restore_database(self, backup_filename: str, backup_dir: str = "backups") -> Tuple[bool, str]:
        """Безопасное горячее восстановление базы данных из бэкапа без остановки бота."""
        clean_name = os.path.basename(backup_filename)
        backup_path = os.path.join(backup_dir, clean_name)
        if not os.path.exists(backup_path):
            return False, f"Файл бэкапа {clean_name} не найден в {backup_dir}."

        # 1. Сначала делаем экстренную копию текущего состояния базы перед откатом
        pre_restore_path = self.backup_database(backup_dir)
        pre_restore_name = os.path.basename(pre_restore_path)

        # 2. Накатываем бэкап на боевую базу через SQLite Backup API
        try:
            with sqlite3.connect(backup_path) as src_conn:
                with self._get_connection() as dst_conn:
                    src_conn.backup(dst_conn)
            return True, f"База успешно восстановлена из <code>{clean_name}</code>.\nСтраховочная копия создана: <code>{pre_restore_name}</code>"
        except Exception as e:
            return False, f"Ошибка восстановления базы: {e}"

    def delete_backup(self, backup_filename: str, backup_dir: str = "backups") -> Tuple[bool, str]:
        """Удаление указанного файла бэкапа."""
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
        """Оставляет последние keep_count бэкапов, остальные удаляет для экономии диска сервера."""
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

    # ==================== FSM & ПЕРСИСТЕНТНЫЕ СЕССИИ ====================
    def set_fsm_state(self, key: str, state: Optional[str]):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if state is None:
                cursor.execute("UPDATE fsm_storage SET state = NULL, updated_at = CURRENT_TIMESTAMP WHERE storage_key = ?", (key,))
            else:
                cursor.execute(
                    """
                    INSERT INTO fsm_storage (storage_key, state, data, updated_at)
                    VALUES (?, ?, "{}", CURRENT_TIMESTAMP)
                    ON CONFLICT(storage_key) DO UPDATE SET state = ?, updated_at = CURRENT_TIMESTAMP
                    """,
                    (key, state, state)
                )
            conn.commit()

    def get_fsm_state(self, key: str) -> Optional[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT state FROM fsm_storage WHERE storage_key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else None

    def set_fsm_data(self, key: str, data: Dict[str, Any]):
        raw_json = json.dumps(data or {}, ensure_ascii=False)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO fsm_storage (storage_key, state, data, updated_at)
                VALUES (?, NULL, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(storage_key) DO UPDATE SET data = ?, updated_at = CURRENT_TIMESTAMP
                """,
                (key, raw_json, raw_json)
            )
            conn.commit()

    def get_fsm_data(self, key: str) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT data FROM fsm_storage WHERE storage_key = ?", (key,))
            row = cursor.fetchone()
            if row and row[0]:
                try:
                    return json.loads(row[0])
                except Exception:
                    return {}
            return {}

    def set_external_session(self, platform: str, user_id: str, data: Dict[str, Any]):
        raw_json = json.dumps(data or {}, ensure_ascii=False)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO external_sessions (platform, user_id, data, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(platform, user_id) DO UPDATE SET data = ?, updated_at = CURRENT_TIMESTAMP
                """,
                (platform, str(user_id), raw_json, raw_json)
            )
            conn.commit()

    def delete_external_session(self, platform: str, user_id: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM external_sessions WHERE platform = ? AND user_id = ?", (platform, str(user_id)))
            conn.commit()

    def get_all_external_sessions(self) -> List[Tuple[str, str, Dict[str, Any]]]:
        sessions = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT platform, user_id, data FROM external_sessions")
            for plat, uid, raw_json in cursor.fetchall():
                try:
                    d = json.loads(raw_json) if raw_json else {}
                except Exception:
                    d = {}
                sessions.append((plat, uid, d))
        return sessions
