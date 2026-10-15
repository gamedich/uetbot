"""
Модуль HRAssistant для интеллектуальной поддержки кадровой службы
МУП «Ульяновскэлектротранс» и серверной логики Telegram Mini App.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


class HRAssistant:
    """Ассистент кадровика для шаблонов сообщений, HTML-валидации и предпросмотра."""

    ALLOWED_TAGS = {
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ins",
        "s",
        "strike",
        "del",
        "span",
        "tg-spoiler",
        "a",
        "code",
        "pre",
        "blockquote",
    }

    TEMPLATES: Dict[str, Dict[str, str]] = {
        "invite_interview": {
            "id": "invite_interview",
            "title": "Приглашение на собеседование",
            "text": (
                "Здравствуйте, {fio}!\n\n"
                "МУП «Ульяновскэлектротранс» рассмотрело вашу анкету на вакансию «{vacancy}» "
                "и приглашает вас на собеседование в отдел кадров.\n"
                "Адрес: г. Ульяновск, ул. Гончарова, 2 (пн-пт 08:00–17:00).\n"
                "Телефон: +7 (8422) 58-46-60."
            ),
        },
        "request_documents": {
            "id": "request_documents",
            "title": "Запрос документов",
            "text": (
                "Здравствуйте, {fio}!\n\n"
                "Для дальнейшего рассмотрения вашей кандидатуры на вакансию «{vacancy}» "
                "просим направить копии документов (паспорт, трудовая книжка, водительское удостоверение при наличии)."
            ),
        },
        "reject_candidate": {
            "id": "reject_candidate",
            "title": "Уведомление об отказе",
            "text": (
                "Здравствуйте, {fio}!\n\n"
                "Благодарим за интерес к работе в МУП «Ульяновскэлектротранс». На текущий момент "
                "мы не готовы предложить вам вакансию «{vacancy}». Ваша анкета сохранена в кадровом резерве."
            ),
        },
        "welcome_employee": {
            "id": "welcome_employee",
            "title": "Приветствие нового сотрудника",
            "text": (
                "Поздравляем с трудоустройством в МУП «Ульяновскэлектротранс», {fio}!\n\n"
                "Добро пожаловать в нашу команду. По всем вопросам обращайтесь к вашему наставнику и в отдел кадров."
            ),
        },
    }

    @classmethod
    def validate_telegram_html(cls, html_text: str) -> Tuple[bool, str]:
        """
        Проверяет корректность разметки HTML для Telegram Bot API:
        - допустимость тегов (только разрешенные Telegram теги)
        - парность и правильность вложенности (LIFO стек)
        - отсутствие XSS/script тегов
        """
        if not html_text:
            return True, ""

        tag_pattern = re.compile(r"<(/?)tags?(?:\s+[^>]*)?>".replace("tags?", r"([a-zA-Z0-9_-]+)"))
        stack: List[str] = []

        pos = 0
        while True:
            match = tag_pattern.search(html_text, pos)
            if not match:
                break

            is_closing = match.group(1) == "/"
            tag_name = match.group(2).lower()

            if tag_name not in cls.ALLOWED_TAGS:
                return False, f"Запрещенный тег <{tag_name}>. Telegram поддерживает только базовые теги оформления."

            if not is_closing:
                # Открывающий тег (проверяем на самозакрывающиеся, Telegram их не использует для форматирования)
                stack.append(tag_name)
            else:
                # Закрывающий тег
                if not stack:
                    return False, f"Обнаружен лишний закрывающий тег </{tag_name}>."
                last_tag = stack.pop()
                if last_tag != tag_name:
                    return False, f"Неправильная вложенность: ожидался </{last_tag}>, но получен </{tag_name}>."

            pos = match.end()

        if stack:
            return False, f"Не закрыты теги: {', '.join(f'<{t}>' for t in stack)}."

        return True, ""

    @classmethod
    def list_templates(cls) -> List[Dict[str, str]]:
        """Возвращает список доступных кадровых шаблонов."""
        return list(cls.TEMPLATES.values())

    @classmethod
    def get_template(cls, template_id: str) -> Optional[Dict[str, str]]:
        """Возвращает шаблон по идентификатору."""
        return cls.TEMPLATES.get(template_id)

    @classmethod
    def assist_preview(cls, template_text: str, data: Dict[str, Any]) -> str:
        """Подставляет переменные {fio}, {vacancy} и др. в текст шаблона для предпросмотра."""
        text = template_text
        for k, v in data.items():
            text = text.replace(f"{{{k}}}", str(v))
        return text

    @classmethod
    def check_text_limits(cls, text: str, max_limit: int = 4096) -> Dict[str, Any]:
        """Проверка лимита длины текста сообщения Telegram (стандартно 4096 символов)."""
        length = len(text or "")
        is_valid = length <= max_limit
        warning = None if is_valid else f"Длина сообщения ({length}) превышает лимит Telegram ({max_limit} символов)."
        return {
            "is_valid": is_valid,
            "length": length,
            "max_limit": max_limit,
            "warning": warning,
        }
