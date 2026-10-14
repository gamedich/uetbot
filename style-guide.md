# Руководство по стилю кода и стандарты разработки UETBot (Style Guide)

Данный документ регламентирует стандарты оформления исходного кода, форматирования, статической типизации и паттернов надежности для проекта **UETBot** (Python 3.12, aiogram 3.x, SQLite WAL).

---

## 1. Обоснование стандарта PEP 8

В проекте UETBot строго соблюдается стандарт **PEP 8 (Style Guide for Python Code)**. Обоснование применения:
1. **Снижение когнитивной нагрузки:** Единообразный стиль именования (`snake_case` для функций/переменных, `PascalCase` для классов, `UPPER_SNAKE_CASE` для констант) позволяет инженерам и диспетчерам DevOps мгновенно ориентироваться в кодовой базе.
2. **Предотвращение скрытых багов:** Статические анализаторы и линтеры выявляют неиспользуемые импорты, циклические зависимости и затенение встроенных переменных (`id`, `format`) на этапе компиляции.
3. **Автоматизация в CI/CD:** Строгие линтеры в пайплайне GitHub Actions блокируют слияние Pull Request, если код нарушает правила форматирования.

---

## 2. Конфигурации инструментов статического анализа

### Конфигурация Ruff (`pyproject.toml` / `ruff.toml`)
```toml
[tool.ruff]
target-version = "py312"
line-length = 120
select = ["E", "F", "W", "C90", "I", "N", "UP", "B", "A", "C4", "PT", "SIM", "RET"]
ignore = ["B008", "UP007", "SIM105"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
```

### Конфигурация Flake8 (`.flake8`)
```ini
[flake8]
max-line-length = 120
extend-ignore = E203, W503, E501
exclude = 
    .git,
    __pycache__,
    venv,
    .venv,
    build,
    dist,
    docs/generated
per-file-ignores =
    __init__.py: F401
```

### Конфигурация Black (`pyproject.toml`)
```toml
[tool.black]
line-length = 120
target-version = ['py312']
include = '\.pyi?$'
exclude = '''
/(
    \.git
  | \.venv
  | venv
  | build
  | dist
)/
'''
```

### Конфигурация Mypy (`mypy.ini`)
```ini
[mypy]
python_version = 3.12
ignore_missing_imports = True
disallow_untyped_defs = False
check_untyped_defs = True
warn_redundant_casts = True
warn_unused_ignores = True
warn_return_any = False
strict_optional = True
```

---

## 3. 5 пар практических примеров «Правильно / Неправильно» из кода бота

### Пример 1: SQL-запросы — Параметризация против f-строк (Предотвращение SQL Injection)

❌ **НЕПРАВИЛЬНО (Уязвимость внедрения SQL-инъекций):**
```python
# ОПАСНО: Конкатенация или f-строки позволяют внедрить произвольный SQL-код
async def get_candidate_by_phone(phone: str):
    cursor = db.cursor()
    query = f"SELECT * FROM candidates WHERE phone = '{phone}' AND is_deleted = 0"
    cursor.execute(query)
    return cursor.fetchone()
```

✅ **ПРАВИЛЬНО (Безопасный параметризованный запрос SQLite):**
```python
async def get_candidate_by_phone(phone: str) -> Optional[dict]:
    query = "SELECT * FROM candidates WHERE phone = ? AND is_deleted = 0"
    async with db.connection() as conn:
        cursor = await conn.execute(query, (phone,))
        row = await cursor.fetchone()
        return dict(row) if row else None
```

---

### Пример 2: Экранирование пользовательских данных (Предотвращение HTML Injection в Telegram)

❌ **НЕПРАВИЛЬНО (Пользовательский текст ломает парсер HTML Telegram):**
```python
# ОПАСНО: Если пользователь укажет имя "<Иван & Ко>", Telegram выдаст ошибку Can't parse entities
await message.answer(
    f"Здравствуйте, <b>{user_input_name}</b>! Ваша заявка принята.",
    parse_mode="HTML"
)
```

✅ **ПРАВИЛЬНО (Обязательная санитизация через `html.escape`):**
```python
import html

safe_name = html.escape(user_input_name.strip())
await message.answer(
    f"Здравствуйте, <b>{safe_name}</b>! Ваша заявка принята.",
    parse_mode="HTML"
)
```

---

### Пример 3: Обработка флуд-контроля Telegram API (`TelegramRetryAfter`)

❌ **НЕПРАВИЛЬНО (Бот падает или зависает при массовой рассылке):**
```python
# ОПАСНО: Нет обработки TelegramRetryAfter, процесс падает при превышении 30 сообщений/сек
async def broadcast_route_alert(user_ids: list[int], text: str):
    for uid in user_ids:
        await bot.send_message(uid, text)
```

✅ **ПРАВИЛЬНО (Экспоненциальная задержка и перехват `TelegramRetryAfter`):**
```python
import asyncio
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError

async def broadcast_route_alert(user_ids: list[int], text: str):
    for uid in user_ids:
        try:
            await bot.send_message(uid, text)
            await asyncio.sleep(0.05)  # Защитный интервал
        except TelegramRetryAfter as exc:
            logger.warning("Flood control hit, sleeping %s seconds", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
            await bot.send_message(uid, text)
        except TelegramAPIError as exc:
            logger.error("Failed to send message to %s: %s", uid, exc)
```

---

### Пример 4: Изоляция файловых дескрипторов SQLite и предотвращение утечек

❌ **НЕПРАВИЛЬНО (Дескриптор файла базы данных остается открытым):**
```python
# ОПАСНО: При ошибке соединение не закрывается, происходит утечка OS дескрипторов и блокировка WAL
def add_feedback(text, user_id):
    conn = sqlite3.connect("resumes.db")
    conn.execute("INSERT INTO feedback (text, user_id) VALUES (?, ?)", (text, user_id))
    conn.commit()
    # Нет conn.close() в блоке finally!
```

✅ **ПРАВИЛЬНО (Контекстный менеджер с гарантированным закрытием):**
```python
def add_feedback(text: str, user_id: int) -> None:
    with self.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO feedback (text, user_id) VALUES (?, ?)", (text, user_id))
            conn.commit()
```

---

### Пример 5: Гарантированный откат транзакций (`rollback()`) при сбоях

❌ **НЕПРАВИЛЬНО (Несогласованное состояние при сбое шага):**
```python
# ОПАСНО: Если второй запрос упадет, первый останется закоммиченным, нарушая целостность
def transfer_ticket(ticket_id: int, target_depot_id: int):
    conn = db.get_raw_conn()
    conn.execute("UPDATE tickets SET depot_id = ? WHERE id = ?", (target_depot_id, ticket_id))
    conn.execute("INSERT INTO ticket_log (ticket_id, action) VALUES (?, 'transfer')", (ticket_id,))
    conn.commit()
```

✅ **ПРАВИЛЬНО (Атомарная транзакция с гарантированным `rollback()` при исключении):**
```python
def transfer_ticket(ticket_id: int, target_depot_id: int) -> bool:
    with self.connection() as conn:
        try:
            conn.execute("BEGIN TRANSACTION")
            conn.execute("UPDATE tickets SET depot_id = ? WHERE id = ?", (target_depot_id, ticket_id))
            conn.execute("INSERT INTO ticket_log (ticket_id, action) VALUES (?, 'transfer')", (ticket_id,))
            conn.commit()
            return True
        except Exception as exc:
            conn.rollback()
            logger.exception("Failed to transfer ticket #%s: %s", ticket_id, exc)
            raise
```
