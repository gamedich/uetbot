# -*- coding: utf-8 -*-
"""
Обработчики диалогов с соискателями МУП «Ульяновскэлектротранс»:
- Главное меню, команды /start, /help, /id, /contacts, /privacy, /my, /training, /mydata, /revoke
- Подача анкеты на работу (16 шагов опросника строго по ТЗ, FSM CandidateForm)
- Вопросы специалисту кадров (/ask, FSM InquiryForm)
- База знаний предприятия (FAQ)
- Экстренная техподдержка (/support, /sos)
"""
import html
import json
import logging
import re
from datetime import datetime

from aiogram import Router, F, types, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

import texts
from common import (
    CONFIG,
    db,
    is_tech_admin,
    is_hr_admin,
    safe_answer,
    safe_send,
    VACANCIES,
    CandidateForm,
    InquiryForm,
    SupportForm,
    RevokeConsentForm,
    route_new_candidate_ticket,
    route_new_inquiry_ticket
)
from keyboards import (
    make_candidate_main_keyboard,
    make_phone_reply_keyboard,
    make_faq_keyboard,
    make_ticket_keyboard,
    make_inquiry_admin_keyboard,
    make_consent_survey_kb,
    make_step_nav_kb,
    make_step2_birthdate_kb,
    make_step4_city_kb,
    make_step5_vacancies_kb,
    make_step6_license_kb,
    make_step6_1_categories_kb,
    make_step7_experience_kb,
    make_step8_education_kb,
    make_step9_relocation_kb,
    make_step10_dormitory_kb,
    make_step11_schedule_kb,
    make_step12_health_kb,
    make_step13_criminal_kb,
    make_step14_source_kb,
    make_step16_confirm_kb,
    make_step16_edit_menu_kb,
    make_mydata_kb,
    make_revoke_confirm_kb
)

logger = logging.getLogger(__name__)
candidate_router = Router(name="candidate")


# =====================================================================
# СИСТЕМНЫЕ КОМАНДЫ НАВИГАЦИИ (/cancel, /stop, /help, /id, /start)
# =====================================================================

@candidate_router.message(Command("cancel"))
@candidate_router.message(F.text.lower().in_(["/cancel", "отмена", "отменить"]))
@candidate_router.callback_query(F.data == "cand_cancel_flow")
async def cmd_cancel(event: types.Message | types.CallbackQuery, state: FSMContext):
    await state.clear()
    uid = event.from_user.id
    if isinstance(event, types.CallbackQuery):
        try:
            await event.message.edit_text(
                texts.NAV_CANCEL_TEXT,
                reply_markup=make_candidate_main_keyboard(user_id=uid),
                parse_mode="HTML"
            )
        except Exception:
            await event.message.answer(
                texts.NAV_CANCEL_TEXT,
                reply_markup=make_candidate_main_keyboard(user_id=uid),
                parse_mode="HTML"
            )
        await event.answer("Отменено")
    else:
        await safe_answer(
            event,
            texts.NAV_CANCEL_TEXT,
            reply_markup=make_candidate_main_keyboard(user_id=uid),
            parse_mode="HTML"
        )


@candidate_router.message(Command("stop"))
async def cmd_candidate_stop_dialog(message: types.Message, bot: Bot):
    user_id = str(message.from_user.id)
    dlg = db.get_dialog_by_user(user_id)
    if not dlg:
        return await safe_answer(message, "ℹ️ У вас сейчас нет активного прямого диалога.")
    operator_id = dlg[1]
    name = dlg[3] or message.from_user.full_name
    db.end_direct_dialog(user_id=user_id)
    await safe_answer(
        message,
        texts.LIVE_CHAT_ENDED,
        reply_markup=make_candidate_main_keyboard(user_id=message.from_user.id),
        parse_mode="HTML"
    )
    await safe_send(
        bot,
        operator_id,
        f"⏹ Соискатель <b>{html.escape(name)}</b> (ID: <code>{user_id}</code>) завершил прямой диалог."
    )


@candidate_router.message(Command("help"))
@candidate_router.message(F.text.lower().in_(["help", "помощь", "справка", "команды", "хелп", "/help"]))
async def cmd_help(message: types.Message, state: FSMContext = None):
    if state:
        await state.clear()
    user_id = message.from_user.id
    is_tech = is_tech_admin(user_id)
    is_hr = is_hr_admin(user_id)
    help_text = texts.format_help_text(user_id=user_id, is_hr=is_hr, is_tech=is_tech)
    await safe_answer(message, help_text, reply_markup=make_candidate_main_keyboard(user_id=user_id), parse_mode="HTML")


