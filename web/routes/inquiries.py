"""
Маршрутизаторы работы с вопросами и обращениями соискателей в отдел кадров МУП «УЭТ».
"""

from __future__ import annotations

import html
import logging
import re
import time
from typing import TYPE_CHECKING

from aiohttp import web

from common import CONFIG, safe_send, send_response_to_candidate
from services.candidate_service import candidate_service

if TYPE_CHECKING:
    from web_app import WebPanel

logger = logging.getLogger("UET_WEB_INQUIRIES")


async def handle_api_inquiry_submit(panel: WebPanel, request: web.Request) -> web.Response:
    """Прием вопроса соискателя с веб-портала и отправка оповещения кадровикам."""
    try:
        data = await request.json()
        full_name = str(data.get("full_name", "")).strip()
        phone = str(data.get("phone", "")).strip()
        email = str(data.get("email", "")).strip()
        question_text = str(data.get("question", "")).strip()
        vacancy = str(data.get("vacancy", "")).strip()
        reply_channel = str(data.get("reply_channel", "tg")).strip().lower()
        if reply_channel not in ("tg", "email", "phone"):
            reply_channel = "tg"

        ticket_id_raw = data.get("ticket_id")
        ticket_id = None
        if ticket_id_raw:
            cleaned_tid = str(ticket_id_raw).replace("№", "").replace("В-", "").replace("в-", "").replace("#", "").strip()
            if cleaned_tid.isdigit():
                ticket_id = int(cleaned_tid)

        if not question_text or len(question_text) < 5:
            return web.json_response(
                {"ok": False, "error": "Пожалуйста, введите вопрос подробно (не менее 5 символов)."}, status=400
            )
        if not phone:
            return web.json_response({"ok": False, "error": "Укажите контактный номер телефона для связи."}, status=400)

        user_id = f"web_{int(time.time())}"
        platform = "web"
        if ticket_id:
            cand = panel.db.get_candidate(ticket_id)
            if cand:
                if str(cand[2]).lstrip("-").isdigit():
                    user_id = str(cand[2])
                    platform = str(cand[1])
                if not full_name and cand[3]:
                    full_name = str(cand[3])
                if not phone and cand[4]:
                    phone = str(cand[4])
        else:
            clean_phone = re.sub(r"\D", "", phone)[-10:]
            if clean_phone:
                with panel.db.connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT user_id, platform, ticket_id, full_name FROM candidates WHERE phone LIKE ? ORDER BY ticket_id DESC LIMIT 1",
                        (f"%{clean_phone}",),
                    )
                    cand_row = cur.fetchone()
                    if cand_row:
                        if str(cand_row[0]).lstrip("-").isdigit():
                            user_id = str(cand_row[0])
                            platform = str(cand_row[1])
                        ticket_id = cand_row[2]
                        if not full_name and cand_row[3]:
                            full_name = str(cand_row[3])

        ok, msg, inq_id = candidate_service.submit_inquiry(
            user_id=user_id,
            question_text=question_text,
            platform=platform,
            ticket_id=ticket_id,
            full_name=full_name,
            phone=phone,
            vacancy=vacancy or "Сайт / Общий вопрос",
            email=email,
            reply_channel=reply_channel,
        )

        if not ok:
            return web.json_response({"ok": False, "error": msg}, status=400)

        if panel.bot:
            try:
                hr_group = CONFIG.get("HR_GROUP_ID")
                super_admin = CONFIG.get("SUPER_ADMIN_ID")

                channel_desc = {
                    "tg": "🤖 В Telegram-бот соискателя",
                    "email": "📧 На Email соискателя",
                    "phone": "📞 По телефону (звонок кадров)",
                }.get(reply_channel, "🤖 В Telegram-бот")

                notify_text = (
                    f"❓ <b>НОВЫЙ ВОПРОС СОИСКАТЕЛЯ № В-{inq_id}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>От:</b> {html.escape(full_name or 'Соискатель')}\n"
                    f"📞 <b>Телефон:</b> <code>{html.escape(phone)}</code>\n"
                    f"📋 <b>Номер анкеты:</b> {f'#{ticket_id}' if ticket_id else '<i>не привязана</i>'}\n"
                    f"📧 <b>Email:</b> <code>{html.escape(email) if email else 'не указан'}</code>\n"
                    f"📬 <b>Желаемый ответ:</b> {channel_desc}\n"
                    f"💼 <b>Тема:</b> {html.escape(vacancy or 'Общий вопрос')}\n\n"
                    f"💬 <b>Текст вопроса:</b>\n<i>{html.escape(question_text)}</i>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"<i>Для ответа перейдите во вкладку «Вопросы соискателей» веб-портала.</i>"
                )
                if hr_group:
                    await safe_send(panel.bot, hr_group, notify_text)
                elif super_admin:
                    await safe_send(panel.bot, super_admin, notify_text)
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление о вопросе в TG: {e}")

        return web.json_response({
            "ok": True,
            "inquiry_id": inq_id,
            "ticket_code": f"В-{inq_id}",
            "message": f"Ваш вопрос успешно принят под номером № В-{inq_id}! Ответ будет направлен в выбранный канал связи.",
        })
    except Exception as e:
        logger.error("Ошибка при отправке вопроса с сайта: %s", e)
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def handle_api_inquiry_check(panel: WebPanel, request: web.Request) -> web.Response:
    """Публичный поиск статуса и ответа на вопрос по номеру обращения или телефону."""
    query = str(
        request.query.get("query")
        or request.query.get("inquiry_id")
        or request.query.get("ticket_code")
        or request.query.get("phone")
        or ""
    ).strip()
    if not query:
        return web.json_response(
            {"ok": False, "error": "Укажите номер обращения (например, В-15) или номер телефона."}, status=400
        )

    cleaned_num = re.sub(r"\D", "", query)
    inquiries = []
    with panel.db.connection() as conn:
        cur = conn.cursor()
        if cleaned_num and (query.startswith("В-") or query.startswith("в-") or query.startswith("#") or len(cleaned_num) <= 6):
            cur.execute(
                """
                SELECT inquiry_id, ticket_id, full_name, phone, vacancy, question_text, status, admin_reply, created_at, closed_at, reply_channel
                FROM inquiries WHERE inquiry_id = ?
                """,
                (int(cleaned_num),),
            )
            for r in cur.fetchall():
                inquiries.append({
                    "inquiry_id": r[0],
                    "ticket_code": f"В-{r[0]}",
                    "ticket_id": r[1],
                    "full_name": r[2] or "Соискатель",
                    "phone": r[3] or "",
                    "vacancy": r[4] or "",
                    "question_text": r[5] or "",
                    "status": "Отвечено" if r[6] == "replied" or r[7] else ("Закрыто" if r[6] == "closed" else "В обработке"),
                    "admin_reply": r[7] or "",
                    "created_at": str(r[8] or "")[:16],
                    "closed_at": str(r[9] or "")[:16],
                    "reply_channel": r[10] or "tg",
                })

        if len(cleaned_num) >= 7:
            last_digits = cleaned_num[-10:]
            cur.execute(
                """
                SELECT inquiry_id, ticket_id, full_name, phone, vacancy, question_text, status, admin_reply, created_at, closed_at, reply_channel
                FROM inquiries WHERE phone LIKE ? ORDER BY inquiry_id DESC LIMIT 10
                """,
                (f"%{last_digits}",),
            )
            for r in cur.fetchall():
                if not any(item["inquiry_id"] == r[0] for item in inquiries):
                    inquiries.append({
                        "inquiry_id": r[0],
                        "ticket_code": f"В-{r[0]}",
                        "ticket_id": r[1],
                        "full_name": r[2] or "Соискатель",
                        "phone": r[3] or "",
                        "vacancy": r[4] or "",
                        "question_text": r[5] or "",
                        "status": "Отвечено" if r[6] == "replied" or r[7] else ("Закрыто" if r[6] == "closed" else "В обработке"),
                        "admin_reply": r[7] or "",
                        "created_at": str(r[8] or "")[:16],
                        "closed_at": str(r[9] or "")[:16],
                        "reply_channel": r[10] or "tg",
                    })

    return web.json_response({"ok": True, "items": inquiries})


