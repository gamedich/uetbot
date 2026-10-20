#!/usr/bin/env python3
"""
Скрипт проверки целостности схемы базы данных SQLite (WAL, таблицы, индексы, колонки 152-ФЗ).
Используется перед деплоем и выкаткой в продакшн.
"""

import sqlite3
import sys
from pathlib import Path

REQUIRED_TABLES = [
    "candidates",
    "admins",
    "inquiries",
    "user_consents",
    "destruction_logs",
    "vacancy_settings",
    "active_dialogs",
    "blacklist",
    "system_settings",
    "user_cooldowns",
    "fsm_storage",
    "external_sessions",
    "employees",
    "employee_requests",
]

REQUIRED_CANDIDATE_COLUMNS = [
    "ticket_id",
    "platform",
    "user_id",
    "full_name",
    "phone",
    "vacancy",
    "experience",
    "status",
    "created_at",
    "consent_timestamp",
    "birth_date",
    "city",
    "driver_license",
    "education",
    "relocation",
    "dormitory",
    "shift_work",
    "medical_restrictions",
    "criminal_record",
    "source",
    "extra_info",
    "raw_data_json",
    "referrer_tab",
    "interview_depot",
    "interview_time",
    "interview_status",
]

REQUIRED_INQUIRY_COLUMNS = [
    "inquiry_id",
    "ticket_id",
    "platform",
    "user_id",
    "question_text",
    "admin_reply",
    "status",
]

REQUIRED_ADMIN_COLUMNS = [
    "user_id",
    "role",
    "web_password",
]


def check_db_file(db_path: Path) -> bool:
    print("\n==================================================")
    print(f"🔍 Проверка базы данных: {db_path}")
    print("==================================================")

    if not db_path.exists():
        print(f"❌ Файл {db_path} не существует!")
        return False

    all_passed = True
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    try:
        # 1. Проверка PRAGMA
        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0].lower()
        if journal_mode == "wal":
            print("✅ PRAGMA journal_mode = WAL")
        else:
            print(f"⚠️  PRAGMA journal_mode = {journal_mode.upper()} (рекомендуется WAL)")

        cursor.execute("PRAGMA foreign_keys;")
        fk = cursor.fetchone()[0]
        print(f"✅ PRAGMA foreign_keys = {fk}")

        cursor.execute("PRAGMA integrity_check;")
        integrity = cursor.fetchone()[0]
        if integrity == "ok":
            print("✅ PRAGMA integrity_check = OK")
        else:
            print(f"❌ Ошибка целостности SQLite: {integrity}")
            all_passed = False

        # 2. Проверка таблиц
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        existing_tables = {row[0] for row in cursor.fetchall()}

        print("\n--- Проверка таблиц ---")
        for table in REQUIRED_TABLES:
            if table in existing_tables:
                cursor.execute(f"SELECT COUNT(*) FROM {table};")
                cnt = cursor.fetchone()[0]
                print(f"✅ Таблица '{table}': найдена ({cnt} записей)")
            else:
                print(f"❌ Таблица '{table}': ОТСУТСТВУЕТ")
                all_passed = False

        # 3. Проверка колонок таблицы candidates
        if "candidates" in existing_tables:
            print("\n--- Проверка колонок таблицы candidates (16 шагов + аудит) ---")
            cursor.execute("PRAGMA table_info(candidates);")
            cand_cols = {row[1] for row in cursor.fetchall()}
            for col in REQUIRED_CANDIDATE_COLUMNS:
                if col in cand_cols:
                    print(f"  ✅ Колонка candidates.{col}")
                else:
                    print(f"  ❌ Колонка candidates.{col}: ОТСУТСТВУЕТ")
                    all_passed = False

        # 4. Проверка колонок таблицы inquiries
        if "inquiries" in existing_tables:
            print("\n--- Проверка колонок таблицы inquiries ---")
            cursor.execute("PRAGMA table_info(inquiries);")
            inq_cols = {row[1] for row in cursor.fetchall()}
            for col in REQUIRED_INQUIRY_COLUMNS:
                if col in inq_cols:
                    print(f"  ✅ Колонка inquiries.{col}")
                else:
                    print(f"  ❌ Колонка inquiries.{col}: ОТСУТСТВУЕТ")
                    all_passed = False

        # 5. Проверка колонок таблицы admins
        if "admins" in existing_tables:
            print("\n--- Проверка колонок таблицы admins ---")
            cursor.execute("PRAGMA table_info(admins);")
            adm_cols = {row[1] for row in cursor.fetchall()}
            for col in REQUIRED_ADMIN_COLUMNS:
                if col in adm_cols:
                    print(f"  ✅ Колонка admins.{col}")
                else:
                    print(f"  ❌ Колонка admins.{col}: ОТСУТСТВУЕТ")
                    all_passed = False

    finally:
        conn.close()

    if all_passed:
        print(f"\n🎉 Все проверки для {db_path.name} успешно пройдены!")
    else:
        print(f"\n❌ Обнаружены несоответствия в схеме {db_path.name}!")

    return all_passed


def main():
    root = Path(__file__).resolve().parent.parent

    if len(sys.argv) > 1:
        dbs_to_check = [Path(p) for p in sys.argv[1:]]
    else:
        dbs_to_check = [
            root / "data" / "resumes.db",
            root / "data" / "resumes_test.db",
            root / "bot_test.db",
        ]

    existing_dbs = [p for p in dbs_to_check if p.exists()]
    if not existing_dbs:
        print("Базы данных не найдены по стандартным путям, инициализируем...")
        from database import ResumeDB
        _ = ResumeDB(data_dir=str(root / "data"))
        existing_dbs = [root / "data" / "resumes.db", root / "data" / "resumes_test.db"]

    success = True
    for db_path in existing_dbs:
        if not check_db_file(db_path):
            success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
