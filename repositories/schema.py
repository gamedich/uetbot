"""
DDL схемы, миграции таблиц и начальные данные SQLite (PRAGMA WAL).
Изолирует структуру таблиц, индексов и первичный сидинг вакансий.
"""

from __future__ import annotations

import json
import sqlite3
from typing import List, Tuple


def init_and_migrate_db(conn: sqlite3.Connection, is_test: bool = False) -> None:
    """Создание таблиц, миграция структуры и установка высокоскоростных индексов."""
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
            updated_at TIMESTAMP DEFAULT NULL,
            consent_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            referrer_tab TEXT DEFAULT NULL,
            interview_depot TEXT DEFAULT '',
            interview_time TEXT DEFAULT '',
            interview_status TEXT DEFAULT 'pending'
        )
        """
    )
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

    new_columns: List[Tuple[str, str]] = [
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
        ("referrer_tab", "TEXT DEFAULT NULL"),
        ("interview_depot", "TEXT DEFAULT ''"),
        ("interview_time", "TEXT DEFAULT ''"),
        ("interview_status", "TEXT DEFAULT 'pending'"),
    ]
    new_columns_dict = dict(new_columns)
    for col_name, _ in new_columns:
        if col_name not in cand_cols and col_name in new_columns_dict and col_name.isidentifier():
            cursor.execute(f"ALTER TABLE candidates ADD COLUMN {col_name} {new_columns_dict[col_name]}")

    # Для тестовой базы стартуем нумерацию тикетов с 900000 (идемпотентно)
    if is_test:
        cursor.execute("INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES ('candidates', 900000)")
        cursor.execute("INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES ('inquiries', 900000)")
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
    if "email" not in inq_cols:
        cursor.execute("ALTER TABLE inquiries ADD COLUMN email TEXT DEFAULT ''")
    if "reply_channel" not in inq_cols:
        cursor.execute("ALTER TABLE inquiries ADD COLUMN reply_channel TEXT DEFAULT 'tg'")

    # Миграции полей администраторов (веб-пароль, телефон, имя)
    cursor.execute("PRAGMA table_info(admins)")
    admin_cols = [row[1] for row in cursor.fetchall()]
    if "web_password" not in admin_cols:
        cursor.execute("ALTER TABLE admins ADD COLUMN web_password TEXT")
    if "phone" not in admin_cols:
        cursor.execute("ALTER TABLE admins ADD COLUMN phone TEXT")
    if "full_name" not in admin_cols:
        cursor.execute("ALTER TABLE admins ADD COLUMN full_name TEXT")
    if "email" not in admin_cols:
        cursor.execute("ALTER TABLE admins ADD COLUMN email TEXT DEFAULT ''")
    if "username" not in admin_cols:
        cursor.execute("ALTER TABLE admins ADD COLUMN username TEXT DEFAULT ''")

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

    # 11. Таблица сотрудников депо (Личный кабинет и рефералка)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tab_number TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            depot TEXT DEFAULT '',
            position TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            telegram_id INTEGER DEFAULT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # 12. Таблица заявок сотрудников на документы
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS employee_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tab_number TEXT NOT NULL,
            full_name TEXT NOT NULL,
            req_type TEXT NOT NULL,
            status TEXT DEFAULT 'Новая',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # 13. Журнал актов уничтожения ПДн (ст. 21 152-ФЗ)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS destruction_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER,
            user_id TEXT,
            platform TEXT,
            reason TEXT,
            destroyed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            act_number TEXT,
            operator TEXT DEFAULT 'Система'
        )
        """
    )
    cursor.execute("PRAGMA table_info(destruction_logs)")
    dl_cols = [row[1] for row in cursor.fetchall()]
    if "operator" not in dl_cols:
        cursor.execute("ALTER TABLE destruction_logs ADD COLUMN operator TEXT DEFAULT 'Система'")

    # 14. Таблица настроек и кастомизации вакансий (Сквозная синхронизация сайт <-> бот)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS vacancy_settings (
            slug TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            is_active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            salary_text TEXT,
            description TEXT,
            meta_json TEXT DEFAULT '{}'
        )
        """
    )

    # Проверка наличия стартовых данных в vacancy_settings
    cursor.execute("SELECT COUNT(*) FROM vacancy_settings")
    vac_count = cursor.fetchone()[0]
    if vac_count == 0:
        initial_vacancies = [
            (
                "tram_driver",
                "Водитель трамвая (обучение со стипендией)",
                "driver",
                1,
                1,
                "55 000 — 100 000 ₽",
                "Управление современными трамвайными вагонами («Львенок», «Татра») на маршрутах Ульяновска. Бесплатное обучение 2-3 мес. со стипендией от 20 000 ₽, оплачиваемый отпуск 40 дней, депо №1 и №2.",
                json.dumps({
                    "icon": "🚋",
                    "badge": "Львёнок • Стипендия 20к",
                    "stipend": "от 20 000 ₽ на период обучения",
                    "training": True,
                    "requirements": "От 21 года (к моменту выпуска), ст. 328.1 ТК РФ",
                    "depots": ["Депо №1 (Северное)", "Депо №2 (Засвияжское)"]
                }, ensure_ascii=False)
            ),
            (
                "trolley_driver",
                "Водитель троллейбуса (обучение со стипендией)",
                "driver",
                1,
                2,
                "55 000 — 95 000 ₽",
                "Пассажирские перевозки на современных троллейбусных маршрутах Заволжского района. Бесплатное переобучение со стипендией 20 000 ₽, льготный пенсионный стаж, депо №3.",
                json.dumps({
                    "icon": "🚎",
                    "badge": "Заволжское депо №3",
                    "stipend": "от 20 000 ₽ на период обучения",
                    "training": True,
                    "requirements": "От 21 года, ст. 328.1 ТК РФ",
                    "depots": ["Депо №3 (Заволжское)"]
                }, ensure_ascii=False)
            ),
            (
                "conductor",
                "Кондуктор общественного транспорта",
                "service",
                1,
                3,
                "38 000 — 48 000 ₽",
                "Сбор платы за проезд и проверка проездных билетов на маршрутах трамвая и троллейбуса. Сменный график 2/2, бесплатный проезд, спецодежда.",
                json.dumps({
                    "icon": "🎫",
                    "badge": "Сменный график 2/2",
                    "stipend": None,
                    "training": False,
                    "requirements": "От 18 лет, вежливость, ответственность",
                    "depots": ["Депо №1", "Депо №2", "Депо №3"]
                }, ensure_ascii=False)
            ),
            (
                "rolling_stock_fitter",
                "Слесарь по ремонту подвижного состава",
                "technical",
                1,
                4,
                "45 000 — 70 000 ₽",
                "Техническое обслуживание и текущий ремонт механического, пневматического и электрического оборудования трамваев и троллейбусов.",
                json.dumps({
                    "icon": "🔧",
                    "badge": "Техническая служба",
                    "stipend": None,
                    "training": False,
                    "requirements": "От 18 лет, знание устройства подвижного состава",
                    "depots": ["Депо №1", "Депо №2", "Депо №3"]
                }, ensure_ascii=False)
            ),
            (
                "catenary_electrician",
                "Электромонтер контактной сети",
                "technical",
                1,
                5,
                "48 000 — 72 000 ₽",
                "Обслуживание, плановый осмотр и оперативное восстановление контактных сетей и тяговых подстанций предприятия.",
                json.dumps({
                    "icon": "⚡",
                    "badge": "Служба энергохозяйства",
                    "stipend": None,
                    "training": False,
                    "requirements": "От 18 лет, электробезопасность от III группы",
                    "depots": ["Служба энергохозяйства (ул. Гончарова, 2)"]
                }, ensure_ascii=False)
            ),
            (
                "track_worker",
                "Монтер пути",
                "technical",
                1,
                6,
                "42 000 — 65 000 ₽",
                "Текущее содержание, ремонт и выправка рельсового полотна и стрелочных переводов трамвайных путей города Ульяновска.",
                json.dumps({
                    "icon": "🛤",
                    "badge": "Служба пути",
                    "stipend": None,
                    "training": False,
                    "requirements": "От 18 лет, выносливость, без противопоказаний по здоровью",
                    "depots": ["Служба пути"]
                }, ensure_ascii=False)
            )
        ]
        cursor.executemany(
            """
            INSERT INTO vacancy_settings (slug, title, category, is_active, sort_order, salary_text, description, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            initial_vacancies
        )

    conn.commit()
