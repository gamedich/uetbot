# -*- coding: utf-8 -*-
"""
Шлюз интеграции с корпоративным мессенджером МАКС (MyTeam / VK Teams).
"""
import asyncio
import logging
from aiohttp import ClientSession, ClientTimeout
from aiogram import Bot

from common import CONFIG, SYSTEM_METRICS
from database import ResumeDB
from gateways.vk_gateway import handle_vk_message

logger = logging.getLogger("MAX_GATEWAY")

async def run_max_gateway(bot: Bot, db: ResumeDB):
    token = CONFIG.get("MAX_BOT_TOKEN", "")
    api_base = CONFIG.get("MAX_API_BASE", "https://api.myteam.mail.ru/bot/v1").rstrip("/")
    if not token:
        logger.info("МАКС (MyTeam): токен не указан в .env. Модуль ожидает настройки.")
        return

    logger.info("Запуск фонового шлюза МАКС / VK Teams...")
    timeout = ClientTimeout(total=45)
    last_event_id = 0

    while True:
        try:
            async with ClientSession(timeout=timeout) as session:
                SYSTEM_METRICS["max_online"] = True
                logger.info("МАКС (MyTeam): подключение к серверу активно.")

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
                        if event.get("type") == "newMessage":
                            payload = event.get("payload", {})
                            chat_id = payload.get("chat", {}).get("chatId")
                            text = payload.get("text", "")
                            if chat_id and text:
                                asyncio.create_task(
                                    handle_vk_message(
                                        user_id=str(chat_id),
                                        text=text,
                                        db=db,
                                        tg_bot=bot,
                                        session=session,
                                        token=token
                                    )
                                )

        except asyncio.CancelledError:
            logger.info("Фоновый шлюз МАКС остановлен.")
            break
        except Exception as e:
            logger.warning(f"Сбой цикла МАКС: {e}")
            SYSTEM_METRICS["max_online"] = False
            await asyncio.sleep(5)
