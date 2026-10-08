# -*- coding: utf-8 -*-
"""
Модуль разметки клавиатур и экранных меню для бота
МУП «Ульяновскэлектротранс».
Реализован в строгом соответствии с архитектурным стандартом SSOT.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

import texts
from common import (
    CONFIG,
    SYSTEM_METRICS,
    db,
    get_uptime,
    is_hr_admin,
    is_tech_admin,
)


# ==============================================================================
# 1. ГЛАВНОЕ МЕНЮ И КЛАВИАТУРЫ ПОЛЬЗОВАТЕЛЯ
# ==============================================================================

def make_candidate_main_keyboard(user_id: int = 0) -> types.InlineKeyboardMarkup:
    """
    Формирует главное интерактивное меню соискателя с адаптивными кнопками
    служебных панелей для авторизованных сотрудников (HR / Tech).
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_APPLY, callback_data="cand_start_apply")
    builder.button(text=texts.BTN_MY_APP, callback_data="cand_my_application")
    builder.button(text=texts.BTN_ASK_QUESTION, callback_data="cand_ask_question")
    builder.button(text=texts.BTN_FAQ, callback_data="cand_faq_menu")
    builder.button(text=texts.BTN_CONTACTS, callback_data="cand_hr_contacts")
    builder.button(text=texts.BTN_SUPPORT, callback_data="cand_support")
    builder.button(text=texts.BTN_PRIVACY, callback_data="cand_privacy_policy")

    super_id = CONFIG.get("SUPER_ADMIN_ID")
    is_tech = bool(user_id and (user_id == super_id or is_tech_admin(user_id)))
    is_hr = bool(user_id and (user_id == super_id or is_hr_admin(user_id)))

    if is_tech or is_hr:
        if is_tech:
            builder.button(text=texts.BTN_TECH_PANEL, callback_data="tech_refresh")
            builder.button(text=texts.BTN_GIT_PANEL, callback_data="tech_git_menu")
        if is_hr:
            builder.button(text=texts.BTN_HR_PANEL, callback_data="admin_stats")

        if is_tech and is_hr:
            builder.adjust(1, 1, 1, 1, 1, 1, 1, 2, 1)
        elif is_tech:
            builder.adjust(1, 1, 1, 1, 1, 1, 1, 2)
        else:
            builder.adjust(1, 1, 1, 1, 1, 1, 1, 1)
    else:
        builder.adjust(1, 1, 1, 1, 1, 1, 1)

    return builder.as_markup()


def make_phone_reply_keyboard() -> types.ReplyKeyboardMarkup:
    """Кнопка быстрой передачи контакта соискателя (Шаг 3)."""
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text=texts.BTN_SHARE_PHONE, request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def make_cand_reply_keyboard(ticket_id: int = 0) -> types.InlineKeyboardMarkup:
    """Инлайн-кнопка для соискателя для ответа на разовое сообщение кадровика."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_DIALOG_REPLY, callback_data=f"cand_reply_hr_{ticket_id}")
    return builder.as_markup()


def make_faq_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура разделов базы знаний предприятия (FAQ)."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_FAQ_TRAINING, callback_data="faq_item_faq_training")
    builder.button(text=texts.BTN_FAQ_HOUSING, callback_data="faq_item_faq_housing")
    builder.button(text=texts.BTN_FAQ_SALARY, callback_data="faq_item_faq_salary")
    builder.button(text=texts.BTN_FAQ_DOCS, callback_data="faq_item_faq_docs")
    builder.button(text=texts.BTN_FAQ_CONTACTS, callback_data="cand_hr_contacts")
    builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
    builder.adjust(1)
    return builder.as_markup()


def make_mydata_kb() -> types.InlineKeyboardMarkup:
    """Клавиатура прав субъекта персональных данных (ст. 14, 21 152-ФЗ)."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_REQUEST_UPDATE, callback_data="cand_ask_question")
    builder.button(text=texts.BTN_REVOKE_CONSENT, callback_data="cand_revoke_ask")
    builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
    builder.adjust(1, 1, 1)
    return builder.as_markup()


