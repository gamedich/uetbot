"""
Доменные правила, валидаторы и финансово-расчетные модели МУП «Ульяновскэлектротранс».
Соответствует требованиям ТК РФ (ст. 153, 154, 328.1), 196-ФЗ и стандартам ГОСТ.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional, Tuple, Union


# =============================================================================
# 1. СЕТЬ МАРШРУТОВ МУП «УЛЬЯНОВСКЭЛЕКТРОТРАНС»
# =============================================================================

# Официальный реестр регулярных трамвайных маршрутов г. Ульяновска
ULYANOVSK_TRAM_ROUTES: Dict[str, Dict[str, Any]] = {
    "1": {
        "title": "Маршрут № 1",
        "depot": "Засвияжское депо № 2",
        "start_point": "Северный Венец",
        "end_point": "УКСМ",
        "is_active": True,
        "is_seasonal": False,
    },
    "4": {
        "title": "Маршрут № 4",
        "depot": "Северное депо № 1",
        "start_point": "мкрн. Огни Севера",
        "end_point": "УКСМ",
        "is_active": True,
        "is_seasonal": False,
    },
    "9": {
        "title": "Маршрут № 9",
        "depot": "Засвияжское депо № 2",
        "start_point": "Дамба",
        "end_point": "УКСМ",
        "is_active": True,
        "is_seasonal": False,
    },
    "10": {
        "title": "Маршрут № 10",
        "depot": "Засвияжское депо № 2",
        "start_point": "Дамба",
        "end_point": "ул. Юности",
        "is_active": True,
        "is_seasonal": False,
    },
    "15": {
        "title": "Маршрут № 15",
        "depot": "Северное депо № 1",
        "start_point": "мкрн. Огни Севера",
        "end_point": "УКСМ",
        "is_active": True,
        "is_seasonal": False,
    },
    "22": {
        "title": "Маршрут № 22",
        "depot": "Северное депо № 1",
        "start_point": "Парк «Победа»",
        "end_point": "УКСМ",
        "is_active": True,
        "is_seasonal": False,
    },
    "70": {
        "title": "Маршрут № 70",
        "depot": "Засвияжское депо № 2",
        "start_point": "УКСМ",
        "end_point": "Кондитерская фабрика «Волжанка»",
        "is_active": True,
        "is_seasonal": False,
    },
    "107": {
        "title": "Маршрут № 107 (Сезонный)",
        "depot": "Северное депо № 1",
        "start_point": "Северный Венец",
        "end_point": "СНТ «Сокольники»",
        "is_active": True,
        "is_seasonal": True,
    },
}


def validate_tram_route(route_number: Union[str, int]) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Валидация трамвайного маршрута г. Ульяновска.
    Принимает номер маршрута в виде строки или числа (например '4', '22', 107, '№ 4').
    Возвращает: (is_valid, normalized_route, route_info)
    """
    if route_number is None:
        return False, None, None

    clean = str(route_number).strip().upper()
    clean = re.sub(r"^[№N#\s]+", "", clean)
    clean = clean.strip()

    if not clean:
        return False, None, None

    if clean in ULYANOVSK_TRAM_ROUTES:
        info = ULYANOVSK_TRAM_ROUTES[clean]
        return True, clean, info

    return False, clean, None


# =============================================================================
# 2. ВАЛИДАЦИЯ ФИО И ТЕЛЕФОНА (СТАНДАРТЫ РФ)
# =============================================================================

def validate_russian_fio(raw_fio: str) -> Tuple[bool, str, Optional[str]]:
    """
    Валидация ФИО гражданина РФ:
    - Минимум 2 слова (Фамилия Имя)
    - Только кириллические символы, дефис и пробелы
    - Автоматическая капитализация каждого слова
    Возвращает: (is_valid, normalized_fio, error_message)
    """
    clean = (raw_fio or "").strip()
    if not clean:
        return False, "", "ФИО не может быть пустым."

    # Проверка на кириллицу (плюс дефис и пробелы для двойных фамилий)
    if not re.match(r"^[А-Яа-яЁё\s\-]+$", clean):
        return False, clean, "ФИО должно содержать только русские буквы и дефис."

    words = [w for w in clean.split() if len(w) > 1]
    if len(words) < 2:
        return False, clean, "Укажите как минимум Фамилию и Имя через пробел."

    # Нормализация: капитализация с учетом составных фамилий через дефис (напр. Мамин-Сибиряк)
    norm_words = []
    for word in words:
        subparts = [p.capitalize() for p in word.split("-")]
        norm_words.append("-".join(subparts))

    normalized = " ".join(norm_words)
    return True, normalized, None


