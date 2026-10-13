# -*- coding: utf-8 -*-
"""
Сервисный слой организации и сопровождения собеседований
МУП «Ульяновскэлектротранс».

Функционал:
- Справочник филиалов депо (Северное, Засвияжское, Заволжское, Управление);
- Генерация рабочих слотов времени с исключением выходных дней;
- Межплатформенная отправка приглашений кандидату (Telegram / VK / MAX) с интерактивными кнопками явки;
- Обработка подтверждения («Буду точно») и переноса встречи («Прошу перенести»).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import texts
from common import (
    CONFIG,
    db,
    safe_send,
    send_response_to_candidate,
)
from keyboards import make_interview_candidate_response_keyboard

logger = logging.getLogger("INTERVIEW_SERVICE")


class InterviewService:
    """Сервис управления собеседованиями и интерактивным взаимодействием с соискателями."""

    @classmethod
    def get_depot(cls, depot_code: str) -> Dict[str, str]:
        """Возвращает справочную информацию о филиале депо."""
        return texts.DEPOT_BRANCHES.get(depot_code, texts.DEPOT_BRANCHES["depot_admin"])

    @classmethod
    def list_depots(cls) -> Dict[str, Dict[str, str]]:
        """Возвращает все филиалы депо предприятия."""
        return texts.DEPOT_BRANCHES

    @classmethod
    def generate_interview_slots(cls, days_ahead: int = 4) -> List[str]:
        """
        Генерирует ближайшие доступные слоты времени собеседований (пн-пт).
        Включает утренние и дневные интервалы приёма кадровой службы.
        """
        slots: List[str] = []
        now = datetime.now()
        base_hours = ["09:30", "11:00", "14:00", "15:30"]

        days_checked = 0
        current_day = now + timedelta(days=1)  # начиная со следующего дня

        ru_months = {
            1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "мая", 6: "июн",
            7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек"
        }

        while len(slots) < 8 and days_checked < 14:
            # 0=Mon, 4=Fri, 5=Sat, 6=Sun
            if current_day.weekday() < 5:
                day_str = f"{current_day.day} {ru_months.get(current_day.month, '')}"
                for hour in base_hours:
                    slots.append(f"{day_str} в {hour}")
                    if len(slots) >= 8:
                        break
            current_day += timedelta(days=1)
            days_checked += 1

        return slots

    @classmethod
    async def send_interview_invite(
        cls,
        ticket_id: int,
        depot_code: str,
        slot_time: str,
    ) -> Tuple[bool, str]:
        """
        Назначает собеседование, сохраняет данные в БД и направляет приглашение кандидату.
        """
        cand = db.get_candidate(ticket_id)
        if not cand:
            return False, "Анкета соискателя не найдена в базе данных."

        depot_info = cls.get_depot(depot_code)
        depot_name = depot_info["name"]

        # 1. Сохранение в базе данных
        db_ok = db.set_interview_details(ticket_id, depot_name, slot_time)
        if not db_ok:
            logger.warning("Не удалось сохранить детали собеседования для тикета #%d", ticket_id)

        # 2. Формирование текста приглашения
        fio = cand[3] if len(cand) > 3 else "Соискатель"
        platform = cand[1] if len(cand) > 1 else "tg"
        user_id = str(cand[2]) if len(cand) > 2 else ""
        vacancy = cand[5] if len(cand) > 5 else "вакансию"

        invite_text = texts.format_interview_invite(fio, vacancy, depot_info, slot_time)

        # 3. Клавиатура подтверждения явки (для TG)
        keyboard = make_interview_candidate_response_keyboard(ticket_id) if platform == "tg" else None

        # 4. Межплатформенная отправка
        send_ok = await send_response_to_candidate(platform, user_id, invite_text, keyboard=keyboard)
        if send_ok:
            logger.info("Приглашение на собеседование #%d успешно отправлено (%s)", ticket_id, platform)
            return True, f"Приглашение на {slot_time} в «{depot_name}» успешно отправлено кандидату."
        else:
            logger.error("Ошибка доставки приглашения #%d соискателю (%s:%s)", ticket_id, platform, user_id)
            return False, "Не удалось доставить сообщение кандидату (возможно, чат заблокирован со стороны пользователя)."

    @classmethod
    async def handle_candidate_feedback(
        cls,
        bot: Any,
        ticket_id: int,
        is_confirmed: bool,
    ) -> Tuple[bool, str]:
        """
        Обработка кнопки подтверждения («Буду точно») или переноса («Прошу перенести») соискателем.
        Уведомляет кадровую группу МУП «УЭТ».
        """
        cand = db.get_candidate(ticket_id)
        if not cand:
            return False, "Анкета не найдена."

        new_status = "confirmed" if is_confirmed else "reschedule"
        db.update_interview_status(ticket_id, new_status)

        fio = cand[3] if len(cand) > 3 else "Кандидат"
        vacancy = cand[5] if len(cand) > 5 else "Должность"
        depot_name = cand[13] if len(cand) > 13 and cand[13] else "Депо МУП «УЭТ»"
        slot_time = cand[14] if len(cand) > 14 and cand[14] else "Время не зафиксировано"

        depot_dummy = {"name": depot_name, "address": "", "phone": ""}

        if is_confirmed:
            admin_msg = texts.format_interview_confirmed_notification(
                ticket_id, fio, vacancy, depot_dummy, slot_time
            )
            user_msg = (
                "✅ <b>Спасибо за подтверждение!</b>\n\n"
                f"Мы ждем вас <code>{slot_time}</code> в {depot_name}.\n"
                "При себе обязательно имейте паспорт РФ."
            )
        else:
            admin_msg = texts.format_interview_reschedule_notification(
                ticket_id, fio, vacancy, depot_dummy, slot_time
            )
            user_msg = (
                "🔄 <b>Запрос на перенос принят.</b>\n\n"
                "Сотрудник отдела кадров свяжется с вами по указанному телефону для подбора удобного времени."
            )

        # Уведомление в кадровый чат
        hr_group_id = CONFIG.get("HR_GROUP_ID")
        if hr_group_id:
            try:
                await safe_send(bot, int(hr_group_id), admin_msg, parse_mode="HTML")
            except Exception as e:
                logger.error("Ошибка уведомления кадровой группы о явке #%d: %s", ticket_id, e)

        return True, user_msg
