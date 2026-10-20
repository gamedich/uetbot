import logging
from typing import Optional

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    MenuButtonDefault,
    MenuButtonWebApp,
    WebAppInfo,
)

logger = logging.getLogger("UET_HR_BOT")


async def setup_bot_commands(bot_instance: Bot, config: dict, db) -> None:
    """Установка контекстных выпадающих меню команд Telegram (по ролям и чатам)."""
    # 1. Меню по умолчанию для соискателей в личном диалоге
    candidate_commands = [
        BotCommand(command="start", description="🏠 Главное меню / Перезапуск"),
        BotCommand(command="apply", description="📝 Заполнить анкету на работу"),
        BotCommand(command="my", description="📊 Моя анкета / Статус"),
        BotCommand(command="training", description="🚋 Обучение"),
        BotCommand(command="ask", description="💬 Задать вопрос специалисту кадров"),
        BotCommand(command="contacts", description="📞 Контакты депо и администрации"),
        BotCommand(command="privacy", description="🔒 Защита данных (152-ФЗ)"),
        BotCommand(command="revoke", description="🗑 Отозвать согласие и удалить данные"),
        BotCommand(command="help", description="ℹ️ Справка"),
    ]
    try:
        await bot_instance.set_my_commands(candidate_commands, scope=BotCommandScopeDefault())
    except Exception as e:
        logger.warning("Не удалось установить дефолтные команды меню Telegram: %s", e)

    # 2. Меню для групп и супергрупп (кадровый чат)
    group_commands = [
        BotCommand(command="hr", description="📋 Кадровая панель управления"),
        BotCommand(command="id", description="🆔 Узнать ID чата и свой ID"),
        BotCommand(command="set_group", description="📌 Привязать чат для анкет"),
        BotCommand(command="sync", description="🔄 Синхронизировать кадровиков"),
    ]
    try:
        await bot_instance.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
    except Exception as e:
        logger.warning("Не удалось установить команды для групп: %s", e)

    # 3. Полное меню для SuperAdmin (Владелец)
    super_commands = [
        BotCommand(command="owner", description="👑 Пульт владельца (UET Kit)"),
        BotCommand(command="tests", description="🧪 Тестовая лаборатория и симуляции"),
        BotCommand(command="panel", description="🌐 Веб-панель управления"),
        BotCommand(command="to", description="🛠 Вкл/Выкл режим ТО"),
        BotCommand(command="password", description="🔑 Пароль от веб-панели"),
        BotCommand(command="hr", description="📋 Кадровая панель (соискатели)"),
        BotCommand(command="tech", description="⚙️ Панель инженера (ОЗУ, БД, логи)"),
        BotCommand(command="admins", description="👥 Управление доступом и ролями"),
        BotCommand(command="export", description="📊 Выгрузка анкет в Excel"),
        BotCommand(command="start", description="🏠 Главное меню"),
    ]
    super_id = config.get("SUPER_ADMIN_ID", 0)
    if super_id and super_id > 0:
        try:
            await bot_instance.set_my_commands(super_commands, scope=BotCommandScopeChat(chat_id=super_id))
        except Exception as e:
            logger.debug("Не удалось установить команды для SuperAdmin %s: %s", super_id, e)

    # 4. Меню строго для инженера (Tech)
    tech_commands = [
        BotCommand(command="panel", description="🌐 Веб-панель инженера"),
        BotCommand(command="to", description="🛠 Вкл/Выкл режим ТО"),
        BotCommand(command="password", description="🔑 Пароль от веб-панели"),
        BotCommand(command="tech", description="⚙️ Панель инженера (ОЗУ, БД, логи)"),
        BotCommand(command="start", description="🏠 Главное меню"),
    ]
    tech_id = config.get("TECH_ADMIN_ID", 0)
    all_tech_ids = {tech_id} if tech_id and tech_id != super_id else set()
    try:
        for adm_id, role in db.get_all_admins():
            if role == "tech" and adm_id > 0 and adm_id != super_id:
                all_tech_ids.add(adm_id)
        for t_id in all_tech_ids:
            try:
                await bot_instance.set_my_commands(tech_commands, scope=BotCommandScopeChat(chat_id=t_id))
            except Exception:
                pass
    except Exception as e:
        logger.warning("Ошибка установки команд Tech-пользователей: %s", e)

    # 5. Меню для кадровиков (HR) в личном диалоге
    hr_commands = [
        BotCommand(command="panel", description="🌐 Веб-панель соискателей"),
        BotCommand(command="password", description="🔑 Пароль от веб-панели"),
        BotCommand(command="hr", description="📋 Кадровая панель (соискатели)"),
        BotCommand(command="export", description="📊 Выгрузка анкет в Excel"),
        BotCommand(command="start", description="🏠 Главное меню"),
    ]
    try:
        for adm_id, role in db.get_all_admins():
            if role == "hr" and adm_id > 0 and adm_id != super_id and adm_id not in all_tech_ids:
                try:
                    await bot_instance.set_my_commands(hr_commands, scope=BotCommandScopeChat(chat_id=adm_id))
                except Exception:
                    pass
    except Exception as e:
        logger.warning("Ошибка установки команд HR-пользователей: %s", e)

    # 6. Установка нативной кнопки Telegram Mini App (Menu Button) рядом с полем ввода
    web_url = config.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")
    if web_url and web_url.startswith("https://"):
        if super_id and super_id > 0:
            try:
                await bot_instance.set_chat_menu_button(
                    chat_id=super_id,
                    menu_button=MenuButtonWebApp(text="🌐 Панель", web_app=WebAppInfo(url=web_url)),
                )
            except Exception as e:
                logger.debug("Не удалось установить MenuButtonWebApp для SuperAdmin: %s", e)
        for t_id in all_tech_ids:
            try:
                await bot_instance.set_chat_menu_button(
                    chat_id=t_id,
                    menu_button=MenuButtonWebApp(text="🛠 Панель", web_app=WebAppInfo(url=web_url)),
                )
            except Exception:
                pass
        for adm_id, role in db.get_all_admins():
            if role == "hr" and adm_id > 0 and adm_id != super_id and adm_id not in all_tech_ids:
                try:
                    await bot_instance.set_chat_menu_button(
                        chat_id=adm_id,
                        menu_button=MenuButtonWebApp(text="📋 Панель", web_app=WebAppInfo(url=web_url)),
                    )
                except Exception:
                    pass