async def handle_api_inquiries(panel: WebPanel, request: web.Request) -> web.Response:
    """Список последних обращений для пульта кадров."""
    _, err = panel.require_roles(request, {"hr", "tech", "superadmin", "super"})
    if err:
        return err
    inquiries = []
    with panel.db.connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT inquiry_id, ticket_id, platform, user_id, full_name, phone, vacancy,
                   question_text, status, created_at, admin_reply, email, reply_channel
            FROM inquiries
            ORDER BY inquiry_id DESC
            LIMIT 60
            """
        )
        for r in cur.fetchall():
            inquiries.append(
                {
                    "inquiry_id": r[0],
                    "ticket_code": f"В-{r[0]}",
                    "ticket_id": r[1],
                    "platform": r[2],
                    "user_id": r[3],
                    "full_name": r[4] or "",
                    "phone": r[5] or "",
                    "vacancy": r[6] or "",
                    "question_text": r[7] or "",
                    "status": "Отвечено" if r[8] == "replied" or r[10] else ("Закрыто" if r[8] == "closed" else "Новое"),
                    "created_at": str(r[9] or "")[:16],
                    "admin_reply": r[10] or "",
                    "email": r[11] or "",
                    "reply_channel": r[12] or "tg",
                }
            )
    return web.json_response(inquiries)


async def handle_api_inquiry_reply(panel: WebPanel, request: web.Request) -> web.Response:
    """Ответ кадровика на вопрос с отправкой в мессенджер соискателя."""
    _, err = panel.require_roles(request, {"hr", "tech", "superadmin", "super"})
    if err:
        return err
    inquiry_id = int(request.match_info["inquiry_id"])
    data = await request.json()
    reply_text = data.get("reply", "").strip()
    if not reply_text:
        return web.json_response({"error": "Введите текст ответа соискателю."}, status=400)

    inq = panel.db.get_inquiry(inquiry_id)
    if not inq:
        return web.json_response({"error": "Обращение не найдено"}, status=404)

    user_id = inq[3]
    platform = inq[2]
    panel.db.reply_inquiry(inquiry_id, reply_text)

    sent_msg = False
    msg_body = (
        f"💬 <b>Ответ отдела кадров МУП «Ульяновскэлектротранс»</b>\n"
        f"<i>(на ваш вопрос № В-{inquiry_id})</i>\n\n"
        f"{html.escape(reply_text)}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📞 <i>Отдел кадров: +7 (8422) 58-46-60</i>"
    )
    try:
        sent_msg = await send_response_to_candidate(platform, str(user_id), msg_body)
    except Exception as e:
        logger.warning("Не удалось переслать ответ соискателю %s (%s): %s", user_id, platform, e)

    return web.json_response({"ok": True, "inquiry_id": inquiry_id, "sent_msg": sent_msg})
