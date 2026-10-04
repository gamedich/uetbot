# -*- coding: utf-8 -*-
"""
Обработчики кадровой службы МУП «Ульяновскэлектротранс»:
- Команда /admin и кадровая аналитика
- Просмотр и изменение статусов анкет (В работу, Пригласить, Отказ, Архив)
- Мост прямого диалога (живой чат соискателя и кадровика)
- Обработка входящих вопросов соискателей (/ask)
- Заметки кадровика к анкетам
- Экспорт базы соискателей в Excel/CSV (/export)
- Привязка кадрового чата (/set_group) и черный список
- Авто-выдача и авто-снятие ролей при входе/выходе из группы
- Двухсторонняя синхронизация состава группы (/sync)
- Быстрый кик нарушителей (/kick)
"""

from __future__ import annotations

import csv
import html
import io
import logging
import os
import re
from datetime import datetime
from typing import Optional, List, Tuple, Any

from aiogram import Router, F, types, Bot
from aiogram.filters import Command, StateFilter
from aiogram.filters.chat_member_updated import (
    ChatMemberUpdatedFilter,
    JOIN_TRANSITION,
    LEAVE_TRANSITION
)
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    ChatMemberAdministrator,
    ChatMemberOwner
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import texts
from common import (
    CONFIG,
    db,
    is_hr_admin,
    is_tech_admin,
    safe_answer,
    safe_send,
    send_response_to_candidate,
    send_photo_to_candidate,
    CustomInviteForm,
    CandidateDirectMsgForm,
    HRReplyForm,
    CandidateNoteForm
)
from keyboards import (
    make_admin_menu_keyboard,
    make_ticket_keyboard,
    make_inquiry_admin_keyboard,
    make_candidate_main_keyboard
)

logger = logging.getLogger("HR_HANDLER")
hr_router = Router(name="hr")


def check_hr_access_or_block(user_id: int, chat_id: int) -> tuple[bool, str | None]:
    """Проверка прав доступа к кадровой информации и анкетам:
    - Главный администратор (SUPER_ADMIN_ID) и технический администратор/разработчик (is_tech_admin) имеют полный доступ.
    - Авторизованный сотрудник отдела кадров (is_hr_admin / роль 'hr') имеет полный доступ.
    - В официальной кадровой группе (HR_GROUP_ID) доступ открыт для всех участников чата.
    - Посторонним пользователям и соискателям доступ строго закрыт.
    """
    super_id = CONFIG.get("SUPER_ADMIN_ID")
    hr_group = CONFIG.get("HR_GROUP_ID", 0)

    # 1. Администраторы системы (Superadmin, Tech Admin / разработчик)
    if (super_id and user_id == super_id) or is_tech_admin(user_id):
        return True, None

    # 2. Сотрудники отдела кадров (роль HR)
    if is_hr_admin(user_id):
        return True, None

    # 3. В официальной рабочей группе отдела кадров
    if hr_group and chat_id == hr_group:
        return True, None

    # Всем остальным посторонним доступ закрыт
    return False, "🚫 <b>Доступ ограничен.</b> Кадровая панель доступна только сотрудникам отдела кадров МУП «Ульяновскэлектротранс»."


