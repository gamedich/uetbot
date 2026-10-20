"""
Тесты сервисного слоя валидации и бизнес-правил кандидата (CandidateService).
Проверяют ФИО, телефон, дату рождения (совершеннолетие 18+), водительские триггеры и лимиты.
"""

import pytest

from services.candidate_service import CandidateService


@pytest.fixture
def candidate_service():
    return CandidateService()


class TestCandidateValidation:
    """Тестирование юридических и технических валидаций соискателя."""

    def test_validate_fio_valid(self, candidate_service):
        ok, norm, err = candidate_service.validate_fio("иванов иван иванович")
        assert ok is True
        assert norm == "Иванов Иван Иванович"
        assert err is None

        ok, norm, err = candidate_service.validate_fio("Петров-Водкин Кузьма")
        assert ok is True
        assert norm == "Петров-водкин Кузьма"

    def test_validate_fio_invalid(self, candidate_service):
        ok, _, err = candidate_service.validate_fio("Иван")
        assert ok is False
        assert err is not None

        ok, _, err = candidate_service.validate_fio("Иван123 Иванов")
        assert ok is False

        ok, _, err = candidate_service.validate_fio("")
        assert ok is False

    def test_validate_phone_normalization(self, candidate_service):
        ok, norm, err = candidate_service.validate_phone("+7 (999) 111-22-33")
        assert ok is True
        assert norm == "+79991112233"

        ok, norm, err = candidate_service.validate_phone("89991112233")
        assert ok is True
        assert norm == "+79991112233"

        ok, norm, err = candidate_service.validate_phone("9991112233")
        assert ok is True
        assert norm == "+79991112233"

    def test_validate_phone_invalid(self, candidate_service):
        ok, _, err = candidate_service.validate_phone("12345")
        assert ok is False

        ok, _, err = candidate_service.validate_phone("+1234567890123")
        assert ok is False

    def test_validate_birth_date_adult(self, candidate_service):
        ok, norm, err = candidate_service.validate_birth_date("15.05.1995")
        assert ok is True
        assert norm == "15.05.1995"
        assert err is None

        # ISO format
        ok, norm, err = candidate_service.validate_birth_date("1990-12-31")
        assert ok is True
        assert norm == "31.12.1990"

        # 8 digits
        ok, norm, err = candidate_service.validate_birth_date("01011985")
        assert ok is True
        assert norm == "01.01.1985"

    def test_validate_birth_date_underage(self, candidate_service):
        ok, _, err = candidate_service.validate_birth_date("01.01.2025")
        assert ok is False
        assert "18" in str(err) or "совершеннолет" in str(err).lower()

    def test_validate_question(self, candidate_service):
        ok, clean, _ = candidate_service.validate_question("Как устроиться на работу?")
        assert ok is True
        assert clean == "Как устроиться на работу?"

        ok, _, _ = candidate_service.validate_question("   ")
        assert ok is False

    def test_is_driver_vacancy(self, candidate_service):
        assert candidate_service.is_driver_vacancy("Водитель трамвая") is True
        assert candidate_service.is_driver_vacancy("Водитель троллейбуса") is True
        assert candidate_service.is_driver_vacancy("Слесарь-электрик") is False

    def test_calculate_applicant_score(self, candidate_service):
        # 1. Идеальный кандидат на водителя трамвая с категорией Tm
        ideal_driver = {
            "vacancy": "Водитель трамвая",
            "license_categories": "B, C, Tm",
            "birth_date": "10.05.1990",
            "medical_restrictions": "Нет",
            "criminal_record": "Нет",
            "experience": "Стаж 5 лет",
        }
        res_ideal = candidate_service.calculate_applicant_score(ideal_driver)
        assert res_ideal["score"] >= 80
        assert res_ideal["level"] == "high"
        assert any("Tm" in tag for tag in res_ideal["tags"])

        # 2. Кандидат с судимостью (риск по ст. 328.1 ТК РФ)
        convicted_cand = {
            "vacancy": "Водитель троллейбуса",
            "criminal_record": "Да",
            "medical_restrictions": "Нет",
        }
        res_convicted = candidate_service.calculate_applicant_score(convicted_cand)
        assert res_convicted["score"] < 50
        assert res_convicted["level"] == "low"
        assert any("328.1" in tag for tag in res_convicted["tags"])

