# -*- coding: utf-8 -*-
"""
Обработчики диалогов с соискателями:
- Главное меню, команды /start, /help, /id, /contacts, /privacy
- Подача анкеты на работу (/apply, FSM CandidateForm)
- Вопросы специалисту кадров (/ask, FSM InquiryForm)
- База знаний предприятия (FAQ)
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
    route_new_candidate_ticket,
    route_new_inquiry_ticket,
    sync_user_commands
)
from keyboards import (
    make_candidate_main_keyboard,
    make_phone_reply_keyboard,
    make_faq_keyboard,
    make_ticket_keyboard,
    make_inquiry_admin_keyboard
)

logger = logging.getLogger(__name__)
candidate_router = Router(name="candidate")


@candidate_router.message(Command("cancel"))
@candidate_router.message(F.text.lower().in_(["/cancel", "отмена", "отменить"]))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await safe_answer(
        message,
        texts.ACTION_CANCELLED,
        reply_markup=make_candidate_main_keyboard(),
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
        reply_markup=make_candidate_main_keyboard(),
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
    is_super = (user_id == CONFIG.get("SUPER_ADMIN_ID"))
    is_tech = is_tech_admin(user_id)
    is_hr = is_hr_admin(user_id)
    help_text = texts.format_help_text(is_super, is_tech, is_hr)
    await safe_answer(message, help_text, reply_markup=make_candidate_main_keyboard(), parse_mode="HTML")


@candidate_router.message(Command("id"))
async def cmd_id(message: types.Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    chat_type = message.chat.type
    info_text = texts.format_id_text(user_id, chat_id, chat_type)

    is_admin_user = (user_id == CONFIG.get("SUPER_ADMIN_ID") or is_hr_admin(user_id) or is_tech_admin(user_id))
    if is_admin_user and chat_type != "private":
        info_text += (
            "\n━━━━━━━━━━━━━━━━━━━━━\n"
            "ℹ️ <i>Чтобы привязать эту группу для получения анкет, отправьте:</i>\n"
            "<code>/set_group</code>"
        )
    await safe_answer(message, info_text, parse_mode="HTML")


@candidate_router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext, bot: Bot):
    await state.clear()
    user_id = message.from_user.id
    # При каждом /start проверяем актуальную роль и очищаем/обновляем команды:
    await sync_user_commands(bot, user_id)
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

    await safe_answer(message, texts.START_WELCOME, reply_markup=make_candidate_main_keyboard(), parse_mode="HTML")


@candidate_router.callback_query(F.data.in_(["cand_my_application", "cand_status"]))
@candidate_router.message(Command("my", "status"))
async def cb_cand_my_application(event: types.CallbackQuery | types.Message):
    user_id = event.from_user.id
    if db.is_blocked(user_id, super_admin_id=CONFIG.get("SUPER_ADMIN_ID")):
        if isinstance(event, types.CallbackQuery):
            return await event.answer("⛔ Доступ ограничен (вы в черном списке).", show_alert=True)
        return await safe_answer(
            event,
            "⛔ <b>Доступ ограничен</b>\n\n"
            "Ваш аккаунт находится в чёрном списке.\n"
            f"Контакты отдела кадров: <code>{CONFIG['HR_PHONE']}</code>",
            parse_mode="HTML"
        )

    cand = db.get_candidate_by_user_id(str(user_id), platform="tg")
    builder = InlineKeyboardBuilder()

    if cand:
        ticket_id = cand[0]
        name = cand[3]
        phone = cand[4]
        vac = cand[5]
        exp = cand[6]
        st = cand[7]
        created = cand[9] if len(cand) > 9 else ""
        text = texts.format_my_application(ticket_id, st, vac, name, created)
        builder.button(text="💬 Задать вопрос по анкете", callback_data="cand_ask_question")
    else:
        text = texts.APP_NOT_FOUND
        builder.button(text=texts.BTN_APPLY, callback_data="cand_start_apply")

    builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
    builder.adjust(1)

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, text, reply_markup=builder.as_markup(), parse_mode="HTML")


@candidate_router.callback_query(F.data.in_(["cand_hr_contacts", "cand_contacts"]))
@candidate_router.message(Command("contacts"))
async def cb_cand_hr_contacts(event: types.CallbackQuery | types.Message):
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_PRIVACY, callback_data="cand_privacy_policy")
    builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
    builder.adjust(1, 1)

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(texts.CONTACTS_SCREEN, reply_markup=builder.as_markup(), parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, texts.CONTACTS_SCREEN, reply_markup=builder.as_markup(), parse_mode="HTML")


@candidate_router.callback_query(F.data == "cand_privacy_policy")
@candidate_router.message(Command("privacy", "policy"))
async def cb_cand_privacy_policy(event: types.CallbackQuery | types.Message):
    """Официальная политика обработки персональных данных (ст. 18.1 152-ФЗ РФ)."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_APPLY, callback_data="cand_start_apply")
    builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
    builder.adjust(1, 1)

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(texts.PRIVACY_POLICY_TEXT, reply_markup=builder.as_markup(), parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, texts.PRIVACY_POLICY_TEXT, reply_markup=builder.as_markup(), parse_mode="HTML")


