"""
Сквозной E2E-тест полного жизненного цикла диалога FSM соискателя (Testing Pyramid: End-to-End Layer).
Симулирует цепочку:
Старт -> Согласие 152-ФЗ -> Выбор вакансии -> Ввод и валидация ФИО -> Дата рождения -> Телефон -> Город/Опыт -> Сохранение в SQLite -> Очистка контекста FSM.
"""

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from common import CandidateForm
from database import ResumeDB
from services.candidate_service import CandidateService
from services.domain_rules import (
    validate_candidate_age_for_role,
    validate_phone_number,
    validate_russian_fio,
)


@pytest.mark.asyncio
async def test_candidate_fsm_e2e_lifecycle(memory_db: ResumeDB):
    """
    Полная симуляция сквозного сценария подачи анкеты через конечный автомат (FSM):
    1. Инициализация хранилища FSM (MemoryStorage).
    2. Переход по состояниям опросника с доменной валидацией на каждом шаге.
    3. Финализация анкеты и сохранение в изолированную базу данных SQLite.
    4. Очистка контекста FSM и верификация персистентности данных.
    """
    bot_id = 8863560607
    user_id = 777888999
    chat_id = 777888999

    storage = MemoryStorage()
    key = StorageKey(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    state = FSMContext(storage=storage, key=key)

    # -------------------------------------------------------------------------
    # Шаг 1: Старт и согласие на обработку персональных данных (152-ФЗ РФ)
    # -------------------------------------------------------------------------
    await state.set_state(CandidateForm.waiting_consent)
    assert await state.get_state() == CandidateForm.waiting_consent.state

    # Пользователь нажимает "Согласен"
    consent_timestamp = "21.10.2026 14:00"
    await state.update_data(
        consent_timestamp=consent_timestamp,
        user_id=str(user_id),
        platform="tg",
    )

    # -------------------------------------------------------------------------
    # Шаг 2: Выбор вакансии
    # -------------------------------------------------------------------------
    await state.set_state(CandidateForm.vacancy)
    assert await state.get_state() == CandidateForm.vacancy.state

    selected_vacancy = "Водитель трамвая (обучение со стипендией)"
    await state.update_data(vacancy=selected_vacancy)

    # -------------------------------------------------------------------------
    # Шаг 3: Ввод и доменная валидация ФИО
    # -------------------------------------------------------------------------
    await state.set_state(CandidateForm.full_name)
    raw_input_fio = "петров василий сергеевич"
    is_valid_fio, norm_fio, fio_err = validate_russian_fio(raw_input_fio)
    assert is_valid_fio is True
    assert norm_fio == "Петров Василий Сергеевич"
    assert fio_err is None

    await state.update_data(full_name=norm_fio)

    # -------------------------------------------------------------------------
    # Шаг 4: Ввод даты рождения и проверка возрастного ценза (ст. 26 196-ФЗ: 21+)
    # -------------------------------------------------------------------------
    await state.set_state(CandidateForm.birth_date)
    raw_birth_date = "15.06.2001"
    is_valid_age, age, age_err = validate_candidate_age_for_role(raw_birth_date, selected_vacancy)
    assert is_valid_age is True
    assert age >= 21
    assert age_err is None

    await state.update_data(birth_date=raw_birth_date, age=age)

    # -------------------------------------------------------------------------
    # Шаг 5: Ввод и нормализация телефона соискателя
    # -------------------------------------------------------------------------
    await state.set_state(CandidateForm.phone)
    raw_input_phone = "8 (902) 123-45-67"
    is_valid_phone, norm_phone, phone_err = validate_phone_number(raw_input_phone)
    assert is_valid_phone is True
    assert norm_phone == "+79021234567"
    assert phone_err is None

    await state.update_data(phone=norm_phone)

    # -------------------------------------------------------------------------
    # Шаг 6: Город, опыт работы и готовность к сменному графику
    # -------------------------------------------------------------------------
    await state.set_state(CandidateForm.city)
    await state.update_data(city="г. Ульяновск")

    await state.set_state(CandidateForm.experience)
    await state.update_data(experience="Без опыта")

    await state.set_state(CandidateForm.schedule)
    await state.update_data(shift_work="Да, готов к графику 2/2")

    # -------------------------------------------------------------------------
    # Шаг 7: Экран подтверждения анкеты (Review)
    # -------------------------------------------------------------------------
    await state.set_state(CandidateForm.confirm_review)
    assert await state.get_state() == CandidateForm.confirm_review.state

    fsm_data = await state.get_data()
    assert fsm_data["full_name"] == "Петров Василий Сергеевич"
    assert fsm_data["phone"] == "+79021234567"
    assert fsm_data["birth_date"] == "15.06.2001"
    assert fsm_data["vacancy"] == selected_vacancy
    assert fsm_data["experience"] == "Без опыта"

    # -------------------------------------------------------------------------
    # Шаг 8: Сохранение анкеты в БД и очистка контекста FSM
    # -------------------------------------------------------------------------
    # Очищаем контекст FSM (по аналогии с обработчиком cb_submit_final)
    await state.clear()
    assert await state.get_state() is None
    assert await state.get_data() == {}

    # Сохраняем данные через единый сервисный слой
    candidate_svc = CandidateService(db_instance=memory_db)
    ticket_id, meta = candidate_svc.register_candidate(fsm_data, platform="tg")

    assert ticket_id > 0
    # Проверка срабатывания умного триггера бесплатного обучения для водителя без опыта
    assert meta.get("offer_training") is True

    # -------------------------------------------------------------------------
    # Шаг 9: Верификация сохранения в реляционной базе данных
    # -------------------------------------------------------------------------
    stored = memory_db.get_candidate(ticket_id)
    assert stored is not None
    assert stored[0] == ticket_id
    assert stored[1] == "tg"
    assert stored[2] == str(user_id)
    assert stored[3] == "Петров Василий Сергеевич"
    assert stored[4] == "+79021234567"
    assert stored[5] == selected_vacancy
    assert stored[6] == "Без опыта"
    assert stored[7] == "Новая"  # Начальный статус в кадровой воронке
    assert stored[10] == "15.06.2001"
    assert stored[11] == "г. Ульяновск"
