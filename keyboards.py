# -*- coding: utf-8 -*-
"""
Модуль разметки клавиатур и экранных меню для бота
МУП «Ульяновскэлектротранс».
"""
from typing import Tuple, List
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from common import get_git_info

from common import db, CONFIG, SYSTEM_METRICS, get_uptime

def make_candidate_main_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Заполнить анкету на работу", callback_data="cand_start_apply")
    builder.button(text="📑 Моя анкета", callback_data="cand_my_application")
    builder.button(text="💬 Связаться с кадровиком / Задать вопрос", callback_data="cand_ask_question")
    builder.button(text="📚 Частые вопросы и ответы (FAQ)", callback_data="cand_faq_menu")
    builder.button(text="📞 Контакты отдела кадров", callback_data="cand_hr_contacts")
    builder.button(text="📄 Политика конфиденциальности", callback_data="cand_privacy_policy")
    builder.adjust(1, 1, 1, 1, 1, 1)
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

# заметка
    builder.button(text="📝 Заметка", callback_data=f"cand_note_{ticket_id}")
    builder.button(text="📅 Дата встречи", callback_data=f"invite_custom_{ticket_id}")
    builder.button(text="🗑 Удалить", callback_data=f"del_ask_{ticket_id}")

    # Кнопки навигации назад:
    builder.button(text="⬅️ К списку анкет", callback_data="admin_list_all")
    builder.button(text="🏠 Панель HR", callback_data="admin_stats")
    builder.button(text="📅 Дата встречи", callback_data=f"invite_custom_{ticket_id}")
    builder.button(text="🗑 Удалить", callback_data=f"del_ask_{ticket_id}")

    # 5. Переключатель ЧС
    if is_cand_blocked:
        builder.button(text="✅ Снять ЧС", callback_data=f"unblock_cand_{ticket_id}")
    else:
        builder.button(text="⛔ В ЧС", callback_data=f"block_cand_{ticket_id}")

    if status == "Архив":
        builder.adjust(1, 2, 2, 2, 2, 1)
    else:
        builder.adjust(1, 2, 2, 1, 2, 1)
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
    builder.button(text="📑 Все резюме", callback_data="admin_list_all")
    builder.button(text="📥 Новые", callback_data="admin_list_new")
    builder.button(text="🟡 В работе", callback_data="admin_list_in_progress")
    builder.button(text="📦 Архив", callback_data="admin_list_archive")
    builder.button(text="📊 Статистика", callback_data="admin_stats")
    builder.button(text="📥 Выгрузить базу (Excel)", callback_data="hr_export_excel")
    builder.adjust(3, 2, 1, 1, 1)

    # Управление набором по вакансиям (для кадровиков)
    builder.button(text="🎯 Вакансии и набор (Вкл/Выкл)", callback_data="tech_vacancies_menu")

    is_enabled = db.get_admin_notify_status(user_id)
    toggle_text = "🔔 Уведы в ЛС: [ВКЛ]" if is_enabled else "🔕 Уведы в ЛС: [ВЫКЛ]"
    builder.button(text=toggle_text, callback_data="toggle_dm_notify")
    builder.adjust(3, 2, 1, 1)
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
    builder.button(text="🎯 Вакансии и набор (Вкл/Выкл)", callback_data="tech_vacancies_menu")

    cur_cd = int(db.get_setting("cooldown_seconds", str(CONFIG.get("COOLDOWN_SECONDS", 1200))))
    cd_min = cur_cd // 60
    builder.button(text=f"⏱ Таймаут между вопросами: [{cd_min} мин]", callback_data="tech_toggle_cooldown")

    maint_text = "🟡 Выключить ТО" if CONFIG.get("MAINTENANCE_MODE") else "🟢 Включить ТО (пауза)"
    builder.button(text=maint_text, callback_data="tech_toggle_maint")
    builder.button(text="📋 Системные логи (/logs)", callback_data="tech_show_logs")
    builder.button(text="🔄 Перезапустить службу бота", callback_data="tech_restart_ask")

    # Переключение веток Git (4 пробела для if/else, 8 пробелов для кнопок внутри)
    git_info = get_git_info()
    if git_info["branch"] == "beta":
        builder.button(text="🛡 Переключить на MAIN", callback_data="tech_switch_main")
    else:
        builder.button(text="🧪 Переключить на BETA", callback_data="tech_switch_beta")

    builder.button(text="🔄 Обновить код (git pull)", callback_data="tech_git_pull")
    builder.button(text="🔄 Обновить статус", callback_data="tech_refresh")
    builder.adjust(1)
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
    git_info = get_git_info()
    
    tg_status = "🟢 Онлайн"
    vk_status = "🟢 Онлайн" if SYSTEM_METRICS["vk_online"] else ("🟡 Не настроен" if not CONFIG.get("VK_GROUP_TOKEN") else "🔴 Ошибка")
    max_status = "🟢 Онлайн" if SYSTEM_METRICS["max_online"] else ("🟡 Не настроен" if not CONFIG.get("MAX_BOT_TOKEN") else "🔴 Ошибка")
    db_status = "🟢 Исправна" if db.check_health() else "🔴 Сбой целостности"
    maint_status = "🟡 Включен (прием на паузе)" if CONFIG.get("MAINTENANCE_MODE") else "🟢 Работа в штатном режиме"
    env_mode = CONFIG.get("ENVIRONMENT", "TEST")

    text = (
        "🛠 <b>ТЕХНИЧЕСКИЙ МОНИТОРИНГ И ОБСЛУЖИВАНИЕ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡 <b>Режим работы:</b> <b>{env_mode}</b> " + ("(Законный режим: проверка анкет, 3 мес. отказ, антифлуд)\n" if env_mode == "PROD" else "(Режим отладки: все ограничения сняты)\n") +
        f"⏱ <b>Аптайм:</b> <code>{get_uptime()}</code>\n"
        f"⚙️ <b>Режим обслуживания:</b> <b>{maint_status}</b>\n"
        f"🌿 <b>Ветка Git:</b> <code>{git_info['branch']}</code> ({git_info['badge']})\n"
        f"🏷 <b>Коммит:</b> <code>{git_info['commit']}</code>\n"
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

