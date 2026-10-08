# -*- coding: utf-8 -*-
"""
Обработчики диалогов с соискателями МУП «Ульяновскэлектротранс»:
- Главное меню, команды /start, /help, /id, /contacts, /privacy, /my, /training, /mydata, /revoke
- Подача анкеты на работу (16 шагов опросника строго по ТЗ, FSM CandidateForm)
- Вопросы специалисту кадров (/ask, FSM InquiryForm)
- База знаний предприятия (FAQ)
- Экстренная связь с техподдержкой (/support, /sos)
- Изолированный прямой диалог (Live-Chat) с кадровой службой
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from aiogram import Bot, F, Router, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

import texts
try:
    from services.candidate_service import candidate_service
except ImportError:
    from services.candidate_service import candidate_service

from common import (
    CONFIG,
    CandidateDirectMsgForm,
    CandidateForm,
    InquiryForm,
    RevokeConsentForm,
    SupportForm,
    db,
    is_hr_admin,
    is_tech_admin,
    route_new_candidate_ticket,
    route_new_inquiry_ticket,
    safe_answer,
    safe_send,
)
from keyboards import (
    make_candidate_main_keyboard,
    make_consent_survey_kb,
    make_faq_keyboard,
    make_inquiry_admin_keyboard,
    make_mydata_kb,
    make_phone_reply_keyboard,
    make_revoke_confirm_kb,
    make_step10_dormitory_kb,
    make_step11_schedule_kb,
    make_step12_health_kb,
    make_step13_criminal_kb,
    make_step14_source_kb,
    make_step16_confirm_kb,
    make_step16_edit_menu_kb,
    make_step2_birthdate_kb,
    make_step4_city_kb,
    make_step5_vacancies_kb,
    make_step6_1_categories_kb,
    make_step6_license_kb,
    make_step7_experience_kb,
    make_step8_education_kb,
    make_step9_relocation_kb,
    make_step_nav_kb,
    make_ticket_keyboard,
)

logger = logging.getLogger("CANDIDATE_HANDLER")
candidate_router = Router(name="candidate")


# ==============================================================================
# СИСТЕМНЫЕ КОМАНДЫ НАВИГАЦИИ (/cancel, /stop, /help, /id, /start)
# ==============================================================================

@candidate_router.message(Command("cancel"))
@candidate_router.message(F.text.lower().in_(["/cancel", "отмена", "отменить"]))
@candidate_router.callback_query(F.data == "cand_cancel_flow")
async def cmd_cancel(event: Union[types.Message, types.CallbackQuery], state: FSMContext) -> None:
    """Отмена любого активного сценария FSM и возврат в главное меню."""
    await state.clear()
    uid = event.from_user.id
    if isinstance(event, types.CallbackQuery):
        try:
            await event.message.edit_text(
                texts.NAV_CANCEL_TEXT,
                reply_markup=make_candidate_main_keyboard(user_id=uid),
                parse_mode="HTML",
            )
        except Exception:
            await event.message.answer(
                texts.NAV_CANCEL_TEXT,
                reply_markup=make_candidate_main_keyboard(user_id=uid),
                parse_mode="HTML",
            )
        await event.answer("Отменено")
    else:
        await safe_answer(
            event,
            texts.NAV_CANCEL_TEXT,
            reply_markup=make_candidate_main_keyboard(user_id=uid),
            parse_mode="HTML",
        )


@candidate_router.message(Command("stop"))
async def cmd_candidate_stop_dialog(message: types.Message, bot: Bot) -> None:
    """Завершение прямого диалога соискателем."""
    user_id = str(message.from_user.id)
    dlg = db.get_dialog_by_user(user_id)
    if not dlg:
        await safe_answer(message, "ℹ️ У вас сейчас нет активного прямого диалога.")
        return

    operator_id = dlg[1]
    name = dlg[3] or message.from_user.full_name
    db.end_direct_dialog(user_id=user_id)

    await safe_answer(
        message,
        texts.LIVE_CHAT_ENDED,
        reply_markup=make_candidate_main_keyboard(user_id=message.from_user.id),
        parse_mode="HTML",
    )
    await safe_send(
        bot,
        operator_id,
        f"⏹ Соискатель <b>{html.escape(name)}</b> (ID: <code>{user_id}</code>) завершил прямой диалог.",
    )


@candidate_router.message(Command("help"))
@candidate_router.message(StateFilter(None), F.text.lower().in_(["help", "помощь", "справка", "команды", "хелп"]))
async def cmd_help(message: types.Message, state: Optional[FSMContext] = None) -> None:
    """Справочник всех команд бота с учетом роли пользователя."""
    if state:
        await state.clear()
    user_id = message.from_user.id
    is_tech = is_tech_admin(user_id)
    is_hr = is_hr_admin(user_id)
    help_text = texts.format_help_text(user_id=user_id, is_hr=is_hr, is_tech=is_tech)
    await safe_answer(message, help_text, reply_markup=make_candidate_main_keyboard(user_id=user_id), parse_mode="HTML")


@candidate_router.message(Command("id"))
async def cmd_id(message: types.Message) -> None:
    """Вывод информации о текущем Telegram ID пользователя и чата."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    username = message.from_user.username
    info_text = texts.format_id_text(user_id, chat_id, username)
    is_admin_user = (
        user_id == CONFIG.get("SUPER_ADMIN_ID") or is_hr_admin(user_id) or is_tech_admin(user_id)
    )
    if is_admin_user and message.chat.type != "private":
        info_text += (
            "\n━━━━━━━━━━━━━━━━━━━━━\n"
            "ℹ️ <i>Чтобы привязать эту группу для получения анкет, отправьте:</i>\n"
            "<code>/set_group</code>"
        )
    await safe_answer(message, info_text, parse_mode="HTML")


@candidate_router.message(Command("start", "menu"))
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    """Точка входа и перезапуск интерактивного меню."""
    await state.clear()
    user_id = message.from_user.id
    is_admin = (
        user_id == CONFIG.get("SUPER_ADMIN_ID") or is_hr_admin(user_id) or is_tech_admin(user_id)
    )

    if db.is_blocked(user_id, super_admin_id=CONFIG.get("SUPER_ADMIN_ID")):
        await safe_answer(
            message,
            "⛔ <b>Доступ к боту ограничен</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Ваш аккаунт находится в <b>чёрном списке</b> информационной системы МУП «Ульяновскэлектротранс».\n"
            f"📞 <i>Контакты отдела кадров предприятия:</i> <code>{CONFIG['HR_PHONE']}</code>",
            parse_mode="HTML",
        )
        return

    if CONFIG.get("MAINTENANCE_MODE") and not is_admin:
        await safe_answer(message, texts.MAINTENANCE_ACTIVE, parse_mode="HTML")
        return

    await safe_answer(
        message,
        texts.START_WELCOME,
        reply_markup=make_candidate_main_keyboard(user_id=user_id),
        parse_mode="HTML",
    )


