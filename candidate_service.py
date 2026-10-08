# -*- coding: utf-8 -*-
"""
Единый сервисный слой подбора персонала МУП «Ульяновскэлектротранс» (Core Service).
Унифицирует валидацию, проверку ограничений (152-ФЗ, дубликаты, кулдауны)
и регистрацию соискателей для всех каналов связи (Telegram, ВКонтакте, МАКС).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple, Union

import texts
from texts import clean_html

logger = logging.getLogger("CANDIDATE_SERVICE")


class CandidateService:
    """
    Сервис бизнес-логики соискателей: валидация, синхронизация 16 шагов,
    проверка дубликатов, антифлуд и соблюдение 152-ФЗ РФ.
    """

    DRIVER_VACANCIES: Tuple[str, ...] = (
        "водитель трамвая",
        "водитель троллейбуса",
        "водитель",
    )

    NO_EXP_KEYWORDS: Tuple[str, ...] = (
        "без опыта",
        "нет опыта",
        "не работал",
        "отсутствует",
        "без стажа",
        "нет стажа",
        "не имеется",
    )

    def __init__(self, db_instance: Optional[Any] = None) -> None:
        self._db = db_instance

    @property
    def db(self) -> Any:
        """Ленивая инициализация ссылки на БД для предотвращения циклических импортов."""
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
        - Допустимы только буквы, дефисы и пробелы
        - Автоматическая капитализация каждого слова
        Возвращает: (is_valid, normalized_name, error_message)
        """
        clean = (raw_name or "").strip()
        if not clean:
            return False, "", clean_html(texts.ERR_INVALID_NAME)

        words = [w for w in clean.split() if len(w) > 1]
        if len(words) < 2:
            return False, clean, clean_html(texts.ERR_INVALID_NAME)

        # Проверка на отсутствие цифр и спецсимволов
        if not re.match(r"^[A-Za-zА-Яа-яЁё\s\-]+$", clean):
            return False, clean, "ФИО должно содержать только буквы и дефис."

        normalized = " ".join(w.capitalize() for w in words)
        return True, normalized, None

    def validate_phone(self, raw_phone: str) -> Tuple[bool, str, Optional[str]]:
        """
        Нормализация и валидация телефонного номера:
        - Преобразование к единому федеральному стандарту +7XXXXXXXXXX
        Возвращает: (is_valid, normalized_phone, error_message)
        """
        clean = re.sub(r"\D", "", raw_phone or "")
        if len(clean) == 11 and clean[0] in ("7", "8"):
            normalized = "+7" + clean[1:]
            return True, normalized, None
        elif len(clean) == 10 and clean[0] == "9":
            normalized = "+7" + clean
            return True, normalized, None

        return False, raw_phone or "", clean_html(texts.ERR_INVALID_PHONE)

    def validate_birth_date(self, raw_date: str) -> Tuple[bool, str, Optional[str]]:
        """
        Юридически точная валидация даты рождения и совершеннолетия (18+):
        - Поддерживает разделители точки, дефиса и слэша
        - Расчет возраста по календарной дате согласно ТК РФ
        Возвращает: (is_valid, normalized_date, error_message)
        """
        clean = (raw_date or "").strip().replace("/", ".").replace("-", ".")
        try:
            dt = datetime.strptime(clean, "%d.%m.%Y")
        except ValueError:
            return False, clean, clean_html(texts.ERR_INVALID_DATE_FORMAT)

        today = date.today()
        birth_d = dt.date()

        if birth_d > today:
            return False, clean, "Дата рождения не может быть в будущем."

        # Точный расчет возраста в полных годах
        age = today.year - birth_d.year - ((today.month, today.day) < (birth_d.month, birth_d.day))
        if age < 18:
            return False, clean, clean_html(texts.ERR_UNDERAGE)
        if age > 100:
            return False, clean, "Пожалуйста, проверьте правильность года рождения."

        return True, dt.strftime("%d.%m.%Y"), None

    def validate_question(self, raw_question: str) -> Tuple[bool, str, Optional[str]]:
        """Валидация вопроса соискателя кадровой службе (до 1000 символов)."""
        clean = (raw_question or "").strip()
        if not clean:
            return False, "", "Вопрос не может быть пустым."
        if len(clean) > 1000:
            return False, clean, clean_html(texts.ERR_QUESTION_TOO_LONG)
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
        if e in ("0", "нет", "-", "—", "без опыта"):
            return True
        if any(k in e for k in self.NO_EXP_KEYWORDS):
            return True
        if re.search(r"\b(нет|0)\b", e):
            return True
        return False

    def should_offer_training(self, vacancy: str, experience: str) -> bool:
        """
        Умный триггер бесплатного обучения:
        Срабатывает, если вакансия водительская (трамвай/троллейбус) и соискатель без опыта.
        """
        return self.is_driver_vacancy(vacancy) and self.is_no_experience(experience)

    # =========================================================================
    # 3. ПРОВЕРКА ОГРАНИЧЕНИЙ И ДУБЛИКАТОВ (152-ФЗ / РЕГЛАМЕНТ)
    # =========================================================================

    def check_can_apply(
        self,
        user_id: Union[str, int],
        platform: str = "tg",
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

    async def async_check_can_apply(
        self,
        user_id: Union[str, int],
        platform: str = "tg",
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """Асинхронная неблокирующая проверка возможности подачи анкеты."""
        return await asyncio.to_thread(self.check_can_apply, user_id, platform)

    # =========================================================================
    # 4. РЕГИСТРАЦИЯ И ОПЕРАЦИИ С АНКЕТАМИ
    # =========================================================================

    def register_candidate(
        self,
        data: Dict[str, Any],
        platform: str = "tg",
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Регистрация новой анкеты со всеми 16 полями:
        Сохраняет запись в SQLite через ResumeDB и вычисляет метаданные (триггер обучения).
        Возвращает: (ticket_id, metadata_dict)
        """
        user_id = str(data.get("user_id", ""))
        full_name = str(data.get("full_name", ""))
        phone = str(data.get("phone", ""))
        vacancy = str(data.get("vacancy", ""))
        experience = str(data.get("experience", "Без опыта"))
        consent_ts = str(data.get("consent_timestamp") or datetime.now().strftime("%d.%m.%Y %H:%M"))
        is_test = bool(data.get("is_test", False))

        # Нормализация категорий прав (если передан список)
        raw_lic = data.get("license_categories") or data.get("driver_license", "Нет")
        if isinstance(raw_lic, list):
            driver_license = ", ".join(str(x) for x in raw_lic) if raw_lic else "Нет"
        else:
            driver_license = str(raw_lic)

        ticket_id = self.db.add_candidate(
            platform=platform,
            user_id=user_id,
            full_name=full_name,
            phone=phone,
            vacancy=vacancy,
            experience=experience,
            is_test=is_test,
            consent_timestamp=consent_ts,
            birth_date=str(data.get("birth_date", "")),
            city=str(data.get("city", "Ульяновск")),
            driver_license=driver_license,
            education=str(data.get("education") or data.get("education_level", "Среднее")),
            relocation=str(data.get("relocation", "Нет")),
            dormitory=str(data.get("dormitory", "Нет")),
            shift_work=str(data.get("shift_work", "Да")),
            medical_restrictions=str(data.get("medical_restrictions", "Нет")),
            criminal_record=str(data.get("criminal_record", "Нет")),
            source=str(data.get("source", "Бот предприятия")),
            extra_info=str(data.get("extra_info") or data.get("additional_info", "")),
            raw_data_json=json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else "{}",
        )

        offer_training = self.should_offer_training(vacancy, experience)
        meta: Dict[str, Any] = {
            "ticket_id": ticket_id,
            "offer_training": offer_training,
            "full_name": full_name,
            "phone": phone,
            "vacancy": vacancy,
            "consent_timestamp": consent_ts,
            "platform": platform,
        }
        logger.info(
            "Зарегистрирована анкета #%s (%s): %s, %s, обучение=%s",
            ticket_id, platform, full_name, vacancy, offer_training
        )
        return ticket_id, meta

    async def async_register_candidate(
        self,
        data: Dict[str, Any],
        platform: str = "tg",
    ) -> Tuple[int, Dict[str, Any]]:
        """Асинхронная неблокирующая регистрация анкеты."""
        return await asyncio.to_thread(self.register_candidate, data, platform)

    def submit_inquiry(
        self,
        user_id: Union[str, int],
        question_text: str,
        platform: str = "tg",
        ticket_id: Optional[int] = None,
        full_name: str = "",
        phone: str = "",
        vacancy: str = "",
        is_test: bool = False,
        consent_timestamp: Optional[str] = None,
        cooldown_seconds: Optional[int] = None,
    ) -> Tuple[bool, str, Optional[int]]:
        """
        Обработка вопроса соискателя кадровой службе (/ask):
        1. Проверка антифлуда (кулдаун между вопросами).
        2. Валидация текста вопроса.
        3. Запись обращения в базу данных.
        Возвращает: (success, status_message, inquiry_id)
        """
        uid = str(user_id)
        if cooldown_seconds is not None and cooldown_seconds > 0:
            can_send, wait_sec = self.db.check_inquiry_cooldown(uid, cooldown_seconds)
            if not can_send:
                minutes_left = max(1, (wait_sec + 59) // 60)
                msg = clean_html(texts.format_inquiry_cooldown(minutes_left))
                return False, msg, None

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
            consent_timestamp=consent_timestamp,
        )
        return True, "Вопрос принят", inquiry_id

    async def async_submit_inquiry(self, *args, **kwargs) -> Tuple[bool, str, Optional[int]]:
        """Асинхронная неблокирующая подача вопроса."""
        return await asyncio.to_thread(self.submit_inquiry, *args, **kwargs)

    def revoke_consent_152fz(
        self,
        user_id: Union[str, int],
        platform: str = "tg",
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        """
        Отзыв согласия и уничтожение персональных данных соискателя (ст. 21 152-ФЗ):
        1. Поиск активной анкеты соискателя.
        2. Запись в электронный журнал уничтожения (Приказ РКН № 179).
        3. Безвозвратное стирание данных в БД.
        """
        uid = str(user_id)
        cand = self.db.get_candidate_by_user_id(uid, platform=platform)
        if not cand:
            return False, None, None

        ticket_id = int(cand[0])
        # Регистрация факта уничтожения в журнале РКН № 179
        self.db.log_pdn_destruction(
            candidate_id=ticket_id,
            user_id=uid,
            platform=platform,
            reason="Отзыв согласия субъектом персональных данных (ст. 21 152-ФЗ)",
            act_number=f"{ticket_id}-УПД",
        )
        self.db.delete_candidate_by_user(uid, platform=platform)
        destroyed_at = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        logger.info("ПДн соискателя %s (#%s) уничтожены по ст. 21 152-ФЗ в %s", uid, ticket_id, destroyed_at)
        return True, ticket_id, destroyed_at

    async def async_revoke_consent_152fz(
        self,
        user_id: Union[str, int],
        platform: str = "tg",
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        """Асинхронный неблокирующий отзыв согласия соискателя."""
        return await asyncio.to_thread(self.revoke_consent_152fz, user_id, platform)


# Синглтон сервиса для импорта во все контроллеры и шлюзы
candidate_service: CandidateService = CandidateService()