def make_revoke_confirm_kb() -> types.InlineKeyboardMarkup:
    """Клавиатура подтверждения отзыва согласия на обработку ПДн."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, отозвать согласие", callback_data="cand_revoke_confirm")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_back_to_menu")
    builder.adjust(1, 1)
    return builder.as_markup()


# ==============================================================================
# 2. КЛАВИАТУРЫ 16 ШАГОВ АНКЕТЫ СОИСКАТЕЛЯ
# ==============================================================================

def make_consent_survey_kb() -> types.InlineKeyboardMarkup:
    """Шаг 0: Согласие на обработку персональных данных (152-ФЗ)."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CONSENT_YES, callback_data="cand_consent_apply")
    builder.button(text=texts.BTN_CONSENT_NO, callback_data="cand_consent_refuse")
    builder.button(text=texts.BTN_READ_PRIVACY, callback_data="cand_privacy_policy")
    builder.adjust(2, 1)
    return builder.as_markup()


def make_step_nav_kb(can_skip: bool = False) -> types.InlineKeyboardMarkup:
    """Универсальная навигационная панель внутри шагов анкеты."""
    builder = InlineKeyboardBuilder()
    if can_skip:
        builder.button(text=texts.BTN_NAV_SKIP, callback_data="cand_nav_skip")
    builder.button(text=texts.BTN_NAV_BACK, callback_data="cand_nav_back")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")

    if can_skip:
        builder.adjust(1, 2)
    else:
        builder.adjust(2)
    return builder.as_markup()


def make_step2_birthdate_kb() -> types.InlineKeyboardMarkup:
    """Шаг 2: Выбор или ручной ввод даты рождения."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_BD_1980, callback_data="bd_01.01.1980")
    builder.button(text=texts.BTN_BD_1985, callback_data="bd_01.01.1985")
    builder.button(text=texts.BTN_BD_1990, callback_data="bd_01.01.1990")
    builder.button(text=texts.BTN_BD_1995, callback_data="bd_01.01.1995")
    builder.button(text=texts.BTN_BD_2000, callback_data="bd_01.01.2000")
    builder.button(text=texts.BTN_MANUAL_INPUT, callback_data="bd_manual")
    builder.button(text=texts.BTN_NAV_BACK, callback_data="cand_nav_back")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    builder.adjust(2, 2, 2, 2)
    return builder.as_markup()


def make_step4_city_kb() -> types.InlineKeyboardMarkup:
    """Шаг 4: Город фактического проживания."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CITY_ULYANOVSK, callback_data="city_Ульяновск")
    builder.button(text=texts.BTN_CITY_DIMITROVGRAD, callback_data="city_Димитровград")
    builder.button(text=texts.BTN_CITY_NOVOULE, callback_data="city_Новоульяновск")
    builder.button(text=texts.BTN_CITY_BARYSH, callback_data="city_Барыш")
    builder.button(text="🏙 Другой город (вписать)", callback_data="city_manual")
    builder.button(text=texts.BTN_NAV_BACK, callback_data="cand_nav_back")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    builder.adjust(2, 2, 1, 2)
    return builder.as_markup()


def make_step5_vacancies_kb() -> types.InlineKeyboardMarkup:
    """Шаг 5: Выбор вакансии с учетом открытых наборов предприятия."""
    builder = InlineKeyboardBuilder()
    closed_json = db.get_setting("closed_vacancies", "[]")
    try:
        closed_set: Set[str] = set(json.loads(closed_json))
    except Exception:
        closed_set = set()

    all_vacancies: List[Tuple[str, str]] = [
        ("🚋 Водитель трамвая", "vac_tram"),
        ("🚎 Водитель троллейбуса", "vac_troll"),
        ("🎫 Кондуктор", "vac_conductor"),
        ("🔧 Слесарь по ремонту ПС", "vac_slesar"),
        ("⚡ Электромонтёр контактной сети", "vac_electro"),
    ]

    for title, cb in all_vacancies:
        # Проверяем, не закрыт ли прием на эту должность
        clean_title = title.split(" ", 1)[-1]
        if clean_title not in closed_set:
            builder.button(text=title, callback_data=cb)

    builder.button(text="📋 Другая должность", callback_data="vac_other")
    builder.button(text=texts.BTN_NAV_BACK, callback_data="cand_nav_back")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    builder.adjust(1)
    return builder.as_markup()


