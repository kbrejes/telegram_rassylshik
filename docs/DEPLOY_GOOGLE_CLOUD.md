# Деплой на Google Cloud (Free Tier)

## Что получаешь бесплатно (навсегда)
- 1x e2-micro VM (2 vCPU, 1GB RAM)
- 30GB HDD
- Регионы: `us-west1`, `us-central1`, `us-east1`

## Требования
- Google аккаунт
- Карта для верификации (не списывают в рамках free tier)

---

## Шаг 1: Создание проекта

1. Зайди на https://console.cloud.google.com
2. Создай новый проект (или используй существующий)
3. Включи Compute Engine API

---

## Шаг 2: Создание VM

1. **Compute Engine** → **VM instances** → **Create Instance**

2. Настройки:
   ```
   Name: telegram-bot
   Region: us-central1 (или us-west1, us-east1)
   Zone: us-central1-a

   Machine configuration:
   - Series: E2
   - Machine type: e2-micro (2 vCPU, 1 GB memory)

   Boot disk:
   - OS: Debian 12
   - Size: 30 GB (Standard persistent disk)

   Firewall:
   - ✅ Allow HTTP traffic
   - ✅ Allow HTTPS traffic
   ```

3. Нажми **Create**

> Цена покажет ~$7/мес — это без учёта Free Tier. Реально $0.

---

## Шаг 3: Подключение к VM

```bash
# Через браузер: нажми SSH в консоли
# Или через gcloud CLI:
gcloud compute ssh telegram-bot --zone=us-central1-a
```

---

## Шаг 4: Установка Docker

```bash
# Обновление системы
sudo apt update && sudo apt upgrade -y

# Установка зависимостей
sudo apt install -y ca-certificates curl gnupg

# Добавление Docker GPG ключа
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Добавление репозитория
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Установка Docker
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Добавить себя в группу docker
sudo usermod -aG docker $USER
newgrp docker
```

---

## Шаг 5: Настройка Swap (важно для 1GB RAM!)

```bash
# Создаём swap файл 2GB
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Делаем постоянным
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Проверка
free -h
```

---

## Шаг 6: Клонирование и настройка бота

```bash
# Клонируем репозиторий
git clone https://github.com/kbrejes/telegram_rassylshik.git
cd telegram_rassylshik

# Создаём .env файл
cat > .env << 'EOF'
API_ID=твой_api_id
API_HASH=твой_api_hash
DATABASE_PATH=/app/data/jobs.db
PYTHONUNBUFFERED=1
EOF

# Создаём директорию для данных
mkdir -p data
```

---

## Шаг 7: Копирование сессии

**На локальной машине:**
```bash
# Закодировать сессию
base64 -i bot_session.session > session_base64.txt
cat session_base64.txt
```

**На VM:**
```bash
# Вставить base64 и декодировать
echo "ВСТАВЬ_BASE64_СЮДА" | base64 -d > bot_session.session
```

---

## Шаг 8: Сборка и запуск

```bash
# Сборка Docker образа
docker build -t telegram-bot .

# Запуск
docker run -d \
  --name telegram-bot \
  --restart unless-stopped \
  -p 8080:8080 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/bot_session.session:/app/bot_session.session \
  --env-file .env \
  telegram-bot ./start_railway.sh
```

---

## Шаг 9: Настройка файрвола для веб-интерфейса

В Google Cloud Console:
1. **VPC Network** → **Firewall** → **Create Firewall Rule**
2. Настройки:
   ```
   Name: allow-8080
   Direction: Ingress
   Targets: All instances
   Source IP ranges: 0.0.0.0/0
   Protocols and ports: tcp:8080
   ```

---

## Шаг 10: Доступ к веб-интерфейсу

Найди External IP в консоли VM и открой:
```
http://EXTERNAL_IP:8080
```

---

## Полезные команды

```bash
# Логи
docker logs -f telegram-bot

# Перезапуск
docker restart telegram-bot

# Остановка
docker stop telegram-bot

# Обновление
cd telegram_rassylshik
git pull
docker build -t telegram-bot .
docker stop telegram-bot
docker rm telegram-bot
# Запустить снова (команда из Шага 8)
```

---

## Автозапуск при перезагрузке VM

Docker с `--restart unless-stopped` автоматически запустится.

---

## Мониторинг ресурсов

```bash
# RAM и CPU
htop

# Диск
df -h

# Docker
docker stats
```

---

## Важно

- **1GB RAM** — впритык. Swap обязателен!
- **Регион** — только US (us-central1, us-west1, us-east1)
- **Трафик** — 1GB/мес бесплатно исходящий, входящий бесплатно

---

## Источники
- [Google Cloud Free Tier](https://cloud.google.com/free)
- [Compute Engine Getting Started](https://cloud.google.com/free/docs/compute-getting-started)
