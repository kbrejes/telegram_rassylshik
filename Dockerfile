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

# Создаем директорию для данных
RUN mkdir -p /app/data

# Переменные окружения по умолчанию
ENV PYTHONUNBUFFERED=1
ENV DATABASE_PATH=/app/data/jobs.db

# Запуск бота
CMD ["python", "main.py"]

