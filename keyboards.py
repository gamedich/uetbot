# -*- coding: utf-8 -*-
"""
Модуль разметки клавиатур и экранных меню для бота
МУП «Ульяновскэлектротранс».
"""
from typing import Tuple, List
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from common import get_git_info
from datetime import datetime
from common import db, CONFIG, SYSTEM_METRICS, get_uptime, is_tech_admin, is_hr_admin



def make_candidate_main_keyboard(user_id: int = 0) -> types.InlineKeyboardMarkup:
    """Динамическое главное меню по правам доступа (соискатель / HR / инженер / суперадмин)."""
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
    """Клавиатура карточки кандидата в HR-панели (без дублирования кнопок)."""
    builder = InlineKeyboardBuilder()
    cand = db.get_candidate(ticket_id)
    platform = cand[1] if cand else "tg"
    user_id = str(cand[2]) if cand else ""
    status = cand[7] if cand and len(cand) > 7 else "Новая"
    is_cand_blocked = db.is_blocked(user_id) if user_id else False

    # 1. Прямой чат через бота
    builder.button(text="🟢 Начать прямой диалог", callback_data=f"live_dlg_cand_{ticket_id}")

    # 2. Переход в профиль / чат
    if platform == "tg" and user_id.lstrip("-").isdigit():
        builder.button(text="👤 Открыть чат в TG", url=f"tg://user?id={user_id}")
    elif platform == "vk":
        builder.button(text="👤 Открыть профиль VK", url=f"https://vk.com/id{user_id}")
    elif platform == "max":
        builder.button(text="👤 Открыть чат МАКС", url=f"https://myteam.mail.ru/chat/{user_id}")

    # 3. Сообщение через бота
    builder.button(text="💬 Написать через бота", callback_data=f"cand_msg_{ticket_id}")

    # 4. Статусы
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

    # 5. Заметка к анкете (callback_data строго cand_note_{ticket_id})
    builder.button(text="📝 Заметка", callback_data=f"cand_note_{ticket_id}")

    # 6. Дата встречи и удаление (ровно один раз!)
    builder.button(text="📅 Дата встречи", callback_data=f"invite_custom_{ticket_id}")
    builder.button(text="🗑 Удалить", callback_data=f"del_ask_{ticket_id}")

    # 7. Чёрный список
    if is_cand_blocked:
        builder.button(text="✅ Снять ЧС", callback_data=f"unblock_cand_{ticket_id}")
    else:
        builder.button(text="⛔ В ЧС", callback_data=f"block_cand_{ticket_id}")

    # 8. Навигация
    builder.button(text="⬅️ К списку анкет", callback_data="admin_list_all")
    builder.button(text="🏠 Панель HR", callback_data="admin_stats")

    if status == "Архив":
        builder.adjust(1, 2, 2, 1, 2, 1, 2, 1, 2)
    else:
        builder.adjust(1, 2, 2, 1, 2, 2, 1, 2)
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
    builder.button(text="🎯 Вакансии и набор (Вкл/Выкл)", callback_data="tech_vacancies_menu")

    cur_cd = int(db.get_setting("cooldown_seconds", str(CONFIG.get("COOLDOWN_SECONDS", 1200))))
    cd_label = f"{cur_cd // 60} мин" if cur_cd > 0 else "0 (выкл)"
    builder.button(text=f"⏱ Таймаут вопросов: [{cd_label}]", callback_data="hr_toggle_cooldown")

    is_enabled = db.get_admin_notify_status(user_id)
    toggle_text = "🔔 Уведы в ЛС: [ВКЛ]" if is_enabled else "🔕 Уведы в ЛС: [ВЫКЛ]"
    builder.button(text=toggle_text, callback_data="toggle_dm_notify")
    builder.adjust(3, 2, 2, 1)
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
    """Технический мониторинг — только системные параметры и кнопка перехода в Git."""
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
def make_tech_git_keyboard(current_branch: str = "beta") -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    other_branch = "main" if current_branch == "beta" else "beta"
    builder.button(text="🔄 Обновить и перезапустить (pull + restart)", callback_data="git_action_pull_restart")
    builder.button(text=f"🔀 Переключить на ветку «{other_branch}»", callback_data=f"git_action_switch_{other_branch}")
    builder.button(text="⏪ Откат коммита (HEAD~1)", callback_data="git_action_rollback_ask")
    builder.button(text="⚡ Быстрый рестарт", callback_data="git_action_restart_only")
    builder.button(text="🔄 Обновить статус Git", callback_data="tech_git_menu")
    builder.button(text="⬅️ Назад в /tech", callback_data="tech_refresh")
    builder.adjust(1, 1, 2, 2)
    return builder.as_markup()
def make_git_menu_keyboard(current_branch: str = "beta") -> types.InlineKeyboardMarkup:
    """Клавиатура управления Git и обновлениями."""
    builder = InlineKeyboardBuilder()
    other_branch = "main" if current_branch == "beta" else "beta"
    builder.button(text="🔄 Обновить и перезапустить (pull + restart)", callback_data="git_action_pull_restart")
    builder.button(text=f"🔀 Переключить на ветку «{other_branch}»", callback_data=f"git_action_switch_{other_branch}")
    builder.button(text="⏪ Откат коммита (HEAD~1)", callback_data="git_action_rollback_ask")
    builder.button(text="⚡ Быстрый рестарт", callback_data="git_action_restart_only")
    builder.button(text="🔄 Обновить статус Git", callback_data="tech_git_menu")
    builder.button(text="⬅️ Назад в /tech", callback_data="tech_refresh")
    builder.adjust(1, 1, 2, 2)
    return builder.as_markup()

# Псевдоним на случай старых вызовов
make_tech_git_keyboard = make_git_menu_keyboard
#клавиатура паннели 
def make_git_menu_keyboard(current_branch: str = "main") -> types.InlineKeyboardMarkup:
    """Клавиатура управления Git с откатом и возвратом на актуальный коммит."""
    builder = InlineKeyboardBuilder()
    other_branch = "beta" if current_branch == "main" else "main"
    builder.button(text="🔄 Обновить и перезапустить (pull + restart)", callback_data="git_action_pull_restart")
    builder.button(text=f"🔀 Переключить на «{other_branch}»", callback_data=f"git_action_switch_{other_branch}")
    builder.button(text="⏪ Откат на 1 коммит (HEAD~1)", callback_data="git_action_rollback_ask")
    builder.button(text="⏩ Вернуть актуальный коммит", callback_data="git_action_forward_ask")
    builder.button(text="⚡ Быстрый рестарт", callback_data="git_action_restart_only")
    builder.button(text="🔄 Обновить статус Git", callback_data="tech_git_menu")
    builder.button(text="⬅️ Назад в /tech", callback_data="tech_refresh")
    builder.adjust(1, 1, 2, 2, 1)
    return builder.as_markup()

make_tech_git_keyboard = make_git_menu_keyboard
