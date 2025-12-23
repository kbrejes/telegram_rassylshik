# Правила деплоя на Google Cloud

## VM информация
- **Имя:** telegram-rassylshik-bot
- **Зона:** us-central1-a
- **IP:** 35.188.128.163
- **Порт:** 8080
- **URL:** http://35.188.128.163:8080/

## Правильный путь
```
/home/brejestovski_kirill/telegram_rassylshik/
```

**НИКОГДА не использовать:**
- `/home/kirill/` — убьет Telegram сессию (AuthKeyDuplicatedError)
- `/app/` — это Docker контейнер со старым кодом

## Команда деплоя
```bash
gcloud compute ssh telegram-rassylshik-bot --zone=us-central1-a --command="cd /home/brejestovski_kirill/telegram_rassylshik && sudo git pull && sudo pkill -f main_multi; sleep 2; sudo nohup python3 main_multi.py > /tmp/bot.log 2>&1 &"
```

## Если SSH таймаутит (exit code 255)
Разбить на короткие команды:
```bash
# 1. Git pull
gcloud compute ssh telegram-rassylshik-bot --zone=us-central1-a --command="cd /home/brejestovski_kirill/telegram_rassylshik && sudo git pull"

# 2. Убить старый процесс
gcloud compute ssh telegram-rassylshik-bot --zone=us-central1-a --command="sudo pkill -9 -f main_multi"

# 3. Запустить новый
gcloud compute ssh telegram-rassylshik-bot --zone=us-central1-a --command="cd /home/brejestovski_kirill/telegram_rassylshik && sudo nohup python3 main_multi.py > /tmp/bot.log 2>&1 &"
```

## Проверка статуса
```bash
# Какой процесс слушает порт
gcloud compute ssh telegram-rassylshik-bot --zone=us-central1-a --command="sudo netstat -tlnp | grep 8080"

# Из какой директории запущен процесс
gcloud compute ssh telegram-rassylshik-bot --zone=us-central1-a --command="sudo ls -la /proc/$(sudo fuser 8080/tcp 2>/dev/null | awk '{print $1}')/cwd"

# Проверка Docker (НЕ должен быть запущен)
gcloud compute ssh telegram-rassylshik-bot --zone=us-central1-a --command="sudo docker ps"
```

---

# Инцидент 2024-12-23: Docker vs Python конфликт

## Симптомы
- Код на диске обновлен (git pull успешен)
- Шаблоны содержат новые изменения
- Но веб-страница отдает старый HTML

## Причина
Docker контейнер `telegram-bot` занимал порт 8080 и отдавал старый код из образа `/app`, игнорируя обновленный код в `/home/brejestovski_kirill/telegram_rassylshik/`.

## Диагностика
```bash
# Показало docker-proxy вместо python
sudo fuser 8080/tcp

# Показало запущенный контейнер
sudo docker ps

# Показало /app вместо правильной директории
sudo ls -la /proc/{pid}/cwd
```

## Решение
```bash
sudo docker stop telegram-bot
sudo docker rm telegram-bot
sudo docker image prune -af
sudo pkill -9 -f main_multi
cd /home/brejestovski_kirill/telegram_rassylshik && sudo nohup python3 main_multi.py > /tmp/bot.log 2>&1 &
```

## Превентивные меры
1. Docker контейнеры и образы удалены с сервера
2. Всегда проверять `docker ps` перед деплоем
3. Проверять из какой директории запущен процесс через `/proc/{pid}/cwd`

---

# Инцидент 2024-12-23: DATABASE_PATH в .env

## Симптомы
```
sqlite3.OperationalError: unable to open database file
```

## Причина
В `.env` файле был путь от Docker: `DATABASE_PATH=/app/data/jobs.db`
Этот путь не существует на хосте.

## Решение
```bash
sudo sed -i 's|DATABASE_PATH=/app/data/jobs.db|DATABASE_PATH=jobs.db|' /home/brejestovski_kirill/telegram_rassylshik/.env
```

---

# Рефакторинг 2024-12-23: Чистка скриптов

## Удалено
- `server.sh` - устаревший Google Cloud скрипт (использовал Docker, противоречил текущему деплою)

## Обновлено
- `scripts/start.sh` - унифицирован синтаксис `docker compose`, путь к сессии, точка входа `main_multi.py`
- `scripts/start_multi.sh` - добавлены проверки .env, Python, создание директорий

## Текущие скрипты:
- `scripts/start.sh` - локальный запуск через Docker
- `scripts/start_multi.sh` - локальный запуск без Docker (прямой Python)
- `scripts/start_railway.sh` - деплой на Railway.app
- `scripts/update_on_server.sh` - обновление на сервере через Docker

---

# Рефакторинг сессий 2024-12-23

## Проблема
Сессии создавались в разных местах с относительными путями:
- `bot_session.session` в CWD
- `sessions/agent_*.session`
- Копирование сессий в web/app.py вызывало AuthKeyDuplicatedError

## Решение
Создан `session_config.py` с абсолютными путями:
- `get_bot_session_path()` → `/абсолютный/путь/sessions/bot_session`
- `get_agent_session_path(name)` → `/абсолютный/путь/sessions/{name}`

Все сессии теперь в одном месте:
```
/home/brejestovski_kirill/telegram_rassylshik/sessions/
├── bot_session.session
└── agent_*.session
```