@candidate_router.message(Command("id"))
async def cmd_id(message: types.Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    username = message.from_user.username
    info_text = texts.format_id_text(user_id, chat_id, username)
    is_admin_user = (user_id == CONFIG.get("SUPER_ADMIN_ID") or is_hr_admin(user_id) or is_tech_admin(user_id))
    if is_admin_user and message.chat.type != "private":
        info_text += (
            "\n━━━━━━━━━━━━━━━━━━━━━\n"
            "ℹ️ <i>Чтобы привязать эту группу для получения анкет, отправьте:</i>\n"
            "<code>/set_group</code>"
        )
    await safe_answer(message, info_text, parse_mode="HTML")


@candidate_router.message(Command("start", "menu"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    is_admin = (user_id == CONFIG.get("SUPER_ADMIN_ID") or is_hr_admin(user_id) or is_tech_admin(user_id))

    if db.is_blocked(user_id, super_admin_id=CONFIG.get("SUPER_ADMIN_ID")):
        return await safe_answer(
            message,
            "⛔ <b>Доступ к боту ограничен</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Ваш аккаунт находится в <b>чёрном списке</b> информационной системы МУП «Ульяновскэлектротранс».\n"
            f"📞 <i>Контакты отдела кадров предприятия:</i> <code>{CONFIG['HR_PHONE']}</code>",
            parse_mode="HTML"
        )

    if CONFIG.get("MAINTENANCE_MODE") and not is_admin:
        return await safe_answer(message, texts.MAINTENANCE_ACTIVE, parse_mode="HTML")

    await safe_answer(
        message,
        texts.START_WELCOME,
        reply_markup=make_candidate_main_keyboard(user_id=user_id),
        parse_mode="HTML"
    )


# =====================================================================
# ЭКСТРЕННАЯ СВЯЗЬ С ТЕХПОДДЕРЖКОЙ (/support, /sos)
# =====================================================================

@candidate_router.callback_query(F.data == "cand_support")
@candidate_router.message(Command("support", "sos", "tech_support"))
@candidate_router.message(F.text.lower().in_(["поддержка", "техподдержка", "сос", "sos", "/support", "/sos"]))
async def cb_cand_support_start(event: types.CallbackQuery | types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(SupportForm.waiting_message)
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="cand_back_to_menu")
    prompt_text = (
        "🚨 <b>ЭКСТРЕННАЯ СВЯЗЬ С ТЕХНИЧЕСКИМ АДМИНИСТРАТОРОМ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Если вы столкнулись с техническим сбоем, критической ошибкой в работе бота "
        "или вам требуется срочная помощь инженера, отправьте сообщение с описанием проблемы <b>одним сообщением</b> ниже.\n\n"
        "<i>Ваше обращение поступит напрямую дежурному техническому администратору.</i>"
    )
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(prompt_text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, prompt_text, reply_markup=builder.as_markup(), parse_mode="HTML")


@candidate_router.message(SupportForm.waiting_message)
async def process_support_message(message: types.Message, state: FSMContext, bot: Bot):
    if not message.text:
        return await safe_answer(message, "⚠️ Пожалуйста, опишите проблему текстом в одном сообщении.")
    await state.clear()
    user_id = message.from_user.id
    user_name = message.from_user.full_name or "Пользователь"
    user_uname = f"@{message.from_user.username}" if message.from_user.username else "Не указан"
    time_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    card_text = (
        "🚨 <b>ЭКСТРЕННОЕ СООБЩЕНИЕ В ТЕХПОДДЕРЖКУ!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>От пользователя:</b> {html.escape(user_name)} (ID: <code>{user_id}</code>)\n"
        f"⏱ <b>Время:</b> <code>{time_str}</code>\n"
        f"🌐 <b>Username:</b> {user_uname}\n"
        "⚠️ <b>Текст обращения:</b>\n"
        f"<blockquote>{html.escape(message.text)}</blockquote>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Связаться с пользователем можно по ссылке ниже.</i>"
    )
    target_techs = set()
    super_admin = CONFIG.get("SUPER_ADMIN_ID")
    if super_admin and super_admin != 0:
        target_techs.add(super_admin)
    for adm_id, role in db.get_all_admins():
        if role == "tech":
            target_techs.add(adm_id)

    builder = InlineKeyboardBuilder()
    if message.from_user.username:
        builder.button(text="💬 Открыть чат в TG", url=f"https://t.me/{message.from_user.username}")
    else:
        builder.button(text="👤 Профиль пользователя", url=f"tg://user?id={user_id}")
    builder.adjust(1)

    for tech_id in target_techs:
        try:
            await safe_send(bot, tech_id, card_text, reply_markup=builder.as_markup())
        except Exception as e:
            logger.error(f"Не удалось отправить SOS админу {tech_id}: {e}")

    await safe_answer(
        message,
        "✅ <b>Экстренное сообщение передано техническому администратору!</b>\n\n"
        "Дежурный инженер уведомлен и разбирается с ситуацией. При необходимости специалист свяжется с вами в Telegram.",
        reply_markup=make_candidate_main_keyboard(user_id=user_id),
        parse_mode="HTML"
    )


# =====================================================================
# РАЗДЕЛ «МОЯ АНКЕТА» (/my)
# =====================================================================

@candidate_router.callback_query(F.data == "cand_my_application")
@candidate_router.message(Command("my"))
async def cb_cand_my_application(event: types.CallbackQuery | types.Message):
    user_id = event.from_user.id
    if db.is_blocked(user_id, super_admin_id=CONFIG.get("SUPER_ADMIN_ID")):
        if isinstance(event, types.CallbackQuery):
            return await event.answer("⛔ Доступ ограничен (вы в черном списке).", show_alert=True)
        return await safe_answer(event, "⛔ Доступ ограничен.", parse_mode="HTML")

    cand_tuple = db.get_candidate_by_user_id(str(user_id), platform="tg")
    builder = InlineKeyboardBuilder()
    if not cand_tuple:
        builder.button(text=texts.BTN_APPLY, callback_data="cand_start_apply")
        builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
        builder.adjust(1)
        text = texts.APP_NOT_FOUND
    else:
        builder.button(text="💬 Задать вопрос / Связаться", callback_data="cand_ask_question")
        builder.button(text="📚 Частые вопросы (FAQ)", callback_data="cand_faq_menu")
        builder.button(text="🏢 Контакты отдела кадров", callback_data="cand_hr_contacts")
        builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
        builder.adjust(1)
        
        text = texts.format_my_application_full(cand_tuple)

        
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, text, reply_markup=builder.as_markup(), parse_mode="HTML")


# =====================================================================
# КОМАНДЫ ПРАВ СУБЪЕКТА 152-ФЗ (/mydata, /revoke)
# =====================================================================

@candidate_router.message(Command("mydata"))
async def cmd_mydata(message: types.Message):
    user_id = message.from_user.id
    cand_dict = db.get_candidate_dict_by_user(str(user_id), platform="tg")
    if not cand_dict:
        builder = InlineKeyboardBuilder()
        builder.button(text=texts.BTN_APPLY, callback_data="cand_start_apply")
        builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
        builder.adjust(1)
        return await safe_answer(
            message,
            "📑 <b>Персональные данные не найдены.</b>\nВы еще не подавали анкету в информационную систему предприятия.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

    text = texts.format_mydata(cand_dict)
    await safe_answer(message, text, reply_markup=make_mydata_kb(), parse_mode="HTML")


@candidate_router.message(Command("revoke"))
@candidate_router.callback_query(F.data == "cand_revoke_ask")
async def cmd_revoke_ask(event: types.Message | types.CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(RevokeConsentForm.waiting_confirm)
    text = texts.REVOKE_CONFIRM_PROMPT
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, reply_markup=make_revoke_confirm_kb(), parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, text, reply_markup=make_revoke_confirm_kb(), parse_mode="HTML")


@candidate_router.callback_query(RevokeConsentForm.waiting_confirm, F.data == "cand_revoke_confirm")
async def cb_revoke_confirm(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    del_id = db.delete_candidate_by_user(str(user_id), platform="tg")
    destroyed_ts = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    ticket_num = del_id if del_id else 0
    text = texts.format_revoke_success(ticket_num, destroyed_ts)
    await callback.message.edit_text(
        text,
        reply_markup=make_candidate_main_keyboard(user_id=user_id),
        parse_mode="HTML"
    )
    await callback.answer("Согласие отозвано")


# =====================================================================
# РАЗДЕЛЫ «ОБУЧЕНИЕ», FAQ И КОНТАКТЫ (/training, /faq, /contacts, /privacy)
# =====================================================================

@candidate_router.message(Command("training"))
async def cmd_training(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Заполнить анкету", callback_data="cand_start_apply")
    builder.button(text="💬 Задать вопрос", callback_data="cand_ask_question")
    builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
    builder.adjust(1)
    await safe_answer(message, texts.TRAINING_INFO_TEXT, reply_markup=builder.as_markup(), parse_mode="HTML")


@candidate_router.callback_query(F.data == "cand_hr_contacts")
@candidate_router.message(Command("contacts"))
async def cb_cand_hr_contacts(event: types.CallbackQuery | types.Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Связаться с кадровиком", callback_data="cand_ask_question")
    builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
    builder.adjust(1)
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(texts.CONTACTS_SCREEN, reply_markup=builder.as_markup(), parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, texts.CONTACTS_SCREEN, reply_markup=builder.as_markup(), parse_mode="HTML")


@candidate_router.callback_query(F.data == "cand_privacy_policy")
@candidate_router.message(Command("privacy"))
async def cb_cand_privacy_policy(event: types.CallbackQuery | types.Message):
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
    builder.adjust(1)
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(texts.PRIVACY_POLICY_TEXT, reply_markup=builder.as_markup(), parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, texts.PRIVACY_POLICY_TEXT, reply_markup=builder.as_markup(), parse_mode="HTML")


@candidate_router.callback_query(F.data == "cand_back_to_menu")
async def cb_cand_back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    await callback.message.edit_text(
        texts.MENU_RETURN,
        reply_markup=make_candidate_main_keyboard(user_id=user_id),
        parse_mode="HTML"
    )
    await callback.answer()


@candidate_router.callback_query(F.data == "cand_faq_menu")
@candidate_router.message(Command("faq"))
async def cb_cand_faq_menu(event: types.CallbackQuery | types.Message):
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(texts.FAQ_MENU_TITLE, reply_markup=make_faq_keyboard(), parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, texts.FAQ_MENU_TITLE, reply_markup=make_faq_keyboard(), parse_mode="HTML")


@candidate_router.callback_query(F.data.startswith("faq_item_"))
async def cb_faq_item(callback: types.CallbackQuery):
    item_key = callback.data.replace("faq_item_", "")
    text = texts.FAQ_DATA.get(item_key, "Информация обновляется...")

    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад к вопросам (FAQ)", callback_data="cand_faq_menu")
    builder.button(text=texts.BTN_APPLY, callback_data="cand_start_apply")
    builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
    builder.adjust(1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


# =====================================================================
# ВОПРОСЫ В ОТДЕЛ КАДРОВ (/ask, InquiryForm)
# =====================================================================

@candidate_router.callback_query(F.data == "cand_ask_question")
@candidate_router.message(Command("ask"))
async def cb_cand_ask_question(event: types.CallbackQuery | types.Message, state: FSMContext):
    await state.clear()
    user_id = event.from_user.id

    if db.is_blocked(user_id, super_admin_id=CONFIG.get("SUPER_ADMIN_ID")):
        if isinstance(event, types.CallbackQuery):
            return await event.answer("⛔ Доступ ограничен.", show_alert=True)
        return await safe_answer(event, "⛔ Отправка вопросов недоступна.", parse_mode="HTML")

    is_admin = (user_id == CONFIG.get("SUPER_ADMIN_ID") or is_hr_admin(user_id) or is_tech_admin(user_id))
    env_mode = (CONFIG.get("ENVIRONMENT") or "TEST").upper()
    if env_mode in ("PROD", "PRODUCTION") and not is_admin:
        allowed, seconds_left = db.can_send_inquiry(str(user_id), cooldown_seconds=CONFIG.get("COOLDOWN_SECONDS", 1200))
        if not allowed:
            mins = (seconds_left // 60) + 1
            msg_text = texts.format_inquiry_cooldown(mins)
            if isinstance(event, types.CallbackQuery):
                return await event.message.edit_text(
                    msg_text,
                    reply_markup=make_candidate_main_keyboard(user_id=user_id),
                    parse_mode="HTML"
                )
            return await safe_answer(event, msg_text)

    await state.set_state(InquiryForm.waiting_consent)
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CONSENT_YES, callback_data="cand_consent_ask")
    builder.button(text=texts.BTN_READ_PRIVACY, callback_data="cand_privacy_policy")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    builder.adjust(1)

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(texts.CONSENT_INQUIRY_PROMPT, reply_markup=builder.as_markup(), parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, texts.CONSENT_INQUIRY_PROMPT, reply_markup=builder.as_markup(), parse_mode="HTML")


@candidate_router.callback_query(InquiryForm.waiting_consent, F.data == "cand_consent_ask")
async def cb_cand_consent_ask(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(InquiryForm.waiting_question)
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    await callback.message.edit_text(texts.INQUIRY_INPUT_PROMPT, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer("Согласие принято")


@candidate_router.message(InquiryForm.waiting_question)
async def process_inquiry_message(message: types.Message, state: FSMContext, bot: Bot):
    q_text = (message.text or "").strip()
    if not q_text:
        return await safe_answer(message, "⚠️ Пожалуйста, напишите вопрос текстом в одном сообщении.")
    if len(q_text) > 1000:
        return await safe_answer(message, texts.ERR_QUESTION_TOO_LONG)

    await state.clear()
    user_id = str(message.from_user.id)
    cand_info = db.get_candidate_by_user_id(user_id, platform="tg")
    full_name = cand_info[3] if cand_info else (message.from_user.full_name or "Не указано")
    phone = cand_info[4] if cand_info else "Не указан"
    vacancy = cand_info[5] if cand_info else "Анкета не подана"

    consent_ts = datetime.now().strftime("%d.%m.%Y %H:%M")
    is_test_inq = (int(user_id) == CONFIG.get("SUPER_ADMIN_ID") or is_hr_admin(int(user_id)) or is_tech_admin(int(user_id))) and (
        CONFIG.get("ENVIRONMENT") == "TEST" or "тест" in q_text.lower()
    )
    inquiry_id = db.add_inquiry(
        platform="tg",
        user_id=user_id,
        full_name=full_name,
        phone=phone,
        vacancy=vacancy,
        question=q_text,
        is_test=is_test_inq,
        consent_timestamp=consent_ts
    )

    conf_text = texts.format_inquiry_sent(inquiry_id)
    await safe_answer(message, conf_text, reply_markup=make_candidate_main_keyboard(user_id=message.from_user.id), parse_mode="HTML")

    # Маршрутизация в кадровый чат
    db_label = "<code>Тестовая база</code>" if is_test_inq else "<code>resumes.db</code>"
    prefix = "🧪 ТЕСТОВОЕ ОБРАЩЕНИЕ" if is_test_inq else "📩 ОБРАЩЕНИЕ"
    hr_card = (
        f"<b>{prefix} СОИСКАТЕЛЯ #{inquiry_id} [TG]</b>\n"
        f"📁 <b>База:</b> {db_label}\n"
        f"👤 <b>Кандидат:</b> {html.escape(full_name)}\n"
        f"📞 <b>Телефон:</b> <code>{html.escape(phone)}</code>\n"
        f"🎯 <b>Вакансия:</b> {html.escape(vacancy)}\n"
        f"⚖️ <b>Согласие 152-ФЗ:</b> <code>✅ Получено ({consent_ts})</code>\n"
        f"⏱ <b>Время:</b> <code>{datetime.now().strftime('%d.%m.%Y %H:%M')}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"❓ <b>Вопрос:</b>\n"
        f"<blockquote>{html.escape(q_text)}</blockquote>\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    inq_kb = make_inquiry_admin_keyboard(inquiry_id)
    await route_new_inquiry_ticket(bot, hr_card, inq_kb)


# =====================================================================
# ОПРОСНИК СОИСКАТЕЛЯ (16 ШАГОВ АНКЕТЫ)
# =====================================================================

@candidate_router.callback_query(F.data == "cand_start_apply")
@candidate_router.message(Command("apply"))
async def cmd_apply(event: types.Message | types.CallbackQuery, state: FSMContext):
    user_id = event.from_user.id
    await run_candidate_survey(event, state, user_id)


async def run_candidate_survey(event: types.Message | types.CallbackQuery, state: FSMContext, user_id: int, force: bool = False):
    async def send_or_edit(txt: str, reply_markup=None):
        if isinstance(event, types.CallbackQuery):
            try:
                await event.message.edit_text(txt, reply_markup=reply_markup, parse_mode="HTML")
            except Exception:
                await event.message.answer(txt, reply_markup=reply_markup, parse_mode="HTML")
            await event.answer()
        else:
            await safe_answer(event, txt, reply_markup=reply_markup, parse_mode="HTML")

    if db.is_blocked(user_id, super_admin_id=CONFIG.get("SUPER_ADMIN_ID")):
        return await send_or_edit(
            "⛔ <b>Подача анкеты недоступна</b>\n\n"
            "Ваш аккаунт находится в чёрном списке информационной системы предприятия.\n"
            f"Контакты отдела кадров: <code>{CONFIG['HR_PHONE']}</code>"
        )

    env = (CONFIG.get("ENVIRONMENT") or "TEST").upper()
    is_prod = env in ("PROD", "PRODUCTION")
    is_admin = (user_id == CONFIG.get("SUPER_ADMIN_ID") or is_hr_admin(user_id) or is_tech_admin(user_id))

    if CONFIG.get("MAINTENANCE_MODE") and not is_admin:
        return await send_or_edit(texts.MAINTENANCE_ACTIVE)

    if (is_prod or not is_admin) and not force:
        can_apply, reason, info = db.check_candidate_can_apply(str(user_id), platform="tg")
        if not can_apply and info:
            ticket_id = info.get("ticket_id")
            if reason == "unprocessed":
                status = info.get("status", "Новая")
                created = info.get("created_at", "")
                vac = info.get("vacancy", "")
                builder = InlineKeyboardBuilder()
                builder.button(text="💬 Задать вопрос / Связаться", callback_data="cand_ask_question")
                builder.button(text="📚 Частые вопросы (FAQ)", callback_data="cand_faq_menu")
                builder.button(text="🏢 Контакты отдела кадров", callback_data="cand_hr_contacts")
                builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
                builder.adjust(1)
                return await send_or_edit(texts.format_already_applied(ticket_id, vac, created, status), reply_markup=builder.as_markup())
            elif reason in ("cooldown", "rejected_cooldown"):
                days_left = info.get("days_left", 0)
                builder = InlineKeyboardBuilder()
                builder.button(text="💬 Связаться с отделом кадров", callback_data="cand_ask_question")
                builder.button(text="📚 Частые вопросы (FAQ)", callback_data="cand_faq_menu")
                builder.button(text="🏢 Контакты предприятия", callback_data="cand_hr_contacts")
                builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
                builder.adjust(1)
                return await send_or_edit(texts.format_rejection_cooldown(ticket_id, "", days_left), reply_markup=builder.as_markup())

    await state.clear()
    await state.set_state(CandidateForm.waiting_consent)
    await send_or_edit(texts.CONSENT_SURVEY_PROMPT, reply_markup=make_consent_survey_kb())


# --- ШАГ 0: СОГЛАСИЕ 152-ФЗ ---
@candidate_router.callback_query(CandidateForm.waiting_consent, F.data == "cand_consent_apply")
async def cb_consent_agree(callback: types.CallbackQuery, state: FSMContext):
    consent_ts = datetime.now().strftime("%d.%m.%Y %H:%M")
    await state.update_data(consent_timestamp=consent_ts)
    await state.set_state(CandidateForm.full_name)
    await callback.message.edit_text(
        texts.SURVEY_STEP1_NAME,
        reply_markup=make_step_nav_kb(can_skip=False),
        parse_mode="HTML"
    )
    await callback.answer("Согласие принято")


@candidate_router.callback_query(CandidateForm.waiting_consent, F.data == "cand_consent_refuse")
async def cb_consent_refuse(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Задать вопрос", callback_data="cand_ask_question")
    builder.button(text="🏠 Главное меню", callback_data="cand_back_to_menu")
    builder.adjust(1)
    await callback.message.edit_text(texts.CONSENT_REFUSED_TEXT, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


# --- ШАГ 1: ФИО ---
@candidate_router.message(CandidateForm.full_name)
async def process_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    words = [w for w in name.split() if len(w) > 1]
    if len(words) < 2:
        return await safe_answer(message, texts.ERR_INVALID_NAME, reply_markup=make_step_nav_kb(can_skip=False), parse_mode="HTML")

    clean_name = " ".join(words)
    await state.update_data(full_name=clean_name)
    await state.set_state(CandidateForm.birth_date)
    await safe_answer(
        message,
        texts.SURVEY_STEP2_BIRTHDATE,
        reply_markup=make_step2_birthdate_kb(),
        parse_mode="HTML"
    )


# --- ШАГ 2: ДАТА РОЖДЕНИЯ ---
@candidate_router.callback_query(CandidateForm.birth_date, F.data.startswith("bd_"))
async def cb_birth_date(callback: types.CallbackQuery, state: FSMContext):
    choice = callback.data.replace("bd_", "")
    if choice == "manual":
        await callback.message.edit_text(
            texts.SURVEY_STEP2_MANUAL_PROMPT,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML"
        )
        return await callback.answer()

    await state.update_data(birth_date=choice)
    data = await state.get_data()
    name = data.get("full_name", "")
    await state.set_state(CandidateForm.phone)
    await callback.message.edit_text(
        f"✅ Дата рождения принята: <b>{choice}</b>\n\n" + texts.format_survey_ask_phone(name),
        parse_mode="HTML"
    )
    await callback.message.answer(
        "Нажмите кнопку внизу или введите номер:",
        reply_markup=make_phone_reply_keyboard()
    )
    await callback.answer()


@candidate_router.message(CandidateForm.birth_date)
async def process_birth_date_text(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    try:
        dt = datetime.strptime(text, "%d.%m.%Y")
    except ValueError:
        return await safe_answer(
            message,
            texts.ERR_INVALID_DATE_FORMAT,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML"
        )

    # Проверка возраста 18+
    age_days = (datetime.now() - dt).days
    if age_days < 18 * 365.25:
        return await safe_answer(
            message,
            texts.ERR_UNDERAGE,
            reply_markup=make_step2_birthdate_kb(),
            parse_mode="HTML"
        )

    await state.update_data(birth_date=text)
    data = await state.get_data()
    name = data.get("full_name", "")
    await state.set_state(CandidateForm.phone)
    await safe_answer(
        message,
        texts.format_survey_ask_phone(name),
        reply_markup=make_phone_reply_keyboard(),
        parse_mode="HTML"
    )


# --- ШАГ 3: ТЕЛЕФОН ---
@candidate_router.message(CandidateForm.phone, F.contact)
async def process_phone_contact(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    await state.update_data(phone=phone)
    await message.answer("✅ Номер телефона успешно получен.", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(CandidateForm.city)
    await safe_answer(
        message,
        texts.SURVEY_STEP4_CITY,
        reply_markup=make_step4_city_kb(),
        parse_mode="HTML"
    )


@candidate_router.message(CandidateForm.phone)
async def process_phone_text(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in ("7", "8"):
        phone = "+7" + digits[1:]
    elif len(digits) == 10 and digits[0] == "9":
        phone = "+7" + digits
    else:
        return await safe_answer(
            message,
            texts.ERR_INVALID_PHONE,
            reply_markup=make_phone_reply_keyboard(),
            parse_mode="HTML"
        )

    await state.update_data(phone=phone)
    await message.answer("✅ Номер телефона принят.", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(CandidateForm.city)
    await safe_answer(
        message,
        texts.SURVEY_STEP4_CITY,
        reply_markup=make_step4_city_kb(),
        parse_mode="HTML"
    )


# --- ШАГ 4: ГОРОД ПРОЖИВАНИЯ ---
@candidate_router.callback_query(CandidateForm.city, F.data.startswith("city_"))
async def cb_city_select(callback: types.CallbackQuery, state: FSMContext):
    c = callback.data.replace("city_", "")
    if c == "manual":
        await state.set_state(CandidateForm.city_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP4_MANUAL_PROMPT,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML"
        )
        return await callback.answer()

    await state.update_data(city=c)
    await state.set_state(CandidateForm.vacancy)
    await callback.message.edit_text(
        f"🏙 Город: <b>{c}</b>\n\n" + texts.SURVEY_STEP5_VACANCY,
        reply_markup=make_step5_vacancies_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@candidate_router.message(CandidateForm.city)
@candidate_router.message(CandidateForm.city_manual)
async def process_city_manual(message: types.Message, state: FSMContext):
    c = (message.text or "").strip()
    await state.update_data(city=c)
    await state.set_state(CandidateForm.vacancy)
    await safe_answer(
        message,
        texts.SURVEY_STEP5_VACANCY,
        reply_markup=make_step5_vacancies_kb(),
        parse_mode="HTML"
    )


# --- ШАГ 5: ВАКАНСИЯ ---
VACANCY_MAP = {
    "tram": "Водитель трамвая",
    "troll": "Водитель троллейбуса",
    "conductor": "Кондуктор",
    "slesar": "Слесарь по ремонту подвижного состава",
    "electro": "Электромонтёр контактной сети",
    "other": "other",
}

@candidate_router.callback_query(CandidateForm.vacancy, F.data.startswith("vac_"))
async def cb_vacancy_select(callback: types.CallbackQuery, state: FSMContext):
    vac_raw = callback.data.replace("vac_", "")
    vac_name = VACANCY_MAP.get(vac_raw, vac_raw)
    if vac_raw == "other" or vac_name == "other":
        await state.set_state(CandidateForm.custom_vacancy)
        await callback.message.edit_text(
            texts.SURVEY_STEP5_1_CUSTOM,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML"
        )
        return await callback.answer()

    await state.update_data(vacancy=vac_name)
    await state.set_state(CandidateForm.has_license)
    await callback.message.edit_text(
        f"🎯 Вакансия: <b>{vac_name}</b>\n\n{texts.SURVEY_STEP6_LICENSE}",
        reply_markup=make_step6_license_kb(),
        parse_mode="HTML"
    )
    await callback.answer()

# --- ШАГ 6: ВОДИТЕЛЬСКОЕ УДОСТОВЕРЕНИЕ ---
@candidate_router.callback_query(CandidateForm.has_license, F.data.in_(["lic_yes", "lic_no"]))
async def cb_license_choice(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "lic_no":
        await state.update_data(driver_license="Нет")
        await state.set_state(CandidateForm.has_experience)
        await callback.message.edit_text(
            texts.SURVEY_STEP7_EXPERIENCE,
            reply_markup=make_step7_experience_kb(),
            parse_mode="HTML"
        )
        return await callback.answer()

    # Если "Да" - переходим к выбору категорий
    await state.update_data(selected_categories=[])
    await state.set_state(CandidateForm.license_categories)
    await callback.message.edit_text(
        texts.SURVEY_STEP6_1_CATEGORIES.format(selected="Не выбрано"),
        reply_markup=make_step6_1_categories_kb([]),
        parse_mode="HTML"
    )
    await callback.answer()


@candidate_router.callback_query(CandidateForm.license_categories, F.data.startswith("cat_toggle_"))
async def cb_license_cat_toggle(callback: types.CallbackQuery, state: FSMContext):
    cat = callback.data.replace("cat_toggle_", "")
    data = await state.get_data()
    cats = list(data.get("selected_categories", []))
    if cat in cats:
        cats.remove(cat)
    else:
        cats.append(cat)
    await state.update_data(selected_categories=cats)

    sel_str = ", ".join(cats) if cats else "Не выбрано"
    await callback.message.edit_text(
        texts.SURVEY_STEP6_1_CATEGORIES.format(selected=sel_str),
        reply_markup=make_step6_1_categories_kb(cats),
        parse_mode="HTML"
    )
    await callback.answer()


@candidate_router.callback_query(CandidateForm.license_categories, F.data == "cat_manual")
async def cb_license_cat_manual(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(CandidateForm.license_categories_manual)
    await callback.message.edit_text(
        texts.SURVEY_STEP6_1_MANUAL,
        reply_markup=make_step_nav_kb(can_skip=False),
        parse_mode="HTML"
    )
    await callback.answer()


@candidate_router.message(CandidateForm.license_categories_manual)
async def process_license_cat_manual(message: types.Message, state: FSMContext):
    cats = (message.text or "").strip()
    await state.update_data(driver_license=cats)
    await state.set_state(CandidateForm.has_experience)
    await safe_answer(
        message,
        texts.SURVEY_STEP7_EXPERIENCE,
        reply_markup=make_step7_experience_kb(),
        parse_mode="HTML"
    )


@candidate_router.callback_query(CandidateForm.license_categories, F.data == "cat_done")
async def cb_license_cat_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cats = data.get("selected_categories", [])
    cat_str = ", ".join(cats) if cats else "Да (категории не указаны)"
    await state.update_data(driver_license=cat_str)
    await state.set_state(CandidateForm.has_experience)
    await callback.message.edit_text(
        f"🚗 Водительские права: <b>{cat_str}</b>\n\n" + texts.SURVEY_STEP7_EXPERIENCE,
        reply_markup=make_step7_experience_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


# --- ШАГ 7: ОПЫТ РАБОТЫ ---
@candidate_router.callback_query(CandidateForm.has_experience, F.data.in_(["exp_yes", "exp_no"]))
async def cb_experience_choice(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "exp_no":
        await state.update_data(experience="Без опыта")
        await state.set_state(CandidateForm.education_level)
        await callback.message.edit_text(
            texts.SURVEY_STEP7_2_NO_EXP + "\n\n" + texts.SURVEY_STEP8_EDUCATION,
            reply_markup=make_step8_education_kb(),
            parse_mode="HTML"
        )
        return await callback.answer()

    await state.set_state(CandidateForm.experience)
    await callback.message.edit_text(
        texts.SURVEY_STEP7_1_DESC,
        reply_markup=make_step_nav_kb(can_skip=False),
        parse_mode="HTML"
    )
    await callback.answer()


@candidate_router.message(CandidateForm.experience)
async def process_experience_text(message: types.Message, state: FSMContext):
    exp = (message.text or "").strip()
    await state.update_data(experience=exp)
    await state.set_state(CandidateForm.education_level)
    await safe_answer(
        message,
        texts.SURVEY_STEP8_EDUCATION,
        reply_markup=make_step8_education_kb(),
        parse_mode="HTML"
    )


# --- ШАГ 8: ОБРАЗОВАНИЕ ---
@candidate_router.callback_query(CandidateForm.education_level, F.data.startswith("edu_"))
async def cb_education_choice(callback: types.CallbackQuery, state: FSMContext):
    edu = callback.data.replace("edu_", "")
    if edu == "manual":
        await state.set_state(CandidateForm.education_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP8_MANUAL,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML"
        )
        return await callback.answer()

    await state.update_data(edu_level=edu)
    await state.set_state(CandidateForm.education_facility)
    await callback.message.edit_text(
        f"🎓 Уровень образования: <b>{edu}</b>\n\n" + texts.SURVEY_STEP8_1_FACILITY,
        reply_markup=make_step_nav_kb(can_skip=True),
        parse_mode="HTML"
    )
    await callback.answer()


@candidate_router.message(CandidateForm.education_manual)
async def process_education_manual(message: types.Message, state: FSMContext):
    edu = (message.text or "").strip()
    await state.update_data(edu_level=edu)
    await state.set_state(CandidateForm.education_facility)
    await safe_answer(
        message,
        texts.SURVEY_STEP8_1_FACILITY,
        reply_markup=make_step_nav_kb(can_skip=True),
        parse_mode="HTML"
    )


@candidate_router.message(CandidateForm.education_facility)
async def process_education_facility(message: types.Message, state: FSMContext):
    facility = (message.text or "").strip()
    data = await state.get_data()
    edu_level = data.get("edu_level", "Среднее")
    full_edu = f"{edu_level}, {facility}" if facility else edu_level
    await state.update_data(education=full_edu)

    await state.set_state(CandidateForm.relocation)
    await safe_answer(
        message,
        texts.SURVEY_STEP9_RELOCATION,
        reply_markup=make_step9_relocation_kb(),
        parse_mode="HTML"
    )


# --- ШАГ 9: ГОТОВНОСТЬ К ПЕРЕЕЗДУ ---
@candidate_router.callback_query(CandidateForm.relocation, F.data.in_(["reloc_yes", "reloc_no", "reloc_manual"]))
async def cb_relocation(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "reloc_manual":
        await state.set_state(CandidateForm.relocation_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP9_MANUAL,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML"
        )
        return await callback.answer()

    val = "Да" if callback.data == "reloc_yes" else "Нет"
    await state.update_data(relocation=val)
    await state.set_state(CandidateForm.dormitory)
    await callback.message.edit_text(
        f"🏠 Переезд в Ульяновск: <b>{val}</b>\n\n" + texts.SURVEY_STEP10_DORMITORY,
        reply_markup=make_step10_dormitory_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@candidate_router.message(CandidateForm.relocation_manual)
async def process_relocation_manual(message: types.Message, state: FSMContext):
    val = (message.text or "").strip()
    await state.update_data(relocation=val)
    await state.set_state(CandidateForm.dormitory)
    await safe_answer(
        message,
        texts.SURVEY_STEP10_DORMITORY,
        reply_markup=make_step10_dormitory_kb(),
        parse_mode="HTML"
    )


# --- ШАГ 10: ОБЩЕЖИТИЕ ---
@candidate_router.callback_query(CandidateForm.dormitory, F.data.in_(["dorm_yes", "dorm_no", "dorm_manual"]))
async def cb_dormitory(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "dorm_manual":
        await state.set_state(CandidateForm.dormitory_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP10_MANUAL,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML"
        )
        return await callback.answer()

    val = "Да" if callback.data == "dorm_yes" else "Нет"
    await state.update_data(dormitory=val)
    await state.set_state(CandidateForm.schedule)
    await callback.message.edit_text(
        f"🛏 Потребность в общежитии: <b>{val}</b>\n\n" + texts.SURVEY_STEP11_SCHEDULE,
        reply_markup=make_step11_schedule_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@candidate_router.message(CandidateForm.dormitory_manual)
async def process_dormitory_manual(message: types.Message, state: FSMContext):
    val = (message.text or "").strip()
    await state.update_data(dormitory=val)
    await state.set_state(CandidateForm.schedule)
    await safe_answer(
        message,
        texts.SURVEY_STEP11_SCHEDULE,
        reply_markup=make_step11_schedule_kb(),
        parse_mode="HTML"
    )


# --- ШАГ 11: СМЕННЫЙ ГРАФИК ---
@candidate_router.callback_query(CandidateForm.schedule, F.data.in_(["sched_yes", "sched_no", "sched_manual"]))
async def cb_schedule(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "sched_manual":
        await state.set_state(CandidateForm.schedule_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP11_MANUAL,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML"
        )
        return await callback.answer()

    val = "Да" if callback.data == "sched_yes" else "Нет"
    await state.update_data(shift_work=val)
    await state.set_state(CandidateForm.health)
    await callback.message.edit_text(
        f"🕐 Сменный график: <b>{val}</b>\n\n" + texts.SURVEY_STEP12_HEALTH,
        reply_markup=make_step12_health_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@candidate_router.message(CandidateForm.schedule_manual)
async def process_schedule_manual(message: types.Message, state: FSMContext):
    val = (message.text or "").strip()
    await state.update_data(shift_work=val)
    await state.set_state(CandidateForm.health)
    await safe_answer(
        message,
        texts.SURVEY_STEP12_HEALTH,
        reply_markup=make_step12_health_kb(),
        parse_mode="HTML"
    )


# --- ШАГ 12: МЕДИЦИНСКИЕ ПРОТИВОПОКАЗАНИЯ ---
@candidate_router.callback_query(CandidateForm.health, F.data.in_(["health_yes", "health_no", "health_manual"]))
async def cb_health(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "health_manual":
        await state.set_state(CandidateForm.health_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP12_MANUAL,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML"
        )
        return await callback.answer()

    val = "Нет" if callback.data == "health_no" else "Да"
    await state.update_data(medical_restrictions=val)
    await state.set_state(CandidateForm.criminal)
    await callback.message.edit_text(
        f"⚕️ Противопоказания: <b>{val}</b>\n\n" + texts.SURVEY_STEP13_CRIMINAL,
        reply_markup=make_step13_criminal_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@candidate_router.message(CandidateForm.health_manual)
async def process_health_manual(message: types.Message, state: FSMContext):
    val = (message.text or "").strip()
    await state.update_data(medical_restrictions=val)
    await state.set_state(CandidateForm.criminal)
    await safe_answer(
        message,
        texts.SURVEY_STEP13_CRIMINAL,
        reply_markup=make_step13_criminal_kb(),
        parse_mode="HTML"
    )


# --- ШАГ 13: СУДИМОСТИ (СТ. 86 УК РФ) ---
@candidate_router.callback_query(CandidateForm.criminal, F.data.in_(["crim_yes", "crim_no", "crim_manual"]))
async def cb_criminal(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "crim_manual":
        await state.set_state(CandidateForm.criminal_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP13_MANUAL,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML"
        )
        return await callback.answer()

    val = "Нет" if callback.data == "crim_no" else "Да"
    await state.update_data(criminal_record=val)
    await state.set_state(CandidateForm.source)
    await callback.message.edit_text(
        f"⚖️ Судимость (ст. 86 УК): <b>{val}</b>\n\n" + texts.SURVEY_STEP14_SOURCE,
        reply_markup=make_step14_source_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@candidate_router.message(CandidateForm.criminal_manual)
async def process_criminal_manual(message: types.Message, state: FSMContext):
    val = (message.text or "").strip()
    await state.update_data(criminal_record=val)
    await state.set_state(CandidateForm.source)
    await safe_answer(
        message,
        texts.SURVEY_STEP14_SOURCE,
        reply_markup=make_step14_source_kb(),
        parse_mode="HTML"
    )


# --- ШАГ 14: ИСТОЧНИК ИНФОРМАЦИИ ---
@candidate_router.callback_query(CandidateForm.source, F.data.startswith("src_"))
async def cb_source(callback: types.CallbackQuery, state: FSMContext):
    s = callback.data.replace("src_", "")
    if s == "manual":
        await state.set_state(CandidateForm.source_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP14_MANUAL,
            reply_markup=make_step_nav_kb(can_skip=True),
            parse_mode="HTML"
        )
        return await callback.answer()
    elif s == "skip":
        s = "Не указан"

    await state.update_data(source=s)
    await state.set_state(CandidateForm.extra_info)
    await callback.message.edit_text(
        f"📢 Источник: <b>{s}</b>\n\n" + texts.SURVEY_STEP15_EXTRA,
        reply_markup=make_step_nav_kb(can_skip=True),
        parse_mode="HTML"
    )
    await callback.answer()


@candidate_router.message(CandidateForm.source_manual)
async def process_source_manual(message: types.Message, state: FSMContext):
    s = (message.text or "").strip()
    await state.update_data(source=s)
    await state.set_state(CandidateForm.extra_info)
    await safe_answer(
        message,
        texts.SURVEY_STEP15_EXTRA,
        reply_markup=make_step_nav_kb(can_skip=True),
        parse_mode="HTML"
    )


# --- ШАГ 15: ДОПОЛНИТЕЛЬНЫЕ СВЕДЕНИЯ ---
@candidate_router.message(CandidateForm.extra_info)
async def process_extra_info(message: types.Message, state: FSMContext):
    extra = (message.text or "").strip()
    await state.update_data(extra_info=extra)
    data = await state.get_data()

    await state.set_state(CandidateForm.confirm_review)
    review_text = texts.format_survey_step16_review(data)
    await safe_answer(
        message,
        review_text,
        reply_markup=make_step16_confirm_kb(),
        parse_mode="HTML"
    )


# --- ШАГ 16: ПОДТВЕРЖДЕНИЕ И ПРАВКА ДАННЫХ ---
@candidate_router.callback_query(CandidateForm.confirm_review, F.data == "cand_edit_fields_menu")
async def cb_edit_fields_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(CandidateForm.edit_field_select)
    await callback.message.edit_text(
        texts.SURVEY_STEP16_EDIT_MENU,
        reply_markup=make_step16_edit_menu_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@candidate_router.callback_query(CandidateForm.edit_field_select, F.data == "edit_back_review")
async def cb_edit_back_review(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.set_state(CandidateForm.confirm_review)
    await callback.message.edit_text(
        texts.format_survey_step16_review(data),
        reply_markup=make_step16_confirm_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@candidate_router.callback_query(CandidateForm.edit_field_select, F.data.startswith("edit_"))
async def cb_edit_field_pick(callback: types.CallbackQuery, state: FSMContext):
    field_code = callback.data.replace("edit_", "")
    await state.update_data(edit_target_field=field_code)
    await state.set_state(CandidateForm.edit_field_input)

    field_prompts = {
        "fio": "Введите новые Фамилию, Имя и Отчество:",
        "birth": "Введите новую дату рождения (ДД.ММ.ГГГГ):",
        "phone": "Введите новый номер телефона (+79001234567):",
        "city": "Введите ваш город проживания:",
        "vac": "Введите желаемую должность:",
        "lic": "Укажите наличие водительских прав и категории:",
        "exp": "Опишите ваш опыт работы или стаж:",
        "edu": "Укажите ваш уровень образования и учебное заведение:",
        "reloc": "Укажите готовность к переезду:",
        "dorm": "Укажите потребность в общежитии:",
        "sched": "Укажите предпочтения по сменному графику:",
        "health": "Укажите медицинские противопоказания:",
        "crim": "Укажите информацию о судимостях (ст. 86 УК):",
        "src": "Укажите источник информации о вакансии:",
        "extra": "Укажите дополнительные сведения:"
    }
    prompt = field_prompts.get(field_code, "Введите новое значение:")
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад", callback_data="edit_back_review")
    await callback.message.edit_text(f"✏️ <b>{prompt}</b>", reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@candidate_router.message(CandidateForm.edit_field_input)
async def process_edit_field_input(message: types.Message, state: FSMContext):
    val = (message.text or "").strip()
    data = await state.get_data()
    field_code = data.get("edit_target_field", "")

    field_map = {
        "fio": "full_name",
        "birth": "birth_date",
        "phone": "phone",
        "city": "city",
        "vac": "vacancy",
        "lic": "driver_license",
        "exp": "experience",
        "edu": "education",
        "reloc": "relocation",
        "dorm": "dormitory",
        "sched": "shift_work",
        "health": "medical_restrictions",
        "crim": "criminal_record",
        "src": "source",
        "extra": "extra_info"
    }
    target_key = field_map.get(field_code)
    if target_key:
        await state.update_data({target_key: val})

    new_data = await state.get_data()
    await state.set_state(CandidateForm.confirm_review)
    await safe_answer(
        message,
        "✅ <b>Данные обновлены!</b>\n\n" + texts.format_survey_step16_review(new_data),
        reply_markup=make_step16_confirm_kb(),
        parse_mode="HTML"
    )


# --- ФИНАЛ: ОТПРАВКА АНКЕТЫ ---
@candidate_router.callback_query(CandidateForm.confirm_review, F.data == "cand_submit_final")
async def cb_submit_final(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()

    user_id = str(callback.from_user.id)
    full_name = data.get("full_name", "Не указано")
    phone = data.get("phone", "Не указан")
    vacancy = data.get("vacancy", "Не выбрана")
    experience = data.get("experience", "Без опыта")
    consent_ts = data.get("consent_timestamp", datetime.now().strftime("%d.%m.%Y %H:%M"))

    birth_date = data.get("birth_date", "")
    city = data.get("city", "")
    driver_license = data.get("driver_license", "")
    education = data.get("education", "")
    relocation = data.get("relocation", "")
    dormitory = data.get("dormitory", "")
    shift_work = data.get("shift_work", "")
    medical_restrictions = data.get("medical_restrictions", "")
    criminal_record = data.get("criminal_record", "")
    source = data.get("source", "")
    extra_info = data.get("extra_info", "")

    is_admin = (int(user_id) == CONFIG.get("SUPER_ADMIN_ID") or is_hr_admin(int(user_id)) or is_tech_admin(int(user_id)))
    is_test_cand = is_admin and (CONFIG.get("ENVIRONMENT") == "TEST" or "тест" in full_name.lower())

    # Сохранение в базу со всеми 16 полями
    ticket_id = db.add_candidate(
        platform="tg",
        user_id=user_id,
        full_name=full_name,
        phone=phone,
        vacancy=vacancy,
        experience=experience,
        is_test=is_test_cand,
        consent_timestamp=consent_ts,
        birth_date=birth_date,
        city=city,
        driver_license=driver_license,
        education=education,
        relocation=relocation,
        dormitory=dormitory,
        shift_work=shift_work,
        medical_restrictions=medical_restrictions,
        criminal_record=criminal_record,
        source=source,
        extra_info=extra_info,
        raw_data_json=json.dumps(data, ensure_ascii=False)
    )

    # Проверка водительской вакансии без опыта (умный триггер бесплатного обучения)
    is_driver_no_exp = ("водитель" in vacancy.lower() and ("без опыта" in experience.lower() or not experience))

    user_success_text = texts.format_survey_success(ticket_id, vacancy, is_driver_no_exp)
    await callback.message.edit_text(
        user_success_text,
        reply_markup=make_candidate_main_keyboard(user_id=int(user_id)),
        parse_mode="HTML"
    )
    await callback.answer("Анкета успешно отправлена!")

    # Формирование карточки для отдела кадров (в группу и админам)
    db_label = "<code>Тестовая запись</code>" if is_test_cand else "<code>resumes.db</code>"
    prefix = "🧪 ТЕСТОВАЯ АНКЕТА" if is_test_cand else "📑 НОВАЯ АНКЕТА"
    recom_line = "\n💡 <b>Рекомендация:</b> кандидат без опыта, можно предложить обучение\n" if is_driver_no_exp else ""

    hr_card = (
        f"<b>{prefix} СОИСКАТЕЛЯ #{ticket_id} [TG]</b>\n"
        f"📁 <b>База:</b> {db_label}\n"
        f"👤 <b>ФИО:</b> {html.escape(full_name)}\n"
        f"🎂 <b>Дата рождения:</b> <code>{html.escape(birth_date or 'Не указана')}</code>\n"
        f"📞 <b>Телефон:</b> <code>{html.escape(phone)}</code>\n"
        f"🏙 <b>Город:</b> {html.escape(city or 'Не указан')}\n"
        f"🎯 <b>Должность:</b> <b>{html.escape(vacancy)}</b>\n"
        f"🚗 <b>Водительские права:</b> {html.escape(driver_license or 'Нет')}\n"
        f"💼 <b>Опыт работы:</b> {html.escape(experience)}\n"
        f"🎓 <b>Образование:</b> {html.escape(education or 'Не указано')}\n"
        f"🏠 <b>Готовность к переезду:</b> {html.escape(relocation or 'Нет')}\n"
        f"🛏 <b>Общежитие:</b> {html.escape(dormitory or 'Нет')}\n"
        f"🕐 <b>Сменный график:</b> {html.escape(shift_work or 'Да')}\n"
        f"⚕️ <b>Противопоказания:</b> {html.escape(medical_restrictions or 'Нет')}\n"
        f"⚖️ <b>Судимость (ст. 86 УК):</b> {html.escape(criminal_record or 'Нет')}\n"
        f"📢 <b>Источник:</b> {html.escape(source or 'Не указан')}\n"
        f"📎 <b>Дополнительно:</b> {html.escape(extra_info or 'Нет')}\n"
        f"⚖️ <b>Согласие 152-ФЗ:</b> <code>✅ Получено ({consent_ts})</code>\n"
        f"⏱ <b>Время подачи:</b> <code>{datetime.now().strftime('%d.%m.%Y %H:%M')}</code>\n"
        f"{recom_line}"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Действия кадровой службы:</i>"
    )
    ticket_kb = make_ticket_keyboard(ticket_id)
    await route_new_candidate_ticket(bot, hr_card, ticket_kb)


# =====================================================================
# КНОПКИ НАВИГАЦИИ (ПРОПУСТИТЬ, НАЗАД)
# =====================================================================

@candidate_router.callback_query(F.data == "cand_nav_skip")
@candidate_router.message(Command("skip"))
async def cb_nav_skip(event: types.CallbackQuery | types.Message, state: FSMContext):
    cur_state = await state.get_state()
    if cur_state == CandidateForm.education_facility.state:
        data = await state.get_data()
        edu_level = data.get("edu_level", "Среднее")
        await state.update_data(education=edu_level)
        await state.set_state(CandidateForm.relocation)
        text = texts.SURVEY_STEP9_RELOCATION
        kb = make_step9_relocation_kb()
    elif cur_state in (CandidateForm.source.state, CandidateForm.source_manual.state):
        await state.update_data(source="Не указан")
        await state.set_state(CandidateForm.extra_info)
        text = texts.SURVEY_STEP15_EXTRA
        kb = make_step_nav_kb(can_skip=True)
    elif cur_state == CandidateForm.extra_info.state:
        await state.update_data(extra_info="Нет")
        data = await state.get_data()
        await state.set_state(CandidateForm.confirm_review)
        text = texts.format_survey_step16_review(data)
        kb = make_step16_confirm_kb()
    else:
        if isinstance(event, types.CallbackQuery):
            return await event.answer("Этот шаг обязателен.", show_alert=True)
        return await safe_answer(event, "Этот шаг обязателен, пожалуйста, заполните его.")

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        await event.answer("Шаг пропущен")
    else:
        await safe_answer(event, text, reply_markup=kb, parse_mode="HTML")


@candidate_router.callback_query(F.data == "cand_nav_back")
@candidate_router.message(Command("back"))
async def cb_nav_back(event: types.CallbackQuery | types.Message, state: FSMContext):
    cur_state = await state.get_state()
    data = await state.get_data()

    step_transitions = {
        CandidateForm.birth_date.state: (CandidateForm.full_name, texts.SURVEY_STEP1_NAME, make_step_nav_kb(can_skip=False)),
        CandidateForm.phone.state: (CandidateForm.birth_date, texts.SURVEY_STEP2_BIRTHDATE, make_step2_birthdate_kb()),
        CandidateForm.city.state: (CandidateForm.phone, texts.format_survey_ask_phone(data.get("full_name", "")), make_phone_reply_keyboard()),
        CandidateForm.city_manual.state: (CandidateForm.city, texts.SURVEY_STEP4_CITY, make_step4_city_kb()),
        CandidateForm.vacancy.state: (CandidateForm.city, texts.SURVEY_STEP4_CITY, make_step4_city_kb()),
        CandidateForm.custom_vacancy.state: (CandidateForm.vacancy, texts.SURVEY_STEP5_VACANCY, make_step5_vacancies_kb()),
        CandidateForm.has_license.state: (CandidateForm.vacancy, texts.SURVEY_STEP5_VACANCY, make_step5_vacancies_kb()),
        CandidateForm.license_categories.state: (CandidateForm.has_license, texts.SURVEY_STEP6_LICENSE, make_step6_license_kb()),
        CandidateForm.license_categories_manual.state: (CandidateForm.has_license, texts.SURVEY_STEP6_LICENSE, make_step6_license_kb()),
        CandidateForm.has_experience.state: (CandidateForm.has_license, texts.SURVEY_STEP6_LICENSE, make_step6_license_kb()),
        CandidateForm.experience.state: (CandidateForm.has_experience, texts.SURVEY_STEP7_EXPERIENCE, make_step7_experience_kb()),
        CandidateForm.education_level.state: (CandidateForm.has_experience, texts.SURVEY_STEP7_EXPERIENCE, make_step7_experience_kb()),
        CandidateForm.education_manual.state: (CandidateForm.education_level, texts.SURVEY_STEP8_EDUCATION, make_step8_education_kb()),
        CandidateForm.education_facility.state: (CandidateForm.education_level, texts.SURVEY_STEP8_EDUCATION, make_step8_education_kb()),
        CandidateForm.relocation.state: (CandidateForm.education_level, texts.SURVEY_STEP8_EDUCATION, make_step8_education_kb()),
        CandidateForm.relocation_manual.state: (CandidateForm.relocation, texts.SURVEY_STEP9_RELOCATION, make_step9_relocation_kb()),
        CandidateForm.dormitory.state: (CandidateForm.relocation, texts.SURVEY_STEP9_RELOCATION, make_step9_relocation_kb()),
        CandidateForm.dormitory_manual.state: (CandidateForm.dormitory, texts.SURVEY_STEP10_DORMITORY, make_step10_dormitory_kb()),
        CandidateForm.schedule.state: (CandidateForm.dormitory, texts.SURVEY_STEP10_DORMITORY, make_step10_dormitory_kb()),
        CandidateForm.schedule_manual.state: (CandidateForm.schedule, texts.SURVEY_STEP11_SCHEDULE, make_step11_schedule_kb()),
        CandidateForm.health.state: (CandidateForm.schedule, texts.SURVEY_STEP11_SCHEDULE, make_step11_schedule_kb()),
        CandidateForm.health_manual.state: (CandidateForm.health, texts.SURVEY_STEP12_HEALTH, make_step12_health_kb()),
        CandidateForm.criminal.state: (CandidateForm.health, texts.SURVEY_STEP12_HEALTH, make_step12_health_kb()),
        CandidateForm.criminal_manual.state: (CandidateForm.criminal, texts.SURVEY_STEP13_CRIMINAL, make_step13_criminal_kb()),
        CandidateForm.source.state: (CandidateForm.criminal, texts.SURVEY_STEP13_CRIMINAL, make_step13_criminal_kb()),
        CandidateForm.source_manual.state: (CandidateForm.source, texts.SURVEY_STEP14_SOURCE, make_step14_source_kb()),
        CandidateForm.extra_info.state: (CandidateForm.source, texts.SURVEY_STEP14_SOURCE, make_step14_source_kb()),
        CandidateForm.confirm_review.state: (CandidateForm.extra_info, texts.SURVEY_STEP15_EXTRA, make_step_nav_kb(can_skip=True)),
        CandidateForm.edit_field_select.state: (CandidateForm.confirm_review, texts.format_survey_step16_review(data), make_step16_confirm_kb()),
        CandidateForm.edit_field_input.state: (CandidateForm.confirm_review, texts.format_survey_step16_review(data), make_step16_confirm_kb()),
    }

    if cur_state in step_transitions:
        prev_state, txt, kb = step_transitions[cur_state]
        await state.set_state(prev_state)
        if isinstance(event, types.CallbackQuery):
            await event.message.edit_text(txt, reply_markup=kb, parse_mode="HTML")
            await event.answer("Назад")
        else:
            await safe_answer(event, txt, reply_markup=kb, parse_mode="HTML")
    else:
        await state.clear()
        if isinstance(event, types.CallbackQuery):
            await event.message.edit_text(
                texts.MENU_RETURN,
                reply_markup=make_candidate_main_keyboard(user_id=event.from_user.id),
                parse_mode="HTML"
            )
            await event.answer()
        else:
            await safe_answer(
                event,
                texts.MENU_RETURN,
                reply_markup=make_candidate_main_keyboard(user_id=event.from_user.id),
                parse_mode="HTML"
            )
