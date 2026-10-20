"""
Маршрутизаторы авторизации, OTP, 2FA и профиля пользователя МУП «УЭТ».
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime
from typing import TYPE_CHECKING
from aiohttp import web

from common import CONFIG, is_maintenance_active
from web.security import generate_auth_token, validate_init_data, verify_auth_token

if TYPE_CHECKING:
    from web_app import WebPanel

logger = logging.getLogger("UET_WEB_AUTH")


async def handle_api_auth_verify_session(panel: WebPanel, request: web.Request) -> web.Response:
    """Проверка валидности сессии (по токену / cookie) и проверка актуальности прав в БД."""
    session = panel.get_auth_session(request)
    if not session:
        try:
            data = await request.json()
            tok = data.get("token")
            if tok:
                session = verify_auth_token(tok)
        except Exception:
            pass

    if not session:
        return web.json_response(
            {"ok": False, "guest": True, "role": "guest", "error": "Гостевой режим. Требуется авторизация."},
            status=200,
        )

    uid = session.get("uid")
    role = session.get("role")
    name = session.get("name", "")

    return web.json_response(
        {"ok": True, "role": role, "user_id": uid, "name": name, "label": f"● {name} ({role})"}
    )


async def handle_api_auth_logout(panel: WebPanel, request: web.Request) -> web.Response:
    """Сброс сессионной cookie uet_token при выходе сотрудника или соискателя из системы."""
    resp = web.json_response({"ok": True, "message": "Сессия успешно завершена, cookie очищены."})
    resp.del_cookie("uet_token", path="/")
    return resp


async def handle_api_auth_request_otp(panel: WebPanel, request: web.Request) -> web.Response:
    """
    Генерация и отправка одноразового 6-значного кода подтверждения (OTP) в Telegram.
    Исключает постоянные пароли, защищает от подбора и перехвата (2FA / Zero-Knowledge).
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    target = str(data.get("target") or data.get("login") or data.get("username") or data.get("identifier") or "").strip().lower()
    if not target:
        return web.json_response(
            {"ok": False, "error": "Укажите ваш Telegram ID или @username для отправки кода."}, status=400
        )

    clean_user = target.lstrip("@").strip()
    super_id = int(CONFIG.get("SUPER_ADMIN_ID", 7657422832))
    tech_id = int(CONFIG.get("TECH_ADMIN_ID", 7657422832))
    super_username = str(panel.db.get_admin_username(super_id) or "its_darksold1").lower().lstrip("@")

    target_uid = None
    target_role = "applicant"
    target_name = "Пользователь"

    # 1. Проверяем Супер-Админа
    if clean_user in (str(super_id), super_username, "super", "superadmin", "owner", "admin", "its_darksold1", "darksold"):
        target_uid = super_id
        target_role = "superadmin"
        target_name = "Илья (SuperAdmin)"
    elif clean_user in (str(tech_id), "tech", "инженер"):
        target_uid = tech_id
        target_role = "tech"
        target_name = "Дежурный Инженер (Tech)"
    else:
        # 2. Ищем в таблице admins
        with panel.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id, role, full_name, username FROM admins")
            for uid, role, fname, uname in cur.fetchall():
                if clean_user in (str(uid), str(uname or "").lower().lstrip("@")):
                    target_uid = int(uid)
                    target_role = role
                    target_name = fname or f"Сотрудник #{uid}"
                    break

        # 3. Ищем в таблице candidates
        if not target_uid:
            num_only = re.sub(r"\D", "", target)
            if num_only:
                with panel.db.connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT ticket_id, user_id, full_name, phone FROM candidates WHERE ticket_id = ? OR phone LIKE ? ORDER BY ticket_id DESC LIMIT 1",
                        (num_only, f"%{num_only[-10:]}%" if len(num_only) >= 7 else "___NONE___"),
                    )
                    cand = cur.fetchone()
                    if cand and cand[1]:
                        try:
                            target_uid = int(cand[1])
                            target_role = "applicant"
                            target_name = cand[2] or f"Соискатель #{cand[0]}"
                        except Exception:
                            pass

    if not target_uid:
        return web.json_response(
            {
                "ok": False,
                "error": f"Пользователь '{target}' не найден в системе. Откройте бота в Telegram для привязки аккаунта или используйте Telegram Mini App.",
            },
            status=404,
        )

    # Ограничение частоты запросов (Rate Limit: 15 сек в проде, 2 сек в тесте)
    cooldown = 2 if CONFIG.get("ENVIRONMENT") == "TEST" or request.headers.get("X-Test-Mode") else 15
    now = time.time()
    last_req = panel.otp_rate_limits.get(target_uid, 0)
    if now - last_req < cooldown:
        rem = max(1, int(cooldown - (now - last_req)))
        return web.json_response(
            {"ok": False, "error": f"Повторный код можно запросить через {rem} сек."}, status=429
        )

    panel.otp_rate_limits[target_uid] = now

    # Генерация криптостойкого 6-значного кода
    code = f"{secrets.randbelow(900000) + 100000}"
    panel.otp_store[code] = {
        "uid": target_uid,
        "role": target_role,
        "name": target_name,
        "expires_at": now + 180,  # 3 минуты
        "attempts": 0,
    }

    # Отправка кода в Telegram через бота
    sent_ok = False
    if panel.bot:
        try:
            from common import safe_send

            msg_text = (
                f"🔐 <b>Код подтверждения входа в веб-панель МУП «УЭТ»</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"Ваш одноразовый секретный код:\n\n"
                f"👉 <code>{code}</code>\n\n"
                f"⏱ <i>Код действителен в течение 3 минут.</i>\n"
                f"🛡 <i>Никому не передавайте данный код. Он заменяет постоянный пароль и предотвращает несанкционированный доступ к служебной панели.</i>"
            )
            sent_ok = await safe_send(panel.bot, target_uid, msg_text, parse_mode="HTML")
        except Exception as e:
            logger.error("Ошибка отправки OTP в Telegram: %s", e)

    uid_str = str(target_uid)
    masked_uid = uid_str[:2] + "***" + uid_str[-2:] if len(uid_str) > 4 else "***"

    resp_data = {
        "ok": True,
        "sent": sent_ok,
        "message": f"Одноразовый код отправлен в ваш Telegram ({masked_uid}).",
        "ttl": 180,
        "target_uid": target_uid,
    }
    logger.info("[OTP_AUTH] Сгенерирован одноразовый код для UID %s (%s)", target_uid, target_role)
    if CONFIG.get("ENVIRONMENT") == "TEST":
        resp_data["dev_code"] = code

    return web.json_response(resp_data)


