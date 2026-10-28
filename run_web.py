"""
Запуск выделенного веб-сервера кадрового портала и личного кабинета МУП «УЭТ».
Работает как отдельный независимый сервис на порту 8081 (ветка beta).
"""

from __future__ import annotations

import asyncio
import logging
import sys

from common import CONFIG, db, bot
from web_app import start_web_panel_task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(name)s] - %(message)s",
)
logger = logging.getLogger("UET_STANDALONE_WEB")


async def main() -> None:
    host = CONFIG.get("WEB_APP_HOST", "127.0.0.1")
    port = int(CONFIG.get("WEB_APP_PORT", 8081))
    logger.info("Запуск независимого веб-сервиса UET на http://%s:%s", host, port)
    await start_web_panel_task(db, bot=bot, host=host, port=port)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Веб-сервис остановлен.")
