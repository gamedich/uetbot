# -*- coding: utf-8 -*-
"""
Главная точка входа бота МУП «Ульяновскэлектротранс».
Инициализирует Dispatcher, подключает маршрутизаторы (роутеры)
и запускает автономные фоновые сервисы (Telegram, VK, MAX).
"""
import asyncio
from datetime import datetime
import logging
import sys

from common import bot, dp, db, setup_bot_commands, SYSTEM_METRICS, CONFIG, safe_send
from handlers import candidate_router, hr_router, tech_router
from gateways.vk_gateway import run_vk_gateway
from gateways.max_gateway import run_max_gateway

logger = logging.getLogger("UET_HR_BOT")

async def notify_super_admin(text: str):
    """Отправляет служебное уведомление ИСКЛЮЧИТЕЛЬНО Главному администратору в ЛС."""
    super_id = CONFIG.get("SUPER_ADMIN_ID")
    if super_id:
        try:
            await safe_send(bot, int(super_id), text)
        except Exception as e:
            logger.warning(f"Не удалось отправить статус в ЛС SuperAdmin: {e}")

async def main():
    logger.info("Запуск многоканального сервиса МУП «Ульяновскэлектротранс»...")

    # 1. Регистрация модулей обработчиков (роутеров) Telegram
    dp.include_router(tech_router)
    dp.include_router(hr_router)
    dp.include_router(candidate_router)

    env_mode = CONFIG.get("ENVIRONMENT", "TEST")

    # 2. Проверка связи с Telegram Bot API
    bot_info_str = "Бот"
    try:
        me = await bot.get_me()
        SYSTEM_METRICS["tg_online"] = True
        bot_info_str = f"@{me.username}"
        logger.info(
            f"Telegram-бот подключен: @{me.username} [ID: {me.id}] "
            f"(Режим: {env_mode})"
        )
    except Exception as e:
        logger.error(f"Не удалось подключиться к Telegram Bot API: {e}")
        SYSTEM_METRICS["tg_online"] = False

    # 3. Регистрация системных команд меню Telegram
    await setup_bot_commands(bot)

    # 4. Фоновые шлюзы внешних мессенджеров (ВКонтакте и МАКС)
    vk_task = asyncio.create_task(run_vk_gateway(bot, db))
    max_task = asyncio.create_task(run_max_gateway(bot, db))

    # 5. Оповещение о старте ИСКЛЮЧИТЕЛЬНО Главному администратору в ЛС
    start_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    start_msg = (
        f"🟢 <b>Сервис МУП «Ульяновскэлектротранс» запущен</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>Бот:</b> {bot_info_str}\n"
        f"⚙️ <b>Режим:</b> <code>{env_mode}</code>\n"
        f"⏱ <b>Время старта:</b> <code>{start_time}</code>\n"
        f"📡 <b>Шлюзы:</b> Telegram (OK) | VK (активен) | MAX (эмуляция)\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Уведомление отправлено только вам в ЛС (SuperAdmin).</i>"
    )
    await notify_super_admin(start_msg)

    # 6. Главный цикл диспетчера с гарантированным завершением фоновых задач
    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        logger.info("Остановка бота: завершение фоновых шлюзов...")
        stop_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        stop_msg = (
            f"🔴 <b>Сервис МУП «Ульяновскэлектротранс» остановлен</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ <b>Время остановки:</b> <code>{stop_time}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Уведомление отправлено только вам в ЛС (SuperAdmin).</i>"
        )
        await notify_super_admin(stop_msg)

        vk_task.cancel()
        max_task.cancel()
        await asyncio.gather(vk_task, max_task, return_exceptions=True)
        await bot.session.close()
        logger.info("Бот успешно остановлен.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Работа бота завершена.")
