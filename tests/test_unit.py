"""
Юнит-тесты доменной бизнес-логики МУП «Ульяновскэлектротранс» (Testing Pyramid: Unit Layer).
Проверяют валидаторы ФИО, телефонов, возрастных цензов (196-ФЗ / ТК РФ), трамвайных маршрутов и расчетов смен.
"""

from datetime import date
import pytest

from services.domain_rules import (
    ULYANOVSK_TRAM_ROUTES,
    calculate_age_from_birth_date,
    calculate_shift_pay,
    validate_candidate_age_for_role,
    validate_phone_number,
    validate_russian_fio,
    validate_tram_route,
)


class TestRussianFioValidation:
    """Тестирование валидации и нормализации ФИО граждан РФ."""

    def test_valid_three_words_fio(self):
        """Корректное ФИО из трех слов нормализуется с заглавных букв."""
        is_valid, norm, err = validate_russian_fio("иванов иван иванович")
        assert is_valid is True
        assert norm == "Иванов Иван Иванович"
        assert err is None

    def test_valid_hyphenated_surname(self):
        """Поддержка двойных фамилий через дефис (напр. Римский-Корсаков)."""
        is_valid, norm, err = validate_russian_fio("римский-корсаков николай андреевич")
        assert is_valid is True
        assert norm == "Римский-Корсаков Николай Андреевич"
        assert err is None

    def test_invalid_fio_with_latin_letters(self):
        """Отклонение ФИО, содержащего латинские буквы."""
        is_valid, norm, err = validate_russian_fio("Ivanov Ivan")
        assert is_valid is False
        assert "только русские буквы" in err

    def test_invalid_fio_with_digits_or_symbols(self):
        """Отклонение ФИО с цифрами и спецсимволами."""
        is_valid, norm, err = validate_russian_fio("Иванов123 Иван!")
        assert is_valid is False
        assert err is not None

    def test_invalid_fio_too_short(self):
        """Отклонение неполного ФИО (только одно слово или пустое)."""
        is_valid, norm, err = validate_russian_fio("Иванов")
        assert is_valid is False
        assert "как минимум Фамилию и Имя" in err

        is_valid_empty, _, _ = validate_russian_fio("")
        assert is_valid_empty is False


class TestPhoneNumberValidation:
    """Тестирование нормализации номеров телефонов стандарта РФ."""

    def test_valid_phone_plus_seven_format(self):
        """Номер с префиксом +7 нормализуется корректно."""
        is_valid, norm, err = validate_phone_number("+79021234567")
        assert is_valid is True
        assert norm == "+79021234567"
        assert err is None

    def test_valid_phone_eight_prefix_and_formatting(self):
        """Номер с восьмеркой, скобками и дефисами преобразуется в +7."""
        is_valid, norm, err = validate_phone_number("8 (927) 801-23-45")
        assert is_valid is True
        assert norm == "+79278012345"
        assert err is None

    def test_valid_phone_ten_digits(self):
        """Номер из 10 цифр, начинающийся с 9, дополняется +7."""
        is_valid, norm, err = validate_phone_number("9176100000")
        assert is_valid is True
        assert norm == "+79176100000"
        assert err is None

    def test_invalid_phone_too_short_or_alphanumeric(self):
        """Отклонение неполных номеров и некорректных символов."""
        is_valid, _, err = validate_phone_number("12345")
        assert is_valid is False
        assert err is not None

        is_valid_alpha, _, _ = validate_phone_number("7902abc4567")
        assert is_valid_alpha is False


