"""
Middleware модуля web для МУП «Ульяновскэлектротранс».
OWASP Security Headers и CORS с защитой персональных данных (152-ФЗ РФ).
"""

from __future__ import annotations

from aiohttp import web

from common import CONFIG


@web.middleware
async def security_headers_middleware(request: web.Request, handler):
    """OWASP Security Headers: X-Content-Type-Options, Referrer-Policy, CSP frame-ancestors."""
    response = await handler(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: https:; "
        "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org;"
    )
    return response


@web.middleware
async def cors_middleware(request: web.Request, handler):
    """Глобальный CORS middleware с валидацией доверенных источников для защиты ПДн."""
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        try:
            response = await handler(request)
        except web.HTTPException as ex:
            response = ex

    origin = request.headers.get("Origin")
    host = request.headers.get("Host", "")

    web_app_url = str(CONFIG.get("WEB_APP_URL", "")).rstrip("/")
    allowed_domains = (
        "web.telegram.org",
        "webk.telegram.org",
        "webz.telegram.org",
        "sslip.io",
    )

    is_trusted = False
    if not origin:
        is_trusted = True
    elif origin.startswith("http://localhost") or origin.startswith("http://127.0.0.1") or (host and host in origin):
        is_trusted = True
    elif web_app_url and origin.startswith(web_app_url):
        is_trusted = True
    else:
        for dom in allowed_domains:
            if origin.endswith(dom) or f".{dom}" in origin:
                is_trusted = True
                break

    if is_trusted and origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
    elif not origin:
        pass
    else:
        response.headers["Access-Control-Allow-Origin"] = "null"

    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, X-Auth-Token"
    return response
