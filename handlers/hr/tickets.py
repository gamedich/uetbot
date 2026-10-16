"""
Управление анкетами, тикетами и прямыми коммуникациями:
- Просмотр карточки соискателя с комплаенс-скорингом (ст. 328.1 ТК РФ)
- Смена статусов (В работу, Пригласить, Отказ, В архив)
- Служебные заметки кадровика (/note, FSM)
- Прямые сообщения соискателю через бота
- Каскадное безопасное уничтожение ПДн (ст. 21 152-ФЗ)
- Мост живого диалога (Live-Chat кадровик <-> кандидат)
- Обработка входящих обращений соискателей (/ask)
"""

from __future__ import annotations

import html
import logging
from typing import Optional

from aiogram import Bot, F, Router, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

import texts
from common import (
    CandidateDirectMsgForm,
    CandidateNoteForm,
    HRReplyForm,
    check_hr_access_or_block,
    db,
    safe_answer,
    send_photo_to_candidate,
    send_response_to_candidate,
)
from keyboards import (
    make_cand_reply_keyboard,
    make_candidate_main_keyboard,
    make_ticket_keyboard,
)
from services.compliance_service import ComplianceService

logger = logging.getLogger("HR_TICKETS")
tickets_router = Router(name="hr_tickets")


# ==============================================================================
# 1. ПРОСМОТР КАРТОЧКИ И БЫСТРЫЙ ЗВОНОК
# ==============================================================================


@tickets_router.callback_query(F.data.startswith("view_"))
async def cb_view_ticket(callback: types.CallbackQuery) -> None:
    """Детальный просмотр карточки соискателя со скорингом безопасности ст. 328.1 ТК РФ."""
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer(err_text or "🚫 Доступ ограничен.", show_alert=True)
        return

    ticket_id = int(callback.data.split("_")[1])
    cand = db.get_candidate(ticket_id)
    if not cand:
        await callback.answer("⚠️ Анкета не найдена!", show_alert=True)
        return

    card = texts.format_hr_card_full(cand)

    # Интеграция скоринга комплаенса
    scoring = ComplianceService.score_candidate(cand)
    if scoring.get("summary_html"):
        card += f"\n\n{scoring['summary_html']}"

    try:
        await callback.message.edit_text(card, reply_markup=make_ticket_keyboard(ticket_id), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@tickets_router.callback_query(F.data.startswith("cand_call_"))
async def cb_cand_call(callback: types.CallbackQuery) -> None:
    """Всплывающее окно с номером телефона соискателя."""
    ticket_id = int(callback.data.split("_")[2])
    cand = db.get_candidate(ticket_id)
    if not cand:
        await callback.answer("Анкета не найдена!", show_alert=True)
        return
    phone = cand[4]
    name = cand[3]
    await callback.answer(f"📞 Телефон {name}: {phone}", show_alert=True)


@tickets_router.callback_query(F.data.startswith("cand_risk_"))
async def cb_cand_risk_check(callback: types.CallbackQuery) -> None:
    """Мгновенная проверка комплаенс-рисков соискателя (ст. 328.1 ТК РФ, права, судимость, медкомиссия)."""
    allowed, _ = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer("🚫 Доступ ограничен сотрудниками отдела кадров.", show_alert=True)
        return

    ticket_id = int(callback.data.split("_")[-1])
    cand = db.get_candidate(ticket_id)
    if not cand:
        await callback.answer("Анкета не найдена!", show_alert=True)
        return

    scoring = ComplianceService.score_candidate(cand)
    has_risk = scoring.get("has_high_risk", False)
    status_emoji = "⚠️ ВЫСОКИЙ РИСК" if has_risk else "✅ РИСКИ НЕ ВЫЯВЛЕНЫ"
    reasons = scoring.get("risk_reasons") or ["Проверки по 152-ФЗ и ст. 328.1 ТК РФ пройдены."]
    reasons_text = "\n• ".join(reasons)
    alert_text = f"🔍 Проверка рисков (#{ticket_id}):\n{status_emoji}\n• {reasons_text}"
    await callback.answer(alert_text[:200], show_alert=True)


# ==============================================================================
# 2. ИЗМЕНЕНИЕ СТАТУСОВ АНКЕТ
# ==============================================================================


@tickets_router.callback_query(F.data.startswith("status_"))
async def cb_change_status(callback: types.CallbackQuery) -> None:
    """Оперативное переключение статуса анкеты в воронке."""
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer("🚫 Доступ ограничен сотрудниками отдела кадров.", show_alert=True)
        return

    if callback.data.startswith("status_noop_"):
        await callback.answer("ℹ️ Анкета уже имеет данный статус!", show_alert=True)
        return

    parts = callback.data.split("_")
    ticket_id = int(parts[1])
    new_status = parts[2]

    cand = db.get_candidate(ticket_id)
    if not cand:
        await callback.answer("Анкета не найдена!", show_alert=True)
        return

    platform, user_id, full_name = cand[1], cand[2], cand[3]
    db.update_status(ticket_id, new_status)

    user_msg: Optional[str] = None
    if new_status == "В работе":
        user_msg = texts.format_status_in_progress(full_name)
        alert_msg = "Статус изменен на «В работе»"
    elif new_status == "Приглашен":
        user_msg = texts.format_status_invited(full_name)
        alert_msg = "Кандидат приглашен"
    elif new_status == "Отказ":
        user_msg = texts.format_status_rejected(full_name)
        alert_msg = "Кандидату отправлен отказ (повтор через 3 мес.)"
    elif new_status == "Архив":
        alert_msg = "📦 Анкета перенесена в архив"
    else:
        user_msg = (
            f"📋 <b>Здравствуйте, {html.escape(full_name)}!</b>\n\n"
            f"Статус вашей анкеты #{ticket_id} изменен на: <b>{html.escape(new_status)}</b>."
        )
        alert_msg = f"Статус: {new_status}"

    if user_msg:
        await send_response_to_candidate(platform, user_id, user_msg)
    await callback.answer(alert_msg)

    try:
        updated_cand = db.get_candidate(ticket_id)
        if updated_cand:
            updated_card = texts.format_hr_card_full(updated_cand)
            scoring = ComplianceService.score_candidate(updated_cand)
            if scoring.get("summary_html"):
                updated_card += f"\n\n{scoring['summary_html']}"
            await callback.message.edit_text(
                updated_card, reply_markup=make_ticket_keyboard(ticket_id), parse_mode="HTML"
            )
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=make_ticket_keyboard(ticket_id))
        except Exception:
            pass


