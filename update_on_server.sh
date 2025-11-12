#!/bin/bash

# Скрипт для быстрого обновления бота на сервере
# Использование: ./update_on_server.sh

echo "════════════════════════════════════════════════════════════════"
echo "🔄 ОБНОВЛЕНИЕ БОТА НА СЕРВЕРЕ"
echo "════════════════════════════════════════════════════════════════"
echo ""

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Шаг 1: Остановка бота...${NC}"
docker compose down

echo ""
echo -e "${YELLOW}Шаг 2: Получение обновлений из Git...${NC}"
git pull origin main

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Ошибка при получении обновлений из Git${NC}"
    echo "Попробуйте выполнить вручную: git pull origin main"
    exit 1
fi

echo ""
echo -e "${YELLOW}Шаг 3: Пересборка Docker образа...${NC}"
docker compose build

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Ошибка при сборке образа${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}Шаг 4: Запуск бота...${NC}"
docker compose up -d

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Ошибка при запуске бота${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}✅ Бот успешно обновлен и запущен!${NC}"
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "📊 ЛОГИ ПОСЛЕДНИХ 30 СТРОК:"
echo "════════════════════════════════════════════════════════════════"
echo ""

sleep 3
docker compose logs --tail=30 bot

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "💡 ПОЛЕЗНЫЕ КОМАНДЫ:"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "  Просмотр логов (реального времени):"
echo "    docker compose logs -f bot"
echo ""
echo "  Просмотр последних 50 строк:"
echo "    docker compose logs --tail=50 bot"
echo ""
echo "  Проверка статуса:"
echo "    docker compose ps"
echo ""
echo "  Перезапуск:"
echo "    docker compose restart bot"
echo ""
echo "════════════════════════════════════════════════════════════════"

