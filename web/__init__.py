"""
Пакет веб-сервера, REST API и Telegram Mini App (TMA) МУП «Ульяновскэлектротранс».
Предоставляет модульную архитектуру для обработки запросов, авторизации и статики.
"""

from __future__ import annotations

from web.middlewares import cors_middleware, security_headers_middleware
from web.security import generate_auth_token, validate_init_data, verify_auth_token

__all__ = [
    "cors_middleware",
    "security_headers_middleware",
    "validate_init_data",
    "generate_auth_token",
    "verify_auth_token",
]
