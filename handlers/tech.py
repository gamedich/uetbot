# -*- coding: utf-8 -*-
"""
Инженерный модуль технического обслуживания, мониторинга и тестирования:
- Команда /tech (чистый мониторинг систем, базы данных, статуса шлюзов, аптайма)
- Команда /tests (отдельная структурированная панель всех тестов)
- Отдельные команды:
    /test_tg   — тестовая анкета Telegram в кадры (16 шагов)
    /test_vk   — тестовая анкета ВКонтакте через шлюз
    /check_vk  — пинг и диагностика связи с API ВКонтакте
    /logs      — оперативный журнал логов прямо в Telegram
    /reset     — сброс тестовых анкет в базе
    /backup    — горячее резервное копирование SQLite
- Управление администраторами и ролями (/admins, /add_hr, /add_tech, /del_admin)
- Управление набором вакансий (открытие/закрытие, добавление, удаление)
- Перезапуск службы бота (/restart)
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from aiohttp import ClientSession, ClientTimeout
from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

import texts
from common import (
    CONFIG,
    SYSTEM_METRICS,
    AdminManageState,
    TechAccessMiddleware,
    db,
    get_all_vacancies,
    get_closed_vacancies,
    get_open_vacancies,
    get_vacancy_icon,
    grant_admin_and_sync,
    is_hr_admin,
    is_tech_admin,
    memory_log_handler,
    revoke_admin_and_sync,
    route_new_candidate_ticket,
    route_new_inquiry_ticket,
    safe_answer,
    safe_send,
    save_all_vacancies,
    set_closed_vacancies,
    sync_user_commands,
    update_env_mode,
    is_maintenance_active,
    set_maintenance_active,
)
from keyboards import (
    get_tech_screen_data,
    make_admins_menu_keyboard,
    make_inquiry_admin_keyboard,
    make_remove_admin_keyboard,
    make_tech_menu_keyboard,
    make_tests_menu_keyboard,
    make_ticket_keyboard,
)

logger = logging.getLogger("TECH_HANDLER")

# Инициализация инженерного роутера с middleware авторизации
tech_router = Router(name="tech")
tech_router.message.middleware(TechAccessMiddleware())
tech_router.callback_query.middleware(TechAccessMiddleware())


def is_privileged_user(user_id: int) -> bool:
    """Проверка прав: Главный администратор или Технический инженер (или TEST режим для HR)."""
    if user_id == CONFIG.get("SUPER_ADMIN_ID"):
        return True
    if is_tech_admin(user_id):
        return True
    if CONFIG.get("ENVIRONMENT") == "TEST" and is_hr_admin(user_id):
        return True
    return False


async def resolve_user_display(bot: Bot, user_id: int) -> Tuple[str, str]:
    """Возвращает кортеж: (полный текст с никнеймом, короткий текст для кнопки)."""
    try:
        chat = await bot.get_chat(user_id)
        name = html.escape(chat.full_name or "Сотрудник")
        if chat.username:
            return f"@{chat.username} ({name})", f"@{chat.username}"
        return f"{name} [ID: <code>{user_id}</code>]", name
    except Exception:
        return f"ID: <code>{user_id}</code>", f"ID: {user_id}"


async def run_shell_cmd(cmd: str) -> Tuple[int, str]:
    """Асинхронный запуск системных команд без блокировки Event Loop."""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    out = (stdout or b"").decode("utf-8", errors="replace").strip()
    err = (stderr or b"").decode("utf-8", errors="replace").strip()
    return proc.returncode or 0, out or err


# ==============================================================================
# 1. МОНИТОРИНГ И ИНЖЕНЕРНАЯ ПАНЕЛЬ (/tech)
# ==============================================================================

@tech_router.message(Command("tech"))
async def cmd_tech(message: types.Message) -> None:
    user_id = message.from_user.id
    if not is_privileged_user(user_id):
        text = (
            "🚫 <b>Доступ ограничен.</b> Команда доступна техническим инженерам и администраторам.\n\n"
            f"👤 <b>Ваш Telegram ID:</b> <code>{user_id}</code>\n"
            f"👑 <b>ID главного администратора:</b> <code>{CONFIG.get('SUPER_ADMIN_ID', 0)}</code>\n\n"
            "💡 <i>Если это ваш аккаунт, укажите его в файле <code>.env</code>:\n"
            f"<code>SUPER_ADMIN_ID={user_id}</code> и перезапустите бота.</i>"
        )
        await safe_answer(message, text, parse_mode="HTML")
        return

    SYSTEM_METRICS["tg_online"] = True
    screen_text, kb = get_tech_screen_data(user_id)
    await safe_answer(message, screen_text, reply_markup=kb, parse_mode="HTML")


@tech_router.message(Command("status", "refresh", "metrics"))
@tech_router.callback_query(F.data == "tech_refresh")
async def cb_tech_refresh(event: Union[types.Message, types.CallbackQuery]) -> None:
    user_id = event.from_user.id
    if not is_privileged_user(user_id):
        # Если соискатель вводит /status — показываем статус его заявки
        cand = db.get_candidate_by_user_id(str(user_id), platform="tg")
        if cand:
            from handlers.candidate import cb_cand_my_application
            await cb_cand_my_application(event)
            return
        if isinstance(event, types.CallbackQuery):
            await event.answer("🚫 Доступ ограничен!", show_alert=True)
            return
        await safe_answer(event, "🚫 Доступ ограничен.")
        return

    if isinstance(event, types.CallbackQuery):
        try:
            await event.answer("🔄 Метрики обновлены!")
        except Exception:
            pass

    SYSTEM_METRICS["tg_online"] = True
    screen_text, kb = get_tech_screen_data(user_id)

    if isinstance(event, types.CallbackQuery):
        try:
            await event.message.edit_text(screen_text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
    else:
        await safe_answer(event, screen_text, reply_markup=kb, parse_mode="HTML")


@tech_router.callback_query(F.data == "tech_toggle_env")
async def cb_tech_toggle_env(callback: types.CallbackQuery) -> None:
    if not is_privileged_user(callback.from_user.id):
        await callback.answer("🚫 Недостаточно прав!", show_alert=True)
        return

    current_env = CONFIG.get("ENVIRONMENT", "TEST")
    new_env = "PROD" if current_env == "TEST" else "TEST"
    update_env_mode(new_env)

    alert_msg = (
        "🛡 Активирован режим PROD!\nВключены строгие требования 152-ФЗ, 3 мес. отказ и антифлуд."
        if new_env == "PROD"
        else "🧪 Активирован режим TEST!\nВсе ограничения и лимиты сняты для отладки."
    )
    await callback.answer(alert_msg, show_alert=True)
    screen_text, kb = get_tech_screen_data(callback.from_user.id)
    try:
        await callback.message.edit_text(screen_text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
@tech_router.callback_query(F.data == "tech_toggle_db")
async def cb_tech_toggle_db(callback: types.CallbackQuery):
    if not is_privileged_user(callback.from_user.id):
        return await callback.answer("🚫 Недостаточно прав!", show_alert=True)

    cur_db = db.get_setting("active_db_target", "resumes.db")
    new_db = "resumes_test.db" if cur_db == "resumes.db" else "resumes.db"
    
    # Записываем выбор в базу и память
    db.set_setting("active_db_target", new_db)
    CONFIG["ACTIVE_DB_TARGET"] = new_db

    alert = (
        "🧪 Активирована ТЕСТОВАЯ база (resumes_test.db)!\nВсе тестовые анкеты будут изолированы с номерами #900000+."
        if new_db == "resumes_test.db" else
        "📁 Активирована БОЕВАЯ база (resumes.db)!\nВсе заявки сохраняются в официальную базу предприятия."
    )
    await callback.answer(alert, show_alert=True)

    # Обновляем экран /tech с новым статусом
    screen_text, kb = get_tech_screen_data(callback.from_user.id)
    try:
        await callback.message.edit_text(screen_text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass

@tech_router.callback_query(F.data == "tech_toggle_maint")
async def cb_tech_toggle_maint(callback: types.CallbackQuery) -> None:
    if not is_privileged_user(callback.from_user.id):
        await callback.answer("🚫 Нет прав доступа!", show_alert=True)
        return

    new_state = not is_maintenance_active()
    set_maintenance_active(new_state)

    status_str = "ВКЛЮЧЕН (прием анкет на паузе)" if new_state else "ВЫКЛЮЧЕН (штатная работа)"
    await callback.answer(f"Режим ТО: {status_str}", show_alert=True)
    screen_text, kb = get_tech_screen_data(callback.from_user.id)
    try:
        await callback.message.edit_text(screen_text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


# ==============================================================================
# УПРАВЛЕНИЕ РЕЗЕРВНЫМИ КОПИЯМИ (/backups, /backup, /restore)
# ==============================================================================

@tech_router.message(Command("backup"))
async def cmd_backup(message: types.Message) -> None:
    """Создание горячего бэкапа и отправка файла в чат Telegram."""
    if not is_privileged_user(message.from_user.id):
        await safe_answer(message, "🚫 Доступ ограничен администраторами.")
        return
    try:
        path = await asyncio.to_thread(db.backup_database)
        filename = os.path.basename(path)
        file_size_kb = round(os.path.getsize(path) / 1024, 1)
        caption = (
            "💾 <b>Резервная копия базы данных успешно создана:</b>\n\n"
            f"📁 <b>Файл:</b> <code>{filename}</code>\n"
            f"📦 <b>Размер:</b> <code>{file_size_kb} КБ</code>\n"
            f"📍 <b>Директория:</b> <code>backups/</code>\n\n"
            "<i>(Горячая копия создана в режиме WAL без блокировки чтения и записи).</i>"
        )
        try:
            doc = FSInputFile(path, filename=filename)
            await message.answer_document(doc, caption=caption, parse_mode="HTML")
        except Exception as send_err:
            logger.warning("Не удалось отправить файл документом: %s", send_err)
            await safe_answer(message, caption, parse_mode="HTML")
    except Exception as e:
        await safe_answer(message, f"❌ Ошибка резервного копирования: {e}")


def make_backups_menu_keyboard(backups_count: int) -> types.InlineKeyboardMarkup:
    """Формирует меню управления архивами."""
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Создать и скачать бэкап", callback_data="tech_backup_create_send")
    if backups_count > 0:
        builder.button(text="📥 Скачать последний бэкап", callback_data="tech_backup_download_latest")
        builder.button(text="♻️ Восстановить базу из бэкапа", callback_data="tech_backup_restore_list")
        builder.button(text="🧹 Очистить старые (оставить 5)", callback_data="tech_backup_cleanup_confirm")
    builder.button(text="⬅️ В главное меню /tech", callback_data="tech_refresh")
    builder.adjust(1)
    return builder.as_markup()


@tech_router.message(Command("backups"))
@tech_router.callback_query(F.data.in_(["tech_manage_backups", "tech_backup_db"]))
async def cmd_manage_backups(event: Union[types.Message, types.CallbackQuery]) -> None:
    """Центр управления резервными копиями: список, скачивание, откат и очистка."""
    user_id = event.from_user.id
    if not is_privileged_user(user_id):
        if isinstance(event, types.CallbackQuery):
            await event.answer("🚫 Нет прав!", show_alert=True)
            return
        await safe_answer(event, "🚫 Доступ ограничен администраторами.")
        return

    backups = await asyncio.to_thread(db.list_backups)
    total_count = len(backups)
    total_size_kb = sum(b.get("size_kb", 0) for b in backups)

    text = (
        "💾 <b>УПРАВЛЕНИЕ РЕЗЕРВНЫМИ КОПИЯМИ БАЗЫ ДАННЫХ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Всего бэкапов на сервере:</b> <code>{total_count}</code>\n"
        f"📦 <b>Общий объём архива:</b> <code>{round(total_size_kb, 1)} КБ</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
    )

    if not backups:
        text += "<i>Резервные копии ещё не создавались. Нажмите кнопку ниже, чтобы сделать первую копию.</i>"
    else:
        text += "<b>Доступные копии на сервере:</b>\n"
        for i, b in enumerate(backups[:8], 1):
            text += f"<b>{i}.</b> <code>{b['filename']}</code>\n   📅 {b['created_at']} | 📦 {b['size_kb']} КБ\n"

        if total_count > 8:
            text += f"\n<i>...и ещё {total_count - 8} более ранних копий.</i>\n"

    kb = make_backups_menu_keyboard(total_count)
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, text, reply_markup=kb, parse_mode="HTML")


@tech_router.callback_query(F.data == "tech_backup_create_send")
async def cb_tech_backup_create_send(callback: types.CallbackQuery) -> None:
    if not is_privileged_user(callback.from_user.id):
        await callback.answer("🚫 Нет прав!", show_alert=True)
        return
    await callback.answer("⏳ Создание бэкапа...")
    try:
        path = await asyncio.to_thread(db.backup_database)
        filename = os.path.basename(path)
        doc = FSInputFile(path, filename=filename)
        await callback.message.answer_document(
            doc,
            caption=f"💾 <b>Свежая резервная копия базы данных:</b>\n<code>{filename}</code>",
            parse_mode="HTML",
        )
        await cmd_manage_backups(callback)
    except Exception as e:
        logger.error("Сбой создания бэкапа: %s", e)
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)


@tech_router.callback_query(F.data == "tech_backup_download_latest")
async def cb_tech_backup_download_latest(callback: types.CallbackQuery) -> None:
    if not is_privileged_user(callback.from_user.id):
        await callback.answer("🚫 Нет прав!", show_alert=True)
        return
    backups = await asyncio.to_thread(db.list_backups)
    if not backups:
        await callback.answer("⚠️ Нет сохранённых бэкапов!", show_alert=True)
        return
    latest = backups[0]
    try:
        doc = FSInputFile(latest["path"], filename=latest["filename"])
        await callback.message.answer_document(
            doc,
            caption=f"📥 <b>Последний бэкап ({latest['created_at']}):</b>\n<code>{latest['filename']}</code>",
            parse_mode="HTML",
        )
        await callback.answer()
    except Exception as e:
        await callback.answer(f"❌ Ошибка отправки: {e}", show_alert=True)


@tech_router.message(Command("restore"))
async def cmd_restore(message: types.Message) -> None:
    """Команда отката базы данных к выбранному бэкапу (только для SUPER_ADMIN_ID)."""
    user_id = message.from_user.id
    if user_id != CONFIG.get("SUPER_ADMIN_ID"):
        await safe_answer(message, "🚫 Команда отката базы данных доступна только Главному администратору.")
        return
    backups = await asyncio.to_thread(db.list_backups)
    if not backups:
        await safe_answer(message, "⚠️ Нет доступных бэкапов для восстановления.")
        return
    text = (
        "♻️ <b>ВЫБЕРИТЕ КОПИЮ ДЛЯ ВОССТАНОВЛЕНИЯ БАЗЫ ДАННЫХ:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <i>Внимание: восстановление перезапишет текущую базу.\n"
        "Перед откатом бот автоматически сделает страховочную копию текущего состояния.</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите файл бэкапа:"
    )
    builder = InlineKeyboardBuilder()
    for b in backups[:6]:
        fn = b["filename"]
        short_label = fn.replace("resumes_backup_", "").replace(".db", "")
        builder.button(text=f"📁 {short_label} ({b['size_kb']} КБ)", callback_data=f"bkp_res_ask_{fn[:35]}")
    builder.button(text="❌ Отмена", callback_data="tech_manage_backups")
    builder.adjust(1)
    await safe_answer(message, text, reply_markup=builder.as_markup(), parse_mode="HTML")


@tech_router.callback_query(F.data == "tech_backup_restore_list")
async def cb_tech_backup_restore_list(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    if user_id != CONFIG.get("SUPER_ADMIN_ID"):
        await callback.answer("🚫 Откат базы данных разрешён только Главному администратору!", show_alert=True)
        return

    backups = await asyncio.to_thread(db.list_backups)
    if not backups:
        await callback.answer("⚠️ Нет бэкапов для восстановления!", show_alert=True)
        return

    text = (
        "♻️ <b>ВЫБЕРИТЕ КОПИЮ ДЛЯ ВОССТАНОВЛЕНИЯ БАЗЫ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <i>Внимание: восстановление перезапишет текущую базу данных. "
        "Перед откатом бот автоматически создаст страховочную копию текущего состояния.</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите файл бэкапа:"
    )
    builder = InlineKeyboardBuilder()
    for b in backups[:6]:
        fn = b["filename"]
        short_label = fn.replace("resumes_backup_", "").replace(".db", "")
        builder.button(text=f"📁 {short_label} ({b['size_kb']} КБ)", callback_data=f"bkp_res_ask_{fn[:35]}")
    builder.button(text="⬅️ Назад к бэкапам", callback_data="tech_manage_backups")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@tech_router.callback_query(F.data.startswith("bkp_res_ask_"))
async def cb_tech_restore_ask(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    if user_id != CONFIG.get("SUPER_ADMIN_ID"):
        await callback.answer("🚫 Откат разрешён только Главному администратору!", show_alert=True)
        return

    raw_fn = callback.data.replace("bkp_res_ask_", "")
    backups = await asyncio.to_thread(db.list_backups)
    matched = next((b["filename"] for b in backups if b["filename"].startswith(raw_fn)), None)
    if not matched:
        await callback.answer("❌ Файл не найден!", show_alert=True)
        return

    text = (
        "⚠️ <b>ПОДТВЕРЖДЕНИЕ ВОССТАНОВЛЕНИЯ БАЗЫ ДАННЫХ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"Вы собираетесь откатить базу к состоянию из файла:\n"
        f"📁 <code>{matched}</code>\n\n"
        "• Все текущие анкеты вернутся к состоянию на момент этой копии.\n"
        "• Бот создаст авто-бэкап текущей базы прямо перед откатом.\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Вы действительно уверены?</b>"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ ДА, ВОССТАНОВИТЬ БАЗУ", callback_data=f"bkp_res_do_{raw_fn}")
    builder.button(text="❌ Отмена", callback_data="tech_manage_backups")
    builder.adjust(1, 1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@tech_router.callback_query(F.data.startswith("bkp_res_do_"))
async def cb_tech_restore_do(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    if user_id != CONFIG.get("SUPER_ADMIN_ID"):
        await callback.answer("🚫 Доступ только для Главного администратора!", show_alert=True)
        return

    raw_fn = callback.data.replace("bkp_res_do_", "")
    backups = await asyncio.to_thread(db.list_backups)
    matched = next((b["filename"] for b in backups if b["filename"].startswith(raw_fn)), None)
    if not matched:
        await callback.answer("❌ Файл не найден!", show_alert=True)
        return

    await callback.answer("⏳ Выполняется восстановление...")
    ok, res_msg = await asyncio.to_thread(db.restore_database, matched)
    if ok:
        text = (
            "🎉 <b>БАЗА ДАННЫХ УСПЕШНО ВОССТАНОВЛЕНА!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"{res_msg}\n\n"
            "<i>(Система продолжает работу в штатном режиме).</i>"
        )
    else:
        text = f"❌ <b>Ошибка при восстановлении базы:</b>\n{res_msg}"

    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ К списку бэкапов", callback_data="tech_manage_backups")
    builder.button(text="⚙️ В инженерное меню /tech", callback_data="tech_refresh")
    builder.adjust(1, 1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@tech_router.callback_query(F.data == "tech_backup_cleanup_confirm")
async def cb_tech_backup_cleanup(callback: types.CallbackQuery) -> None:
    if not is_privileged_user(callback.from_user.id):
        await callback.answer("🚫 Нет прав!", show_alert=True)
        return
    deleted_cnt, _ = await asyncio.to_thread(db.cleanup_old_backups, 5)
    if deleted_cnt > 0:
        await callback.answer(f"🧹 Удалено старых копий: {deleted_cnt}. Оставлено 5 свежих бэкапов.", show_alert=True)
    else:
        await callback.answer("ℹ️ Старых копий нет. На диске 5 или меньше бэкапов.", show_alert=True)
    await cmd_manage_backups(callback)


@tech_router.callback_query(F.data == "tech_toggle_cooldown")
async def cb_tech_toggle_cooldown(callback: types.CallbackQuery) -> None:
    if not is_privileged_user(callback.from_user.id):
        await callback.answer("🚫 Нет прав!", show_alert=True)
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
    cd_label = f"{new_cd // 60} мин" if new_cd > 0 else "0 сек (без ограничений)"
    await callback.answer(f"Таймаут изменен на: {cd_label}", show_alert=True)
    screen_text, kb = get_tech_screen_data(callback.from_user.id)
    try:
        await callback.message.edit_text(screen_text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


@tech_router.message(Command("set_cooldown"))
async def cmd_set_cooldown(message: types.Message) -> None:
    if not is_privileged_user(message.from_user.id):
        await safe_answer(message, "🚫 Доступ ограничен администраторами.")
        return

    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        cur_cd = int(db.get_setting("cooldown_seconds", str(CONFIG.get("COOLDOWN_SECONDS", 1200)))) // 60
        await safe_answer(
            message,
            "ℹ️ <b>Управление таймаутом между вопросами (антифлуд):</b>\n\n"
            f"Текущий таймаут: <b>{cur_cd} мин.</b>\n\n"
            "Формат команды: <code>/set_cooldown МИНУТЫ</code>\n"
            "<i>(Например: <code>/set_cooldown 0</code> для тестов или <code>/set_cooldown 5</code>)</i>",
            parse_mode="HTML",
        )
        return

    minutes = int(parts[1])
    new_seconds = minutes * 60
    db.set_setting("cooldown_seconds", str(new_seconds))
    CONFIG["COOLDOWN_SECONDS"] = new_seconds
    await safe_answer(
        message,
        f"✅ Таймаут между вопросами соискателя установлен: <b>{minutes} мин.</b>",
        parse_mode="HTML",
    )


# ==============================================================================
# 2. ПАНЕЛЬ ТЕСТОВ И ДИАГНОСТИКИ (/tests)
# ==============================================================================

@tech_router.message(Command("tests", "test", "test_menu"))
@tech_router.callback_query(F.data == "test_menu_back")
async def cmd_tests_menu(event: Union[types.Message, types.CallbackQuery]) -> None:
    user_id = event.from_user.id
    if not is_privileged_user(user_id):
        if isinstance(event, types.CallbackQuery):
            await event.answer("🚫 Доступ только для администраторов!", show_alert=True)
            return
        await safe_answer(event, "🚫 Доступ ограничен администраторами.")
        return

    text = (
        "🧪 <b>ПАНЕЛЬ ТЕСТИРОВАНИЯ И ДИАГНОСТИКИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите тестовый сценарий для быстрой проверки:\n\n"
        "• <b>Анкета из TG</b> — симуляция подачи анкеты из Telegram в кадровую группу.\n"
        "• <b>Анкета из VK</b> — симуляция поступления анкеты через шлюз ВКонтакте.\n"
        "• <b>Тестовый вопрос</b> — отправка обращения соискателя кадровикам.\n"
        "• <b>Проверить шлюз ВК</b> — пинг серверов API ВКонтакте и тест подключения.\n"
        "• <b>Системные логи</b> — просмотр журнала логов прямо в чате.\n"
        "• <b>Сбросить тесты</b> — очистить свои тестовые заявки в базе (/reset).\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    kb = make_tests_menu_keyboard()
    if isinstance(event, types.CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await event.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, text, reply_markup=kb, parse_mode="HTML")


@tech_router.message(Command("changelog", "version"))
@tech_router.callback_query(F.data == "tech_changelog")
async def cmd_view_changelog(event: Union[types.Message, types.CallbackQuery]) -> None:
    changelog_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "CHANGELOG.md")
    version = CONFIG.get("BOT_VERSION", "1.7.0")
    header = f"🚀 <b>Бот v{version}</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
    if os.path.exists(changelog_path):
        try:
            with open(changelog_path, "r", encoding="utf-8") as f:
                preview = "\n".join(f.read().strip().splitlines()[:30])
            text = f"{header}<pre>{html.escape(preview)}</pre>"
        except Exception as e:
            text = f"{header}<i>Ошибка чтения: {e}</i>"
    else:
        text = f"{header}Версия: <code>v{version}</code>"
    target = event.message if isinstance(event, types.CallbackQuery) else event
    await safe_answer(target, text, parse_mode="HTML")


def generate_random_candidate(platform: str = "tg") -> Dict[str, Any]:
    """Генерирует полноценную тестовую анкету со всеми 16 шагами опросника."""
    names_m = [
        "Смирнов Алексей Сергеевич",
        "Васильев Дмитрий Андреевич",
        "Кузнецов Михаил Игоревич",
        "Морозов Артем Владимирович",
        "Федоров Илья Николаевич",
    ]
    names_f = [
        "Смирнова Анна Павловна",
        "Кузнецова Ольга Ивановна",
        "Васильева Елена Сергеевна",
        "Новикова Мария Александровна",
    ]
    fio = random.choice(names_m if random.random() > 0.3 else names_f)
    phone = f"+7927{random.randint(1000000, 9999999)}"

    vacs = get_all_vacancies()
    vac = random.choice(vacs) if vacs else "Водитель трамвая"
    exps = [
        "Без опыта работы, готов пройти обучение со стипендией.",
        "Водительский стаж категории B, C более 4 лет, хочу обучиться на трамвай.",
        "Опыт работы кондуктором 2 года в городском транспорте.",
        "Слесарь-ремонтник 4 разряда, стаж 5 лет, разбираюсь в подвижном составе.",
    ]
    cities = ["Ульяновск", "Димитровград", "Новоульяновск", "Барыш"]
    edus = [
        "Среднее специальное, Ульяновский электромеханический колледж",
        "Среднее, Школа № 25 г. Ульяновска",
        "Высшее, УлГТУ (Инженерный факультет)",
        "Среднее профессиональное, Автомеханический техникум",
    ]
    licenses = ["B, C", "B", "A, B", "Нет", "B, Трамвай"]
    sources = ["Объявление в трамвае", "Сайт предприятия", "ВКонтакте", "Центр занятости населения", "Знакомые"]

    b_year = random.randint(1975, 2004)
    b_month = random.randint(1, 12)
    b_day = random.randint(1, 28)
    birth_date = f"{b_day:02d}.{b_month:02d}.{b_year}"

    tag = "[ТЕСТ TG]" if platform == "tg" else "[ТЕСТ VK]"
    now_ts = datetime.now().strftime("%d.%m.%Y %H:%M")

    return {
        "full_name": f"{fio} {tag}",
        "birth_date": birth_date,
        "phone": phone,
        "city": random.choice(cities),
        "vacancy": vac,
        "driver_license": random.choice(licenses),
        "experience": random.choice(exps),
        "education": random.choice(edus),
        "relocation": random.choice(["Нет", "Да"]),
        "dormitory": random.choice(["Нет", "Да, нуждаюсь"]),
        "shift_work": "Да, готов к сменному графику 2/2",
        "medical_restrictions": "Нет",
        "criminal_record": "Нет (ст. 86 УК РФ)",
        "source": random.choice(sources),
        "extra_info": "Ответственный, без вредных привычек, готов приступить в ближайшее время.",
        "consent_timestamp": now_ts,
    }


@tech_router.message(Command("test_tg", "test_apply"))
@tech_router.callback_query(F.data == "test_send_tg")
async def cb_test_send_tg(event: Union[types.Message, types.CallbackQuery], bot: Bot) -> None:
    c = generate_random_candidate("tg")
    t_id = db.add_candidate(
        platform="tg",
        user_id=str(event.from_user.id),
        full_name=c["full_name"],
        phone=c["phone"],
        vacancy=c["vacancy"],
        experience=c["experience"],
        is_test=True,
        consent_timestamp=c["consent_timestamp"],
        birth_date=c["birth_date"],
        city=c["city"],
        driver_license=c["driver_license"],
        education=c["education"],
        relocation=c["relocation"],
        dormitory=c["dormitory"],
        shift_work=c["shift_work"],
        medical_restrictions=c["medical_restrictions"],
        criminal_record=c["criminal_record"],
        source=c["source"],
        extra_info=c["extra_info"],
    )
    cand = db.get_candidate(t_id)
    card = texts.format_hr_card_full(cand) if cand else f"🧪 ТЕСТОВАЯ АНКЕТА #{t_id}"
    await route_new_candidate_ticket(bot, card, reply_markup=make_ticket_keyboard(t_id))

    if isinstance(event, types.CallbackQuery):
        await event.answer("Тестовая анкета TG отправлена!")
    await safe_answer(event, f"✅ Сгенерирована тестовая анкета #{t_id} с 16 шагами ({c['vacancy']})", parse_mode="HTML")


@tech_router.message(Command("test_vk"))
@tech_router.callback_query(F.data == "test_send_vk")
async def cb_test_send_vk(event: Union[types.Message, types.CallbackQuery], bot: Bot) -> None:
    c = generate_random_candidate("vk")
    fake_uid = str(random.randint(100000000, 999999999))
    t_id = db.add_candidate(
        platform="vk",
        user_id=fake_uid,
        full_name=c["full_name"],
        phone=c["phone"],
        vacancy=c["vacancy"],
        experience=c["experience"],
        is_test=True,
        consent_timestamp=c["consent_timestamp"],
        birth_date=c["birth_date"],
        city=c["city"],
        driver_license=c["driver_license"],
        education=c["education"],
        relocation=c["relocation"],
        dormitory=c["dormitory"],
        shift_work=c["shift_work"],
        medical_restrictions=c["medical_restrictions"],
        criminal_record=c["criminal_record"],
        source=c["source"],
        extra_info=c["extra_info"],
    )
    cand = db.get_candidate(t_id)
    card = texts.format_hr_card_full(cand) if cand else f"🧪 ТЕСТОВАЯ АНКЕТА VK #{t_id}"
    await route_new_candidate_ticket(bot, card, reply_markup=make_ticket_keyboard(t_id))

    if isinstance(event, types.CallbackQuery):
        await event.answer("Тестовая анкета VK отправлена!")
    await safe_answer(event, f"✅ Сгенерирована тестовая анкета VK #{t_id} с 16 шагами ({c['vacancy']})", parse_mode="HTML")


RANDOM_QUESTIONS_POOL: List[str] = [
    "Здравствуйте! Подскажите, какой график работы у водителей трамвая и есть ли вечерняя развозка?",
    "Добрый день! Выплачивается ли стипендия во время обучения на водителя троллейбуса и сколько она составляет?",
    "Здравствуйте, предоставляется ли общежитие или компенсация жилья для иногородних сотрудников?",
    "У меня есть права категорий B и C. Можно ли переучиться на трамвай по ускоренной программе?",
    "Добрый день! Какая заработная плата у слесаря по ремонту подвижного состава 4 разряда?",
    "Здравствуйте, есть ли вакансии кондуктора с частичной занятостью или со сменным графиком 2 через 2?",
    "Подскажите, через сколько лет водители трамваев имеют право выйти на досрочную льготную пенсию?",
    "Добрый день! Нужен ли опыт работы для трудоустройства электромонтёром контактной сети?",
]


def generate_random_inquiry(platform: str = "tg") -> Dict[str, str]:
    """Генератор уникальных тестовых обращений с вопросами."""
    c = generate_random_candidate(platform)
    question = random.choice(RANDOM_QUESTIONS_POOL)
    return {
        "full_name": c["full_name"],
        "phone": c["phone"],
        "vacancy": c["vacancy"],
        "question": question,
    }


@tech_router.message(Command("test_inquiry", "test_question"))
@tech_router.callback_query(F.data == "test_send_inquiry")
async def cb_test_send_inquiry(event: Union[types.Message, types.CallbackQuery], bot: Bot) -> None:
    user_id = event.from_user.id
    if not is_privileged_user(user_id):
        await safe_answer(event, "🚫 Доступ ограничен.")
        return

    inq_data = generate_random_inquiry("tg")
    consent_ts = datetime.now().strftime("%d.%m.%Y %H:%M")
    inq_id = db.add_inquiry(
        platform="tg",
        user_id=str(user_id),
        question_text=inq_data["question"],
        full_name=inq_data["full_name"],
        phone=inq_data["phone"],
        vacancy=inq_data["vacancy"],
        is_test=True,
        consent_timestamp=consent_ts,
    )

    card_text = texts.format_inquiry_hr_card(
        inquiry_id=inq_id,
        platform="tg",
        full_name=inq_data["full_name"],
        phone=inq_data["phone"],
        vacancy=inq_data["vacancy"],
        question_text=inq_data["question"],
        consent_timestamp=consent_ts,
        is_test=True,
    )
    await route_new_inquiry_ticket(bot, card_text, reply_markup=make_inquiry_admin_keyboard(inq_id))

    resp = f"✅ <b>Сгенерировано тестовое обращение #{inq_id} (TG):</b>\n<i>«{html.escape(inq_data['question'])}»</i>"
    if isinstance(event, types.CallbackQuery):
        await event.answer("Вопрос отправлен!")
    await safe_answer(event, resp, parse_mode="HTML")


@tech_router.message(Command("test_inquiry_vk"))
@tech_router.callback_query(F.data == "test_send_inquiry_vk")
async def cb_test_send_inquiry_vk(event: Union[types.Message, types.CallbackQuery], bot: Bot) -> None:
    user_id = event.from_user.id
    if not is_privileged_user(user_id):
        await safe_answer(event, "🚫 Доступ ограничен.")
        return

    inq_data = generate_random_inquiry("vk")
    fake_vk_id = str(random.randint(100000000, 999999999))
    consent_ts = datetime.now().strftime("%d.%m.%Y %H:%M")
    inq_id = db.add_inquiry(
        platform="vk",
        user_id=fake_vk_id,
        question_text=inq_data["question"],
        full_name=inq_data["full_name"],
        phone=inq_data["phone"],
        vacancy=inq_data["vacancy"],
        is_test=True,
        consent_timestamp=consent_ts,
    )

    card_text = texts.format_inquiry_hr_card(
        inquiry_id=inq_id,
        platform="vk",
        full_name=inq_data["full_name"],
        phone=inq_data["phone"],
        vacancy=inq_data["vacancy"],
        question_text=inq_data["question"],
        consent_timestamp=consent_ts,
        is_test=True,
    )
    await route_new_inquiry_ticket(bot, card_text, reply_markup=make_inquiry_admin_keyboard(inq_id))

    resp = f"✅ <b>Сгенерировано тестовое обращение #{inq_id} (VK):</b>\n<i>«{html.escape(inq_data['question'])}»</i>"
    if isinstance(event, types.CallbackQuery):
        await event.answer("Вопрос VK отправлен!")
    await safe_answer(event, resp, parse_mode="HTML")


@tech_router.message(Command("check_vk", "vk_ping"))
@tech_router.callback_query(F.data == "test_ping_vk")
async def cb_test_ping_vk(event: Union[types.Message, types.CallbackQuery]) -> None:
    token = CONFIG.get("VK_GROUP_TOKEN", "")
    group_id = CONFIG.get("VK_GROUP_ID", "")

    if not token or not group_id:
        text = (
            "⚠️ <b>Шлюз ВКонтакте не настроен в .env!</b>\n\n"
            "• <code>VK_GROUP_TOKEN</code>: не задан\n"
            "• <code>VK_GROUP_ID</code>: не задан\n\n"
            "💡 Укажите ключи в файле <code>.env</code> и перезапустите бота."
        )
        if isinstance(event, types.CallbackQuery):
            await event.message.answer(text, parse_mode="HTML")
            await event.answer()
            return
        await safe_answer(event, text, parse_mode="HTML")
        return

    start_t = time.time()
    try:
        timeout = ClientTimeout(total=8)
        async with ClientSession(timeout=timeout) as session:
            url = "https://api.vk.com/method/groups.getById"
            params = {"group_id": group_id, "access_token": token, "v": "5.131"}
            async with session.get(url, params=params) as resp:
                data = await resp.json()

        latency = int((time.time() - start_t) * 1000)

        if "error" in data:
            err = data["error"]
            err_msg = err.get("error_msg", "Неизвестная ошибка")
            err_code = err.get("error_code", 0)
            text = (
                f"🔴 <b>ОШИБКА ПОДКЛЮЧЕНИЯ К VK API (Код {err_code}):</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ <i>{err_msg}</i>\n\n"
                f"⏱ Задержка: <code>{latency} мс</code>\n"
                "💡 <i>Проверьте права токена в группе (Управление сообществом + Сообщения).</i>"
            )
        else:
            group_info = data.get("response", [{}])[0]
            g_name = group_info.get("name", "Без названия")
            g_screen = group_info.get("screen_name", "")
            lp_status = "🟢 Онлайн" if SYSTEM_METRICS.get("vk_online") else "🟡 Подключается..."

            text = (
                "🟢 <b>ШЛЮЗ ВКОНТАКТЕ РАБОТАЕТ ИСПРАВНО!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏢 <b>Сообщество:</b> {html.escape(g_name)}\n"
                f"🌐 <b>Адрес:</b> https://vk.com/{g_screen} (ID: <code>{group_id}</code>)\n"
                f"📡 <b>Служба Long Poll:</b> <b>{lp_status}</b>\n"
                f"⏱ <b>Отклик API:</b> <code>{latency} мс</code>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "✅ Бот успешно принимает сообщения и анкеты соискателей из ВК."
            )
    except Exception as e:
        text = f"🔴 <b>Сетевой сбой при обращении к VK API:</b> <code>{html.escape(str(e))}</code>"

    if isinstance(event, types.CallbackQuery):
        await event.message.answer(text, parse_mode="HTML")
        await event.answer()
    else:
        await safe_answer(event, text, parse_mode="HTML")


@tech_router.callback_query(F.data == "tech_force_toggle_hr_dm")
async def cb_tech_force_toggle_hr_dm(callback: types.CallbackQuery, bot: Bot) -> None:
    if not is_privileged_user(callback.from_user.id):
        await callback.answer("🚫 Нет прав!", show_alert=True)
        return

    active_hrs = db.get_hr_admins_with_dm_enabled()
    hr_group = CONFIG.get("HR_GROUP_ID")

    if active_hrs:
        count = db.set_all_hr_notify_dm(0)
        notice_text = (
            "🔕 <b>Служебное уведомление:</b>\n"
            "Личные уведомления о новых анкетах в ЛС были <b>принудительно отключены</b> администратором системы.\n\n"
            "Все анкеты соискателей поступают в штатном режиме в этот кадровый чат.\n"
            "<i>(Если кому-то из сотрудников персонально требуются дубли в ЛС, их можно включить через /hr).</i>"
        )
        alert_msg = f"🔕 Уведомления в ЛС выключены для {count} сотрудников HR!"
    else:
        count = db.set_all_hr_notify_dm(1)
        notice_text = (
            "🔔 <b>Служебное уведомление:</b>\n"
            "Личные уведомления о новых анкетах в ЛС были <b>включены</b> администратором для всех сотрудников кадровой службы."
        )
        alert_msg = f"🔔 Уведомления в ЛС включены для {count} сотрудников HR!"

    if hr_group:
        try:
            await safe_send(bot, hr_group, notice_text)
        except Exception as e:
            logger.warning("Не удалось отправить уведомление в чат кадров: %s", e)

    await callback.answer(alert_msg, show_alert=True)
    await cmd_tests_menu(callback)


@tech_router.message(Command("logs", "log"))
@tech_router.callback_query(F.data == "tech_show_logs")
async def cmd_show_logs(event: Union[types.Message, types.CallbackQuery]) -> None:
    """Просмотр оперативного журнала событий прямо в Telegram."""
    user_id = event.from_user.id
    if not is_privileged_user(user_id):
        if isinstance(event, types.CallbackQuery):
            await event.answer("🚫 Доступ только для администраторов!", show_alert=True)
            return
        await safe_answer(event, "🚫 Доступ ограничен администраторами.")
        return

    if isinstance(event, types.CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass

    logs = memory_log_handler.get_logs(n=20)
    if not logs:
        text = "📋 <b>СИСТЕМНЫЕ ЛОГИ:</b>\n<i>Журнал пока пуст или бот только что запущен.</i>"
    else:
        log_content = html.escape("\n".join(logs))
        if len(log_content) > 2200:
            log_content = log_content[-2200:]
        text = (
            "📋 <b>ОПЕРАТИВНЫЙ ЖУРНАЛ СИСТЕМЫ (ПОСЛЕДНИЕ СОБЫТИЯ):</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"<pre>{log_content}</pre>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Ошибок с момента старта: <b>{SYSTEM_METRICS['errors_count']}</b>"
        )

    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить логи", callback_data="tech_show_logs")
    builder.button(text="⬅️ Меню тестов", callback_data="test_menu_back")
    builder.adjust(1, 1)

    if isinstance(event, types.CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        except Exception:
            await event.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await safe_answer(event, text, reply_markup=builder.as_markup(), parse_mode="HTML")


@tech_router.message(Command("reset"))
@tech_router.callback_query(F.data == "test_do_reset")
async def cb_test_do_reset(
    event: Union[types.Message, types.CallbackQuery],
    state: Optional[FSMContext] = None,
) -> None:
    """Безопасный сброс: удаляет ТОЛЬКО анкету текущего администратора-тестировщика."""
    if state:
        await state.clear()

    user_id = event.from_user.id
    if not is_privileged_user(user_id):
        if isinstance(event, types.CallbackQuery):
            await event.answer("🚫 Доступ только для технических инженеров!", show_alert=True)
            return
        await safe_answer(event, "🚫 Команда доступна только администраторам.", parse_mode="HTML")
        return

    try:
        db.backup_database()
    except Exception as e:
        logger.warning("Не удалось сделать автобэкап: %s", e)

    db.reset_candidate_for_test(str(user_id), platform="tg")
    db.reset_candidate_for_test(str(user_id), platform="vk")

    resp = (
        "🧹 <b>ВАША ТЕСТОВАЯ АНКЕТА СБРОШЕНА!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Очищены тестовые данные для вашего ID: <code>{user_id}</code>\n"
        "• Сняты блокировки на повторную подачу анкеты.\n"
        "• <b>Все остальные анкеты соискателей в базе сохранены!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Теперь вы можете отправить анкету заново для проверки.</i>"
    )
    if isinstance(event, types.CallbackQuery):
        await event.answer("Ваша анкета сброшена!", show_alert=True)
        try:
            await event.message.edit_text(resp, reply_markup=make_tests_menu_keyboard(), parse_mode="HTML")
        except Exception:
            await event.message.answer(resp, reply_markup=make_tests_menu_keyboard(), parse_mode="HTML")
    else:
        await safe_answer(event, resp, reply_markup=make_tests_menu_keyboard(), parse_mode="HTML")


# ==============================================================================
# 3. УПРАВЛЕНИЕ АДМИНИСТРАТОРАМИ (/admins)
# ==============================================================================

@tech_router.message(Command("admins", "team"))
async def cmd_admins_menu(message: types.Message, bot: Bot) -> None:
    user_id = message.from_user.id
    if user_id != CONFIG.get("SUPER_ADMIN_ID"):
        await safe_answer(message, "🚫 Управление сотрудниками и ролями доступно исключительно Главному администратору.")
        return

    all_admins = db.get_all_admins()
    super_id = CONFIG.get("SUPER_ADMIN_ID", 0)

    tasks = [resolve_user_display(bot, adm_id) for adm_id, _ in all_admins]
    resolved = await asyncio.gather(*tasks)
    owner_full, _ = await resolve_user_display(bot, super_id)

    text = (
        "👥 <b>УПРАВЛЕНИЕ СОТРУДНИКАМИ И РОЛЯМИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"👑 <b>Главный администратор:</b> {owner_full}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Текущий список сотрудников с доступом:</b>\n"
    )

    for (adm_id, role), (full_label, _) in zip(all_admins, resolved):
        if adm_id == super_id:
            text += f"• 👑 <b>{full_label}</b> — Владелец\n"
        elif role == "hr":
            text += f"• 📋 <b>{full_label}</b> — Кадры (HR)\n"
        elif role == "tech":
            text += f"• 🛠 <b>{full_label}</b> — Инженер (Tech)\n"
        else:
            text += f"• 👤 <b>{full_label}</b> — {role}\n"

    text += "━━━━━━━━━━━━━━━━━━━━━\n💡 <i>Нажмите кнопку ниже для выдачи или отзыва прав:</i>"
    await safe_answer(message, text, reply_markup=make_admins_menu_keyboard(all_admins), parse_mode="HTML")


@tech_router.callback_query(F.data == "adm_ui_refresh")
async def cb_adm_ui_refresh(callback: types.CallbackQuery, bot: Bot) -> None:
    if callback.from_user.id != CONFIG.get("SUPER_ADMIN_ID"):
        await callback.answer("🚫 Доступ только у Главного администратора!", show_alert=True)
        return
    all_admins = db.get_all_admins()
    super_id = CONFIG.get("SUPER_ADMIN_ID", 0)

    tasks = [resolve_user_display(bot, adm_id) for adm_id, _ in all_admins]
    resolved = await asyncio.gather(*tasks)
    owner_full, _ = await resolve_user_display(bot, super_id)

    text = (
        "👥 <b>УПРАВЛЕНИЕ СОТРУДНИКАМИ И РОЛЯМИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"👑 <b>Главный администратор:</b> {owner_full}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Текущий список сотрудников с доступом:</b>\n"
    )

    for (adm_id, role), (full_label, _) in zip(all_admins, resolved):
        if adm_id == super_id:
            text += f"• 👑 <b>{full_label}</b> — Владелец\n"
        elif role == "hr":
            text += f"• 📋 <b>{full_label}</b> — Кадры (HR)\n"
        elif role == "tech":
            text += f"• 🛠 <b>{full_label}</b> — Инженер (Tech)\n"

    text += "\n━━━━━━━━━━━━━━━━━━━━━\n💡 <i>Нажмите кнопку ниже для выдачи прав:</i>"
    try:
        await callback.message.edit_text(text, reply_markup=make_admins_menu_keyboard(all_admins), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("Список обновлен")


@tech_router.callback_query(F.data == "adm_ui_remove_list")
async def cb_adm_ui_remove_list(callback: types.CallbackQuery, bot: Bot) -> None:
    if callback.from_user.id != CONFIG.get("SUPER_ADMIN_ID"):
        await callback.answer("🚫 Отзывать права может только Главный администратор!", show_alert=True)
        return
    all_admins = db.get_all_admins()
    admins_with_names: List[Tuple[int, str, str]] = []
    for adm_id, role in all_admins:
        _, short_label = await resolve_user_display(bot, adm_id)
        admins_with_names.append((adm_id, role, short_label))

    text = (
        "➖ <b>ОТЗЫВ ПРАВ ДОСТУПА СОТРУДНИКА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите сотрудника для отзыва прав:"
    )
    await callback.message.edit_text(text, reply_markup=make_remove_admin_keyboard(admins_with_names), parse_mode="HTML")
    await callback.answer()


@tech_router.callback_query(F.data.startswith("adm_del_id_"))
async def cb_adm_del_id(callback: types.CallbackQuery, bot: Bot) -> None:
    if callback.from_user.id != CONFIG.get("SUPER_ADMIN_ID"):
        await callback.answer("🚫 Отзывать права может только Главный администратор!", show_alert=True)
        return
    adm_id = int(callback.data.replace("adm_del_id_", ""))
    super_id = CONFIG.get("SUPER_ADMIN_ID")
    if adm_id == super_id:
        await callback.answer("🚫 Нельзя удалить Главного администратора!", show_alert=True)
        return

    await revoke_admin_and_sync(bot, adm_id, reason="Отозвано Главным администратором")
    await callback.answer(f"Сотрудник {adm_id} удалён, права отозваны!", show_alert=True)
    all_admins = db.get_all_admins()
    tasks = [resolve_user_display(bot, aid) for aid, _ in all_admins]
    resolved = await asyncio.gather(*tasks)
    owner_full, _ = await resolve_user_display(bot, super_id or 0)

    text = (
        "👥 <b>УПРАВЛЕНИЕ СОТРУДНИКАМИ И РОЛЯМИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"👑 <b>Главный администратор:</b> {owner_full}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Текущий список сотрудников с доступом:</b>\n"
    )
    for (aid, role), (full_label, _) in zip(all_admins, resolved):
        if aid == super_id:
            text += f"• 👑 <b>{full_label}</b> — Владелец\n"
        elif role == "hr":
            text += f"• 📋 <b>{full_label}</b> — Кадры (HR)\n"
        elif role == "tech":
            text += f"• 🛠 <b>{full_label}</b> — Инженер (Tech)\n"

    try:
        await callback.message.edit_text(text, reply_markup=make_admins_menu_keyboard(all_admins), parse_mode="HTML")
    except Exception:
        pass


@tech_router.message(Command("add_hr"))
async def cmd_add_hr(message: types.Message, bot: Bot) -> None:
    if message.from_user.id != CONFIG.get("SUPER_ADMIN_ID"):
        await safe_answer(message, "🚫 Назначать сотрудников может только Главный администратор.")
        return

    new_id = None
    user_display = None

    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        new_id = u.id
        user_display = f"@{u.username} ({html.escape(u.full_name)})" if u.username else html.escape(u.full_name)
    else:
        digits = re.findall(r"\d+", message.text or "")
        if digits:
            digits.sort(key=len, reverse=True)
            new_id = int(digits[0])
            try:
                chat_info = await bot.get_chat(new_id)
                escaped_name = html.escape(chat_info.full_name or "Сотрудник")
                user_display = f"@{chat_info.username} ({escaped_name})" if chat_info.username else escaped_name
            except Exception:
                user_display = f"ID: <code>{new_id}</code>"

    if not new_id:
        await safe_answer(
            message,
            "ℹ️ <b>Формат:</b> <code>/add_hr TELEGRAM_ID</code> или ответьте на сообщение сотрудника в группе.",
            parse_mode="HTML",
        )
        return

    db.unblock_user(new_id)
    db.add_admin(new_id, role="hr")

    web_url = CONFIG.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")
    notify_text = (
        "🎉 <b>Вам выданы права доступа в боте МУП «Ульяновскэлектротранс»!</b>\n\n"
        "📋 <b>Роль:</b> <b>Специалист отдела кадров (HR)</b>\n"
        "• Вам доступна кадровая панель: <code>/hr</code>\n"
        "• Вы будете получать новые анкеты соискателей в личные сообщения.\n\n"
        f"🌐 <b>Служебная веб-панель:</b> <a href='{web_url}'>{web_url}</a>\n"
        "🔑 Для входа на сайт установите любой пароль командой: <code>/password ВАШ_ПАРОЛЬ</code>\n"
        "<i>(Или просто откройте панель прямо в Telegram командой /panel)</i>\n"
    )
    btn_kb = InlineKeyboardBuilder()
    btn_kb.button(text="🌐 Открыть Mini App (внутри TG)", web_app=types.WebAppInfo(url=web_url))
    sent = await safe_send(bot, new_id, notify_text, reply_markup=btn_kb.as_markup())
    note = "✅ Уведомление отправлено в ЛС." if sent else "⚠️ <i>Сотруднику нужно нажать /start в боте для получения анкет в ЛС.</i>"
    await sync_user_commands(bot, new_id, role="hr")

    await safe_answer(
        message,
        f"✅ <b>Сотрудник отдела кадров успешно назначен!</b>\n\n"
        f"👤 <b>Сотрудник:</b> {user_display}\n"
        "📋 <b>Роль:</b> HR-специалист\n"
        f"{note}",
        reply_markup=make_admins_menu_keyboard(db.get_all_admins()),
        parse_mode="HTML",
    )


@tech_router.message(Command("add_tech"))
async def cmd_add_tech(message: types.Message, bot: Bot) -> None:
    if message.from_user.id != CONFIG.get("SUPER_ADMIN_ID"):
        await safe_answer(message, "🚫 Назначать инженеров может только Главный администратор.")
        return

    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await safe_answer(
            message,
            "ℹ️ <b>Формат команды:</b> <code>/add_tech TELEGRAM_ID</code>\nНапример: <code>/add_tech 123456789</code>",
            parse_mode="HTML",
        )
        return

    new_id = int(parts[1])
    db.unblock_user(new_id)
    db.add_admin(new_id, role="tech")

    full_label, _ = await resolve_user_display(bot, new_id)

    web_url = CONFIG.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")
    notify_text = (
        "🛠 <b>Вам выданы права доступа в боте МУП «Ульяновскэлектротранс»!</b>\n\n"
        "🔧 <b>Роль:</b> <b>Технический инженер (Tech)</b>\n"
        "• Вам доступна панель мониторинга систем: <code>/tech</code>\n"
        "• Панель тестирования и диагностики: <code>/tests</code>\n"
        "• Системные логи: <code>/logs</code>\n"
        "• Резервное копирование базы данных: <code>/backup</code>\n\n"
        f"🌐 <b>Служебная веб-панель:</b> <a href='{web_url}'>{web_url}</a>\n"
        "🔑 Для входа на сайт установите любой пароль командой: <code>/password ВАШ_ПАРОЛЬ</code>\n\n"
        "<i>Отправьте /help для просмотра полного списка команд или /panel для открытия панели.</i>"
    )
    btn_kb = InlineKeyboardBuilder()
    btn_kb.button(text="🌐 Открыть Mini App (внутри TG)", web_app=types.WebAppInfo(url=web_url))
    sent = await safe_send(bot, new_id, notify_text, reply_markup=btn_kb.as_markup())
    note = "✅ Оповещение отправлено инженеру в ЛС." if sent else "⚠️ <i>Инженер назначен, но не получил сообщение в ЛС (нужно нажать /start в боте).</i>"
    await sync_user_commands(bot, new_id, role="tech")

    await safe_answer(
        message,
        f"✅ <b>Технический инженер успешно назначен!</b>\n\n"
        f"👤 <b>Инженер:</b> {full_label}\n"
        "🔧 <b>Роль:</b> Tech\n"
        f"{note}",
        reply_markup=make_admins_menu_keyboard(db.get_all_admins()),
        parse_mode="HTML",
    )


@tech_router.message(Command("add_depot"))
async def cmd_add_depot(message: types.Message, bot: Bot) -> None:
    if message.from_user.id != CONFIG.get("SUPER_ADMIN_ID"):
        await safe_answer(message, "🚫 Назначать сотрудников может только Главный администратор.")
        return

    parts = message.text.strip().split()
    new_id = None
    if len(parts) >= 2 and parts[1].isdigit():
        new_id = int(parts[1])
    elif message.reply_to_message and message.reply_to_message.from_user:
        new_id = message.reply_to_message.from_user.id

    if not new_id:
        await safe_answer(
            message,
            "ℹ️ <b>Формат команды:</b> <code>/add_depot TELEGRAM_ID</code>\n"
            "Например: <code>/add_depot 123456789</code> или ответом на сообщение сотрудника.",
            parse_mode="HTML",
        )
        return

    db.unblock_user(new_id)
    db.add_admin(new_id, role="depot")

    full_label, _ = await resolve_user_display(bot, new_id)
    web_url = CONFIG.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")

    notify_text = (
        "🚋 <b>Вам выданы права сотрудника депо в МУП «Ульяновскэлектротранс»!</b>\n\n"
        "⭐ <b>Роль:</b> <b>Сотрудник депо & Реферальная программа</b>\n"
        "• Ваша реферальная статистика и ссылки: <code>/ref</code>\n"
        "• Бонусная премия: <b>10 000 ₽</b> за каждого принятого кандидата!\n\n"
        f"🌐 <b>Личный кабинет депо (Mini App):</b> <a href='{web_url}'>{web_url}</a>\n"
        "🔑 Для входа на сайт установите любой пароль командой: <code>/password ВАШ_ПАРОЛЬ</code>\n"
        "<i>(Или просто откройте кабинет прямо в Telegram командой /panel)</i>\n"
    )
    btn_kb = InlineKeyboardBuilder()
    btn_kb.button(text="🌐 Открыть кабинет депо", web_app=types.WebAppInfo(url=web_url))
    sent = await safe_send(bot, new_id, notify_text, reply_markup=btn_kb.as_markup())
    note = "✅ Оповещение отправлено сотруднику в ЛС." if sent else "⚠️ <i>Сотрудник депо назначен, но не получил сообщение в ЛС (нужно нажать /start в боте).</i>"
    await sync_user_commands(bot, new_id, role="depot")

    await safe_answer(
        message,
        f"✅ <b>Сотрудник депо успешно назначен!</b>\n\n"
        f"👤 <b>Сотрудник:</b> {full_label}\n"
        "🚋 <b>Роль:</b> Депо (Реферальная программа)\n"
        f"{note}",
        reply_markup=make_admins_menu_keyboard(db.get_all_admins()),
        parse_mode="HTML",
    )


@tech_router.message(Command("del_admin", "rm_admin", "rm_hr"))
async def cmd_del_admin(message: types.Message, bot: Bot) -> None:
    if message.from_user.id != CONFIG.get("SUPER_ADMIN_ID"):
        await safe_answer(message, "🚫 Удалять сотрудников может только Главный администратор.")
        return

    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await safe_answer(message, "ℹ️ <b>Формат команды:</b> <code>/del_admin TELEGRAM_ID</code>", parse_mode="HTML")
        return

    adm_id = int(parts[1])
    super_id = CONFIG.get("SUPER_ADMIN_ID")
    if adm_id == super_id:
        await safe_answer(message, "🚫 Нельзя отозвать права у Главного администратора!")
        return

    await revoke_admin_and_sync(bot, adm_id, reason="Отозвано Главным администратором через команду /del_admin")

    await safe_answer(
        message,
        f"✅ <b>Права администратора для {adm_id} успешно отозваны!</b>\n"
        f"Сотрудник уведомлён в ЛС, выпадающее меню команд Telegram сброшено к соискательскому.",
        reply_markup=make_admins_menu_keyboard(db.get_all_admins()),
        parse_mode="HTML",
    )


@tech_router.message(Command("notify_panel", "panel_notify"))
async def cmd_notify_panel_broadcast(message: types.Message, bot: Bot) -> None:
    """Массовая отправка инструкций по доступу к веб-панели всем действующим сотрудникам."""
    if message.from_user.id != CONFIG.get("SUPER_ADMIN_ID"):
        await safe_answer(message, "🚫 Доступно только Главному администратору.")
        return

    all_admins = db.get_all_admins()
    super_id = CONFIG.get("SUPER_ADMIN_ID", 0)
    web_url = CONFIG.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")

    sent_count = 0
    btn_kb = InlineKeyboardBuilder()
    btn_kb.button(text="🌐 Открыть Mini App (внутри TG)", web_app=types.WebAppInfo(url=web_url))
    app_markup = btn_kb.as_markup()

    for adm_id, role in all_admins:
        if adm_id == super_id:
            continue
        role_label = "Отдел кадров (HR)" if role == "hr" else "Инженер (Tech)"
        text = (
            "🌐 <b>СЛУЖЕБНАЯ ВЕБ-ПАНЕЛЬ МУП «УЛЬЯНОВСКЭЛЕКТРОТРАНС»</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"Уважаемый сотрудник! Вам открыт доступ к рабочей веб-панели управления ({role_label}).\n\n"
            f"👉 <b>Адрес:</b> <a href='{web_url}'>{web_url}</a>\n\n"
            "🔑 <b>Как войти:</b>\n"
            "1. Задайте любой удобный пароль для входа, отправив команду боту:\n"
            "   👉 <code>/password ВАШ_ПАРОЛЬ</code> <i>(например: /password tram2026)</i>\n"
            "2. Перейдите по ссылке на сайт и введите ваш пароль.\n"
            "3. Или просто нажмите кнопку Mini App ниже прямо в Telegram!\n"
            "━━━━━━━━━━━━━━━━━━━━━"
        )
        if await safe_send(bot, adm_id, text, reply_markup=app_markup):
            sent_count += 1

    await safe_answer(
        message,
        f"✅ <b>Рассылка завершена!</b>\nИнструкции по входу в веб-панель отправлены {sent_count} сотрудникам.",
        parse_mode="HTML"
    )


# --- Кнопки интерфейса добавления через меню ---

@tech_router.callback_query(F.data == "adm_ui_add_hr")
async def cb_adm_ui_add_hr(callback: types.CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id != CONFIG.get("SUPER_ADMIN_ID"):
        await callback.answer("🚫 Назначать сотрудников может только Главный администратор!", show_alert=True)
        return
    await state.set_state(AdminManageState.waiting_hr_id)
    text = (
        "📋 <b>НАЗНАЧЕНИЕ СОТРУДНИКА ОТДЕЛА КАДРОВ (HR)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Введите цифровой <b>Telegram ID</b> сотрудника.\n\n"
        "<i>(Сотрудник может узнать свой ID, отправив команду /id боту).\n\n"
        "Либо просто ответьте командой /add_hr на сообщение сотрудника в группе!</i>\n\n"
        "Для отмены отправьте /cancel."
    )
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()


@tech_router.message(AdminManageState.waiting_hr_id)
async def process_add_hr_id(message: types.Message, state: FSMContext, bot: Bot) -> None:
    user_input = message.text.strip()
    if not user_input.isdigit():
        await safe_answer(message, "⚠️ ID должен состоять только из цифр. Попробуйте снова или отправьте /cancel:")
        return

    new_id = int(user_input)
    db.unblock_user(new_id)
    db.add_admin(new_id, role="hr")
    await state.clear()

    full_label, _ = await resolve_user_display(bot, new_id)

    web_url = CONFIG.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")
    notify_text = (
        "🎉 <b>Вам выданы права доступа в боте МУП «Ульяновскэлектротранс»!</b>\n\n"
        "📋 <b>Роль:</b> <b>Специалист отдела кадров (HR)</b>\n"
        "• Вам доступна кадровая панель: <code>/hr</code>\n"
        "• Вы будете получать новые анкеты соискателей в личные сообщения.\n\n"
        f"🌐 <b>Служебная веб-панель:</b> <a href='{web_url}'>{web_url}</a>\n"
        "🔑 Для входа на сайт установите любой пароль командой: <code>/password ВАШ_ПАРОЛЬ</code>\n"
        "<i>(Или просто откройте панель прямо в Telegram командой /panel)</i>\n"
    )
    btn_kb = InlineKeyboardBuilder()
    btn_kb.button(text="🌐 Открыть Mini App (внутри TG)", web_app=types.WebAppInfo(url=web_url))
    sent = await safe_send(bot, new_id, notify_text, reply_markup=btn_kb.as_markup())
    note = "✅ Уведомление отправлено в ЛС." if sent else "⚠️ <i>Сотруднику нужно нажать /start в боте для получения анкет в ЛС.</i>"
    await sync_user_commands(bot, new_id, role="hr")

    await safe_answer(
        message,
        f"✅ <b>Сотрудник отдела кадров успешно назначен!</b>\n\n"
        f"👤 <b>Сотрудник:</b> {full_label}\n"
        "📋 <b>Роль:</b> HR\n"
        f"{note}",
        reply_markup=make_admins_menu_keyboard(db.get_all_admins()),
        parse_mode="HTML",
    )


@tech_router.callback_query(F.data == "adm_ui_add_tech")
async def cb_adm_ui_add_tech(callback: types.CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id != CONFIG.get("SUPER_ADMIN_ID"):
        await callback.answer("🚫 Назначать инженеров может только Главный администратор!", show_alert=True)
        return
    await state.set_state(AdminManageState.waiting_tech_id)
    text = (
        "🛠 <b>НАЗНАЧЕНИЕ ТЕХНИЧЕСКОГО ИНЖЕНЕРА (TECH)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Введите цифровой <b>Telegram ID</b> инженера.\n\n"
        "<i>(Инженер получит доступ к командам /tech, /tests, /logs и /backup).</i>\n\n"
        "Для отмены отправьте /cancel."
    )
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()


@tech_router.message(AdminManageState.waiting_tech_id)
async def process_add_tech_id(message: types.Message, state: FSMContext, bot: Bot) -> None:
    user_input = message.text.strip()
    if not user_input.isdigit():
        await safe_answer(message, "⚠️ ID должен состоять только из цифр. Попробуйте снова или отправьте /cancel:")
        return

    new_id = int(user_input)
    db.unblock_user(new_id)
    db.add_admin(new_id, role="tech")
    await state.clear()

    full_label, _ = await resolve_user_display(bot, new_id)

    web_url = CONFIG.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")
    notify_text = (
        "🛠 <b>Вам выданы права доступа в боте МУП «Ульяновскэлектротранс»!</b>\n\n"
        "🔧 <b>Роль:</b> <b>Технический инженер (Tech)</b>\n"
        "• Вам доступна панель мониторинга систем: <code>/tech</code>\n"
        "• Панель тестирования и диагностики: <code>/tests</code>\n"
        "• Системные логи: <code>/logs</code>\n"
        "• Резервное копирование базы данных: <code>/backup</code>\n\n"
        f"🌐 <b>Служебная веб-панель:</b> <a href='{web_url}'>{web_url}</a>\n"
        "🔑 Для входа на сайт установите любой пароль командой: <code>/password ВАШ_ПАРОЛЬ</code>\n\n"
        "<i>Отправьте /help для просмотра полного списка команд или /panel для открытия панели.</i>"
    )
    btn_kb = InlineKeyboardBuilder()
    btn_kb.button(text="🌐 Открыть Mini App (внутри TG)", web_app=types.WebAppInfo(url=web_url))
    sent = await safe_send(bot, new_id, notify_text, reply_markup=btn_kb.as_markup())
    note = "✅ Оповещение отправлено инженеру в ЛС." if sent else "⚠️ <i>Инженер назначен, но не получил сообщение в ЛС (нужно нажать /start в боте).</i>"
    await sync_user_commands(bot, new_id, role="tech")

    await safe_answer(
        message,
        f"✅ <b>Технический инженер успешно назначен!</b>\n\n"
        f"👤 <b>Инженер:</b> {full_label}\n"
        "🔧 <b>Роль:</b> Tech\n"
        f"{note}",
        reply_markup=make_admins_menu_keyboard(db.get_all_admins()),
        parse_mode="HTML",
    )


# ==============================================================================
# 4. УПРАВЛЕНИЕ НАБОРОМ ВАКАНСИЙ
# ==============================================================================

@tech_router.callback_query(F.data == "tech_vacancies_menu")
async def cb_tech_vacancies_menu(
    callback: types.CallbackQuery,
    state: Optional[FSMContext] = None,
) -> None:
    if not is_privileged_user(callback.from_user.id):
        await callback.answer("🚫 Нет прав!", show_alert=True)
        return
    if state:
        await state.clear()

    vacancies = get_all_vacancies()
    closed = get_closed_vacancies()

    builder = InlineKeyboardBuilder()
    for idx, vac in enumerate(vacancies):
        status_icon = "🔴 ЗАКРЫТ" if vac in closed else "🟢 Открыт"
        builder.button(text=f"{status_icon}: {vac[:22]}", callback_data=f"vac_tgl_idx_{idx}")
    builder.adjust(1)

    action_row = InlineKeyboardBuilder()
    action_row.button(text="➕ Добавить вакансию", callback_data="vac_ui_add")
    action_row.button(text="🗑 Удалить вакансию", callback_data="vac_ui_del_menu")
    action_row.button(text="⬅️ В меню /tech", callback_data="tech_refresh")
    action_row.adjust(2, 1)

    builder.attach(action_row)
    text = (
        "🎯 <b>УПРАВЛЕНИЕ НАБОРОМ ПО ВАКАНСИЯМ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Нажмите на должность, чтобы открыть или закрыть приём анкет:\n"
        "• 🟢 <b>Открыт</b> — соискатели видят эту вакансию в анкете\n"
        "• 🔴 <b>ЗАКРЫТ</b> — вакансия скрыта от соискателей\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@tech_router.callback_query(F.data.startswith("vac_tgl_idx_"))
async def cb_tech_vac_toggle_idx(callback: types.CallbackQuery) -> None:
    if not is_privileged_user(callback.from_user.id):
        await callback.answer("🚫 Нет прав!", show_alert=True)
        return

    idx = int(callback.data.split("_")[-1])
    vacancies = get_all_vacancies()
    if not (0 <= idx < len(vacancies)):
        await callback.answer("Ошибка индекса", show_alert=True)
        return

    target_vac = vacancies[idx]
    closed = get_closed_vacancies()
    if target_vac in closed:
        closed.remove(target_vac)
        msg = f"🟢 Приём на «{target_vac}» открыт!"
    else:
        closed.add(target_vac)
        msg = f"🔴 Приём на «{target_vac}» закрыт!"

    set_closed_vacancies(closed)
    await callback.answer(msg, show_alert=True)
    await cb_tech_vacancies_menu(callback)


@tech_router.callback_query(F.data == "vac_ui_add")
async def cb_vac_ui_add(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not is_privileged_user(callback.from_user.id):
        await callback.answer("🚫 Нет прав!", show_alert=True)
        return
    await state.set_state(AdminManageState.waiting_vacancy_name)
    text = (
        "➕ <b>ДОБАВЛЕНИЕ НОВОЙ ВАКАНСИИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Введите точное наименование новой должности/профессии:\n\n"
        "<i>Пример: Маляр по покраске трамваев</i>\n\n"
        "Для отмены отправьте /cancel."
    )
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()


@tech_router.message(AdminManageState.waiting_vacancy_name)
async def process_new_vacancy_title(message: types.Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title or title.startswith("/"):
        await state.clear()
        await safe_answer(message, "Действие отменено.")
        return

    vacancies = get_all_vacancies()
    if title not in vacancies:
        vacancies.append(title)
        save_all_vacancies(vacancies)

    await state.clear()
    await safe_answer(message, f"✅ <b>Вакансия добавлена:</b> «{html.escape(title)}»", parse_mode="HTML")


@tech_router.callback_query(F.data == "vac_ui_del_menu")
async def cb_vac_ui_del_menu(callback: types.CallbackQuery) -> None:
    if not is_privileged_user(callback.from_user.id):
        await callback.answer("🚫 Нет прав!", show_alert=True)
        return

    vacancies = get_all_vacancies()
    builder = InlineKeyboardBuilder()
    for idx, vac in enumerate(vacancies):
        builder.button(text=f"❌ {vac}", callback_data=f"vac_del_idx_{idx}")
    builder.button(text="⬅️ Назад к списку", callback_data="tech_vacancies_menu")
    builder.adjust(1)

    text = (
        "🗑 <b>УДАЛЕНИЕ ВАКАНСИИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите вакансию для полного удаления из системы предприятия:"
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@tech_router.callback_query(F.data.startswith("vac_del_idx_"))
async def cb_vac_del_execute(callback: types.CallbackQuery) -> None:
    if not is_privileged_user(callback.from_user.id):
        await callback.answer("🚫 Нет прав!", show_alert=True)
        return

    idx = int(callback.data.split("_")[-1])
    vacancies = get_all_vacancies()
    if 0 <= idx < len(vacancies):
        removed = vacancies.pop(idx)
        save_all_vacancies(vacancies)
        closed = get_closed_vacancies()
        if removed in closed:
            closed.remove(removed)
            set_closed_vacancies(closed)
        await callback.answer(f"🗑 Вакансия «{removed}» удалена!", show_alert=True)
        await cb_tech_vacancies_menu(callback)


# ==============================================================================
# 5. ПЕРЕЗАПУСК СЛУЖБЫ (/restart)
# ==============================================================================

@tech_router.callback_query(F.data == "tech_action_restart_only")
async def cb_tech_restart_only(callback: types.CallbackQuery) -> None:
    if not is_privileged_user(callback.from_user.id):
        await callback.answer("🚫 Нет прав!", show_alert=True)
        return

    await callback.message.edit_text(
        "🔄 <b>Перезапуск службы uet_bot выполняется...</b>\n\nБот перезагрузится через 2-3 секунды.",
        parse_mode="HTML",
    )
    await callback.answer()

    async def _do_restart():
        await asyncio.sleep(1.0)
        await run_shell_cmd("systemctl restart uet_bot")

    asyncio.create_task(_do_restart())


@tech_router.message(Command("restart", "reboot"))
async def cmd_restart(message: types.Message) -> None:
    if not is_privileged_user(message.from_user.id):
        await safe_answer(message, "🚫 Доступ ограничен администраторами.")
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ ДА, ПЕРЕЗАПУСТИТЬ", callback_data="tech_action_restart_only")
    builder.button(text="❌ Отмена", callback_data="tech_refresh")
    builder.adjust(1, 1)
    await safe_answer(
        message,
        "⚠️ <b>ПОДТВЕРЖДЕНИЕ ПЕРЕЗАПУСКА СЛУЖБЫ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Вы собираетесь перезапустить системную службу <code>uet_bot</code>.\n"
        "Процесс перезагрузится за 2–3 секунды и применит изменения.\n\n"
        "<b>Перезапустить сейчас?</b>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


# ==============================================================================
# 6. ВЫГРУЗКА ПАКЕТА ДОКУМЕНТАЦИИ (ГОСТ 19.505, ГОСТ 19.503, 152-ФЗ)
# ==============================================================================

@tech_router.message(Command("docs", "manual"))
async def cmd_generate_project_docs(message: types.Message) -> None:
    """Генерация полного комплекта нормативной и эксплуатационной документации."""
    if not is_privileged_user(message.from_user.id):
        await safe_answer(message, "🚫 Доступ ограничен администраторами.")
        return

    await safe_answer(message, "⏳ <b>Формирую официальный комплект документации (ГОСТ/152-ФЗ)...</b>")
    try:
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root_dir not in sys.path:
            sys.path.insert(0, root_dir)

        import doc_generator  # type: ignore

        p_policy = doc_generator.generate_privacy_policy_docx()
        p_manual = doc_generator.generate_operator_manual_docx()
        p_passport = doc_generator.generate_system_passport_docx()

        with open(p_policy, "rb") as f:
            await message.answer_document(
                BufferedInputFile(f.read(), filename="Политика_обработки_ПДн_МУП_УЭТ.docx"),
                caption="📑 <b>Политика обработки и защиты ПДн (ст. 18.1 152-ФЗ)</b>",
                parse_mode="HTML",
            )
        with open(p_manual, "rb") as f:
            await message.answer_document(
                BufferedInputFile(f.read(), filename="Руководство_оператора_кадров_ГОСТ_19.505.docx"),
                caption="📚 <b>Руководство оператора (кадровой службы) по ГОСТ 19.505-79</b>",
                parse_mode="HTML",
            )
        with open(p_passport, "rb") as f:
            await message.answer_document(
                BufferedInputFile(f.read(), filename="Технический_паспорт_АИС_Рекрутинг_Сервис.docx"),
                caption="🛡 <b>Технический паспорт и архитектура системы (УЗ-3 ФСТЭК)</b>",
                parse_mode="HTML",
            )
    except ImportError:
        await safe_answer(message, "⚠️ Модуль <code>doc_generator</code> не найден в директории проекта.")
    except Exception as e:
        logger.error("Ошибка формирования документации: %s", e)
        await safe_answer(message, f"❌ Ошибка формирования документации: {e}")