# Деплой на Railway

## Требования
- Аккаунт на [Railway](https://railway.app)
- Карта для Hobby плана ($5/мес бесплатно)
- Git репозиторий (GitHub)

## Шаг 1: Подготовка переменных окружения

Создай файл `.env` локально (не коммить!) с переменными:

```env
API_ID=твой_api_id
API_HASH=твой_api_hash
BOT_SESSION_BASE64=base64_сессии
```

### Как получить BOT_SESSION_BASE64:

```bash
# Закодировать существующую сессию
base64 -i bot_session.session | tr -d '\n'
```

Скопируй результат — это будет значение `BOT_SESSION_BASE64`.

## Шаг 2: Создание проекта на Railway

1. Зайди на https://railway.app
2. Нажми **New Project**
3. Выбери **Deploy from GitHub repo**
4. Выбери репозиторий с ботом
5. Railway автоматически определит Dockerfile

## Шаг 3: Настройка переменных окружения

В Railway Dashboard → твой проект → **Variables**:

```
API_ID=твой_api_id
API_HASH=твой_api_hash
BOT_SESSION_BASE64=base64_закодированная_сессия
DATABASE_PATH=/app/data/jobs.db
PYTHONUNBUFFERED=1
```

## Шаг 4: Добавление Volume (опционально, но рекомендуется)

Для сохранения данных между деплоями:

1. В Railway Dashboard → твой проект
2. Нажми **+ New** → **Volume**
3. Mount path: `/app/data`
4. Это сохранит БД, сессии и конфиги

## Шаг 5: Настройка домена

1. В Railway Dashboard → твой сервис → **Settings**
2. В разделе **Networking** → **Generate Domain**
3. Получишь URL вида `твой-бот.up.railway.app`

## Шаг 6: Деплой

Railway автоматически деплоит при пуше в main. Или вручную:

1. Нажми **Deploy** в Railway Dashboard

## Проверка

После деплоя открой:
- `https://твой-бот.up.railway.app/` — главная страница
- `https://твой-бот.up.railway.app/api/stats` — статус API
- `https://твой-бот.up.railway.app/auth` — авторизация (если нужна)

## Логи

В Railway Dashboard → твой сервис → **Logs**

## Стоимость

При использовании ≤$5/мес — **бесплатно**.

Примерное потребление бота:
- ~$0.5-2/мес при активном использовании
- Volume: ~$0.1/GB/мес

## Troubleshooting

### Бот не авторизован
1. Локально запусти бота и авторизуйся
2. Закодируй сессию: `base64 -i bot_session.session | tr -d '\n'`
3. Обнови `BOT_SESSION_BASE64` в Railway Variables
4. Redeploy

### База данных теряется
Добавь Volume с mount path `/app/data`

### Ошибка порта
Railway автоматически устанавливает `PORT`. Код уже настроен его читать.

## Полезные команды

```bash
# Локальная проверка Docker
docker build -t job-bot .
docker run -p 8080:8080 --env-file .env job-bot ./start_railway.sh

# Закодировать сессию
base64 -i bot_session.session | tr -d '\n' > session_base64.txt
```