class TestCandidateAgeThresholds:
    """Тестирование соблюдения ст. 26 196-ФЗ и ст. 328.1 ТК РФ."""

    def test_driver_underage_rejected(self):
        """Кандидат на должность водителя моложе 21 года бракуется."""
        ref_date = date(2026, 10, 1)
        # Кандидату 20 лет
        is_ok, age, err = validate_candidate_age_for_role("15.05.2006", "Водитель трамвая", reference_date=ref_date)
        assert is_ok is False
        assert age == 20
        assert "196-ФЗ" in err
        assert "с 21 года" in err

    def test_driver_eligible_age(self):
        """Кандидат на должность водителя в возрасте 21+ допускается."""
        ref_date = date(2026, 10, 1)
        # Кандидату 21 год
        is_ok, age, err = validate_candidate_age_for_role("15.05.2005", "Водитель трамвая", reference_date=ref_date)
        assert is_ok is True
        assert age == 21
        assert err is None

    def test_conductor_underage_rejected(self):
        """Кандидат на должность кондуктора моложе 18 лет бракуется."""
        ref_date = date(2026, 10, 1)
        # Кандидату 17 лет
        is_ok, age, err = validate_candidate_age_for_role("15.05.2009", "Кондуктор", reference_date=ref_date)
        assert is_ok is False
        assert age == 17
        assert "строго с 18 лет" in err

    def test_conductor_eligible_age(self):
        """Кандидат на должность кондуктора в возрасте 18 лет допускается."""
        ref_date = date(2026, 10, 1)
        # Кандидату ровно 18 лет
        is_ok, age, err = validate_candidate_age_for_role("15.05.2008", "Кондуктор", reference_date=ref_date)
        assert is_ok is True
        assert age == 18
        assert err is None

    def test_calculate_age_leap_year_boundary(self):
        """Корректный расчет возраста с учетом високосного года."""
        ref_date = date(2026, 2, 28)
        # Рожден 29 февраля 2004 года
        age = calculate_age_from_birth_date("29.02.2004", reference_date=ref_date)
        assert age == 21  # 29 февраля наступает 1 марта в невисокосном


class TestTramRouteValidation:
    """Тестирование валидации маршрутной сети Ульяновска."""

    def test_valid_main_routes(self):
        """Маршруты № 4 и № 22 валидны и привязаны к Северному депо № 1."""
        is_valid_4, norm_4, info_4 = validate_tram_route("4")
        assert is_valid_4 is True
        assert norm_4 == "4"
        assert info_4["depot"] == "Северное депо № 1"

        is_valid_22, norm_22, info_22 = validate_tram_route("№ 22")
        assert is_valid_22 is True
        assert norm_22 == "22"
        assert info_22["depot"] == "Северное депо № 1"

    def test_valid_seasonal_route(self):
        """Маршрут № 107 является сезонным маршрутом до СНТ Сокольники."""
        is_valid, norm, info = validate_tram_route("107")
        assert is_valid is True
        assert info["is_seasonal"] is True
        assert info["end_point"] == "СНТ «Сокольники»"

    def test_invalid_route(self):
        """Несуществующий номер маршрута (например 99) отклоняется."""
        is_valid, norm, info = validate_tram_route("99")
        assert is_valid is False
        assert info is None


class TestShiftPayrollCalculation:
    """Тестирование расчета оплаты труда (ст. 153, 154 ТК РФ)."""

    def test_standard_day_shift(self):
        """Стандартная дневная 8-часовая смена без надбавок (водитель 3 класса)."""
        res = calculate_shift_pay(base_hourly_rate=300, day_hours=8, night_hours=0, driver_class=3)
        assert res["total_hours"] == 8.0
        assert res["base_pay"] == 2400.0
        assert res["night_pay"] == 0.0
        assert res["class_pay"] == 0.0
        assert res["total_payout"] == 2400.0

    def test_night_shift_surcharge(self):
        """Смена с 3 ночными часами включает надбавку 40% (ст. 154 ТК РФ)."""
        # 5 часов днем + 3 часа ночью = 8 часов
        # Ночные 3 ч * 300 * 0.4 = 360 руб
        res = calculate_shift_pay(base_hourly_rate=300, day_hours=5, night_hours=3, driver_class=3)
        assert res["base_pay"] == 2400.0
        assert res["night_pay"] == 360.0
        assert res["total_payout"] == 2760.0

    def test_driver_first_class_bonus(self):
        """Водитель 1 класса получает надбавку 25% ко всей смене."""
        # 8 ч * 300 = 2400 руб; 2400 * 0.25 = 600 руб
        res = calculate_shift_pay(base_hourly_rate=300, day_hours=8, night_hours=0, driver_class=1)
        assert res["class_pay"] == 600.0
        assert res["total_payout"] == 3000.0

    def test_holiday_double_pay_with_regularity_bonus(self):
        """Смена в праздничный день удваивает базовую оплату + премия за регулярность 10%."""
        # Базовая 2400 + праздничные 2400 = 4800. Премия 10% от 4800 = 480. Итого 5280
        res = calculate_shift_pay(
            base_hourly_rate=300,
            day_hours=8,
            night_hours=0,
            driver_class=3,
            is_holiday_or_weekend=True,
            regularity_bonus_pct=10,
        )
        assert res["holiday_extra"] == 2400.0
        assert res["regularity_bonus"] == 480.0
        assert res["total_payout"] == 5280.0
