"""
Конфигурация бота для мониторинга вакансий в Telegram
"""
import os
from dotenv import load_dotenv

# Загружаем переменные окружения из .env
load_dotenv()


class Config:
    """Класс с настройками приложения"""
    
    # Telegram API настройки
    API_ID = int(os.getenv('API_ID', '0'))
    API_HASH = os.getenv('API_HASH', '')
    PHONE = os.getenv('PHONE', '')
    
    # ID пользователя для отправки уведомлений
    NOTIFICATION_USER_ID = int(os.getenv('NOTIFICATION_USER_ID', '0'))
    
    # ID канала для уведомлений (приоритетнее чем USER_ID)
    NOTIFICATION_CHANNEL_ID = int(os.getenv('NOTIFICATION_CHANNEL_ID', '0'))
    
    # Ollama настройки
    OLLAMA_URL = os.getenv('OLLAMA_URL', 'http://localhost:11434')
    OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'qwen2.5:3b')
    
    # Настройки бота
    MAX_MESSAGE_AGE_HOURS = int(os.getenv('MAX_MESSAGE_AGE_HOURS', '24'))
    
    # База данных
    DATABASE_PATH = os.getenv('DATABASE_PATH', 'jobs.db')
    
    # Пути к файлам
    CHANNELS_FILE = 'channels.txt'
    TEMPLATES_FILE = 'templates.json'

    # DEPRECATED: Используйте session_config.get_bot_session_path()
    # Оставлено для обратной совместимости
    SESSION_NAME = 'bot_session'
    
    # AI промпт для квалификации вакансий
    AI_QUALIFICATION_PROMPT = """Проанализируй следующее сообщение из Telegram чата и определи:
1. Является ли это объявлением о работе, вакансией или фриланс-проектом?
2. Если да, то насколько оно релевантно для специалиста по таргетированной рекламе Facebook/Instagram?

Сообщение:
{message_text}

Ответь в формате JSON:
{{
    "is_job": true/false,
    "is_relevant": true/false,
    "position": "название позиции или null",
    "skills": ["навык1", "навык2"],
    "reason": "краткое объяснение решения"
}}
"""
    
    @classmethod
    def validate(cls):
        """Проверяет, что все необходимые настройки заданы"""
        errors = []
        
        if not cls.API_ID or cls.API_ID == 0:
            errors.append("API_ID не задан в .env файле")
        
        if not cls.API_HASH:
            errors.append("API_HASH не задан в .env файле")
        
        if not cls.PHONE:
            errors.append("PHONE не задан в .env файле")
        
        if not cls.NOTIFICATION_USER_ID or cls.NOTIFICATION_USER_ID == 0:
            errors.append("NOTIFICATION_USER_ID не задан в .env файле")
        
        if errors:
            raise ValueError("Ошибки конфигурации:\n" + "\n".join(f"- {e}" for e in errors))
        
        return True


# Глобальный экземпляр конфигурации
config = Config()

