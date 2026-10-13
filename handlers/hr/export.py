# -*- coding: utf-8 -*-
"""
Выгрузка данных и генерация актов МУП «Ульяновскэлектротранс»:
- Экспорт базы соискателей в CSV (/export, callback hr_export_excel)
- Генерация официального Акта об уничтожении ПДн по 152-ФЗ (/act)
"""

from __future__ import annotations

import csv
import html
import io
import logging
from datetime import datetime
from typing import Union

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile

from common import (
    CONFIG,
    db,
    is_hr_admin,
    is_tech_admin,
    safe_answer,
)
from services import generate_destruction_act_docx

logger = logging.getLogger("HR_EXPORT")
export_router = Router(name="hr_export")


@export_router.message(Command("export"))
@export_router.callback_query(F.data == "hr_export_excel")
async def process_export_candidates(event: Union[types.Message, types.CallbackQuery]) -> None:
    """Экспорт базы соискателей в формате CSV (Excel-совместимый)."""
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


@export_router.message(Command("act"))
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
        path = generate_destruction_act_docx({
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
    except Exception as e:
        logger.error("Ошибка генерации Акта: %s", e)
        await safe_answer(message, f"❌ Ошибка генерации документа: {e}")
