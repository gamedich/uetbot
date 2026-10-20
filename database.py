"""
Модуль базы данных SQLite для МУП «Ульяновскэлектротранс».
Поддерживает высоконагруженный режим WAL (Write-Ahead Logging),
пул транзакций без утечек соединений, индексирование, кулдауны,
историю заявок, изоляцию кадровых данных и требования 152-ФЗ РФ.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
import sqlite3
import threading
from typing import Generator, Optional

try:
    import aiosqlite
except ImportError:
    aiosqlite = None

from repositories import (
    AdminMixin,
    BackupMixin,
    CandidateMixin,
    EmployeeMixin,
    FSMMixin,
    InquiryMixin,
    SettingsMixin,
    VacancyMixin,
    init_and_migrate_db,
)

logger = logging.getLogger("UET_DATABASE")


class ResumeDB(
    CandidateMixin,
    EmployeeMixin,
    AdminMixin,
    InquiryMixin,
    VacancyMixin,
    BackupMixin,
    FSMMixin,
    SettingsMixin,
):
    """
    Фасад базы данных МУП «Ульяновскэлектротранс».
    Объединяет пулинг соединений SQLite, миграции и доменные репозитории:
    - CandidateMixin: управление анкетами кандидатов и 152-ФЗ
    - EmployeeMixin: личный кабинет сотрудников депо и реферальная программа
    - AdminMixin: управление администраторами, пароли scrypt и уведомления
    - InquiryMixin: вопросы соискателей, антифлуд и прямой диалог
    - VacancyMixin: витрина вакансий и сквозная синхронизация
    - BackupMixin: горячее резервное копирование SQLite Online Backup API
    - FSMMixin: персистентные состояния FSM и сессии мультимессенджера
    - SettingsMixin: безопасность, черный список и системные настройки
    """

    def __init__(self, db_path: Optional[str] = None, test_db_path: Optional[str] = None):
        base_dir = Path(__file__).resolve().parent
        data_dir = base_dir / "data"

        if db_path is None:
            db_path = os.getenv("DB_PATH")

        if not db_path:
            db_path = str(data_dir / "resumes.db")
        elif db_path != ":memory:" and not os.path.isabs(db_path):
            db_path = (
                str(base_dir / db_path)
                if db_path.startswith("data/")
                else str(data_dir / os.path.basename(db_path))
            )

        if not test_db_path:
            if db_path == ":memory:":
                test_db_path = ":memory:"
            else:
                test_db_path = str(data_dir / "resumes_test.db")
        elif test_db_path != ":memory:" and not os.path.isabs(test_db_path):
            test_db_path = (
                str(base_dir / test_db_path)
                if test_db_path.startswith("data/")
                else str(data_dir / os.path.basename(test_db_path))
            )

        self.db_path = db_path
        self.test_db_path = test_db_path

        # Автоматически создаем папку data/, если её нет
        for p in (self.db_path, self.test_db_path):
            if p != ":memory:":
                parent = os.path.dirname(p)
                if parent:
                    os.makedirs(parent, exist_ok=True)

        self._local = threading.local()

        self._init_and_migrate_db(is_test=False)
        if self.test_db_path != self.db_path:
            self._init_and_migrate_db(is_test=True)

    def _get_thread_connection(self, is_test: bool = False) -> sqlite3.Connection:
        """Получение или создание переиспользуемого соединения для текущего потока (Thread-local caching)."""
        attr_name = "test_conn" if is_test else "prod_conn"
        conn = getattr(self._local, attr_name, None)
        if conn is None:
            target = self.test_db_path if is_test else self.db_path
            if target != ":memory:":
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
            conn = sqlite3.connect(target, timeout=30.0, check_same_thread=False)
            if target != ":memory:":
                conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=30000;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA cache_size=-64000;")
            conn.execute("PRAGMA temp_store=MEMORY;")
            setattr(self._local, attr_name, conn)
        return conn

    @contextlib.contextmanager
    def connection(self, is_test: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """Контекстный менеджер подключения к SQLite с транзакцией и переиспользованием соединений."""
        conn = self._get_thread_connection(is_test=is_test)
        try:
            with conn:
                yield conn
        except sqlite3.OperationalError as e:
            # При сбросе соединения или дисковом сбое пересоздаем thread-local соединение
            if "disk I/O" in str(e) or "database is locked" in str(e):
                attr_name = "test_conn" if is_test else "prod_conn"
                try:
                    conn.close()
                except Exception:
                    pass
                setattr(self._local, attr_name, None)
            raise

    @contextlib.contextmanager
    def _get_connection(self, is_test: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """Совместимый контекстный менеджер _get_connection() с гарантированным закрытием соединения."""
        with self.connection(is_test=is_test) as conn:
            yield conn

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
        if target != ":memory:":
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
        conn = await aiosqlite.connect(target, timeout=30.0)
        if target != ":memory:":
            await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA foreign_keys=ON;")
        await conn.execute("PRAGMA busy_timeout=30000;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("PRAGMA cache_size=-64000;")
        await conn.execute("PRAGMA temp_store=MEMORY;")
        return conn

    def _init_and_migrate_db(self, is_test: bool = False):
        """Создание таблиц, миграция структуры и установка высокоскоростных индексов"""
        with self._get_connection(is_test=is_test) as conn:
            init_and_migrate_db(conn, is_test=is_test)
