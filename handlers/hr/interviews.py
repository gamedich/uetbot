"""
Сценарии назначения собеседований и выбора филиалов депо:
- Выбор филиала депо (№1 Северное, №2 Засвияжское, №3 Заволжское, Управление)
- Выбор готовых слотов времени
- Ручной ввод даты и времени встречи (CustomInviteForm)
- Отправка приглашений кандидату с инлайн-кнопками подтверждения
- Обработка ответов соискателя («Буду точно» / «Прошу перенести»)
"""

from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

import texts
from common import (
    CustomInviteForm,
    check_hr_access_or_block,
    db,
    safe_answer,
    send_response_to_candidate,
)
from keyboards import (
    make_cand_reply_keyboard,
    make_depot_selection_keyboard,
    make_interview_slots_keyboard,
    make_ticket_keyboard,
)
from services.compliance_service import ComplianceService
from services.interview_service import InterviewService

logger = logging.getLogger("HR_INTERVIEWS")
interviews_router = Router(name="hr_interviews")


# ==============================================================================
# 1. СЦЕНАРИЙ НАЗНАЧЕНИЯ СОБЕСЕДОВАНИЯ В ДЕПО
# ==============================================================================


@interviews_router.callback_query(F.data.startswith("schedule_interview_"))
async def cb_schedule_interview_start(callback: types.CallbackQuery) -> None:
    """Шаг 1: Выбор филиала депо для встречи с соискателем."""
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer(err_text or "🚫 Доступ ограничен.", show_alert=True)
        return

    ticket_id = int(callback.data.split("_")[2])
    cand = db.get_candidate(ticket_id)
    if not cand:
        await callback.answer("Анкета не найдена!", show_alert=True)
        return

    name = html.escape(cand[3] if len(cand) > 3 else "Соискатель")
    vac = html.escape(cand[5] if len(cand) > 5 else "Должность")

    text = (
        f"🏢 <b>НАЗНАЧЕНИЕ СОБЕСЕДОВАНИЯ #{ticket_id}</b>\n"
        f"👤 Кандидат: <b>{name}</b> ({vac})\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите филиал депо или подразделение, куда приглашается соискатель:"
    )
    await callback.message.edit_text(text, reply_markup=make_depot_selection_keyboard(ticket_id), parse_mode="HTML")
    await callback.answer()


@interviews_router.callback_query(F.data.startswith("int_depot_"))
async def cb_select_depot(callback: types.CallbackQuery) -> None:
    """Шаг 2: Выбор времени встречи в выбранном филиале депо."""
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer(err_text or "🚫 Доступ ограничен.", show_alert=True)
        return

    parts = callback.data.split("_")
    ticket_id = int(parts[2])
    depot_code = f"{parts[3]}_{parts[4]}" if len(parts) > 4 else parts[3]

    depot_info = InterviewService.get_depot(depot_code)
    slots = InterviewService.generate_interview_slots()

    text = (
        f"🕒 <b>ВЫБОР СЛОТА ВРЕМЕНИ СОБЕСЕДОВАНИЯ</b>\n"
        f"🏢 Филиал: <b>{html.escape(depot_info['name'])}</b>\n"
        f"🏠 Адрес: <code>{html.escape(depot_info['address'])}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите один из предложенных рабочих слотов времени или укажите произвольную дату вручную:"
    )
    await callback.message.edit_text(
        text,
        reply_markup=make_interview_slots_keyboard(ticket_id, depot_code, slots),
        parse_mode="HTML",
    )
    await callback.answer()


@interviews_router.callback_query(F.data.startswith("int_slot_"))
async def cb_select_slot(callback: types.CallbackQuery, bot: Bot) -> None:
    """Шаг 3: Фиксация слота, сохранение в БД и отправка приглашения кандидату."""
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer(err_text or "🚫 Доступ ограничен.", show_alert=True)
        return

    parts = callback.data.split("_", 4)
    ticket_id = int(parts[2])
    depot_code = parts[3]
    slot_time = parts[4]

    ok, desc = await InterviewService.send_interview_invite(ticket_id, depot_code, slot_time)
    if ok:
        await callback.answer("✅ Приглашение отправлено кандидату!", show_alert=True)
    else:
        await callback.answer(f"⚠️ {desc}", show_alert=True)

    cand = db.get_candidate(ticket_id)
    if cand:
        card = texts.format_hr_card_full(cand)
        scoring = ComplianceService.score_candidate(cand)
        if scoring.get("summary_html"):
            card += f"\n\n{scoring['summary_html']}"
        await callback.message.edit_text(
            f"{'✅' if ok else '⚠️'} {desc}\n\n{card}",
            reply_markup=make_ticket_keyboard(ticket_id),
            parse_mode="HTML",
        )


