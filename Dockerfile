# Используем официальный Python образ
FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Рабочая директория
WORKDIR /app

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY *.py ./
COPY templates.json ./
COPY channels.txt ./

# Копируем веб-интерфейс
COPY web/ ./web/

# Копируем default configs
COPY configs/ ./configs_default/

# Копируем startup скрипты
COPY start_flyio.sh ./
COPY start_railway.sh ./
RUN chmod +x start_flyio.sh start_railway.sh

# Создаем директории для данных (будут заменены симлинками при запуске)
RUN mkdir -p /app/data /app/logs /app/configs /app/sessions

# Переменные окружения по умолчанию
ENV PYTHONUNBUFFERED=1
ENV DATABASE_PATH=/app/data/jobs.db

# Порт для веб-интерфейса
EXPOSE 8080

# Запуск через startup скрипт (для fly.io с persistent volume)
# Для локального Docker можно переопределить: docker run ... python main_multi.py
CMD ["./start_flyio.sh"]
