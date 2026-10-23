"""
Маршрутизаторы выдачи веб-страниц и статических интерфейсов (SPA Portal, Docs, Presentation).
"""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
DOCS_DIR = BASE_DIR / "docs"
UET_STATIC_DIR = BASE_DIR / "static" / "var_www_uet"
INDEX_HTML_PATH = Path("/var/www/uet/index.html")
ADMIN_HTML_PATH = Path("/var/www/uet/admin/admin.html")


async def handle_dashboard_page(request: web.Request) -> web.Response:
    """Мастер-дашборд экосистемы МУП «УЭТ»."""
    target = None
    if INDEX_HTML_PATH.exists():
        target = INDEX_HTML_PATH
    elif (UET_STATIC_DIR / "index.html").exists():
        target = UET_STATIC_DIR / "index.html"
    elif APPLY_HTML_PATH.exists():
        target = APPLY_HTML_PATH
    else:
        target = DASHBOARD_HTML_PATH

    if target and target.exists():
        content = target.read_text(encoding="utf-8")
    else:
        content = "<h1>МУП «Ульяновскэлектротранс»</h1>"
    return web.Response(
        text=content,
        content_type="text/html",
        charset="utf-8",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
    )


async def handle_admin_page(request: web.Request) -> web.Response:
    """Приватный диспетчерский и HR-пульт /admin."""
    target = None
    if ADMIN_HTML_PATH.exists():
        target = ADMIN_HTML_PATH
    elif (UET_STATIC_DIR / "admin" / "admin.html").exists():
        target = UET_STATIC_DIR / "admin" / "admin.html"

    if target and target.exists():
        content = target.read_text(encoding="utf-8")
    else:
        content = "<h1>Диспетчерский пульт не найден</h1>"
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


async def handle_map_public_page(request: web.Request) -> web.Response:
    """Интерактивная карта путей и маршрутов для пассажиров."""
    path = Path("/home/botuser/uetmap/frontend/public.html")
    if path.exists():
        return web.Response(
            text=path.read_text(encoding="utf-8"),
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return web.Response(text="<h1>Карта не найдена</h1>", status=404, content_type="text/html")


async def handle_map_admin_page(request: web.Request) -> web.Response:
    """Панель оцифровки путей и маршрутов /map/admin."""
    path = Path("/home/botuser/uetmap/frontend/admin.html")
    if path.exists():
        return web.Response(
            text=path.read_text(encoding="utf-8"),
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
async def handle_manifest_page(request: web.Request) -> web.Response:
    """PWA Manifest для установки приложения на Android."""
    path = Path("/home/botuser/uetmap/frontend/manifest.json")
    if path.exists():
        return web.Response(
            text=path.read_text(encoding="utf-8"),
            content_type="application/manifest+json",
            charset="utf-8",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    return web.Response(text="{}", status=404, content_type="application/json")


async def handle_app_download(request: web.Request) -> web.Response:
    """Скачивание исходного Android APK проекта (.zip)."""
    path = Path("/home/botuser/uetmap/uetmap_android_project.zip")
    if path.exists():
        return web.Response(
            body=path.read_bytes(),
            content_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="uetmap_android_project.zip"'},
        )
    return web.Response(text="Файл проекта не найден", status=404)


async def handle_map_api_proxy(request: web.Request) -> web.Response:
    """Проксирование API запросов карты к сервису uetmap (127.0.0.1:8085)."""
    import aiohttp
    target_url = f"http://127.0.0.1:8085{request.rel_url}"
    try:
        async with aiohttp.ClientSession() as session:
            body = await request.read() if request.method in ("POST", "PUT", "PATCH") else None
            async with session.request(
                method=request.method,
                url=target_url,
                headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")},
                data=body,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp_data = await resp.read()
                resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ("transfer-encoding", "content-encoding", "content-length", "content-type")}
                return web.Response(
                    body=resp_data,
                    status=resp.status,
                    content_type=resp.content_type,
                    charset=resp.charset,
                    headers=resp_headers,
                )
    except Exception as exc:
        return web.Response(text=f'{{"error": "proxy failed: {exc}"}}', status=502, content_type="application/json")

