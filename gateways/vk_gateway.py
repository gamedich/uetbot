# -*- coding: utf-8 -*-
"""
Шлюз интеграции с ВКонтакте для МУП «Ульяновскэлектротранс» (Production Ready).

Архитектурные принципы модуля:
1. Single Source of Truth (SSOT): Все тексты шагов, проверок и карточек
   импортируются напрямую из texts.py через clean_html(). В модуле нет хардкода!
2. Полная синхронизация 16-шаговой воронки соискателя с Telegram (CandidateForm).
3. Интерактивные цветные клавиатуры ВКонтакте под каждый этап анкетирования.
4. Контур 152-ФЗ РФ: обязательное согласие на шаге 0, защита от дублей и кулдауны.
5. Умный триггер бесплатного обучения водителей трамвая и троллейбуса со стипендией.
6. Доставка полных блочных карточек в Telegram-группу отдела кадров в формате HTML.
7. Сквозной Live-Chat мост: соискатель в ВК <-> кадровик в Telegram в реальном времени.
8. Потоковая передача фото и документов в ОЗУ (без сохранения на диск по 152-ФЗ).
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
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp
from aiohttp import ClientSession, ClientTimeout
from aiogram import Bot
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

import texts
try:
    from services.candidate_service import candidate_service
except ImportError:
    from services.candidate_service import candidate_service

from common import (
    CONFIG,
    EXTERNAL_SESSIONS,
    SYSTEM_METRICS,
    VACANCIES,
    db,
    get_all_vacancies,
    route_new_candidate_ticket,
    route_new_inquiry_ticket,
    safe_send,
)
from database import ResumeDB
from keyboards import make_inquiry_admin_keyboard, make_ticket_keyboard
from texts import clean_html, format_hr_card_full, format_survey_step16_review

logger = logging.getLogger("VK_GATEWAY")


# ==============================================================================
# БАЗА ЗНАНИЙ (СИНХРОНИЗИРОВАНА С TEXTS.PY ЧЕРЕЗ CLEAN_HTML)
# ==============================================================================

VK_FAQ_DATA: Dict[str, str] = {
    "training": clean_html(texts.FAQ_DATA.get("faq_training", "")),
    "housing": clean_html(texts.FAQ_DATA.get("faq_housing", "")),
    "salary": clean_html(texts.FAQ_DATA.get("faq_salary", "")),
    "docs": clean_html(texts.FAQ_DATA.get("faq_docs", "")),
}


# ==============================================================================
# МУЛЬТИМЕДИА СЕРВИС ВКОНТАКТЕ (RAM-ONLY 152-ФЗ)
# ==============================================================================

class VKPhotoService:
    """Сервис взаимодействия с мультимедиа API ВКонтакте."""

    def __init__(self, vk_token: str, session: ClientSession) -> None:
        self.vk_token = vk_token
        self.session = session
        self.api_version = "5.131"

    async def _vk_call(self, method: str, **params: Any) -> Dict[str, Any]:
        params["access_token"] = self.vk_token
        params["v"] = self.api_version
        async with self.session.get(f"https://api.vk.com/method/{method}", params=params) as resp:
            data = await resp.json()
            if "error" in data:
                raise RuntimeError(f"VK API Error: {data['error'].get('error_msg')}")
            return data.get("response", {})

    async def upload_photo_to_vk_chat(self, peer_id: int, image_bytes: io.BytesIO) -> str:
        """Загрузка изображения в сообщения ВК без сохранения на диск (152-ФЗ)."""
        upload_server = await self._vk_call("photos.getMessagesUploadServer", peer_id=peer_id)
        upload_url = upload_server["upload_url"]

        form = aiohttp.FormData()
        form.add_field("photo", image_bytes, filename="document_photo.jpg", content_type="image/jpeg")
        async with self.session.post(upload_url, data=form) as upload_resp:
            upload_result = await upload_resp.json(content_type=None)

        saved_photos = await self._vk_call(
            "photos.saveMessagesPhoto",
            photo=upload_result.get("photo"),
            server=upload_result.get("server"),
            hash=upload_result.get("hash"),
        )

        photo_obj = saved_photos[0]
        return f"photo{photo_obj['owner_id']}_{photo_obj['id']}"

    @staticmethod
    def extract_best_photo_url(sizes: List[Dict[str, Any]]) -> Optional[str]:
        """Выбор максимального разрешения изображения из массива sizes."""
        if not sizes:
            return None
        valid_sizes = [s for s in sizes if "url" in s]
        if not valid_sizes:
            return None
        best = max(valid_sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))
        return best.get("url")


# ==============================================================================
# КЛАВИАТУРЫ ВКОНТАКТЕ ДЛЯ ВСЕХ 16 ШАГОВ АНКЕТЫ
# ==============================================================================

def make_vk_main_keyboard() -> str:
    """Главное меню ВКонтакте с постоянными цветными кнопками."""
    buttons = [
        [{"action": {"type": "text", "label": "📝 Заполнить анкету", "payload": json.dumps({"command": "apply"})}, "color": "positive"}],
        [{"action": {"type": "text", "label": "📑 Моя анкета", "payload": json.dumps({"command": "my_app"})}, "color": "primary"}],
        [{"action": {"type": "text", "label": "💬 Связаться с кадровиком", "payload": json.dumps({"command": "ask"})}, "color": "secondary"}],
        [
            {"action": {"type": "text", "label": "📚 Частые вопросы (FAQ)", "payload": json.dumps({"command": "faq"})}, "color": "secondary"},
            {"action": {"type": "text", "label": "📞 Контакты", "payload": json.dumps({"command": "contacts"})}, "color": "secondary"},
        ],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_faq_keyboard() -> str:
    """Клавиатура разделов базы знаний (FAQ) ВКонтакте."""
    buttons = [
        [{"action": {"type": "text", "label": "🎓 Обучение на водителя", "payload": json.dumps({"faq": "training"})}, "color": "primary"}],
        [{"action": {"type": "text", "label": "🏠 Жилье и общежитие", "payload": json.dumps({"faq": "housing"})}, "color": "secondary"}],
        [{"action": {"type": "text", "label": "💰 Зарплата и льготная пенсия", "payload": json.dumps({"faq": "salary"})}, "color": "secondary"}],
        [{"action": {"type": "text", "label": "📄 Необходимые документы", "payload": json.dumps({"faq": "docs"})}, "color": "secondary"}],
        [
            {"action": {"type": "text", "label": "📝 Заполнить анкету", "payload": json.dumps({"command": "apply"})}, "color": "positive"},
            {"action": {"type": "text", "label": "⬅️ Главное меню", "payload": json.dumps({"command": "start"})}, "color": "secondary"},
        ],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_cancel_keyboard() -> str:
    """Клавиатура с кнопкой отмены текущего сценария."""
    buttons = [
        [{"action": {"type": "text", "label": "❌ Отмена", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}]
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_consent_keyboard() -> str:
    """Шаг 0: Клавиатура согласия на обработку персональных данных (152-ФЗ РФ)."""
    buttons = [
        [{"action": {"type": "text", "label": "✅ Согласен на обработку ПДн", "payload": json.dumps({"consent": "yes"})}, "color": "positive"}],
        [{"action": {"type": "text", "label": "❌ Отмена", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_city_keyboard() -> str:
    """Шаг 4: Выбор города проживания соискателя."""
    buttons = [
        [{"action": {"type": "text", "label": "Ульяновск", "payload": json.dumps({"city": "Ульяновск"})}, "color": "primary"}],
        [{"action": {"type": "text", "label": "Димитровград", "payload": json.dumps({"city": "Димитровград"})}, "color": "secondary"}],
        [{"action": {"type": "text", "label": "Другой населенный пункт", "payload": json.dumps({"city": "other"})}, "color": "secondary"}],
        [{"action": {"type": "text", "label": "❌ Отмена", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_vacancies_keyboard() -> str:
    """Шаг 5: Выбор должности со скрытием закрытых вакансий предприятия."""
    val = db.get_setting("closed_vacancies", "[]")
    try:
        closed = set(json.loads(val))
    except Exception:
        closed = set()

    vacancies = get_all_vacancies()
    active_vacs = [v for v in vacancies if v not in closed]

    buttons: List[List[Dict[str, Any]]] = []
    for vac in active_vacs:
        buttons.append([{
            "action": {
                "type": "text",
                "label": vac[:40],
                "payload": json.dumps({"vac": vac}),
            },
            "color": "primary",
        }])

    buttons.append([{
        "action": {
            "type": "text",
            "label": "Другая должность",
            "payload": json.dumps({"vac": "other"}),
        },
        "color": "secondary",
    }])
    buttons.append([{
        "action": {
            "type": "text",
            "label": "❌ Отмена",
            "payload": json.dumps({"command": "cancel"}),
        },
        "color": "negative",
    }])

    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_license_keyboard() -> str:
    """Шаг 6: Водительское удостоверение соискателя."""
    buttons = [
        [
            {"action": {"type": "text", "label": "Нет прав", "payload": json.dumps({"lic": "Нет прав"})}, "color": "secondary"},
            {"action": {"type": "text", "label": "Категория B", "payload": json.dumps({"lic": "Категория B"})}, "color": "primary"},
        ],
        [
            {"action": {"type": "text", "label": "Категории B, C", "payload": json.dumps({"lic": "Категории B, C"})}, "color": "primary"},
            {"action": {"type": "text", "label": "Категория D", "payload": json.dumps({"lic": "Категория D"})}, "color": "primary"},
        ],
        [
            {"action": {"type": "text", "label": "Трамвай / Троллейбус", "payload": json.dumps({"lic": "Трамвай/Троллейбус"})}, "color": "positive"},
            {"action": {"type": "text", "label": "Другие категории", "payload": json.dumps({"lic": "Другие категории"})}, "color": "secondary"},
        ],
        [{"action": {"type": "text", "label": "❌ Отмена", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_experience_keyboard() -> str:
    """Шаг 7: Стаж / опыт работы соискателя."""
    buttons = [
        [
            {"action": {"type": "text", "label": "Без опыта", "payload": json.dumps({"exp": "Без опыта"})}, "color": "positive"},
            {"action": {"type": "text", "label": "До 1 года", "payload": json.dumps({"exp": "До 1 года"})}, "color": "primary"},
        ],
        [
            {"action": {"type": "text", "label": "1–3 года", "payload": json.dumps({"exp": "1-3 года"})}, "color": "primary"},
            {"action": {"type": "text", "label": "Более 3 лет", "payload": json.dumps({"exp": "Более 3 лет"})}, "color": "primary"},
        ],
        [{"action": {"type": "text", "label": "❌ Отмена", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_education_keyboard() -> str:
    """Шаг 8: Уровень образования соискателя."""
    buttons = [
        [{"action": {"type": "text", "label": "Среднее специальное", "payload": json.dumps({"edu": "Среднее специальное"})}, "color": "primary"}],
        [{"action": {"type": "text", "label": "Высшее", "payload": json.dumps({"edu": "Высшее"})}, "color": "primary"}],
        [{"action": {"type": "text", "label": "Среднее общее (11 кл.)", "payload": json.dumps({"edu": "Среднее общее"})}, "color": "secondary"}],
        [{"action": {"type": "text", "label": "Неоконченное высшее", "payload": json.dumps({"edu": "Неоконченное высшее"})}, "color": "secondary"}],
        [{"action": {"type": "text", "label": "❌ Отмена", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_relocation_keyboard() -> str:
    """Шаг 9: Готовность к переезду в г. Ульяновск."""
    buttons = [
        [{"action": {"type": "text", "label": "Уже проживаю в Ульяновске", "payload": json.dumps({"rel": "Уже проживаю"})}, "color": "positive"}],
        [
            {"action": {"type": "text", "label": "Да, готов к переезду", "payload": json.dumps({"rel": "Да, готов"})}, "color": "primary"},
            {"action": {"type": "text", "label": "Нет, не готов", "payload": json.dumps({"rel": "Нет"})}, "color": "secondary"},
        ],
        [{"action": {"type": "text", "label": "❌ Отмена", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_dormitory_keyboard() -> str:
    """Шаг 10: Потребность в служебном общежитии предприятия."""
    buttons = [
        [{"action": {"type": "text", "label": "Да, требуется общежитие", "payload": json.dumps({"dorm": "Да, требуется"})}, "color": "primary"}],
        [{"action": {"type": "text", "label": "Нет, жильё есть", "payload": json.dumps({"dorm": "Нет, есть жилье"})}, "color": "secondary"}],
        [{"action": {"type": "text", "label": "❌ Отмена", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_schedule_keyboard() -> str:
    """Шаг 11: Готовность к сменному графику 2/2."""
    buttons = [
        [{"action": {"type": "text", "label": "Да, готов к графику 2/2", "payload": json.dumps({"shift": "Да, готов"})}, "color": "positive"}],
        [{"action": {"type": "text", "label": "Нет, не подходит", "payload": json.dumps({"shift": "Нет"})}, "color": "secondary"}],
        [{"action": {"type": "text", "label": "❌ Отмена", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_health_keyboard() -> str:
    """Шаг 12: Медицинские противопоказания."""
    buttons = [
        [{"action": {"type": "text", "label": "Противопоказаний нет", "payload": json.dumps({"med": "Нет"})}, "color": "positive"}],
        [{"action": {"type": "text", "label": "Есть ограничения", "payload": json.dumps({"med": "Есть ограничения"})}, "color": "secondary"}],
        [{"action": {"type": "text", "label": "❌ Отмена", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_criminal_keyboard() -> str:
    """Шаг 13: Проверка судимости по ст. 86 УК РФ."""
    buttons = [
        [{"action": {"type": "text", "label": "Судимости нет", "payload": json.dumps({"crim": "Нет"})}, "color": "positive"}],
        [{"action": {"type": "text", "label": "Имеется судимость", "payload": json.dumps({"crim": "Имеется"})}, "color": "secondary"}],
        [{"action": {"type": "text", "label": "❌ Отмена", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_source_keyboard() -> str:
    """Шаг 14: Источник информирования о вакансиях."""
    buttons = [
        [
            {"action": {"type": "text", "label": "Реклама в транспорте", "payload": json.dumps({"src": "Реклама в транспорте"})}, "color": "primary"},
            {"action": {"type": "text", "label": "Соцсети / ВКонтакте", "payload": json.dumps({"src": "ВКонтакте"})}, "color": "primary"},
        ],
        [
            {"action": {"type": "text", "label": "Сайт предприятия", "payload": json.dumps({"src": "Сайт предприятия"})}, "color": "secondary"},
            {"action": {"type": "text", "label": "От знакомых", "payload": json.dumps({"src": "От знакомых"})}, "color": "secondary"},
        ],
        [{"action": {"type": "text", "label": "❌ Отмена", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_skip_keyboard() -> str:
    """Клавиатура с кнопкой пропуска необязательного шага."""
    buttons = [
        [{"action": {"type": "text", "label": "Пропустить ➡️", "payload": json.dumps({"skip": True})}, "color": "secondary"}],
        [{"action": {"type": "text", "label": "❌ Отмена", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_confirm_keyboard() -> str:
    """Шаг 16: Финальное подтверждение анкеты соискателем."""
    buttons = [
        [{"action": {"type": "text", "label": "✅ Подтвердить и отправить", "payload": json.dumps({"confirm": "yes"})}, "color": "positive"}],
        [{"action": {"type": "text", "label": "❌ Отменить анкету", "payload": json.dumps({"command": "cancel"})}, "color": "negative"}],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_blocked_apply_keyboard() -> str:
    """Клавиатура при наличии активной заявки соискателя."""
    buttons = [
        [{"action": {"type": "text", "label": "📑 Моя анкета", "payload": json.dumps({"command": "my_app"})}, "color": "primary"}],
        [{"action": {"type": "text", "label": "💬 Связаться с кадровиком", "payload": json.dumps({"command": "ask"})}, "color": "secondary"}],
        [{"action": {"type": "text", "label": "⬅️ Главное меню", "payload": json.dumps({"command": "start"})}, "color": "secondary"}],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_dialog_keyboard() -> str:
    """Клавиатура соискателя во время прямого диалога с кадровой службой."""
    buttons = [
        [{"action": {"type": "text", "label": "⏹ Завершить диалог", "payload": json.dumps({"command": "stop"})}, "color": "negative"}]
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def make_vk_reply_keyboard(ticket_id: int = 0) -> str:
    """Инлайн-кнопка под сообщением кадровика для удобного ответа соискателя."""
    buttons = [
        [
            {
                "action": {
                    "type": "text",
                    "label": "💬 Ответить кадровику",
                    "payload": json.dumps({"command": "reply_hr", "ticket_id": ticket_id}),
                },
                "color": "primary",
            }
        ]
    ]
    return json.dumps({"inline": True, "buttons": buttons}, ensure_ascii=False)


# ==============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ВАЛИДАЦИИ И МАРШРУТИЗАЦИИ
# ==============================================================================

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


def resolve_vacancy(text: str, payload_dict: Optional[Dict[str, Any]] = None) -> str:
    """Умное сопоставление вакансии по тексту, кнопке или payload."""
    if payload_dict and "vac" in payload_dict:
        v = payload_dict["vac"]
        if v == "other":
            return "Другая должность"
        for real_v in VACANCIES:
            if real_v == v or real_v.startswith(v):
                return real_v
        return str(v)

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
    keyboard: str = "",
) -> bool:
    """Безопасная отправка сообщения в ЛС ВКонтакте с уникальным random_id."""
    url = "https://api.vk.com/method/messages.send"
    params: Dict[str, Any] = {
        "user_id": user_id,
        "message": message,
        "random_id": random.randint(1, 2147483647),
        "v": "5.131",
        "access_token": token,
    }
    if attachment:
        params["attachment"] = attachment
    if keyboard:
        params["keyboard"] = keyboard

    try:
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            if "error" in data:
                logger.error("Ошибка VK API при отправке user_id=%s: %s", user_id, data["error"])
                return False
            return "response" in data
    except Exception as e:
        logger.error("Сетевая ошибка отправки VK: %s", e)
        return False


# ==============================================================================
# ГЛАВНЫЙ ОБРАБОТЧИК СООБЩЕНИЙ ВКОНТАКТЕ
# ==============================================================================

async def handle_vk_message(
    user_id: str,
    text: str,
    attachments: List[Dict[str, Any]],
    db: ResumeDB,
    tg_bot: Bot,
    session: ClientSession,
    token: str,
    payload: str = "",
) -> None:
    """Интеллектуальная обработка диалогов ВКонтакте с соискателями со всеми 16 шагами."""
    try:
        clean = (text or "").strip()
        clean_lower = clean.lower()

        # 1. Проверка блокировки в ЧС
        if db.is_blocked(user_id, super_admin_id=CONFIG.get("SUPER_ADMIN_ID")):
            return

        # 2. Разбор payload кнопки
        payload_dict: Dict[str, Any] = {}
        cmd_from_payload = ""
        if payload:
            try:
                payload_dict = json.loads(payload) if isinstance(payload, str) else payload
                cmd_from_payload = payload_dict.get("command", "")
            except Exception:
                pass

        # 3. Режим техобслуживания — блокирует только приём новых анкет
        is_apply_attempt = (
            cmd_from_payload in ["apply", "start_survey"]
            or clean_lower in ["подать анкету", "заполнить анкету", "анкета", "работа", "/apply"]
        )
        if CONFIG.get("MAINTENANCE_MODE") and is_apply_attempt:
            maint_msg = clean_html(texts.MAINTENANCE_ACTIVE) + f"\n\n📞 Отдел кадров: {CONFIG['HR_PHONE']}"
            await send_vk_message(session, token, int(user_id), maint_msg, keyboard=make_vk_main_keyboard())
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
                    clean_html(texts.LIVE_CHAT_ENDED),
                    keyboard=make_vk_main_keyboard(),
                )
                await tg_bot.send_message(
                    chat_id=operator_id,
                    text=f"⏹ Соискатель <b>{html.escape(name)}</b> (VK id{user_id}) завершил диалог.",
                    parse_mode="HTML",
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
                                    cap = (
                                        f"📷 <b>[Фото от соискателя {html.escape(name)}]</b>\n#vk_uid_{user_id}\n{html.escape(clean)}"
                                        if clean
                                        else f"📷 <b>[Фото от соискателя {html.escape(name)}]</b>\n#vk_uid_{user_id}"
                                    )
                                    await tg_bot.send_photo(
                                        chat_id=operator_id,
                                        photo=p_file,
                                        caption=cap,
                                        reply_markup=builder.as_markup(),
                                        parse_mode="HTML",
                                    )
                        except Exception as e:
                            logger.error("Не удалось переслать фото из VK в TG: %s", e)

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
                                    cap = (
                                        f"📄 <b>[Документ от соискателя {html.escape(name)}]:</b> <code>{html.escape(doc_title)}</code>\n"
                                        f"#vk_uid_{user_id}\n{html.escape(clean)}"
                                    )
                                    await tg_bot.send_document(
                                        chat_id=operator_id,
                                        document=d_file,
                                        caption=cap,
                                        reply_markup=builder.as_markup(),
                                        parse_mode="HTML",
                                    )
                        except Exception as e:
                            logger.error("Не удалось переслать документ из VK в TG: %s", e)

            if clean and not attachments:
                relayed = texts.format_live_dialog_candidate_msg(name, clean)
                delivered = await safe_send(tg_bot, operator_id, relayed, reply_markup=builder.as_markup())
                hr_group = CONFIG.get("HR_GROUP_ID")
                if hr_group and str(operator_id) != str(hr_group):
                    await safe_send(tg_bot, int(hr_group), relayed)

                if delivered:
                    await send_vk_message(session, token, int(user_id), "✅ Сообщение передано в отдел кадров.")
            return

        key: Tuple[str, str] = ("vk", user_id)
        session_data = EXTERNAL_SESSIONS.get(key)

        # 5. Глобальная отмена сценария
        is_cancel = (
            cmd_from_payload == "cancel"
            or clean_lower in ["/cancel", "отмена", "отменить", "стоп", "/stop", "❌ отмена", "❌ отменить анкету"]
        )
        if is_cancel:
            if key in EXTERNAL_SESSIONS:
                del EXTERNAL_SESSIONS[key]
            return await send_vk_message(
                session, token, int(user_id),
                clean_html(texts.NAV_CANCEL_TEXT),
                keyboard=make_vk_main_keyboard(),
            )

        # ----------------------------------------------------------------------
        # ПОШАГОВОЕ ЗАПОЛНЕНИЕ АНКЕТЫ И ВОПРОСА (ПРИОРИТЕТ АКТИВНОЙ СЕССИИ FSM)
        # ----------------------------------------------------------------------
        if session_data:
            step = session_data.get("step")

            # Сценарий: Вопрос соискателя кадровику (/ask)
            if step == "waiting_question":
                is_valid, clean_q, err = candidate_service.validate_question(clean)
                if not is_valid:
                    return await send_vk_message(
                        session, token, int(user_id),
                        f"⚠️ {err or 'Пожалуйста, напишите ваш вопрос текстом в одном сообщении:'}",
                        keyboard=make_vk_cancel_keyboard(),
                    )

                last_cand = db.get_candidate_by_user_id(str(user_id), platform="vk")
                ticket_id = last_cand[0] if last_cand else None
                full_name = last_cand[3] if last_cand else f"Пользователь VK id{user_id}"
                phone = last_cand[4] if last_cand else "Не указан"
                vacancy = last_cand[5] if last_cand else "Анкета не подана"

                del EXTERNAL_SESSIONS[key]
                consent_ts = datetime.now().strftime("%d.%m.%Y %H:%M")
                ok, status_msg, inquiry_id = candidate_service.submit_inquiry(
                    user_id=str(user_id),
                    question_text=clean_q,
                    platform="vk",
                    ticket_id=ticket_id,
                    full_name=full_name,
                    phone=phone,
                    vacancy=vacancy,
                    consent_timestamp=consent_ts,
                )

                conf_text = clean_html(texts.format_inquiry_sent(inquiry_id or 0))
                await send_vk_message(session, token, int(user_id), conf_text, keyboard=make_vk_main_keyboard())

                card_text = texts.format_inquiry_hr_card(
                    inquiry_id=inquiry_id or 0,
                    platform="vk",
                    full_name=full_name,
                    phone=phone,
                    vacancy=vacancy,
                    question_text=clean_q,
                    consent_timestamp=consent_ts,
                )
                try:
                    await route_new_inquiry_ticket(
                        tg_bot, card_text, reply_markup=make_inquiry_admin_keyboard(inquiry_id or 0)
                    )
                except Exception as e:
                    logger.error("Ошибка отправки вопроса #%s в Telegram: %s", inquiry_id, e)
                return

            # Сценарий: Ответ на сообщение кадровика
            elif step == "waiting_hr_reply":
                cand_t_id = session_data.get("data", {}).get("ticket_id") or 0
                del EXTERNAL_SESSIONS[key]

                cand = (
                    db.get_candidate(cand_t_id)
                    if cand_t_id
                    else db.get_candidate_by_user_id(str(user_id), platform="vk")
                )
                fio = cand[3] if cand else f"id{user_id}"

                reply_card = (
                    f"💬 <b>Ответ соискателя из VK {html.escape(fio)} (Анкета #{cand_t_id}):</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{html.escape(clean)}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━"
                )
                builder = InlineKeyboardBuilder()
                builder.button(text="🟢 Начать прямой диалог", callback_data=f"live_dlg_cand_{cand_t_id}")
                builder.button(text="💬 Написать соискателю", callback_data=f"cand_msg_{cand_t_id}")
                builder.adjust(1)

                hr_group = CONFIG.get("HR_GROUP_ID") or CONFIG.get("SUPER_ADMIN_ID")
                if hr_group:
                    await safe_send(tg_bot, int(hr_group), reply_card, reply_markup=builder.as_markup())

                await send_vk_message(
                    session, token, int(user_id),
                    "✅ Ваш ответ успешно доставлен специалисту отдела кадров!",
                    keyboard=make_vk_main_keyboard(),
                )
                return

            # Шаг 0: Фиксация согласия на обработку ПДн (152-ФЗ)
            elif step == "consent":
                if payload_dict.get("consent") == "yes" or clean_lower in [
                    "согласен", "да", "согласна", "принимаю", "ок", "✅ согласен на обработку пдн"
                ]:
                    session_data["data"]["consent_timestamp"] = datetime.now().strftime("%d.%m.%Y %H:%M")
                    session_data["step"] = "name"
                    prompt = "✅ Согласие на обработку ПДн принято.\n\n" + clean_html(texts.SURVEY_STEP1_NAME)
                    return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_cancel_keyboard())
                else:
                    return await send_vk_message(
                        session, token, int(user_id),
                        clean_html(texts.CONSENT_REFUSED_TEXT),
                        keyboard=make_vk_consent_keyboard(),
                    )

            # Шаг 1: ФИО
            elif step == "name":
                is_valid, clean_name, err = candidate_service.validate_fio(clean)
                if not is_valid:
                    return await send_vk_message(
                        session, token, int(user_id),
                        f"⚠️ {err or clean_html(texts.ERR_INVALID_NAME)}",
                        keyboard=make_vk_cancel_keyboard(),
                    )
                session_data["data"]["full_name"] = clean_name
                session_data["step"] = "birth_date"
                prompt = f"Принято: {clean_name}\n\n" + clean_html(texts.SURVEY_STEP2_BIRTHDATE)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_cancel_keyboard())

            # Шаг 2: Дата рождения (18+)
            elif step == "birth_date":
                is_valid, clean_date, err = candidate_service.validate_birth_date(clean)
                if not is_valid:
                    return await send_vk_message(
                        session, token, int(user_id),
                        f"⚠️ {err or clean_html(texts.ERR_INVALID_DATE_FORMAT)}",
                        keyboard=make_vk_cancel_keyboard(),
                    )
                session_data["data"]["birth_date"] = clean_date
                session_data["step"] = "phone"
                prompt = f"Дата рождения принята: {clean_date}\n\n" + clean_html(texts.SURVEY_STEP3_PHONE)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_cancel_keyboard())

            # Шаг 3: Номер телефона
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
                        clean_html(texts.ERR_INVALID_PHONE),
                        keyboard=make_vk_cancel_keyboard(),
                    )

                session_data["data"]["phone"] = norm_phone
                session_data["step"] = "city"
                prompt = f"Номер телефона принят: {norm_phone}\n\n" + clean_html(texts.SURVEY_STEP4_CITY)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_city_keyboard())

            # Шаг 4: Город проживания
            elif step == "city":
                payload_city = payload_dict.get("city")
                if payload_city == "other" or clean_lower in ["другой", "другой город", "другой населенный пункт"]:
                    session_data["step"] = "city_manual"
                    return await send_vk_message(
                        session, token, int(user_id),
                        clean_html(texts.SURVEY_STEP4_MANUAL_PROMPT),
                        keyboard=make_vk_cancel_keyboard(),
                    )

                chosen_city = payload_city or clean or "Ульяновск"
                session_data["data"]["city"] = chosen_city
                session_data["step"] = "vacancy"
                prompt = f"Город: {chosen_city}\n\n" + clean_html(texts.SURVEY_STEP5_VACANCY)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_vacancies_keyboard())

            elif step == "city_manual":
                session_data["data"]["city"] = clean or "Ульяновск"
                session_data["step"] = "vacancy"
                prompt = f"Город: {session_data['data']['city']}\n\n" + clean_html(texts.SURVEY_STEP5_VACANCY)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_vacancies_keyboard())

            # Шаг 5: Должность
            elif step == "vacancy":
                if payload_dict.get("vac") == "other" or clean_lower in ["другая", "другая должность", "другое"]:
                    session_data["step"] = "vacancy_manual"
                    return await send_vk_message(
                        session, token, int(user_id),
                        clean_html(texts.SURVEY_STEP5_1_CUSTOM),
                        keyboard=make_vk_cancel_keyboard(),
                    )

                chosen_vac = resolve_vacancy(clean, payload_dict)
                session_data["data"]["vacancy"] = chosen_vac
                session_data["step"] = "license"
                prompt = f"Выбранная должность: {chosen_vac}\n\n" + clean_html(texts.SURVEY_STEP6_LICENSE)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_license_keyboard())

            elif step == "vacancy_manual":
                chosen_vac = clean or "Специалист"
                session_data["data"]["vacancy"] = chosen_vac
                session_data["step"] = "license"
                prompt = f"Должность: {chosen_vac}\n\n" + clean_html(texts.SURVEY_STEP6_LICENSE)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_license_keyboard())

            # Шаг 6: Водительское удостоверение
            elif step == "license":
                lic_text = payload_dict.get("lic") or clean or "Нет прав"
                session_data["data"]["driver_license"] = lic_text
                session_data["step"] = "experience"
                prompt = f"Водительские права: {lic_text}\n\n" + clean_html(texts.SURVEY_STEP7_EXPERIENCE)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_experience_keyboard())

            # Шаг 7: Стаж / Опыт работы (+ умный триггер бесплатного обучения со стипендией)
            elif step == "experience":
                if payload_dict.get("exp") == "Без опыта" or clean_lower in ["без опыта", "нет опыта", "нет", "0", "-"]:
                    exp_text = "Без опыта"
                else:
                    exp_text = payload_dict.get("exp") or clean or "Без опыта"

                session_data["data"]["experience"] = exp_text
                session_data["step"] = "education"

                # Безопасный умный триггер бесплатного обучения со стипендией
                training_notice = ""
                vac_lower = (session_data["data"].get("vacancy") or "").lower()
                if "водитель" in vac_lower and exp_text == "Без опыта":
                    training_notice = (
                        "\n\n💡 Обратите внимание: предприятие бесплатно обучает водителей "
                        "трамвая и троллейбуса с выплатой ежемесячной стипендии!\n"
                    )

                prompt = f"Опыт работы: {exp_text}{training_notice}\n\n" + clean_html(texts.SURVEY_STEP8_EDUCATION)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_education_keyboard())

            # Шаг 8: Уровень образования
            elif step == "education":
                edu_text = payload_dict.get("edu") or clean or "Среднее"
                session_data["data"]["edu_level"] = edu_text
                session_data["step"] = "facility"
                prompt = f"Образование: {edu_text}\n\n" + clean_html(texts.SURVEY_STEP8_1_FACILITY)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_skip_keyboard())

            # Шаг 8.1: Учебное заведение
            elif step == "facility":
                is_skip = payload_dict.get("skip") or clean_lower in ["пропустить", "пропустить ➡️", "нет", "-", "skip"]
                fac_text = "" if is_skip else clean
                edu_level = session_data["data"].get("edu_level", "Среднее")
                session_data["data"]["education"] = f"{edu_level}, {fac_text}" if fac_text else edu_level
                session_data["step"] = "relocation"
                prompt = clean_html(texts.SURVEY_STEP9_RELOCATION)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_relocation_keyboard())

            # Шаг 9: Готовность к переезду в г. Ульяновск
            elif step == "relocation":
                rel_text = payload_dict.get("rel") or clean or "Уже проживаю"
                session_data["data"]["relocation"] = rel_text
                session_data["step"] = "dormitory"
                prompt = f"Переезд: {rel_text}\n\n" + clean_html(texts.SURVEY_STEP10_DORMITORY)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_dormitory_keyboard())

            # Шаг 10: Общежитие предприятия
            elif step == "dormitory":
                dorm_text = payload_dict.get("dorm") or clean or "Нет, есть жилье"
                session_data["data"]["dormitory"] = dorm_text
                session_data["step"] = "shift_work"
                prompt = f"Общежитие: {dorm_text}\n\n" + clean_html(texts.SURVEY_STEP11_SCHEDULE)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_schedule_keyboard())

            # Шаг 11: Сменный график 2/2
            elif step == "shift_work":
                shift_text = payload_dict.get("shift") or clean or "Да, готов"
                session_data["data"]["shift_work"] = shift_text
                session_data["step"] = "health"
                prompt = f"Сменный график: {shift_text}\n\n" + clean_html(texts.SURVEY_STEP12_HEALTH)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_health_keyboard())

            # Шаг 12: Медицинские противопоказания
            elif step == "health":
                med_text = payload_dict.get("med") or clean or "Противопоказаний нет"
                session_data["data"]["medical_restrictions"] = med_text
                session_data["step"] = "criminal"
                prompt = f"Мед. ограничения: {med_text}\n\n" + clean_html(texts.SURVEY_STEP13_CRIMINAL)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_criminal_keyboard())

            # Шаг 13: Судимость по ст. 86 УК РФ
            elif step == "criminal":
                crim_text = payload_dict.get("crim") or clean or "Судимости нет"
                session_data["data"]["criminal_record"] = crim_text
                session_data["step"] = "source"
                prompt = f"Судимость: {crim_text}\n\n" + clean_html(texts.SURVEY_STEP14_SOURCE)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_source_keyboard())

            # Шаг 14: Источник информации
            elif step == "source":
                src_text = payload_dict.get("src") or clean or "ВКонтакте"
                session_data["data"]["source"] = src_text
                session_data["step"] = "extra_info"
                prompt = f"Источник: {src_text}\n\n" + clean_html(texts.SURVEY_STEP15_EXTRA)
                return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_skip_keyboard())

            # Шаг 15: Дополнительные сведения о себе
            elif step == "extra_info":
                is_skip = payload_dict.get("skip") or clean_lower in ["пропустить", "пропустить ➡️", "нет", "-", "skip"]
                extra_text = "Нет" if is_skip else clean
                session_data["data"]["extra_info"] = extra_text
                session_data["step"] = "confirm_review"

                # Сводная 16-шаговая проверка анкеты строго из texts.format_survey_step16_review
                summary_text = clean_html(format_survey_step16_review(session_data["data"]))
                return await send_vk_message(session, token, int(user_id), summary_text, keyboard=make_vk_confirm_keyboard())

            # Шаг 16: Финальное подтверждение достоверности и отправка
            elif step == "confirm_review":
                is_confirmed = payload_dict.get("confirm") == "yes" or clean_lower in [
                    "подтвердить", "да", "отправить", "все верно", "готово",
                    "✅ подтвердить и отправить", "подтверждаю",
                ]
                if not is_confirmed:
                    return await send_vk_message(
                        session, token, int(user_id),
                        "Для отправки анкеты нажмите кнопку «✅ Подтвердить и отправить» или «❌ Отменить анкету»:",
                        keyboard=make_vk_confirm_keyboard(),
                    )

                cand_data = session_data["data"]
                cand_data["user_id"] = str(user_id)
                del EXTERNAL_SESSIONS[key]

                # 1. Регистрация через сервисный слой
                ticket_id, meta = candidate_service.register_candidate(cand_data, platform="vk")
                cand_row = db.get_candidate(ticket_id)

                # 2. Формирование карточки для Telegram отдела кадров через SSOT
                admin_card = format_hr_card_full(cand_row if cand_row else cand_data)

                try:
                    await route_new_candidate_ticket(tg_bot, admin_card, reply_markup=make_ticket_keyboard(ticket_id))
                    logger.info("Полная анкета #%s [VK] успешно доставлена в Telegram!", ticket_id)
                except Exception as e:
                    logger.error("Ошибка доставки анкеты #%s в Telegram: %s", ticket_id, e)

                # 3. Пересылка прикрепленных фото/документов кадровикам
                if attachments:
                    safe_fn = html.escape(meta.get("full_name", ""))
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
                                                    caption=f"📎 Документ к анкете #{ticket_id} ({safe_fn})",
                                                )
                                except Exception as e:
                                    logger.error("Ошибка пересылки фото VK к анкете: %s", e)

                # 4. Сообщение соискателю об успешной подаче анкеты из texts.py
                resp_text = clean_html(texts.format_survey_success(
                    ticket_id=ticket_id,
                    vacancy=cand_data.get("vacancy", ""),
                    is_driver_no_exp=bool(meta.get("offer_training", False)),
                ))
                return await send_vk_message(session, token, int(user_id), resp_text, keyboard=make_vk_main_keyboard())

        # ----------------------------------------------------------------------
        # ОБРАБОТКА МЕНЮ И КОМАНД (КОГДА НЕТ АКТИВНОЙ СЕССИИ FSM)
        # ----------------------------------------------------------------------

        # Главное меню / Приветствие
        if cmd_from_payload == "start" or clean_lower in [
            "/start", "start", "старт", "начать", "меню", "/menu",
            "главное меню", "привет", "здравствуйте", "хелп", "/help", "помощь", "⬅️ главное меню"
        ]:
            if key in EXTERNAL_SESSIONS:
                del EXTERNAL_SESSIONS[key]
            welcome = clean_html(texts.START_WELCOME)
            return await send_vk_message(session, token, int(user_id), welcome, keyboard=make_vk_main_keyboard())

        # Моя анкета / Статус
        if cmd_from_payload == "my_app" or clean_lower in [
            "/my", "моя анкета", "📑 моя анкета", "статус", "/status", "мой статус"
        ]:
            cand = db.get_candidate_by_user_id(str(user_id), platform="vk")
            if cand:
                my_text = clean_html(texts.format_my_application_full(cand))
            else:
                my_text = clean_html(texts.APP_NOT_FOUND)
            return await send_vk_message(session, token, int(user_id), my_text, keyboard=make_vk_main_keyboard())

        # Задать вопрос (/ask)
        if cmd_from_payload == "ask" or clean_lower in [
            "/ask", "ask", "вопрос", "задать вопрос", "связаться",
            "кадровик", "связаться с кадровиком", "💬 связаться с кадровиком"
        ]:
            is_prod = (CONFIG.get("ENVIRONMENT") == "PROD")
            cooldown = int(db.get_setting("cooldown_seconds", str(CONFIG.get("COOLDOWN_SECONDS", 1200)))) if is_prod else 0
            can_ask, seconds_left = db.check_inquiry_cooldown(str(user_id), cooldown)
            if not can_ask:
                minutes_left = max(1, (seconds_left + 59) // 60)
                msg_text = clean_html(texts.format_inquiry_cooldown(minutes_left))
                return await send_vk_message(session, token, int(user_id), msg_text, keyboard=make_vk_main_keyboard())

            EXTERNAL_SESSIONS[key] = {"step": "waiting_question", "data": {}}
            ask_prompt = (
                clean_html(texts.CONSENT_INQUIRY_PROMPT) + "\n\n"
                "💬 Пожалуйста, напишите ваш вопрос одним сообщением. "
                "Он будет передан специалисту кадровой службы предприятия.\n\n"
                "Для отмены нажмите «❌ Отмена» ниже:"
            )
            return await send_vk_message(session, token, int(user_id), ask_prompt, keyboard=make_vk_cancel_keyboard())

        # Подать анкету (/apply)
        if cmd_from_payload == "apply" or clean_lower in [
            "/apply", "apply", "подать анкету", "заполнить анкету",
            "📝 заполнить анкету", "анкета", "работа", "вакансии", "трудоустройство"
        ]:
            can_apply, reason, info = candidate_service.check_can_apply(user_id, platform="vk")
            if not can_apply and info:
                ticket_id = info.get("ticket_id", 0)
                if reason == "unprocessed":
                    msg = clean_html(texts.format_already_applied(
                        ticket_id=ticket_id,
                        vacancy=info.get("vacancy", "Не указана"),
                        created_at=info.get("created_at", ""),
                        status=info.get("status", "Новая"),
                    ))
                    return await send_vk_message(session, token, int(user_id), msg, keyboard=make_vk_blocked_apply_keyboard())
                elif reason in ("cooldown", "rejected_cooldown"):
                    msg = clean_html(texts.format_rejection_cooldown(
                        ticket_id=ticket_id,
                        rejected_date=info.get("refuse_date", ""),
                        days_left=info.get("days_left", 0),
                        cooldown_end=info.get("available_date", ""),
                    ))
                    return await send_vk_message(session, token, int(user_id), msg, keyboard=make_vk_blocked_apply_keyboard())

            # Старт воронки: Шаг 0 (Согласие 152-ФЗ)
            EXTERNAL_SESSIONS[key] = {"step": "consent", "data": {}}
            consent_prompt = (
                clean_html(texts.CONSENT_SURVEY_PROMPT) + "\n\n"
                "Для продолжения нажмите «✅ Согласен на обработку ПДн» ниже:"
            )
            return await send_vk_message(session, token, int(user_id), consent_prompt, keyboard=make_vk_consent_keyboard())

        # База знаний (FAQ)
        if cmd_from_payload == "faq" or clean_lower in [
            "/faq", "faq", "вопросы", "частые вопросы", "📚 частые вопросы (faq)", "вопросы и ответы"
        ]:
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

        # Контакты
        if cmd_from_payload == "contacts" or clean_lower in [
            "/contacts", "контакты", "📞 контакты", "телефон", "адрес", "где находитесь"
        ]:
            contacts_text = clean_html(texts.CONTACTS_SCREEN)
            return await send_vk_message(session, token, int(user_id), contacts_text, keyboard=make_vk_main_keyboard())

        # Ответ на сообщение кадровика
        if cmd_from_payload == "reply_hr" or clean_lower in [
            "ответить", "ответить кадровику", "💬 ответить кадровику", "ответ кадровику"
        ]:
            cand_t_id = payload_dict.get("ticket_id")
            if not cand_t_id:
                last_c = db.get_candidate_by_user_id(str(user_id), platform="vk")
                if last_c:
                    cand_t_id = last_c[0]

            EXTERNAL_SESSIONS[key] = {
                "step": "waiting_hr_reply",
                "data": {"ticket_id": cand_t_id},
            }
            prompt = (
                "💬 Ответ специалисту отдела кадров МУП «Ульяновскэлектротранс»\n\n"
                "Напишите ваш ответ в одном сообщении. Вы также можете прикрепить фото документов.\n\n"
                "Для отмены нажмите кнопку «❌ Отмена» ниже:"
            )
            return await send_vk_message(session, token, int(user_id), prompt, keyboard=make_vk_cancel_keyboard())

        # Подсказка по умолчанию (если сообщение не распознано)
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
        logger.exception("Критическая ошибка обработки VK от %s: %s", user_id, e)
        try:
            await send_vk_message(
                session, token, int(user_id),
                "⚠️ Произошла ошибка при обработке запроса. Вы возвращены в главное меню.",
                keyboard=make_vk_main_keyboard(),
            )
        except Exception:
            pass


# ==============================================================================
# ФОНОВЫЙ СЕРВИС LONG POLL ВКОНТАКТЕ
# ==============================================================================

async def run_vk_gateway(bot: Bot, db: ResumeDB) -> None:
    """Фоновый воркер Long Poll ВКонтакте с автопереподключением и Exponential Backoff."""
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
                    logger.error("VK Auth Error: %s", data["error"].get("error_msg"))
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
                        logger.debug("Временная задержка связи VK Long Poll: %s", poll_err)
                        await asyncio.sleep(1)
                        continue

                    await asyncio.sleep(0.05)

                    if "failed" in lp_data:
                        code = lp_data.get("failed")
                        if code == 1:
                            ts = lp_data.get("ts", ts)
                            continue
                        elif code in (2, 3):
                            logger.info("VK Long Poll требует обновления сессии (код %s). Переподключение...", code)
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
                                    payload=payload,
                                )
                            )

        except asyncio.CancelledError:
            logger.info("Фоновый шлюз ВКонтакте остановлен.")
            break
        except Exception as e:
            logger.warning("Сбой цикла VK: %s. Повтор через %s сек...", e, backoff)
            SYSTEM_METRICS["vk_online"] = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)