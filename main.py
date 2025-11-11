"""
Точка входа для Telegram Job Monitor Bot
"""
import asyncio
import logging
import sys
from pathlib import Path

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)


async def main():
    """Главная функция запуска бота"""
    logger.info("=" * 60)
    logger.info("Telegram Job Monitor Bot - Запуск")
    logger.info("=" * 60)
    
    try:
        # Импорты модулей (после настройки логирования)
        from config import config
        from database import db
        from bot import bot
        
        # Валидация конфигурации
        logger.info("Проверка конфигурации...")
        try:
            config.validate()
            logger.info("✓ Конфигурация валидна")
        except ValueError as e:
            logger.error(f"✗ Ошибка конфигурации:\n{e}")
            logger.error("\nПожалуйста, создайте файл .env и заполните необходимые параметры.")
            logger.error("Пример: cp .env.example .env")
            return 1
        
        logger.info("ℹ️  Запуск в упрощенном режиме (без AI квалификации)")
        logger.info("   Фильтрация по ключевым словам и маркерам вакансий")
        
        # Подключение к базе данных
        logger.info("Подключение к базе данных...")
        await db.connect()
        logger.info("✓ База данных подключена")
        
        # Вывод статистики
        stats = await db.get_statistics()
        logger.info(f"Статистика базы данных:")
        logger.info(f"  - Всего обработано: {stats['total']}")
        logger.info(f"  - Релевантных: {stats['relevant']}")
        logger.info(f"  - Уникальных чатов: {stats['unique_chats']}")
        
        # Запуск бота
        logger.info("\nЗапуск Telegram бота...")
        await bot.start()
        
        logger.info("\n" + "=" * 60)
        logger.info("Бот успешно запущен и работает!")
        logger.info("=" * 60)
        logger.info("\nБот отслеживает указанные чаты и будет отправлять")
        logger.info("уведомления о релевантных вакансиях.")
        logger.info("\nДля остановки нажмите Ctrl+C")
        logger.info("=" * 60 + "\n")
        
        # Основной цикл
        await bot.run()
        
        return 0
    
    except KeyboardInterrupt:
        logger.info("\n\nПолучен сигнал остановки (Ctrl+C)")
        return 0
    
    except Exception as e:
        logger.error(f"\n\nКритическая ошибка: {e}", exc_info=True)
        return 1
    
    finally:
        # Очистка ресурсов
        logger.info("\nЗавершение работы...")
        try:
            await db.close()
            logger.info("База данных закрыта")
        except:
            pass
        
        logger.info("Бот остановлен")
        logger.info("=" * 60)


def run():
    """Обертка для запуска асинхронной функции"""
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Прервано пользователем")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    run()