def validate_phone_number(raw_phone: str) -> Tuple[bool, str, Optional[str]]:
    """
    Валидация и нормализация телефонного номера стандарта РФ:
    - Принимает форматы: +79991234567, 89991234567, 9991234567, 8 (999) 123-45-67
    - Возвращает нормализованный вид: +79XXXXXXXXX
    """
    digits = re.sub(r"\D", "", raw_phone or "")
    if len(digits) == 11 and digits[0] in ("7", "8"):
        normalized = "+7" + digits[1:]
        return True, normalized, None
    elif len(digits) == 10 and digits[0] == "9":
        normalized = "+7" + digits
        return True, normalized, None

    return False, raw_phone or "", "Неверный формат телефона. Введите номер в формате +79XXXXXXXXX."


# =============================================================================
# 3. ВОЗРАСТНЫЕ КРИТЕРИИ КАНДИДАТОВ (196-ФЗ и ТК РФ)
# =============================================================================

# Минимальные возрастные пороги допуска к управлению и работе
MIN_AGE_DRIVER = 21       # Ст. 26 196-ФЗ «О безопасности дорожного движения» (категории Tb, Tm)
MIN_AGE_CONDUCTOR = 18    # Ст. 63 и 265 ТК РФ (полная материальная ответственность)
MIN_AGE_GENERAL = 18      # Общий порог трудоустройства в депо


def calculate_age_from_birth_date(birth_date_val: Union[str, date, datetime], reference_date: Optional[date] = None) -> int:
    """
    Точный расчет возраста в полных годах на заданную референсную дату (по умолчанию сегодня).
    Поддерживает строки ДД.ММ.ГГГГ, ГГГГ-ММ-ДД, ДД/ММ/ГГГГ.
    """
    if reference_date is None:
        reference_date = date.today()

    if isinstance(birth_date_val, datetime):
        bdate = birth_date_val.date()
    elif isinstance(birth_date_val, date):
        bdate = birth_date_val
    elif isinstance(birth_date_val, str):
        clean = birth_date_val.strip().replace("/", ".").replace("-", ".")
        parts = clean.split(".")
        if len(parts) == 3:
            if len(parts[0]) == 4:  # YYYY.MM.DD
                bdate = date(int(parts[0]), int(parts[1]), int(parts[2]))
            else:  # DD.MM.YYYY
                bdate = date(int(parts[2]), int(parts[1]), int(parts[0]))
        else:
            raise ValueError(f"Некорректный формат даты: {birth_date_val}")
    else:
        raise TypeError(f"Неподдерживаемый тип даты: {type(birth_date_val)}")

    if bdate > reference_date:
        raise ValueError("Дата рождения не может быть в будущем.")

    return reference_date.year - bdate.year - ((reference_date.month, reference_date.day) < (bdate.month, bdate.day))


def validate_candidate_age_for_role(
    birth_date_or_age: Union[int, str, date, datetime],
    role_or_vacancy: str,
    reference_date: Optional[date] = None,
) -> Tuple[bool, int, Optional[str]]:
    """
    Проверка соответствия возраста соискателя нормативным требованиям РФ:
    - Водители трамвая / троллейбуса: не менее 21 года к моменту выпуска (196-ФЗ, ст. 328.1 ТК РФ).
    - Кондукторы: не менее 18 лет (ст. 63 ТК РФ).
    - Технический персонал депо: не менее 18 лет.
    Возвращает: (is_eligible, calculated_age, error_or_warning_message)
    """
    if isinstance(birth_date_or_age, int):
        age = birth_date_or_age
    else:
        try:
            age = calculate_age_from_birth_date(birth_date_or_age, reference_date)
        except Exception as e:
            return False, 0, f"Ошибка определения возраста: {e}"

    if age < 0 or age > 100:
        return False, age, "Недопустимый возраст соискателя."

    v_lower = (role_or_vacancy or "").lower()

    # Проверка на водительские позиции
    is_driver = any(kw in v_lower for kw in ("водител", "driver", "трамва", "троллейбус", "автобус"))
    is_conductor = any(kw in v_lower for kw in ("кондуктор", "conductor"))

    if is_driver:
        if age < MIN_AGE_DRIVER:
            return (
                False,
                age,
                f"Кандидату {age} лет. Согласно ст. 26 196-ФЗ управление трамваем/троллейбусом разрешено с 21 года.",
            )
        return True, age, None

    if is_conductor:
        if age < MIN_AGE_CONDUCTOR:
            return (
                False,
                age,
                f"Кандидату {age} лет. Работа кондуктором с материальной ответственностью разрешена строго с 18 лет.",
            )
        return True, age, None

    # Для остальных вакансий (слесари, диспетчеры, электрики)
    if age < MIN_AGE_GENERAL:
        return False, age, f"Кандидату {age} лет. Трудоустройство в депо разрешено строго с 18 лет."

    return True, age, None