# ==============================================================================
# 3. СЛУЖЕБНЫЕ ЗАМЕТКИ КАДРОВИКА
# ==============================================================================


@tickets_router.callback_query(F.data.startswith("cand_note_") | F.data.startswith("note_"))
async def cb_cand_note_ask(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Запрос на ввод служебной заметки к анкете соискателя."""
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer("🚫 Доступ ограничен сотрудниками отдела кадров.", show_alert=True)
        return

    ticket_id = int(callback.data.split("_")[-1])
    cand = db.get_candidate(ticket_id)
    if not cand:
        await callback.answer("Анкета не найдена!", show_alert=True)
        return

    cand_name = cand[3]
    cur_note = cand[8] if len(cand) > 8 and cand[8] else "отсутствует"
    await state.set_state(CandidateNoteForm.waiting_note)
    await state.update_data(ticket_id=ticket_id)

    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=f"view_{ticket_id}")
    prompt_text = (
        f"📝 <b>Заметка к анкете #{ticket_id} ({html.escape(cand_name)})</b>\n\n"
        f"📌 <b>Текущая заметка:</b> <i>{html.escape(cur_note)}</i>\n\n"
        "Отправьте текст новой заметки (или отправьте <code>-</code> для удаления):"
    )
    try:
        await callback.message.reply(prompt_text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        await safe_answer(callback.message, prompt_text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@tickets_router.message(CandidateNoteForm.waiting_note)
async def process_candidate_note(message: types.Message, state: FSMContext) -> None:
    """Сохранение текста служебной заметки к анкете."""
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    await state.clear()

    if not ticket_id:
        await safe_answer(message, "Ошибка: не найден номер анкеты.")
        return

    text = message.text.strip() if message.text else ""
    if text == "-":
        db.update_admin_note(ticket_id, "")
        msg = f"🗑 Заметка к анкете #{ticket_id} удалена."
    else:
        db.update_admin_note(ticket_id, text)
        msg = texts.format_ticket_note_saved(ticket_id, text)

    builder = InlineKeyboardBuilder()
    builder.button(text=f"Открыть карточку #{ticket_id}", callback_data=f"view_{ticket_id}")
    await safe_answer(message, msg, reply_markup=builder.as_markup(), parse_mode="HTML")


@tickets_router.message(Command("note", "admin_note"))
async def cmd_admin_note(message: types.Message) -> None:
    """Быстрое добавление заметки через команду: /note <id> <текст>."""
    allowed, err_text = check_hr_access_or_block(message.from_user.id, message.chat.id)
    if not allowed:
        await safe_answer(message, err_text or "🚫 Доступ ограничен.", parse_mode="HTML")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await safe_answer(
            message, "Использование: <code>/note &lt;номер_анкеты&gt; &lt;текст заметки&gt;</code>", parse_mode="HTML"
        )
        return

    try:
        t_id = int(parts[1])
    except ValueError:
        await safe_answer(message, "Номер анкеты должен быть числом.")
        return

    cand = db.get_candidate(t_id)
    if not cand:
        await safe_answer(message, f"Анкета #{t_id} не найдена.")
        return

    note_text = parts[2].strip()
    db.update_admin_note(t_id, note_text)
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Открыть карточку #{t_id}", callback_data=f"view_{t_id}")
    await safe_answer(
        message,
        texts.format_ticket_note_saved(t_id, note_text),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


# ==============================================================================
# 4. ПРЯМЫЕ СООБЩЕНИЯ КАНДИДАТУ
# ==============================================================================


@tickets_router.callback_query(F.data.startswith("cand_msg_"))
async def cb_cand_direct_msg_ask(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Запрос на ввод одноразового сообщения кандидату от лица отдела кадров."""
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer("🚫 Доступ ограничен сотрудниками отдела кадров.", show_alert=True)
        return

    ticket_id = int(callback.data.split("_")[2])
    cand = db.get_candidate(ticket_id)
    if not cand:
        await callback.answer("Анкета не найдена!", show_alert=True)
        return

    await state.set_state(CandidateDirectMsgForm.waiting_text)
    await state.update_data(ticket_id=ticket_id, platform=cand[1], user_id=cand[2], full_name=cand[3])

    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=f"view_{ticket_id}")
    prompt = (
        f"✉️ <b>Сообщение соискателю #{ticket_id} ({html.escape(cand[3])})</b>\n\n"
        f"Платформа: <b>{cand[1].upper()}</b>\n"
        "Отправьте текст, который будет доставлен кандидату от лица отдела кадров:"
    )
    try:
        await callback.message.reply(prompt, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        await safe_answer(callback.message, prompt, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@tickets_router.message(CandidateDirectMsgForm.waiting_text)
async def process_candidate_direct_msg(message: types.Message, state: FSMContext) -> None:
    """Отправка прямого сообщения кандидату в Telegram, VK или МАКС."""
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    platform = data.get("platform", "tg")
    user_id = data.get("user_id")
    full_name = data.get("full_name", "")
    await state.clear()

    if not ticket_id or not user_id:
        await safe_answer(message, "Ошибка отправки: не найдены данные соискателя.")
        return

    text_to_send = (
        "📨 <b>СООБЩЕНИЕ ОТ ОТДЕЛА КАДРОВ МУП «УЛЬЯНОВСКЭЛЕКТРОТРАНС»:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"{html.escape(message.text or '')}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Вы можете отправить ответ ответным сообщением боту.</i>"
    )

    reply_kb = make_cand_reply_keyboard(ticket_id) if platform == "tg" else None
    ok = await send_response_to_candidate(platform, user_id, text_to_send, keyboard=reply_kb)

    builder = InlineKeyboardBuilder()
    builder.button(text=f"Открыть карточку #{ticket_id}", callback_data=f"view_{ticket_id}")

    if ok:
        await safe_answer(
            message,
            f"✅ Сообщение кандидату #{ticket_id} ({html.escape(full_name)}) успешно доставлено в {platform.upper()}.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    else:
        await safe_answer(
            message,
            f"⚠️ Не удалось доставить сообщение кандидату в {platform.upper()}.",
            reply_markup=builder.as_markup(),
        )


# ==============================================================================
# 5. КАСКАДНОЕ УДАЛЕНИЕ ДАННЫХ (ст. 21 152-ФЗ)
# ==============================================================================


@tickets_router.callback_query(F.data.startswith("del_ask_"))
async def cb_delete_ask(callback: types.CallbackQuery) -> None:
    """Подтверждение удаления анкеты."""
    ticket_id = int(callback.data.split("_")[2])
    builder = InlineKeyboardBuilder()
    builder.button(text="💥 Да, удалить безвозвратно", callback_data=f"del_confirm_{ticket_id}")
    builder.button(text="❌ Отмена", callback_data=f"view_{ticket_id}")
    builder.adjust(1)
    await callback.message.edit_text(
        texts.format_ticket_delete_confirm_prompt(ticket_id),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@tickets_router.callback_query(F.data.startswith("del_confirm_"))
async def cb_delete_confirm(callback: types.CallbackQuery) -> None:
    """Физическое удаление анкеты соискателя."""
    ticket_id = int(callback.data.split("_")[2])
    cand = db.get_candidate(ticket_id)
    if not cand:
        await callback.answer("Анкета не найдена!", show_alert=True)
        return

    name = cand[3]
    platform = cand[1]
    user_id = str(cand[2])

    # Фиксация в протоколе уничтожения
    db.log_pdn_destruction(ticket_id, user_id, platform, reason="Удаление кадровиком через панель управления")
    db.delete_candidate(ticket_id)

    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ К списку резюме", callback_data="admin_list_all")
    await callback.message.edit_text(
        f"🗑 <b>Анкета #{ticket_id} ({html.escape(name)}) успешно удалена из системы.</b>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer("Анкета удалена.")


# ==============================================================================
# 6. МОСТ ЖИВОГО ДИАЛОГА (Live-Chat)
# ==============================================================================


@tickets_router.callback_query(F.data.startswith("live_dlg_cand_"))
async def cb_live_dlg_cand(callback: types.CallbackQuery) -> None:
    """Инициация прямого чата с соискателем по анкете."""
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer("🚫 Доступ ограничен сотрудниками отдела кадров.", show_alert=True)
        return

    ticket_id = int(callback.data.split("_")[3])
    cand = db.get_candidate(ticket_id)
    if not cand:
        await callback.answer("Анкета не найдена!", show_alert=True)
        return

    platform = cand[1]
    cand_user_id = str(cand[2])
    cand_name = cand[3]

    db.start_dialog(cand_user_id, callback.message.chat.id, ticket_id=ticket_id, full_name=cand_name, platform=platform)

    builder = InlineKeyboardBuilder()
    builder.button(text="⏹ Завершить диалог", callback_data=f"end_live_dlg_{cand_user_id}")
    builder.button(text="📋 К анкете", callback_data=f"view_{ticket_id}")
    builder.adjust(1)

    notice_for_cand = (
        "🟢 <b>С вами на связи сотрудник отдела кадров МУП «Ульяновскэлектротранс».</b>\n\n"
        "Вы можете задавать ваши вопросы прямо здесь. Все ваши ответы доставляются кадровику."
    )
    await send_response_to_candidate(platform, cand_user_id, notice_for_cand)

    await safe_answer(
        callback.message,
        f"🟢 <b>Прямой диалог открыт!</b>\n"
        f"Соискатель: <b>{html.escape(cand_name)}</b> (#{ticket_id}, {platform.upper()})\n\n"
        "Все ваши текстовые сообщения и фото будут направляться кандидату.\n"
        "Для завершения отправьте команду /stop или нажмите кнопку ниже.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer("Диалог активирован!")


@tickets_router.callback_query(F.data.startswith("live_dlg_inq_"))
async def cb_live_dlg_inq(callback: types.CallbackQuery) -> None:
    """Инициация прямого чата по входящему обращению соискателя (/ask)."""
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer("🚫 Доступ ограничен сотрудниками отдела кадров.", show_alert=True)
        return

    inq_id = int(callback.data.split("_")[3])
    inq = db.get_inquiry(inq_id)
    if not inq:
        await callback.answer("Обращение не найдено!", show_alert=True)
        return

    ticket_id = inq[1]
    platform = inq[2]
    user_id = str(inq[3])
    full_name = inq[4] or "Соискатель"

    db.start_dialog(user_id, callback.message.chat.id, ticket_id=ticket_id, full_name=full_name, platform=platform)

    builder = InlineKeyboardBuilder()
    builder.button(text="⏹ Завершить диалог", callback_data=f"end_live_dlg_{user_id}")

    notice = (
        "🟢 <b>Сотрудник отдела кадров подключился к диалогу по вашему вопросу.</b>\n\n"
        "Вы можете продолжить общение прямо здесь."
    )
    await send_response_to_candidate(platform, user_id, notice)

    await safe_answer(
        callback.message,
        f"🟢 <b>Прямой диалог по вопросу #{inq_id} начат!</b>\n"
        f"Кандидат: <b>{html.escape(full_name)}</b> ({platform.upper()})\n\n"
        "Для завершения отправьте /stop.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer("Диалог открыт!")


@tickets_router.message(Command("stop"))
async def cmd_stop_live_dlg(message: types.Message) -> None:
    """Завершение активного прямого диалога со стороны кадровика."""
    dlg = db.get_dialog_by_operator(message.chat.id) or db.get_dialog_by_operator(message.from_user.id)
    if not dlg:
        await safe_answer(message, "У вас нет активного прямого диалога.")
        return

    user_id = str(dlg[0])
    platform = dlg[4] if len(dlg) > 4 and dlg[4] else "tg"
    cand_name = dlg[3] or "Соискатель"

    db.end_dialog(user_id)
    await send_response_to_candidate(
        platform,
        user_id,
        "⏹ <b>Диалог с отделом кадров завершён.</b>\n\nБлагодарим за обращение в МУП «Ульяновскэлектротранс»!",
        keyboard=make_candidate_main_keyboard(),
    )
    await safe_answer(
        message,
        f"⏹ <b>Прямой диалог с {html.escape(cand_name)} (ID: <code>{user_id}</code>) успешно завершён.</b>",
        parse_mode="HTML",
    )


@tickets_router.callback_query(F.data.startswith("end_live_dlg_"))
async def cb_end_live_dlg(callback: types.CallbackQuery) -> None:
    """Завершение диалога по инлайн-кнопке."""
    user_id = callback.data.split("_")[3]
    dlg = db.get_dialog(user_id)
    if not dlg:
        await callback.answer("Диалог уже был завершён.", show_alert=True)
        return

    platform = dlg[4] if len(dlg) > 4 and dlg[4] else "tg"
    db.end_dialog(user_id)
    await send_response_to_candidate(
        platform,
        user_id,
        "⏹ <b>Диалог с отделом кадров завершён.</b>\nСпасибо за обращение!",
        keyboard=make_candidate_main_keyboard(),
    )
    await callback.answer("Прямой диалог завершен.")
    try:
        await callback.message.edit_text(
            f"⏹ <b>Прямой диалог с кандидатом {html.escape(dlg[3])} (ID: <code>{user_id}</code>) успешно завершён.</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass


def is_operator_in_dialog_filter(message: types.Message) -> bool:
    """Срабатывает ТОЛЬКО если чат/кадровик находится в активном прямом диалоге."""
    return bool(db.get_dialog_by_operator(message.chat.id) or db.get_dialog_by_operator(message.from_user.id))


@tickets_router.message(is_operator_in_dialog_filter, F.text & ~F.text.startswith("/"))
async def process_live_dialog_router(message: types.Message, state: FSMContext, bot: Bot) -> None:
    """Трансляция сообщений кадровика соискателю в режиме Live-Chat."""
    current_state = await state.get_state()
    if current_state is not None:
        return

    sender_id = message.from_user.id
    op_dlg = db.get_dialog_by_operator(message.chat.id) or db.get_dialog_by_operator(sender_id)
    if not op_dlg:
        return

    cand_user_id = str(op_dlg[0])
    ticket_id = op_dlg[2]
    platform = op_dlg[4] if len(op_dlg) > 4 and op_dlg[4] else "tg"
    if platform == "tg" and ticket_id:
        cand = db.get_candidate(ticket_id)
        if cand and cand[1]:
            platform = cand[1]

    relayed_to_cand = texts.format_live_dialog_operator_msg(message.text or "")
    ok = await send_response_to_candidate(platform, cand_user_id, relayed_to_cand)
    if ok:
        await safe_answer(message, f"✅ <i>Доставлено соискателю [{platform.upper()}]</i>", parse_mode="HTML")
    else:
        await safe_answer(message, f"⚠️ Не удалось доставить сообщение кандидату ({platform.upper()}).")


@tickets_router.message(is_operator_in_dialog_filter, StateFilter(None), F.photo)
async def process_live_dialog_photo(message: types.Message, state: FSMContext, bot: Bot) -> None:
    """Потоковая передача фото от кадровика соискателю строго через RAM (152-ФЗ)."""
    current_state = await state.get_state()
    if current_state is not None:
        return

    sender_id = message.from_user.id
    op_dlg = db.get_dialog_by_operator(message.chat.id) or db.get_dialog_by_operator(sender_id)
    if not op_dlg:
        return

    cand_user_id = str(op_dlg[0])
    ticket_id = op_dlg[2]
    platform = op_dlg[4] if len(op_dlg) > 4 and op_dlg[4] else "tg"
    if platform == "tg" and ticket_id:
        cand = db.get_candidate(ticket_id)
        if cand and cand[1]:
            platform = cand[1]

    caption = message.caption or ""
    relayed_caption = (
        f"📷 [Отдел кадров МУП УЭТ]:\n{caption}" if caption else "📷 Фотография от сотрудника отдела кадров."
    )

    try:
        photo = message.photo[-1]
        file_io = await bot.download(photo.file_id)
        file_bytes = file_io.read()
        ok = await send_photo_to_candidate(platform, cand_user_id, file_bytes, caption=relayed_caption)
        if ok:
            await safe_answer(message, f"✅ <i>Фото доставлено соискателю [{platform.upper()}]</i>", parse_mode="HTML")
        else:
            await safe_answer(message, f"⚠️ Не удалось доставить фото соискателю ({platform.upper()}).")
    except Exception as e:
        logger.error("Ошибка передачи фото в диалоге: %s", e)
        await safe_answer(message, "Ошибка отправки изображения.")


# ==============================================================================
# 7. ОБРАЩЕНИЯ СОИСКАТЕЛЕЙ (/ask)
# ==============================================================================


@tickets_router.callback_query(F.data.startswith("inq_call_"))
async def cb_inq_call(callback: types.CallbackQuery) -> None:
    """Показ номера соискателя из вопроса."""
    inquiry_id = int(callback.data.split("_")[2])
    inq = db.get_inquiry(inquiry_id)
    if not inq:
        await callback.answer("Вопрос не найден!", show_alert=True)
        return
    phone = inq[5] or "Не указан"
    name = inq[4] or "Соискатель"
    await callback.answer(f"📞 Телефон {name}: {phone}", show_alert=True)


@tickets_router.callback_query(F.data.startswith("inq_close_"))
async def cb_inq_close(callback: types.CallbackQuery) -> None:
    """Закрытие обращения без ответа."""
    inquiry_id = int(callback.data.split("_")[2])
    db.close_inquiry(inquiry_id)
    await callback.answer("Вопрос закрыт.")
    try:
        await callback.message.edit_text(
            f"📁 <b>Обращение #{inquiry_id} закрыто сотрудником отдела кадров.</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass


@tickets_router.callback_query(F.data.startswith("inq_reply_"))
async def cb_inq_reply_ask(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Запрос на ввод ответа на вопрос соискателя."""
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer("🚫 Доступ ограничен сотрудниками отдела кадров.", show_alert=True)
        return

    inquiry_id = int(callback.data.split("_")[2])
    inq = db.get_inquiry(inquiry_id)
    if not inq:
        await callback.answer("Обращение не найдено!", show_alert=True)
        return

    await state.set_state(HRReplyForm.waiting_reply)
    await state.update_data(
        inquiry_id=inquiry_id,
        user_id=str(inq[3]),
        platform=inq[2],
        full_name=inq[4] or "Соискатель",
        question=inq[7],
    )

    prompt = (
        f"💬 <b>Ответ на вопрос #{inquiry_id} ({html.escape(inq[4] or 'Кандидат')})</b>\n\n"
        f"❓ <b>Вопрос:</b> <i>{html.escape(inq[7])}</i>\n\n"
        "Отправьте текст официального ответа:"
    )
    await safe_answer(callback.message, prompt, parse_mode="HTML")
    await callback.answer()


@tickets_router.message(HRReplyForm.waiting_reply)
async def process_hr_reply(message: types.Message, state: FSMContext) -> None:
    """Доставка официального ответа соискателю."""
    data = await state.get_data()
    inquiry_id = data.get("inquiry_id")
    user_id = data.get("user_id")
    platform = data.get("platform", "tg")
    full_name = data.get("full_name", "")
    question = data.get("question", "")
    await state.clear()

    reply_text = message.text.strip() if message.text else ""
    db.reply_inquiry(inquiry_id, reply_text)

    cand_text = texts.format_inquiry_reply_msg(inquiry_id, question, reply_text)
    ok = await send_response_to_candidate(platform, user_id, cand_text)

    if ok:
        await safe_answer(
            message,
            f"✅ Ответ на обращение #{inquiry_id} успешно доставлен кандидату ({html.escape(full_name)}) в {platform.upper()}.",
            parse_mode="HTML",
        )
    else:
        await safe_answer(message, f"⚠️ Не удалось доставить ответ кандидату в {platform.upper()}.")
