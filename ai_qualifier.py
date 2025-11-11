"""
Модуль для AI квалификации вакансий через Ollama
"""
import requests
import json
import logging
import re
from typing import Dict, Optional
from config import config

logger = logging.getLogger(__name__)


class AIQualifier:
    """Класс для квалификации вакансий с помощью Ollama AI"""
    
    def __init__(self, ollama_url: str = None, model: str = None):
        self.ollama_url = ollama_url or config.OLLAMA_URL
        self.model = model or config.OLLAMA_MODEL
        self.api_endpoint = f"{self.ollama_url}/api/generate"
    
    def _extract_json_from_response(self, text: str) -> Optional[Dict]:
        """Извлекает JSON из ответа AI, даже если он окружен текстом"""
        try:
            # Пробуем распарсить весь текст как JSON
            return json.loads(text)
        except json.JSONDecodeError:
            # Ищем JSON в тексте
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
        
        return None
    
    def qualify_message(self, message_text: str, timeout: int = 30) -> Dict:
        """
        Квалифицирует сообщение как потенциальную вакансию
        
        Args:
            message_text: Текст сообщения для анализа
            timeout: Таймаут запроса в секундах
        
        Returns:
            Словарь с результатами квалификации:
            {
                'is_job': bool,
                'is_relevant': bool,
                'position': str or None,
                'skills': list of str,
                'reason': str
            }
        """
        # Ограничиваем длину сообщения для AI
        max_length = 2000
        if len(message_text) > max_length:
            message_text = message_text[:max_length] + "..."
        
        prompt = config.AI_QUALIFICATION_PROMPT.format(message_text=message_text)
        
        try:
            logger.info(f"Отправка запроса к Ollama ({self.model})...")
            
            response = requests.post(
                self.api_endpoint,
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json"
                },
                timeout=timeout
            )
            
            if response.status_code != 200:
                logger.error(f"Ошибка Ollama API: {response.status_code} - {response.text}")
                return self._default_response("Ошибка API")
            
            result = response.json()
            ai_response = result.get('response', '')
            
            # Парсим JSON ответ от AI
            parsed = self._extract_json_from_response(ai_response)
            
            if parsed:
                # Валидация и нормализация ответа
                qualification = {
                    'is_job': bool(parsed.get('is_job', False)),
                    'is_relevant': bool(parsed.get('is_relevant', False)),
                    'position': parsed.get('position'),
                    'skills': parsed.get('skills', []) if isinstance(parsed.get('skills'), list) else [],
                    'reason': parsed.get('reason', 'Не указано')
                }
                
                logger.info(f"AI квалификация: job={qualification['is_job']}, "
                          f"relevant={qualification['is_relevant']}, "
                          f"position={qualification['position']}")
                
                return qualification
            else:
                logger.warning(f"Не удалось распарсить JSON ответ: {ai_response[:200]}")
                return self._default_response("Не удалось распарсить ответ")
        
        except requests.exceptions.Timeout:
            logger.error(f"Таймаут при обращении к Ollama ({timeout}s)")
            return self._default_response("Таймаут")
        
        except requests.exceptions.ConnectionError:
            logger.error(f"Не удается подключиться к Ollama по адресу {self.ollama_url}")
            return self._default_response("Ошибка подключения к Ollama")
        
        except Exception as e:
            logger.error(f"Неожиданная ошибка при квалификации: {e}", exc_info=True)
            return self._default_response(str(e))
    
    def _default_response(self, reason: str) -> Dict:
        """Возвращает дефолтный ответ при ошибке"""
        return {
            'is_job': False,
            'is_relevant': False,
            'position': None,
            'skills': [],
            'reason': f'Ошибка квалификации: {reason}'
        }
    
    def check_connection(self) -> bool:
        """Проверяет доступность Ollama"""
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json().get('models', [])
                model_names = [m.get('name', '') for m in models]
                
                if self.model in model_names:
                    logger.info(f"Ollama доступна, модель {self.model} найдена")
                    return True
                else:
                    logger.warning(f"Модель {self.model} не найдена. Доступные: {model_names}")
                    logger.warning(f"Выполните: ollama pull {self.model}")
                    return False
            else:
                logger.error(f"Ollama вернула статус {response.status_code}")
                return False
        
        except Exception as e:
            logger.error(f"Ошибка при проверке Ollama: {e}")
            return False


# Глобальный экземпляр квалификатора
ai_qualifier = AIQualifier()

