# -*- coding: utf-8 -*-
"""
Шлюз интеграции с корпоративным мессенджером МАКС (VK Teams / MyTeam) для МУП «Ульяновскэлектротранс».
Работает внутри единого процесса бота в фоновой задаче asyncio.
Автоматически активируется при наличии MAX_BOT_TOKEN в .env.
"""
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

from common import (
    CONFIG,
    VACANCIES,
    EXTERNAL_SESSIONS,
    SYSTEM_METRICS,
    route_new_candidate_ticket,
    route_new_inquiry_ticket,
    safe_send
)
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
        builder.button(text="⏹ Завершить диалог", callback_data=f"stop_live_{user_id}")
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

    # 5. Контакты
    if clean_lower in ("контакты", "/contacts") or payload_cmd == "contacts":
        text_contacts = (
            f"🏢 <b>МУП «Ульяновскэлектротранс» (Отдел кадров)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 Адрес: {CONFIG['HR_ADDRESS']}\n"
            f"☎️ Телефон: {CONFIG['HR_PHONE']}\n"
            f"⏰ График приёма: {CONFIG['HR_SCHEDULE']}\n\n"
            "🚋 Проезд трамваями № 4, 22 до остановки «Улица Гончарова» или «ЦУМ»."
        )
        return await send_max_message(session, api_base, token, chat_id, text_contacts, keyboard=make_max_main_keyboard())

    # 6. FAQ
    if clean_lower in ("faq", "/faq", "вопросы") or payload_cmd == "faq":
        faq_text = (
            "📚 <b>ЧАСТЫЕ ВОПРОСЫ (FAQ)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "🎓 <b>Обучение на водителя:</b>\n"
            "Бесплатное обучение от 4.5 до 6 месяцев со стипендией и 100% трудоустройством.\n\n"
            "🛡 <b>Соцпакет:</b>\n"
            "Бесплатный проезд на электротранспорте, доставка служебными рейсами, льготная пенсия для водителей."
        )
        return await send_max_message(session, api_base, token, chat_id, faq_text, keyboard=make_max_main_keyboard())

    # 7. Подача анкеты (/apply)
    if clean_lower in ("/apply", "подать анкету", "заполнить анкету") or payload_cmd == "apply":
        can_apply, reason, info = db.check_candidate_can_apply(str(user_id), platform="max")
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
            "⚖️ <b>СОГЛАСИЕ НА ОБРАБОТКУ ПЕРСОНАЛЬНЫХ ДАННЫХ (152-ФЗ РФ)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Для подачи анкеты на трудоустройство в МУП «Ульяновскэлектротранс» (г. Ульяновск, ул. Гончарова, 17) "
            "требуется ваше согласие на обработку персональных данных (ФИО, телефон, опыт работы).\n\n"
            "🎯 <b>Цель:</b> рассмотрение кандидатуры на трудоустройство.\n"
            "🔒 <b>Защита:</b> данные защищены и уничтожаются по вашему требованию (ст. 21 152-ФЗ).\n"
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
            "⚖️ <b>СОГЛАСИЕ НА ОБРАБОТКУ ПЕРСОНАЛЬНЫХ ДАННЫХ (152-ФЗ РФ)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Направляя обращение в кадровую службу МУП «Ульяновскэлектротранс», вы подтверждаете согласие "
            "на обработку контактных данных для рассмотрения вопроса и связи с вами.\n\n"
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
            if len(clean.split()) < 2:
                return await send_max_message(
                    session, api_base, token, chat_id,
                    "⚠️ Пожалуйста, укажите имя и фамилию полностью (минимум 2 слова):",
                    keyboard=make_max_cancel_keyboard()
                )
            session_data["data"]["full_name"] = clean
            session_data["step"] = "phone"
            prompt = "2️⃣ Укажите ваш <b>контактный номер телефона</b> (например: <code>+79001234567</code> или <code>89001234567</code>):"
            return await send_max_message(session, api_base, token, chat_id, prompt, keyboard=make_max_cancel_keyboard())

        # В. Ввод телефона
        elif step == "phone":
            digits = re.sub(r'\D', '', clean)
            if len(digits) == 11 and digits.startswith(('7', '8')):
                norm_phone = "+7" + digits[1:]
            else:
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