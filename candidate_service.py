# -*- coding: utf-8 -*-
"""
Единый сервисный слой подбора персонала МУП «Ульяновскэлектротранс» (Core Service).
Унифицирует валидацию, проверку ограничений (152-ФЗ, дубликаты, кулдауны)
и регистрацию соискателей для всех каналов связи (Telegram, ВКонтакте, МАКС).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("CANDIDATE_SERVICE")


class CandidateService:
    """Сервис бизнес-логики соискателей."""

    DRIVER_VACANCIES = (
        "водитель трамвая",
        "водитель троллейбуса",
        "водитель",
    )

    NO_EXP_KEYWORDS = (
        "без опыта",
        "нет опыта",
        "не работал",
        "отсутствует",
        "без стажа",
        "нет стажа",
        "не имеется",
    )

    def __init__(self, db_instance=None):
        self._db = db_instance

    @property
    def db(self):
        if self._db is None:
            from common import db
            self._db = db
        return self._db

    # =========================================================================
    # 1. ВАЛИДАЦИЯ ПЕРСОНАЛЬНЫХ ДАННЫХ
    # =========================================================================

    def validate_fio(self, raw_name: str) -> Tuple[bool, str, Optional[str]]:
        """
        Валидация ФИО соискателя:
        - Минимум 2 слова (Фамилия Имя)
        - Без цифр и спецсимволов
        Возвращает: (is_valid, normalized_name, error_message)
        """
        clean = (raw_name or "").strip()
        if not clean:
            return False, "", "Пожалуйста, введите ваши фамилию и имя."

        words = [w for w in clean.split() if len(w) > 1]
        if len(words) < 2:
            return False, clean, "ФИО должно состоять минимум из двух слов (Фамилия и Имя)."

        if any(char.isdigit() for char in clean):
            return False, clean, "ФИО не должно содержать цифры."

        normalized = " ".join(words)
        return True, normalized, None

    def validate_phone(self, raw_phone: str) -> Tuple[bool, str, Optional[str]]:
        """
        Нормализация и валидация телефонного номера:
        - Преобразование к федеральному формату +7XXXXXXXXXX
        Возвращает: (is_valid, normalized_phone, error_message)
        """
        clean = re.sub(r"\D", "", raw_phone or "")
        if len(clean) == 11 and clean[0] in ("7", "8"):
            normalized = "+7" + clean[1:]
            return True, normalized, None
        elif len(clean) == 10 and clean[0] == "9":
            normalized = "+7" + clean
            return True, normalized, None
        return False, raw_phone or "", "Некорректный формат телефона. Введите номер в формате +79001234567."

    def validate_birth_date(self, raw_date: str) -> Tuple[bool, str, Optional[str]]:
        """
        Валидация даты рождения и возраста 18+:
        - Формат ДД.ММ.ГГГГ
        - Контроль совершеннолетия
        Возвращает: (is_valid, normalized_date, error_message)
        """
        clean = (raw_date or "").strip()
        try:
            dt = datetime.strptime(clean, "%d.%m.%Y")
        except ValueError:
            return False, clean, "Не удалось распознать дату. Введите дату в формате ДД.ММ.ГГГГ (например: 15.03.1990)."

        if dt > datetime.now():
            return False, clean, "Дата рождения не может быть в будущем."

        age_days = (datetime.now() - dt).days
        if age_days < 18 * 365.25:
            return False, clean, "Для трудоустройства на предприятие необходимо достижение 18 лет."

        return True, clean, None

    def validate_question(self, raw_question: str) -> Tuple[bool, str, Optional[str]]:
        """Валидация вопроса соискателя кадровой службе (до 1000 символов)."""
        clean = (raw_question or "").strip()
        if not clean:
            return False, "", "Вопрос не может быть пустым."
        if len(clean) > 1000:
            return False, clean, "Вопрос слишком длинный (максимум 1000 символов)."
        return True, clean, None

    # =========================================================================
    # 2. УМНЫЕ ТРИГГЕРЫ И БИЗНЕС-ПРАВИЛА
    # =========================================================================

    def is_driver_vacancy(self, vacancy: str) -> bool:
        """Проверяет, относится ли вакансия к водительскому составу."""
        v = (vacancy or "").lower()
        return any(k in v for k in self.DRIVER_VACANCIES)

    def is_no_experience(self, experience: str) -> bool:
        """Проверяет, указал ли соискатель отсутствие опыта."""
        e = (experience or "").lower().strip()
        if not e:
            return True
        if e in ("0", "нет", "-", "—"):
            return True
        if any(k in e for k in self.NO_EXP_KEYWORDS):
            return True
        if re.search(r"(нет|0)", e):
            return True
        return False

    def should_offer_training(self, vacancy: str, experience: str) -> bool:
        """
        Умный триггер бесплатного обучения:
        Срабатывает, если вакансия водительская (трамвай/троллейбус) и кандидат без опыта.
        """
        return self.is_driver_vacancy(vacancy) and self.is_no_experience(experience)

    # =========================================================================
    # 3. ПРОВЕРКА ОГРАНИЧЕНИЙ И ДУБЛИКАТОВ (152-ФЗ / РЕГЛАМЕНТ)
    # =========================================================================

    def check_can_apply(
        self,
        user_id: str | int,
        platform: str = "tg"
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """
        Проверка возможности подачи анкеты:
        1. Проверка блокировки в ЧС (is_blocked).
        2. Проверка активной анкеты на рассмотрении (unprocessed).
        3. Проверка 3-месячного кулдауна после отказа (rejected_cooldown).
        Возвращает: (can_apply, reason_code, details_dict)
        """
        uid = str(user_id)
        if self.db.is_blocked(uid):
            return False, "banned", {"message": "Пользователь заблокирован в системе."}

        can_apply, reason, info = self.db.check_candidate_can_apply(uid, platform=platform)
        return can_apply, reason, info or {}

    # =========================================================================
    # 4. РЕГИСТРАЦИЯ И ОПЕРАЦИИ С АНКЕТАМИ
    # =========================================================================

    def register_candidate(
        self,
        data: Dict[str, Any],
        platform: str = "tg"
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Регистрация новой анкеты со всеми 16 шагами:
        Сохраняет запись в SQLite через ResumeDB и вычисляет метаданные (триггер обучения).
        Возвращает: (ticket_id, metadata_dict)
        """
        user_id = str(data.get("user_id", ""))
        full_name = data.get("full_name", "")
        phone = data.get("phone", "")
        vacancy = data.get("vacancy", "")
        experience = data.get("experience", "Без опыта")
        consent_ts = data.get("consent_timestamp") or datetime.now().strftime("%d.%m.%Y %H:%M")
        is_test = bool(data.get("is_test", False))

        ticket_id = self.db.add_candidate(
            platform=platform,
            user_id=user_id,
            full_name=full_name,
            phone=phone,
            vacancy=vacancy,
            experience=experience,
            is_test=is_test,
            consent_timestamp=consent_ts,
            birth_date=data.get("birth_date", ""),
            city=data.get("city", "Ульяновск"),
            driver_license=data.get("license_categories") or data.get("driver_license", "Нет"),
            education=data.get("education") or data.get("education_level", "Среднее"),
            relocation=data.get("relocation", "Нет"),
            dormitory=data.get("dormitory", "Нет"),
            shift_work=data.get("shift_work", "Да"),
            medical_restrictions=data.get("medical_restrictions", "Нет"),
            criminal_record=data.get("criminal_record", "Нет"),
            source=data.get("source", "Бот предприятия"),
            extra_info=data.get("extra_info") or data.get("additional_info", ""),
            raw_data_json=json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else "{}"
        )

        offer_training = self.should_offer_training(vacancy, experience)
        meta = {
            "ticket_id": ticket_id,
            "offer_training": offer_training,
            "full_name": full_name,
            "phone": phone,
            "vacancy": vacancy,
            "consent_timestamp": consent_ts,
            "platform": platform
        }
        logger.info(f"Зарегистрирована анкета #{ticket_id} ({platform}): {full_name}, {vacancy}, обучение={offer_training}")
        return ticket_id, meta

    def submit_inquiry(
        self,
        user_id: str | int,
        question_text: str,
        platform: str = "tg",
        ticket_id: Optional[int] = None,
        full_name: str = "",
        phone: str = "",
        vacancy: str = "",
        is_test: bool = False,
        consent_timestamp: Optional[str] = None,
        cooldown_seconds: Optional[int] = None
    ) -> Tuple[bool, str, Optional[int]]:
        """
        Обработка вопроса соискателя кадровой службе (/ask):
        1. Проверка антифлуда (кулдаун между вопросами).
        2. Валидация текста.
        3. Запись в базу данных.
        Возвращает: (success, status_message, inquiry_id)
        """
        uid = str(user_id)
        if cooldown_seconds is not None and cooldown_seconds > 0:
            can_send, wait_sec = self.db.check_inquiry_cooldown(uid, cooldown_seconds)
            if not can_send:
                minutes_left = max(1, (wait_sec + 59) // 60)
                return False, f"⏳ Вы сможете задать следующий вопрос через {minutes_left} мин.", None

        is_valid, clean_q, err = self.validate_question(question_text)
        if not is_valid:
            return False, err or "Некорректный вопрос.", None

        inquiry_id = self.db.add_inquiry(
            platform=platform,
            user_id=uid,
            question_text=clean_q,
            ticket_id=ticket_id,
            full_name=full_name,
            phone=phone,
            vacancy=vacancy,
            is_test=is_test,
            consent_timestamp=consent_timestamp
        )
        return True, "Вопрос принят", inquiry_id

    def revoke_consent_152fz(
        self,
        user_id: str | int,
        platform: str = "tg"
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        """
        Отзыв согласия и уничтожение персональных данных соискателя (ст. 21 152-ФЗ):
        1. Поиск активной анкеты соискателя.
        2. Запись в электронный журнал уничтожения (Приказ РКН № 179).
        3. Безвозвратное затирание анкеты в базе данных.
        """
        uid = str(user_id)
        cand = self.db.get_candidate_by_user_id(uid, platform=platform)
        if not cand:
            return False, None, None

        ticket_id = cand[0]
        # Регистрация факта уничтожения в журнале РКН № 179
        if hasattr(self.db, 'log_pdn_destruction'):
            self.db.log_pdn_destruction(
                candidate_id=ticket_id,
                user_id=uid,
                platform=platform,
                reason="Отзыв согласия субъектом персональных данных (ст. 21 152-ФЗ)",
                act_number=f"{ticket_id}-УПД"
            )
        self.db.delete_candidate_by_user(uid, platform=platform)
        destroyed_at = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        logger.info(f"ПДн соискателя {uid} (#{ticket_id}) уничтожены по ст. 21 152-ФЗ в {destroyed_at}")
        return True, ticket_id, destroyed_at


# Синглтон сервиса для импорта в контроллеры
candidate_service = CandidateService()