def make_step6_license_kb() -> types.InlineKeyboardMarkup:
    """Шаг 6: Наличие водительского удостоверения."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_YES, callback_data="lic_yes")
    builder.button(text=texts.BTN_NO, callback_data="lic_no")
    builder.button(text=texts.BTN_NAV_BACK, callback_data="cand_nav_back")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    builder.adjust(2, 2)
    return builder.as_markup()


def make_step6_1_categories_kb(selected: Optional[List[str]] = None) -> types.InlineKeyboardMarkup:
    """Шаг 6.1: Мультивыбор категорий водительских прав."""
    selected = selected or []
    builder = InlineKeyboardBuilder()
    cats = [
        texts.BTN_CAT_A,
        texts.BTN_CAT_B,
        texts.BTN_CAT_C,
        texts.BTN_CAT_D,
        texts.BTN_CAT_E,
        texts.BTN_CAT_TRAM,
        texts.BTN_CAT_TROLLEY,
    ]
    for c in cats:
        mark = "✅ " if c in selected else ""
        builder.button(text=f"{mark}{c}", callback_data=f"cat_toggle_{c}")

    builder.button(text=texts.BTN_MANUAL_INPUT, callback_data="cat_manual")
    builder.button(text=texts.BTN_CAT_DONE, callback_data="cat_done")
    builder.button(text=texts.BTN_NAV_BACK, callback_data="cand_nav_back")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    builder.adjust(3, 2, 2, 2, 2)
    return builder.as_markup()


def make_step7_experience_kb() -> types.InlineKeyboardMarkup:
    """Шаг 7: Наличие опыта работы."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_YES, callback_data="exp_yes")
    builder.button(text=texts.BTN_NO, callback_data="exp_no")
    builder.button(text=texts.BTN_NAV_BACK, callback_data="cand_nav_back")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    builder.adjust(2, 2)
    return builder.as_markup()


def make_step8_education_kb() -> types.InlineKeyboardMarkup:
    """Шаг 8: Уровень образования."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_EDU_SECONDARY, callback_data="edu_Среднее")
    builder.button(text=texts.BTN_EDU_SPECIAL, callback_data="edu_Среднее специальное")
    builder.button(text=texts.BTN_EDU_INCOMPLETE_HIGHER, callback_data="edu_Неоконченное высшее")
    builder.button(text=texts.BTN_EDU_HIGHER, callback_data="edu_Высшее")
    builder.button(text=texts.BTN_EDU_DEGREE, callback_data="edu_Учёная степень")
    builder.button(text="✏️ Вписать свой вариант", callback_data="edu_manual")
    builder.button(text=texts.BTN_NAV_BACK, callback_data="cand_nav_back")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    builder.adjust(2, 2, 2, 2)
    return builder.as_markup()


def make_step9_relocation_kb() -> types.InlineKeyboardMarkup:
    """Шаг 9: Готовность к переезду в г. Ульяновск."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_YES, callback_data="reloc_yes")
    builder.button(text=texts.BTN_NO, callback_data="reloc_no")
    builder.button(text="✏️ Указать город переезда", callback_data="reloc_manual")
    builder.button(text=texts.BTN_NAV_BACK, callback_data="cand_nav_back")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    builder.adjust(2, 1, 2)
    return builder.as_markup()