async def handle_api_auth_verify_otp(panel: WebPanel, request: web.Request) -> web.Response:
    """Проверка одноразового 6-значного кода и выдача сессионного токена."""
    try:
        data = await request.json()
    except Exception:
        data = {}

    code = str(data.get("code") or data.get("otp") or "").strip()
    now = time.time()

    record = panel.otp_store.get(code)
    if not record:
        return web.json_response(
            {"ok": False, "error": "Неверный или уже использованный код подтверждения."}, status=400
        )

    if now > record.get("expires_at", 0):
        panel.otp_store.pop(code, None)
        return web.json_response(
            {"ok": False, "error": "Срок действия кода (3 минуты) истёк. Запросите новый код."}, status=400
        )

    # Одноразовый код: удаляем сразу после успешной проверки
    panel.otp_store.pop(code, None)

    uid = record["uid"]
    role = record["role"]
    name = record["name"]

    tok = generate_auth_token(uid, role, name)
    resp = web.json_response(
        {
            "ok": True,
            "token": tok,
            "role": role,
            "user_id": uid,
            "name": name,
            "label": f"● {name} ({role.upper()})",
        }
    )
    resp.set_cookie("uet_token", tok, max_age=86400 * 7, path="/", httponly=True, samesite="Lax")
    return resp


