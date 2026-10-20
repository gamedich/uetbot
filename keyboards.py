"""
Модуль разметки клавиатур и экранных меню для бота
МУП «Ульяновскэлектротранс».
"""

from datetime import datetime
from typing import List, Tuple

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from common import (
    CONFIG,
    SYSTEM_METRICS,
    db,
    get_open_vacancies,
    get_uptime,
    get_vacancy_icon,
    is_hr_admin,
    is_maintenance_active,
    is_tech_admin,
    logger,
)


def get_valid_webapp_url(path: str = "") -> str:
    """
    Возвращает строго валидный HTTPS URL для Telegram WebApp.
    Telegram Bot API блокирует любые http:// ссылки для WebAppInfo ошибкой BUTTON_URL_INVALID.
    """
    url = str(CONFIG.get("WEB_APP_URL") or "").strip()
    if not url.startswith("https://"):
        url = "https://213.226.124.135.sslip.io/"
    url = url.rstrip("/")
    if path:
        return f"{url}/{path.lstrip('/')}"
    return url


def make_candidate_persistent_reply_keyboard() -> types.ReplyKeyboardMarkup:
    """
    Закрепленная внизу экрана эргономичная панель быстрых действий (Reply-клавиатура).
    Позволяет соискателю в 1 клик открыть интерактивную форму анкеты или проверить статус.
    """
    web_url = get_valid_webapp_url("tg")
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [
                types.KeyboardButton(text="🌐 Заполнить анкету на сайте", web_app=types.WebAppInfo(url=web_url)),
                types.KeyboardButton(text="📊 Мой статус"),
            ],
            [
                types.KeyboardButton(text="💬 Задать вопрос"),
                types.KeyboardButton(text="📞 Контакты депо"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def make_candidate_main_keyboard(user_id: int = 0) -> types.InlineKeyboardMarkup:
    """
    Эргономичное и минималистичное главное меню бота МУП «УЭТ»:
    - [ 🌐 Заполнить анкету на сайте / Личный кабинет ] (закреплённая WebApp-кнопка)
    - Сетка 2x2:
        [ 📝 Анкета в чате ]    [ 📑 Мой статус ]
        [ 💬 Задать вопрос ]    [ 📞 Контакты депо ]
    """
    builder = InlineKeyboardBuilder()
    web_url = get_valid_webapp_url("tg")

    maint_active = is_maintenance_active()

    # 1. Закреплённая WebApp-кнопка (Главный акцент на сайт)
    if maint_active:
        builder.button(text="🌐 На сайте (техработы ⏸)", web_app=types.WebAppInfo(url=web_url))
    else:
        builder.button(text="🌐 Заполнить анкету на сайте / Личный кабинет", web_app=types.WebAppInfo(url=web_url))

    # 2. Компактная сетка 2x2
    if maint_active:
        builder.button(text="⏸ Анкета (ТО на линии)", callback_data="cand_start_apply")
    else:
        builder.button(text="📝 Анкета в чате", callback_data="cand_start_apply")

    builder.button(text="📑 Мой статус", callback_data="cand_my_application")
    builder.button(text="💬 Задать вопрос", callback_data="cand_ask_question")
    builder.button(text="📞 Контакты депо", callback_data="cand_hr_contacts")

    # Для служебного персонала (Кадры / Tech / SuperAdmin)
    super_id = CONFIG.get("SUPER_ADMIN_ID")
    is_tech = bool(user_id and (user_id == super_id or is_tech_admin(user_id)))
    is_hr = bool(user_id and (user_id == super_id or is_hr_admin(user_id)))

    if is_tech or is_hr:
        builder.button(text="👔 Служебный кабинет", web_app=types.WebAppInfo(url=get_valid_webapp_url("dashboard")))
        builder.adjust(1, 2, 2, 1)
    else:
        builder.adjust(1, 2, 2)

    return builder.as_markup()


def make_phone_reply_keyboard() -> types.ReplyKeyboardMarkup:
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="📱 Поделиться номером телефона", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def make_ticket_keyboard(ticket_id: int) -> types.InlineKeyboardMarkup:
    """
    Карточка соискателя для HR (Fast Action Bar):
    Ряд 1: [ 📞 Позвонить ] (tel:) | [ 🟢 Live-чат ] | [ 📝 Заметка ]
    Ряд 2: [ 📅 Назначить встречу ] | [ 🔍 Проверка рисков ]
    Ряд 3: [ 📂 В работу ] | [ ❌ Отказ ] | [ 🌐 Открыть на сайте ]
    """
    builder = InlineKeyboardBuilder()
    cand = db.get_candidate(ticket_id)
    cand[1] if cand else "tg"
    cand[7] if cand and len(cand) > 7 else "Новая"

    phone = str(cand[4]) if cand and len(cand) > 4 and cand[4] else ""
    # Очищаем телефон для ссылки tel:
    clean_tel = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    if clean_tel and not clean_tel.startswith("+"):
        clean_tel = f"+{clean_tel}"

    web_url = CONFIG.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")
    cand_web_url = f"{web_url.rstrip('/')}/#cand_{ticket_id}"

    # --- РЯД 1 ---
    if clean_tel:
        builder.button(text="📞 Позвонить", url=f"https://t.me/share/url?url={clean_tel}")
    else:
        builder.button(text="📞 Позвонить", callback_data=f"cand_call_{ticket_id}")

    builder.button(text="🟢 Live-чат", callback_data=f"live_dlg_cand_{ticket_id}")
    builder.button(text="📝 Заметка", callback_data=f"cand_note_{ticket_id}")

    # --- РЯД 2 ---
    builder.button(text="📅 Назначить встречу", callback_data=f"schedule_interview_{ticket_id}")
    builder.button(text="🔍 Проверка рисков", callback_data=f"cand_risk_{ticket_id}")

    # --- РЯД 3 ---
    builder.button(text="📂 В работу", callback_data=f"status_{ticket_id}_В работе")
    builder.button(text="❌ Отказ", callback_data=f"status_{ticket_id}_Отказ")
    builder.button(text="🌐 Открыть на сайте", url=cand_web_url)

    builder.adjust(3, 2, 3)
    return builder.as_markup()


def make_depot_selection_keyboard(ticket_id: int) -> types.InlineKeyboardMarkup:
    """Выбор филиала депо для проведения собеседования."""
    builder = InlineKeyboardBuilder()
    from texts import DEPOT_BRANCHES

    for code, info in DEPOT_BRANCHES.items():
        builder.button(text=f"🏢 {info['short_name']}", callback_data=f"int_depot_{ticket_id}_{code}")
    builder.button(text="⬅️ Назад к анкете", callback_data=f"view_{ticket_id}")
    builder.adjust(1)
    return builder.as_markup()


def make_interview_slots_keyboard(ticket_id: int, depot_code: str, slots: List[str]) -> types.InlineKeyboardMarkup:
    """Выбор слота времени для собеседования в выбранном филиале."""
    builder = InlineKeyboardBuilder()
    for slot in slots:
        builder.button(text=f"🕒 {slot}", callback_data=f"int_slot_{ticket_id}_{depot_code}_{slot}")
    builder.button(text="✍️ Указать другое время", callback_data=f"invite_custom_{ticket_id}")
    builder.button(text="⬅️ Назад к филиалам", callback_data=f"schedule_interview_{ticket_id}")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def make_interview_candidate_response_keyboard(ticket_id: int) -> types.InlineKeyboardMarkup:
    """Инлайн-кнопки подтверждения явки на собеседование для соискателя."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Буду точно", callback_data=f"cand_int_confirm_{ticket_id}")
    builder.button(text="🔄 Прошу перенести", callback_data=f"cand_int_resched_{ticket_id}")
    builder.adjust(1)
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
    super_id = CONFIG.get("SUPER_ADMIN_ID")
    is_super = bool(user_id and user_id == super_id)

    # 1 ряд — фильтры заявок
    builder.button(text="📑 Все резюме", callback_data="admin_list_all")
    builder.button(text="📥 Новые", callback_data="admin_list_new")

    # 2 ряд — статусы
    builder.button(text="🟡 В работе", callback_data="admin_list_in_progress")
    builder.button(text="📦 Архив", callback_data="admin_list_archive")

    # 3 ряд — аналитика воронки (перенесена для HR) и экспорт
    builder.button(text="📈 Аналитика воронки", callback_data="hr_funnel_analytics")
    builder.button(text="📊 Скачать Excel (/export)", callback_data="hr_export_excel")

    # 4 ряд — таймаут и веб-панель
    cur_cd = int(db.get_setting("cooldown_seconds", str(CONFIG.get("COOLDOWN_SECONDS", 1200))))
    cd_label = f"{cur_cd // 60} мин" if cur_cd > 0 else "0 (выкл)"
    builder.button(text=f"⏱ Таймаут: [{cd_label}]", callback_data="hr_toggle_cooldown")

    web_url = CONFIG.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")
    if web_url and web_url.startswith("https://"):
        builder.button(text="🌐 Веб-панель (Mini App)", web_app=types.WebAppInfo(url=web_url))
    else:
        builder.button(text="🌐 Веб-панель управления", callback_data="hr_web_panel_info")

    # 5 ряд — персональные уведомления в ЛС и обновление
    is_enabled = db.get_admin_notify_status(user_id)
    toggle_text = "🔔 Уведы в ЛС: [ВКЛ]" if is_enabled else "🔕 Уведы в ЛС: [ВЫКЛ]"
    builder.button(text=toggle_text, callback_data="toggle_dm_notify")
    builder.button(text="🔄 Обновить", callback_data="admin_stats")

    # Кнопку ТО и массового отключения видит ТОЛЬКО Главный администратор:
    if is_super:
        is_maint = is_maintenance_active()
        maint_btn_text = "🟡 ТО: [ВКЛЮЧЕН] ⇄" if is_maint else "🟢 ТО: [ВЫКЛЮЧЕН] ⇄"
        builder.button(text=maint_btn_text, callback_data="tech_toggle_maint")
        builder.button(text="🔕 Выкл всем HR", callback_data="hr_mute_all_dm")
        builder.adjust(2, 2, 2, 2, 2, 2)
    else:
        builder.adjust(2, 2, 2, 2, 2)

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


def make_remove_admin_keyboard(admins_with_names: list) -> types.InlineKeyboardMarkup:
    """Генерирует кнопки отзыва прав с отображением ника и имени."""
    builder = InlineKeyboardBuilder()
    super_id = CONFIG.get("SUPER_ADMIN_ID")
    for adm_id, role, name_label in admins_with_names:
        if adm_id == super_id:
            continue
        role_label = "HR" if role == "hr" else "Tech"
        btn_text = f"❌ {name_label} ({role_label})"
        if len(btn_text) > 35:
            btn_text = btn_text[:32] + "..."
        builder.button(text=btn_text, callback_data=f"adm_del_id_{adm_id}")
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

    # 1. Режим проверок и ограничений (152-ФЗ, лимиты)
    env = CONFIG.get("ENVIRONMENT", "TEST")
    toggle_env_text = "🛡 Включить PROD (152-ФЗ, лимиты)" if env == "TEST" else "🧪 Включить TEST (без ограничений)"
    builder.button(text=toggle_env_text, callback_data="tech_toggle_env")

    # 2. Индикатор базы данных (12-Factor App: DB_PATH)
    cur_db = os.path.basename(getattr(db, "db_path", "resumes.db"))
    db_btn_text = f"📁 База: [{cur_db}] ℹ️"
    builder.button(text=db_btn_text, callback_data="tech_toggle_db")

    builder.button(text="💾 Управление бэкапами (/backups)", callback_data="tech_manage_backups")
    if is_maintenance_active():
        builder.button(text="🟢 Завершить ТО и уведомить всех ✅", callback_data="tech_maint_resolve")
    else:
        builder.button(text="🟡 Плановое ТО (рассылка со временем)", callback_data="tech_maint_plan")
        builder.button(text="🚨 Срочное ТО (мгновенная рассылка)", callback_data="tech_maint_emergency")
    builder.button(text="📋 Системные логи (/logs)", callback_data="tech_show_logs")
    builder.button(text="🔄 Обновить статус", callback_data="tech_refresh")
    builder.adjust(1)
    return builder.as_markup()


def make_tests_menu_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Системные логи (/logs)", callback_data="tech_show_logs")
    builder.adjust(1)
    return builder.as_markup()


def get_tech_screen_data(user_id: int = 0) -> Tuple[str, types.InlineKeyboardMarkup]:
    tg_status = "🟢 Онлайн"
    vk_status = (
        "🟢 Онлайн"
        if SYSTEM_METRICS["vk_online"]
        else ("🟡 Не настроен" if not CONFIG.get("VK_GROUP_TOKEN") else "🔴 Ошибка")
    )
    max_status = (
        "🟢 Онлайн"
        if SYSTEM_METRICS["max_online"]
        else ("🟡 Не настроен" if not CONFIG.get("MAX_BOT_TOKEN") else "🔴 Ошибка")
    )
    db_status = "🟢 Исправна" if db.check_health() else "🔴 Сбой целостности"
    maint_status = "🟡 Включен (прием на паузе)" if is_maintenance_active() else "🟢 Работа в штатном режиме"
    env_mode = CONFIG.get("ENVIRONMENT", "TEST")
    check_time = datetime.now().strftime("%H:%M:%S")

    cur_db_name = os.path.basename(getattr(db, "db_path", "resumes.db"))
    active_db_label = f"📁 {cur_db_name} (12-Factor DB_PATH)"

    text = (
        "🛠 <b>ТЕХНИЧЕСКИЙ МОНИТОРИНГ И ОБСЛУЖИВАНИЕ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡 <b>Режим работы:</b> <b>{env_mode}</b> "
        + (
            "(Законный режим: проверка анкет, 3 мес. отказ, антифлуд)\n"
            if env_mode == "PROD"
            else "(Режим отладки: все ограничения сняты)\n"
        )
        + f"💾 <b>Целевая БД:</b> <b>{active_db_label}</b>\n"
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
    """
    Клавиатура выбора вакансии для соискателя (Шаг 5).
    Динамически генерируется через InlineKeyboardBuilder на основе выборки из SQLite:
    SELECT slug, title FROM vacancy_settings WHERE is_active = 1 ORDER BY sort_order ASC.
    """
    builder = InlineKeyboardBuilder()
    vac_list = []
    try:
        if hasattr(db, "get_vacancies"):
            vac_list = db.get_vacancies(only_active=True)
    except Exception as e:
        logger.warning("Ошибка выборки вакансий для клавиатуры: %s", e)

    if vac_list:
        for v in vac_list:
            slug = v.get("slug")
            title = v.get("title")
            meta = v.get("meta") or {}
            icon = meta.get("icon") or get_vacancy_icon(title)
            builder.button(text=f"{icon} {title}", callback_data=f"vac_pick_{slug}")
        builder.button(text="📋 Другая должность", callback_data="vac_other")
        builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
        builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
        layout = [1] * len(vac_list) + [1, 2]
        builder.adjust(*layout)
        return builder.as_markup()

    open_vacancies = get_open_vacancies()
    if not open_vacancies:
        open_vacancies = [
            "Водитель трамвая",
            "Водитель троллейбуса",
            "Кондуктор",
            "Слесарь по ремонту подвижного состава",
            "Электромонтер контактной сети",
        ]

    for idx, vac in enumerate(open_vacancies):
        icon = get_vacancy_icon(vac)
        builder.button(text=f"{icon} {vac}", callback_data=f"vac_pick_{idx}")

    builder.button(text="📋 Другая должность", callback_data="vac_other")
    builder.button(text="⬅️ Назад", callback_data="cand_nav_back")
    builder.button(text="❌ Отмена", callback_data="cand_cancel_flow")
    layout = [1] * len(open_vacancies) + [1, 2]
    builder.adjust(*layout)
    return builder.as_markup()


def make_vacancies_catalog_kb() -> types.InlineKeyboardMarkup:
    """
    Инлайн-каталог активных вакансий МУП «УЭТ».
    Формирует список кнопок для открытия персональной карточки по slug.
    """
    builder = InlineKeyboardBuilder()
    vac_list = []
    try:
        if hasattr(db, "get_vacancies"):
            vac_list = db.get_vacancies(only_active=True)
    except Exception:
        pass
    if not vac_list:
        open_vac = get_open_vacancies()
        for idx, t in enumerate(open_vac):
            vac_list.append({"slug": f"vac_{idx}", "title": t, "meta": {}})

    for v in vac_list:
        slug = v.get("slug")
        title = v.get("title")
        meta = v.get("meta") or {}
        icon = meta.get("icon") or get_vacancy_icon(title)
        builder.button(text=f"{icon} {title}", callback_data=f"vac_card_{slug}")

    builder.button(text="📝 Подать анкету соискателя", callback_data="cand_start_apply")
    builder.button(text="🏠 Главное меню", callback_data="cand_back_to_main")
    layout = [1] * len(vac_list) + [1, 1]
    builder.adjust(*layout)
    return builder.as_markup()


def make_vacancy_card_kb(slug: str) -> types.InlineKeyboardMarkup:
    """Клавиатура для карточки конкретной вакансии (Откликнуться / Назад к списку)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Откликнуться на вакансию", callback_data=f"vac_apply_{slug}")
    builder.button(text="💼 Все вакансии", callback_data="cand_show_vacancies")
    builder.button(text="🏠 Главное меню", callback_data="cand_back_to_main")
    builder.adjust(1, 2)
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


# Псевдоним для обратной совместимости
get_ticket_control_inline_keyboard = make_ticket_keyboard