# ==============================================================================
# ЭКСТРЕННАЯ СВЯЗЬ С ТЕХПОДДЕРЖКОЙ (/support, /sos)
# ==============================================================================

@candidate_router.callback_query(F.data == "cand_support")
@candidate_router.message(Command("support", "sos", "tech_support"))
@candidate_router.message(StateFilter(None), F.text.lower().in_(["поддержка", "техподдержка", "сос", "sos"]))
async def cb_cand_support_start(event: Union[types.CallbackQuery, types.Message], state: FSMContext) -> None:
    """Запрос экстренной связи с техническим инженером."""
    await state.clear()
    await state.set_state(SupportForm.waiting_message)
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="cand_back_to_menu")
    prompt_text = (
        "🚨 <b>ЭКСТРЕННАЯ СВЯЗЬ С ТЕХНИЧЕСКИМ АДМИНИСТРАТОРОМ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Если вы столкнулись с техническим сбоем, критической ошибкой в работе бота "
        "или вам требуется срочная помощь инженера, отправьте описание проблемы <b>одним сообщением</b> ниже.\n\n"
        "<i>Ваше обращение поступит напрямую дежурному техническому администратору.</i>"
    )
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(prompt_text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, prompt_text, reply_markup=builder.as_markup(), parse_mode="HTML")


@candidate_router.message(SupportForm.waiting_message)
async def process_support_message(message: types.Message, state: FSMContext, bot: Bot) -> None:
    msg_text = (message.text or "").strip()
    if not msg_text:
        await safe_answer(message, "⚠️ Пожалуйста, опишите проблему текстом в одном сообщении.")
        return

    await state.clear()
    user_id = message.from_user.id
    user_name = message.from_user.full_name or "Пользователь"
    user_uname = message.from_user.username or ""
    time_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    card_text = texts.format_support_card(
        user_id=user_id,
        user_name=user_name,
        username=user_uname,
        time_str=time_str,
        message_text=msg_text,
    )

    target_techs: set[int] = set()
    super_admin = CONFIG.get("SUPER_ADMIN_ID")
    if super_admin and super_admin != 0:
        target_techs.add(super_admin)
    for adm_id, role in db.get_all_admins():
        if role == "tech":
            target_techs.add(adm_id)

    builder = InlineKeyboardBuilder()
    if user_uname:
        builder.button(text="💬 Открыть чат в TG", url=f"https://t.me/{user_uname}")
    else:
        builder.button(text="👤 Профиль пользователя", url=f"tg://user?id={user_id}")
    builder.adjust(1)

    for tech_id in target_techs:
        try:
            await safe_send(bot, tech_id, card_text, reply_markup=builder.as_markup())
        except Exception as e:
            logger.error("Не удалось отправить SOS админу %s: %s", tech_id, e)

    await safe_answer(
        message,
        "✅ <b>Экстренное сообщение передано техническому администратору!</b>\n\n"
        "Дежурный инженер уведомлен и разбирается с ситуацией. При необходимости специалист свяжется с вами в Telegram.",
        reply_markup=make_candidate_main_keyboard(user_id=user_id),
        parse_mode="HTML",
    )


# ==============================================================================
# РАЗДЕЛ «МОЯ АНКЕТА» (/my)
# ==============================================================================

@candidate_router.callback_query(F.data == "cand_my_application")
@candidate_router.message(Command("my"))
async def cb_cand_my_application(event: Union[types.CallbackQuery, types.Message]) -> None:
    user_id = event.from_user.id
    if db.is_blocked(user_id, super_admin_id=CONFIG.get("SUPER_ADMIN_ID")):
        if isinstance(event, types.CallbackQuery):
            await event.answer("⛔ Доступ ограничен (вы в черном списке).", show_alert=True)
            return
        await safe_answer(event, "⛔ Доступ ограничен.", parse_mode="HTML")
        return

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


# ==============================================================================
# КОМАНДЫ ПРАВ СУБЪЕКТА 152-ФЗ (/mydata, /revoke)
# ==============================================================================

@candidate_router.message(Command("mydata"))
async def cmd_mydata(message: types.Message) -> None:
    user_id = message.from_user.id
    cand_dict = db.get_candidate_dict_by_user(str(user_id), platform="tg")
    if not cand_dict:
        builder = InlineKeyboardBuilder()
        builder.button(text=texts.BTN_APPLY, callback_data="cand_start_apply")
        builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
        builder.adjust(1)
        await safe_answer(
            message,
            "📑 <b>Персональные данные не найдены.</b>\nВы еще не подавали анкету в информационную систему предприятия.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        return

    text = texts.format_mydata(cand_dict)
    await safe_answer(message, text, reply_markup=make_mydata_kb(), parse_mode="HTML")


@candidate_router.message(Command("revoke"))
@candidate_router.callback_query(F.data == "cand_revoke_ask")
async def cmd_revoke_ask(event: Union[types.Message, types.CallbackQuery], state: FSMContext) -> None:
    await state.clear()
    await state.set_state(RevokeConsentForm.waiting_confirm)
    text = texts.REVOKE_CONFIRM_PROMPT
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, reply_markup=make_revoke_confirm_kb(), parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, text, reply_markup=make_revoke_confirm_kb(), parse_mode="HTML")