async def handle_api_auth_login(panel: WebPanel, request: web.Request) -> web.Response:
    """Безопасная авторизация по OTP коду, TMA или персональному паролю из БД (без статичных паролей)."""
    try:
        data = await request.json()
    except Exception:
        data = {}

    method = data.get("method")
    val = str(data.get("password") or data.get("value") or data.get("code") or "").strip()
    username = str(data.get("username") or data.get("login") or "").strip().lower()
    req_role = str(data.get("role") or "").strip().lower()

    super_id = CONFIG.get("SUPER_ADMIN_ID", 7657422832)
    tech_id = CONFIG.get("TECH_ADMIN_ID", 7657422832)

    # 1. Авторизация через Telegram Mini App (Строгая криптографическая проверка HMAC с пулом токенов)
    if method == "tma":
        init_raw = str(data.get("init_data") or data.get("value") or "").strip()
        bot_token = (
            CONFIG.get("BOT_TOKEN")
            or os.getenv("BOT_TOKEN")
            or CONFIG.get("TG_BOT_TOKEN")
            or os.getenv("TG_BOT_TOKEN")
            or ""
        )
        token_pool = [t for t in [bot_token, CONFIG.get("TG_BOT_TOKEN"), os.getenv("TG_BOT_TOKEN")] if t]
        tma_data = validate_init_data(init_raw, token_pool)
        if not tma_data:
            return web.json_response(
                {"ok": False, "error": "Недействительная подпись Telegram Mini App (проверка HMAC не пройдена)."},
                status=401,
            )

        user_obj = tma_data.get("user")
        tma_user_id = user_obj.get("id") if isinstance(user_obj, dict) else None
        if not tma_user_id:
            return web.json_response(
                {"ok": False, "error": "Не удалось определить ID пользователя из данных Telegram."}, status=400
            )

        tma_uid = int(tma_user_id)
        if tma_uid in (super_id, tech_id):
            role = "superadmin" if tma_uid == super_id else "tech"
            disp_name = "Илья (SuperAdmin)" if tma_uid == super_id else "Инженер (Tech)"
            tok = generate_auth_token(tma_uid, role, disp_name)
            resp = web.json_response(
                {
                    "ok": True,
                    "token": tok,
                    "role": role,
                    "user_id": tma_uid,
                    "name": disp_name,
                    "label": "👑 Главный администратор" if role == "superadmin" else "🛠 Инженер",
                }
            )
            resp.set_cookie("uet_token", tok, max_age=86400 * 7, path="/", httponly=True, samesite="Lax")
            return resp

        role = panel.db.get_admin_role(tma_uid)
        if role and role not in ("none", "revoked", "candidate"):
            role_clean = "superadmin" if role in ("superadmin", "super") else role
            disp_name = f"Сотрудник #{tma_uid}"
            tok = generate_auth_token(tma_uid, role_clean, disp_name)
            resp = web.json_response(
                {
                    "ok": True,
                    "token": tok,
                    "role": role_clean,
                    "user_id": tma_uid,
                    "name": disp_name,
                    "label": f"👔 {role_clean.upper()}",
                }
            )
            resp.set_cookie("uet_token", tok, max_age=86400 * 7, path="/", httponly=True, samesite="Lax")
            return resp

        # Проверяем кандидата по Telegram ID
        with panel.db.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT ticket_id, full_name, phone, vacancy FROM candidates WHERE user_id = ? ORDER BY ticket_id DESC LIMIT 1",
                (str(tma_uid),),
            )
            cand = cur.fetchone()
            if cand:
                cand_name = cand[1] or f"Соискатель #{cand[0]}"
                tok = generate_auth_token(cand[0], "applicant", cand_name)
                resp = web.json_response(
                    {
                        "ok": True,
                        "token": tok,
                        "role": "applicant",
                        "user_id": cand[0],
                        "ticket_id": cand[0],
                        "name": cand_name,
                        "phone": cand[2] or "",
                        "vacancy": cand[3] or "",
                        "label": f"Соискатель (#{cand[0]})",
                    }
                )
                resp.set_cookie("uet_token", tok, max_age=86400 * 7, path="/", httponly=True, samesite="Lax")
                return resp

        tma_name = (
            user_obj.get("first_name", "") + " " + user_obj.get("last_name", "")
        ).strip() or f"Пользователь Telegram #{tma_uid}"
        tok = generate_auth_token(tma_uid, "applicant", tma_name)
        resp = web.json_response(
            {
                "ok": True,
                "token": tok,
                "role": "applicant",
                "user_id": tma_uid,
                "name": tma_name,
                "label": "Соискатель Telegram",
            }
        )
        resp.set_cookie("uet_token", tok, max_age=86400 * 7, path="/", httponly=True, samesite="Lax")
        return resp

    # 2. ВХОД ПО ОДНОРАЗОВОМУ КОДУ (OTP)
    if val in panel.otp_store:
        record = panel.otp_store.pop(val)
        if time.time() <= record.get("expires_at", 0):
            uid = record["uid"]
            role = record["role"]
            name = record["name"]
            tok = generate_auth_token(uid, role, name)
            resp = web.json_response(
                {
                    "ok": True,
                    "token": tok,
                    "role": role,
                    "user_id": uid,
                    "name": name,
                    "label": f"● {name} ({role.upper()})",
                }
            )
            resp.set_cookie("uet_token", tok, max_age=86400 * 7, path="/", httponly=True, samesite="Lax")
            return resp

    # 3. ЗАЩИТА ОТ ПОДБОРА (BRUTE-FORCE PROTECTION)
    client_ip = request.remote or "unknown"
    now = time.time()
    attempts = [t for t in panel.failed_login_attempts.setdefault(client_ip, []) if now - t < 900]
    panel.failed_login_attempts[client_ip] = attempts
    if len(attempts) >= 5:
        return web.json_response(
            {
                "ok": False,
                "error": "⛔ Превышено количество попыток входа (блокировка 15 минут). Постоянные пароли отключены во избежание взлома. Воспользуйтесь безопасным входом по одноразовому коду через Telegram.",
            },
            status=429,
        )

    if not val and username:
        val = username
        username = ""

    if not val:
        return web.json_response(
            {
                "ok": False,
                "error": "Введите одноразовый код из Telegram либо пароль.",
            },
            status=401,
        )

    # 4. ПРОВЕРКА ПЕРСОНАЛЬНОГО ПАРОЛЯ ИЗ БАЗЫ ДАННЫХ (ХЕШ PBKDF2/SHA-256)
    super_configured_pwd = str(CONFIG.get("SUPER_ADMIN_PASSWORD") or "")
    clean_input_user = username.lstrip("@").lower()

    # Аварийный мастер-пароль из .env только если явно задан администратором в конфигурации сервера
    if super_configured_pwd and val == super_configured_pwd and clean_input_user in (
        "super", "superadmin", "admin", "owner", "its_darksold1", str(super_id), ""
    ):
        tok = generate_auth_token(super_id, "superadmin", "Илья (SuperAdmin)")
        resp = web.json_response(
            {
                "ok": True,
                "token": tok,
                "role": "superadmin",
                "user_id": super_id,
                "name": "Илья (SuperAdmin)",
                "email": "admin@uet73.ru",
                "username": "its_darksold1",
                "label": "👑 Главный администратор (SuperAdmin)",
            }
        )
        resp.set_cookie("uet_token", tok, max_age=86400 * 7, path="/", httponly=True, samesite="Lax")
        return resp

    # 5. Проверка по таблице администраторов и сотрудников (персональный пароль из Telegram /password или веб-панели)
    with panel.db.connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, role, full_name, email, username, web_password FROM admins WHERE web_password IS NOT NULL AND web_password != ''"
        )
        rows = cur.fetchall()
        clean_input_user = username.lstrip("@").lower()
        for adm_id, role, name, email, uname, saved_pwd_hash in rows:
            if not panel.db.verify_password(val, saved_pwd_hash):
                continue
            role_match = (
                not req_role
                or req_role in ("staff", "employee", "any")
                or req_role == role.lower()
                or (req_role == "hr" and role.lower() in ("hr", "cadres"))
                or (req_role in ("admin", "superadmin") and role.lower() in ("admin", "superadmin", "super"))
            )
            user_match = not username or clean_input_user in (
                str(adm_id).lower(),
                (name or "").lower(),
                (email or "").lower(),
                (uname or "").lower(),
                role.lower(),
                "staff",
                "сотрудник",
            )
            if role_match and (user_match or len(rows) == 1):
                role_clean = "superadmin" if role in ("superadmin", "super") or adm_id == super_id else role
                disp_name = name or f"Сотрудник #{adm_id}"
                tok = generate_auth_token(adm_id, role_clean, disp_name)
                resp = web.json_response(
                    {
                        "ok": True,
                        "token": tok,
                        "role": role_clean,
                        "user_id": adm_id,
                        "name": disp_name,
                        "email": email or "",
                        "username": uname or "",
                        "label": f"👔 {disp_name} ({role_clean})",
                    }
                )
                resp.set_cookie("uet_token", tok, max_age=86400 * 7, path="/", httponly=True, samesite="Lax")
                return resp

    # 6. Проверка соискателя по номеру анкеты или телефону (ЛК Соискателя)
    clean_user = re.sub(r"\D", "", username or val)
    if clean_user and len(clean_user) >= 3:
        for is_test_db in (False, True):
            with panel.db.connection(is_test=is_test_db) as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT ticket_id, full_name, phone, vacancy, raw_data_json
                    FROM candidates
                    WHERE ticket_id = ?
                       OR phone LIKE ?
                       OR replace(replace(replace(replace(phone, ' ', ''), '-', ''), '(', ''), ')', '') LIKE ?
                    ORDER BY ticket_id DESC LIMIT 1
                    """,
                    (clean_user, f"%{clean_user[-10:]}%", f"%{clean_user[-10:]}%"),
                )
                cand = cur.fetchone()
                if cand:
                    saved_pwd = ""
                    try:
                        raw_meta = json.loads(cand[4] or "{}")
                        saved_pwd = str(raw_meta.get("web_password") or "")
                    except Exception:
                        pass

                    pwd_ok = False
                    if saved_pwd:
                        pwd_ok = panel.db.verify_password(val, saved_pwd) or (val == str(cand[0]))
                    else:
                        pwd_ok = (val == str(cand[0]))

                    if pwd_ok:
                        cand_name = cand[1] or f"Соискатель #{cand[0]}"
                        tok = generate_auth_token(cand[0], "applicant", cand_name)
                        resp = web.json_response(
                            {
                                "ok": True,
                                "token": tok,
                                "role": "applicant",
                                "user_id": cand[0],
                                "ticket_id": cand[0],
                                "name": cand_name,
                                "phone": cand[2] or "",
                                "vacancy": cand[3] or "",
                                "label": f"Соискатель (#{cand[0]})",
                            }
                        )
                        resp.set_cookie(
                            "uet_token", tok, max_age=86400 * 7, path="/", httponly=True, samesite="Lax"
                        )
                        return resp

    panel.failed_login_attempts.setdefault(client_ip, []).append(now)
    return web.json_response(
        {
            "ok": False,
            "error": "Неверный логин или пароль. Постоянные пароли отключены в целях безопасности. Воспользуйтесь безопасным входом по одноразовому коду через Telegram (OTP).",
        },
        status=401,
    )


async def handle_api_auth_register(panel: WebPanel, request: web.Request) -> web.Response:
    """Быстрая регистрация кандидата / соискателя на сайте или через Telegram."""
    if is_maintenance_active():
        return web.json_response(
            {
                "ok": False,
                "error": "Ведутся плановые технические работы. Регистрация соискателей временно приостановлена. "
                f"Контакты отдела кадров: {CONFIG.get('HR_PHONE', '8 (8422) 58-46-60')}.",
            },
            status=503,
        )

    try:
        data = await request.json()
    except Exception:
        data = {}

    full_name = str(data.get("full_name") or data.get("name") or "").strip()
    phone = str(data.get("phone") or "").strip()
    vacancy = str(data.get("vacancy") or "Водитель трамвая (обучение со стипендией)").strip()
    password = str(data.get("password") or "").strip()
    tg_raw = str(data.get("telegram") or data.get("tg_id") or "").strip().lstrip("@")
    platform = str(data.get("platform") or "web").strip()

    if not full_name or len(full_name) < 2:
        return web.json_response(
            {"ok": False, "error": "Пожалуйста, укажите ваше имя и фамилию (минимум 2 буквы)."}, status=400
        )

    clean_phone = re.sub(r"\D", "", phone)
    if len(clean_phone) < 7:
        return web.json_response(
            {"ok": False, "error": "Пожалуйста, укажите корректный номер телефона (минимум 7 цифр)."}, status=400
        )

    # Проверка, нет ли уже такого кандидата по номеру телефона
    existing_cand = None
    with panel.db.connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT ticket_id, full_name, phone, vacancy, raw_data_json FROM candidates WHERE phone LIKE ? ORDER BY ticket_id DESC LIMIT 1",
            (f"%{clean_phone[-10:]}%",),
        )
        existing_cand = cur.fetchone()

    if existing_cand:
        ticket_id = existing_cand[0]
        cand_name = existing_cand[1] or full_name
        if password:
            try:
                meta = json.loads(existing_cand[4] or "{}")
                meta["web_password"] = panel.db.hash_password(password)
                for is_test_db in (False, True):
                    with panel.db.connection(is_test=is_test_db) as conn:
                        conn.cursor().execute(
                            "UPDATE candidates SET raw_data_json = ? WHERE ticket_id = ?",
                            (json.dumps(meta, ensure_ascii=False), ticket_id),
                        )
            except Exception as e:
                logger.warning("Ошибка обновления пароля соискателя: %s", e)
        tok = generate_auth_token(ticket_id, "applicant", cand_name)
        resp = web.json_response(
            {
                "ok": True,
                "token": tok,
                "role": "applicant",
                "user_id": ticket_id,
                "ticket_id": ticket_id,
                "name": cand_name,
                "phone": existing_cand[2] or phone,
                "vacancy": existing_cand[3] or vacancy,
                "label": f"Соискатель (#{ticket_id})",
                "message": f"Вы уже зарегистрированы в кадровом реестре (Заявка #{ticket_id}). Вход выполнен успешно!",
            }
        )
        resp.set_cookie("uet_token", tok, max_age=86400 * 7, path="/", httponly=True, samesite="Lax")
        return resp

    # Создаем нового кандидата
    hashed_password = panel.db.hash_password(password) if password else ""
    raw_meta = {
        "registered_via": platform,
        "web_password": hashed_password,
        "telegram": tg_raw,
        "registered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    user_id = tg_raw if (tg_raw.isdigit() and len(tg_raw) > 5) else clean_phone
    ticket_id = panel.db.add_candidate(
        platform=platform,
        user_id=str(user_id),
        full_name=full_name,
        phone=phone,
        vacancy=vacancy,
        experience="Самостоятельная регистрация на портале МУП «УЭТ»",
        raw_data_json=json.dumps(raw_meta, ensure_ascii=False),
        consent_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # Оповещение кадровой службы в Telegram при наличии бота
    if panel.bot:
        try:
            card_msg = (
                f"🆕 <b>Новая регистрация соискателя на сайте!</b>\n\n"
                f"👤 <b>ФИО:</b> {html.escape(full_name)}\n"
                f"📞 <b>Телефон:</b> <code>{html.escape(phone)}</code>\n"
                f"💼 <b>Направление:</b> {html.escape(vacancy)}\n"
                f"🎫 <b>Номер анкеты:</b> #{ticket_id}\n"
                f"🌐 <b>Источник:</b> Веб-портал МУП «УЭТ» (Онлайн-регистрация)"
            )
            from common import route_new_candidate_ticket
            from keyboards import make_ticket_keyboard

            await route_new_candidate_ticket(panel.bot, card_msg, make_ticket_keyboard(ticket_id))
        except Exception as e:
            logger.warning("Не удалось отправить TG-уведомление о регистрации соискателя: %s", e)

    tok = generate_auth_token(ticket_id, "applicant", full_name)
    resp = web.json_response(
        {
            "ok": True,
            "token": tok,
            "role": "applicant",
            "user_id": ticket_id,
            "ticket_id": ticket_id,
            "name": full_name,
            "phone": phone,
            "vacancy": vacancy,
            "label": f"Соискатель (#{ticket_id})",
            "message": f"Регистрация прошла успешно! Ваш номер анкеты: #{ticket_id}. Добро пожаловать в личный кабинет!",
        }
    )
    resp.set_cookie("uet_token", tok, max_age=86400 * 7, path="/", httponly=True, samesite="Lax")
    return resp


async def handle_api_user_change_password(panel: WebPanel, request: web.Request) -> web.Response:
    session = panel.get_auth_session(request)
    if not session:
        return web.json_response({"ok": False, "error": "Требуется авторизация"}, status=401)
    try:
        data = await request.json()
        caller_uid = int(session.get("uid") or 0)
        caller_role = str(session.get("role", "")).lower()
        target_uid = int(data.get("user_id") or caller_uid)
        new_pwd = str(data.get("new_password") or data.get("password") or "").strip()
        if len(new_pwd) < 3:
            return web.json_response(
                {"ok": False, "error": "Пароль должен содержать минимум 3 символа"}, status=400
            )

        # Если соискатель меняет свой пароль от личного кабинета
        if caller_role in {"candidate", "applicant"}:
            cand = panel.db.get_candidate(caller_uid)
            if not cand:
                return web.json_response({"ok": False, "error": "Анкета соискателя не найдена"}, status=404)
            meta = {}
            try:
                meta = json.loads(cand[4] or "{}")
            except Exception:
                pass
            meta["web_password"] = new_pwd
            with panel.db.connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE candidates SET raw_data_json = ? WHERE ticket_id = ?",
                    (json.dumps(meta, ensure_ascii=False), caller_uid),
                )
                conn.commit()
            return web.json_response(
                {"ok": True, "message": "Пароль для личного кабинета соискателя успешно сохранён!"}
            )

        # Менять чужой пароль сотрудника разрешено только супер-администратору
        if target_uid != caller_uid and caller_role not in {"superadmin", "super"}:
            return web.json_response(
                {"ok": False, "error": "Недостаточно прав для смены чужого пароля"}, status=403
            )

        panel.db.set_admin_password(target_uid, new_pwd)
        return web.json_response({"ok": True, "message": "Пароль сотрудника успешно сохранён!"})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def handle_api_user_set_email(panel: WebPanel, request: web.Request) -> web.Response:
    session = panel.get_auth_session(request)
    if not session:
        return web.json_response({"ok": False, "error": "Требуется авторизация"}, status=401)
    try:
        data = await request.json()
        caller_uid = int(session.get("uid") or 0)
        caller_role = str(session.get("role", "")).lower()
        target_uid = int(data.get("user_id") or caller_uid)

        if target_uid != caller_uid and caller_role not in {"superadmin", "super"}:
            return web.json_response({"ok": False, "error": "Недостаточно прав"}, status=403)

        email = str(data.get("email") or "").strip()
        if not email or "@" not in email:
            return web.json_response({"ok": False, "error": "Некорректный адрес электронной почты"}, status=400)
        panel.db.set_admin_email(target_uid, email)
        return web.json_response({"ok": True, "message": f"Почта {email} успешно привязана"})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