async def sync_user_commands(
    bot_instance: Bot,
    config: dict,
    db,
    user_id: Optional[int] = None,
    role: Optional[str] = None,
    **kwargs,
) -> bool:
    """
    Синхронизирует выпадающее меню команд Telegram (/) и нативную кнопку Mini App
    в строгом соответствии с фактической ролью пользователя.
    """
    try:
        web_url = config.get("WEB_APP_URL", "https://213.226.124.135.sslip.io/")

        if user_id:
            super_id = config.get("SUPER_ADMIN_ID", 0)
            tech_id = config.get("TECH_ADMIN_ID", 0)

            actual_role = role
            if actual_role is None:
                if user_id == super_id:
                    actual_role = "superadmin"
                elif user_id == tech_id:
                    actual_role = "tech"
                else:
                    actual_role = db.get_admin_role(user_id) or "none"

            if actual_role in ("none", "revoked", "candidate", ""):
                try:
                    await bot_instance.delete_my_commands(scope=BotCommandScopeChat(chat_id=user_id))
                except Exception as e:
                    logger.debug("delete_my_commands for %s: %s", user_id, e)

                try:
                    await bot_instance.set_chat_menu_button(
                        chat_id=user_id,
                        menu_button=MenuButtonDefault(),
                    )
                except Exception as e:
                    logger.debug("set_chat_menu_button default for %s: %s", user_id, e)
            else:
                target_commands = []
                btn_text = "🌐 Панель"
                if actual_role == "superadmin":
                    target_commands = [
                        BotCommand(command="panel", description="🌐 Веб-панель управления"),
                        BotCommand(command="to", description="🛠 Вкл/Выкл режим ТО"),
                        BotCommand(command="password", description="🔑 Пароль от веб-панели"),
                        BotCommand(command="hr", description="📋 Кадровая панель (соискатели)"),
                        BotCommand(command="tech", description="⚙️ Панель инженера (ОЗУ, БД, логи)"),
                        BotCommand(command="admins", description="👥 Управление доступом"),
                        BotCommand(command="export", description="📊 Выгрузка анкет в Excel"),
                        BotCommand(command="start", description="🏠 Главное меню"),
                    ]
                    btn_text = "🌐 Панель"
                elif actual_role == "tech":
                    target_commands = [
                        BotCommand(command="panel", description="🌐 Веб-панель инженера"),
                        BotCommand(command="to", description="🛠 Вкл/Выкл режим ТО"),
                        BotCommand(command="password", description="🔑 Пароль от веб-панели"),
                        BotCommand(command="tech", description="⚙️ Панель инженера (ОЗУ, БД, логи)"),
                        BotCommand(command="start", description="🏠 Главное меню"),
                    ]
                    btn_text = "🛠 Панель"
                elif actual_role == "hr":
                    target_commands = [
                        BotCommand(command="panel", description="🌐 Веб-панель соискателей"),
                        BotCommand(command="password", description="🔑 Пароль от веб-панели"),
                        BotCommand(command="hr", description="📋 Кадровая панель (соискатели)"),
                        BotCommand(command="export", description="📊 Выгрузка анкет в Excel"),
                        BotCommand(command="start", description="🏠 Главное меню"),
                    ]
                    btn_text = "📋 Панель"
                elif actual_role == "depot":
                    target_commands = [
                        BotCommand(command="start", description="🏠 Главное меню"),
                        BotCommand(command="help", description="ℹ️ Справка депо"),
                    ]
                    btn_text = "🚋 Кабинет"

                if target_commands:
                    try:
                        await bot_instance.set_my_commands(target_commands, scope=BotCommandScopeChat(chat_id=user_id))
                    except Exception as e:
                        logger.warning("set_my_commands for %s: %s", user_id, e)

                if web_url and web_url.startswith("https://"):
                    try:
                        await bot_instance.set_chat_menu_button(
                            chat_id=user_id,
                            menu_button=MenuButtonWebApp(text=btn_text, web_app=WebAppInfo(url=web_url)),
                        )
                    except Exception as e:
                        logger.debug("set_chat_menu_button for %s: %s", user_id, e)

        await setup_bot_commands(bot_instance, config, db)
        return True
    except Exception as e:
        logger.warning("Ошибка sync_user_commands: %s", e)
        return False
