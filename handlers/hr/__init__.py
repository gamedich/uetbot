# -*- coding: utf-8 -*-
"""
Пакет обработчиков кадровой службы МУП «Ульяновскэлектротранс».
Объединяет модули:
- panel: Главное меню /hr, /admin, фильтры и статистика
- tickets: Просмотр анкет, статусы, заметки, удаление ПДн, live-чат, вопросы /ask
- interviews: Умное планирование собеседований (депо, слоты, подтверждение явки)
- access: Управление чатом, ролями, правами, блокировками (/ban, /unban, /sync, /kick)
- export: Экспорт базы соискателей в CSV и генерация Актов об уничтожении ПДн (/export, /act)
"""

from __future__ import annotations

from aiogram import Router

from common import HRAccessMiddleware

from .access import access_router
from .export import export_router
from .interviews import interviews_router
from .panel import panel_router
from .tickets import tickets_router

hr_router = Router(name="hr")

# Подключение централизованного middleware авторизации
hr_router.message.middleware(HRAccessMiddleware())
hr_router.callback_query.middleware(HRAccessMiddleware())

hr_router.include_routers(
    panel_router,
    tickets_router,
    interviews_router,
    access_router,
    export_router,
)

__all__ = ["hr_router"]
