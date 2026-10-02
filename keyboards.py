from datetime import datetime
# -*- coding: utf-8 -*-
"""
Модуль разметки клавиатур и экранных меню для бота
МУП «Ульяновскэлектротранс».
"""
from typing import Tuple, List
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from common import db, CONFIG, SYSTEM_METRICS, get_uptime, is_tech_admin, is_hr_admin

def make_candidate_main_keyboard(user_id: int = 0) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Заполнить анкету на работу", callback_data="cand_start_apply")
    builder.button(text="📑 Моя анкета", callback_data="cand_my_application")
    builder.button(text="💬 Связаться с кадровиком / Задать вопрос", callback_data="cand_ask_question")
    builder.button(text="📚 Частые вопросы и ответы (FAQ)", callback_data="cand_faq_menu")
    builder.button(text="📞 Контакты отдела кадров", callback_data="cand_hr_contacts")
    builder.button(text="🚨 Экстренная техподдержка", callback_data="cand_support")
    builder.button(text="📄 Политика конфиденциальности", callback_data="cand_privacy_policy")

    super_id = CONFIG.get("SUPER_ADMIN_ID")
    is_tech = bool(user_id and (user_id == super_id or is_tech_admin(user_id)))
    is_hr = bool(user_id and (user_id == super_id or is_hr_admin(user_id)))

    if is_tech or is_hr:
        if is_tech:
            builder.button(text="🛠 Панель инженера (/tech)", callback_data="tech_refresh")
            builder.button(text="🚀 Управление Git (/git)", callback_data="tech_git_menu")
        if is_hr:
            builder.button(text="📋 Кадровая панель (/admin)", callback_data="admin_stats")
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
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="📱 Поделиться номером телефона", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def make_ticket_keyboard(ticket_id: int) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    cand = db.get_candidate(ticket_id)
    platform = cand[1] if cand else "tg"
    user_id = str(cand[2]) if cand else ""
    status = cand[7] if cand and len(cand) > 7 else "Новая"
    is_cand_blocked = db.is_blocked(user_id) if user_id else False

    # 1. Прямой живой чат через бота
    builder.button(text="🟢 Начать прямой диалог", callback_data=f"live_dlg_cand_{ticket_id}")

    # 2. Прямой переход в профиль/чат
    if platform == "tg" and user_id.lstrip("-").isdigit():
        builder.button(text="👤 Открыть чат в TG", url=f"tg://user?id={user_id}")
    elif platform == "vk":
        builder.button(text="👤 Открыть профиль VK", url=f"https://vk.com/id{user_id}")
    elif platform == "max":
        builder.button(text="👤 Открыть чат МАКС", url=f"https://myteam.mail.ru/chat/{user_id}")

    # 3. Одноразовое сообщение
    builder.button(text="💬 Написать через бота", callback_data=f"cand_msg_{ticket_id}")

    # 4. Решения по статусу (моментальная смена статуса в БД и на кнопках)
    if status == "В работе":
        builder.button(text="🟡 В работе (активно)", callback_data=f"status_noop_{ticket_id}")
    else:
        builder.button(text="🟡 В работу", callback_data=f"status_{ticket_id}_В работе")

    if "Приглашен" in status:
        builder.button(text="🟢 Приглашен (активно)", callback_data=f"status_noop_{ticket_id}")
    else:
        builder.button(text="🟢 Пригласить", callback_data=f"status_{ticket_id}_Приглашен")

    if status == "Отказ":
        builder.button(text="🔴 Отказ (активно)", callback_data=f"status_noop_{ticket_id}")
    else:
        builder.button(text="🔴 Отказ", callback_data=f"status_{ticket_id}_Отказ")

    if status == "Архив":
        builder.button(text="📦 В архиве (активно)", callback_data=f"status_noop_{ticket_id}")
        builder.button(text="↩️ Вернуть в работу", callback_data=f"status_{ticket_id}_В работе")
    else:
        builder.button(text="📦 В архив", callback_data=f"status_{ticket_id}_Архив")

    # 5. Заметка к анкете
    #builder.button(text="📝 Заметка", callback_data=f"cand_note_{ticket_id}")

    # 6. Дата встречи и удаление (без дублирования!)
    builder.button(text="📅 Дата встречи", callback_data=f"invite_custom_{ticket_id}")
    builder.button(text="🗑 Удалить", callback_data=f"del_ask_{ticket_id}")

    # 7. Переключатель ЧС
    if is_cand_blocked:
        builder.button(text="✅ Снять ЧС", callback_data=f"unblock_cand_{ticket_id}")
    else:
        builder.button(text="⛔ В ЧС", callback_data=f"block_cand_{ticket_id}")

    # 8. Навигация к списку анкет (без дублирования и лишней кнопки Панель HR)
    builder.button(text="⬅️ К списку анкет", callback_data="admin_list_all")

    if status == "Архив":
        builder.adjust(1, 2, 2, 1, 2, 1, 2, 1, 1)
    else:
        builder.adjust(1, 2, 2, 1, 2, 2, 1, 1)
    return builder.as_markup()