# ==============================================================================
# 2. РУЧНОЙ ВВОД ДАТЫ ВСТРЕЧИ (CustomInviteForm)
# ==============================================================================


@interviews_router.callback_query(F.data.startswith("invite_custom_"))
async def cb_invite_custom(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Запрос произвольной даты и времени собеседования в свободной форме."""
    ticket_id = int(callback.data.split("_")[2])
    cand = db.get_candidate(ticket_id)
    if not cand:
        await callback.answer("Анкета не найдена!", show_alert=True)
        return

    await state.set_state(CustomInviteForm.waiting_datetime)
    await state.update_data(ticket_id=ticket_id)

    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=f"view_{ticket_id}")

    text = (
        f"📅 <b>ПРИГЛАШЕНИЕ НА СОБЕСЕДОВАНИЕ</b>\n"
        f"для соискателя <b>{html.escape(cand[3])}</b> (Анкета #{ticket_id}):\n\n"
        "Напишите ответным сообщением дату, время, кабинет и любые пояснения для кандидата в свободной форме.\n\n"
        "<i>Сообщение будет отправлено соискателю. Для отмены нажмите кнопку ниже:</i>"
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@interviews_router.message(CustomInviteForm.waiting_datetime)
async def process_custom_invite_text(message: types.Message, state: FSMContext) -> None:
    """Обработка ручного ввода даты и времени."""
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    cand = db.get_candidate(ticket_id)
    if not cand:
        await state.clear()
        await safe_answer(message, "⚠️ Анкета не найдена.")
        return

    dt_text = (message.text or "").strip()
    if not dt_text:
        await safe_answer(message, "⚠️ Пожалуйста, напишите дату и время встречи сообщением.")
        return

    await state.clear()
    platform, user_id, full_name = cand[1], cand[2], cand[3]
    db.set_interview_details(ticket_id, "Отдел кадров", dt_text)

    user_msg = texts.format_hr_invite_custom(full_name, dt_text)

    if platform == "vk":
        from gateways.vk_gateway import make_vk_reply_keyboard  # type: ignore

        ok = await send_response_to_candidate(platform, user_id, user_msg, keyboard=make_vk_reply_keyboard(ticket_id))
    else:
        ok = await send_response_to_candidate(platform, user_id, user_msg, keyboard=make_cand_reply_keyboard(ticket_id))

    updated_cand = db.get_candidate(ticket_id)
    updated_card = texts.format_hr_card_full(updated_cand) if updated_cand else ""
    if updated_cand:
        scoring = ComplianceService.score_candidate(updated_cand)
        if scoring.get("summary_html"):
            updated_card += f"\n\n{scoring['summary_html']}"

    status_note = (
        "✅ Приглашение успешно отправлено соискателю!" if ok else "⚠️ Не удалось доставить сообщение кандидату."
    )
    await safe_answer(
        message,
        f"{status_note}\n\n{updated_card}",
        reply_markup=make_ticket_keyboard(ticket_id),
        parse_mode="HTML",
    )


# ==============================================================================
# 3. ОБРАБОТКА ПОДТВЕРЖДЕНИЯ ЯВКИ СОИСКАТЕЛЕМ
# ==============================================================================


@interviews_router.callback_query(F.data.startswith("cand_int_confirm_"))
async def cb_candidate_confirm_interview(callback: types.CallbackQuery, bot: Bot) -> None:
    """Кандидат нажимает 'Буду точно'."""
    ticket_id = int(callback.data.split("_")[3])
    ok, user_msg = await InterviewService.handle_candidate_feedback(bot, ticket_id, is_confirmed=True)
    await callback.answer("Явка подтверждена!")
    await callback.message.edit_text(user_msg, parse_mode="HTML")


@interviews_router.callback_query(F.data.startswith("cand_int_resched_"))
async def cb_candidate_reschedule_interview(callback: types.CallbackQuery, bot: Bot) -> None:
    """Кандидат нажимает 'Прошу перенести'."""
    ticket_id = int(callback.data.split("_")[3])
    ok, user_msg = await InterviewService.handle_candidate_feedback(bot, ticket_id, is_confirmed=False)
    await callback.answer("Запрос на перенос отправлен в отдел кадров.")
    await callback.message.edit_text(user_msg, parse_mode="HTML")
