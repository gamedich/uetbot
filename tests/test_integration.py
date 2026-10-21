"""
Интеграционные тесты базы данных SQLite МУП «Ульяновскэлектротранс» (Testing Pyramid: Integration Layer).
Проверяют транзакции, CRUD операции соискателей, переходы статусов кадровой воронки и уникальные ограничения целостности данных.
"""

import sqlite3
import pytest

from database import ResumeDB


class TestDatabaseCandidateOperations:
    """Интеграционное тестирование операций с анкетами соискателей в изолированной БД."""

    def test_candidate_creation_and_retrieval(self, memory_db: ResumeDB):
        """Создание анкеты соискателя и проверка целостности всех атрибутов в БД."""
        ticket_id = memory_db.add_candidate(
            platform="tg",
            user_id="123456789",
            full_name="Волков Алексей Сергеевич",
            phone="+79021234567",
            vacancy="Водитель трамвая (обучение со стипендией)",
            experience="Без опыта",
            birth_date="12.04.1998",
            city="г. Ульяновск",
            driver_license="Нет",
            education="Среднее специальное",
            shift_work="Да, готов к сменному графику 2/2",
            is_test=False,
            consent_timestamp="2026-10-21 12:00:00",
        )

        assert ticket_id > 0

        candidate = memory_db.get_candidate(ticket_id)
        assert candidate is not None
        # Индексы согласно кортежу get_candidate
        assert candidate[0] == ticket_id
        assert candidate[1] == "tg"
        assert candidate[2] == "123456789"
        assert candidate[3] == "Волков Алексей Сергеевич"
        assert candidate[4] == "+79021234567"
        assert "Водитель трамвая" in candidate[5]
        assert candidate[7] == "Новая"  # Дефолтный статус новой заявки
        assert candidate[10] == "12.04.1998"  # birth_date
        assert candidate[11] == "г. Ульяновск"  # city

    def test_candidate_status_lifecycle_and_interview_booking(self, memory_db: ResumeDB):
        """Проверка жизненного цикла статусов кандидата и назначения собеседования в депо."""
        ticket_id = memory_db.add_candidate(
            platform="tg",
            user_id="987654321",
            full_name="Смирнова Елена Павловна",
            phone="+79039876543",
            vacancy="Кондуктор",
            experience="1 год",
            birth_date="05.08.2001",
        )

        # 1. Смена статуса на "В работе"
        memory_db.update_status(ticket_id, "В работе")
        cand = memory_db.get_candidate(ticket_id)
        assert cand[7] == "В работе"

        # 2. Назначение собеседования в Северное депо № 1
        success = memory_db.set_interview_details(
            ticket_id=ticket_id,
            depot="Депо № 1 (Северное)",
            slot_time="24.10.2026 10:00",
        )
        assert success is True

        cand_invited = memory_db.get_candidate(ticket_id)
        assert cand_invited[7] == "Приглашен"
        assert cand_invited[23] == "Депо № 1 (Северное)"  # interview_depot
        assert cand_invited[24] == "24.10.2026 10:00"  # interview_time
        assert cand_invited[25] == "pending"  # interview_status

        # 3. Подтверждение явки
        memory_db.update_interview_status(ticket_id, "confirmed")
        cand_confirmed = memory_db.get_candidate(ticket_id)
        assert cand_confirmed[25] == "confirmed"

        # 4. Перевод в итоговый статус "Принят"
        memory_db.update_status(ticket_id, "Принят")
        cand_accepted = memory_db.get_candidate(ticket_id)
        assert cand_accepted[7] == "Принят"

    def test_database_unique_constraints_enforcement(self, memory_db: ResumeDB):
        """Проверка ограничений уникальности (UNIQUE / PRIMARY KEY) в реляционной схеме."""
        with memory_db.connection() as conn:
            cursor = conn.cursor()

            # 1. Проверка уникальности табельного номера сотрудника (tab_number UNIQUE)
            cursor.execute(
                """
                INSERT INTO employees (tab_number, full_name, depot, position, phone)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("TAB-1044", "Кузнецов Петр Васильевич", "Северное депо № 1", "Водитель 1 класса", "+79021112233"),
            )
            conn.commit()

            # Повторная вставка с тем же tab_number должна возбуждать sqlite3.IntegrityError
            with pytest.raises(sqlite3.IntegrityError):
                cursor.execute(
                    """
                    INSERT INTO employees (tab_number, full_name, depot, position, phone)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("TAB-1044", "Другой Сотрудник", "Депо № 2", "Слесарь", "+79029998877"),
                )

            # 2. Проверка уникальности первичного ключа в черном списке (user_id PRIMARY KEY)
            cursor.execute(
                "INSERT INTO blacklist (user_id, reason) VALUES (?, ?)",
                ("SPAMMER_USER_99", "Массовый спам"),
            )
            conn.commit()

            with pytest.raises(sqlite3.IntegrityError):
                cursor.execute(
                    "INSERT INTO blacklist (user_id, reason) VALUES (?, ?)",
                    ("SPAMMER_USER_99", "Повторная попытка"),
                )

    def test_cooldown_and_duplicate_prevention(self, memory_db: ResumeDB):
        """Проверка бизнес-правила недопущения повторной подачи заявки при активной анкете."""
        user_id = "555444333"
        # Первая заявка создается со статусом "Новая"
        memory_db.add_candidate(
            platform="tg",
            user_id=user_id,
            full_name="Николаев Дмитрий Игоревич",
            phone="+79271239876",
            vacancy="Водитель трамвая",
            experience="Без опыта",
        )

        # Проверка метода check_candidate_can_apply: повторная подача должна блокироваться
        can_apply, reason, active_data = memory_db.check_candidate_can_apply(user_id=user_id, platform="tg")
        assert can_apply is False
        assert reason in ("unprocessed", "active", "cooldown") or "на рассмотрении" in reason.lower()
        assert active_data is not None