def make_step10_dormitory_kb() -> types.InlineKeyboardMarkup:
    """Шаг 10: Потребность в общежитии предприятия."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_YES, callback_data="dorm_yes")
    builder.button(text=texts.BTN_NO, callback_data="dorm_no")
    builder.button(text="✏️ Добавить комментарий", callback_data="dorm_manual")
    builder.button(text=texts.BTN_NAV_BACK, callback_data="cand_nav_back")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    builder.adjust(2, 1, 2)
    return builder.as_markup()


def make_step11_schedule_kb() -> types.InlineKeyboardMarkup:
    """Шаг 11: Готовность к сменному графику."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_YES, callback_data="sched_yes")
    builder.button(text=texts.BTN_NO, callback_data="sched_no")
    builder.button(text="✏️ Указать предпочтения", callback_data="sched_manual")
    builder.button(text=texts.BTN_NAV_BACK, callback_data="cand_nav_back")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    builder.adjust(2, 1, 2)
    return builder.as_markup()


def make_step12_health_kb() -> types.InlineKeyboardMarkup:
    """Шаг 12: Медицинские противопоказания."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_NO, callback_data="health_no")
    builder.button(text=texts.BTN_YES, callback_data="health_yes")
    builder.button(text="✏️ Описать ограничения", callback_data="health_manual")
    builder.button(text=texts.BTN_NAV_BACK, callback_data="cand_nav_back")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    builder.adjust(2, 1, 2)
    return builder.as_markup()


def make_step13_criminal_kb() -> types.InlineKeyboardMarkup:
    """Шаг 13: Сведения о судимости по ст. 86 УК РФ."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_NO, callback_data="crim_no")
    builder.button(text=texts.BTN_YES, callback_data="crim_yes")
    builder.button(text="✏️ Описать детали", callback_data="crim_manual")
    builder.button(text=texts.BTN_NAV_BACK, callback_data="cand_nav_back")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    builder.adjust(2, 1, 2)
    return builder.as_markup()


def make_step14_source_kb() -> types.InlineKeyboardMarkup:
    """Шаг 14: Источник информации о вакансии."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🌐 Сайт предприятия", callback_data="src_Сайт предприятия")
    builder.button(text="✈️ Telegram-бот", callback_data="src_Telegram-бот")
    builder.button(text="🟦 ВКонтакте", callback_data="src_ВКонтакте")
    builder.button(text="💼 МАКС / VK Teams", callback_data="src_МАКС / VK Teams")
    builder.button(text="🏢 Центр занятости", callback_data="src_Центр занятости")
    builder.button(text="👥 Знакомые", callback_data="src_Знакомые")
    builder.button(text="✏️ Вписать свой вариант", callback_data="src_manual")
    builder.button(text=texts.BTN_NAV_SKIP, callback_data="src_skip")
    builder.button(text=texts.BTN_NAV_BACK, callback_data="cand_nav_back")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    builder.adjust(2, 2, 2, 2, 2)
    return builder.as_markup()


def make_step16_confirm_kb() -> types.InlineKeyboardMarkup:
    """Шаг 16: Подтверждение достоверности и отправка анкеты в кадры."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_NAV_CONFIRM, callback_data="cand_submit_final")
    builder.button(text=texts.BTN_NAV_EDIT, callback_data="cand_edit_fields_menu")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    builder.adjust(1, 1, 1)
    return builder.as_markup()


def make_step16_edit_menu_kb() -> types.InlineKeyboardMarkup:
    """Шаг 16.1: Интерактивный выбор поля для точечного изменения."""
    builder = InlineKeyboardBuilder()
    fields: List[Tuple[str, str]] = [
        ("👤 ФИО", "edit_fio"),
        ("🎂 Дата рождения", "edit_birth"),
        ("📞 Телефон", "edit_phone"),
        ("🏙 Город", "edit_city"),
        ("💼 Вакансия", "edit_vac"),
        ("🚗 Водительские права", "edit_lic"),
        ("📝 Опыт работы", "edit_exp"),
        ("🎓 Образование", "edit_edu"),
        ("🏠 Переезд", "edit_reloc"),
        ("🛏 Общежитие", "edit_dorm"),
        ("🕐 Сменный график", "edit_sched"),
        ("⚕️ Противопоказания", "edit_health"),
        ("⚖️ Судимость", "edit_crim"),
        ("📢 Источник", "edit_src"),
        ("📎 Дополнительно", "edit_extra"),
    ]
    for title, cb in fields:
        builder.button(text=title, callback_data=cb)
    builder.button(text="⬅️ Назад к проверке", callback_data="edit_back_review")
    builder.adjust(2, 2, 2, 2, 2, 2, 2, 1, 1)
    return builder.as_markup()


