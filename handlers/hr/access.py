# -*- coding: utf-8 -*-
"""
Управление доступом, чатом кадровой службы, блокировками и настройками.
- Авто-выдача и снятие ролей HR при входе/выходе из группы
- /set_group привязка кадрового чата
- /sync двухсторонняя синхронизация состава группы
- /kick исключение нарушителей из чата
- Блокировка/разблокировка соискателей (/ban, /unban, /blacklist, cb_block_candidate)
- Переключатели уведомлений в ЛС и таймаута вопросов
"""

from __future__ import annotations

import html
import logging
from typing import List, Optional, Union

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.filters.chat_member_updated import (
    JOIN_TRANSITION,
    LEAVE_TRANSITION,
    ChatMemberUpdatedFilter,
)
from aiogram.types import (
    ChatMemberAdministrator,
    ChatMemberOwner,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import texts
from common import (
    CONFIG,
    check_hr_access_or_block,
    db,
    is_hr_admin,
    is_tech_admin,
    safe_answer,
    safe_send,
    send_response_to_candidate,
)
from config import update_env_variable
from keyboards import (
    make_admin_menu_keyboard,
    make_candidate_main_keyboard,
    make_ticket_keyboard,
)

logger = logging.getLogger("HR_ACCESS")
access_router = Router(name="hr_access")


# ==============================================================================
# 1. АВТО-УПРАВЛЕНИЕ РОЛЯМИ В КАДРОВОМ ЧАТЕ (ВХОД, ВЫХОД)
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


@access_router.message(F.new_chat_members)
async def on_hr_user_added(message: types.Message, bot: Bot) -> None:
    hr_group = CONFIG.get("HR_GROUP_ID")
    if hr_group and message.chat.id == hr_group:
        for new_user in message.new_chat_members:
            await grant_hr_role_and_welcome(new_user, message.chat.id, bot)


@access_router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_hr_user_joined(event: types.ChatMemberUpdated, bot: Bot) -> None:
    hr_group = CONFIG.get("HR_GROUP_ID")
    if hr_group and event.chat.id == hr_group:
        await grant_hr_role_and_welcome(event.new_chat_member.user, hr_group, bot)


@access_router.message(F.chat.type.in_({"group", "supergroup"}))
async def on_existing_hr_member_message(message: types.Message, bot: Bot) -> None:
    hr_group = CONFIG.get("HR_GROUP_ID")
    if not hr_group or message.chat.id != hr_group or message.from_user.is_bot:
        return

    user_id = message.from_user.id
    if user_id != CONFIG.get("SUPER_ADMIN_ID"):
        if db.get_admin_role(user_id) != "hr":
            await grant_hr_role_and_welcome(message.from_user, hr_group, bot)


@access_router.chat_member(ChatMemberUpdatedFilter(member_status_changed=LEAVE_TRANSITION))
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


# ==============================================================================
# 2. ПРИВЯЗКА ГРУППЫ И СИНХРОНИЗАЦИЯ
# ==============================================================================

@access_router.message(Command("kick", "kick_hr"))
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


@access_router.message(Command("set_group"))
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


@access_router.message(Command("sync", "sync_group"))
@access_router.callback_query(F.data == "hr_sync_all_in_one")
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
# 3. ЧЕРНЫЙ СПИСОК И БЛОКИРОВКИ
# ==============================================================================

@access_router.callback_query(F.data.startswith("block_cand_") | F.data.startswith("block_"))
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


@access_router.callback_query(F.data.startswith("unblock_cand_"))
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


@access_router.message(Command("block", "ban"))
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


@access_router.message(Command("unblock", "unban"))
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


@access_router.message(Command("blacklist", "banlist"))
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


@access_router.callback_query(F.data.startswith("unblock_raw_"))
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
# 4. НАСТРОЙКИ УВЕДОМЛЕНИЙ И КУЛДАУН
# ==============================================================================

@access_router.callback_query(F.data == "toggle_dm_notify")
async def cb_toggle_dm_notify(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    new_state = db.toggle_admin_notify(user_id)
    status_str = "ВКЛЮЧЕНЫ 🔔" if new_state else "ВЫКЛЮЧЕНЫ 🔕"
    await callback.answer(f"Уведомления в ЛС: {status_str}")
    try:
        await callback.message.edit_reply_markup(reply_markup=make_admin_menu_keyboard(user_id))
    except Exception:
        pass


@access_router.callback_query(F.data == "hr_toggle_cooldown")
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


@access_router.callback_query(F.data == "hr_mute_all_dm")
async def cb_hr_mute_all_dm(callback: types.CallbackQuery) -> None:
    super_id = CONFIG.get("SUPER_ADMIN_ID")
    if callback.from_user.id != super_id:
        await callback.answer("🚫 Это действие доступно только Главному администратору!", show_alert=True)
        return

    count = db.disable_all_hr_notifications()
    await callback.answer(
        f"🔕 Уведомления в ЛС принудительно отключены для всех кадровиков ({count} чел.)!\n"
        f"Анкеты теперь будут поступать строго в общую группу кадров.",
        show_alert=True,
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=make_admin_menu_keyboard(callback.from_user.id))
    except Exception:
        pass
