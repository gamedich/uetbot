# -*- coding: utf-8 -*-
"""
Шлюз интеграции с корпоративным мессенджером МАКС (VK Teams / MyTeam) для МУП «Ульяновскэлектротранс».
Поддерживает:
- Автономный Long Poll для API MyTeam (VK Teams).
- Интеграцию с единым сервисным слоем CandidateService и texts.py.
- Полный контур 152-ФЗ РФ (согласие перед анкетой и вопросом).
- Двусторонний мост прямого диалога соискателя из МАКС со специалистом кадровой службы в Telegram.
- Автоматически активируется при наличии MAX_BOT_TOKEN в .env.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from datetime import datetime
from typing import Optional, Dict, Any, List

from aiohttp import ClientSession, ClientTimeout
from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder

import texts
from common import (
    CONFIG,
    VACANCIES,
    EXTERNAL_SESSIONS,
    SYSTEM_METRICS,
    route_new_candidate_ticket,
    route_new_inquiry_ticket,
    safe_send
)
from candidate_service import candidate_service
from database import ResumeDB
from keyboards import make_ticket_keyboard, make_inquiry_admin_keyboard

logger = logging.getLogger("MAX_GATEWAY")

# ==================== КЛАВИАТУРЫ ДЛЯ МАКС ====================

def make_max_main_keyboard() -> List[List[Dict[str, Any]]]:
    """Главное меню соискателя в мессенджере МАКС."""
    return [
        [{"text": "📝 Заполнить анкету", "callbackData": "apply"}],
        [{"text": "📑 Моя анкета", "callbackData": "my_app"}],
        [{"text": "💬 Связаться с кадровиком", "callbackData": "ask"}],
        [
            {"text": "📚 Частые вопросы (FAQ)", "callbackData": "faq"},
            {"text": "📞 Контакты", "callbackData": "contacts"}
        ]
    ]

def make_max_consent_keyboard(command: str) -> List[List[Dict[str, Any]]]:
    """Клавиатура согласия с 152-ФЗ в МАКС."""
    return [
        [{"text": "✅ Согласен", "callbackData": command}],
        [{"text": "❌ Отмена", "callbackData": "cancel"}]
    ]

def make_max_cancel_keyboard() -> List[List[Dict[str, Any]]]:
    """Кнопка отмены для шагов FSM."""
    return [
        [{"text": "❌ Отмена", "callbackData": "cancel"}]
    ]

def make_max_vacancies_keyboard() -> List[List[Dict[str, Any]]]:
    """Список вакансий предприятия для МАКС."""
    buttons = []
    for vac in VACANCIES:
        buttons.append([{"text": f"🚋 {vac[:35]}", "callbackData": f"vac_{vac[:30]}"}])
    buttons.append([{"text": "📋 Другая должность", "callbackData": "vac_other"}])
    buttons.append([{"text": "❌ Отмена", "callbackData": "cancel"}])
    return buttons

def make_max_experience_keyboard() -> List[List[Dict[str, Any]]]:
    """Кнопка без опыта для МАКС."""
    return [
        [{"text": "Без опыта", "callbackData": "exp_no"}],
        [{"text": "❌ Отмена", "callbackData": "cancel"}]
    ]

# ==================== ОТПРАВКА СООБЩЕНИЙ ====================

async def send_max_message(
    session: ClientSession,
    api_base: str,
    token: str,
    chat_id: str,
    text: str,
    keyboard: Optional[List[List[Dict[str, Any]]]] = None
) -> bool:
    """Отправка текстового сообщения в МАКС с поддержкой инлайн-клавиатур."""
    url = f"{api_base}/messages/sendText"
    params: Dict[str, Any] = {
        "token": token,
        "chatId": str(chat_id),
        "text": text,
        "parseMode": "HTML"
    }
    if keyboard:
        params["inlineKeyboardMarkup"] = json.dumps(keyboard, ensure_ascii=False)

    try:
        async with session.post(url, json=params) as resp:
            if resp.status == 200:
                return True
            body = await resp.text()
            logger.error(f"Ошибка API МАКС ({resp.status}): {body}")
            return False
    except Exception as e:
        logger.error(f"Сбой отправки сообщения в МАКС chat_id={chat_id}: {e}")
        return False

async def answer_max_callback(session: ClientSession, api_base: str, token: str, query_id: str) -> bool:
    """Ответ на нажатие инлайн-кнопки (callback query) в МАКС."""
    url = f"{api_base}/messages/answerCallbackQuery"
    params = {"token": token, "queryId": query_id}
    try:
        async with session.post(url, json=params) as resp:
            return resp.status == 200
    except Exception:
        return False

# ==================== ОБРАБОТКА ДИАЛОГА ====================

async def handle_max_message(
    user_id: str,
    chat_id: str,
    text: str,
    db: ResumeDB,
    tg_bot: Bot,
    session: ClientSession,
    api_base: str,
    token: str,
    payload_cmd: str = ""
):
    """Интеллектуальная обработка сообщений соискателей в МАКС."""
    key = ("max", str(user_id))
    session_data = EXTERNAL_SESSIONS.get(key)
    clean = text.strip()
    clean_lower = clean.lower()

    # 1. Проверка активного моста диалога кадровик <-> кандидат
    dialog = db.get_dialog_by_user(str(user_id))
    if dialog and clean and not clean.startswith("/"):
        op_id = dialog[1]
        t_id = dialog[2]
        cand_name = dialog[3]
        relayed = (
            f"💬 <b>[Соискатель из МАКС {cand_name} | #{t_id}]:</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{clean}\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="⏹ Завершить диалог", callback_data=f"end_live_dlg_{user_id}")
        await safe_send(tg_bot, op_id, relayed, reply_markup=builder.as_markup())
        return

    # 2. Команда завершения диалога
    if clean_lower in ("/stop", "стоп", "завершить") or payload_cmd == "stop":
        if dialog:
            db.end_direct_dialog(str(user_id))
            await send_max_message(
                session, api_base, token, chat_id,
                "⏹ Диалог со специалистом отдела кадров завершён.",
                keyboard=make_max_main_keyboard()
            )
            await tg_bot.send_message(
                chat_id=dialog[1],
                text=f"⏹ Соискатель <b>{dialog[3]}</b> (МАКС id{user_id}) завершил диалог.",
                parse_mode="HTML"
            )
            return

    # 3. Отмена текущего действия
    if clean_lower in ("/cancel", "отмена", "отменить") or payload_cmd == "cancel":
        if key in EXTERNAL_SESSIONS:
            del EXTERNAL_SESSIONS[key]
        return await send_max_message(
            session, api_base, token, chat_id,
            "🚫 Действие отменено. Вы возвращены в главное меню.",
            keyboard=make_max_main_keyboard()
        )

    # 4. Моя анкета (статус)
    if clean_lower in ("/my", "моя анкета", "статус", "/status") or payload_cmd == "my_app":
        if key in EXTERNAL_SESSIONS:
            del EXTERNAL_SESSIONS[key]
        cand = db.get_candidate_by_user_id(str(user_id), platform="max")
        if cand:
            c_id = cand[0]
            name = cand[3]
            phone = cand[4]
            vac = cand[5]
            exp = cand[6]
            st = cand[7]
            admin_note = cand[8] if len(cand) > 8 else ""
            created = cand[9] if len(cand) > 9 else ""
            meet_line = f"\n📍 Назначенное собеседование: {admin_note}\n" if "Приглашен" in st and admin_note else ""

            my_text = (
                "📑 <b>ВАША АНКЕТА В МУП «УЛЬЯНОВСКЭЛЕКТРОТРАНС»</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 Номер заявки: <b>#{c_id}</b>\n"
                f"👤 ФИО: {name}\n"
                f"📞 Телефон: <code>{phone}</code>\n"
                f"🎯 Должность: {vac}\n"
                f"💼 Опыт работы: {exp}\n"
                f"📊 Статус: <b>{st}</b>\n"
                f"⏱ Дата подачи: <code>{created}</code>\n"
                f"{meet_line}"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "Для уточнения данных напишите нам кнопкой «💬 Связаться с кадровиком»."
            )
        else:
            my_text = (
                "📑 У вас пока нет поданных анкет в МУП «Ульяновскэлектротранс».\n\n"
                "Чтобы отправить резюме на рассмотрение кадровой службы, нажмите кнопку «📝 Заполнить анкету»."
            )
        return await send_max_message(session, api_base, token, chat_id, my_text, keyboard=make_max_main_keyboard())

    # 5. Контакты (синхронизировано с texts.py)
    if clean_lower in ("контакты", "/contacts") or payload_cmd == "contacts":
        text_contacts = texts.CONTACTS_SCREEN
        return await send_max_message(session, api_base, token, chat_id, text_contacts, keyboard=make_max_main_keyboard())

    # 6. FAQ (синхронизировано с texts.py)
    if clean_lower in ("faq", "/faq", "вопросы") or payload_cmd == "faq":
        faq_text = (
            "📚 <b>ЧАСТЫЕ ВОПРОСЫ (FAQ)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"{texts.FAQ_DATA.get('faq_training', '')}\n\n"
            f"{texts.FAQ_DATA.get('faq_salary', '')}"
        )
        return await send_max_message(session, api_base, token, chat_id, faq_text, keyboard=make_max_main_keyboard())

    # 7. Подача анкеты (/apply) через CandidateService
    if clean_lower in ("/apply", "подать анкету", "заполнить анкету") or payload_cmd == "apply":
        can_apply, reason, info = candidate_service.check_can_apply(str(user_id), platform="max")
        if not can_apply and info:
            ticket_id = info.get("ticket_id")
            if reason == "unprocessed":
                msg = (
                    f"⚠️ У вас уже есть активная анкета №{ticket_id} на рассмотрении!\n\n"
                    f"Специалисты отдела кадров обязательно свяжутся с вами. Телефон: {CONFIG['HR_PHONE']}."
                )
                return await send_max_message(session, api_base, token, chat_id, msg, keyboard=make_max_main_keyboard())
            elif reason in ("cooldown", "rejected_cooldown"):
                msg = (
                    f"⏳ Повторная подача анкеты временно недоступна.\n"
                    f"По предыдущей анкете №{ticket_id} было принято решение об отказе.\n"
                    f"Повторная подача возможна с {info.get('available_date')} (осталось {info.get('days_left')} дн.)."
                )
                return await send_max_message(session, api_base, token, chat_id, msg, keyboard=make_max_main_keyboard())

        EXTERNAL_SESSIONS[key] = {"step": "waiting_consent_apply", "data": {}}
        apply_prompt = (
            f"{texts.CONSENT_SURVEY_PROMPT}\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Для продолжения нажмите кнопку «✅ Согласен» ниже:</i>"
        )
        return await send_max_message(session, api_base, token, chat_id, apply_prompt, keyboard=make_max_consent_keyboard("consent_apply"))

    # 8. Вопрос кадровику (/ask)
    if clean_lower in ("/ask", "задать вопрос", "связаться с кадровиком") or payload_cmd == "ask":
        is_prod = (CONFIG.get("ENVIRONMENT") == "PROD")
        cooldown = int(db.get_setting("cooldown_seconds", str(CONFIG.get("COOLDOWN_SECONDS", 1200)))) if is_prod else 0
        can_ask, seconds_left = db.check_inquiry_cooldown(str(user_id), cooldown)
        if not can_ask:
            minutes_left = max(1, (seconds_left + 59) // 60)
            msg_text = f"⏳ Вы сможете задать следующий вопрос через {minutes_left} мин.\nСрочные вопросы: {CONFIG['HR_PHONE']}"
            return await send_max_message(session, api_base, token, chat_id, msg_text, keyboard=make_max_main_keyboard())

        EXTERNAL_SESSIONS[key] = {"step": "waiting_consent_ask", "data": {}}
        ask_prompt = (
            f"{texts.CONSENT_INQUIRY_PROMPT}\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Для продолжения нажмите кнопку «✅ Согласен» ниже:</i>"
        )
        return await send_max_message(session, api_base, token, chat_id, ask_prompt, keyboard=make_max_consent_keyboard("consent_ask"))

    # ==================== ПОШАГОВЫЕ СЦЕНАРИИ ====================
    if session_data:
        step = session_data.get("step")

        # А. Согласие перед анкетой
        if step == "waiting_consent_apply":
            if payload_cmd == "consent_apply" or clean_lower in ("согласен", "да", "✅ согласен"):
                session_data["data"]["consent_timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                session_data["step"] = "name"
                msg = "✅ Согласие принято!\n\n1️⃣ Введите ваши <b>ФИО полностью</b> (например: Иванов Иван Иванович):"
                return await send_max_message(session, api_base, token, chat_id, msg, keyboard=make_max_cancel_keyboard())
            else:
                return await send_max_message(
                    session, api_base, token, chat_id,
                    "⚠️ Для заполнения анкеты нажмите кнопку «✅ Согласен» ниже:",
                    keyboard=make_max_consent_keyboard("consent_apply")
                )

        # Б. Ввод ФИО
        elif step == "name":
            is_valid, clean_name, err = candidate_service.validate_fio(clean)
            if not is_valid:
                return await send_max_message(
                    session, api_base, token, chat_id,
                    f"⚠️ {err or 'Пожалуйста, укажите имя и фамилию полностью (минимум 2 слова):'}",
                    keyboard=make_max_cancel_keyboard()
                )
            session_data["data"]["full_name"] = clean_name
            session_data["step"] = "phone"
            prompt = "2️⃣ Укажите ваш <b>контактный номер телефона</b> (например: <code>+79001234567</code> или <code>89001234567</code>):"
            return await send_max_message(session, api_base, token, chat_id, prompt, keyboard=make_max_cancel_keyboard())

        # В. Ввод телефона
        elif step == "phone":
            is_valid, norm_phone, err = candidate_service.validate_phone(clean)
            if not is_valid:
                return await send_max_message(
                    session, api_base, token, chat_id,
                    "⚠️ Некорректный номер. Введите 11 цифр (например: <code>+79001234567</code>):",
                    keyboard=make_max_cancel_keyboard()
                )
            session_data["data"]["phone"] = norm_phone
            session_data["step"] = "vacancy"
            prompt = f"✅ Номер принят: <code>{norm_phone}</code>\n\n3️⃣ Выберите вакансию из списка ниже:"
            return await send_max_message(session, api_base, token, chat_id, prompt, keyboard=make_max_vacancies_keyboard())

        # Г. Выбор вакансии
        elif step == "vacancy":
            chosen_vac = "Другая должность"
            if payload_cmd.startswith("vac_"):
                val = payload_cmd.replace("vac_", "")
                for v in VACANCIES:
                    if v.startswith(val):
                        chosen_vac = v
                        break
            elif clean:
                chosen_vac = clean

            session_data["data"]["vacancy"] = chosen_vac
            session_data["step"] = "experience"
            prompt = (
                f"Выбранная должность: <b>{chosen_vac}</b>\n\n"
                "4️⃣ Опишите ваш <b>опыт работы</b> или стаж (если опыта нет — нажмите «Без опыта» ниже):"
            )
            return await send_max_message(session, api_base, token, chat_id, prompt, keyboard=make_max_experience_keyboard())

        # Д. Опыт работы и отправка анкеты
        elif step == "experience":
            if payload_cmd == "exp_no" or clean_lower in ("без опыта", "нет", "нет опыта", "0", "-"):
                exp_text = "Без опыта"
            else:
                exp_text = clean or "Указан в резюме"

            session_data["data"]["experience"] = exp_text
            cand_data = session_data["data"]
            if key in EXTERNAL_SESSIONS:
                del EXTERNAL_SESSIONS[key]

            cand_data["user_id"] = str(user_id)
            ticket_id, meta = candidate_service.register_candidate(cand_data, platform="max")
            fn = meta["full_name"]
            ph = meta["phone"]
            vc = meta["vacancy"]
            cand_consent = meta["consent_timestamp"]
            recom_line = "\n💡 <b>Рекомендация:</b> кандидат без опыта, можно предложить обучение\n" if meta["offer_training"] else ""

            safe_fn = html.escape(fn)
            safe_ph = html.escape(ph)
            safe_vc = html.escape(vc)
            safe_ex = html.escape(exp_text)

            admin_card = (
                f"📑 <b>НОВАЯ АНКЕТА СОИСКАТЕЛЯ #{ticket_id} [MAX]</b>\n"
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
                logger.info(f"Анкета #{ticket_id} [MAX] успешно доставлена в Telegram!")
            except Exception as e:
                logger.error(f"Ошибка отправки анкеты #{ticket_id} в Telegram: {e}")

            resp_text = (
                f"🎉 Спасибо! Ваша анкета #{ticket_id} успешно отправлена.\n\n"
                f"Специалисты отдела кадров свяжутся с вами по телефону {ph}.\n"
                f"Статус заявки доступен по кнопке «📑 Моя анкета».\n"
                f"Телефон отдела кадров: {CONFIG['HR_PHONE']}."
            )
            return await send_max_message(session, api_base, token, chat_id, resp_text, keyboard=make_max_main_keyboard())

        # Е. Согласие перед вопросом
        elif step == "waiting_consent_ask":
            if payload_cmd == "consent_ask" or clean_lower in ("согласен", "да", "✅ согласен"):
                session_data["data"]["consent_timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                session_data["step"] = "waiting_question"
                msg = "💬 Напишите ваш вопрос <b>одним сообщением</b>. Он будет передан специалисту кадровой службы:"
                return await send_max_message(session, api_base, token, chat_id, msg, keyboard=make_max_cancel_keyboard())
            else:
                return await send_max_message(
                    session, api_base, token, chat_id,
                    "⚠️ Для отправки вопроса нажмите кнопку «✅ Согласен» ниже:",
                    keyboard=make_max_consent_keyboard("consent_ask")
                )

        # Ж. Текст вопроса
        elif step == "waiting_question":
            is_valid, clean_q, err = candidate_service.validate_question(clean)
            if not is_valid:
                return await send_max_message(
                    session, api_base, token, chat_id,
                    f"⚠️ {err or 'Напишите ваш вопрос текстом в одном сообщении:'}",
                    keyboard=make_max_cancel_keyboard()
                )

            inq_consent = session_data.get("data", {}).get("consent_timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if key in EXTERNAL_SESSIONS:
                del EXTERNAL_SESSIONS[key]

            last_cand = db.get_candidate_by_user_id(str(user_id), platform="max")
            if last_cand:
                ticket_id = last_cand[0]
                full_name = last_cand[3]
                phone = last_cand[4]
                vacancy = last_cand[5]
            else:
                ticket_id = None
                full_name = f"Пользователь МАКС id{user_id}"
                phone = "Не указан"
                vacancy = "Анкета не подана"

            ok, status_msg, inquiry_id = candidate_service.submit_inquiry(
                user_id=str(user_id),
                question_text=clean_q,
                platform="max",
                ticket_id=ticket_id,
                full_name=full_name,
                phone=phone,
                vacancy=vacancy,
                consent_timestamp=inq_consent
            )

            safe_clean = html.escape(clean_q)
            safe_full = html.escape(full_name)
            card_text = (
                f"📩 <b>ОБРАЩЕНИЕ СОИСКАТЕЛЯ #{inquiry_id} [MAX]</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📁 <b>База:</b> <code>resumes.db</code> (Боевая)\n"
                f"👤 <b>Кандидат:</b> {safe_full}\n"
                f"📞 <b>Телефон:</b> <code>{html.escape(phone)}</code>\n"
                f"🎯 <b>Вакансия:</b> {html.escape(vacancy)}\n"
                f"⚖️ <b>Согласие 152-ФЗ:</b> <code>✅ Получено ({inq_consent})</code>\n"
                f"⏱ <b>Время:</b> <code>{datetime.now().strftime('%d.%m.%Y %H:%M')}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"❓ <b>Вопрос:</b>\n"
                f"«{safe_clean}»"
            )
            try:
                await route_new_inquiry_ticket(tg_bot, card_text, reply_markup=make_inquiry_admin_keyboard(inquiry_id))
                logger.info(f"Вопрос #{inquiry_id} [MAX] успешно доставлен в Telegram!")
            except Exception as e:
                logger.error(f"Ошибка отправки вопроса #{inquiry_id} в Telegram: {e}")

            conf_text = (
                f"✅ Ваш вопрос принят! (Обращение #{inquiry_id})\n\n"
                f"Специалист рассмотрит его в рабочее время ({CONFIG['HR_SCHEDULE']}). Ответ поступит прямо в этот диалог.\n\n"
                f"Телефон отдела кадров: {CONFIG['HR_PHONE']}."
            )
            return await send_max_message(session, api_base, token, chat_id, conf_text, keyboard=make_max_main_keyboard())

    # Главное меню по умолчанию
    welcome = (
        "👋 Здравствуйте! Вас приветствует официальный бот по подбору персонала <b>МУП «Ульяновскэлектротранс»</b> в мессенджере МАКС.\n\n"
        "Воспользуйтесь кнопками меню ниже для подачи анкеты или связи с кадровой службой:"
    )
    return await send_max_message(session, api_base, token, chat_id, welcome, keyboard=make_max_main_keyboard())

# ==================== ГЛАВНЫЙ ЦИКЛ LONG POLL ДЛЯ МАКС ====================

async def run_max_gateway(bot: Bot, db: ResumeDB):
    """Фоновый воркер Long Poll для мессенджера МАКС внутри единого процесса бота."""
    token = CONFIG.get("MAX_BOT_TOKEN", "").strip()
    api_base = CONFIG.get("MAX_API_BASE", "https://api.myteam.mail.ru/bot/v1").rstrip("/")

    if not token:
        logger.info("МАКС: токен не указан в .env (MAX_BOT_TOKEN пуст). Шлюз ожидает настройки.")
        return

    logger.info("Запуск фонового шлюза МАКС внутри единого процесса бота...")
    timeout = ClientTimeout(total=45)
    last_event_id = 0

    while True:
        try:
            async with ClientSession(timeout=timeout) as session:
                SYSTEM_METRICS["max_online"] = True
                logger.info("МАКС: подключение к серверу мессенджера успешно установлено.")

                while True:
                    poll_url = f"{api_base}/events/get"
                    params = {"token": token, "lastEventId": last_event_id, "pollTime": 25}
                    async with session.get(poll_url, params=params) as resp:
                        if resp.status != 200:
                            await asyncio.sleep(5)
                            continue
                        events_data = await resp.json()

                    for event in events_data.get("events", []):
                        last_event_id = max(last_event_id, event.get("eventId", last_event_id))
                        ev_type = event.get("type")

                        if ev_type == "newMessage":
                            payload = event.get("payload", {})
                            chat_id = payload.get("chat", {}).get("chatId")
                            from_user = payload.get("from", {}).get("userId") or chat_id
                            text = payload.get("text", "")
                            if chat_id and text:
                                asyncio.create_task(
                                    handle_max_message(
                                        user_id=str(from_user),
                                        chat_id=str(chat_id),
                                        text=text,
                                        db=db,
                                        tg_bot=bot,
                                        session=session,
                                        api_base=api_base,
                                        token=token
                                    )
                                )

                        elif ev_type == "callbackQuery":
                            payload = event.get("payload", {})
                            query_id = payload.get("queryId")
                            cb_data = payload.get("callbackData", "")
                            chat_id = payload.get("message", {}).get("chat", {}).get("chatId")
                            from_user = payload.get("from", {}).get("userId") or chat_id
                            if query_id:
                                asyncio.create_task(answer_max_callback(session, api_base, token, query_id))
                            if chat_id:
                                asyncio.create_task(
                                    handle_max_message(
                                        user_id=str(from_user),
                                        chat_id=str(chat_id),
                                        text="",
                                        db=db,
                                        tg_bot=bot,
                                        session=session,
                                        api_base=api_base,
                                        token=token,
                                        payload_cmd=cb_data
                                    )
                                )

        except asyncio.CancelledError:
            logger.info("Фоновый шлюз МАКС остановлен.")
            break
        except Exception as e:
            logger.warning(f"Сбой цикла Long Poll МАКС: {e}. Переподключение через 5 сек...")
            SYSTEM_METRICS["max_online"] = False
            await asyncio.sleep(5)
