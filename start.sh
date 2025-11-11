#!/bin/bash

# Скрипт для быстрого запуска бота

set -e

echo "=================================="
echo "Telegram Job Monitor Bot - Старт"
echo "=================================="
echo ""

# Проверка .env файла
if [ ! -f .env ]; then
    echo "❌ Файл .env не найден!"
    echo "Создайте его из примера:"
    echo "  cp .env.example .env"
    echo "И заполните необходимые параметры"
    exit 1
fi

# Проверка channels.txt
if [ ! -f channels.txt ]; then
    echo "⚠️  Файл channels.txt не найден!"
    echo "Создайте его и добавьте чаты для мониторинга"
fi

# Проверка Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker не установлен!"
    echo "Установите Docker: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose не установлен!"
    echo "Установите Docker Compose: https://docs.docker.com/compose/install/"
    exit 1
fi

# Создание директории для данных
mkdir -p data

echo "✅ Все проверки пройдены"
echo ""

# Проверка session файла для определения первого запуска
if [ ! -f "data/bot_session.session" ]; then
    echo "🔐 Первый запуск - требуется авторизация в Telegram"
    echo ""
    echo "Запуск бота для авторизации..."
    echo "Вам нужно будет ввести код из SMS"
    echo ""
    docker-compose run --rm bot python main.py
    
    echo ""
    echo "✅ Авторизация завершена!"
    echo ""
fi

# Запуск в фоновом режиме
echo "🚀 Запуск бота в фоновом режиме..."
docker-compose up -d

echo ""
echo "=================================="
echo "✅ Бот успешно запущен!"
echo "=================================="
echo ""
echo "Полезные команды:"
echo "  docker-compose logs -f bot     # Просмотр логов"
echo "  docker-compose stop bot        # Остановить бота"
echo "  docker-compose restart bot     # Перезапустить бота"
echo "  docker-compose down            # Остановить все контейнеры"
echo ""
echo "Для просмотра логов выполните:"
echo "  docker-compose logs -f bot"
echo ""

