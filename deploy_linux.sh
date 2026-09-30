#!/bin/bash
set -e

echo "================================================================"
echo "  МУП «УЛЬЯНОВСКЭЛЕКТРОТРАНС» | Автоматическая установка (Linux)"
echo "================================================================"

# 1. Проверка root прав для установки пакетов
if [ "$EUID" -ne 0 ]; then
    SUDO="sudo"
else
    SUDO=""
fi

echo "[1/4] Обновление репозиториев и установка системных пакетов..."
$SUDO apt update -y
$SUDO apt install -y python3 python3-pip python3-venv unzip curl

echo "[2/4] Развертывание виртуального окружения и библиотек..."
python3 deploy.py install

echo "[3/4] Регистрация службы systemd для автозапуска 24/7..."
$SUDO python3 deploy.py systemd

echo "[4/4] Проверка готовности..."
./venv/bin/python deploy.py check

echo "================================================================"
echo "✅ Базовая установка успешно завершена!"
echo ""
echo "ДАЛЬНЕЙШИЕ ШАГИ:"
echo "1. Откройте конфигурационный файл и укажите токены:"
echo "   nano .env"
echo ""
echo "2. Запустите службу бота:"
echo "   systemctl start uet_bot"
echo ""
echo "3. Проверьте статус работы:"
echo "   systemctl status uet_bot"
echo ""
echo "4. Для просмотра логов в реальном времени:"
echo "   journalctl -u uet_bot -f"
echo "================================================================"