# =============================================================================
# 4. РАСЧЕТ ОПЛАТЫ СМЕНЫ ВОДИТЕЛЕЙ И КОНДУКТОРОВ (ТК РФ)
# =============================================================================

# Коэффициенты квалификации водителей (доплата за классность)
CLASS_COEFFICIENTS: Dict[int, Decimal] = {
    1: Decimal("0.25"),  # 1 класс: +25%
    2: Decimal("0.10"),  # 2 класс: +10%
    3: Decimal("0.00"),  # 3 класс: без надбавки
}

# Доплата за ночные часы (с 22:00 до 06:00) согласно ст. 154 ТК РФ и Положению об оплате труда МУП УЭТ
NIGHT_SURCHARGE_RATE = Decimal("0.40")  # +40% для водителей городского электротранспорта


def calculate_shift_pay(
    base_hourly_rate: Union[float, int, Decimal],
    day_hours: Union[float, int, Decimal],
    night_hours: Union[float, int, Decimal] = 0,
    driver_class: int = 3,
    is_holiday_or_weekend: bool = False,
    regularity_bonus_pct: Union[float, int, Decimal] = 0,
) -> Dict[str, Any]:
    """
    Расчет оплаты рабочей смены водителя/кондуктора электротранспорта:
    - Базовая почасовая ставка
    - Оплата дневных и ночных часов (ночные +40% по ст. 154 ТК РФ)
    - Доплата за классность водителя (1 класс +25%, 2 класс +10%)
    - Двойная оплата в выходные/праздничные дни (ст. 153 ТК РФ)
    - Премия за соблюдение графика движения (регулярность рейсов)
    Возвращает детализированный финансовый расчет с округлением до копеек.
    """
    rate = Decimal(str(base_hourly_rate))
    d_hours = Decimal(str(day_hours))
    n_hours = Decimal(str(night_hours))
    total_hours = d_hours + n_hours
    bonus_pct = Decimal(str(regularity_bonus_pct)) / Decimal("100")

    if total_hours <= 0 or rate <= 0:
        return {
            "total_hours": float(total_hours),
            "base_hourly_rate": float(rate),
            "base_pay": 0.0,
            "night_pay": 0.0,
            "class_pay": 0.0,
            "holiday_multiplier": 2 if is_holiday_or_weekend else 1,
            "regularity_bonus": 0.0,
            "total_payout": 0.0,
        }

    # 1. Базовая оплата за фактически отработанное время
    base_pay = rate * total_hours

    # 2. Доплата за ночные часы (ст. 154 ТК РФ)
    night_pay = (rate * NIGHT_SURCHARGE_RATE) * n_hours

    # 3. Надбавка за классность (от базовой ставки)
    class_bonus_rate = CLASS_COEFFICIENTS.get(driver_class, Decimal("0.00"))
    class_pay = (rate * class_bonus_rate) * total_hours

    # Сумма до применения коэффициента праздничного дня
    subtotal = base_pay + night_pay + class_pay

    # 4. Коэффициент работы в нерабочие праздничные / выходные дни (ст. 153 ТК РФ)
    if is_holiday_or_weekend:
        holiday_extra = base_pay  # доплата еще одной одинарной ставки
        subtotal += holiday_extra
    else:
        holiday_extra = Decimal("0.00")

    # 5. Премия за регулярность движения
    regularity_bonus = subtotal * bonus_pct

    # 6. Итоговое начисление
    total_payout = (subtotal + regularity_bonus).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "total_hours": float(total_hours),
        "day_hours": float(d_hours),
        "night_hours": float(n_hours),
        "base_hourly_rate": float(rate),
        "base_pay": float(base_pay.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "night_pay": float(night_pay.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "class_bonus_pct": float(class_bonus_rate * 100),
        "class_pay": float(class_pay.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "holiday_extra": float(holiday_extra.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "is_holiday_or_weekend": is_holiday_or_weekend,
        "regularity_bonus": float(regularity_bonus.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "total_payout": float(total_payout),
    }
