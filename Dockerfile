FROM python:3.12-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    sqlite3 \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Europe/Samara
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

# Копирование и установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода приложения
COPY . .

# Создание необходимых директорий
RUN mkdir -p /app/data /app/backups /app/logs /app/static /app/templates

EXPOSE 8080

# Запуск основного сервиса бота и веб-панели
CMD ["python", "bot.py"]
