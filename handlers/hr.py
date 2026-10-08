# -*- coding: utf-8 -*-
"""
Обработчики кадровой службы МУП «Ульяновскэлектротранс»:
- Команда /hr, /admin и кадровая аналитика
- Просмотр и изменение статусов анкет (В работу, Пригласить, Отказ, Архив)
- Мост прямого диалога (живой чат соискателя и кадровика)
- Обработка входящих вопросов соискателей (/ask)
- Заметки кадровика к анкетам
- Экспорт базы соискателей в Excel/CSV (/export)
- Привязка кадрового чата (/set_group) и управление черным списком (/ban, /unban)
- Авто-выдача и авто-снятие ролей при входе/выходе из группы
- Двухсторонняя синхронизация состава группы (/sync)
- Быстрый кик нарушителей (/kick)
- Генерация официального Акта об уничтожении ПДн (Приказ Роскомнадзора № 179)
"""

from __future__ import annotations

import csv
import html
import io
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from aiogram import Bot, F, Router, types
from aiogram.filters import Command, StateFilter
from aiogram.filters.chat_member_updated import (
    JOIN_TRANSITION,
    LEAVE_TRANSITION,
    ChatMemberUpdatedFilter,
)
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    ChatMemberAdministrator,
    ChatMemberOwner,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import texts
from common import (
    CONFIG,
    CandidateDirectMsgForm,
    CandidateNoteForm,
    CustomInviteForm,
    HRAccessMiddleware,
    HRReplyForm,
    check_hr_access_or_block,
    db,
    is_hr_admin,
    is_tech_admin,
    safe_answer,
    safe_send,
    send_photo_to_candidate,
    send_response_to_candidate,
)
from config import update_env_variable
from keyboards import (
    make_admin_menu_keyboard,
    make_cand_reply_keyboard,
    make_candidate_main_keyboard,
    make_inquiry_admin_keyboard,
    make_ticket_keyboard,
)

logger = logging.getLogger("HR_HANDLER")
hr_router = Router(name="hr")

# Подключение централизованного middleware авторизации
hr_router.message.middleware(HRAccessMiddleware())
hr_router.callback_query.middleware(HRAccessMiddleware())


# ==============================================================================
# 1. КАДРОВАЯ ПАНЕЛЬ И АНАЛИТИКА (/hr, /admin)
# ==============================================================================

@hr_router.message(Command("hr", "admin", "kadry"))
async def cmd_admin(message: types.Message) -> None:
    """Вывод сводки кадровой панели управления и воронки подбора."""
    user_id = message.from_user.id
    chat_id = message.chat.id

    allowed, err_text = check_hr_access_or_block(user_id, chat_id)
    if not allowed:
        await safe_answer(message, err_text or "🚫 Доступ ограничен.", parse_mode="HTML")
        return

    stats = db.get_statistics()
    text = (
        "⚙️ <b>КАДРОВАЯ ПАНЕЛЬ МУП «УЛЬЯНОВСКЭЛЕКТРОТРАНС»</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Всего анкет: <code>{stats['total']}</code> | 🆕 Новых: <code>{stats['new']}</code>\n"
        f"🟡 В работе: <code>{stats.get('in_progress', 0)}</code> | 📦 В архиве: <code>{stats.get('archive', 0)}</code>\n"
        f"🟢 Приглашено: <code>{stats['invited']}</code> | 🔴 Отклонено: <code>{stats['rejected']}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    await safe_answer(message, text, reply_markup=make_admin_menu_keyboard(user_id), parse_mode="HTML")


@hr_router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: types.CallbackQuery) -> None:
    """Интерактивное обновление статистики кадровой службы."""
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.message.edit_text(err_text or "🚫 Доступ ограничен.", parse_mode="HTML")
        return

    stats = db.get_statistics()
    text = (
        "📊 <b>СТАТИСТИКА ОТДЕЛА КАДРОВ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 Всего анкет: <b>{stats['total']}</b>\n"
        f"🆕 Ожидают проверки (новые): <b>{stats['new']}</b>\n"
        f"🟡 В работе: <b>{stats.get('in_progress', 0)}</b>\n"
        f"🟢 Приглашены: <b>{stats['invited']}</b>\n"
        f"🔴 Отклонены: <b>{stats['rejected']}</b>\n"
        f"📦 В архиве: <b>{stats.get('archive', 0)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    await callback.message.edit_text(
        text, reply_markup=make_admin_menu_keyboard(callback.from_user.id), parse_mode="HTML"
    )
    await callback.answer()


@hr_router.callback_query(
    F.data.in_(["admin_list_all", "admin_list_new", "admin_list_in_progress", "admin_list_archive"])
)
async def cb_admin_list(callback: types.CallbackQuery) -> None:
    """Просмотр списков анкет с фильтрацией по статусам воронки."""
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.message.edit_text(err_text or "🚫 Доступ ограничен.", parse_mode="HTML")
        return

    only_new = callback.data == "admin_list_new"
    is_archive = callback.data == "admin_list_archive"
    filter_status = "В работе" if callback.data == "admin_list_in_progress" else ("Архив" if is_archive else None)
    candidates = db.get_recent_candidates(
        limit=10, filter_status=filter_status, only_new=only_new, is_archive=is_archive
    )

    if only_new:
        title = "📥 <b>НОВЫЕ РЕЗЮМЕ:</b>"
    elif filter_status == "В работе":
        title = "🟡 <b>АНКЕТЫ В РАБОТЕ:</b>"
    elif is_archive:
        title = "📦 <b>АРХИВНЫЕ РЕЗЮМЕ:</b>"
    else:
        title = "📑 <b>АКТИВНЫЕ РЕЗЮМЕ:</b>"

    if not candidates:
        builder = InlineKeyboardBuilder()
        builder.button(text="⬅️ Назад", callback_data="admin_stats")
        await callback.message.edit_text(
            f"{title}\n\n<i>Список пуст.</i>", reply_markup=builder.as_markup(), parse_mode="HTML"
        )
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    text = f"{title}\n━━━━━━━━━━━━━━━━━━━━━\n"
    for cand in candidates:
        t_id, name, vac, status, _, plat = cand
        icon = (
            "🆕" if status == "Новая"
            else ("🟡" if status == "В работе"
            else ("🟢" if "Приглашен" in status
            else "🔴"))
        )
        text += f"{icon} <b>#{t_id} [{plat.upper()}]</b> | {html.escape(name)}\n└ <i>{html.escape(vac)}</i> (<b>{status}</b>)\n\n"
        builder.button(text=f"Открыть #{t_id}", callback_data=f"view_{t_id}")

    builder.button(text="⬅️ Назад в меню", callback_data="admin_stats")
    builder.adjust(2)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@hr_router.callback_query(F.data.startswith("view_"))
