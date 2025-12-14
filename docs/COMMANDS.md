# Полезные команды

## Первый запуск

```bash
# 1. Настройте конфигурацию
cp .env.example .env
nano .env  # Заполните API_ID, API_HASH, PHONE, NOTIFICATION_USER_ID

# 2. Добавьте чаты для мониторинга
nano channels.txt  # Добавьте @username или ID чатов

# 3. Запустите автоматический скрипт
./start.sh
```

## Управление ботом (Docker)

```bash
# Запуск всех сервисов
docker-compose up -d

# Только Ollama
docker-compose up -d ollama

# Только бот
docker-compose up -d bot

# Остановка
docker-compose stop

# Полная остановка (удаление контейнеров)
docker-compose down

# Перезапуск
docker-compose restart bot

# Пересборка после изменения кода
docker-compose build
docker-compose up -d
```

## Логи и мониторинг

```bash
# Просмотр логов бота (в реальном времени)
docker-compose logs -f bot

# Логи Ollama
docker-compose logs -f ollama

# Последние 100 строк логов
docker-compose logs --tail=100 bot

# Статус контейнеров
docker-compose ps
```

## Ollama команды

```bash
# Список загруженных моделей
docker exec telegram_bot_ollama ollama list

# Загрузить модель
docker exec telegram_bot_ollama ollama pull qwen2.5:3b

# Удалить модель
docker exec telegram_bot_ollama ollama rm qwen2.5:3b

# Запустить модель вручную (для теста)
docker exec -it telegram_bot_ollama ollama run qwen2.5:3b
```

## Работа с базой данных

```bash
# Войти в контейнер бота
docker-compose exec bot sh

# Открыть базу данных SQLite
docker-compose exec bot sqlite3 /app/data/jobs.db

# SQL запросы:
# SELECT COUNT(*) FROM processed_jobs;
# SELECT * FROM processed_jobs WHERE is_relevant=1 LIMIT 10;
# SELECT COUNT(*) as cnt, chat_title FROM processed_jobs GROUP BY chat_title;
```

## Отладка

```bash
# Удалить session для переавторизации
rm data/bot_session.session*

# Авторизация заново
docker-compose run --rm bot python main.py

# Запуск бота без Docker (локально)
python main.py

# Проверка синтаксиса Python
python -m py_compile *.py

# Проверка зависимостей
pip install -r requirements.txt
```

## Обновление

```bash
# После изменения кода
docker-compose down
docker-compose build
docker-compose up -d

# Обновление только бота (без пересборки Ollama)
docker-compose build bot
docker-compose up -d bot
```

## Резервное копирование

```bash
# Бэкап базы данных
cp data/jobs.db data/jobs.db.backup

# Бэкап session файлов
cp data/bot_session.session* backup/

# Бэкап всей папки data
tar -czf backup_$(date +%Y%m%d).tar.gz data/
```

## Мониторинг ресурсов

```bash
# Использование ресурсов контейнерами
docker stats

# Размер образов
docker images

# Очистка неиспользуемых образов
docker image prune

# Логи Docker
docker-compose logs --tail=50
```

## Производство (VPS)

```bash
# Автозапуск при перезагрузке сервера
# Добавьте в crontab:
crontab -e
# @reboot cd /path/to/telegram_rassylshik && docker-compose up -d

# Systemd сервис (альтернатива)
# Создайте /etc/systemd/system/telegram-bot.service:
[Unit]
Description=Telegram Job Bot
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/path/to/telegram_rassylshik
ExecStart=/usr/local/bin/docker-compose up -d
ExecStop=/usr/local/bin/docker-compose down

[Install]
WantedBy=multi-user.target

# Затем:
sudo systemctl enable telegram-bot
sudo systemctl start telegram-bot
```

## Быстрые alias (опционально)

Добавьте в `~/.bashrc` или `~/.zshrc`:

```bash
alias tbot-start='cd /path/to/telegram_rassylshik && docker-compose up -d'
alias tbot-stop='cd /path/to/telegram_rassylshik && docker-compose stop'
alias tbot-logs='cd /path/to/telegram_rassylshik && docker-compose logs -f bot'
alias tbot-restart='cd /path/to/telegram_rassylshik && docker-compose restart bot'
alias tbot-status='cd /path/to/telegram_rassylshik && docker-compose ps'
```

Затем используйте просто:
```bash
tbot-start
tbot-logs
tbot-stop
```

