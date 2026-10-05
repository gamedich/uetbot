# -*- coding: utf-8 -*-
"""
Шлюз интеграции с ВКонтакте для МУП «Ульяновскэлектротранс».
Поддерживает:
- Автономный Long Poll с экспоненциальной задержкой (Exponential Backoff).
- Интерактивные цветные клавиатуры ВКонтакте.
- Централизованный сервисный слой (CandidateService) и единую карту текстов (texts.py).
- Контур 152-ФЗ: проверка повторной подачи, кулдаунов и фиксация согласия.
- Доставка анкет и обращений в Telegram-группу кадровой службы в реальном времени.
- Двусторонний потоковый обмен медиафайлами и фото документов в ОЗУ (без сохранения на диск по 152-ФЗ).
- Сквозной Live-Chat мост соискателя из ВК с кадровиком в Telegram.
"""
from __future__ import annotations

import asyncio
import html
import io
import json
import logging
import random
import re
from datetime import datetime
from typing import List, Optional, Dict, Any

import aiohttp
from aiohttp import ClientSession, ClientTimeout
from aiogram import Bot
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

import texts
from texts import clean_html
from common import (
    CONFIG,
    VACANCIES,
    EXTERNAL_SESSIONS,
    SYSTEM_METRICS,
    route_new_candidate_ticket,
    route_new_inquiry_ticket,
    safe_send,
    db,
    get_all_vacancies
)
from candidate_service import candidate_service
from database import ResumeDB
from keyboards import make_ticket_keyboard, make_inquiry_admin_keyboard

logger = logging.getLogger("VK_GATEWAY")

# ==================== БАЗА ЗНАНИЙ (СИНХРОНИЗИРОВАНА С TEXTS.PY) ====================

VK_FAQ_DATA = {
    "training": clean_html(texts.FAQ_DATA.get("faq_training", "")),
    "housing": clean_html(texts.FAQ_DATA.get("faq_housing", "")),
    "salary": clean_html(texts.FAQ_DATA.get("faq_salary", "")),
    "docs": clean_html(texts.FAQ_DATA.get("faq_docs", "")),
}


class VKPhotoService:
    """Сервис взаимодействия с мультимедиа API ВКонтакте."""
    def __init__(self, vk_token: str, session: ClientSession):
        self.vk_token = vk_token
        self.session = session
        self.api_version = "5.131"

    async def _vk_call(self, method: str, **params) -> dict:
        params["access_token"] = self.vk_token
        params["v"] = self.api_version
        async with self.session.get(f"https://api.vk.com/method/{method}", params=params) as resp:
            data = await resp.json()
            if "error" in data:
                raise RuntimeError(f"VK API Error: {data['error'].get('error_msg')}")
            return data.get("response", {})

    async def upload_photo_to_vk_chat(self, peer_id: int, image_bytes: io.BytesIO) -> str:
        upload_server = await self._vk_call("photos.getMessagesUploadServer", peer_id=peer_id)
        upload_url = upload_server["upload_url"]

        form = aiohttp.FormData()
        form.add_field("photo", image_bytes, filename="photo.jpg", content_type="image/jpeg")
        async with self.session.post(upload_url, data=form) as upload_resp:
            upload_result = await upload_resp.json(content_type=None)

        saved_photos = await self._vk_call(
            "photos.saveMessagesPhoto",
            photo=upload_result.get("photo"),
            server=upload_result.get("server"),
            hash=upload_result.get("hash")
        )

        photo_obj = saved_photos[0]
        return f"photo{photo_obj['owner_id']}_{photo_obj['id']}"

    @staticmethod
    def extract_best_photo_url(sizes: List[dict]) -> Optional[str]:
        if not sizes:
            return None
        valid_sizes = [s for s in sizes if "url" in s]
        if not valid_sizes:
            return None
        best = max(valid_sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))
        return best.get("url")


# ==================== КЛАВИАТУРЫ ВКОНТАКТЕ ====================

