#!/bin/bash
# Скрипт локального запуска Job Notification Bot (без Docker)

set -e

echo "=== Job Notification Bot - Multi-Channel ==="
echo ""

# Проверка .env файла
if [ ! -f .env ]; then
    echo "❌ Файл .env не найден!"
    echo "Создайте его из примера: cp .env.example .env"
    exit 1
fi

# Проверка Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 не установлен!"
    exit 1
fi

# Создаем необходимые директории
mkdir -p logs
mkdir -p configs
mkdir -p sessions
mkdir -p data

echo "✅ Все проверки пройдены"
echo "🚀 Запуск системы..."
echo ""

# Запускаем
python3 main_multi.py

