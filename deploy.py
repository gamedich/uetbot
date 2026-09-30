#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единый менеджер развертывания и обслуживания бота МУП «Ульяновскэлектротранс»
Поддерживает Windows (локальный ПК) и Linux (боевой сервер, systemd).
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def color_print(msg: str, color: str = "green"):
    colors = {
        "green": "\033[92m[УСПЕХ]\033[0m",
        "blue": "\033[94m[ИНФО]\033[0m",
        "yellow": "\033[93m[ВНИМАНИЕ]\033[0m",
        "red": "\033[91m[ОШИБКА]\033[0m",
    }
    prefix = colors.get(color, "[*]")
    print(f"{prefix} {msg}")

def check_python_version():
    if sys.version_info < (3, 9):
        color_print("Требуется версия Python 3.9 или выше!", "red")
        sys.exit(1)

def get_venv_python(venv_dir: Path) -> Path:
    if sys.platform.startswith("win"):
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"

def install_environment():
    check_python_version()
    print("=" * 64)
    print("  МУП «УЛЬЯНОВСКЭЛЕКТРОТРАНС» | Установка сервиса бота")
    print("=" * 64)

    # 1. Виртуальное окружение
    venv_dir = BASE_DIR / "venv"
    if not venv_dir.exists():
        color_print("Создание изолированного виртуального окружения (venv)...", "blue")
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        color_print("Виртуальное окружение venv успешно создано.", "green")
    else:
        color_print("Виртуальное окружение venv уже существует.", "blue")

    venv_py = get_venv_python(venv_dir)

    # 2. Установка зависимостей
    req_file = BASE_DIR / "requirements.txt"
    if req_file.exists():
        color_print("Установка зависимостей из requirements.txt...", "blue")
        subprocess.run([str(venv_py), "-m", "pip", "install", "--upgrade", "pip"], check=True)
        subprocess.run([str(venv_py), "-m", "pip", "install", "-r", str(req_file)], check=True)
        color_print("Все зависимости успешно установлены.", "green")

    # 3. Проверка .env файла
    env_file = BASE_DIR / ".env"
    env_example = BASE_DIR / ".env.example"
    if not env_file.exists():
        if env_example.exists():
            shutil.copyfile(env_example, env_file)
            color_print("Создан файл конфигурации .env из шаблона .env.example", "green")
            color_print("ОБЯЗАТЕЛЬНО откройте .env и укажите ваш TG_BOT_TOKEN и SUPER_ADMIN_ID!", "yellow")
        else:
            color_print("Внимание: файл .env отсутствует. Создайте его для работы бота.", "yellow")
    else:
        color_print("Файл конфигурации .env обнаружен.", "green")

    # 4. Проверка и миграция БД
    try:
        sys.path.insert(0, str(BASE_DIR))
        from database import ResumeDB
        db = ResumeDB(str(BASE_DIR / "resumes.db"))
        color_print("Структура базы данных SQLite инициализирована и проверена.", "green")
    except Exception as e:
        color_print(f"Предупреждение при инициализации БД: {e}", "yellow")

    print("=" * 64)
    color_print("Установка полностью завершена!", "green")
    if sys.platform.startswith("win"):
        color_print("Для запуска используйте: run_bot.bat", "green")
    else:
        color_print("Для запуска используйте: ./venv/bin/python bot.py", "green")
        color_print("Для создания службы systemd выполните: python deploy.py systemd", "blue")
    print("=" * 64)

def update_environment():
    print("=" * 64)
    color_print("Проверка и обновление окружения бота...", "blue")
    venv_dir = BASE_DIR / "venv"
    if venv_dir.exists():
        venv_py = get_venv_python(venv_dir)
        req_file = BASE_DIR / "requirements.txt"
        if req_file.exists():
            color_print("Обновление зависимостей...", "blue")
            subprocess.run([str(venv_py), "-m", "pip", "install", "-r", str(req_file)], check=False)

    try:
        sys.path.insert(0, str(BASE_DIR))
        from database import ResumeDB
        db = ResumeDB(str(BASE_DIR / "resumes.db"))
        color_print("База данных актуализирована и оптимизирована (WAL-режим активен).", "green")
    except Exception as e:
        color_print(f"Ошибка проверки БД: {e}", "yellow")

    color_print("Обновление окружения завершено. Исполняемые файлы bot.py и database.py сохранены.", "green")
    print("=" * 64)