# ==============================================================================
# 3. КЛАВИАТУРЫ КАДРОВОЙ СЛУЖБЫ (HR PANEL)
# ==============================================================================

def make_admin_menu_keyboard(user_id: int) -> types.InlineKeyboardMarkup:
    """Главная панель кадровой службы (/hr, /admin) со сбалансированной сеткой."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📑 Все резюме", callback_data="admin_list_all")
    builder.button(text="📥 Новые", callback_data="admin_list_new")
    builder.button(text="🟡 В работе", callback_data="admin_list_in_progress")
    builder.button(text="📦 Архив", callback_data="admin_list_archive")
    builder.button(text="📊 Скачать Excel (/export)", callback_data="hr_export_excel")
    builder.button(text="🔄 Обновить сводку", callback_data="admin_stats")
    builder.button(text="🎯 Вакансии (Вкл/Выкл)", callback_data="tech_vacancies_menu")

    cur_cd = int(db.get_setting("cooldown_seconds", str(CONFIG.get("COOLDOWN_SECONDS", 1200))))
    cd_label = f"{cur_cd // 60} мин" if cur_cd > 0 else "0 (выкл)"
    builder.button(text=f"⏱ Таймаут вопросов: [{cd_label}]", callback_data="hr_toggle_cooldown")

    is_enabled = db.get_admin_notify_status(user_id)
    toggle_text = "🔔 Уведы в ЛС: [ВКЛ]" if is_enabled else "🔕 Уведы в ЛС: [ВЫКЛ]"
    builder.button(text=toggle_text, callback_data="toggle_dm_notify")

    # Сетка из 9 кнопок: 3 + 2 + 2 + 2 = 9
    builder.adjust(3, 2, 2, 2)
    return builder.as_markup()


def make_ticket_keyboard(ticket_id: int) -> types.InlineKeyboardMarkup:
    """
    Клавиатура действий кадровика под карточкой соискателя.
    Обеспечивает быстрый переход в профиль, смену статусов, заметки и ЧС.
    """
    builder = InlineKeyboardBuilder()
    cand = db.get_candidate(ticket_id)
    platform = cand[1] if cand and len(cand) > 1 else "tg"
    user_id = str(cand[2]) if cand and len(cand) > 2 else ""
    status = str(cand[7]) if cand and len(cand) > 7 else "Новая"
    is_cand_blocked = db.is_blocked(user_id) if user_id else False

    # 1. Прямой живой чат
    builder.button(text=texts.BTN_LIVE_DLG_START, callback_data=f"live_dlg_cand_{ticket_id}")

    # 2. Прямой переход в профиль и разовое сообщение через бота
    if platform == "tg" and user_id.lstrip("-").isdigit():
        builder.button(text="👤 Профиль TG", url=f"tg://user?id={user_id}")
    elif platform == "vk":
        builder.button(text="👤 Профиль VK", url=f"https://vk.com/id{user_id}")
    elif platform == "max":
        builder.button(text="👤 Чат МАКС", url=f"https://myteam.mail.ru/chat/{user_id}")
    else:
        builder.button(text="👤 Профиль", callback_data=f"cand_call_{ticket_id}")

    builder.button(text=texts.BTN_CAND_MSG, callback_data=f"cand_msg_{ticket_id}")

    # 3. Управление статусами
    if status == "В работе":
        builder.button(text="🟡 В работе (активно)", callback_data=f"status_noop_{ticket_id}")
    else:
        builder.button(text=texts.BTN_STATUS_IN_PROGRESS, callback_data=f"status_{ticket_id}_В работе")

    if "Приглашен" in status:
        builder.button(text="🟢 Приглашен (активно)", callback_data=f"status_noop_{ticket_id}")
    else:
        builder.button(text=texts.BTN_STATUS_INVITE, callback_data=f"status_{ticket_id}_Приглашен")

    if status == "Отказ":
        builder.button(text="🔴 Отказ (активно)", callback_data=f"status_noop_{ticket_id}")
    else:
        builder.button(text=texts.BTN_STATUS_REJECT, callback_data=f"status_{ticket_id}_Отказ")

    if status == "Архив":
        builder.button(text=texts.BTN_STATUS_RESTORE, callback_data=f"status_{ticket_id}_В работе")
    else:
        builder.button(text=texts.BTN_STATUS_ARCHIVE, callback_data=f"status_{ticket_id}_Архив")

    # 4. Заметки, дата встречи, удаление и блокировка
    builder.button(text=texts.BTN_CAND_NOTE, callback_data=f"cand_note_{ticket_id}")
    builder.button(text=texts.BTN_INVITE_CUSTOM, callback_data=f"invite_custom_{ticket_id}")
    builder.button(text=texts.BTN_DELETE, callback_data=f"del_ask_{ticket_id}")

    if is_cand_blocked:
        builder.button(text=texts.BTN_UNBLOCK, callback_data=f"unblock_cand_{ticket_id}")
    else:
        builder.button(text=texts.BTN_BLOCK, callback_data=f"block_cand_{ticket_id}")

    # 5. Возврат к общему списку
    builder.button(text=texts.BTN_BACK_TO_LIST, callback_data="admin_list_all")

    # Симметричная сетка: 1 (диалог) + 2 (профиль/сообщение) + 2 + 2 + 2 + 2 + 1 (назад)
    builder.adjust(1, 2, 2, 2, 2, 2, 1)
    return builder.as_markup()


def make_inquiry_admin_keyboard(inquiry_id: int) -> types.InlineKeyboardMarkup:
    """Клавиатура управления входящим обращением соискателя."""
    builder = InlineKeyboardBuilder()
    inq = db.get_inquiry(inquiry_id)
    platform = inq[2] if inq and len(inq) > 2 else "tg"
    user_id = str(inq[3]) if inq and len(inq) > 3 else ""
    is_inq_blocked = db.is_blocked(user_id) if user_id else False

    builder.button(text=texts.BTN_LIVE_DLG_START, callback_data=f"live_dlg_inq_{inquiry_id}")

    if platform == "tg" and user_id.lstrip("-").isdigit():
        builder.button(text="👤 Профиль TG", url=f"tg://user?id={user_id}")
    elif platform == "vk":
        builder.button(text="👤 Профиль VK", url=f"https://vk.com/id{user_id}")
    elif platform == "max":
        builder.button(text="👤 Профиль МАКС", url=f"https://myteam.mail.ru/chat/{user_id}")
    else:
        builder.button(text="👤 Профиль", callback_data=f"inq_call_{inquiry_id}")

    builder.button(text="💬 Ответить в чат", callback_data=f"inq_reply_{inquiry_id}")
    builder.button(text="⏹ Завершить обращение", callback_data=f"inq_close_{inquiry_id}")

    if is_inq_blocked:
        builder.button(text=texts.BTN_UNBLOCK, callback_data=f"inq_unblock_{inquiry_id}")
    else:
        builder.button(text=texts.BTN_BLOCK, callback_data=f"inq_block_{inquiry_id}")

    builder.adjust(1, 2, 2)
    return builder.as_markup()


# ==============================================================================
# 4. КЛАВИАТУРЫ ИНЖЕНЕРНОГО МОНИТОРИНГА И СЛУЖБЫ (/tech, /git, /tests)
# ==============================================================================

def make_tech_menu_keyboard(user_id: int = 0) -> types.InlineKeyboardMarkup:
    """Главная клавиатура инженерной панели мониторинга (/tech)."""
    builder = InlineKeyboardBuilder()
    env = CONFIG.get("ENVIRONMENT", "TEST")
    toggle_env_text = (
        "🛡 Включить PROD (152-ФЗ, лимиты)"
        if env == "TEST"
        else "🧪 Включить TEST (без ограничений)"
    )
    builder.button(text=toggle_env_text, callback_data="tech_toggle_env")
    builder.button(text="💾 Управление бэкапами (/backups)", callback_data="tech_manage_backups")
    maint_text = "🟡 Выключить ТО" if CONFIG.get("MAINTENANCE_MODE") else "🟢 Включить ТО (пауза)"
    builder.button(text=maint_text, callback_data="tech_toggle_maint")
    builder.button(text="📋 Системные логи (/logs)", callback_data="tech_show_logs")
    builder.button(text="🔄 Обновить статус", callback_data="tech_refresh")
    builder.button(text="🚀 Панель GitHub и деплой (/git)", callback_data="tech_git_menu")
    builder.adjust(1)
    return builder.as_markup()


def make_git_menu_keyboard(current_branch: str = "main") -> types.InlineKeyboardMarkup:
    """Клавиатура управления репозиторием Git и перезапуском службы."""
    builder = InlineKeyboardBuilder()
    other_branch = "beta" if current_branch == "main" else "main"
    builder.button(text="🔄 Обновить и перезапустить (pull + restart)", callback_data="git_action_pull_restart")
    builder.button(text=f"🔀 Переключить на ветку «{other_branch}»", callback_data=f"git_action_switch_{other_branch}")
    builder.button(text="⏪ Откат на 1 коммит (HEAD~1)", callback_data="git_action_rollback_ask")
    builder.button(text="⏩ Вернуть актуальный коммит", callback_data="git_action_forward_ask")
    builder.button(text="⚡ Быстрый рестарт", callback_data="git_action_restart_only")
    builder.button(text="🔄 Обновить статус Git", callback_data="tech_git_menu")
    builder.button(text="⬅️ Назад в /tech", callback_data="tech_refresh")

    web_url = CONFIG.get("WEB_APP_URL", "")
    if web_url and web_url.startswith("https://"):
        builder.button(text="🌐 Веб-панель (Mini App)", web_app=types.WebAppInfo(url=web_url))
    elif web_url:
        builder.button(text="🌐 Веб-панель (браузер)", url=web_url)
    else:
        builder.button(text="🌐 Веб-панель (Mini App)", callback_data="adm_webapp_info")

    builder.adjust(1, 1, 2, 2, 1, 1)
    return builder.as_markup()


def make_tests_menu_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура панели нагрузочного и интеграционного тестирования (/tests)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🧪 Отправить анкету из TG", callback_data="test_send_tg")
    builder.button(text="🧪 Отправить анкету из VK", callback_data="test_send_vk")
    builder.button(text="❓ Отправить вопрос кандидата", callback_data="test_send_inquiry")
    builder.button(text="📡 Проверить шлюз ВКонтакте", callback_data="test_ping_vk")
    builder.button(text="♻️ Сбросить мою анкету (/reset)", callback_data="test_do_reset")
    builder.button(text="📋 Системные логи (/logs)", callback_data="tech_show_logs")
    builder.adjust(1, 1, 1, 1, 1, 1)
    return builder.as_markup()


def make_admins_menu_keyboard(all_admins: List[Tuple[int, str]]) -> types.InlineKeyboardMarkup:
    """Клавиатура управления ролями и назначения сотрудников (/admins)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Назначить кадровика (HR)", callback_data="adm_ui_add_hr")
    builder.button(text="➕ Назначить инженера (Tech)", callback_data="adm_ui_add_tech")

    super_id = CONFIG.get("SUPER_ADMIN_ID")
    removable = [adm for adm, role in all_admins if adm != super_id]
    if removable:
        builder.button(text="➖ Отозвать доступ (удалить)", callback_data="adm_ui_remove_list")

    builder.button(text="🔄 Обновить список", callback_data="adm_ui_refresh")
    builder.adjust(1, 1, 1, 1)
    return builder.as_markup()