def make_vk_main_keyboard() -> str:
    """Главное меню ВКонтакте с постоянными цветными кнопками."""
    buttons = [
        [{"action": {"type": "text", "label": "📝 Заполнить анкету", "payload": json.dumps({"command": "apply"})}, "color": "positive"}],
        [{"action": {"type": "text", "label": "📑 Моя анкета", "payload": json.dumps({"command": "my_app"})}, "color": "primary"}],
        [{"action": {"type": "text", "label": "💬 Связаться с кадровиком", "payload": json.dumps({"command": "ask"})}, "color": "secondary"}],
        [
            {"action": {"type": "text", "label": "📚 Частые вопросы (FAQ)", "payload": json.dumps({"command": "faq"})}, "color": "secondary"},
            {"action": {"type": "text", "label": "📞 Контакты", "payload": json.dumps({"command": "contacts"})}, "color": "secondary"}
        ]
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_vacancies_keyboard() -> str:
    """Динамическая клавиатура вакансий ВКонтакте со скрытием закрытых позиций."""
    val = db.get_setting("closed_vacancies", "[]")
    try:
        closed = set(json.loads(val))
    except Exception:
        closed = set()

    vacancies = get_all_vacancies()
    active_vacs = [v for v in vacancies if v not in closed]

    buttons = []
    for vac in active_vacs:
        buttons.append([{
            "action": {
                "type": "text",
                "label": vac[:40],
                "payload": json.dumps({"vac": vac})
            },
            "color": "primary"
        }])

    buttons.append([{
        "action": {
            "type": "text",
            "label": "Другая должность",
            "payload": json.dumps({"vac": "other"})
        },
        "color": "secondary"
    }])
    buttons.append([{
        "action": {
            "type": "text",
            "label": "❌ Отмена",
            "payload": json.dumps({"command": "cancel"})
        },
        "color": "negative"
    }])

    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_experience_keyboard() -> str:
    """Клавиатура для шага опыта работы."""
    buttons = [
        [{"action": {"type": "text", "label": "Без опыта", "payload": json.dumps({"exp": "no_exp"})}, "color": "primary"}],
        [{"action": {"type": "text", "label": "❌ Отмена", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}]
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_cancel_keyboard() -> str:
    """Клавиатура с кнопкой отмены."""
    buttons = [
        [{"action": {"type": "text", "label": "❌ Отмена", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}]
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_faq_keyboard() -> str:
    """Клавиатура разделов FAQ ВКонтакте."""
    buttons = [
        [{"action": {"type": "text", "label": "🎓 Обучение на водителя"}, "color": "primary"}],
        [{"action": {"type": "text", "label": "🏠 Жилье и общежитие"}, "color": "secondary"}],
        [{"action": {"type": "text", "label": "💰 Зарплата и льготная пенсия"}, "color": "secondary"}],
        [{"action": {"type": "text", "label": "📄 Необходимые документы"}, "color": "secondary"}],
        [
            {"action": {"type": "text", "label": "📝 Заполнить анкету", "payload": json.dumps({"command": "apply"})}, "color": "positive"},
            {"action": {"type": "text", "label": "⬅️ Главное меню", "payload": json.dumps({"command": "start"})}, "color": "secondary"}
        ]
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_blocked_apply_keyboard() -> str:
    """Клавиатура при наличии активной анкеты."""
    buttons = [
        [{"action": {"type": "text", "label": "📑 Моя анкета", "payload": json.dumps({"command": "my_app"})}, "color": "primary"}],
        [{"action": {"type": "text", "label": "💬 Связаться с кадровиком", "payload": json.dumps({"command": "ask"})}, "color": "secondary"}],
        [{"action": {"type": "text", "label": "⬅️ Главное меню", "payload": json.dumps({"command": "start"})}, "color": "secondary"}]
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_dialog_keyboard() -> str:
    """Клавиатура соискателя во время прямого диалога с кадровой службой."""
    buttons = [
        [{"action": {"type": "text", "label": "⏹ Завершить диалог", "payload": json.dumps({"command": "stop"})}, "color": "negative"}]
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_reply_keyboard(ticket_id: int = 0) -> str:
    """Инлайн-кнопка прямо под сообщением кадровика для удобного ответа."""
    buttons = [
        [
            {
                "action": {
                    "type": "text",
                    "label": "💬 Ответить кадровику",
                    "payload": json.dumps({"command": "reply_hr", "ticket_id": ticket_id})
                },
                "color": "primary"
            }
        ]
    ]
    return json.dumps({"inline": True, "buttons": buttons}, ensure_ascii=False)


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def parse_phone_number(raw_text: str) -> Optional[str]:
    """Универсальный парсер российских телефонных номеров."""
    m = re.search(r"(\+?[78][\s\-\(]?\d{3}[\s\-\)]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}|\+?\d{10,15})", raw_text)
    candidate = m.group(1) if m else raw_text
    clean = re.sub(r"[^\d+]", "", candidate)

    if clean.startswith("8") and len(clean) == 11:
        clean = "+7" + clean[1:]
    elif clean.startswith("7") and len(clean) == 11:
        clean = "+7" + clean[1:]
    elif not clean.startswith("+") and len(clean) == 10:
        clean = "+7" + clean
    elif not clean.startswith("+") and len(clean) >= 10:
        clean = "+" + clean

    if 11 <= len(clean) <= 16 and clean[1:].isdigit():
        return clean
    return None


def resolve_vacancy(text: str, payload_dict: dict = None) -> str:
    """Умное сопоставление вакансии по тексту, кнопке, номеру или payload."""
    if payload_dict and "vac" in payload_dict:
        v = payload_dict["vac"]
        if v == "other":
            return "Другая должность"
        for real_v in VACANCIES:
            if real_v == v or real_v.startswith(v):
                return real_v
        return v

    tl = text.lower().strip()
    if tl in ["1", "трамвай", "водитель трамвая"]:
        return "Водитель трамвая"
    if tl in ["2", "троллейбус", "водитель троллейбуса"]:
        return "Водитель троллейбуса"
    if tl in ["3", "кондуктор"]:
        return "Кондуктор"
    if tl in ["4", "слесарь", "слесарь по ремонту подвижного состава"]:
        return "Слесарь по ремонту подвижного состава"
    if tl in ["5", "электромонтер", "электромонтер контактной сети", "монтер"]:
        return "Электромонтер контактной сети"
    if tl in ["6", "другая", "другая должность"]:
        return "Другая должность"

    for v in VACANCIES:
        if v.lower() in tl or tl in v.lower():
            return v
    return text.strip()


async def send_vk_message(
    session: ClientSession,
    token: str,
    user_id: int,
    message: str = "",
    attachment: str = "",
    keyboard: str = ""
) -> bool:
    """Отправка сообщения в ЛС ВКонтакте с уникальным random_id."""
    url = "https://api.vk.com/method/messages.send"
    params = {
        "user_id": user_id,
        "message": message,
        "random_id": random.randint(1, 2147483647),
        "v": "5.131",
        "access_token": token
    }
    if attachment:
        params["attachment"] = attachment
    if keyboard:
        params["keyboard"] = keyboard

    try:
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            if "error" in data:
                logger.error(f"Ошибка VK API при отправке user_id={user_id}: {data['error']}")
                return False
            return "response" in data
    except Exception as e:
        logger.error(f"Сетевая ошибка отправки VK: {e}")
        return False


# ==================== ГЛАВНЫЙ ОБРАБОТЧИК СООБЩЕНИЙ ====================

async def handle_vk_message(
    user_id: str,
    text: str,
    attachments: list,
    db: ResumeDB,
    tg_bot: Bot,
    session: ClientSession,
    token: str,
    payload: str = ""
):
    """Интеллектуальная обработка диалогов ВКонтакте с соискателями."""
    try:
        clean = (text or "").strip()
        clean_lower = clean.lower()

        # 1. Проверка блокировки в ЧС
        if db.is_blocked(user_id, super_admin_id=CONFIG.get("SUPER_ADMIN_ID")):
            return

        # 2. Разбор payload кнопки
        payload_dict = {}
        cmd_from_payload = ""
        if payload:
            try:
                payload_dict = json.loads(payload) if isinstance(payload, str) else payload
                cmd_from_payload = payload_dict.get("command", "")
            except Exception:
                pass

        # 3. Режим техобслуживания — блокирует ТОЛЬКО подачу анкет
        is_apply_attempt = (
            cmd_from_payload in ["apply", "start_survey"] or 
            clean_lower in ["подать анкету", "заполнить анкету", "анкета", "работа", "/apply"]
        )
        if CONFIG.get("MAINTENANCE_MODE") and is_apply_attempt:
            maint_msg = (
                "⚠️ Приём анкет временно приостановлен (технический перерыв).\n"
                "Пожалуйста, повторите попытку позже.\n\n"
                f"📞 По срочным вопросам: {CONFIG.get('HR_PHONE', '58-46-60')}"
            )
            await send_vk_message(
                session, token, int(user_id),
                maint_msg,
                keyboard=make_vk_main_keyboard()
            )
            return

        # 4. Мост прямого диалога (кадровик в TG <-> соискатель в VK)
        dlg = db.get_dialog_by_user(user_id)
        if dlg:
            operator_id = dlg[1]
            name = dlg[3] or f"id{user_id}"

            if cmd_from_payload == "stop" or clean_lower in ["/stop", "стоп", "завершить", "⏹ завершить диалог"]:
                db.end_direct_dialog(user_id=user_id)
                await send_vk_message(
                    session, token, int(user_id),
                    "⏹ Диалог со специалистом отдела кадров завершён. Вы возвращены в главное меню.",
                    keyboard=make_vk_main_keyboard()
                )
                await tg_bot.send_message(
                    chat_id=operator_id,
                    text=f"⏹ Соискатель <b>{name}</b> (VK id{user_id}) завершил диалог.",
                    parse_mode="HTML"
                )
                return

            builder = InlineKeyboardBuilder()
            builder.button(text="⏹ Завершить диалог", callback_data=f"end_live_dlg_{user_id}")

            # Пересылка фото в Telegram в ОЗУ (без сохранения на диск по 152-ФЗ)
            for att in attachments:
                if att.get("type") == "photo":
                    photo_url = VKPhotoService.extract_best_photo_url(att.get("photo", {}).get("sizes", []))
                    if photo_url:
                        try:
                            async with session.get(photo_url) as p_resp:
                                if p_resp.status == 200:
                                    p_bytes = await p_resp.read()
                                    p_file = BufferedInputFile(p_bytes, filename=f"vk_doc_{user_id}.jpg")
                                    cap = f"📷 <b>[Фото от соискателя {name}]</b>\n#vk_uid_{user_id}\n{clean}" if clean else f"📷 <b>[Фото от соискателя {name}]</b>\n#vk_uid_{user_id}"
                                    await tg_bot.send_photo(
                                        chat_id=operator_id,
                                        photo=p_file,
                                        caption=cap,
                                        reply_markup=builder.as_markup(),
                                        parse_mode="HTML"
                                    )
                        except Exception as e:
                            logger.error(f"Не удалось переслать фото из VK в TG: {e}")

                elif att.get("type") == "doc":
                    doc_obj = att.get("doc", {})
                    doc_url = doc_obj.get("url")
                    doc_title = doc_obj.get("title", f"document_{user_id}.pdf")
                    if doc_url:
                        try:
                            async with session.get(doc_url) as d_resp:
                                if d_resp.status == 200:
                                    d_bytes = await d_resp.read()
                                    d_file = BufferedInputFile(d_bytes, filename=doc_title)
                                    cap = f"📄 <b>[Документ от соискателя {name}]:</b> <code>{doc_title}</code>\n#vk_uid_{user_id}\n{clean}"
                                    await tg_bot.send_document(
                                        chat_id=operator_id,
                                        document=d_file,
                                        caption=cap,
                                        reply_markup=builder.as_markup(),
                                        parse_mode="HTML"
                                    )
                        except Exception as e:
                            logger.error(f"Не удалось переслать документ из VK в TG: {e}")

            if clean and not attachments:
                relayed = (
                    f"💬 <b>[Соискатель из VK {name}]:</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{clean}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━"
                )
                await safe_send(tg_bot, operator_id, relayed, reply_markup=builder.as_markup())
            return

        key = ("vk", user_id)
        session_data = EXTERNAL_SESSIONS.get(key)

        # 5. Маршрутизация глобальных намерений
        is_cancel = (
            cmd_from_payload == "cancel"
            or clean_lower in ["/cancel", "отмена", "отменить", "назад", "стоп", "/stop", "❌ отмена"]
        )

        is_my_app = (
            cmd_from_payload == "my_app"
            or clean_lower in ["/my", "моя анкета", "📑 моя анкета", "статус", "/status", "мой статус", "где моя анкета"]
        )

        is_start_menu = (
            cmd_from_payload == "start"
            or clean_lower in [
                "/start", "start", "старт", "начать", "меню", "/menu",
                "главное меню", "привет", "здравствуйте", "хелп", "/help",
                "помощь", "⬅️ главное меню"
            ]
        )

        is_apply = (
            cmd_from_payload == "apply"
            or clean_lower in [
                "/apply", "apply", "подать анкету", "заполнить анкету",
                "📝 заполнить анкету", "анкета", "работа", "вакансии", "трудоустройство"
            ]
        )

        is_ask = (
            cmd_from_payload == "ask"
            or clean_lower in [
                "/ask", "ask", "вопрос", "задать вопрос", "связаться",
                "кадровик", "связаться с кадровиком", "💬 связаться с кадровиком"
            ]
            or "связат" in clean_lower
        )

        is_faq = (
            cmd_from_payload == "faq"
            or clean_lower in ["/faq", "faq", "вопросы", "частые вопросы", "📚 частые вопросы (faq)", "вопросы и ответы"]
        )

        is_reply_hr = (
            cmd_from_payload == "reply_hr"
            or clean_lower in ["ответить", "ответить кадровику", "💬 ответить кадровику", "ответ кадровику"]
        )

        is_contacts = (
            cmd_from_payload == "contacts"
            or clean_lower in ["/contacts", "контакты", "📞 контакты", "телефон", "адрес", "где находитесь"]
        )

        # -------------------------------------------------------------
        # ОБРАБОТКА КОМАНД МЕНЮ
        # -------------------------------------------------------------

        # А. ОТМЕНА
        if is_cancel:
            if key in EXTERNAL_SESSIONS:
                del EXTERNAL_SESSIONS[key]
            return await send_vk_message(
                session, token, int(user_id),
                "🚫 Ввод отменён. Вы возвращены в главное меню.",
                keyboard=make_vk_main_keyboard()
            )

        # Б. МОЯ АНКЕТА / СТАТУС
        if is_my_app:
            if key in EXTERNAL_SESSIONS:
                del EXTERNAL_SESSIONS[key]
            cand = db.get_candidate_by_user_id(str(user_id), platform="vk")
            if cand:
                c_id = cand[0]
                name = cand[3]
                phone = cand[4]
                vac = cand[5]
                exp = cand[6]
                st = cand[7]
                admin_note = cand[8] if len(cand) > 8 else ""
                created = cand[9] if len(cand) > 9 else ""
                meeting_line = f"\n📍 Назначенное собеседование: {admin_note}\n" if "Приглашен" in st and admin_note else ""

                my_text = (
                    "📑 ВАША АНКЕТА В МУП «УЛЬЯНОВСКЭЛЕКТРОТРАНС»\n"
                    "━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🆔 Номер заявки: #{c_id}\n"
                    f"👤 ФИО: {name}\n"
                    f"📞 Телефон: {phone}\n"
                    f"🎯 Должность: {vac}\n"
                    f"💼 Опыт работы: {exp}\n"
                    f"📊 Текущий статус: {st}\n"
                    f"⏱ Дата подачи: {created}\n"
                    f"{meeting_line}"
                    "━━━━━━━━━━━━━━━━━━━━━\n"
                    "Для уточнения данных вы можете написать специалисту через кнопку «💬 Связаться с кадровиком»."
                )
            else:
                my_text = (
                    "📑 У вас пока нет поданных анкет в МУП «Ульяновскэлектротранс».\n\n"
                    "Чтобы отправить резюме на рассмотрение кадровой службы, нажмите кнопку «📝 Заполнить анкету»."
                )
            return await send_vk_message(session, token, int(user_id), my_text, keyboard=make_vk_main_keyboard())

        # В. СВЯЗАТЬСЯ С КАДРОВИКОМ (/ask)
        if is_ask:
            is_prod = (CONFIG.get("ENVIRONMENT") == "PROD")
            cooldown = int(db.get_setting("cooldown_seconds", str(CONFIG.get("COOLDOWN_SECONDS", 1200)))) if is_prod else 0
            can_ask, seconds_left = db.check_inquiry_cooldown(str(user_id), cooldown)
            if not can_ask:
                minutes_left = max(1, (seconds_left + 59) // 60)
                msg_text = (
                    f"⏳ Вы сможете задать следующий вопрос через {minutes_left} мин.\n"
                    f"Срочные вопросы по телефону отдела кадров: {CONFIG['HR_PHONE']}"
                )
                return await send_vk_message(session, token, int(user_id), msg_text, keyboard=make_vk_main_keyboard())

            EXTERNAL_SESSIONS[key] = {"step": "waiting_question", "data": {}}
            ask_prompt = (
                clean_html(texts.CONSENT_INQUIRY_PROMPT) + "\n\n"
                "💬 Пожалуйста, напишите ваш вопрос одним сообщением. "
                "Он будет передан специалисту кадровой службы предприятия.\n\n"
                "Для отмены нажмите «❌ Отмена» ниже:"
            )
            return await send_vk_message(session, token, int(user_id), ask_prompt, keyboard=make_vk_cancel_keyboard())

        # Г. ПОДАТЬ АНКЕТУ (/apply) ЧЕРЕЗ CANDIDATE_SERVICE
        if is_apply:
            can_apply, reason, info = candidate_service.check_can_apply(user_id, platform="vk")
            if not can_apply and info:
                ticket_id = info.get("ticket_id")
                if reason == "unprocessed":
                    msg = (
                        f"⚠️ У вас уже есть активная анкета №{ticket_id}!\n"
                        "━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 Должность: {info.get('vacancy', 'Не указана')}\n"
                        f"📊 Текущий статус: {info.get('status', 'Новая')}\n"
                        f"⏱ Дата подачи: {info.get('created_at', '')}\n"
                        "━━━━━━━━━━━━━━━━━━━━━\n"
                        "Подать новую анкету нельзя, пока предыдущая заявка находится на рассмотрении кадровой службы.\n"
                        f"Специалисты обязательно свяжутся с вами. Телефон отдела кадров: {CONFIG['HR_PHONE']}."
                    )
                    return await send_vk_message(session, token, int(user_id), msg, keyboard=make_vk_blocked_apply_keyboard())
                elif reason in ("cooldown", "rejected_cooldown"):
                    msg = (
                        f"⏳ Подача повторной анкеты временно недоступна.\n"
                        "━━━━━━━━━━━━━━━━━━━━━\n"
                        f"По вашей предыдущей анкете №{ticket_id} было принято решение об отказе ({info.get('refuse_date')}).\n"
                        f"По регламенту предприятия, повторная подача анкеты возможна через 3 месяца — начиная с {info.get('available_date')} (осталось {info.get('days_left')} дн.).\n"
                        "━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Телефон для справок: {CONFIG['HR_PHONE']}."
                    )
                    return await send_vk_message(session, token, int(user_id), msg, keyboard=make_vk_blocked_apply_keyboard())

            EXTERNAL_SESSIONS[key] = {"step": "name", "data": {}}
            apply_intro = (
                clean_html(texts.CONSENT_SURVEY_PROMPT) + "\n\n"
                "1️⃣ Введите ваши ФИО полностью (например: Иванов Иван Иванович):"
            )
            return await send_vk_message(session, token, int(user_id), apply_intro, keyboard=make_vk_cancel_keyboard())

        # Д. БАЗА ЗНАНИЙ (FAQ)
        if is_faq:
            if key in EXTERNAL_SESSIONS:
                del EXTERNAL_SESSIONS[key]
            faq_text = (
                "📚 ЧАСТЫЕ ВОПРОСЫ И ОТВЕТЫ (FAQ)\n"
                "МУП «Ульяновскэлектротранс»\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "Выберите интересующую тему с помощью кнопок ниже:"
            )
            return await send_vk_message(session, token, int(user_id), faq_text, keyboard=make_vk_faq_keyboard())

        if "обучение" in clean_lower or "🎓" in clean:
            return await send_vk_message(session, token, int(user_id), VK_FAQ_DATA["training"], keyboard=make_vk_faq_keyboard())
        if "жиль" in clean_lower or "общежит" in clean_lower or "🏠" in clean:
            return await send_vk_message(session, token, int(user_id), VK_FAQ_DATA["housing"], keyboard=make_vk_faq_keyboard())
        if "зарплат" in clean_lower or "пенси" in clean_lower or "льгот" in clean_lower or "💰" in clean:
            return await send_vk_message(session, token, int(user_id), VK_FAQ_DATA["salary"], keyboard=make_vk_faq_keyboard())
        if "документ" in clean_lower or "📄" in clean:
            return await send_vk_message(session, token, int(user_id), VK_FAQ_DATA["docs"], keyboard=make_vk_faq_keyboard())

        # Е. КОНТАКТЫ (СИНХРОНИЗИРОВАНО С TEXTS.PY)
        if is_contacts:
            if key in EXTERNAL_SESSIONS:
                del EXTERNAL_SESSIONS[key]
            contacts_text = (
                clean_html(texts.CONTACTS_SCREEN) + "\n\n"
                "Вы также можете отправить анкету или задать вопрос прямо в этом диалоге."
            )
            return await send_vk_message(session, token, int(user_id), contacts_text, keyboard=make_vk_main_keyboard())

        # Ж. ОТВЕТ НА СООБЩЕНИЕ КАДРОВИКА
        if is_reply_hr:
            cand_t_id = payload_dict.get("ticket_id")
            if not cand_t_id:
                last_c = db.get_candidate_by_user_id(str(user_id), platform="vk")
                if last_c:
                    cand_t_id = last_c[0]

            EXTERNAL_SESSIONS[key] = {
                "step": "waiting_hr_reply",
                "data": {"ticket_id": cand_t_id}
            }
            prompt = (
                "💬 Ответ специалисту отдела кадров МУП «Ульяновскэлектротранс»\n\n"
                "Напишите ваш ответ в одном сообщении. Вы также можете прикрепить фото или сканы документов.\n\n"
                "Для отмены нажмите кнопку «❌ Отмена» ниже:"
            )
            return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_cancel_keyboard())

        # З. СТАРТ / ГЛАВНОЕ МЕНЮ
        if is_start_menu:
            if key in EXTERNAL_SESSIONS:
                del EXTERNAL_SESSIONS[key]
            welcome = (
                "👋 Здравствуйте!\n\n"
                "Вас приветствует официальный бот по подбору персонала МУП «Ульяновскэлектротранс».\n\n"
                "Здесь вы можете подать анкету на трудоустройство, проверить статус заявки, задать вопрос кадровику "
                "или узнать контакты предприятия.\n\n"
                "Выберите интересующее действие с помощью кнопок меню ниже:"
            )
            return await send_vk_message(session, token, int(user_id), welcome, keyboard=make_vk_main_keyboard())

        # -------------------------------------------------------------
        # ПОШАГОВОЕ ЗАПОЛНЕНИЕ АНКЕТЫ / ВОПРОСА
        # -------------------------------------------------------------
        if session_data:
            step = session_data.get("step")

            # Шаг: Вопрос в кадры
            if step == "waiting_question":
                is_valid, clean_q, err = candidate_service.validate_question(clean)
                if not is_valid:
                    return await send_vk_message(
                        session, token, int(user_id),
                        f"⚠️ {err or 'Пожалуйста, напишите ваш вопрос текстом в одном сообщении:'}",
                        keyboard=make_vk_cancel_keyboard()
                    )

                last_cand = db.get_candidate_by_user_id(str(user_id), platform="vk")
                if last_cand:
                    ticket_id = last_cand[0]
                    full_name = last_cand[3]
                    phone = last_cand[4]
                    vacancy = last_cand[5]
                else:
                    ticket_id = None
                    full_name = f"Пользователь VK id{user_id}"
                    phone = "Не указан"
                    vacancy = "Анкета не подана"

                del EXTERNAL_SESSIONS[key]
                ok, status_msg, inquiry_id = candidate_service.submit_inquiry(
                    user_id=str(user_id),
                    question_text=clean_q,
                    platform="vk",
                    ticket_id=ticket_id,
                    full_name=full_name,
                    phone=phone,
                    vacancy=vacancy
                )

                conf_text = (
                    f"✅ Ваш вопрос принят и передан в отдел кадров!\n"
                    f"Номер обращения: #{inquiry_id}.\n\n"
                    f"Специалист рассмотрит его в рабочее время ({CONFIG['HR_SCHEDULE']}). Ответ поступит прямо в этот диалог.\n\n"
                    f"Телефон отдела кадров: {CONFIG['HR_PHONE']}."
                )
                await send_vk_message(session, token, int(user_id), conf_text, keyboard=make_vk_main_keyboard())

                safe_clean = html.escape(clean_q)
                safe_full = html.escape(full_name)
                card_text = (
                    f"📩 <b>ОБРАЩЕНИЕ СОИСКАТЕЛЯ #{inquiry_id} [VK]</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>Кандидат:</b> {safe_full}\n"
                    f"📞 <b>Телефон:</b> <code>{html.escape(phone)}</code>\n"
                    f"🎯 <b>Вакансия:</b> {html.escape(vacancy)}\n"
                    f"⏱ <b>Время:</b> <code>{datetime.now().strftime('%d.%m.%Y %H:%M')}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"❓ <b>Вопрос:</b>\n"
                    f"«{safe_clean}»"
                )
                try:
                    await route_new_inquiry_ticket(tg_bot, card_text, reply_markup=make_inquiry_admin_keyboard(inquiry_id))
                    logger.info(f"Вопрос #{inquiry_id} [VK] успешно доставлен в Telegram!")
                except Exception as e:
                    logger.error(f"Ошибка отправки вопроса #{inquiry_id} в Telegram: {e}")
                return

            # Шаг: Ввод ФИО
            elif step == "name":
                is_valid, clean_name, err = candidate_service.validate_fio(clean)
                if not is_valid:
                    return await send_vk_message(
                        session, token, int(user_id),
                        f"⚠️ {err or 'Пожалуйста, укажите фамилию и имя полностью (минимум 2 слова, например: Иванов Иван):'}",
                        keyboard=make_vk_cancel_keyboard()
                    )
                session_data["data"]["full_name"] = clean_name
                session_data["step"] = "phone"
                phone_prompt = (
                    "2️⃣ Укажите контактный номер телефона для связи с отделом кадров:\n\n"
                    "Введите номер цифрами (например: +79001234567 или 89001234567):"
                )
                return await send_vk_message(session, token, int(user_id), phone_prompt, keyboard=make_vk_cancel_keyboard())

            # Шаг: Ввод телефона
            elif step == "phone":
                parsed_phone = None
                for att in attachments:
                    if att.get("type") == "contact":
                        parsed_phone = att.get("contact", {}).get("phone")

                if not parsed_phone:
                    parsed_phone = parse_phone_number(clean)

                is_valid, norm_phone, err = candidate_service.validate_phone(parsed_phone or clean)
                if not is_valid:
                    return await send_vk_message(
                        session, token, int(user_id),
                        "⚠️ Не удалось распознать номер телефона.\n"
                        "Пожалуйста, введите ваш номер цифрами, например: +79001234567 или 89001234567:",
                        keyboard=make_vk_cancel_keyboard()
                    )

                session_data["data"]["phone"] = norm_phone
                session_data["step"] = "vacancy"

                vac_prompt = (
                    f"✅ Номер телефона принят: {norm_phone}\n\n"
                    "3️⃣ Выберите вакансию, которая вас интересует, нажав на кнопку ниже (или напишите цифрой 1-6):"
                )
                return await send_vk_message(
                    session, token, int(user_id),
                    vac_prompt,
                    keyboard=make_vk_vacancies_keyboard()
                )

            # Шаг: Выбор вакансии
            elif step == "vacancy":
                chosen_vac = resolve_vacancy(clean, payload_dict)
                session_data["data"]["vacancy"] = chosen_vac
                session_data["step"] = "experience"
                exp_prompt = (
                    f"Выбранная должность: {chosen_vac}\n\n"
                    "4️⃣ Опишите ваш опыт работы или стаж (если опыта нет — нажмите кнопку «Без опыта» ниже).\n\n"
                    "При наличии вы также можете прикрепить резюме или фото документов:"
                )
                return await send_vk_message(
                    session, token, int(user_id),
                    exp_prompt,
                    keyboard=make_vk_experience_keyboard()
                )

            # Шаг: Опыт работы и регистрация анкеты
            elif step == "experience":
                if payload_dict.get("exp") == "no_exp" or clean_lower in ["без опыта", "нет опыта", "нет", "0", "-"]:
                    exp_text = "Без опыта"
                else:
                    exp_text = clean or "Указан во вложениях"

                session_data["data"]["experience"] = exp_text
                cand_data = session_data["data"]
                cand_data["user_id"] = str(user_id)
                del EXTERNAL_SESSIONS[key]

                ticket_id, meta = candidate_service.register_candidate(cand_data, platform="vk")
                safe_fn = html.escape(meta["full_name"])
                safe_ph = html.escape(meta["phone"])
                safe_vc = html.escape(meta["vacancy"])
                safe_ex = html.escape(cand_data["experience"])
                cand_consent = meta["consent_timestamp"]
                recom_line = "\n💡 <b>Рекомендация:</b> кандидат без опыта, можно предложить обучение\n" if meta["offer_training"] else ""

                admin_card = (
                    f"📑 <b>НОВАЯ АНКЕТА СОИСКАТЕЛЯ #{ticket_id} [VK]</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📁 <b>База:</b> <code>resumes.db</code> (Боевая)\n"
                    f"👤 <b>ФИО:</b> {safe_fn}\n"
                    f"📞 <b>Телефон:</b> <code>{safe_ph}</code>\n"
                    f"🎯 <b>Должность:</b> {safe_vc}\n"
                    f"💼 <b>Опыт:</b> {safe_ex}\n"
                    f"⚖️ <b>Согласие 152-ФЗ:</b> <code>✅ Получено ({cand_consent})</code>\n"
                    f"⏱ <b>Время подачи:</b> <code>{datetime.now().strftime('%d.%m.%Y %H:%M')}</code>\n"
                    f"{recom_line}"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"<i>Действия кадровой службы:</i>"
                )
                try:
                    await route_new_candidate_ticket(tg_bot, admin_card, reply_markup=make_ticket_keyboard(ticket_id))
                    logger.info(f"Анкета #{ticket_id} [VK] успешно доставлена в Telegram!")
                except Exception as e:
                    logger.error(f"Ошибка доставки анкеты #{ticket_id} в Telegram: {e}")

                if attachments:
                    for att in attachments:
                        if att.get("type") == "photo":
                            photo_url = VKPhotoService.extract_best_photo_url(att.get("photo", {}).get("sizes", []))
                            if photo_url:
                                try:
                                    async with session.get(photo_url) as p_resp:
                                        if p_resp.status == 200:
                                            p_bytes = await p_resp.read()
                                            p_file = BufferedInputFile(p_bytes, filename=f"cand_{ticket_id}_doc.jpg")
                                            target_chat = CONFIG.get("HR_GROUP_ID") or CONFIG.get("SUPER_ADMIN_ID")
                                            if target_chat:
                                                await tg_bot.send_photo(
                                                    chat_id=target_chat,
                                                    photo=p_file,
                                                    caption=f"📎 Документ к анкете #{ticket_id} ({safe_fn})"
                                                )
                                except Exception as e:
                                    logger.error(f"Ошибка пересылки фото VK к анкете: {e}")

                resp_text = (
                    f"🎉 Спасибо! Ваша анкета #{ticket_id} успешно отправлена.\n\n"
                    f"Специалисты службы кадров МУП «Ульяновскэлектротранс» свяжутся с вами в ближайшее рабочее время по телефону {cand_data['phone']}.\n\n"
                    f"Вы всегда можете проверить статус заявки кнопкой «📑 Моя анкета».\n"
                    f"Телефон для справок: {CONFIG['HR_PHONE']}."
                )
                return await send_vk_message(session, token, int(user_id), resp_text, keyboard=make_vk_main_keyboard())

        # Подсказка по умолчанию
        hint = (
            "👋 Здравствуйте! Вас приветствует официальный бот по подбору персонала МУП «Ульяновскэлектротранс».\n\n"
            "Воспользуйтесь кнопками меню ниже:\n"
            "• «📝 Заполнить анкету» — подать резюме на работу\n"
            "• «📑 Моя анкета» — проверить статус вашей заявки\n"
            "• «💬 Связаться с кадровиком» — задать вопрос\n"
            "• «📚 Частые вопросы (FAQ)» — справочная информация\n"
            "• «📞 Контакты» — адрес и телефон отдела кадров"
        )
        await send_vk_message(session, token, int(user_id), hint, keyboard=make_vk_main_keyboard())

    except Exception as e:
        logger.exception(f"Критическая ошибка обработки VK от {user_id}: {e}")
        try:
            await send_vk_message(
                session, token, int(user_id),
                "⚠️ Произошла ошибка при обработке запроса. Вы возвращены в главное меню.",
                keyboard=make_vk_main_keyboard()
            )
        except Exception:
            pass


async def run_vk_gateway(bot: Bot, db: ResumeDB):
    """Фоновый воркер Long Poll ВКонтакте с автопереподключением."""
    token = CONFIG.get("VK_GROUP_TOKEN", "")
    group_id = CONFIG.get("VK_GROUP_ID", "")
    if not token or not group_id:
        logger.info("ВКонтакте: токен или group_id не указаны в .env. Модуль ожидает настройки.")
        return

    logger.info("Запуск фонового шлюза ВКонтакте (Long Poll с Exponential Backoff)...")
    timeout = ClientTimeout(total=50)
    backoff = 1

    while True:
        try:
            async with ClientSession(timeout=timeout) as session:
                server_url = "https://api.vk.com/method/groups.getLongPollServer"
                params = {"group_id": group_id, "access_token": token, "v": "5.131"}
                async with session.get(server_url, params=params) as resp:
                    data = await resp.json()

                if "error" in data:
                    logger.error(f"VK Auth Error: {data['error'].get('error_msg')}")
                    SYSTEM_METRICS["vk_online"] = False
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                    continue

                server = data["response"]["server"]
                key = data["response"]["key"]
                ts = data["response"]["ts"]
                SYSTEM_METRICS["vk_online"] = True
                backoff = 1
                logger.info("ВКонтакте: шлюз Long Poll успешно подключен.")

                while True:
                    lp_url = f"{server}?act=a_check&key={key}&ts={ts}&wait=25"
                    try:
                        async with session.get(lp_url, timeout=35) as lp_resp:
                            lp_data = await lp_resp.json()
                    except (asyncio.TimeoutError, aiohttp.ClientPayloadError, aiohttp.ServerDisconnectedError):
                        continue
                    except Exception as poll_err:
                        logger.debug(f"Временная задержка связи VK Long Poll: {poll_err}")
                        await asyncio.sleep(1)
                        continue

                    await asyncio.sleep(0.05)

                    if "failed" in lp_data:
                        code = lp_data.get("failed")
                        if code == 1:
                            ts = lp_data.get("ts", ts)
                            continue
                        elif code in (2, 3):
                            logger.info(f"VK Long Poll требует обновления сессии (код {code}). Переподключение...")
                            break

                    ts = lp_data.get("ts", ts)
                    for update in lp_data.get("updates", []):
                        if update.get("type") == "message_new":
                            msg_obj = update.get("object", {})
                            if "message" in msg_obj:
                                msg_obj = msg_obj["message"]

                            sender_id = str(msg_obj.get("from_id"))
                            body = msg_obj.get("text", "")
                            attachments = msg_obj.get("attachments", [])
                            payload = msg_obj.get("payload", "")

                            asyncio.create_task(
                                handle_vk_message(
                                    user_id=sender_id,
                                    text=body,
                                    attachments=attachments,
                                    db=db,
                                    tg_bot=bot,
                                    session=session,
                                    token=token,
                                    payload=payload
                                )
                            )

        except asyncio.CancelledError:
            logger.info("Фоновый шлюз ВКонтакте остановлен.")
            break
        except Exception as e:
            logger.warning(f"Сбой цикла VK: {e}. Повтор через {backoff} сек...")
            SYSTEM_METRICS["vk_online"] = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