def backup_db():
    try:
        sys.path.insert(0, str(BASE_DIR))
        from database import ResumeDB
        db = ResumeDB(str(BASE_DIR / "resumes.db"))
        backup_dir = BASE_DIR / "backups"
        path = db.backup_database(str(backup_dir))
        color_print(f"Резервная копия базы данных успешно создана: {path}", "green")
    except Exception as e:
        color_print(f"Ошибка резервного копирования: {e}", "red")

def setup_systemd():
    if sys.platform.startswith("win"):
        color_print("Службы systemd предназначены только для серверов под управлением Linux!", "yellow")
        return

    service_name = "uet_bot"
    service_file = f"/etc/systemd/system/{service_name}.service"
    venv_py = get_venv_python(BASE_DIR / "venv")
    user = os.getenv("USER", "root")

    service_content = f"""[Unit]
Description=Telegram/VK/MAX Bot for MUP Ulyanovskelektrotrans
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={BASE_DIR}
ExecStart={venv_py} {BASE_DIR / 'bot.py'}
Restart=always
RestartSec=5
EnvironmentFile={BASE_DIR / '.env'}

[Install]
WantedBy=multi-user.target
"""
    print(f"\nСгенерированный Unit-файл ({service_file}):\n")
    print(service_content)

    try:
        if os.geteuid() == 0:
            with open(service_file, "w", encoding="utf-8") as f:
                f.write(service_content)
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "enable", service_name], check=True)
            color_print(f"Служба {service_name} зарегистрирована в автозапуске systemd!", "green")
            color_print(f"Команды управления: systemctl start {service_name} | systemctl status {service_name}", "blue")
        else:
            color_print("Для автоматической регистрации службы запустите команду с sudo:", "yellow")
            color_print(f"sudo python3 deploy.py systemd", "yellow")
    except Exception as e:
        color_print(f"Не удалось записать service-файл: {e}", "red")

def check_health():
    print("=" * 64)
    color_print("ДИАГНОСТИКА ГОТОВНОСТИ СИСТЕМЫ (HEALTH CHECK)", "blue")
    print("=" * 64)

    # 1. Python
    print(f"• Python: {sys.version.split()[0]} (OK)")

    # 2. Venv
    venv_dir = BASE_DIR / "venv"
    print(f"• Virtualenv: {'Создано' if venv_dir.exists() else 'Не создано (запустите deploy.py install)'}")

    # 3. .env
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        print("• Конфигурация (.env): Обнаружена")
        from dotenv import dotenv_values
        vals = dotenv_values(env_file)
        has_token = bool(vals.get("TG_BOT_TOKEN"))
        has_admin = bool(vals.get("SUPER_ADMIN_ID"))
        print(f"  - TG_BOT_TOKEN: {'Задан' if has_token else 'ОТСУТСТВУЕТ (требуется заполнить!)'}")
        print(f"  - SUPER_ADMIN_ID: {'Задан (' + str(vals.get('SUPER_ADMIN_ID')) + ')' if has_admin else 'ОТСУТСТВУЕТ'}")
        print(f"  - Режим: {vals.get('ENVIRONMENT', 'TEST')}")
    else:
        print("• Конфигурация (.env): ОТСУТСТВУЕТ (скопируйте из .env.example)")

    # 4. Database
    db_file = BASE_DIR / "resumes.db"
    print(f"• База данных SQLite: {'Обнаружена' if db_file.exists() else 'Будет создана при первом старте'}")
    print("=" * 64)

def print_help():
    print("Использование: python deploy.py [КОМАНДА]")
    print("\nДоступные команды:")
    print("  install   - Полная установка: создание venv, pip install, генерация .env")
    print("  update    - Обновление зависимостей и валидация миграций БД")
    print("  backup    - Онлайн-резервное копирование SQLite БД без остановки бота")
    print("  check     - Диагностика готовности системы к запуску (health check)")
    print("  systemd   - Генерация службы автозапуска для Linux серверов")
    print("  help      - Показать эту справку")

if __name__ == "__main__":
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "help"

    if action == "install":
        install_environment()
    elif action == "update":
        update_environment()
    elif action == "backup":
        backup_db()
    elif action == "systemd":
        setup_systemd()
    elif action in ("check", "status"):
        check_health()
    else:
        print_help()