@hr_router.message(Command("hr", "admin", "kadry"))
async def cmd_admin(message: types.Message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    allowed, err_text = check_hr_access_or_block(user_id, chat_id)
    if not allowed:
        return await safe_answer(message, err_text, parse_mode="HTML")

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
async def cb_admin_stats(callback: types.CallbackQuery):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.message.edit_text(err_text, parse_mode="HTML")
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
    await callback.message.edit_text(text, reply_markup=make_admin_menu_keyboard(callback.from_user.id), parse_mode="HTML")
    await callback.answer()

@hr_router.callback_query(F.data.in_(["admin_list_all", "admin_list_new", "admin_list_in_progress", "admin_list_archive"]))
async def cb_admin_list(callback: types.CallbackQuery):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.message.edit_text(err_text, parse_mode="HTML")
    only_new = callback.data == "admin_list_new"
    is_archive = callback.data == "admin_list_archive"
    filter_status = "В работе" if callback.data == "admin_list_in_progress" else ("Архив" if is_archive else None)
    candidates = db.get_recent_candidates(limit=10, filter_status=filter_status, only_new=only_new, is_archive=is_archive)

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
        await callback.message.edit_text(f"{title}\n\n<i>Список пуст.</i>", reply_markup=builder.as_markup(), parse_mode="HTML")
        return await callback.answer()

    builder = InlineKeyboardBuilder()
    text = f"{title}\n━━━━━━━━━━━━━━━━━━━━━\n"
    for cand in candidates:
        t_id, name, vac, status, _, plat = cand
        icon = "🆕" if status == "Новая" else ("🟡" if status == "В работе" else ("🟢" if "Приглашен" in status else "🔴"))
        text += f"{icon} <b>#{t_id} [{plat.upper()}]</b> | {name}\n└ <i>{vac}</i> (<b>{status}</b>)\n\n"
        builder.button(text=f"Открыть #{t_id}", callback_data=f"view_{t_id}")

    builder.button(text="⬅️ Назад в меню", callback_data="admin_stats")
    builder.adjust(2)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()

@hr_router.callback_query(F.data.startswith("view_"))
async def cb_view_ticket(callback: types.CallbackQuery):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.answer(err_text or "🚫 Доступ ограничен.", show_alert=True)

    ticket_id = int(callback.data.split("_")[1])
    cand = db.get_candidate(ticket_id)
    if not cand:
        return await callback.answer("⚠️ Анкета не найдена!", show_alert=True)

    card = texts.format_hr_card_full(cand)
    try:
        await callback.message.edit_text(card, reply_markup=make_ticket_keyboard(ticket_id), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

@hr_router.callback_query(F.data.startswith("cand_call_"))
async def cb_cand_call(callback: types.CallbackQuery):
    ticket_id = int(callback.data.split("_")[2])
    cand = db.get_candidate(ticket_id)
    if not cand:
        return await callback.answer("Анкета не найдена!", show_alert=True)
    phone = cand[4]
    name = cand[3]
    await callback.answer(f"📞 Телефон {name}: {phone}", show_alert=True)


@hr_router.callback_query(F.data.startswith("cand_note_") | F.data.startswith("note_"))
async def cb_cand_note_ask(callback: types.CallbackQuery, state: FSMContext):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.answer("🚫 Доступ ограничен. Только для сотрудников отдела кадров.", show_alert=True)
    ticket_id = int(callback.data.split("_")[-1])
    cand = db.get_candidate(ticket_id)
    if not cand:
        return await callback.answer("Анкета не найдена!", show_alert=True)

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
async def process_cand_note(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    cand = db.get_candidate(ticket_id)
    if not cand:
        await state.clear()
        return await safe_answer(message, "⚠️ Анкета не найдена.")

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
        await safe_answer(message, f"✅ Заметка к анкете #{ticket_id} сохранена:\n«<i>{html.escape(text)}</i>»", reply_markup=builder.as_markup(), parse_mode="HTML")


@hr_router.message(Command("note", "admin_note"))
async def cmd_set_note(message: types.Message):
    allowed, err_text = check_hr_access_or_block(message.from_user.id, message.chat.id)
    if not allowed:
        return await safe_answer(message, err_text, parse_mode="HTML")
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        return await safe_answer(
            message,
            "ℹ️ Использование: <code>/note <номер_анкеты> <текст заметки></code>\nПример: <code>/note 12 Созвонились, ждём в четверг</code>",
            parse_mode="HTML"
        )
    t_id = int(parts[1])
    note_text = parts[2].strip()
    db.update_admin_note(t_id, note_text)
    builder = InlineKeyboardBuilder()
    builder.button(text=f"📑 Открыть анкету #{t_id}", callback_data=f"view_{t_id}")
    await safe_answer(message, f"✅ Заметка к анкете #{t_id} обновлена:\n«<i>{html.escape(note_text)}</i>»", reply_markup=builder.as_markup(), parse_mode="HTML")

@hr_router.callback_query(F.data.startswith("cand_msg_"))
async def cb_cand_direct_msg(callback: types.CallbackQuery, state: FSMContext):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.answer("🚫 Доступ ограничен. Только для сотрудников отдела кадров.", show_alert=True)
    ticket_id = int(callback.data.split("_")[2])
    cand = db.get_candidate(ticket_id)
    if not cand:
        return await callback.answer("Анкета не найдена!", show_alert=True)
    
    await state.set_state(CandidateDirectMsgForm.waiting_text)
    await state.update_data(ticket_id=ticket_id)
    await callback.message.reply(
        f"💬 <b>Написать кандидату {cand[3]} (Анкета #{ticket_id}):</b>\n\n"
        "Введите текст сообщения. Бот официально перешлет его соискателю в Telegram/VK/МАКС.\n\n"
        "<i>(Ваш личный контакт останется скрыт).</i>",
        parse_mode="HTML"
    )
    await callback.answer()

@hr_router.message(CandidateDirectMsgForm.waiting_text)
async def process_candidate_direct_msg(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    cand = db.get_candidate(ticket_id)
    if not cand:
        await state.clear()
        return await safe_answer(message, "⚠️ Анкета не найдена.")
    
    reply_text = (message.text or "").strip()
    if not reply_text:
        return await safe_answer(message, "⚠️ Введите текст сообщения.")
    
    await state.clear()
    platform, user_id, full_name = cand[1], cand[2], cand[3]
    user_msg = (
        f"📩 <b>Сообщение от отдела кадров МУП «Ульяновскэлектротранс»:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{reply_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>По вашей анкете #{ticket_id}. Чтобы отправить ответ, нажмите кнопку ниже:</i>"
    )
    if platform == "vk":
        from gateways.vk_gateway import make_vk_reply_keyboard
        ok = await send_response_to_candidate(platform, user_id, user_msg, keyboard=make_vk_reply_keyboard(ticket_id))
    else:
        from keyboards import make_cand_reply_keyboard
        ok = await send_response_to_candidate(platform, user_id, user_msg, keyboard=make_cand_reply_keyboard(ticket_id))
    if ok:
        await safe_answer(message, f"✅ Сообщение успешно отправлено кандидату <b>{full_name}</b>!", parse_mode="HTML")
    else:
        await safe_answer(message, f"⚠️ Не удалось доставить сообщение кандидату в {platform}.", parse_mode="HTML")

@hr_router.callback_query(F.data.startswith("status_"))
async def cb_change_status(callback: types.CallbackQuery):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.answer("🚫 Доступ ограничен. Только для сотрудников отдела кадров.", show_alert=True)

    if callback.data.startswith("status_noop_"):
        return await callback.answer("ℹ️ Анкета уже имеет данный статус!", show_alert=True)

    parts = callback.data.split("_")
    ticket_id = int(parts[1])
    new_status = parts[2]

    cand = db.get_candidate(ticket_id)
    if not cand:
        return await callback.answer("Анкета не найдена!", show_alert=True)

    platform, user_id, full_name = cand[1], cand[2], cand[3]
    db.update_status(ticket_id, new_status)

    if new_status == "В работе":
        user_msg = (
            f"🟡 <b>Здравствуйте, {full_name}!</b>\n\n"
            "Ваша анкета взята <b>в работу</b> специалистами отдела кадров <b>МУП «Ульяновскэлектротранс»</b>.\n"
            "Специалист изучает ваши данные. О решении и дальнейших шагах мы уведомим вас здесь."
        )
        alert_msg = "Статус изменен на «В работе»"
    elif new_status == "Приглашен":
        user_msg = (
            f"🟢 <b>Здравствуйте, {full_name}!</b>\n\n"
            "Ваша анкета рассмотрена специалистами <b>МУП «Ульяновскэлектротранс»</b>.\n"
            "Мы рады <b>пригласить вас на собеседование</b>! В ближайшее время с вами свяжутся по телефону."
        )
        alert_msg = "Кандидат приглашен"
    elif new_status == "Отказ":
        user_msg = (
            f"📋 <b>Здравствуйте, {full_name}!</b>\n\n"
            "Благодарим вас за интерес к трудоустройству в <b>МУП «Ульяновскэлектротранс»</b>.\n\n"
            "К сожалению, в настоящее время мы не готовы предложить вам эту должность. "
            "Ваша анкета сохранена в кадровом резерве предприятия.\n\n"
            "⏳ <i>В соответствии с регламентом предприятия, повторную анкету можно подать <b>через 3 месяца</b>.</i>"
        )
        alert_msg = "Кандидату отправлен отказ (повтор через 3 мес.)"
    elif new_status == "Архив":
        user_msg = None
        alert_msg = "📦 Анкета перенесена в архив"
    else:
        user_msg = (
            f"📋 <b>Здравствуйте, {full_name}!</b>\n\n"
            f"Статус вашей анкеты #{ticket_id} изменен на: <b>{new_status}</b>."
        )
        alert_msg = f"Статус: {new_status}"

    if user_msg:
        await send_response_to_candidate(platform, user_id, user_msg)
    await callback.answer(alert_msg)

    try:
        updated_cand = db.get_candidate(ticket_id)
        if updated_cand:
            updated_card = texts.format_hr_card_full(updated_cand)
            await callback.message.edit_text(updated_card, reply_markup=make_ticket_keyboard(ticket_id), parse_mode="HTML")
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=make_ticket_keyboard(ticket_id))
        except Exception:
            pass

@hr_router.callback_query(F.data.startswith("del_ask_"))
async def cb_delete_ticket_ask(callback: types.CallbackQuery):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.message.edit_text(err_text, parse_mode="HTML")
    """Шаг 1: Защита от случайного нажатия — запрос подтверждения."""
    ticket_id = int(callback.data.replace("del_ask_", ""))
    cand = db.get_candidate(ticket_id)
    if not cand:
        return await callback.answer("Анкета не найдена или уже удалена!", show_alert=True)

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
        f"👤 <b>Кандидат:</b> {full_name}\n"
        f"🎯 <b>Должность:</b> {vac}\n\n"
        f"<i>Все данные будут стёрты из базы. Перед удалением автоматически создаётся резервная копия.</i>"
    )
    try:
        await callback.message.edit_text(warn_text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@hr_router.callback_query(F.data.startswith("del_confirm_"))
async def cb_delete_ticket(callback: types.CallbackQuery):
    """Шаг 2: Реальное удаление после подтверждения со 100% гарантией уведомления соискателя."""
    ticket_id = int(callback.data.replace("del_confirm_", ""))
    cand = db.get_candidate(ticket_id)
    if not cand:
        builder = InlineKeyboardBuilder()
        builder.button(text="⬅️ К списку резюме", callback_data="admin_list_all")
        try:
            await callback.message.edit_text(f"🗑 <b>Анкета #{ticket_id} уже удалена из базы данных.</b>", reply_markup=builder.as_markup(), parse_mode="HTML")
        except Exception:
            pass
        return await callback.answer("Анкета уже удалена!", show_alert=True)

    cand_info = {
        "ticket_id": cand[0],
        "platform": cand[1],
        "user_id": str(cand[2]),
        "full_name": cand[3],
        "phone": cand[4],
        "vacancy": cand[5],
    }

    # Автоматический бэкап перед любым удалением (защита от случайной потери)
    try:
        db.backup_database()
    except Exception as e:
        logger.warning(f"Не удалось создать автобэкап перед удалением: {e}")

    # Гарантированное физическое удаление из SQLite базы
    db.delete_candidate(ticket_id)

    platform = cand_info["platform"]
    user_id = cand_info["user_id"]
    full_name = cand_info["full_name"]
    vac = cand_info["vacancy"]
    destroy_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    # 1. Понятное и официальное уведомление соискателю об удалении анкеты
    esc_name = html.escape(full_name)
    esc_vac = html.escape(vac)
    del_msg = (
        f"🗑 <b>Уведомление об удалении анкеты</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Здравствуйте, <b>{esc_name}</b>!\n\n"
        f"Ваша анкета <b>№{ticket_id}</b> на вакансию <b>«{esc_vac}»</b> была <b>удалена</b> кадровой службой МУП «Ульяновскэлектротранс».\n\n"
        f"Все связанные персональные данные были безвозвратно уничтожены в соответствии со ст. 21 Федерального закона № 152-ФЗ.\n"
        f"⏱ <b>Время удаления:</b> <code>{destroy_time}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>При необходимости вы можете подать новую анкету или задать вопрос через главное меню:</i>"
    )

    notif_delivered = False
    try:
        from keyboards import make_candidate_main_keyboard
        notif_delivered = await send_response_to_candidate(
            platform, user_id, del_msg, keyboard=make_candidate_main_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления об удалении анкеты #{ticket_id}: {e}")

    # 2. Карточка в кадровый чат
    op_name = callback.from_user.full_name or f"ID {callback.from_user.id}"
    status_label = "✅ Соискатель успешно уведомлен в ЛС" if notif_delivered else "⚠️ Соискатель не получил уведомление (диалог не начат или бот заблокирован)"
    audit_card = (
        f"🗑 <b>АНКЕТА И ПЕРСОНАЛЬНЫЕ ДАННЫЕ УНИЧТОЖЕНЫ (152-ФЗ)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>Номер заявки:</b> #{ticket_id}\n"
        f"👤 <b>Субъект ПДн:</b> {esc_name}\n"
        f"🎯 <b>Должность:</b> {esc_vac}\n"
        f"⏱ <b>Время уничтожения:</b> <code>{destroy_time}</code>\n"
        f"👨‍💼 <b>Оператор:</b> {op_name}\n"
        f"📢 <b>Статус соискателя:</b> {status_label}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚖️ <i>Запись полностью удалена из базы данных SQLite.</i>"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ К списку резюме", callback_data="admin_list_all")
    try:
        await callback.message.edit_text(audit_card, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("Анкета успешно удалена!")


@hr_router.callback_query(F.data.startswith("block_cand_") | F.data.startswith("block_"))
async def cb_block_candidate(callback: types.CallbackQuery):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.answer("🚫 Доступ ограничен. Только для сотрудников отдела кадров.", show_alert=True)
    data_parts = callback.data.split("_")
    ticket_id = int(data_parts[-1])
    cand = db.get_candidate(ticket_id)
    if not cand:
        return await callback.answer("Анкета не найдена!", show_alert=True)

    platform = cand[1]
    cand_uid = str(cand[2])
    full_name = cand[3]
    super_uid = str(CONFIG["SUPER_ADMIN_ID"])

    if cand_uid == super_uid or (cand_uid.isdigit() and (is_tech_admin(int(cand_uid)) or is_hr_admin(int(cand_uid)))):
        return await callback.answer("🚫 Нельзя добавить администратора в черный список!", show_alert=True)

    reason = "Блокировка кадровой службой"
    # Блокируем в ЧС, завершаем активный диалог, переводим анкеты в Отказ (ЧС), закрываем обращения
    db.block_user_and_clean(cand_uid, reason=reason)

    # Отправляем уведомление соискателю
    ban_user_msg = (
        "⛔ <b>Уведомление об ограничении доступа</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"Здравствуйте, <b>{html.escape(full_name)}</b>!\n\n"
        "Информируем вас о том, что ваш аккаунт внесён в <b>чёрный список</b> "
        "информационной системы МУП «Ульяновскэлектротранс».\n\n"
        f"📋 <b>Причина:</b> <i>{html.escape(reason)}</i>\n\n"
        "• Все ваши активные заявки и обращения аннулированы.\n"
        "• Прямой диалог с кадровой службой прекращен.\n"
        "• Доступ к отправке анкет и сообщений в боте ограничен.\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📞 <i>При возникновении вопросов:</i> <code>{CONFIG['HR_PHONE']}</code>"
    )
    sent_to_cand = await send_response_to_candidate(platform, cand_uid, ban_user_msg)

    alert_text = "⛔ Пользователь добавлен в ЧС! Уведомление отправлено." if sent_to_cand else "⛔ Добавлен в ЧС (не удалось отправить ЛС)."
    await callback.answer(alert_text, show_alert=True)

    try:
        updated_cand = db.get_candidate(ticket_id)
        if updated_cand:
            t_id, plat, _, name, phone, vac, exp, st, _, created, *rest = updated_cand
            db_label = "<code>resumes_test.db</code> (Тестовая)" if t_id >= 900000 else "<code>resumes.db</code> (Боевая)"
            prefix = "🧪 <b>ТЕСТОВАЯ АНКЕТА</b>" if t_id >= 900000 else "📑 <b>АНКЕТА</b>"
            updated_card = (
                f"{prefix} СОИСКАТЕЛЯ #{t_id}\n"
                f"📁 <b>База:</b> {db_label}\n"
                f"🌐 <b>Источник:</b> <code>{plat.upper()}</code> | Статус: <b>{st}</b>\n"
                f"⏱ <b>Дата подачи:</b> <code>{created}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>ФИО:</b> {html.escape(name)}\n"
                f"📞 <b>Телефон:</b> <code>{phone}</code>\n"
                f"🎯 <b>Вакансия:</b> {html.escape(vac)}\n"
                f"💼 <b>Опыт работы:</b> {html.escape(exp)}\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "⛔ <i>Пользователь находится в чёрном списке. Анкета отклонена.</i>"
            )
            await callback.message.edit_text(updated_card, reply_markup=make_ticket_keyboard(ticket_id), parse_mode="HTML")
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=make_ticket_keyboard(ticket_id))
        except Exception:
            pass


@hr_router.callback_query(F.data.startswith("unblock_cand_"))
async def cb_unblock_candidate(callback: types.CallbackQuery):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.answer("🚫 Доступ ограничен кадровой службой!", show_alert=True)
    ticket_id = int(callback.data.split("_")[2])
    cand = db.get_candidate(ticket_id)
    if not cand:
        return await callback.answer("Анкета не найдена!", show_alert=True)

    platform = cand[1]
    cand_uid = cand[2]
    full_name = cand[3]
    db.unblock_user(cand_uid)

    from keyboards import make_candidate_main_keyboard
    unban_user_msg = (
        "✅ <b>Ограничение доступа снято</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"Здравствуйте, <b>{html.escape(full_name)}</b>!\n\n"
        "Блокировка вашего аккаунта в сервисе МУП «Ульяновскэлектротранс» была снята отделом кадров.\n\n"
        "Вы снова можете пользоваться ботом, задавать вопросы и подавать анкеты на вакансии предприятия.\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    sent = await send_response_to_candidate(platform, str(cand_uid), unban_user_msg, keyboard=make_candidate_main_keyboard())
    alert = "✅ Блокировка снята! Соискателю отправлено уведомление." if sent else "✅ Блокировка снята!"
    await callback.answer(alert, show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=make_ticket_keyboard(ticket_id))
    except Exception:
        pass

@hr_router.callback_query(F.data.startswith("invite_menu_"))
async def cb_invite_menu(callback: types.CallbackQuery):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.answer("🚫 Доступ ограничен. Только для сотрудников отдела кадров.", show_alert=True)
    ticket_id = int(callback.data.split("_")[2])
    cand = db.get_candidate(ticket_id)
    if not cand:
        return await callback.answer("Анкета не найдена!", show_alert=True)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📞 Стандартно (свяжемся по телефону)", callback_data=f"status_{ticket_id}_Приглашен")
    builder.button(text="📅 Назначить дату и время встречи", callback_data=f"invite_custom_{ticket_id}")
    builder.button(text="⬅️ Назад к анкете", callback_data=f"view_{ticket_id}")
    builder.adjust(1)

    text = (
        f"🟢 <b>ПРИГЛАШЕНИЕ КАНДИДАТА #{ticket_id} ({cand[3]})</b>\n\n"
        "Выберите формат приглашения:\n"
        "• <b>Стандартно:</b> кандидату придет уведомление, что с ним свяжутся по телефону.\n"
        "• <b>С датой и временем:</b> вы введете точное время собеседования, адрес и кабинет."
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()

@hr_router.callback_query(F.data.startswith("invite_custom_"))
async def cb_invite_custom(callback: types.CallbackQuery, state: FSMContext):
    ticket_id = int(callback.data.split("_")[2])
    cand = db.get_candidate(ticket_id)
    if not cand:
        return await callback.answer("Анкета не найдена!", show_alert=True)

    await state.set_state(CustomInviteForm.waiting_datetime)
    await state.update_data(ticket_id=ticket_id)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=f"view_{ticket_id}")
    
    text = (
    f"📅 <b>ПРИГЛАШЕНИЕ НА СОБЕСЕДОВАНИЕ</b>\n"
    f"для соискателя <b>{cand[3]}</b> (Анкета #{ticket_id}):\n\n"
    "Напишите ответным сообщением дату, время, кабинет и любые пояснения для кандидата в свободной форме.\n\n"
    "<i>Сообщение будет отправлено соискателю. Для отмены нажмите кнопку ниже:</i>"
)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()

@hr_router.message(CustomInviteForm.waiting_datetime)
async def process_custom_invite_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    cand = db.get_candidate(ticket_id)
    if not cand:
        await state.clear()
        return await safe_answer(message, "⚠️ Анкета не найдена.")

    dt_text = (message.text or "").strip()
    if not dt_text:
        return await safe_answer(message, "⚠️ Пожалуйста, напишите дату и время встречи сообщением.")

    await state.clear()
    platform, user_id, full_name = cand[1], cand[2], cand[3]
    db.update_status(ticket_id, "Приглашен (с датой)")

    user_msg = (
        f"🎉 <b>Здравствуйте, {full_name}!</b>\n\n"
        f"Ваша анкета рассмотрена кадровой службой <b>МУП «Ульяновскэлектротранс»</b>.\n\n"
        f"Мы рады <b>пригласить вас на очное собеседование</b>!\n\n"
        f"📅 <b>Детали встречи:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{dt_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>При возникновении вопросов звоните: {CONFIG['HR_PHONE']}</i>"
    )
    if platform == "vk":
        from gateways.vk_gateway import make_vk_reply_keyboard
        ok = await send_response_to_candidate(platform, user_id, user_msg, keyboard=make_vk_reply_keyboard(ticket_id))
    else:
        from keyboards import make_cand_reply_keyboard
        ok = await send_response_to_candidate(platform, user_id, user_msg, keyboard=make_cand_reply_keyboard(ticket_id))
    
    updated_cand = db.get_candidate(ticket_id)
    t_id, plat, _, name, phone, vac, exp, st, _, created, *rest = updated_cand
    db_label = "<code>resumes_test.db</code> (Тестовая)" if t_id >= 900000 else "<code>resumes.db</code> (Боевая)"
    prefix = "🧪 <b>ТЕСТОВАЯ АНКЕТА</b>" if t_id >= 900000 else "📑 <b>АНКЕТА</b>"
    updated_card = (
        f"{prefix} СОИСКАТЕЛЯ #{t_id}\n"
        f"📁 <b>База:</b> {db_label}\n"
        f"🌐 <b>Источник:</b> <code>{plat.upper()}</code> | Статус: <b>{st}</b>\n"
        f"⏱ <b>Дата подачи:</b> <code>{created}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>ФИО:</b> {name}\n"
        f"📞 <b>Телефон:</b> <code>{phone}</code>\n"
        f"🎯 <b>Должность:</b> {vac}\n"
        f"💼 <b>Опыт работы:</b> {exp}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Текущий статус: <b>{st}</b></i>\n"
        f"📍 <i>Назначено: {dt_text}</i>"
    )
    if ok:
        await safe_answer(
            message,
            f"✅ Приглашение с точным временем отправлено кандидату <b>{full_name}</b>!\n\n{updated_card}",
            reply_markup=make_ticket_keyboard(ticket_id),
            parse_mode="HTML"
        )
    else:
        await safe_answer(
            message,
            f"⚠️ Не удалось отправить сообщение кандидату ({platform}).\n\n{updated_card}",
            reply_markup=make_ticket_keyboard(ticket_id),
            parse_mode="HTML"
        )

@hr_router.callback_query(F.data.startswith("live_dlg_cand_"))
async def cb_live_dlg_cand(callback: types.CallbackQuery, bot: Bot):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.message.edit_text(err_text, parse_mode="HTML")
    ticket_id = int(callback.data.split("_")[3])
    cand = db.get_candidate(ticket_id)
    if not cand:
        return await callback.answer("Анкета не найдена!", show_alert=True)

    # Если диалог открыт в кадровом чате — сообщения соискателя будут идти прямо в кадровый чат, а не в ЛС!
    target_chat_id = callback.message.chat.id
    platform = cand[1] or "tg"
    user_id = str(cand[2])
    full_name = cand[3] or "Соискатель"

    db.start_direct_dialog(user_id=user_id, operator_id=target_chat_id, ticket_id=ticket_id, full_name=full_name, platform=platform)

    hr_kb = InlineKeyboardBuilder()
    hr_kb.button(text="⏹ Завершить прямой диалог", callback_data=f"end_live_dlg_{user_id}")
    
    cand_kb = InlineKeyboardBuilder()
    cand_kb.button(text="⏹ Завершить диалог", callback_data=f"end_live_dlg_{user_id}")

    await callback.message.reply(
        f"🟢 <b>ПРЯМОЙ ДИАЛОГ НАЧАТ!</b>\n\n"
        f"Вы подключены к прямому чату с соискателем <b>{full_name}</b> (ID: <code>{user_id}</code>, {platform.upper()}).\n"
        f"Все ваши текстовые сообщения теперь будут автоматически пересылаться кандидату.\n\n"
        f"<i>Чтобы закрыть чат, нажмите кнопку ниже:</i>",
        reply_markup=hr_kb.as_markup(),
        parse_mode="HTML"
    )

    cand_notify = (
        f"🟢 <b>Специалист отдела кадров МУП «Ульяновскэлектротранс» подключился к прямому диалогу!</b>\n\n"
        f"Вы можете общаться со специалистом напрямую в этом чате. "
        f"Все ваши сообщения видит кадровик.\n\n"
        f"<i>Для завершения диалога напишите: /stop</i>"
    )
    if platform == "vk":
        from gateways.vk_gateway import make_vk_dialog_keyboard
        await send_response_to_candidate(platform, user_id, cand_notify, keyboard=make_vk_dialog_keyboard())
    else:
        await send_response_to_candidate(platform, user_id, cand_notify, keyboard=cand_kb.as_markup())

    await callback.answer("Прямой диалог открыт!")

@hr_router.callback_query(F.data.startswith("live_dlg_inq_"))
async def cb_live_dlg_inq(callback: types.CallbackQuery, bot: Bot):
    inquiry_id = int(callback.data.split("_")[3])
    inq = db.get_inquiry(inquiry_id)
    if not inq:
        return await callback.answer("Обращение не найдено!", show_alert=True)

    target_chat_id = callback.message.chat.id
    platform = inq[2] or "tg"
    user_id = str(inq[3])
    full_name = inq[4] or "Соискатель"

    db.start_direct_dialog(user_id=user_id, operator_id=target_chat_id, ticket_id=inq[1], full_name=full_name, platform=platform)

    hr_kb = InlineKeyboardBuilder()
    hr_kb.button(text="⏹ Завершить прямой диалог", callback_data=f"end_live_dlg_{user_id}")

    cand_kb = InlineKeyboardBuilder()
    cand_kb.button(text="⏹ Завершить диалог", callback_data=f"end_live_dlg_{user_id}")

    await callback.message.reply(
        f"🟢 <b>ПРЯМОЙ ДИАЛОГ НАЧАТ!</b>\n\n"
        f"Вы подключены к кандидату <b>{full_name}</b> (ID: <code>{user_id}</code>, {platform.upper()}).\n"
        f"Пишите сообщения — они будут моментально уходить соискателю.",
        reply_markup=hr_kb.as_markup(),
        parse_mode="HTML"
    )

    cand_notify = (
        f"🟢 <b>Специалист отдела кадров подключился к прямому диалогу по вашему вопросу!</b>\n\n"
        f"Вы можете задавать вопросы и общаться напрямую в этом чате.\n\n"
        f"<i>Для завершения диалога напишите: /stop</i>"
    )
    if platform == "vk":
        from gateways.vk_gateway import make_vk_dialog_keyboard
        await send_response_to_candidate(platform, user_id, cand_notify, keyboard=make_vk_dialog_keyboard())
    else:
        await send_response_to_candidate(platform, user_id, cand_notify, keyboard=cand_kb.as_markup())

    await callback.answer("Прямой диалог открыт!")

@hr_router.message(Command("stop"))
async def cmd_operator_stop_dialog(message: types.Message, bot: Bot):
    op_id = message.chat.id
    dlg = db.get_dialog_by_operator(op_id) or db.get_dialog_by_operator(message.from_user.id)
    if not dlg:
        return await safe_answer(message, "ℹ️ В этом чате нет активного прямого диалога.")
    cand_user_id = str(dlg[0])
    platform = dlg[4] if len(dlg) > 4 and dlg[4] else "tg"
    name = dlg[3] or "Соискатель"
    db.end_direct_dialog(user_id=cand_user_id)
    db.end_direct_dialog(operator_id=op_id)
    cand_end_text = (
        "⏹ <b>Прямой диалог с отделом кадров завершён.</b>\n\n"
        "Благодарим вас за уделённое время! Вы всегда можете подать анкету или воспользоваться меню бота."
    )
    if platform == "vk":
        from gateways.vk_gateway import make_vk_main_keyboard
        await send_response_to_candidate(platform, cand_user_id, cand_end_text, keyboard=make_vk_main_keyboard())
    else:
        from keyboards import make_candidate_main_keyboard
        await send_response_to_candidate(platform, cand_user_id, cand_end_text, keyboard=make_candidate_main_keyboard())
    await safe_answer(
        message,
        f"⏹ <b>Прямой диалог с кандидатом {name} (ID: <code>{cand_user_id}</code>, {platform.upper()}) успешно завершён.</b>",
        parse_mode="HTML"
    )


@hr_router.callback_query(F.data.startswith("end_live_dlg_"))
async def cb_end_live_dlg(callback: types.CallbackQuery, bot: Bot):
    user_id = callback.data.replace("end_live_dlg_", "")
    dlg = db.get_dialog_by_user(user_id)
    if not dlg:
        return await callback.answer("Диалог уже завершен.", show_alert=True)

    db.end_direct_dialog(user_id=user_id)

    ticket_id = dlg[2]
    full_name = dlg[3]
    platform = dlg[4] if len(dlg) > 4 and dlg[4] else "tg"
    if platform == "tg" and ticket_id:
        cand = db.get_candidate(ticket_id)
        if cand and cand[1]:
            platform = cand[1]

    cand_end_text = (
        "⏹ <b>Прямой диалог с отделом кадров завершён.</b>\n\n"
        "Благодарим вас за уделённое время! Вы всегда можете подать анкету или воспользоваться меню бота."
    )
    if platform == "vk":
        from gateways.vk_gateway import make_vk_main_keyboard
        await send_response_to_candidate(platform, user_id, cand_end_text, keyboard=make_vk_main_keyboard())
    else:
        await send_response_to_candidate(platform, user_id, cand_end_text)

    await callback.answer("Прямой диалог завершен.")
    try:
        await callback.message.edit_text(
            f"⏹ <b>Прямой диалог с кандидатом {dlg[3]} (ID: <code>{user_id}</code>) успешно завершён.</b>",
            parse_mode="HTML"
        )
    except Exception:
        pass

@hr_router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def process_live_dialog_router(message: types.Message, state: FSMContext, bot: Bot):
    current_state = await state.get_state()
    if current_state is not None:
        return

    sender_id = message.from_user.id
    sender_id_str = str(sender_id)

    user_dlg = db.get_dialog_by_user(sender_id_str)
    if user_dlg:
        operator_id = user_dlg[1]
        name = user_dlg[3] or message.from_user.full_name
        relayed_text = (
            f"💬 <b>[Соискатель {name}]:</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{message.text}\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="⏹ Завершить диалог", callback_data=f"end_live_dlg_{sender_id_str}")
        await safe_send(bot, operator_id, relayed_text, reply_markup=builder.as_markup())
        return

    op_dlg = db.get_dialog_by_operator(message.chat.id) or db.get_dialog_by_operator(sender_id)
    if op_dlg:
        cand_user_id = op_dlg[0]
        ticket_id = op_dlg[2]
        platform = op_dlg[4] if len(op_dlg) > 4 and op_dlg[4] else "tg"
        if platform == "tg" and ticket_id:
            cand = db.get_candidate(ticket_id)
            if cand and cand[1]:
                platform = cand[1]

        relayed_to_cand = (
            f"💬 <b>[Специалист отдела кадров МУП «УЭТ»]:</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{message.text}\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        ok = await send_response_to_candidate(platform, cand_user_id, relayed_to_cand)
        if ok:
            await safe_answer(message, f"✅ <i>Доставлено соискателю [{platform.upper()}]</i>", parse_mode="HTML")
        else:
            await safe_answer(message, f"⚠️ Не удалось доставить сообщение кандидату ({platform.upper()}).")
        return

@hr_router.callback_query(F.data.startswith("inq_call_"))
async def cb_inq_call(callback: types.CallbackQuery):
    inquiry_id = int(callback.data.split("_")[2])
    inq = db.get_inquiry(inquiry_id)
    if not inq:
        return await callback.answer("Обращение не найдено.", show_alert=True)
    phone = inq[5] or "Не указан"
    await callback.answer(f"📞 Телефон соискателя: {phone}", show_alert=True)

@hr_router.callback_query(F.data.startswith("inq_close_"))
async def cb_inq_close(callback: types.CallbackQuery):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.answer("🚫 Доступ ограничен кадровой службой!", show_alert=True)
    inquiry_id = int(callback.data.split("_")[2])
    inq = db.get_inquiry(inquiry_id)
    if not inq:
        return await callback.answer("Обращение не найдено.", show_alert=True)

    platform, user_id = inq[2], inq[3]
    db.close_inquiry(inquiry_id)

    close_user_msg = (
        f"⏹ <b>Диалог по обращению #{inquiry_id} завершён</b>\n\n"
        "Специалист отдела кадров <b>МУП «Ульяновскэлектротранс»</b> завершил сессию общения по вашему вопросу. "
        "Спасибо за обращение!"
    )
    await send_response_to_candidate(platform, user_id, close_user_msg)

    await callback.answer("Обращение закрыто.")
    try:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n━━━━━━━━━━━━━━━━━━━━━\n⏹ <b>Обращение #{inquiry_id} закрыто специалистом.</b>",
            parse_mode="HTML"
        )
    except Exception:
        pass

@hr_router.callback_query(F.data.startswith("inq_reply_"))
async def cb_inq_reply(callback: types.CallbackQuery, state: FSMContext):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.answer("🚫 Доступ ограничен кадровой службой!", show_alert=True)
    inquiry_id = int(callback.data.split("_")[2])
    inq = db.get_inquiry(inquiry_id)
    if not inq:
        return await callback.answer("Обращение не найдено.", show_alert=True)

    await state.set_state(HRReplyForm.waiting_reply)
    await state.update_data(inquiry_id=inquiry_id)
    await callback.message.reply(
        f"✍️ <b>Введите текст ответа</b> для соискателя <b>{inq[4]}</b> (обращение #{inquiry_id}):",
        parse_mode="HTML"
    )
    await callback.answer()

@hr_router.message(HRReplyForm.waiting_reply)
async def process_hr_reply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    inquiry_id = data.get("inquiry_id")
    inq = db.get_inquiry(inquiry_id)
    if not inq:
        await state.clear()
        return await safe_answer(message, "⚠️ Обращение не найдено в базе данных.")

    reply_text = (message.text or "").strip()
    if not reply_text:
        return await safe_answer(message, "⚠️ Ответ должен быть текстовым сообщением.")

    db.reply_inquiry(inquiry_id, reply_text)
    await state.clear()

    platform, user_id, full_name = inq[2], inq[3], inq[4]
    cand_t_id = inq[1] or 0
    user_msg = (
        f"💬 <b>Ответ отдела кадров МУП «Ульяновскэлектротранс»:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{reply_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>По обращению #{inquiry_id}. Чтобы отправить ответ, нажмите кнопку ниже:</i>"
    )
    if platform == "vk":
        from gateways.vk_gateway import make_vk_reply_keyboard
        ok = await send_response_to_candidate(platform, user_id, user_msg, keyboard=make_vk_reply_keyboard(cand_t_id))
    else:
        from keyboards import make_cand_reply_keyboard
        ok = await send_response_to_candidate(platform, user_id, user_msg, keyboard=make_cand_reply_keyboard(cand_t_id))
    if ok:
        await safe_answer(message, f"✅ Ответ успешно доставлен соискателю <b>{full_name}</b>!", parse_mode="HTML")
    else:
        await safe_answer(message, f"⚠️ Не удалось доставить сообщение в платформу {platform}.", parse_mode="HTML")

@hr_router.callback_query(F.data.startswith("inq_block_"))
async def cb_inq_block(callback: types.CallbackQuery):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.answer("🚫 Доступ ограничен кадровой службой!", show_alert=True)
    inquiry_id = int(callback.data.split("_")[2])
    inq = db.get_inquiry(inquiry_id)
    if not inq:
        return await callback.answer("Обращение не найдено!", show_alert=True)
    platform = inq[2]
    user_id = str(inq[3])
    full_name = inq[5] or f"ID {user_id}"
    super_uid = str(CONFIG["SUPER_ADMIN_ID"])
    if user_id == super_uid or (user_id.isdigit() and (is_tech_admin(int(user_id)) or is_hr_admin(int(user_id)))):
        return await callback.answer("🚫 Нельзя добавить администратора в черный список!", show_alert=True)

    reason = "Блокировка из обращения"
    db.block_user_and_clean(user_id, reason=reason)

    ban_user_msg = (
        "⛔ <b>Уведомление об ограничении доступа</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"Здравствуйте, <b>{html.escape(full_name)}</b>!\n\n"
        "Информируем вас о том, что ваш аккаунт внесён в <b>чёрный список</b> "
        "информационной системы МУП «Ульяновскэлектротранс».\n\n"
        f"📋 <b>Причина:</b> <i>{html.escape(reason)}</i>\n\n"
        "• Все ваши активные заявки и обращения аннулированы.\n"
        "• Прямой диалог с кадровой службой прекращен.\n"
        "• Доступ к отправке анкет и сообщений в боте ограничен.\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📞 <i>При возникновении вопросов:</i> <code>{CONFIG['HR_PHONE']}</code>"
    )
    sent = await send_response_to_candidate(platform, user_id, ban_user_msg)
    alert_text = "⛔ Пользователь добавлен в ЧС! Уведомление отправлено." if sent else "⛔ Добавлен в ЧС."
    await callback.answer(alert_text, show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=make_inquiry_admin_keyboard(inquiry_id))
    except Exception:
        pass


@hr_router.callback_query(F.data.startswith("inq_unblock_"))
async def cb_inq_unblock(callback: types.CallbackQuery):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.answer("🚫 Доступ ограничен кадровой службой!", show_alert=True)
    inquiry_id = int(callback.data.split("_")[2])
    inq = db.get_inquiry(inquiry_id)
    if not inq:
        return await callback.answer("Обращение не найдено!", show_alert=True)
    platform = inq[2]
    user_id = str(inq[3])
    full_name = inq[5] or f"ID {user_id}"
    db.unblock_user(user_id)

    from keyboards import make_candidate_main_keyboard
    unban_user_msg = (
        "✅ <b>Ограничение доступа снято</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"Здравствуйте, <b>{html.escape(full_name)}</b>!\n\n"
        "Блокировка вашего аккаунта в сервисе МУП «Ульяновскэлектротранс» была снята отделом кадров.\n\n"
        "Вы снова можете пользоваться ботом, задавать вопросы и подавать анкеты на вакансии предприятия.\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    sent = await send_response_to_candidate(platform, user_id, unban_user_msg, keyboard=make_candidate_main_keyboard())
    alert = "✅ Блокировка снята! Оповещение отправлено." if sent else "✅ Блокировка снята!"
    await callback.answer(alert, show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=make_inquiry_admin_keyboard(inquiry_id))
    except Exception:
        pass


@hr_router.callback_query(F.data == "toggle_dm_notify")
async def cb_toggle_dm_notify(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    new_state = db.toggle_admin_notify(user_id)
    status_str = "ВКЛЮЧЕНЫ 🔔" if new_state else "ВЫКЛЮЧЕНЫ 🔕"
    await callback.answer(f"Уведомления в ЛС: {status_str}")
    try:
        await callback.message.edit_reply_markup(reply_markup=make_admin_menu_keyboard(user_id))
    except Exception:
        pass


@hr_router.callback_query(F.data == "hr_toggle_cooldown")
async def cb_hr_toggle_cooldown(callback: types.CallbackQuery):
    allowed, err_text = check_hr_access_or_block(callback.from_user.id, callback.message.chat.id)
    if not allowed:
        return await callback.answer(err_text or "🚫 Нет прав!", show_alert=True)

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


@hr_router.message(Command("block", "ban"))
async def cmd_block_user(message: types.Message):
    """Блокировка пользователя в боте (добавление в ЧС с полным оповещением и закрытием заявок)."""
    user_id = message.from_user.id
    if not is_tech_admin(user_id) and not is_hr_admin(user_id):
        return await safe_answer(message, "🚫 Доступ ограничен.")

    parts = message.text.strip().split(maxsplit=2)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        return await safe_answer(
            message,
            "ℹ️ <b>Формат команды блокировки:</b>\n\n"
            "<code>/ban ID_пользователя [причина]</code>\n"
            "<i>(Например: <code>/ban 123456789 Спам и нецензурная брань</code>)</i>",
            parse_mode="HTML"
        )
    target_id = parts[1]
    reason = parts[2] if len(parts) > 2 else "Блокировка администратором"
    super_uid = str(CONFIG.get("SUPER_ADMIN_ID"))
    tech_uid = str(CONFIG.get("TECH_ADMIN_ID"))

    if target_id in (super_uid, tech_uid) or (target_id.isdigit() and (is_tech_admin(int(target_id)) or is_hr_admin(int(target_id)))):
        return await safe_answer(message, "🚫 Нельзя добавить администратора в черный список!", parse_mode="HTML")

    # Прерываем прямой диалог, аннулируем анкеты и обращения, добавляем в ЧС
    db.block_user_and_clean(target_id, reason=reason)

    # Отправляем уведомление соискателю в Telegram
    ban_user_msg = (
        "⛔ <b>Уведомление об ограничении доступа</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Здравствуйте!\n\n"
        "Информируем вас о том, что ваш аккаунт внесён в <b>чёрный список</b> "
        "информационной системы МУП «Ульяновскэлектротранс».\n\n"
        f"📋 <b>Причина:</b> <i>{html.escape(reason)}</i>\n\n"
        "• Все ваши активные заявки и обращения аннулированы.\n"
        "• Прямой диалог с кадровой службой прекращен.\n"
        "• Доступ к отправке анкет и сообщений в боте ограничен.\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📞 <i>При возникновении вопросов:</i> <code>{CONFIG['HR_PHONE']}</code>"
    )
    cand_sent = await send_response_to_candidate("tg", str(target_id), ban_user_msg)
    cand_note = "✅ Соискатель получил уведомление в ЛС." if cand_sent else "⚠️ Соискатель не получил уведомление (бот заблокирован или диалог не начат)."

    await safe_answer(
        message,
        f"⛔ <b>Пользователь <code>{target_id}</code> успешно заблокирован!</b>\n\n"
        f"📋 <b>Причина:</b> <i>{html.escape(reason)}</i>\n"
        f"• Активные анкеты пользователя переведены в статус «Отказ (ЧС)».\n"
        f"• Прямые диалоги и обращения закрыты.\n"
        f"• {cand_note}\n\n"
        f"<i>Для разблокировки используйте: <code>/unban {target_id}</code></i>",
        parse_mode="HTML"
    )


@hr_router.message(Command("unblock", "unban"))
async def cmd_unblock_user(message: types.Message):
    user_id = message.from_user.id
    if not is_tech_admin(user_id) and not is_hr_admin(user_id):
        return await safe_answer(message, "🚫 Доступ ограничен.")

    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        return await safe_answer(
            message,
            "ℹ️ <b>Формат разблокировки пользователя:</b>\n\n"
            "<code>/unblock ID_пользователя</code>\n"
            "<i>(Например: <code>/unblock 7657422832</code>)</i>",
            parse_mode="HTML"
        )
    target_id = parts[1]
    ok = db.unblock_user(target_id)
    if ok:
        from keyboards import make_candidate_main_keyboard
        unban_user_msg = (
            "✅ <b>Ограничение доступа снято</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Здравствуйте!\n\n"
            "Блокировка вашего аккаунта в сервисе МУП «Ульяновскэлектротранс» была снята отделом кадров.\n\n"
            "Вы снова можете пользоваться ботом, задавать вопросы и подавать анкеты на вакансии предприятия.\n"
            "━━━━━━━━━━━━━━━━━━━━━"
        )
        cand_sent = await send_response_to_candidate("tg", str(target_id), unban_user_msg, keyboard=make_candidate_main_keyboard())
        cand_note = "✅ Соискателю отправлено оповещение в ЛС." if cand_sent else "⚠️ Соискатель не получил оповещение в ЛС (диалог не начат)."
        await safe_answer(
            message,
            f"✅ <b>Пользователь <code>{target_id}</code> успешно разблокирован!</b>\n\n"
            f"• Исключен из черного списка.\n"
            f"• {cand_note}",
            parse_mode="HTML"
        )
    else:
        await safe_answer(message, f"ℹ️ Пользователь <code>{target_id}</code> не найден в черном списке.", parse_mode="HTML")

@hr_router.message(Command("blacklist", "banlist"))
async def cmd_blacklist(message: types.Message):
    user_id = message.from_user.id
    if not is_tech_admin(user_id) and not is_hr_admin(user_id):
        return await safe_answer(message, "🚫 Доступ ограничен.")

    b_list = db.get_blacklist()
    if not b_list:
        return await safe_answer(message, "🕊 <b>Черный список пуст.</b> Заблокированных пользователей нет.", parse_mode="HTML")

    text = "⛔ <b>ЧЕРНЫЙ СПИСОК (БЛОКИРОВКИ):</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
    builder = InlineKeyboardBuilder()
    for uid, reason, b_date in b_list[:15]:
        text += f"• <code>{uid}</code> | <i>{reason}</i> ({b_date})\n"
        builder.button(text=f"✅ Разблокировать {uid}", callback_data=f"unblock_raw_{uid}")

    builder.adjust(1)
    text += "\n━━━━━━━━━━━━━━━━━━━━━\n<i>Для разблокировки нажмите кнопку ниже или введите:</i> <code>/unblock ID</code>"
    await safe_answer(message, text, reply_markup=builder.as_markup(), parse_mode="HTML")

@hr_router.callback_query(F.data.startswith("unblock_raw_"))
async def cb_unblock_raw(callback: types.CallbackQuery):
    target_uid = callback.data.replace("unblock_raw_", "")
    db.unblock_user(target_uid)
    await callback.answer(f"Пользователь {target_uid} разблокирован!", show_alert=True)
    b_list = db.get_blacklist()
    if not b_list:
        return await callback.message.edit_text("🕊 <b>Черный список пуст.</b> Все пользователи разблокированы.", parse_mode="HTML")
    
    builder = InlineKeyboardBuilder()
    text = "⛔ <b>ЧЕРНЫЙ СПИСОК (БЛОКИРОВКИ):</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
    for uid, reason, b_date in b_list[:15]:
        text += f"• <code>{uid}</code> | <i>{reason}</i> ({b_date})\n"
        builder.button(text=f"✅ Разблокировать {uid}", callback_data=f"unblock_raw_{uid}")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@hr_router.message(Command("set_group"))
async def cmd_set_group(message: types.Message, bot: Bot):
    user_id = message.from_user.id
    super_id = CONFIG.get("SUPER_ADMIN_ID")
    is_admin_user = (user_id == super_id) or is_tech_admin(user_id) or is_hr_admin(user_id)

    if not is_admin_user:
        return await message.reply("🚫 Только администраторы бота могут привязывать группу отдела кадров.")

    parts = message.text.strip().split()
    target_group_id = None
    
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
            return await message.reply(
                "ℹ️ <b>КАК ПРИВЯЗАТЬ ГРУППУ ДЛЯ АНКЕТ:</b>\n\n"
                "1️⃣ <b>Внутри группы:</b> Добавьте бота в чат отдела кадров, дайте права администратора и напишите там команду <code>/set_group</code>\n"
                "2️⃣ <b>Из личного диалога:</b> Отправьте команду с ID группы: <code>/set_group -100XXXXXXXXXX</code>\n\n"
                "<i>(Чтобы узнать ID группы, добавьте бота в чат и отправьте там команду /id)</i>",
                parse_mode="HTML"
            )

    try:
        await bot.send_message(
            chat_id=target_group_id,
            text="🔔 <b>ТЕСТ СВЯЗИ:</b> Чат успешно привязан к боту МУП «Ульяновскэлектротранс»! Все анкеты кандидатов будут поступать сюда.",
            parse_mode="HTML"
        )
    except Exception as e:
        return await message.reply(
            f"❌ <b>ОШИБКА ПРИВЯЗКИ ЧАТА {target_group_id}:</b>\n\n"
            f"Telegram ответил: <code>{e}</code>\n\n"
            "⚠️ <b>Что нужно проверить:</b>\n"
            "1. Бот добавлен в этот чат?\n"
            "2. Вы назначили бота <b>Администратором</b> чата?\n"
            "3. Правильно ли скопирован ID (со знаком минус в начале)?",
            parse_mode="HTML"
        )

    CONFIG["HR_GROUP_ID"] = target_group_id
    if target_group_id not in CONFIG["TARGET_CHATS"]:
        CONFIG["TARGET_CHATS"].append(target_group_id)
    
    db.set_setting("hr_group_id", str(target_group_id))
    
    env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(env_file):
        env_file = ".env"
    if os.path.exists(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                env_text = f.read()
            if re.search(r"^HR_GROUP_ID=.*$", env_text, flags=re.MULTILINE):
                env_text = re.sub(r"^HR_GROUP_ID=.*$", f"HR_GROUP_ID={target_group_id}", env_text, flags=re.MULTILINE)
            else:
                env_text += f"\nHR_GROUP_ID={target_group_id}\n"
            with open(env_file, "w", encoding="utf-8") as f:
                f.write(env_text)
        except Exception as e:
            pass

    await message.reply(
        f"🎯 <b>Кадровая группа успешно проверена и привязана!</b>\n\n"
        f"• ID чата: <code>{target_group_id}</code>\n"
        f"• Тестовое сообщение доставлено в чат.\n\n"
        f"✅ <i>Все анкеты кандидатов теперь поступают в этот чат!</i>",
        parse_mode="HTML"
    )

@hr_router.message(StateFilter(None), F.photo)
async def process_live_dialog_photo(message: types.Message, state: FSMContext, bot: Bot):
    current_state = await state.get_state()
    if current_state is not None:
        return

    sender_id = message.from_user.id
    op_dlg = db.get_dialog_by_operator(message.chat.id) or db.get_dialog_by_operator(sender_id)
    if not op_dlg:
        return

    cand_user_id = op_dlg[0]
    caption = message.caption or ""

    try:
        import io
        photo = message.photo[-1]
        file_obj = io.BytesIO()
        await bot.download(photo.file_id, destination=file_obj)
        file_bytes = file_obj.getvalue()

        from common import send_photo_to_candidate
        ok = await send_photo_to_candidate(cand_user_id, file_bytes, caption=caption)
        if ok:
            await safe_answer(message, "✅ <i>Фотография успешно доставлена соискателю</i>", parse_mode="HTML")
        else:
            await safe_answer(message, "⚠️ Не удалось доставить фото соискателю.")
    except Exception as e:
        logger.error(f"Ошибка пересылки фото от кадровика: {e}")
        await safe_answer(message, f"❌ Ошибка пересылки фото: {e}")


@hr_router.message(Command("export"))
@hr_router.callback_query(F.data == "hr_export_excel")
async def process_export_candidates(event: types.Message | types.CallbackQuery):
    user_id = event.from_user.id
    if not is_hr_admin(user_id) and not is_tech_admin(user_id) and user_id != CONFIG.get("SUPER_ADMIN_ID"):
        if isinstance(event, types.CallbackQuery):
            return await event.answer("🚫 Нет прав!", show_alert=True)
        return await safe_answer(event, "🚫 Доступ ограничен.")

    if isinstance(event, types.CallbackQuery):
        await event.answer("⏳ Формирую файл...")

    candidates = db.get_all_candidates_for_export()
    if not candidates:
        msg = "ℹ️ В базе пока нет анкет для выгрузки."
        if isinstance(event, types.CallbackQuery):
            return await event.message.answer(msg)
        return await safe_answer(event, msg)

    import csv, io
    from aiogram.types import BufferedInputFile
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "ID", "Дата подачи", "ФИО", "Телефон", "Вакансия", 
        "Опыт работы", "Статус", "Заметка HR", "Платформа", "ID пользователя"
    ])

    for c in candidates:
        row = [
            str(c[0]),                             # ID
            str(c[9] if len(c) > 9 and c[9] else ""),       # Дата подачи
            str(c[3] if len(c) > 3 and c[3] else ""),       # ФИО
            str(c[4] if len(c) > 4 and c[4] else ""),       # Телефон
            str(c[5] if len(c) > 5 and c[5] else ""),       # Вакансия
            str(c[6] if len(c) > 6 and c[6] else ""),       # Опыт работы
            str(c[7] if len(c) > 7 and c[7] else ""),       # Статус
            str(c[8] if len(c) > 8 and c[8] else ""),       # Заметка HR
            str(c[1] if len(c) > 1 and c[1] else "").upper(), # Платформа (TG / VK / MAX)
            str(c[2] if len(c) > 2 and c[2] else ""),       # ID пользователя
        ]
        writer.writerow(row)

    file_bytes = output.getvalue().encode("utf-8-sig")
    filename = f"candidates_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    doc = BufferedInputFile(file_bytes, filename=filename)

    target = event.message if isinstance(event, types.CallbackQuery) else event
    await target.answer_document(
        document=doc,
        caption=f"📊 <b>Выгрузка базы соискателей</b> (Записей: {len(candidates)})",
        parse_mode="HTML"
    )



# =====================================================================
# АВТО-УПРАВЛЕНИЕ РОЛЯМИ В КАДРОВОМ ЧАТЕ (ВХОД, ВЫХОД, СООБЩЕНИЯ)
# =====================================================================

async def grant_hr_role_and_welcome(user: types.User, chat_id: int, bot: Bot) -> None:
    """Выдаёт роль HR в базе и отправляет приветствие с тегом по нику."""
    if user.is_bot or user.id == CONFIG.get("SUPER_ADMIN_ID"):
        return

    # Записываем в базу роль hr
    db.unblock_user(user.id)
    db.add_admin(user.id, role="hr")

    name_escaped = html.escape(user.full_name or "Сотрудник")
    user_tag = f"@{user.username}" if user.username else name_escaped

    welcome_msg = (
        f"👋 <b>Добро пожаловать в кадровую службу, {user_tag}!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Вам <b>автоматически открыт доступ</b> к кадровой панели: <code>/admin</code>.\n\n"
        f"💡 <i>Чтобы бот мог пересылать вам анкеты соискателей в личные сообщения, "
        f"откройте диалог с ботом и нажмите <b>/start</b>.</i>"
    )
    await safe_send(bot, chat_id, welcome_msg)


# 1. Когда сотрудника ДОБАВИЛИ в группу
@hr_router.message(F.new_chat_members)
async def on_hr_user_added(message: types.Message, bot: Bot):
    hr_group = CONFIG.get("HR_GROUP_ID")
    if hr_group and message.chat.id == hr_group:
        for new_user in message.new_chat_members:
            await grant_hr_role_and_welcome(new_user, message.chat.id, bot)


# 2. Когда сотрудник САМ ЗАШЁЛ по ссылке-приглашению
@hr_router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_hr_user_joined(event: types.ChatMemberUpdated, bot: Bot):
    hr_group = CONFIG.get("HR_GROUP_ID")
    if hr_group and event.chat.id == hr_group:
        await grant_hr_role_and_welcome(event.new_chat_member.user, hr_group, bot)


# 3. Для тех, кто УЖЕ в группе (выдаёт роль при первом отправленном сообщении)
@hr_router.message(F.chat.type.in_({"group", "supergroup"}))
async def on_existing_hr_member_message(message: types.Message, bot: Bot):
    hr_group = CONFIG.get("HR_GROUP_ID")
    if not hr_group or message.chat.id != hr_group or message.from_user.is_bot:
        return

    user_id = message.from_user.id
    if user_id != CONFIG.get("SUPER_ADMIN_ID"):
        if db.get_admin_role(user_id) != "hr":
            await grant_hr_role_and_welcome(message.from_user, hr_group, bot)


# 4. Когда сотрудника КИКНУЛИ или он САМ ВЫШЕЛ — роль снимается из базы
@hr_router.chat_member(ChatMemberUpdatedFilter(member_status_changed=LEAVE_TRANSITION))
async def on_hr_member_left(event: types.ChatMemberUpdated, bot: Bot):
    hr_group = CONFIG.get("HR_GROUP_ID")
    super_admin = CONFIG.get("SUPER_ADMIN_ID")
    if not hr_group or event.chat.id != hr_group:
        return

    user = event.old_chat_member.user
    if user.is_bot or user.id == super_admin:
        return

    # Моментально отзываем роль HR из базы данных
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
        f"🔒 <b>Роль HR аннулирована в базе данных.</b> Доступ к <code>/admin</code> закрыт."
    )


# 5. Быстрый кик администратором чата через команду /kick (ответом на сообщение)
@hr_router.message(Command("kick", "kick_hr"))
async def cmd_kick_hr_reply(message: types.Message, bot: Bot):
    hr_group = CONFIG.get("HR_GROUP_ID")
    super_admin = CONFIG.get("SUPER_ADMIN_ID")
    user_id = message.from_user.id

    if user_id != super_admin and not is_tech_admin(user_id):
        try:
            m = await bot.get_chat_member(message.chat.id, user_id)
            if not isinstance(m, (ChatMemberAdministrator, ChatMemberOwner)):
                return await safe_answer(message, "🚫 Исключать сотрудников могут только администраторы чата.")
        except Exception:
            return await safe_answer(message, "🚫 Недостаточно прав.")

    if not message.reply_to_message or not message.reply_to_message.from_user:
        return await safe_answer(message, "ℹ️ Ответьте командой <code>/kick</code> на сообщение сотрудника в группе.", parse_mode="HTML")

    target = message.reply_to_message.from_user
    if target.is_bot or target.id == super_admin:
        return await safe_answer(message, "🚫 Нельзя исключить данного пользователя.")

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
        f"Роль HR удалена из базы, доступ к панели <code>/admin</code> аннулирован.",
        parse_mode="HTML"
    )


# =====================================================================
# 6. ДВУХСТОРОННЯЯ СИНХРОНИЗАЦИЯ (/sync): ВЫДАЕТ И ЗАБИРАЕТ РОЛИ
# =====================================================================

@hr_router.message(Command("sync", "sync_group"))
@hr_router.callback_query(F.data == "hr_sync_all_in_one")
async def cmd_sync_group_two_way(event: types.Message | types.CallbackQuery, bot: Bot):
    user_id = event.from_user.id
    chat_id = event.message.chat.id if isinstance(event, types.CallbackQuery) else event.chat.id
    allowed, err = check_hr_access_or_block(user_id, chat_id)
    if not allowed:
        return await safe_answer(event, err or "🚫 Доступ ограничен.")

    hr_group = CONFIG.get("HR_GROUP_ID", 0)
    super_admin = CONFIG.get("SUPER_ADMIN_ID", 0)

    if not hr_group:
        return await safe_answer(event, "⚠️ Кадровая группа не привязана! Используйте <code>/set_group</code> в группе.")

    try:
        # Проверяем бота в группе
        bot_member = await bot.get_chat_member(hr_group, bot.id)
        if not isinstance(bot_member, (ChatMemberAdministrator, ChatMemberOwner)):
            return await safe_answer(event, "⚠️ <b>Бот не является администратором группы кадров!</b>\nВыдайте боту права администратора в чате.")

        # 1. Получаем список действующих администраторов группы
        group_admins = await bot.get_chat_administrators(hr_group)
        current_admin_ids = {a.user.id for a in group_admins if not a.user.is_bot}

        # 2. Получаем всех, кто сейчас записан в базе с ролью HR
        db_hr_ids = [adm_id for adm_id, role in db.get_all_admins() if role == "hr"]

        active_list = []
        added_list = []
        removed_list = []

        # А) Проверяем администраторов группы -> выдаём роль в базе
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

        # Б) Проверяем тех, кто в базе, но кого нет в группе -> ЗАБИРАЕМ РОЛЬ
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

        # Формируем наглядный отчет
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