@candidate_router.callback_query(RevokeConsentForm.waiting_confirm, F.data == "cand_revoke_confirm")
async def cb_revoke_confirm(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user_id = callback.from_user.id
    success, ticket_id, destroyed_ts = candidate_service.revoke_consent_152fz(user_id, platform="tg")

    if not success:
        await callback.message.edit_text(
            "ℹ️ <b>Активных анкет для отзыва согласия не найдено.</b>",
            reply_markup=make_candidate_main_keyboard(user_id=user_id),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    text = texts.format_revoke_success(ticket_id or 0, destroyed_ts or "")
    await callback.message.edit_text(
        text,
        reply_markup=make_candidate_main_keyboard(user_id=user_id),
        parse_mode="HTML",
    )
    await callback.answer("Согласие отозвано")


# ==============================================================================
# РАЗДЕЛЫ «ОБУЧЕНИЕ», FAQ И КОНТАКТЫ (/training, /faq, /contacts, /privacy)
# ==============================================================================

@candidate_router.message(Command("training"))
async def cmd_training(message: types.Message) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_APPLY, callback_data="cand_start_apply")
    builder.button(text="💬 Задать вопрос", callback_data="cand_ask_question")
    builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
    builder.adjust(1)
    await safe_answer(message, texts.TRAINING_INFO_TEXT, reply_markup=builder.as_markup(), parse_mode="HTML")


@candidate_router.callback_query(F.data == "cand_hr_contacts")
@candidate_router.message(Command("contacts"))
async def cb_cand_hr_contacts(event: Union[types.CallbackQuery, types.Message]) -> None:
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
async def cb_cand_privacy_policy(event: Union[types.CallbackQuery, types.Message]) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
    builder.adjust(1)
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(texts.PRIVACY_POLICY_TEXT, reply_markup=builder.as_markup(), parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, texts.PRIVACY_POLICY_TEXT, reply_markup=builder.as_markup(), parse_mode="HTML")


@candidate_router.callback_query(F.data == "cand_back_to_menu")
async def cb_cand_back_to_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user_id = callback.from_user.id
    await callback.message.edit_text(
        texts.MENU_RETURN,
        reply_markup=make_candidate_main_keyboard(user_id=user_id),
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.callback_query(F.data == "cand_faq_menu")
@candidate_router.message(Command("faq"))
async def cb_cand_faq_menu(event: Union[types.CallbackQuery, types.Message]) -> None:
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(texts.FAQ_MENU_TITLE, reply_markup=make_faq_keyboard(), parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, texts.FAQ_MENU_TITLE, reply_markup=make_faq_keyboard(), parse_mode="HTML")


@candidate_router.callback_query(F.data.startswith("faq_item_"))
async def cb_faq_item(callback: types.CallbackQuery) -> None:
    item_key = callback.data.replace("faq_item_", "")
    text = texts.FAQ_DATA.get(item_key, "Информация обновляется...")

    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад к вопросам (FAQ)", callback_data="cand_faq_menu")
    builder.button(text=texts.BTN_APPLY, callback_data="cand_start_apply")
    builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
    builder.adjust(1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


# ==============================================================================
# ВОПРОСЫ В ОТДЕЛ КАДРОВ (/ask, InquiryForm)
# ==============================================================================

@candidate_router.callback_query(F.data == "cand_ask_question")
@candidate_router.message(Command("ask"))
async def cb_cand_ask_question(event: Union[types.CallbackQuery, types.Message], state: FSMContext) -> None:
    await state.clear()
    user_id = event.from_user.id

    if db.is_blocked(user_id, super_admin_id=CONFIG.get("SUPER_ADMIN_ID")):
        if isinstance(event, types.CallbackQuery):
            await event.answer("⛔ Доступ ограничен.", show_alert=True)
            return
        await safe_answer(event, "⛔ Отправка вопросов недоступна.", parse_mode="HTML")
        return

    is_admin = (
        user_id == CONFIG.get("SUPER_ADMIN_ID") or is_hr_admin(user_id) or is_tech_admin(user_id)
    )
    env_mode = (CONFIG.get("ENVIRONMENT") or "TEST").upper()
    if env_mode in ("PROD", "PRODUCTION") and not is_admin:
        allowed, seconds_left = db.check_inquiry_cooldown(
            str(user_id), cooldown_seconds=CONFIG.get("COOLDOWN_SECONDS", 1200)
        )
        if not allowed:
            mins = (seconds_left // 60) + 1
            msg_text = texts.format_inquiry_cooldown(mins)
            if isinstance(event, types.CallbackQuery):
                await event.message.edit_text(
                    msg_text,
                    reply_markup=make_candidate_main_keyboard(user_id=user_id),
                    parse_mode="HTML",
                )
                return
            await safe_answer(event, msg_text)
            return

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
async def cb_cand_consent_ask(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(InquiryForm.waiting_question)
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CANCEL, callback_data="cand_cancel_flow")
    await callback.message.edit_text(texts.INQUIRY_INPUT_PROMPT, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer("Согласие принято")


@candidate_router.message(InquiryForm.waiting_question)
async def process_inquiry_message(message: types.Message, state: FSMContext, bot: Bot) -> None:
    q_text = (message.text or "").strip()
    user_id = str(message.from_user.id)

    cand_info = db.get_candidate_by_user_id(user_id, platform="tg")
    full_name = cand_info[3] if cand_info else (message.from_user.full_name or "Не указано")
    phone = cand_info[4] if cand_info else "Не указан"
    vacancy = cand_info[5] if cand_info else "Анкета не подана"

    consent_ts = datetime.now().strftime("%d.%m.%Y %H:%M")
    is_admin = (
        int(user_id) == CONFIG.get("SUPER_ADMIN_ID")
        or is_hr_admin(int(user_id))
        or is_tech_admin(int(user_id))
    )
    is_test_inq = is_admin and (db.get_setting("active_db_target", "resumes.db") == "resumes_test.db" or "тест" in q_text.lower())
    cooldown = CONFIG.get("COOLDOWN_SECONDS", 1200) if not is_admin else 0

    success, msg, inq_id = candidate_service.submit_inquiry(
        user_id=user_id,
        question_text=q_text,
        platform="tg",
        full_name=full_name,
        phone=phone,
        vacancy=vacancy,
        is_test=is_test_inq,
        consent_timestamp=consent_ts,
        cooldown_seconds=cooldown,
    )

    if not success:
        await safe_answer(message, msg)
        return

    await state.clear()
    conf_text = texts.format_inquiry_sent(inq_id or 0)
    await safe_answer(message, conf_text, reply_markup=make_candidate_main_keyboard(user_id=message.from_user.id), parse_mode="HTML")

    # Маршрутизация в кадровый чат через SSOT шаблон
    hr_card = texts.format_inquiry_hr_card(
        inquiry_id=inq_id or 0,
        platform="tg",
        full_name=full_name,
        phone=phone,
        vacancy=vacancy,
        question_text=q_text,
        consent_timestamp=consent_ts,
        is_test=is_test_inq,
    )
    inq_kb = make_inquiry_admin_keyboard(inq_id or 0)
    await route_new_inquiry_ticket(bot, hr_card, inq_kb)


# ==============================================================================
# ОПРОСНИК СОИСКАТЕЛЯ (16 ШАГОВ АНКЕТЫ)
# ==============================================================================

@candidate_router.callback_query(F.data == "cand_start_apply")
@candidate_router.message(Command("apply"))
async def cmd_apply(event: Union[types.Message, types.CallbackQuery], state: FSMContext) -> None:
    user_id = event.from_user.id
    await run_candidate_survey(event, state, user_id)


async def run_candidate_survey(
    event: Union[types.Message, types.CallbackQuery],
    state: FSMContext,
    user_id: int,
    force: bool = False,
) -> None:
    async def send_or_edit(txt: str, reply_markup: Optional[Any] = None) -> None:
        if isinstance(event, types.CallbackQuery):
            try:
                await event.message.edit_text(txt, reply_markup=reply_markup, parse_mode="HTML")
            except Exception:
                await event.message.answer(txt, reply_markup=reply_markup, parse_mode="HTML")
            await event.answer()
        else:
            await safe_answer(event, txt, reply_markup=reply_markup, parse_mode="HTML")

    is_admin = (
        user_id == CONFIG.get("SUPER_ADMIN_ID") or is_hr_admin(user_id) or is_tech_admin(user_id)
    )

    if CONFIG.get("MAINTENANCE_MODE") and not is_admin:
        await send_or_edit(texts.MAINTENANCE_ACTIVE)
        return

    # Сервисная проверка права подачи анкеты (ЧС, дубликат, кулдаун)
    can_apply, reason, info = candidate_service.check_can_apply(user_id, platform="tg")
    if not can_apply and not (force or (is_admin and CONFIG.get("ENVIRONMENT") == "TEST")):
        if reason == "banned":
            await send_or_edit(
                "⛔ <b>Подача анкеты недоступна</b>\n\n"
                "Ваш аккаунт находится в чёрном списке информационной системы предприятия.\n"
                f"Контакты отдела кадров: <code>{CONFIG['HR_PHONE']}</code>"
            )
            return
        elif reason == "unprocessed" and info:
            ticket_id = info.get("ticket_id", 0)
            status = info.get("status", "Новая")
            created = info.get("created_at", "")
            vac = info.get("vacancy", "")
            builder = InlineKeyboardBuilder()
            builder.button(text="💬 Задать вопрос / Связаться", callback_data="cand_ask_question")
            builder.button(text="📚 Частые вопросы (FAQ)", callback_data="cand_faq_menu")
            builder.button(text="🏢 Контакты отдела кадров", callback_data="cand_hr_contacts")
            builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
            builder.adjust(1)
            await send_or_edit(texts.format_already_applied(ticket_id, vac, created, status), reply_markup=builder.as_markup())
            return
        elif reason == "rejected_cooldown" and info:
            ticket_id = info.get("ticket_id", 0)
            days_left = info.get("days_left", 0)
            ref_date = info.get("refuse_date", "")
            builder = InlineKeyboardBuilder()
            builder.button(text="💬 Связаться с отделом кадров", callback_data="cand_ask_question")
            builder.button(text="📚 Частые вопросы (FAQ)", callback_data="cand_faq_menu")
            builder.button(text="🏢 Контакты предприятия", callback_data="cand_hr_contacts")
            builder.button(text=texts.BTN_BACK_TO_MENU, callback_data="cand_back_to_menu")
            builder.adjust(1)
            await send_or_edit(texts.format_rejection_cooldown(ticket_id, ref_date, days_left), reply_markup=builder.as_markup())
            return

    await state.clear()
    await state.set_state(CandidateForm.waiting_consent)
    await send_or_edit(texts.CONSENT_SURVEY_PROMPT, reply_markup=make_consent_survey_kb())


# --- ШАГ 0: СОГЛАСИЕ 152-ФЗ ---
@candidate_router.callback_query(CandidateForm.waiting_consent, F.data == "cand_consent_apply")
async def cb_consent_agree(callback: types.CallbackQuery, state: FSMContext) -> None:
    consent_ts = datetime.now().strftime("%d.%m.%Y %H:%M")
    await state.update_data(consent_timestamp=consent_ts)
    await state.set_state(CandidateForm.full_name)
    await callback.message.edit_text(
        texts.SURVEY_STEP1_NAME,
        reply_markup=make_step_nav_kb(can_skip=False),
        parse_mode="HTML",
    )
    await callback.answer("Согласие принято")


@candidate_router.callback_query(CandidateForm.waiting_consent, F.data == "cand_consent_refuse")
async def cb_consent_refuse(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Задать вопрос", callback_data="cand_ask_question")
    builder.button(text="🏠 Главное меню", callback_data="cand_back_to_menu")
    builder.adjust(1)
    await callback.message.edit_text(texts.CONSENT_REFUSED_TEXT, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


# --- ШАГ 1: ФИО ---
@candidate_router.message(CandidateForm.full_name)
async def process_name(message: types.Message, state: FSMContext) -> None:
    raw_name = (message.text or "").strip()
    is_valid, clean_name, err = candidate_service.validate_fio(raw_name)
    if not is_valid:
        await safe_answer(message, err or texts.ERR_INVALID_NAME, reply_markup=make_step_nav_kb(can_skip=False), parse_mode="HTML")
        return

    await state.update_data(full_name=clean_name)
    await state.set_state(CandidateForm.birth_date)
    await safe_answer(
        message,
        texts.SURVEY_STEP2_BIRTHDATE,
        reply_markup=make_step2_birthdate_kb(),
        parse_mode="HTML",
    )


# --- ШАГ 2: ДАТА РОЖДЕНИЯ ---
@candidate_router.callback_query(CandidateForm.birth_date, F.data.startswith("bd_"))
async def cb_birth_date(callback: types.CallbackQuery, state: FSMContext) -> None:
    choice = callback.data.replace("bd_", "")
    if choice == "manual":
        await callback.message.edit_text(
            texts.SURVEY_STEP2_MANUAL_PROMPT,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    await state.update_data(birth_date=choice)
    data = await state.get_data()
    name = data.get("full_name", "")
    await state.set_state(CandidateForm.phone)
    await callback.message.edit_text(
        f"✅ Дата рождения принята: <b>{choice}</b>\n\n" + texts.format_survey_ask_phone(name),
        parse_mode="HTML",
    )
    await callback.message.answer(
        "Нажмите кнопку внизу или введите номер:",
        reply_markup=make_phone_reply_keyboard(),
    )
    await callback.answer()


@candidate_router.message(CandidateForm.birth_date)
async def process_birth_date_text(message: types.Message, state: FSMContext) -> None:
    raw_date = (message.text or "").strip()
    is_valid, clean_date, err = candidate_service.validate_birth_date(raw_date)
    if not is_valid:
        await safe_answer(
            message,
            err or texts.ERR_INVALID_DATE_FORMAT,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML",
        )
        return

    await state.update_data(birth_date=clean_date)
    data = await state.get_data()
    name = data.get("full_name", "")
    await state.set_state(CandidateForm.phone)
    await safe_answer(
        message,
        texts.format_survey_ask_phone(name),
        reply_markup=make_phone_reply_keyboard(),
        parse_mode="HTML",
    )


# --- ШАГ 3: ТЕЛЕФОН ---
@candidate_router.message(CandidateForm.phone, F.contact)
async def process_phone_contact(message: types.Message, state: FSMContext) -> None:
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
        parse_mode="HTML",
    )


@candidate_router.message(CandidateForm.phone)
async def process_phone_text(message: types.Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    is_valid, clean_phone, err = candidate_service.validate_phone(raw)
    if not is_valid:
        await safe_answer(
            message,
            err or texts.ERR_INVALID_PHONE,
            reply_markup=make_phone_reply_keyboard(),
            parse_mode="HTML",
        )
        return

    await state.update_data(phone=clean_phone)
    await message.answer("✅ Номер телефона принят.", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(CandidateForm.city)
    await safe_answer(
        message,
        texts.SURVEY_STEP4_CITY,
        reply_markup=make_step4_city_kb(),
        parse_mode="HTML",
    )


# --- ШАГ 4: ГОРОД ПРОЖИВАНИЯ ---
@candidate_router.callback_query(CandidateForm.city, F.data.startswith("city_"))
async def cb_city_select(callback: types.CallbackQuery, state: FSMContext) -> None:
    c = callback.data.replace("city_", "")
    if c == "manual":
        await state.set_state(CandidateForm.city_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP4_MANUAL_PROMPT,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    await state.update_data(city=c)
    await state.set_state(CandidateForm.vacancy)
    await callback.message.edit_text(
        f"🏙 Город: <b>{html.escape(c)}</b>\n\n" + texts.SURVEY_STEP5_VACANCY,
        reply_markup=make_step5_vacancies_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.message(CandidateForm.city)
@candidate_router.message(CandidateForm.city_manual)
async def process_city_manual(message: types.Message, state: FSMContext) -> None:
    c = (message.text or "").strip()
    await state.update_data(city=c)
    await state.set_state(CandidateForm.vacancy)
    await safe_answer(
        message,
        texts.SURVEY_STEP5_VACANCY,
        reply_markup=make_step5_vacancies_kb(),
        parse_mode="HTML",
    )


# --- ШАГ 5: ВАКАНСИЯ ---
VACANCY_MAP: Dict[str, str] = {
    "tram": "Водитель трамвая",
    "troll": "Водитель троллейбуса",
    "conductor": "Кондуктор",
    "slesar": "Слесарь по ремонту подвижного состава",
    "electro": "Электромонтёр контактной сети",
    "other": "other",
}

@candidate_router.callback_query(CandidateForm.vacancy, F.data.startswith("vac_"))
async def cb_vacancy_select(callback: types.CallbackQuery, state: FSMContext) -> None:
    vac_raw = callback.data.replace("vac_", "")
    vac_name = VACANCY_MAP.get(vac_raw, vac_raw)
    if vac_raw == "other" or vac_name == "other":
        await state.set_state(CandidateForm.custom_vacancy)
        await callback.message.edit_text(
            texts.SURVEY_STEP5_1_CUSTOM,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    await state.update_data(vacancy=vac_name)
    await state.set_state(CandidateForm.has_license)
    await callback.message.edit_text(
        f"🎯 Вакансия: <b>{html.escape(vac_name)}</b>\n\n{texts.SURVEY_STEP6_LICENSE}",
        reply_markup=make_step6_license_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.message(CandidateForm.custom_vacancy)
async def process_custom_vacancy_text(message: types.Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    if not val:
        await safe_answer(message, "⚠️ Пожалуйста, впишите желаемую должность текстом.")
        return
    await state.update_data(vacancy=val)
    await state.set_state(CandidateForm.has_license)
    await safe_answer(
        message,
        f"🎯 Вакансия: <b>{html.escape(val)}</b>\n\n{texts.SURVEY_STEP6_LICENSE}",
        reply_markup=make_step6_license_kb(),
        parse_mode="HTML",
    )


# --- ШАГ 6: ВОДИТЕЛЬСКОЕ УДОСТОВЕРЕНИЕ ---
@candidate_router.callback_query(CandidateForm.has_license, F.data.in_(["lic_yes", "lic_no"]))
async def cb_license_choice(callback: types.CallbackQuery, state: FSMContext) -> None:
    if callback.data == "lic_no":
        await state.update_data(driver_license="Нет")
        await state.set_state(CandidateForm.has_experience)
        await callback.message.edit_text(
            texts.SURVEY_STEP7_EXPERIENCE,
            reply_markup=make_step7_experience_kb(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    await state.update_data(selected_categories=[])
    await state.set_state(CandidateForm.license_categories)
    await callback.message.edit_text(
        texts.SURVEY_STEP6_1_CATEGORIES.format(selected="Не выбрано"),
        reply_markup=make_step6_1_categories_kb([]),
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.callback_query(CandidateForm.license_categories, F.data.startswith("cat_toggle_"))
async def cb_license_cat_toggle(callback: types.CallbackQuery, state: FSMContext) -> None:
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
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.callback_query(CandidateForm.license_categories, F.data == "cat_manual")
async def cb_license_cat_manual(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CandidateForm.license_categories_manual)
    await callback.message.edit_text(
        texts.SURVEY_STEP6_1_MANUAL,
        reply_markup=make_step_nav_kb(can_skip=False),
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.message(CandidateForm.license_categories_manual)
async def process_license_cat_manual(message: types.Message, state: FSMContext) -> None:
    cats = (message.text or "").strip()
    await state.update_data(driver_license=cats)
    await state.set_state(CandidateForm.has_experience)
    await safe_answer(
        message,
        texts.SURVEY_STEP7_EXPERIENCE,
        reply_markup=make_step7_experience_kb(),
        parse_mode="HTML",
    )


@candidate_router.callback_query(CandidateForm.license_categories, F.data == "cat_done")
async def cb_license_cat_done(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cats = data.get("selected_categories", [])
    cat_str = ", ".join(cats) if cats else "Да (категории не указаны)"
    await state.update_data(driver_license=cat_str)
    await state.set_state(CandidateForm.has_experience)
    await callback.message.edit_text(
        f"🚗 Водительские права: <b>{html.escape(cat_str)}</b>\n\n" + texts.SURVEY_STEP7_EXPERIENCE,
        reply_markup=make_step7_experience_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


# --- ШАГ 7: ОПЫТ РАБОТЫ ---
@candidate_router.callback_query(CandidateForm.has_experience, F.data.in_(["exp_yes", "exp_no"]))
async def cb_experience_choice(callback: types.CallbackQuery, state: FSMContext) -> None:
    if callback.data == "exp_no":
        await state.update_data(experience="Без опыта")
        await state.set_state(CandidateForm.education_level)
        await callback.message.edit_text(
            texts.SURVEY_STEP7_2_NO_EXP + "\n\n" + texts.SURVEY_STEP8_EDUCATION,
            reply_markup=make_step8_education_kb(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    await state.set_state(CandidateForm.experience)
    await callback.message.edit_text(
        texts.SURVEY_STEP7_1_DESC,
        reply_markup=make_step_nav_kb(can_skip=False),
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.message(CandidateForm.experience)
async def process_experience_text(message: types.Message, state: FSMContext) -> None:
    exp = (message.text or "").strip()
    await state.update_data(experience=exp)
    await state.set_state(CandidateForm.education_level)
    await safe_answer(
        message,
        texts.SURVEY_STEP8_EDUCATION,
        reply_markup=make_step8_education_kb(),
        parse_mode="HTML",
    )


# --- ШАГ 8: ОБРАЗОВАНИЕ ---
@candidate_router.callback_query(CandidateForm.education_level, F.data.startswith("edu_"))
async def cb_education_choice(callback: types.CallbackQuery, state: FSMContext) -> None:
    edu = callback.data.replace("edu_", "")
    if edu == "manual":
        await state.set_state(CandidateForm.education_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP8_MANUAL,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    await state.update_data(edu_level=edu)
    await state.set_state(CandidateForm.education_facility)
    await callback.message.edit_text(
        f"🎓 Уровень образования: <b>{html.escape(edu)}</b>\n\n" + texts.SURVEY_STEP8_1_FACILITY,
        reply_markup=make_step_nav_kb(can_skip=True),
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.message(CandidateForm.education_manual)
async def process_education_manual(message: types.Message, state: FSMContext) -> None:
    edu = (message.text or "").strip()
    await state.update_data(edu_level=edu)
    await state.set_state(CandidateForm.education_facility)
    await safe_answer(
        message,
        texts.SURVEY_STEP8_1_FACILITY,
        reply_markup=make_step_nav_kb(can_skip=True),
        parse_mode="HTML",
    )


@candidate_router.message(CandidateForm.education_facility)
async def process_education_facility(message: types.Message, state: FSMContext) -> None:
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
        parse_mode="HTML",
    )


# --- ШАГ 9: ГОТОВНОСТЬ К ПЕРЕЕЗДУ ---
@candidate_router.callback_query(CandidateForm.relocation, F.data.in_(["reloc_yes", "reloc_no", "reloc_manual"]))
async def cb_relocation(callback: types.CallbackQuery, state: FSMContext) -> None:
    if callback.data == "reloc_manual":
        await state.set_state(CandidateForm.relocation_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP9_MANUAL,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    val = "Да" if callback.data == "reloc_yes" else "Нет"
    await state.update_data(relocation=val)
    await state.set_state(CandidateForm.dormitory)
    await callback.message.edit_text(
        f"🏠 Переезд в Ульяновск: <b>{val}</b>\n\n" + texts.SURVEY_STEP10_DORMITORY,
        reply_markup=make_step10_dormitory_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.message(CandidateForm.relocation_manual)
async def process_relocation_manual(message: types.Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    await state.update_data(relocation=val)
    await state.set_state(CandidateForm.dormitory)
    await safe_answer(
        message,
        texts.SURVEY_STEP10_DORMITORY,
        reply_markup=make_step10_dormitory_kb(),
        parse_mode="HTML",
    )


# --- ШАГ 10: ОБЩЕЖИТИЕ ---
@candidate_router.callback_query(CandidateForm.dormitory, F.data.in_(["dorm_yes", "dorm_no", "dorm_manual"]))
async def cb_dormitory(callback: types.CallbackQuery, state: FSMContext) -> None:
    if callback.data == "dorm_manual":
        await state.set_state(CandidateForm.dormitory_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP10_MANUAL,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    val = "Да" if callback.data == "dorm_yes" else "Нет"
    await state.update_data(dormitory=val)
    await state.set_state(CandidateForm.schedule)
    await callback.message.edit_text(
        f"🛏 Потребность в общежитии: <b>{val}</b>\n\n" + texts.SURVEY_STEP11_SCHEDULE,
        reply_markup=make_step11_schedule_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.message(CandidateForm.dormitory_manual)
async def process_dormitory_manual(message: types.Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    await state.update_data(dormitory=val)
    await state.set_state(CandidateForm.schedule)
    await safe_answer(
        message,
        texts.SURVEY_STEP11_SCHEDULE,
        reply_markup=make_step11_schedule_kb(),
        parse_mode="HTML",
    )


# --- ШАГ 11: СМЕННЫЙ ГРАФИК ---
@candidate_router.callback_query(CandidateForm.schedule, F.data.in_(["sched_yes", "sched_no", "sched_manual"]))
async def cb_schedule(callback: types.CallbackQuery, state: FSMContext) -> None:
    if callback.data == "sched_manual":
        await state.set_state(CandidateForm.schedule_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP11_MANUAL,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    val = "Да" if callback.data == "sched_yes" else "Нет"
    await state.update_data(shift_work=val)
    await state.set_state(CandidateForm.health)
    await callback.message.edit_text(
        f"🕐 Сменный график: <b>{val}</b>\n\n" + texts.SURVEY_STEP12_HEALTH,
        reply_markup=make_step12_health_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.message(CandidateForm.schedule_manual)
async def process_schedule_manual(message: types.Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    await state.update_data(shift_work=val)
    await state.set_state(CandidateForm.health)
    await safe_answer(
        message,
        texts.SURVEY_STEP12_HEALTH,
        reply_markup=make_step12_health_kb(),
        parse_mode="HTML",
    )


# --- ШАГ 12: МЕДИЦИНСКИЕ ПРОТИВОПОКАЗАНИЯ ---
@candidate_router.callback_query(CandidateForm.health, F.data.in_(["health_yes", "health_no", "health_manual"]))
async def cb_health(callback: types.CallbackQuery, state: FSMContext) -> None:
    if callback.data == "health_manual":
        await state.set_state(CandidateForm.health_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP12_MANUAL,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    val = "Нет" if callback.data == "health_no" else "Да"
    await state.update_data(medical_restrictions=val)
    await state.set_state(CandidateForm.criminal)
    await callback.message.edit_text(
        f"⚕️ Противопоказания: <b>{val}</b>\n\n" + texts.SURVEY_STEP13_CRIMINAL,
        reply_markup=make_step13_criminal_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.message(CandidateForm.health_manual)
async def process_health_manual(message: types.Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    await state.update_data(medical_restrictions=val)
    await state.set_state(CandidateForm.criminal)
    await safe_answer(
        message,
        texts.SURVEY_STEP13_CRIMINAL,
        reply_markup=make_step13_criminal_kb(),
        parse_mode="HTML",
    )


# --- ШАГ 13: СУДИМОСТИ (СТ. 86 УК РФ) ---
@candidate_router.callback_query(CandidateForm.criminal, F.data.in_(["crim_yes", "crim_no", "crim_manual"]))
async def cb_criminal(callback: types.CallbackQuery, state: FSMContext) -> None:
    if callback.data == "crim_manual":
        await state.set_state(CandidateForm.criminal_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP13_MANUAL,
            reply_markup=make_step_nav_kb(can_skip=False),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    val = "Нет" if callback.data == "crim_no" else "Да"
    await state.update_data(criminal_record=val)
    await state.set_state(CandidateForm.source)
    await callback.message.edit_text(
        f"⚖️ Судимость (ст. 86 УК): <b>{val}</b>\n\n" + texts.SURVEY_STEP14_SOURCE,
        reply_markup=make_step14_source_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.message(CandidateForm.criminal_manual)
async def process_criminal_manual(message: types.Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    await state.update_data(criminal_record=val)
    await state.set_state(CandidateForm.source)
    await safe_answer(
        message,
        texts.SURVEY_STEP14_SOURCE,
        reply_markup=make_step14_source_kb(),
        parse_mode="HTML",
    )


# --- ШАГ 14: ИСТОЧНИК ИНФОРМАЦИИ ---
@candidate_router.callback_query(CandidateForm.source, F.data.startswith("src_"))
async def cb_source(callback: types.CallbackQuery, state: FSMContext) -> None:
    s = callback.data.replace("src_", "")
    if s == "manual":
        await state.set_state(CandidateForm.source_manual)
        await callback.message.edit_text(
            texts.SURVEY_STEP14_MANUAL,
            reply_markup=make_step_nav_kb(can_skip=True),
            parse_mode="HTML",
        )
        await callback.answer()
        return
    elif s == "skip":
        s = "Не указан"

    await state.update_data(source=s)
    await state.set_state(CandidateForm.extra_info)
    await callback.message.edit_text(
        f"📢 Источник: <b>{html.escape(s)}</b>\n\n" + texts.SURVEY_STEP15_EXTRA,
        reply_markup=make_step_nav_kb(can_skip=True),
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.message(CandidateForm.source_manual)
async def process_source_manual(message: types.Message, state: FSMContext) -> None:
    s = (message.text or "").strip()
    await state.update_data(source=s)
    await state.set_state(CandidateForm.extra_info)
    await safe_answer(
        message,
        texts.SURVEY_STEP15_EXTRA,
        reply_markup=make_step_nav_kb(can_skip=True),
        parse_mode="HTML",
    )


# --- ШАГ 15: ДОПОЛНИТЕЛЬНЫЕ СВЕДЕНИЯ ---
@candidate_router.message(CandidateForm.extra_info)
async def process_extra_info(message: types.Message, state: FSMContext) -> None:
    extra = (message.text or "").strip()
    await state.update_data(extra_info=extra)
    data = await state.get_data()

    await state.set_state(CandidateForm.confirm_review)
    review_text = texts.format_survey_step16_review(data)
    await safe_answer(
        message,
        review_text,
        reply_markup=make_step16_confirm_kb(),
        parse_mode="HTML",
    )


# --- ШАГ 16: ПОДТВЕРЖДЕНИЕ И ТОЧЕЧНОЕ РЕДАКТИРОВАНИЕ ДАННЫХ ---
@candidate_router.callback_query(CandidateForm.confirm_review, F.data == "cand_edit_fields_menu")
async def cb_edit_fields_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CandidateForm.edit_field_select)
    await callback.message.edit_text(
        texts.SURVEY_STEP16_EDIT_MENU,
        reply_markup=make_step16_edit_menu_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.callback_query(CandidateForm.edit_field_select, F.data == "edit_back_review")
async def cb_edit_back_review(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(CandidateForm.confirm_review)
    await callback.message.edit_text(
        texts.format_survey_step16_review(data),
        reply_markup=make_step16_confirm_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@candidate_router.callback_query(CandidateForm.edit_field_select, F.data.startswith("edit_"))
async def cb_edit_field_pick(callback: types.CallbackQuery, state: FSMContext) -> None:
    field_code = callback.data.replace("edit_", "")
    await state.update_data(edit_target_field=field_code)
    await state.set_state(CandidateForm.edit_field_input)

    prompt = texts.EDIT_FIELD_PROMPTS.get(field_code, "Введите новое значение:")
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад", callback_data="edit_back_review")
    await callback.message.edit_text(f"✏️ <b>{prompt}</b>", reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@candidate_router.message(CandidateForm.edit_field_input)
async def process_edit_field_input(message: types.Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    data = await state.get_data()
    field_code = data.get("edit_target_field", "")

    # Сервисная валидация ключевых полей при их редактировании
    if field_code == "fio":
        ok, norm_fio, err = candidate_service.validate_fio(val)
        if not ok:
            await safe_answer(message, err or texts.ERR_INVALID_NAME, parse_mode="HTML")
            return
        val = norm_fio
    elif field_code == "birth":
        ok, norm_birth, err = candidate_service.validate_birth_date(val)
        if not ok:
            await safe_answer(message, err or texts.ERR_INVALID_DATE_FORMAT, parse_mode="HTML")
            return
        val = norm_birth
    elif field_code == "phone":
        ok, norm_phone, err = candidate_service.validate_phone(val)
        if not ok:
            await safe_answer(message, err or texts.ERR_INVALID_PHONE, parse_mode="HTML")
            return
        val = norm_phone

    field_map: Dict[str, str] = {
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
        "extra": "extra_info",
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
        parse_mode="HTML",
    )


# --- ФИНАЛ: ОТПРАВКА АНКЕТЫ В КАДРОВУЮ СЛУЖБУ ---
@candidate_router.callback_query(CandidateForm.confirm_review, F.data == "cand_submit_final")
async def cb_submit_final(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()

    user_id = str(callback.from_user.id)
    data["user_id"] = user_id

    is_admin = (
        int(user_id) == CONFIG.get("SUPER_ADMIN_ID")
        or is_hr_admin(int(user_id))
        or is_tech_admin(int(user_id))
    )
    full_name = data.get("full_name", "")
    is_test_cand = is_admin and (db.get_setting("active_db_target", "resumes.db") == "resumes_test.db" or "тест" in full_name.lower())
    data["is_test"] = is_test_cand

    # Регистрация анкеты через сервисный слой (16 полей, триггер обучения со стипендией)
    ticket_id, meta = candidate_service.register_candidate(data, platform="tg")

    vacancy = data.get("vacancy", "")
    is_driver_no_exp = bool(meta.get("offer_training", False))

    user_success_text = texts.format_survey_success(ticket_id, vacancy, is_driver_no_exp)
    await callback.message.edit_text(
        user_success_text,
        reply_markup=make_candidate_main_keyboard(user_id=int(user_id)),
        parse_mode="HTML",
    )
    await callback.answer("Анкета успешно отправлена!")

    # Формирование карточки для отдела кадров строго через SSOT
    cand = db.get_candidate(ticket_id)
    if cand:
        hr_card = texts.format_hr_card_full(cand)
    else:
        hr_card = texts.format_hr_card_full(data)

    ticket_kb = make_ticket_keyboard(ticket_id)
    await route_new_candidate_ticket(bot, hr_card, ticket_kb)


# ==============================================================================
# КНОПКИ НАВИГАЦИИ (ПРОПУСТИТЬ, НАЗАД)
# ==============================================================================

@candidate_router.callback_query(F.data == "cand_nav_skip")
@candidate_router.message(Command("skip"))
async def cb_nav_skip(event: Union[types.CallbackQuery, types.Message], state: FSMContext) -> None:
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
            await event.answer("Этот шаг обязателен.", show_alert=True)
            return
        await safe_answer(event, "Этот шаг обязателен, пожалуйста, заполните его.")
        return

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        await event.answer("Шаг пропущен")
    else:
        await safe_answer(event, text, reply_markup=kb, parse_mode="HTML")


@candidate_router.callback_query(F.data == "cand_nav_back")
@candidate_router.message(Command("back"))
async def cb_nav_back(event: Union[types.CallbackQuery, types.Message], state: FSMContext) -> None:
    cur_state = await state.get_state()
    data = await state.get_data()

    step_transitions: Dict[str, Tuple[Any, str, Any]] = {
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
            if isinstance(kb, types.ReplyKeyboardMarkup):
                await event.message.delete()
                await event.message.answer(txt, reply_markup=kb, parse_mode="HTML")
            else:
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
                parse_mode="HTML",
            )
            await event.answer()
        else:
            await safe_answer(
                event,
                texts.MENU_RETURN,
                reply_markup=make_candidate_main_keyboard(user_id=event.from_user.id),
                parse_mode="HTML",
            )


@candidate_router.callback_query(F.data.startswith("cand_reply_hr_"))
async def cb_cand_reply_hr(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Позволяет соискателю ответить на разовое сообщение кадровика."""
    ticket_id = callback.data.split("_")[3] if len(callback.data.split("_")) > 3 else "0"
    await state.set_state(InquiryForm.waiting_question)
    await state.update_data(ticket_id=ticket_id, is_reply=True)
    await callback.message.reply(
        "✏️ <b>Введите ваш ответ для специалиста отдела кадров:</b>\n\n"
        "<i>Напишите ваше сообщение прямо в этот чат — бот передаст его в кадровый центр МУП «УЭТ».</i>",
        parse_mode="HTML",
    )
    await callback.answer()


# ==============================================================================
# ПРЯМОЙ ДИАЛОГ (LIVE-CHAT): СООБЩЕНИЯ СОИСКАТЕЛЯ В ОТДЕЛ КАДРОВ
# ==============================================================================

def is_candidate_in_dialog_filter(message: types.Message) -> bool:
    """Срабатывает ТОЛЬКО если соискатель находится в открытом прямом диалоге."""
    return bool(db.get_dialog_by_user(str(message.from_user.id)))


@candidate_router.message(is_candidate_in_dialog_filter, F.text & ~F.text.startswith("/"))
async def process_candidate_live_dialog_msg(message: types.Message, state: FSMContext, bot: Bot) -> None:
    """Трансляция сообщений кандидата кадровику во время живого чата."""
    sender_id_str = str(message.from_user.id)
    user_dlg = db.get_dialog_by_user(sender_id_str)
    if not user_dlg:
        return

    await state.clear()
    operator_id = user_dlg[1]
    name = user_dlg[3] or message.from_user.full_name

    relayed_text = texts.format_live_dialog_candidate_msg(name, message.text or "")

    builder = InlineKeyboardBuilder()
    builder.button(text="⏹ Завершить диалог", callback_data=f"end_live_dlg_{sender_id_str}")

    try:
        delivered = await safe_send(bot, int(operator_id), relayed_text, reply_markup=builder.as_markup())
    except Exception:
        delivered = False

    hr_group = CONFIG.get("HR_GROUP_ID")
    if hr_group and str(operator_id) != str(hr_group):
        try:
            await safe_send(bot, int(hr_group), relayed_text)
        except Exception:
            pass

    if delivered:
        await safe_answer(message, "✅ <i>Ваше сообщение передано в отдел кадров.</i>", parse_mode="HTML")
    else:
        await safe_answer(message, "⚠️ Не удалось доставить сообщение в отдел кадров.", parse_mode="HTML")