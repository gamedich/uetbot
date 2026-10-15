"""
Кадровая панель и воронка подбора персонала:
- Команда /hr, /admin, /kadry
- Интерактивный дашборд статистики воронки
- Фильтрация списков анкет (новые, в работе, архив, все)
"""

from __future__ import annotations

import html
import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from common import (
    check_hr_access_or_block,
    db,
    safe_answer,
)
from keyboards import make_admin_menu_keyboard

logger = logging.getLogger("HR_PANEL")
panel_router = Router(name="hr_panel")


@panel_router.message(Command("hr", "admin", "kadry"))
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


@panel_router.callback_query(F.data == "admin_stats")
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


@panel_router.callback_query(
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
            "🆕" if status == "Новая" else ("🟡" if status == "В работе" else ("🟢" if "Приглашен" in status else "🔴"))
        )
        text += (
            f"{icon} <b>#{t_id} [{plat.upper()}]</b> | {html.escape(name)}\n"
            f"└ <i>{html.escape(vac)}</i> (<b>{status}</b>)\n\n"
        )
        builder.button(text=f"Открыть #{t_id}", callback_data=f"view_{t_id}")

    builder.button(text="⬅️ Назад в меню", callback_data="admin_stats")
    builder.adjust(2)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@panel_router.callback_query(F.data == "hr_web_panel_info")
async def cb_hr_web_panel_info(callback: types.CallbackQuery) -> None:
    """Информация о доступе к веб-панели управления."""
    from config import CONFIG

    web_url = CONFIG.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")

    text = (
        "🌐 <b>ВЕБ-ПАНЕЛЬ МУП «УЛЬЯНОВСКЭЛЕКТРОТРАНС»</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 <b>Telegram Mini App (TMA):</b>\n"
        f"Адрес защищённого портала: <a href='{web_url}'>{web_url}</a>\n\n"
        "<i>Панель содержит интерактивную воронку кандидатов, быстрый поиск, "
        "историю заметок, фильтры и статус сервисов в реальном времени.</i>\n\n"
        "Нажмите кнопку ниже, чтобы открыть панель прямо внутри Telegram:"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🌐 Открыть Mini App (внутри TG)", web_app=types.WebAppInfo(url=web_url))
    builder.button(text="🖥 Открыть в обычном браузере", url=web_url)
    builder.button(text="⬅️ Назад в меню", callback_data="admin_stats")
    builder.adjust(1, 1, 1)
    await callback.message.edit_text(
        text, reply_markup=builder.as_markup(), parse_mode="HTML", disable_web_page_preview=True
    )
    await callback.answer()
