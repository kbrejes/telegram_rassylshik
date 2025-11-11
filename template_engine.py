"""
Модуль для генерации ответов по шаблонам
"""
import json
import logging
from typing import Dict, Optional, List
from config import config

logger = logging.getLogger(__name__)


class TemplateEngine:
    """Класс для работы с шаблонами ответов"""
    
    def __init__(self, templates_file: str = None):
        self.templates_file = templates_file or config.TEMPLATES_FILE
        self.templates = {}
        self.load_templates()
    
    def load_templates(self):
        """Загружает шаблоны из JSON файла"""
        try:
            with open(self.templates_file, 'r', encoding='utf-8') as f:
                self.templates = json.load(f)
            
            logger.info(f"Загружено {len(self.templates)} шаблонов из {self.templates_file}")
            
            # Логируем список доступных шаблонов
            for template_id, template_data in self.templates.items():
                logger.debug(f"  - {template_id}: {template_data.get('name', 'Без названия')}")
        
        except FileNotFoundError:
            logger.error(f"Файл шаблонов не найден: {self.templates_file}")
            self.templates = {}
        
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON в {self.templates_file}: {e}")
            self.templates = {}
    
    def get_template_list(self) -> List[Dict]:
        """Возвращает список доступных шаблонов"""
        return [
            {
                'id': template_id,
                'name': template_data.get('name', 'Без названия')
            }
            for template_id, template_data in self.templates.items()
        ]
    
    def select_template(self, position: Optional[str], skills: List[str]) -> str:
        """
        Автоматически выбирает подходящий шаблон на основе вакансии
        
        Args:
            position: Название позиции
            skills: Список навыков
        
        Returns:
            ID выбранного шаблона
        """
        if not position:
            return 'default'
        
        position_lower = position.lower()
        skills_lower = [s.lower() for s in skills]
        
        # Правила выбора шаблона
        if 'python' in position_lower or 'python' in skills_lower:
            if 'python_developer' in self.templates:
                return 'python_developer'
        
        if any(word in position_lower for word in ['фриланс', 'freelance', 'проект']):
            if 'freelance' in self.templates:
                return 'freelance'
        
        # По умолчанию
        return 'default'
    
    def generate_response(
        self,
        template_id: str = 'default',
        position: Optional[str] = None,
        skills: Optional[List[str]] = None,
        company: Optional[str] = None,
        custom_data: Optional[Dict] = None
    ) -> str:
        """
        Генерирует ответ на основе шаблона
        
        Args:
            template_id: ID шаблона для использования
            position: Название позиции
            skills: Список навыков
            company: Название компании
            custom_data: Дополнительные данные для подстановки
        
        Returns:
            Сгенерированный текст ответа
        """
        # Проверяем наличие шаблона
        if template_id not in self.templates:
            logger.warning(f"Шаблон '{template_id}' не найден, используется 'default'")
            template_id = 'default'
        
        if template_id not in self.templates:
            logger.error("Даже шаблон 'default' не найден!")
            return "Здравствуйте! Меня заинтересовала ваша вакансия. Готов обсудить детали."
        
        template_data = self.templates[template_id]
        template_text = template_data.get('template', '')
        
        # Подготовка данных для подстановки
        skills_str = ', '.join(skills) if skills else 'указанными технологиями'
        
        substitutions = {
            'skills': skills_str,
            'position': position or 'вашу вакансию',
            'company': company or 'вас',
        }
        
        # Добавляем пользовательские данные
        if custom_data:
            substitutions.update(custom_data)
        
        # Выполняем подстановку
        try:
            response = template_text.format(**substitutions)
        except KeyError as e:
            logger.error(f"Ошибка подстановки в шаблоне: ключ {e} не найден")
            response = template_text
        
        logger.info(f"Сгенерирован ответ по шаблону '{template_id}'")
        return response
    
    def generate_auto_response(
        self,
        position: Optional[str],
        skills: Optional[List[str]],
        company: Optional[str] = None
    ) -> tuple[str, str]:
        """
        Автоматически выбирает шаблон и генерирует ответ
        
        Returns:
            Кортеж (template_id, response_text)
        """
        template_id = self.select_template(position, skills or [])
        response = self.generate_response(
            template_id=template_id,
            position=position,
            skills=skills,
            company=company
        )
        
        return template_id, response


# Глобальный экземпляр template engine
template_engine = TemplateEngine()