def make_cand_reply_keyboard(ticket_id: int = 0) -> types.InlineKeyboardMarkup:
    """Инлайн-кнопка для соискателя в Telegram для ответа на сообщение отдела кадров."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Ответить кадровику", callback_data=f"cand_reply_hr_{ticket_id}")
    return builder.as_markup()


def make_inquiry_admin_keyboard(inquiry_id: int) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    inq = db.get_inquiry(inquiry_id)
    platform = inq[2] if inq else "tg"
    user_id = str(inq[3]) if inq else ""
    is_inq_blocked = db.is_blocked(user_id) if user_id else False

    builder.button(text="🟢 Начать прямой диалог", callback_data=f"live_dlg_inq_{inquiry_id}")

    if platform == "tg" and user_id.lstrip("-").isdigit():
        builder.button(text="👤 Открыть чат в TG", url=f"tg://user?id={user_id}")
    elif platform == "vk":
        builder.button(text="👤 Профиль VK", url=f"https://vk.com/id{user_id}")
    elif platform == "max":
        builder.button(text="👤 Профиль МАКС", url=f"https://myteam.mail.ru/chat/{user_id}")

    builder.button(text="💬 Ответить в чат", callback_data=f"inq_reply_{inquiry_id}")
    builder.button(text="⏹ Завершить обращение", callback_data=f"inq_close_{inquiry_id}")
    
    if is_inq_blocked:
        builder.button(text="✅ Снять ЧС", callback_data=f"inq_unblock_{inquiry_id}")
    else:
        builder.button(text="⛔ В ЧС", callback_data=f"inq_block_{inquiry_id}")

    builder.adjust(1, 2, 1, 1)
    return builder.as_markup()

def make_admin_menu_keyboard(user_id: int) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    # 1 ряд — фильтры резюме
    builder.button(text="📑 Все резюме", callback_data="admin_list_all")
    builder.button(text="📥 Новые", callback_data="admin_list_new")
    builder.button(text="🟡 В работе", callback_data="admin_list_in_progress")
    builder.button(text="📦 Архив", callback_data="admin_list_archive")
    
    # 2 ряд — полезные действия вместо дубля статистики:
    builder.button(text="📊 Скачать Excel (/export)", callback_data="hr_export_excel")
    builder.button(text="🔄 Обновить", callback_data="admin_stats")
    
    # 3 ряд — управление
    builder.button(text="🎯 Вакансии и набор", callback_data="tech_vacancies_menu")

    cur_cd = int(db.get_setting("cooldown_seconds", str(CONFIG.get("COOLDOWN_SECONDS", 1200))))
    cd_label = f"{cur_cd // 60} мин" if cur_cd > 0 else "0 (выкл)"
    builder.button(text=f"⏱ Таймаут: [{cd_label}]", callback_data="hr_toggle_cooldown")

    is_enabled = db.get_admin_notify_status(user_id)
    toggle_text = "🔔 Уведы в ЛС: [ВКЛ]" if is_enabled else "🔕 Уведы в ЛС: [ВЫКЛ]"
    builder.button(text=toggle_text, callback_data="toggle_dm_notify")
    # 4 ряд тесты 
    # Кнопки проверки группы и принудительной синхронизации:
    builder.button(text="🛡 Проверить группу кадров тестого", callback_data="hr_check_group_perms")
    builder.button(text="🔄 Синхронизировать права", callback_data="hr_sync_group_admins")
    builder.adjust(2, 2, 2, 1, 2)
    return builder.as_markup()

def make_admins_menu_keyboard(all_admins: list) -> types.InlineKeyboardMarkup:
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

def make_remove_admin_keyboard(all_admins: list) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    super_id = CONFIG.get("SUPER_ADMIN_ID")
    for adm_id, role in all_admins:
        if adm_id == super_id:
            continue
        role_label = "Кадры" if role == "hr" else "Инженер"
        builder.button(text=f"❌ {adm_id} ({role_label})", callback_data=f"adm_del_id_{adm_id}")
    builder.button(text="⬅️ Назад в меню", callback_data="adm_ui_refresh")
    builder.adjust(1)
    return builder.as_markup()



def make_faq_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎓 Обучение (водитель трамвая/троллейбуса)", callback_data="faq_item_faq_training")
    builder.button(text="🏠 Жилье и общежитие", callback_data="faq_item_faq_housing")
    builder.button(text="💰 Зарплата, график и льготная пенсия", callback_data="faq_item_faq_salary")
    builder.button(text="📄 Необходимые документы", callback_data="faq_item_faq_docs")
    builder.button(text="🏢 Контакты отдела кадров", callback_data="cand_hr_contacts")
    builder.button(text="⬅️ В главное меню", callback_data="cand_back_to_menu")
    builder.adjust(1)
    return builder.as_markup()

def make_tech_menu_keyboard(user_id: int = 0) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    env = CONFIG.get("ENVIRONMENT", "TEST")
    toggle_env_text = "🛡 Включить PROD (152-ФЗ, лимиты)" if env == "TEST" else "🧪 Включить TEST (без ограничений)"
    builder.button(text=toggle_env_text, callback_data="tech_toggle_env")
    builder.button(text="💾 Управление бэкапами (/backups)", callback_data="tech_manage_backups")
    maint_text = "🟡 Выключить ТО" if CONFIG.get("MAINTENANCE_MODE") else "🟢 Включить ТО (пауза)"
    builder.button(text=maint_text, callback_data="tech_toggle_maint")
    builder.button(text="📋 Системные логи (/logs)", callback_data="tech_show_logs")
    builder.button(text="🔄 Обновить статус", callback_data="tech_refresh")
    builder.button(text="🚀 Панель GitHub и деплой (/git)", callback_data="tech_git_menu")
    builder.adjust(1)
    return builder.as_markup()

make_tech_git_keyboard = None

def make_git_menu_keyboard(current_branch: str = "main") -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    other_branch = "beta" if current_branch == "main" else "main"
    builder.button(text="🔄 Обновить и перезапустить (pull + restart)", callback_data="git_action_pull_restart")
    builder.button(text=f"🔀 Переключить на ветку «{other_branch}»", callback_data=f"git_action_switch_{other_branch}")
    builder.button(text="⏪ Откат на 1 коммит (HEAD~1)", callback_data="git_action_rollback_ask")
    builder.button(text="⏩ Вернуть актуальный коммит", callback_data="git_action_forward_ask")
    builder.button(text="⚡ Быстрый рестарт", callback_data="git_action_restart_only")
    builder.button(text="🔄 Обновить статус Git", callback_data="tech_git_menu")
    builder.button(text="⬅️ Назад в /tech", callback_data="tech_refresh")
    builder.adjust(1, 1, 2, 2, 1)
    return builder.as_markup()

def make_tests_menu_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🧪 Отправить анкету из TG", callback_data="test_send_tg")
    builder.button(text="🧪 Отправить анкету из VK", callback_data="test_send_vk")
    builder.button(text="❓ Отправить вопрос кандидата", callback_data="test_send_inquiry")
    builder.button(text="📡 Проверить шлюз ВКонтакте", callback_data="test_ping_vk")
    builder.button(text="♻️ Сбросить мою анкету (/reset)", callback_data="test_do_reset")
    builder.button(text="📋 Системные логи (/logs)", callback_data="tech_show_logs")
    builder.adjust(1, 1, 1, 1, 1, 1)
    return builder.as_markup()

def get_tech_screen_data(user_id: int = 0) -> Tuple[str, types.InlineKeyboardMarkup]:
    tg_status = "🟢 Онлайн"
    vk_status = "🟢 Онлайн" if SYSTEM_METRICS["vk_online"] else ("🟡 Не настроен" if not CONFIG.get("VK_GROUP_TOKEN") else "🔴 Ошибка")
    max_status = "🟢 Онлайн" if SYSTEM_METRICS["max_online"] else ("🟡 Не настроен" if not CONFIG.get("MAX_BOT_TOKEN") else "🔴 Ошибка")
    db_status = "🟢 Исправна" if db.check_health() else "🔴 Сбой целостности"
    maint_status = "🟡 Включен (прием на паузе)" if CONFIG.get("MAINTENANCE_MODE") else "🟢 Работа в штатном режиме"
    env_mode = CONFIG.get("ENVIRONMENT", "TEST")
    check_time = datetime.now().strftime("%H:%M:%S")

    text = (
        "🛠 <b>ТЕХНИЧЕСКИЙ МОНИТОРИНГ И ОБСЛУЖИВАНИЕ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡 <b>Режим работы:</b> <b>{env_mode}</b> " + ("(Законный режим: проверка анкет, 3 мес. отказ, антифлуд)\n" if env_mode == "PROD" else "(Режим отладки: все ограничения сняты)\n") +
        f"⏱ <b>Аптайм:</b> <code>{get_uptime()}</code>\n"
        f"🔄 <b>Проверено в:</b> <code>{check_time}</code>\n"
        f"⚙️ <b>Режим обслуживания:</b> <b>{maint_status}</b>\n"
        f"💾 <b>База данных:</b> <b>{db_status}</b>\n"
        f"⚠️ <b>Ошибок сети/вызовов:</b> <code>{SYSTEM_METRICS['errors_count']}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📡 <b>Статус платформ:</b>\n"
        f"• Telegram: {tg_status}\n"
        f"• ВКонтакте: {vk_status}\n"
        f"• МАКС (MyTeam): {max_status}\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    return text, make_tech_menu_keyboard(user_id)

make_tech_git_keyboard = make_git_menu_keyboard


# ==============================================================================
# КЛАВИАТУРЫ 16 ШАГОВ АНКЕТЫ СОИСКАТЕЛЯ И 152-ФЗ
# ==============================================================================

def make_consent_survey_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Даю согласие", callback_data="cand_consent_apply")
    builder.button(text="❌ Не даю согласие", callback_data="cand_consent_refuse")
    builder.button(text="📄 Политика обработки данных", callback_data="cand_privacy_policy")
    builder.adjust(2, 1)
    return builder.as_markup()

def make_step_nav_kb(can_skip: bool = False) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_skip:
        builder.button(text="⏭ Пропустить", callback_data="cand_nav_skip")
    builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    builder.adjust(1 if can_skip else 2, 2 if can_skip else 0)
    return builder.as_markup()

def make_step2_birthdate_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 01.01.1980", callback_data="bd_01.01.1980")
    builder.button(text="📅 01.01.1985", callback_data="bd_01.01.1985")
    builder.button(text="📅 01.01.1990", callback_data="bd_01.01.1990")
    builder.button(text="📅 01.01.1995", callback_data="bd_01.01.1995")
    builder.button(text="📅 01.01.2000", callback_data="bd_01.01.2000")
    builder.button(text="✏️ Ввести вручную", callback_data="bd_manual")
    builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    builder.adjust(2, 2, 2, 2)
    return builder.as_markup()

def make_step4_city_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏙 Ульяновск", callback_data="city_Ульяновск")
    builder.button(text="🏙 Димитровград", callback_data="city_Димитровград")
    builder.button(text="🏙 Новоульяновск", callback_data="city_Новоульяновск")
    builder.button(text="🏙 Барыш", callback_data="city_Барыш")
    builder.button(text="🏙 Другой город", callback_data="city_manual")
    builder.button(text="✏️ Вписать вручную", callback_data="city_manual")
    builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    builder.adjust(2, 2, 2, 2)
    return builder.as_markup()

def make_step5_vacancies_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚋 Водитель трамвая", callback_data="vac_tram")
    builder.button(text="🚎 Водитель троллейбуса", callback_data="vac_troll")
    builder.button(text="🎫 Кондуктор", callback_data="vac_conductor")
    builder.button(text="🔧 Слесарь по ремонту ПС", callback_data="vac_slesar")
    builder.button(text="⚡ Электромонтёр контактной сети", callback_data="vac_electro")
    builder.button(text="📋 Другая должность", callback_data="vac_other")
    builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    builder.adjust(1, 1, 1, 1, 1, 1, 2)
    return builder.as_markup()

def make_step6_license_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data="lic_yes")
    builder.button(text="❌ Нет", callback_data="lic_no")
    builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    builder.adjust(2, 2)
    return builder.as_markup()

def make_step6_1_categories_kb(selected: list = None) -> types.InlineKeyboardMarkup:
    selected = selected or []
    builder = InlineKeyboardBuilder()
    cats = ["A", "B", "C", "D", "E", "Трамвай", "Троллейбус"]
    for c in cats:
        mark = "✅ " if c in selected else ""
        builder.button(text=f"{mark}{c}", callback_data=f"cat_toggle_{c}")
    builder.button(text="✏️ Вписать вручную", callback_data="cat_manual")
    builder.button(text="✅ Готово", callback_data="cat_done")
    builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    builder.adjust(3, 2, 2, 2, 2)
    return builder.as_markup()

def make_step7_experience_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data="exp_yes")
    builder.button(text="❌ Нет", callback_data="exp_no")
    builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    builder.adjust(2, 2)
    return builder.as_markup()

def make_step8_education_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎓 Среднее", callback_data="edu_Среднее")
    builder.button(text="🎓 Среднее специальное", callback_data="edu_Среднее специальное")
    builder.button(text="🎓 Неоконченное высшее", callback_data="edu_Неоконченное высшее")
    builder.button(text="🎓 Высшее", callback_data="edu_Высшее")
    builder.button(text="🎓 Учёная степень", callback_data="edu_Учёная степень")
    builder.button(text="✏️ Вписать свой вариант", callback_data="edu_manual")
    builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    builder.adjust(2, 2, 2, 2)
    return builder.as_markup()

def make_step9_relocation_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data="reloc_yes")
    builder.button(text="❌ Нет", callback_data="reloc_no")
    builder.button(text="✏️ Указать город переезда", callback_data="reloc_manual")
    builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    builder.adjust(2, 1, 2)
    return builder.as_markup()

def make_step10_dormitory_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data="dorm_yes")
    builder.button(text="❌ Нет", callback_data="dorm_no")
    builder.button(text="✏️ Добавить комментарий", callback_data="dorm_manual")
    builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    builder.adjust(2, 1, 2)
    return builder.as_markup()

def make_step11_schedule_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data="sched_yes")
    builder.button(text="❌ Нет", callback_data="sched_no")
    builder.button(text="✏️ Указать предпочтения", callback_data="sched_manual")
    builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    builder.adjust(2, 1, 2)
    return builder.as_markup()

def make_step12_health_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Нет", callback_data="health_no")
    builder.button(text="✅ Да", callback_data="health_yes")
    builder.button(text="✏️ Описать противопоказания", callback_data="health_manual")
    builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    builder.adjust(2, 1, 2)
    return builder.as_markup()

def make_step13_criminal_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Нет", callback_data="crim_no")
    builder.button(text="✅ Да", callback_data="crim_yes")
    builder.button(text="✏️ Описать", callback_data="crim_manual")
    builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    builder.adjust(2, 1, 2)
    return builder.as_markup()

def make_step14_source_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🌐 Сайт предприятия", callback_data="src_Сайт предприятия")
    builder.button(text="✈️ Telegram-бот", callback_data="src_Telegram-бот")
    builder.button(text="🟦 ВКонтакте", callback_data="src_ВКонтакте")
    builder.button(text="💼 МАКС / VK Teams", callback_data="src_МАКС / VK Teams")
    builder.button(text="🏢 Центр занятости", callback_data="src_Центр занятости")
    builder.button(text="👥 Знакомые", callback_data="src_Знакомые")
    builder.button(text="✏️ Вписать свой вариант", callback_data="src_manual")
    builder.button(text="⏭ Пропустить", callback_data="src_skip")
    builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    builder.adjust(2, 2, 2, 2, 2)
    return builder.as_markup()

def make_step16_confirm_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтверждаю", callback_data="cand_submit_final")
    builder.button(text="✏️ Изменить данные", callback_data="cand_edit_fields_menu")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    builder.adjust(1, 1, 1)
    return builder.as_markup()

def make_step16_edit_menu_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    fields = [
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

def make_mydata_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Запросить уточнение", callback_data="cand_ask_question")
    builder.button(text="❌ Отозвать согласие", callback_data="cand_revoke_ask")
    builder.button(text="🏠 Главное меню", callback_data="cand_back_to_menu")
    builder.adjust(1, 1, 1)
    return builder.as_markup()

def make_revoke_confirm_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, отозвать согласие", callback_data="cand_revoke_confirm")
    builder.button(text="❌ Отмена", callback_data="cand_back_to_menu")
    builder.adjust(1, 1)
    return builder.as_markup()