async def cb_view_ticket(callback: types.CallbackQuery) -> None:
    """Детальный просмотр карточки соискателя (16 шагов) со служебными кнопками."""
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
    try:
        await callback.message.edit_text(card, reply_markup=make_ticket_keyboard(ticket_id), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@hr_router.callback_query(F.data.startswith("cand_call_"))
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


# ==============================================================================
# 2. ЗАМЕТКИ КАДРОВИКА К АНКЕТАМ (/note)
# ==============================================================================

@hr_router.callback_query(F.data.startswith("cand_note_") | F.data.startswith("note_"))
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


@hr_router.message(CandidateNoteForm.waiting_note)
async def process_cand_note(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    cand = db.get_candidate(ticket_id)
    if not cand:
        await state.clear()
        await safe_answer(message, "⚠️ Анкета не найдена.")
        return

    text = (message.text or "").strip()
    builder = InlineKeyboardBuilder()
    builder.button(text=f"📑 Открыть анкету #{ticket_id}", callback_data=f"view_{ticket_id}")

    if text == "-":
        db.update_admin_note(ticket_id, "")
        await state.clear()
        await safe_answer(message, f"🗑 Заметка к анкете #{ticket_id} удалена.", reply_markup=builder.as_markup())
    else:
        db.update_admin_note(ticket_id, text)
        await state.clear()
        await safe_answer(
            message,
            f"✅ Заметка к анкете #{ticket_id} сохранена:\n«<i>{html.escape(text)}</i>»",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )


@hr_router.message(Command("note", "admin_note"))
async def cmd_set_note(message: types.Message) -> None:
    allowed, err_text = check_hr_access_or_block(message.from_user.id, message.chat.id)
    if not allowed:
        await safe_answer(message, err_text or "🚫 Доступ ограничен.", parse_mode="HTML")
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await safe_answer(
            message,
            "ℹ️ Использование: <code>/note <номер_анкеты> <текст заметки></code>\n"
            "Пример: <code>/note 12 Созвонились, ждём в четверг</code>",
            parse_mode="HTML",
        )
        return

    t_id = int(parts[1])
    note_text = parts[2].strip()
    db.update_admin_note(t_id, note_text)

    builder = InlineKeyboardBuilder()
    builder.button(text=f"📑 Открыть анкету #{t_id}", callback_data=f"view_{t_id}")
    await safe_answer(
        message,
        f"✅ Заметка к анкете #{t_id} обновлена:\n«<i>{html.escape(note_text)}</i>»",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


# ==============================================================================
# 3. ОТПРАВКА СООБЩЕНИЙ СОИСКАТЕЛЮ ЧЕРЕЗ БОТА
# ==============================================================================

@hr_router.callback_query(F.data.startswith("cand_msg_"))
async def cb_cand_direct_msg(callback: types.CallbackQuery, state: FSMContext) -> None:
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
    await state.update_data(ticket_id=ticket_id)
    await callback.message.reply(
        f"💬 <b>Написать кандидату {html.escape(cand[3])} (Анкета #{ticket_id}):</b>\n\n"
        "Введите текст сообщения. Бот официально перешлет его соискателю в Telegram/VK/МАКС.\n\n"
        "<i>(Ваш личный контакт останется скрыт).</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@hr_router.message(CandidateDirectMsgForm.waiting_text)
async def process_candidate_direct_msg(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    cand = db.get_candidate(ticket_id)
    if not cand:
        await state.clear()
        await safe_answer(message, "⚠️ Анкета не найдена.")
        return

    reply_text = (message.text or "").strip()
    if not reply_text:
        await safe_answer(message, "⚠️ Введите текст сообщения.")
        return

    await state.clear()
    platform, user_id, full_name = cand[1], cand[2], cand[3]

    user_msg = texts.format_hr_direct_reply(reply_text, ticket_id)

    if platform == "vk":
        from gateways.vk_gateway import make_vk_reply_keyboard  # type: ignore
        ok = await send_response_to_candidate(
            platform, user_id, user_msg, keyboard=make_vk_reply_keyboard(ticket_id)
        )
    else:
        ok = await send_response_to_candidate(
            platform, user_id, user_msg, keyboard=make_cand_reply_keyboard(ticket_id)
        )

    if ok:
        await safe_answer(
            message,
            f"✅ Сообщение успешно отправлено кандидату <b>{html.escape(full_name)}</b>!",
            parse_mode="HTML",
        )
    else:
        await safe_answer(message, f"⚠️ Не удалось доставить сообщение кандидату в {platform}.")


# ==============================================================================
# 4. ИЗМЕНЕНИЕ СТАТУСОВ АНКЕТ И ПРИГЛАШЕНИЯ
# ==============================================================================

@hr_router.callback_query(F.data.startswith("status_"))
async def cb_change_status(callback: types.CallbackQuery) -> None:
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
            await callback.message.edit_text(
                updated_card, reply_markup=make_ticket_keyboard(ticket_id), parse_mode="HTML"
            )
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=make_ticket_keyboard(ticket_id))
        except Exception:
            pass


@hr_router.callback_query(F.data.startswith("invite_custom_"))
async def cb_invite_custom(callback: types.CallbackQuery, state: FSMContext) -> None:
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


@hr_router.message(CustomInviteForm.waiting_datetime)
async def process_custom_invite_text(message: types.Message, state: FSMContext) -> None:
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
    db.update_status(ticket_id, "Приглашен (с датой)")

    user_msg = texts.format_hr_invite_custom(full_name, dt_text)

    if platform == "vk":
        from gateways.vk_gateway import make_vk_reply_keyboard  # type: ignore
        ok = await send_response_to_candidate(
            platform, user_id, user_msg, keyboard=make_vk_reply_keyboard(ticket_id)
        )
    else:
        ok = await send_response_to_candidate(
            platform, user_id, user_msg, keyboard=make_cand_reply_keyboard(ticket_id)
        )

    updated_cand = db.get_candidate(ticket_id)
    updated_card = texts.format_hr_card_full(updated_cand) if updated_cand else ""

    status_note = "✅ Приглашение успешно отправлено соискателю!" if ok else "⚠️ Не удалось доставить сообщение кандидату."
    await safe_answer(
        message,
        f"{status_note}\n\n{updated_card}",
        reply_markup=make_ticket_keyboard(ticket_id),
        parse_mode="HTML",
    )


# ==============================================================================
# 5. УДАЛЕНИЕ АНКЕТЫ И ПЕРСОНАЛЬНЫХ ДАННЫХ (152-ФЗ РФ)
# ==============================================================================

@hr_router.callback_query(F.data.startswith("del_ask_"))
async def cb_delete_ticket_ask(callback: types.CallbackQuery) -> None:
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.message.edit_text(err_text or "🚫 Доступ ограничен.", parse_mode="HTML")
        return

    ticket_id = int(callback.data.replace("del_ask_", ""))
    cand = db.get_candidate(ticket_id)
    if not cand:
        await callback.answer("Анкета не найдена или уже удалена!", show_alert=True)
        return

    full_name = cand[3]
    vac = cand[5]

    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Да, удалить навсегда", callback_data=f"del_confirm_{ticket_id}")
    builder.button(text="❌ Отмена (сохранить)", callback_data=f"view_{ticket_id}")
    builder.adjust(1, 1)

    warn_text = (
        f"⚠️ <b>ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ (ст. 21 152-ФЗ)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Вы действительно хотите <b>безвозвратно удалить</b> анкету <b>#{ticket_id}</b>?\n\n"
        f"👤 <b>Кандидат:</b> {html.escape(full_name)}\n"
        f"🎯 <b>Должность:</b> {html.escape(vac)}\n\n"
        f"<i>Все данные будут стёрты из базы. Перед удалением автоматически создаётся резервная копия.</i>"
    )
    try:
        await callback.message.edit_text(warn_text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@hr_router.callback_query(F.data.startswith("del_confirm_"))
async def cb_delete_ticket(callback: types.CallbackQuery) -> None:
    ticket_id = int(callback.data.replace("del_confirm_", ""))
    cand = db.get_candidate(ticket_id)
    if not cand:
        builder = InlineKeyboardBuilder()
        builder.button(text="⬅️ К списку резюме", callback_data="admin_list_all")
        try:
            await callback.message.edit_text(
                f"🗑 <b>Анкета #{ticket_id} уже удалена из базы данных.</b>",
                reply_markup=builder.as_markup(),
                parse_mode="HTML",
            )
        except Exception:
            pass
        await callback.answer("Анкета уже удалена!", show_alert=True)
        return

    cand_info = {
        "ticket_id": cand[0],
        "platform": cand[1],
        "user_id": str(cand[2]),
        "full_name": cand[3],
        "phone": cand[4],
        "vacancy": cand[5],
    }

    try:
        db.backup_database()
    except Exception as e:
        logger.warning("Не удалось создать автобэкап перед удалением: %s", e)

    # Фиксация факта уничтожения в журнале РКН № 179 и физическое удаление
    db.log_pdn_destruction(
        candidate_id=ticket_id,
        user_id=cand_info["user_id"],
        platform=cand_info["platform"],
        reason="Уничтожение кадровой службой по ст. 21 152-ФЗ",
        act_number=f"{ticket_id}-УПД",
    )
    db.delete_candidate(ticket_id)

    destroy_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    del_msg = texts.format_candidate_deleted_notification(
        ticket_id=ticket_id,
        full_name=cand_info["full_name"],
        vacancy=cand_info["vacancy"],
        destroy_time=destroy_time,
    )

    notif_delivered = await send_response_to_candidate(
        cand_info["platform"], cand_info["user_id"], del_msg, keyboard=make_candidate_main_keyboard()
    )

    op_name = callback.from_user.full_name or f"ID {callback.from_user.id}"
    status_label = (
        "✅ Соискатель успешно уведомлен в ЛС"
        if notif_delivered
        else "⚠️ Соискатель не получил уведомление (диалог не начат)"
    )

    audit_card = texts.format_candidate_deleted_hr_audit(
        ticket_id=ticket_id,
        full_name=cand_info["full_name"],
        vacancy=cand_info["vacancy"],
        destroy_time=destroy_time,
        operator_name=op_name,
        notification_status=status_label,
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ К списку резюме", callback_data="admin_list_all")
    try:
        await callback.message.edit_text(audit_card, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("Анкета успешно удалена!")


# ==============================================================================
# 6. ЧЕРНЫЙ СПИСОК И БЛОКИРОВКИ (/ban, /unban, /blacklist)
# ==============================================================================

@hr_router.callback_query(F.data.startswith("block_cand_") | F.data.startswith("block_"))
async def cb_block_candidate(callback: types.CallbackQuery) -> None:
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer("🚫 Доступ ограничен сотрудниками отдела кадров.", show_alert=True)
        return

    data_parts = callback.data.split("_")
    ticket_id = int(data_parts[-1])
    cand = db.get_candidate(ticket_id)
    if not cand:
        await callback.answer("Анкета не найдена!", show_alert=True)
        return

    platform = cand[1]
    cand_uid = str(cand[2])
    full_name = cand[3]
    super_uid = str(CONFIG.get("SUPER_ADMIN_ID", 0))

    if cand_uid == super_uid or (
        cand_uid.isdigit() and (is_tech_admin(int(cand_uid)) or is_hr_admin(int(cand_uid)))
    ):
        await callback.answer("🚫 Нельзя добавить администратора в черный список!", show_alert=True)
        return

    reason = "Блокировка кадровой службой"
    db.block_user_and_clean(cand_uid, reason=reason)

    ban_user_msg = texts.format_blacklist_notification(full_name, reason)
    sent_to_cand = await send_response_to_candidate(platform, cand_uid, ban_user_msg)

    alert_text = (
        "⛔ Пользователь добавлен в ЧС! Уведомление отправлено."
        if sent_to_cand
        else "⛔ Добавлен в ЧС (не удалось отправить ЛС)."
    )
    await callback.answer(alert_text, show_alert=True)

    try:
        updated_cand = db.get_candidate(ticket_id)
        if updated_cand:
            updated_card = texts.format_hr_card_full(updated_cand)
            await callback.message.edit_text(
                updated_card, reply_markup=make_ticket_keyboard(ticket_id), parse_mode="HTML"
            )
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=make_ticket_keyboard(ticket_id))
        except Exception:
            pass


@hr_router.callback_query(F.data.startswith("unblock_cand_"))
async def cb_unblock_candidate(callback: types.CallbackQuery) -> None:
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer("🚫 Доступ ограничен кадровой службой!", show_alert=True)
        return

    ticket_id = int(callback.data.split("_")[2])
    cand = db.get_candidate(ticket_id)
    if not cand:
        await callback.answer("Анкета не найдена!", show_alert=True)
        return

    platform = cand[1]
    cand_uid = str(cand[2])
    full_name = cand[3]
    db.unblock_user(cand_uid)

    unban_user_msg = texts.format_unblock_notification(full_name)
    sent = await send_response_to_candidate(
        platform, cand_uid, unban_user_msg, keyboard=make_candidate_main_keyboard()
    )
    alert = "✅ Блокировка снята! Соискателю отправлено уведомление." if sent else "✅ Блокировка снята!"
    await callback.answer(alert, show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=make_ticket_keyboard(ticket_id))
    except Exception:
        pass


@hr_router.message(Command("block", "ban"))
async def cmd_block_user(message: types.Message) -> None:
    user_id = message.from_user.id
    if not is_tech_admin(user_id) and not is_hr_admin(user_id):
        await safe_answer(message, "🚫 Доступ ограничен.")
        return

    parts = message.text.strip().split(maxsplit=2)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await safe_answer(
            message,
            "ℹ️ <b>Формат команды:</b> <code>/ban ID_пользователя [причина]</code>",
            parse_mode="HTML",
        )
        return

    target_id = parts[1]
    reason = parts[2] if len(parts) > 2 else "Блокировка администратором"
    super_uid = str(CONFIG.get("SUPER_ADMIN_ID", 0))

    if target_id == super_uid or (
        target_id.isdigit() and (is_tech_admin(int(target_id)) or is_hr_admin(int(target_id)))
    ):
        await safe_answer(message, "🚫 Нельзя добавить администратора в черный список!", parse_mode="HTML")
        return

    db.block_user_and_clean(target_id, reason=reason)
    ban_user_msg = texts.format_blacklist_notification(f"ID {target_id}", reason)
    cand_sent = await send_response_to_candidate("tg", target_id, ban_user_msg)
    cand_note = (
        "✅ Соискатель получил уведомление в ЛС."
        if cand_sent
        else "⚠️ Соискатель не получил уведомление (бот заблокирован)."
    )

    await safe_answer(
        message,
        f"⛔ <b>Пользователь <code>{target_id}</code> заблокирован!</b>\n\n"
        f"📋 <b>Причина:</b> <i>{html.escape(reason)}</i>\n"
        f"• Активные анкеты аннулированы.\n"
        f"• {cand_note}",
        parse_mode="HTML",
    )


@hr_router.message(Command("unblock", "unban"))
async def cmd_unblock_user(message: types.Message) -> None:
    user_id = message.from_user.id
    if not is_tech_admin(user_id) and not is_hr_admin(user_id):
        await safe_answer(message, "🚫 Доступ ограничен.")
        return

    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await safe_answer(message, "ℹ️ <b>Формат команды:</b> <code>/unblock ID_пользователя</code>", parse_mode="HTML")
        return

    target_id = parts[1]
    ok = db.unblock_user(target_id)
    if ok:
        unban_user_msg = texts.format_unblock_notification(f"ID {target_id}")
        await send_response_to_candidate("tg", target_id, unban_user_msg, keyboard=make_candidate_main_keyboard())
        await safe_answer(message, f"✅ <b>Пользователь <code>{target_id}</code> успешно разблокирован!</b>", parse_mode="HTML")
    else:
        await safe_answer(message, f"ℹ️ Пользователь <code>{target_id}</code> не найден в черном списке.", parse_mode="HTML")


@hr_router.message(Command("blacklist", "banlist"))
async def cmd_blacklist(message: types.Message) -> None:
    user_id = message.from_user.id
    if not is_tech_admin(user_id) and not is_hr_admin(user_id):
        await safe_answer(message, "🚫 Доступ ограничен.")
        return

    b_list = db.get_blacklist()
    if not b_list:
        await safe_answer(message, "🕊 <b>Черный список пуст.</b> Заблокированных пользователей нет.", parse_mode="HTML")
        return

    text = "⛔ <b>ЧЕРНЫЙ СПИСОК (БЛОКИРОВКИ):</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
    builder = InlineKeyboardBuilder()
    for uid, reason, b_date in b_list[:15]:
        text += f"• <code>{uid}</code> | <i>{html.escape(reason)}</i> ({b_date})\n"
        builder.button(text=f"✅ Разблокировать {uid}", callback_data=f"unblock_raw_{uid}")

    builder.adjust(1)
    await safe_answer(message, text, reply_markup=builder.as_markup(), parse_mode="HTML")


@hr_router.callback_query(F.data.startswith("unblock_raw_"))
async def cb_unblock_raw(callback: types.CallbackQuery) -> None:
    target_uid = callback.data.replace("unblock_raw_", "")
    db.unblock_user(target_uid)
    await callback.answer(f"Пользователь {target_uid} разблокирован!", show_alert=True)
    b_list = db.get_blacklist()
    if not b_list:
        await callback.message.edit_text("🕊 <b>Черный список пуст.</b> Все пользователи разблокированы.", parse_mode="HTML")
        return

    builder = InlineKeyboardBuilder()
    text = "⛔ <b>ЧЕРНЫЙ СПИСОК (БЛОКИРОВКИ):</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
    for uid, reason, b_date in b_list[:15]:
        text += f"• <code>{uid}</code> | <i>{html.escape(reason)}</i> ({b_date})\n"
        builder.button(text=f"✅ Разблокировать {uid}", callback_data=f"unblock_raw_{uid}")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# ==============================================================================
# 7. ПРЯМОЙ ДИАЛОГ (LIVE-CHAT): КАДРОВИК <-> СОИСКАТЕЛЬ
# ==============================================================================

@hr_router.callback_query(F.data.startswith("live_dlg_cand_"))
async def cb_live_dlg_cand(callback: types.CallbackQuery, bot: Bot) -> None:
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer("🚫 Доступ к прямому диалогу разрешён только сотрудникам отдела кадров!", show_alert=True)
        return

    ticket_id = int(callback.data.split("_")[3])
    cand = db.get_candidate(ticket_id)
    if not cand:
        await callback.answer("Анкета не найдена!", show_alert=True)
        return

    target_chat_id = callback.message.chat.id
    platform = cand[1] or "tg"
    user_id = str(cand[2])
    full_name = cand[3] or "Соискатель"

    db.start_direct_dialog(
        user_id=user_id,
        operator_id=target_chat_id,
        ticket_id=ticket_id,
        full_name=full_name,
        platform=platform,
    )

    hr_kb = InlineKeyboardBuilder()
    hr_kb.button(text="⏹ Завершить прямой диалог", callback_data=f"end_live_dlg_{user_id}")

    cand_kb = InlineKeyboardBuilder()
    cand_kb.button(text="⏹ Завершить диалог", callback_data=f"end_live_dlg_{user_id}")

    await callback.message.reply(
        f"🟢 <b>ПРЯМОЙ ДИАЛОГ НАЧАТ!</b>\n\n"
        f"Вы подключены к прямому чату с соискателем <b>{html.escape(full_name)}</b> (ID: <code>{user_id}</code>, {platform.upper()}).\n"
        f"Все ваши текстовые сообщения теперь будут автоматически пересылаться кандидату.\n\n"
        f"<i>Чтобы закрыть чат, нажмите кнопку ниже:</i>",
        reply_markup=hr_kb.as_markup(),
        parse_mode="HTML",
    )

    cand_notify = (
        "🟢 <b>Специалист отдела кадров МУП «Ульяновскэлектротранс» подключился к прямому диалогу!</b>\n\n"
        "Вы можете общаться со специалистом напрямую в этом чате. Все ваши сообщения видит кадровик.\n\n"
        "<i>Для завершения диалога напишите: /stop</i>"
    )
    if platform == "vk":
        from gateways.vk_gateway import make_vk_dialog_keyboard  # type: ignore
        await send_response_to_candidate(platform, user_id, cand_notify, keyboard=make_vk_dialog_keyboard())
    else:
        await send_response_to_candidate(platform, user_id, cand_notify, keyboard=cand_kb.as_markup())

    await callback.answer("Прямой диалог открыт!")


@hr_router.callback_query(F.data.startswith("live_dlg_inq_"))
async def cb_live_dlg_inq(callback: types.CallbackQuery, bot: Bot) -> None:
    inquiry_id = int(callback.data.split("_")[3])
    inq = db.get_inquiry(inquiry_id)
    if not inq:
        await callback.answer("Обращение не найдено!", show_alert=True)
        return

    target_chat_id = callback.message.chat.id
    platform = inq[2] or "tg"
    user_id = str(inq[3])
    full_name = inq[4] or "Соискатель"

    db.start_direct_dialog(
        user_id=user_id,
        operator_id=target_chat_id,
        ticket_id=inq[1],
        full_name=full_name,
        platform=platform,
    )

    hr_kb = InlineKeyboardBuilder()
    hr_kb.button(text="⏹ Завершить прямой диалог", callback_data=f"end_live_dlg_{user_id}")

    cand_kb = InlineKeyboardBuilder()
    cand_kb.button(text="⏹ Завершить диалог", callback_data=f"end_live_dlg_{user_id}")

    await callback.message.reply(
        f"🟢 <b>ПРЯМОЙ ДИАЛОГ НАЧАТ!</b>\n\n"
        f"Вы подключены к кандидату <b>{html.escape(full_name)}</b> (ID: <code>{user_id}</code>, {platform.upper()}).\n"
        f"Пишите сообщения — они будут моментально уходить соискателю.",
        reply_markup=hr_kb.as_markup(),
        parse_mode="HTML",
    )

    cand_notify = (
        "🟢 <b>Специалист отдела кадров подключился к прямому диалогу по вашему вопросу!</b>\n\n"
        "Вы можете задавать вопросы и общаться напрямую в этом чате.\n\n"
        "<i>Для завершения диалога напишите: /stop</i>"
    )
    if platform == "vk":
        from gateways.vk_gateway import make_vk_dialog_keyboard  # type: ignore
        await send_response_to_candidate(platform, user_id, cand_notify, keyboard=make_vk_dialog_keyboard())
    else:
        await send_response_to_candidate(platform, user_id, cand_notify, keyboard=cand_kb.as_markup())

    await callback.answer("Прямой диалог открыт!")


@hr_router.message(Command("stop"))
async def cmd_operator_stop_dialog(message: types.Message, bot: Bot) -> None:
    op_id = message.chat.id
    dlg = db.get_dialog_by_operator(op_id) or db.get_dialog_by_operator(message.from_user.id)
    if not dlg:
        await safe_answer(message, "ℹ️ В этом чате нет активного прямого диалога.")
        return

    cand_user_id = str(dlg[0])
    platform = dlg[4] if len(dlg) > 4 and dlg[4] else "tg"
    name = dlg[3] or "Соискатель"

    db.end_direct_dialog(user_id=cand_user_id)
    db.end_direct_dialog(operator_id=op_id)

    cand_end_text = texts.LIVE_CHAT_ENDED
    if platform == "vk":
        from gateways.vk_gateway import make_vk_main_keyboard  # type: ignore
        await send_response_to_candidate(platform, cand_user_id, cand_end_text, keyboard=make_vk_main_keyboard())
    else:
        await send_response_to_candidate(
            platform, cand_user_id, cand_end_text, keyboard=make_candidate_main_keyboard()
        )

    await safe_answer(
        message,
        f"⏹ <b>Прямой диалог с кандидатом {html.escape(name)} (ID: <code>{cand_user_id}</code>, {platform.upper()}) завершён.</b>",
        parse_mode="HTML",
    )


@hr_router.callback_query(F.data.startswith("end_live_dlg_"))
async def cb_end_live_dlg(callback: types.CallbackQuery, bot: Bot) -> None:
    user_id = callback.data.replace("end_live_dlg_", "")
    dlg = db.get_dialog_by_user(user_id)
    if not dlg:
        await callback.answer("Диалог уже завершен.", show_alert=True)
        return

    db.end_direct_dialog(user_id=user_id)

    ticket_id = dlg[2]
    platform = dlg[4] if len(dlg) > 4 and dlg[4] else "tg"
    if platform == "tg" and ticket_id:
        cand = db.get_candidate(ticket_id)
        if cand and cand[1]:
            platform = cand[1]

    cand_end_text = texts.LIVE_CHAT_ENDED
    if platform == "vk":
        from gateways.vk_gateway import make_vk_main_keyboard  # type: ignore
        await send_response_to_candidate(platform, user_id, cand_end_text, keyboard=make_vk_main_keyboard())
    else:
        await send_response_to_candidate(platform, user_id, cand_end_text)

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


@hr_router.message(is_operator_in_dialog_filter, F.text & ~F.text.startswith("/"))
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


@hr_router.message(StateFilter(None), F.photo)
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
    caption = message.caption or ""

    try:
        photo = message.photo[-1]
        file_obj = io.BytesIO()
        await bot.download(photo.file_id, destination=file_obj)
        file_bytes = file_obj.getvalue()

        ok = await send_photo_to_candidate(cand_user_id, file_bytes, caption=caption)
        if ok:
            await safe_answer(message, "✅ <i>Фотография успешно доставлена соискателю</i>", parse_mode="HTML")
        else:
            await safe_answer(message, "⚠️ Не удалось доставить фото соискателю.")
    except Exception as e:
        logger.error("Ошибка пересылки фото от кадровика: %s", e)
        await safe_answer(message, f"❌ Ошибка пересылки фото: {e}")


# ==============================================================================
# 8. ОБРАЩЕНИЯ СОИСКАТЕЛЕЙ ПО ВОПРОСАМ (/ask)
# ==============================================================================

@hr_router.callback_query(F.data.startswith("inq_call_"))
async def cb_inq_call(callback: types.CallbackQuery) -> None:
    inquiry_id = int(callback.data.split("_")[2])
    inq = db.get_inquiry(inquiry_id)
    if not inq:
        await callback.answer("Обращение не найдено.", show_alert=True)
        return
    phone = inq[5] or "Не указан"
    await callback.answer(f"📞 Телефон соискателя: {phone}", show_alert=True)


@hr_router.callback_query(F.data.startswith("inq_close_"))
async def cb_inq_close(callback: types.CallbackQuery) -> None:
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer("🚫 Доступ ограничен кадровой службой!", show_alert=True)
        return

    inquiry_id = int(callback.data.split("_")[2])
    inq = db.get_inquiry(inquiry_id)
    if not inq:
        await callback.answer("Обращение не найдено.", show_alert=True)
        return

    platform, user_id = inq[2], inq[3]
    db.close_inquiry(inquiry_id)

    close_user_msg = (
        f"⏹ <b>Диалог по обращению #{inquiry_id} завершён</b>\n\n"
        "Специалист отдела кадров <b>МУП «Ульяновскэлектротранс»</b> закрыл обращение по вашему вопросу. "
        "Спасибо за обращение!"
    )
    await send_response_to_candidate(platform, user_id, close_user_msg)
    await callback.answer("Обращение закрыто.")
    try:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n━━━━━━━━━━━━━━━━━━━━━\n⏹ <b>Обращение #{inquiry_id} закрыто специалистом.</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass


@hr_router.callback_query(F.data.startswith("inq_reply_"))
async def cb_inq_reply(callback: types.CallbackQuery, state: FSMContext) -> None:
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer("🚫 Доступ ограничен кадровой службой!", show_alert=True)
        return

    inquiry_id = int(callback.data.split("_")[2])
    inq = db.get_inquiry(inquiry_id)
    if not inq:
        await callback.answer("Обращение не найдено.", show_alert=True)
        return

    await state.set_state(HRReplyForm.waiting_reply)
    await state.update_data(inquiry_id=inquiry_id)
    await callback.message.reply(
        f"✍️ <b>Введите текст ответа</b> для соискателя <b>{html.escape(inq[4])}</b> (обращение #{inquiry_id}):",
        parse_mode="HTML",
    )
    await callback.answer()


@hr_router.message(HRReplyForm.waiting_reply)
async def process_hr_reply(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    inquiry_id = data.get("inquiry_id")
    inq = db.get_inquiry(inquiry_id)
    if not inq:
        await state.clear()
        await safe_answer(message, "⚠️ Обращение не найдено в базе данных.")
        return

    reply_text = (message.text or "").strip()
    if not reply_text:
        await safe_answer(message, "⚠️ Ответ должен быть текстовым сообщением.")
        return

    db.reply_inquiry(inquiry_id, reply_text)
    await state.clear()

    platform, user_id, full_name = inq[2], inq[3], inq[4]
    user_msg = texts.format_hr_inquiry_reply(inquiry_id, reply_text)

    if platform == "vk":
        from gateways.vk_gateway import make_vk_reply_keyboard  # type: ignore
        ok = await send_response_to_candidate(
            platform, user_id, user_msg, keyboard=make_vk_reply_keyboard(inq[1] or 0)
        )
    else:
        ok = await send_response_to_candidate(
            platform, user_id, user_msg, keyboard=make_cand_reply_keyboard(inq[1] or 0)
        )

    if ok:
        await safe_answer(
            message,
            f"✅ Ответ успешно доставлен соискателю <b>{html.escape(full_name)}</b>!",
            parse_mode="HTML",
        )
    else:
        await safe_answer(message, f"⚠️ Не удалось доставить сообщение в платформу {platform}.")


# ==============================================================================
# 9. НАСТРОЙКА УВЕДОМЛЕНИЙ И ЭКСПОРТ (/export)
# ==============================================================================

@hr_router.callback_query(F.data == "toggle_dm_notify")
async def cb_toggle_dm_notify(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    new_state = db.toggle_admin_notify(user_id)
    status_str = "ВКЛЮЧЕНЫ 🔔" if new_state else "ВЫКЛЮЧЕНЫ 🔕"
    await callback.answer(f"Уведомления в ЛС: {status_str}")
    try:
        await callback.message.edit_reply_markup(reply_markup=make_admin_menu_keyboard(user_id))
    except Exception:
        pass


@hr_router.callback_query(F.data == "hr_toggle_cooldown")
async def cb_hr_toggle_cooldown(callback: types.CallbackQuery) -> None:
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        await callback.answer(err_text or "🚫 Нет прав!", show_alert=True)
        return

    cur_cd = int(db.get_setting("cooldown_seconds", str(CONFIG.get("COOLDOWN_SECONDS", 1200))))
    if cur_cd >= 1200:
        new_cd = 300
    elif cur_cd >= 300:
        new_cd = 60
    elif cur_cd >= 60:
        new_cd = 0
    else:
        new_cd = 1200

    db.set_setting("cooldown_seconds", str(new_cd))
    CONFIG["COOLDOWN_SECONDS"] = new_cd
    cd_label = f"{new_cd // 60} мин" if new_cd > 0 else "0 сек (без задержки)"
    await callback.answer(f"Таймаут вопросов: {cd_label}", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=make_admin_menu_keyboard(callback.from_user.id))
    except Exception:
        pass


@hr_router.message(Command("export"))
@hr_router.callback_query(F.data == "hr_export_excel")
async def process_export_candidates(event: Union[types.Message, types.CallbackQuery]) -> None:
    user_id = event.from_user.id
    if not is_hr_admin(user_id) and not is_tech_admin(user_id) and user_id != CONFIG.get("SUPER_ADMIN_ID"):
        if isinstance(event, types.CallbackQuery):
            await event.answer("🚫 Нет прав!", show_alert=True)
            return
        await safe_answer(event, "🚫 Доступ ограничен.")
        return

    if isinstance(event, types.CallbackQuery):
        await event.answer("⏳ Формирую файл...")

    candidates = db.get_all_candidates_for_export()
    if not candidates:
        msg = "ℹ️ В базе пока нет анкет для выгрузки."
        if isinstance(event, types.CallbackQuery):
            await event.message.answer(msg)
            return
        await safe_answer(event, msg)
        return

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "ID", "Дата подачи", "ФИО", "Телефон", "Вакансия",
        "Опыт работы", "Статус", "Заметка HR", "Платформа", "ID пользователя",
    ])

    for c in candidates:
        row = [
            str(c[0]),
            str(c[9] if len(c) > 9 and c[9] else ""),
            str(c[3] if len(c) > 3 and c[3] else ""),
            str(c[4] if len(c) > 4 and c[4] else ""),
            str(c[5] if len(c) > 5 and c[5] else ""),
            str(c[6] if len(c) > 6 and c[6] else ""),
            str(c[7] if len(c) > 7 and c[7] else ""),
            str(c[8] if len(c) > 8 and c[8] else ""),
            str(c[1] if len(c) > 1 and c[1] else "").upper(),
            str(c[2] if len(c) > 2 and c[2] else ""),
        ]
        writer.writerow(row)

    file_bytes = output.getvalue().encode("utf-8-sig")
    filename = f"candidates_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    doc = BufferedInputFile(file_bytes, filename=filename)

    target = event.message if isinstance(event, types.CallbackQuery) else event
    await target.answer_document(
        document=doc,
        caption=f"📊 <b>Выгрузка базы соискателей</b> (Записей: {len(candidates)})",
        parse_mode="HTML",
    )


# ==============================================================================
# 10. АВТО-УПРАВЛЕНИЕ РОЛЯМИ В КАДРОВОМ ЧАТЕ (ВХОД, ВЫХОД, СИНХРОНИЗАЦИЯ)
# ==============================================================================

async def grant_hr_role_and_welcome(user: types.User, chat_id: int, bot: Bot) -> None:
    """Выдаёт роль HR в базе и отправляет официальное приветствие."""
    if user.is_bot or user.id == CONFIG.get("SUPER_ADMIN_ID"):
        return

    db.unblock_user(user.id)
    db.add_admin(user.id, role="hr")

    name_escaped = html.escape(user.full_name or "Сотрудник")
    user_tag = f"@{user.username}" if user.username else name_escaped

    welcome_msg = (
        f"👋 <b>Добро пожаловать в кадровую службу, {user_tag}!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Вам <b>автоматически открыт доступ</b> к кадровой панели: <code>/hr</code>.\n\n"
        f"💡 <i>Чтобы бот мог пересылать вам анкеты соискателей в личные сообщения, "
        f"откройте диалог с ботом и нажмите <b>/start</b>.</i>"
    )
    await safe_send(bot, chat_id, welcome_msg)


@hr_router.message(F.new_chat_members)
async def on_hr_user_added(message: types.Message, bot: Bot) -> None:
    hr_group = CONFIG.get("HR_GROUP_ID")
    if hr_group and message.chat.id == hr_group:
        for new_user in message.new_chat_members:
            await grant_hr_role_and_welcome(new_user, message.chat.id, bot)


@hr_router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_hr_user_joined(event: types.ChatMemberUpdated, bot: Bot) -> None:
    hr_group = CONFIG.get("HR_GROUP_ID")
    if hr_group and event.chat.id == hr_group:
        await grant_hr_role_and_welcome(event.new_chat_member.user, hr_group, bot)


@hr_router.message(F.chat.type.in_({"group", "supergroup"}))
async def on_existing_hr_member_message(message: types.Message, bot: Bot) -> None:
    hr_group = CONFIG.get("HR_GROUP_ID")
    if not hr_group or message.chat.id != hr_group or message.from_user.is_bot:
        return

    user_id = message.from_user.id
    if user_id != CONFIG.get("SUPER_ADMIN_ID"):
        if db.get_admin_role(user_id) != "hr":
            await grant_hr_role_and_welcome(message.from_user, hr_group, bot)


@hr_router.chat_member(ChatMemberUpdatedFilter(member_status_changed=LEAVE_TRANSITION))
async def on_hr_member_left(event: types.ChatMemberUpdated, bot: Bot) -> None:
    hr_group = CONFIG.get("HR_GROUP_ID")
    super_admin = CONFIG.get("SUPER_ADMIN_ID")
    if not hr_group or event.chat.id != hr_group:
        return

    user = event.old_chat_member.user
    if user.is_bot or user.id == super_admin:
        return

    db.remove_admin(user.id)
    name_escaped = html.escape(user.full_name or "Сотрудник")
    user_tag = f"@{user.username} ({name_escaped})" if user.username else f"{name_escaped} [ID: <code>{user.id}</code>]"
    actor = event.from_user
    action_text = "исключён из чата" if (actor and actor.id != user.id) else "покинул чат"

    await safe_send(
        bot,
        hr_group,
        f"⛔ <b>Сотрудник {action_text}:</b> {user_tag}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔒 <b>Роль HR аннулирована в базе данных.</b> Доступ к <code>/hr</code> закрыт.",
    )


@hr_router.message(Command("kick", "kick_hr"))
async def cmd_kick_hr_reply(message: types.Message, bot: Bot) -> None:
    hr_group = CONFIG.get("HR_GROUP_ID")
    super_admin = CONFIG.get("SUPER_ADMIN_ID")
    user_id = message.from_user.id

    if user_id != super_admin and not is_tech_admin(user_id):
        try:
            m = await bot.get_chat_member(message.chat.id, user_id)
            if not isinstance(m, (ChatMemberAdministrator, ChatMemberOwner)):
                await safe_answer(message, "🚫 Исключать сотрудников могут только администраторы чата.")
                return
        except Exception:
            await safe_answer(message, "🚫 Недостаточно прав.")
            return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await safe_answer(
            message,
            "ℹ️ Ответьте командой <code>/kick</code> на сообщение сотрудника в группе.",
            parse_mode="HTML",
        )
        return

    target = message.reply_to_message.from_user
    if target.is_bot or target.id == super_admin:
        await safe_answer(message, "🚫 Нельзя исключить данного пользователя.")
        return

    db.remove_admin(target.id)
    try:
        await bot.ban_chat_member(message.chat.id, target.id)
        await bot.unban_chat_member(message.chat.id, target.id)
    except Exception:
        pass

    target_tag = f"@{target.username}" if target.username else html.escape(target.full_name)
    await safe_answer(
        message,
        f"✅ <b>Сотрудник {target_tag} исключён из кадровой группы.</b>\n"
        f"Роль HR удалена из базы, доступ к панели <code>/hr</code> аннулирован.",
        parse_mode="HTML",
    )


@hr_router.message(Command("set_group"))
async def cmd_set_group(message: types.Message, bot: Bot) -> None:
    """Привязка официальной кадровой группы предприятия с атомарной записью в .env."""
    user_id = message.from_user.id
    super_id = CONFIG.get("SUPER_ADMIN_ID")
    is_admin_user = (user_id == super_id) or is_tech_admin(user_id) or is_hr_admin(user_id)

    if not is_admin_user:
        await message.reply("🚫 Только администраторы бота могут привязывать группу отдела кадров.")
        return

    parts = message.text.strip().split()
    target_group_id: Optional[int] = None

    if len(parts) > 1:
        arg = parts[1]
        if arg.startswith("-") and arg[1:].isdigit():
            target_group_id = int(arg)
        elif arg.isdigit():
            target_group_id = -int(arg)

    if not target_group_id:
        if message.chat.type in ["group", "supergroup"]:
            target_group_id = message.chat.id
        else:
            await message.reply(
                "ℹ️ <b>КАК ПРИВЯЗАТЬ ГРУППУ ДЛЯ АНКЕТ:</b>\n\n"
                "1️⃣ <b>Внутри группы:</b> Добавьте бота в чат отдела кадров, дайте права администратора и отправьте команду <code>/set_group</code>\n"
                "2️⃣ <b>Из личного диалога:</b> Отправьте команду с ID группы: <code>/set_group -100XXXXXXXXXX</code>\n\n"
                "<i>(Чтобы узнать ID группы, отправьте там команду /id)</i>",
                parse_mode="HTML",
            )
            return

    try:
        await bot.send_message(
            chat_id=target_group_id,
            text="🔔 <b>ТЕСТ СВЯЗИ:</b> Чат успешно привязан к боту МУП «Ульяновскэлектротранс»! Все анкеты соискателей поступают сюда.",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.reply(
            f"❌ <b>ОШИБКА ПРИВЯЗКИ ЧАТА {target_group_id}:</b>\n\n"
            f"Telegram ответил: <code>{e}</code>\n\n"
            "⚠️ <b>Проверьте:</b>\n"
            "1. Бот добавлен в этот чат?\n"
            "2. Вы назначили бота <b>Администратором</b> чата?\n"
            "3. Правильно ли скопирован ID со знаком минус?",
            parse_mode="HTML",
        )
        return

    CONFIG["HR_GROUP_ID"] = target_group_id
    if target_group_id not in CONFIG["TARGET_CHATS"]:
        CONFIG["TARGET_CHATS"].append(target_group_id)

    db.set_setting("hr_group_id", str(target_group_id))
    update_env_variable("HR_GROUP_ID", target_group_id)

    await message.reply(
        f"🎯 <b>Кадровая группа успешно проверена и привязана!</b>\n\n"
        f"• ID чата: <code>{target_group_id}</code>\n"
        f"• Тестовое сообщение доставлено в чат.\n\n"
        f"✅ <i>Все анкеты кандидатов теперь поступают в этот чат!</i>",
        parse_mode="HTML",
    )


@hr_router.message(Command("sync", "sync_group"))
@hr_router.callback_query(F.data == "hr_sync_all_in_one")
async def cmd_sync_group_two_way(event: Union[types.Message, types.CallbackQuery], bot: Bot) -> None:
    """Двухсторонняя синхронизация состава группы: выдача и отзыв ролей HR."""
    user_id = event.from_user.id
    chat_id = event.message.chat.id if isinstance(event, types.CallbackQuery) else event.chat.id
    allowed, err = check_hr_access_or_block(user_id, chat_id)
    if not allowed:
        await safe_answer(event, err or "🚫 Доступ ограничен.")
        return

    hr_group = CONFIG.get("HR_GROUP_ID", 0)
    super_admin = CONFIG.get("SUPER_ADMIN_ID", 0)

    if not hr_group:
        await safe_answer(event, "⚠️ Кадровая группа не привязана! Используйте <code>/set_group</code> в группе.")
        return

    try:
        bot_member = await bot.get_chat_member(hr_group, bot.id)
        if not isinstance(bot_member, (ChatMemberAdministrator, ChatMemberOwner)):
            await safe_answer(
                event,
                "⚠️ <b>Бот не является администратором группы кадров!</b>\nВыдайте боту права администратора в чате.",
            )
            return

        group_admins = await bot.get_chat_administrators(hr_group)
        db_hr_ids = [adm_id for adm_id, role in db.get_all_admins() if role == "hr"]

        active_list: List[str] = []
        added_list: List[str] = []
        removed_list: List[str] = []

        # Выдаем права присутствующим администраторам группы
        for a in group_admins:
            u = a.user
            if u.is_bot:
                continue

            name = html.escape(u.full_name or "Сотрудник")
            user_label = f"@{u.username} ({name})" if u.username else f"{name} [ID: <code>{u.id}</code>]"

            if u.id == super_admin:
                active_list.append(f"👑 <b>{user_label}</b> — Владелец")
                continue

            if db.get_admin_role(u.id) != "hr":
                db.unblock_user(u.id)
                db.add_admin(u.id, role="hr")
                added_list.append(user_label)
                active_list.append(f"➕ <b>{user_label}</b> — <b>выдана роль HR</b>")
            else:
                active_list.append(f"👤 <b>{user_label}</b> — роль HR активна ✅")

        # Отзываем права у тех, кто покинул группу
        for old_hr_id in db_hr_ids:
            if old_hr_id == super_admin:
                continue

            try:
                member = await bot.get_chat_member(hr_group, old_hr_id)
                is_still_in_group = member.status not in ("left", "kicked")
            except Exception:
                is_still_in_group = False

            if not is_still_in_group:
                db.remove_admin(old_hr_id)
                try:
                    c = await bot.get_chat(old_hr_id)
                    rem_label = f"@{c.username}" if c.username else c.full_name
                except Exception:
                    rem_label = f"ID: <code>{old_hr_id}</code>"
                removed_list.append(rem_label)

        report = (
            "🔄 <b>СИНХРОНИЗАЦИЯ БАЗЫ ДАННЫХ И ЧАТА</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏢 <b>Кадровый чат:</b> <code>{hr_group}</code>\n\n"
            f"👥 <b>Сотрудники кадровой службы в базе:</b>\n"
            + ("\n".join(active_list) if active_list else "<i>Список пуст</i>")
            + "\n━━━━━━━━━━━━━━━━━━━━━\n"
        )
        if added_list:
            report += f"➕ <b>Выдана роль HR ({len(added_list)}):</b>\n" + "\n".join(f"• {x}" for x in added_list) + "\n\n"
        if removed_list:
            report += f"➖ <b>Забрана роль HR (покинули чат) ({len(removed_list)}):</b>\n" + "\n".join(f"• {x}" for x in removed_list) + "\n\n"

        report += "<i>Все права зафиксированы в базе данных и соответствуют чату.</i>"

        if isinstance(event, types.CallbackQuery):
            await event.message.edit_text(report, parse_mode="HTML")
            await event.answer("Синхронизировано!")
        else:
            await safe_answer(event, report, parse_mode="HTML")

    except Exception as e:
        await safe_answer(event, f"❌ Ошибка синхронизации: <code>{html.escape(str(e))}</code>", parse_mode="HTML")


# ==============================================================================
# 11. ГЕНЕРАЦИЯ АКТА ОБ УНИЧТОЖЕНИИ ПДн (Приказ Роскомнадзора № 179)
# ==============================================================================

@hr_router.message(Command("act"))
async def cmd_generate_destruction_act(message: types.Message) -> None:
    """Генерация официального Акта об уничтожении ПДн соискателя по 152-ФЗ."""
    user_id = message.from_user.id
    if not is_hr_admin(user_id) and not is_tech_admin(user_id) and user_id != CONFIG.get("SUPER_ADMIN_ID"):
        await safe_answer(message, "🚫 Доступ ограничен.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        msg = (
            "ℹ️ <b>Использование:</b> <code>/act &lt;ID_анкеты&gt;</code>\n"
            "Генерирует официальный Акт об уничтожении персональных данных по Приказу Роскомнадзора № 179."
        )
        await safe_answer(message, msg)
        return

    cand_id = int(parts[1].strip())
    cand = db.get_candidate(cand_id)
    fio = cand[3] if cand else "Субъект ПДн"
    phone = cand[4] if cand else "N/A"
    vac = cand[5] if cand else "Соискатель"

    try:
        import doc_generator  # type: ignore

        path = doc_generator.generate_destruction_act_docx({
            "id": cand_id,
            "fio": fio,
            "phone": phone,
            "vacancy": vac,
            "reason": "Запрос кадровой службы / уничтожение по ст. 21 152-ФЗ",
        })
        with open(path, "rb") as f:
            data = f.read()

        doc_file = BufferedInputFile(data, filename=f"Act_Destruction_PDn_{cand_id}.docx")
        caption = (
            f"📄 <b>Официальный Акт об уничтожении ПДн № {cand_id}-УПД</b>\n"
            f"Субъект: <b>{html.escape(fio)}</b>\n"
            "Сформирован по форме Приказа Роскомнадзора от 28.10.2022 № 179."
        )
        await message.answer_document(doc_file, caption=caption, parse_mode="HTML")
    except ImportError:
        await safe_answer(message, "⚠️ Модуль <code>doc_generator</code> не найден в проекте.")
    except Exception as e:
        logger.error("Ошибка генерации Акта: %s", e)
        await safe_answer(message, f"❌ Ошибка генерации документа: {e}")
@hr_router.callback_query(F.data == "hr_mute_all_dm")
async def cb_hr_mute_all_dm(callback: types.CallbackQuery):
    super_id = CONFIG.get("SUPER_ADMIN_ID")
    if callback.from_user.id != super_id:
        return await callback.answer("🚫 Это действие доступно только Главному администратору!", show_alert=True)

    count = db.disable_all_hr_notifications()
    await callback.answer(
        f"🔕 Уведомления в ЛС принудительно отключены для всех кадровиков ({count} чел.)!\n"
        f"Анкеты теперь будут поступать строго в общую группу кадров.",
        show_alert=True
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=make_admin_menu_keyboard(callback.from_user.id))
    except Exception:
        pass        