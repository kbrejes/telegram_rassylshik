#!/bin/bash
# Скрипт запуска Job Notification Bot с веб-интерфейсом

echo "=== Job Notification Bot - Multi-Channel ==="
echo "Запуск системы..."

# Создаем необходимые директории
mkdir -p logs
mkdir -p configs

# Запускаем
python3 main_multi.py

