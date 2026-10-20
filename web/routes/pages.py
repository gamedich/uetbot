"""
Маршрутизаторы выдачи веб-страниц и статических интерфейсов (SPA Portal, Docs, Presentation).
"""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
DOCS_DIR = BASE_DIR / "docs"
APPLY_HTML_PATH = TEMPLATES_DIR / "apply.html"
DASHBOARD_HTML_PATH = TEMPLATES_DIR / "dashboard.html"


async def handle_dashboard_page(request: web.Request) -> web.Response:
    """Мастер-дашборд экосистемы МУП «УЭТ» (публичный портал + хабы HR, Tech, SuperAdmin)."""
    target = APPLY_HTML_PATH if APPLY_HTML_PATH.exists() else DASHBOARD_HTML_PATH
    if target.exists():
        content = target.read_text(encoding="utf-8")
    else:
        content = "<h1>Ошибка: Шаблон apply.html не найден</h1>"
    return web.Response(
        text=content,
        content_type="text/html",
        charset="utf-8",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
    )


async def handle_index(request: web.Request) -> web.Response:
    """
    Единая входная точка веб-сервиса с поддержкой адаптивных шаблонов:
    - ?view=tg -> Telegram Mini App
    - ?view=mobile / ?view=apply -> мобильный портал
    - ?view=kadry / ?view=hr -> служебный кабинет кадров
    - по умолчанию -> универсальный мастер-дашборд
    """
    return await handle_dashboard_page(request)


async def handle_tg_page(request: web.Request) -> web.Response:
    """Специализированный нативный шаблон Telegram Mini App (TMA)."""
    return await handle_dashboard_page(request)


async def handle_showcase_page(request: web.Request) -> web.Response:
    """Интерактивная витрина и эмулятор адаптивных шаблонов."""
    return await handle_dashboard_page(request)


async def handle_apply_page(request: web.Request) -> web.Response:
    """Карьерный портал соискателя МУП «УЭТ»: мобильная и десктопная адаптация."""
    return await handle_dashboard_page(request)


async def handle_kadry_page(request: web.Request) -> web.Response:
    """Служебный рабочий кабинет сотрудников и отдела кадров «КАДРЫ»."""
    return await handle_dashboard_page(request)


async def handle_docs_page(request: web.Request) -> web.Response:
    """Официальный сайт документации и презентации UETBot 2.0.0."""
    docs_file = DOCS_DIR / "index.html"
    if docs_file.exists():
        return web.Response(
            text=docs_file.read_text(encoding="utf-8"),
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "public, max-age=300"},
        )
    return web.Response(text="<h1>Документация не найдена</h1>", status=404, content_type="text/html")


async def handle_presentation_page(request: web.Request) -> web.Response:
    """Markdown-документ презентации экосистемы UETBot 2.0.0."""
    pres_file = DOCS_DIR / "presentation.md"
    if pres_file.exists():
        return web.Response(
            text=pres_file.read_text(encoding="utf-8"),
            content_type="text/markdown",
            charset="utf-8",
            headers={"Cache-Control": "public, max-age=300"},
        )
    return web.Response(text="# Презентация не найдена", status=404, content_type="text/markdown")