def make_remove_admin_keyboard(all_admins: List[Tuple[int, str, str]]) -> types.InlineKeyboardMarkup:
    """Клавиатура выбора сотрудника для отзыва прав доступа."""
    builder = InlineKeyboardBuilder()
    super_id = CONFIG.get("SUPER_ADMIN_ID")
    for adm_id, role, short_name in all_admins:
        if adm_id == super_id:
            continue
        role_label = "Кадры" if role == "hr" else "Инженер"
        builder.button(text=f"❌ {short_name} ({role_label})", callback_data=f"adm_del_id_{adm_id}")
    builder.button(text="⬅️ Назад в меню", callback_data="adm_ui_refresh")
    builder.adjust(1)
    return builder.as_markup()


def get_tech_screen_data(user_id: int = 0) -> Tuple[str, types.InlineKeyboardMarkup]:
    """
    Формирует полный экран технического мониторинга со сводкой здоровья базы
    данных SQLite WAL, статусом внешних шлюзов, аптаймом и расходом памяти процесса.
    """
    tg_status = "🟢 Онлайн"
    vk_status = (
        "🟢 Онлайн"
        if SYSTEM_METRICS.get("vk_online")
        else ("🟡 Не настроен" if not CONFIG.get("VK_GROUP_TOKEN") else "🔴 Ошибка")
    )
    max_status = (
        "🟢 Онлайн"
        if SYSTEM_METRICS.get("max_online")
        else ("🟡 Не настроен" if not CONFIG.get("MAX_BOT_TOKEN") else "🔴 Ошибка")
    )
    db_status = "🟢 Исправна (WAL)" if db.check_health() else "🔴 Сбой целостности"
    maint_status = (
        "🟡 Включен (прием на паузе)"
        if CONFIG.get("MAINTENANCE_MODE")
        else "🟢 Работа в штатном режиме"
    )
    env_mode = CONFIG.get("ENVIRONMENT", "TEST")
    check_time = datetime.now().strftime("%H:%M:%S")

    try:
        from common import get_process_memory_mb
        ram_mb = get_process_memory_mb()
    except Exception:
        ram_mb = 0.0

    ram_note = (
        "🟢 Норма" if ram_mb < 250 else ("🟡 Повышено" if ram_mb < 500 else "🔴 Высокое")
    )
    env_desc = (
        "(Законный режим: проверка анкет, 3 мес. отказ, антифлуд)"
        if env_mode == "PROD"
        else "(Режим отладки: все ограничения сняты)"
    )

    text = (
        "🛠 <b>ТЕХНИЧЕСКИЙ МОНИТОРИНГ И ОБСЛУЖИВАНИЕ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡 <b>Режим работы:</b> <b>{env_mode}</b> {env_desc}\n"
        f"⏱ <b>Аптайм:</b> <code>{get_uptime()}</code>\n"
        f"🧠 <b>Память (RAM):</b> <code>{ram_mb} МБ</code> ({ram_note})\n"
        f"🔄 <b>Проверено в:</b> <code>{check_time}</code>\n"
        f"⚙️ <b>Режим обслуживания:</b> <b>{maint_status}</b>\n"
        f"💾 <b>База данных:</b> <b>{db_status}</b>\n"
        f"⚠️ <b>Ошибок сети/вызовов:</b> <code>{SYSTEM_METRICS.get('errors_count', 0)}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📡 <b>Статус платформ:</b>\n"
        f"• Telegram: {tg_status}\n"
        f"• ВКонтакте: {vk_status}\n"
        f"• МАКС (MyTeam): {max_status}\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    return text, make_tech_menu_keyboard(user_id)