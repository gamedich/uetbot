#!/bin/bash
set -e

echo "=== Развертывание статики и конфигурации Nginx ==="

# 1. Создание каталогов статики
mkdir -p /var/www/uet/admin
cp -f /home/botuser/uetbot2/static/var_www_uet/index.html /var/www/uet/index.html
cp -f /home/botuser/uetbot2/static/var_www_uet/admin/admin.html /var/www/uet/admin/admin.html
chmod -R 755 /var/www/uet

# 2. Установка .htpasswd (логин uetadmin, пароль uet2026)
cp -f /home/botuser/uetbot2/infra/nginx/.htpasswd /etc/nginx/.htpasswd
chmod 644 /etc/nginx/.htpasswd

# 3. Установка виртуального хоста
cp -f /home/botuser/uetbot2/infra/nginx/uet /etc/nginx/sites-available/uet
ln -sf /etc/nginx/sites-available/uet /etc/nginx/sites-enabled/uet

# 4. Проверка синтаксиса Nginx и перезагрузка
nginx -t
systemctl reload nginx
echo "=== Nginx успешно проверен и перезагружен! ==="