@candidate_router.callback_query(F.data == "cand_back_to_menu")
async def cb_cand_back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(texts.MENU_RETURN, reply_markup=make_candidate_main_keyboard(), parse_mode="HTML")
    await callback.answer()


@candidate_router.callback_query(F.data.in_(["cand_faq_menu", "cand_faq"]))
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
    text = getattr(texts, "FAQ_DATA", {}).get(item_key, "Информация обновляется...")
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад к вопросам (FAQ)", callback_data="cand_faq_menu")
    builder.button(text=getattr(texts, "BTN_APPLY", "📝 Подать анкету"), callback_data="cand_start_apply")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@candidate_router.callback_query(F.data.startswith("cand_reply_hr_"))
async def cb_cand_reply_hr(callback: types.CallbackQuery, state: FSMContext):
    ticket_id = int(callback.data.split("_")[3])
    await state.set_state(InquiryForm.waiting_question)
    await state.update_data(ticket_id=ticket_id)
    text = texts.format_hr_reply_prompt(ticket_id)
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_back_to_menu")
    await callback.message.reply(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@candidate_router.callback_query(F.data.in_(["cand_ask_question", "cand_start_inquiry"]))
@candidate_router.message(Command("ask"))
async def cb_cand_ask_question(event: types.CallbackQuery | types.Message, state: FSMContext):
    user_id = event.from_user.id
    is_admin = (user_id == CONFIG.get("SUPER_ADMIN_ID") or is_hr_admin(user_id) or is_tech_admin(user_id))

    if db.is_blocked(user_id, super_admin_id=CONFIG.get("SUPER_ADMIN_ID")):
        if isinstance(event, types.CallbackQuery):
            return await event.answer("⛔ Доступ ограничен (вы в черном списке).", show_alert=True)
        return await safe_answer(
            event,
            "⛔ <b>Отправка вопросов недоступна</b>\n\n"
            "Ваш аккаунт находится в чёрном списке.\n"
            f"Контакты отдела кадров: <code>{CONFIG['HR_PHONE']}</code>",
            parse_mode="HTML"
        )

    if CONFIG.get("MAINTENANCE_MODE") and not is_admin:
        if isinstance(event, types.CallbackQuery):
            await event.message.edit_text(texts.MAINTENANCE_ACTIVE, parse_mode="HTML")
            return await event.answer()
        return await safe_answer(event, texts.MAINTENANCE_ACTIVE, parse_mode="HTML")

    is_prod = (CONFIG.get("ENVIRONMENT") == "PROD")
    cooldown = int(db.get_setting("cooldown_seconds", str(CONFIG.get("COOLDOWN_SECONDS", 1200)))) if is_prod else 0
    can_ask, seconds_left = db.check_inquiry_cooldown(str(user_id), cooldown)
    if not can_ask:
        minutes_left = max(1, (seconds_left + 59) // 60)
        msg_text = texts.format_inquiry_cooldown(minutes_left)
        if isinstance(event, types.CallbackQuery):
            return await event.answer(msg_text, show_alert=True)
        return await safe_answer(event, msg_text)

    existing_consent = db.get_user_consent(user_id)
    if existing_consent:
        await state.set_state(InquiryForm.waiting_question)
        await state.update_data(consent_timestamp=existing_consent)
        builder = InlineKeyboardBuilder()
        builder.button(text=texts.BTN_CANCEL, callback_data="cand_back_to_menu")
        if isinstance(event, types.CallbackQuery):
            await event.message.edit_text(texts.INQUIRY_INPUT_PROMPT, reply_markup=builder.as_markup(), parse_mode="HTML")
            await event.answer()
        else:
            await safe_answer(event, texts.INQUIRY_INPUT_PROMPT, reply_markup=builder.as_markup(), parse_mode="HTML")
        return

    await state.set_state(InquiryForm.waiting_consent)
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CONSENT_YES, callback_data="cand_consent_ask")
    builder.button(text=texts.BTN_READ_PRIVACY, callback_data="cand_privacy_policy")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_back_to_menu")
    builder.adjust(1, 2)
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(texts.CONSENT_INQUIRY_PROMPT, reply_markup=builder.as_markup(), parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, texts.CONSENT_INQUIRY_PROMPT, reply_markup=builder.as_markup(), parse_mode="HTML")


@candidate_router.callback_query(InquiryForm.waiting_consent, F.data == "cand_consent_ask")
async def cb_cand_consent_ask(callback: types.CallbackQuery, state: FSMContext):
    consent_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.set_user_consent(callback.from_user.id, consent_ts)
    await state.update_data(consent_timestamp=consent_ts)
    await state.set_state(InquiryForm.waiting_question)

    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_back_to_menu")
    await callback.message.edit_text(texts.INQUIRY_INPUT_PROMPT, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer("Согласие принято")


@candidate_router.message(InquiryForm.waiting_consent)
async def process_inq_consent_fallback(message: types.Message, state: FSMContext, bot: Bot):
    q_cand_text = (message.text or "").strip()
    if q_cand_text and len(q_cand_text) >= 3 and not q_cand_text.startswith("/"):
        consent_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.set_user_consent(message.from_user.id, consent_ts)
        await state.update_data(consent_timestamp=consent_ts)
        return await process_inquiry_message(message, state, bot)

    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CONSENT_YES, callback_data="cand_consent_ask")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_back_to_menu")
    builder.adjust(1, 1)
    await safe_answer(
        message,
        "⚠️ Для отправки вопроса подтвердите согласие с обработкой данных (152-ФЗ):\n"
        "Нажмите кнопку <b>«✅ Согласен»</b> ниже или введите ваш вопрос:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@candidate_router.message(InquiryForm.waiting_question)
async def process_inquiry_message(message: types.Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    q_text = (message.text or "").strip()
    if len(q_text) > 1000:
        return await safe_answer(message, texts.ERR_QUESTION_TOO_LONG)
    if not q_text:
        return await safe_answer(message, "⚠️ Пожалуйста, напишите вопрос текстом в одном сообщении.")

    last_cand = db.get_candidate_by_user_id(str(user_id), platform="tg")
    if last_cand:
        ticket_id = last_cand[0]
        full_name = last_cand[3]
        phone = last_cand[4]
        vacancy = last_cand[5]
    else:
        ticket_id = None
        full_name = message.from_user.full_name or "Не указано"
        phone = "Не указан"
        vacancy = "Анкета не подана"

    data = await state.get_data()
    is_test_inq = (
        user_id in (CONFIG.get("SUPER_ADMIN_ID"), CONFIG.get("TECH_ADMIN_ID"))
        and (CONFIG.get("ENVIRONMENT") == "TEST" or "тест" in (q_text or "").lower())
    )
    consent_ts = data.get("consent_timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    inquiry_id = await db.async_add_inquiry(
        platform="tg",
        user_id=str(user_id),
        question_text=q_text,
        ticket_id=ticket_id,
        full_name=full_name,
        phone=phone,
        vacancy=vacancy,
        is_test=is_test_inq,
        consent_timestamp=consent_ts
    )

    await state.clear()
    conf_text = texts.format_inquiry_sent(inquiry_id)
    await safe_answer(message, conf_text, reply_markup=make_candidate_main_keyboard(), parse_mode="HTML")

    db_label = "<code>Тестовая запись</code>" if is_test_inq else "<code>Основная база (resumes.db)</code>"
    prefix = "🧪 ТЕСТОВОЕ ОБРАЩЕНИЕ" if is_test_inq else "📩 ОБРАЩЕНИЕ"
    card_text = (
        f"<b>{prefix} СОИСКАТЕЛЯ #{inquiry_id} [TG]</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 <b>База:</b> {db_label}\n"
        f"👤 <b>Кандидат:</b> {html.escape(full_name)}\n"
        f"📞 <b>Телефон:</b> <code>{html.escape(phone)}</code>\n"
        f"🎯 <b>Вакансия:</b> {html.escape(vacancy)}\n"
        f"⚖️ <b>Согласие 152-ФЗ:</b> <code>✅ Получено ({consent_ts})</code>\n"
        f"⏱ <b>Время:</b> <code>{datetime.now().strftime('%d.%m.%Y %H:%M')}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"❓ <b>Вопрос:</b>\n"
        f"«{html.escape(q_text)}»"
    )
    await route_new_inquiry_ticket(bot, card_text, reply_markup=make_inquiry_admin_keyboard(inquiry_id))


@candidate_router.callback_query(F.data == "cand_start_apply")
async def cb_cand_start_apply(callback: types.CallbackQuery, state: FSMContext):
    await run_candidate_survey(callback, state, callback.from_user.id)


@candidate_router.message(Command("apply"))
async def cmd_apply(message: types.Message, state: FSMContext):
    await run_candidate_survey(message, state, message.from_user.id)


async def run_candidate_survey(event: types.Message | types.CallbackQuery, state: FSMContext, user_id: int, force: bool = False):
    """Универсальный запуск анкеты: редактирует сообщение на месте (при клике) или отвечает в чат."""
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
                if is_admin:
                    builder.button(text="🧪 Сбросить анкету (для теста)", callback_data=f"dev_reset_apply_{ticket_id}")
                    builder.button(text="🧪 Всё равно продолжить (тест)", callback_data="dev_force_apply")
                builder.adjust(1)
                text = texts.format_already_applied(ticket_id, status, created, vac)
                return await send_or_edit(text, reply_markup=builder.as_markup())

            elif reason in ("cooldown", "rejected_cooldown"):
                days_left = info.get("days_left", 0)
                builder = InlineKeyboardBuilder()
                builder.button(text="💬 Связаться с отделом кадров", callback_data="cand_ask_question")
                builder.button(text="📚 Частые вопросы (FAQ)", callback_data="cand_faq_menu")
                builder.button(text="🏢 Контакты предприятия", callback_data="cand_hr_contacts")
                builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
                if is_admin:
                    builder.button(text="🧪 Сбросить отказ (для теста)", callback_data=f"dev_reset_apply_{ticket_id}")
                    builder.button(text="🧪 Всё равно продолжить (тест)", callback_data="dev_force_apply")
                builder.adjust(1)
                text = texts.format_rejection_cooldown(ticket_id, days_left)
                return await send_or_edit(text, reply_markup=builder.as_markup())

    existing_consent = db.get_user_consent(user_id)
    if existing_consent:
        await state.clear()
        await state.update_data(consent_timestamp=existing_consent)
        await state.set_state(CandidateForm.full_name)
        builder = InlineKeyboardBuilder()
        builder.button(text=texts.BTN_CANCEL, callback_data="cand_back_to_menu")
        return await send_or_edit(texts.SURVEY_START_NAME, reply_markup=builder.as_markup())

    await state.clear()
    await state.set_state(CandidateForm.waiting_consent)
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CONSENT_YES, callback_data="cand_consent_apply")
    builder.button(text=texts.BTN_READ_PRIVACY, callback_data="cand_privacy_policy")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_back_to_menu")
    builder.adjust(1, 2)
    await send_or_edit(texts.CONSENT_SURVEY_PROMPT, reply_markup=builder.as_markup())


@candidate_router.callback_query(F.data.startswith("dev_reset_apply_"))
async def cb_dev_reset_apply(callback: types.CallbackQuery, state: FSMContext):
    ticket_id = int(callback.data.split("_")[3])
    db.reset_candidate_for_test(str(callback.from_user.id), platform="tg")
    await callback.answer("Тестовая анкета сброшена!")
    await run_candidate_survey(callback, state, callback.from_user.id, force=True)


@candidate_router.callback_query(F.data == "dev_force_apply")
async def cb_dev_force_apply(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Тестовый запуск!")
    await run_candidate_survey(callback, state, callback.from_user.id, force=True)


@candidate_router.callback_query(CandidateForm.waiting_consent, F.data == "cand_consent_apply")
async def cb_cand_consent_apply(callback: types.CallbackQuery, state: FSMContext):
    consent_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.set_user_consent(callback.from_user.id, consent_ts)
    await state.update_data(consent_timestamp=consent_ts)
    await state.set_state(CandidateForm.full_name)

    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_back_to_menu")
    await callback.message.edit_text(texts.SURVEY_START_NAME, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer("Согласие зафиксировано")


@candidate_router.message(CandidateForm.waiting_consent)
async def process_apply_consent_fallback(message: types.Message, state: FSMContext):
    u_text = (message.text or "").strip()
    words = u_text.split()
    if len(words) >= 2:
        consent_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.set_user_consent(message.from_user.id, consent_ts)
        await state.update_data(consent_timestamp=consent_ts, full_name=u_text)
        await state.set_state(CandidateForm.phone)
        next_text = (
            f"✅ <b>Согласие 152-ФЗ зафиксировано!</b> (<code>{consent_ts}</code>)\n"
            f"👤 ФИО: <b>{html.escape(u_text)}</b>\n\n"
            + texts.format_survey_ask_phone(u_text)
        )
        return await message.answer(next_text, reply_markup=make_phone_reply_keyboard(), parse_mode="HTML")

    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CONSENT_YES, callback_data="cand_consent_apply")
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_back_to_menu")
    builder.adjust(1, 1)
    await safe_answer(
        message,
        "⚠️ Для заполнения анкеты необходимо подтвердить согласие с обработкой данных (152-ФЗ).\n\n"
        "Нажмите кнопку <b>«✅ Согласен»</b> ниже или введите ваши ФИО полностью:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@candidate_router.message(CandidateForm.full_name)
async def process_name(message: types.Message, state: FSMContext):
    raw_name = (message.text or "").strip()
    words = raw_name.split()

    if len(words) < 2:
        return await safe_answer(message, "⚠️ <b>Введите фамилию и имя полностью.</b>\n<i>Например: Иванов Алексей</i>", parse_mode="HTML")

    clean_letters = re.sub(r"[\s\-]", "", raw_name)
    if not clean_letters.isalpha():
        return await safe_answer(message, "⚠️ <b>ФИО может содержать только буквы и дефис.</b> Цифры не допускаются.", parse_mode="HTML")

    if re.search(r"(.)\1{3,}", raw_name.lower()):
        return await safe_answer(message, "⚠️ Укажите ваши реальные ФИО без повторяющихся символов.")

    stop_words = ["тест", "test", "фыва", "йцук", "анон", "никто", "не знаю"]
    if any(sw in raw_name.lower() for sw in stop_words):
        return await safe_answer(message, "⚠️ Введите корректные ФИО для кадровой службы:")

    formatted_name = " ".join(w.capitalize() for w in words)
    await state.update_data(full_name=formatted_name)
    await message.answer(texts.format_survey_ask_phone(formatted_name), reply_markup=make_phone_reply_keyboard(), parse_mode="HTML")
    await state.set_state(CandidateForm.phone)
async def ask_vacancy(message: types.Message, state: FSMContext):
    from common import get_all_vacancies
    builder = InlineKeyboardBuilder()

    val = db.get_setting("closed_vacancies", "[]")
    try:
        import json
        closed = set(json.loads(val))
    except Exception:
        closed = set()

    vacancies = get_all_vacancies()
    active_vacs = [v for v in vacancies if v not in closed]

    if not active_vacs:
        return await safe_answer(
            message,
            "ℹ️ <b>В настоящее время открытых вакансий нет.</b>\nПриём анкет временно приостановлен.",
            parse_mode="HTML"
        )

    for vac in active_vacs:
        builder.button(text=vac, callback_data=f"vac_{vac[:30]}")
    builder.button(text="Другая специальность / Резерв", callback_data="vac_other")
    builder.adjust(1)

    await safe_answer(message, texts.SURVEY_ASK_VACANCY, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(CandidateForm.vacancy)


@candidate_router.callback_query(CandidateForm.vacancy, F.data.startswith("vac_"))
async def process_vacancy_select(callback: types.CallbackQuery, state: FSMContext):
    from common import get_all_vacancies
    vac_raw = callback.data[4:]
    if vac_raw == "other":
        selected = "Другая специальность / Резерв"
    else:
        vacancies = get_all_vacancies()
        selected = next((v for v in vacancies if v.startswith(vac_raw)), vac_raw)

    await state.update_data(vacancy=selected)
    await callback.message.edit_text(
        texts.format_survey_ask_experience(selected),
        parse_mode="HTML"
    )
    await state.set_state(CandidateForm.experience)
    await callback.answer()

@candidate_router.message(CandidateForm.phone)
async def process_phone_text(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    digits = re.sub(r"\D", "", text)

    if len(digits) == 11 and digits[0] in ["7", "8"]:
        clean_phone = "+7" + digits[1:]
    elif len(digits) == 10 and digits[0] == "9":
        clean_phone = "+7" + digits
    else:
        return await safe_answer(
            message,
            "⚠️ <b>Некорректный номер телефона.</b> Введите номер из 10–11 цифр (например, <code>+79271234567</code>) или нажмите кнопку ниже:",
            reply_markup=make_phone_reply_keyboard(),
            parse_mode="HTML"
        )

    if len(set(clean_phone[2:])) <= 2 or clean_phone[2:] == "123456789":
        return await safe_answer(message, "⚠️ Номер похож на тестовый. Укажите реальный номер для связи:", reply_markup=make_phone_reply_keyboard(), parse_mode="HTML")

    await state.update_data(phone=clean_phone)
    await message.answer("✅ Номер телефона принят.", reply_markup=types.ReplyKeyboardRemove())
    await ask_vacancy(message, state)


@candidate_router.message(CandidateForm.experience)
async def process_experience(message: types.Message, state: FSMContext, bot: Bot):
    exp = (message.text or "").strip()

    if len(exp) < 4 or not any(c.isalpha() for c in exp):
        return await safe_answer(message, "⚠️ <b>Опишите опыт работы чуть подробнее</b> (хотя бы несколько слов, либо «Без опыта, готов обучаться»).")

    if len(exp) > 1000:
        exp = exp[:1000]

    await state.update_data(experience=exp)
    data = await state.get_data()
    await state.clear()

    full_name, phone, vacancy = data.get("full_name"), data.get("phone"), data.get("vacancy")
    user_id = message.from_user.id
    is_test_cand = (user_id in (CONFIG.get("SUPER_ADMIN_ID"), CONFIG.get("TECH_ADMIN_ID")))

    ticket_id = db.add_candidate(
        platform="tg", user_id=str(user_id), full_name=full_name,
        phone=phone, vacancy=vacancy, experience=exp,
        is_test=is_test_cand, consent_timestamp=data.get("consent_timestamp")
    )

    await safe_answer(message, texts.format_survey_success(ticket_id), reply_markup=make_candidate_main_keyboard(), parse_mode="HTML")
    admin_card = texts.format_admin_candidate_card(ticket_id, "TG", full_name, phone, vacancy, exp)
    await route_new_candidate_ticket(bot, admin_card, reply_markup=make_ticket_keyboard(ticket_id))

@candidate_router.callback_query(CandidateForm.vacancy, F.data.startswith("vac_"))
async def process_vacancy_select(callback: types.CallbackQuery, state: FSMContext):
    vac_raw = callback.data[4:]
    if vac_raw == "other":
        await state.update_data(vacancy="Другая должность")
        selected = "Другая должность"
    else:
        selected = next((v for v in VACANCIES if v.startswith(vac_raw)), vac_raw)
        await state.update_data(vacancy=selected)

    await callback.message.edit_text(
        texts.format_survey_ask_experience(selected),
        parse_mode="HTML"
    )
    await state.set_state(CandidateForm.experience)
    await callback.answer()


@candidate_router.message(CandidateForm.experience)
async def process_experience(message: types.Message, state: FSMContext, bot: Bot):
    exp = (message.text or "").strip()
    if len(exp) > 1000:
        exp = exp[:1000]
    await state.update_data(experience=exp)
    data = await state.get_data()
    await state.clear()

    full_name = data.get("full_name")
    phone = data.get("phone")
    vacancy = data.get("vacancy")
    experience = data.get("experience")
    user_id = message.from_user.id

    is_test_cand = (
        user_id in (CONFIG.get("SUPER_ADMIN_ID"), CONFIG.get("TECH_ADMIN_ID"))
        and (CONFIG.get("ENVIRONMENT") == "TEST" or "тест" in (full_name or "").lower())
    )
    consent_ts = data.get("consent_timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ticket_id = await db.async_add_candidate(
        "tg", str(user_id), full_name, phone, vacancy, experience,
        is_test=is_test_cand, consent_timestamp=consent_ts
    )

    user_success_text = texts.format_survey_success(ticket_id, full_name, vacancy, phone)
    await safe_answer(message, user_success_text, reply_markup=make_candidate_main_keyboard(), parse_mode="HTML")

    db_label = "<code>Тестовая запись</code>" if is_test_cand else "<code>Основная база (resumes.db)</code>"
    prefix = "🧪 ТЕСТОВАЯ АНКЕТА" if is_test_cand else "📑 НОВАЯ АНКЕТА"
    admin_card = (
        f"<b>{prefix} СОИСКАТЕЛЯ #{ticket_id} [TG]</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 <b>База:</b> {db_label}\n"
        f"👤 <b>ФИО:</b> {html.escape(full_name)}\n"
        f"📞 <b>Телефон:</b> <code>{html.escape(phone)}</code>\n"
        f"🎯 <b>Должность:</b> {html.escape(vacancy)}\n"
        f"💼 <b>Опыт работы:</b> {html.escape(experience)}\n"
        f"⚖️ <b>Согласие 152-ФЗ:</b> <code>✅ Получено ({consent_ts})</code>\n"
        f"⏱ <b>Время подачи:</b> <code>{datetime.now().strftime('%d.%m.%Y %H:%M')}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Действия кадровой службы:</i>"
    )
    await route_new_candidate_ticket(bot, admin_card, reply_markup=make_ticket_keyboard(ticket_id))
# ==============================================================================
# ЭКСТРЕННАЯ ТЕХПОДДЕРЖКА (/support, /sos)
# ==============================================================================
@candidate_router.message(Command("support", "sos", "tech_support"))
@candidate_router.message(F.text.lower().in_(["поддержка", "техподдержка", "сос", "sos", "/support", "/sos"]))
async def cmd_support_start(message: types.Message, state: FSMContext):
    """Старт сценария экстренной связи с инженером."""
    await state.set_state(SupportForm.waiting_message)
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_back_to_menu")
    await safe_answer(message, texts.SUPPORT_PROMPT, reply_markup=builder.as_markup(), parse_mode="HTML")


@candidate_router.message(SupportForm.waiting_message)
async def process_support_message(message: types.Message, state: FSMContext, bot: Bot):
    """Приём текста сбоя и отправка алертов техническим администраторам."""
    text = (message.text or "").strip()
    if not text:
        return await safe_answer(message, "⚠️ Пожалуйста, опишите проблему текстом в одном сообщении.")

    await state.clear()
    user_id = message.from_user.id
    user_name = message.from_user.full_name or "Пользователь"
    if message.from_user.username:
        user_name += f" (@{message.from_user.username})"

    time_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    alert_text = texts.format_support_alert(
        user_name=html.escape(user_name),
        user_id=user_id,
        time_str=time_str,
        message_text=html.escape(text)
    )

    # Отправка напрямую тех-администратору (в обход кадровых чатов)
    super_admin = CONFIG.get("SUPER_ADMIN_ID")
    if super_admin:
        await safe_send(bot, int(super_admin), alert_text, parse_mode="HTML")

    tech_admin = CONFIG.get("TECH_ADMIN_ID")
    if tech_admin and tech_admin != super_admin:
        await safe_send(bot, int(tech_admin), alert_text, parse_mode="HTML")

    await message.answer(texts.SUPPORT_SUCCESS, parse_mode="HTML")