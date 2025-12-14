"""
Главный файл для запуска Job Notification Bot с веб-интерфейсом
"""
import asyncio
import logging
import uvicorn
from bot_multi import bot
from multiprocessing import Process
import sys


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/bot_multi.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


def run_web_interface():
    """Запускает веб-интерфейс"""
    logger.info("Запуск веб-интерфейса на http://0.0.0.0:8080")
    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=8080,
        log_level="info"
    )


async def run_bot():
    """Запускает телеграм бота"""
    try:
        await bot.start()
        await bot.run()
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}", exc_info=True)
        sys.exit(1)


def main():
    """Главная функция"""
    logger.info("=== Job Notification Bot - Multi-Channel ===")
    logger.info("Запуск системы...")
    
    # Создаем директории
    import os
    os.makedirs('logs', exist_ok=True)
    os.makedirs('configs', exist_ok=True)
    
    # Запускаем веб-интерфейс в отдельном процессе
    web_process = Process(target=run_web_interface)
    web_process.start()
    
    logger.info("Веб-интерфейс запущен")
    logger.info("Доступ: http://localhost:8080")
    
    # Запускаем бота в основном процессе
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки")
    finally:
        # Останавливаем веб-интерфейс
        logger.info("Остановка веб-интерфейса...")
        web_process.terminate()
        web_process.join()
        logger.info("Система остановлена")


if __name__ == "__main__":
    main()

