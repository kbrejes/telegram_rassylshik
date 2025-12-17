"""
Главный файл для запуска Job Notification Bot с веб-интерфейсом
Архитектура: веб-интерфейс работает независимо от бота
"""
import asyncio
import logging
import os
import signal
import sys
from threading import Thread
import uvicorn

# Настройка логирования (до импорта bot)
os.makedirs('logs', exist_ok=True)
os.makedirs('configs', exist_ok=True)
os.makedirs('sessions', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/bot_multi.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Глобальное состояние бота (доступно из веб-интерфейса)
bot_state = {
    "status": "starting",  # starting, running, waiting_auth, error, stopped
    "error": None,
    "user_info": None,
    "flood_wait_until": None
}


def run_web_interface():
    """Запускает веб-интерфейс в отдельном потоке"""
    # Читаем порт из переменной окружения (Railway использует PORT)
    port = int(os.environ.get("WEB_PORT", os.environ.get("PORT", 8080)))
    logger.info(f"Запуск веб-интерфейса на http://0.0.0.0:{port}")

    # Передаём bot_state в веб-приложение
    import web.app as web_app
    web_app.bot_state = bot_state

    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=port,
        log_level="warning"  # Уменьшаем спам от uvicorn
    )


async def run_bot():
    """Запускает телеграм бота с graceful error handling"""
    from bot_multi import bot, NeedsAuthenticationError
    from telethon import errors
    import time

    while True:
        try:
            bot_state["status"] = "starting"
            bot_state["error"] = None

            # Пытаемся запустить бота
            await bot.start()

            # Если дошли сюда - бот запущен успешно
            bot_state["status"] = "running"
            bot_state["user_info"] = {
                "name": (await bot.client.get_me()).first_name,
                "phone": (await bot.client.get_me()).phone
            }

            # Запускаем основной цикл
            await bot.run()

        except NeedsAuthenticationError:
            # Требуется авторизация через веб-интерфейс
            bot_state["status"] = "waiting_auth"
            bot_state["error"] = "Требуется авторизация. Откройте веб-интерфейс для входа."
            logger.info("Ожидание авторизации через веб-интерфейс...")
            port = os.environ.get("WEB_PORT", os.environ.get("PORT", 8080))
            logger.info(f"Откройте http://localhost:{port}/auth для авторизации")

            # Ждём пока пользователь авторизуется через веб
            while bot_state["status"] == "waiting_auth":
                await asyncio.sleep(5)
                # Проверяем, может сессия уже создана
                if await bot.check_session_valid():
                    logger.info("Обнаружена валидная сессия, перезапуск бота...")
                    break

            # После авторизации - перезапускаем цикл
            continue

        except errors.FloodWaitError as e:
            wait_until = time.time() + e.seconds
            bot_state["status"] = "flood_wait"
            bot_state["flood_wait_until"] = wait_until
            bot_state["error"] = f"Telegram ограничил запросы. Ожидание {e.seconds} секунд (~{e.seconds // 3600}ч {(e.seconds % 3600) // 60}м)"

            logger.warning(f"FloodWaitError: ожидание {e.seconds} секунд")
            logger.info("Веб-интерфейс продолжает работать. Загрузите сессию через /api/bot/upload-session")

            # Ждём, но проверяем каждые 60 секунд не появилась ли сессия
            while time.time() < wait_until:
                await asyncio.sleep(60)
                # Проверяем, может сессия уже загружена
                if await bot.check_session_valid():
                    logger.info("Обнаружена валидная сессия, перезапуск бота...")
                    break

        except errors.SessionPasswordNeededError:
            bot_state["status"] = "waiting_2fa"
            bot_state["error"] = "Требуется пароль двухфакторной аутентификации"
            logger.warning("Требуется 2FA пароль. Используйте веб-интерфейс для авторизации.")
            # Ждём пока пользователь авторизуется через веб
            await asyncio.sleep(30)

        except Exception as e:
            bot_state["status"] = "error"
            bot_state["error"] = str(e)
            logger.error(f"Ошибка бота: {e}", exc_info=True)

            # Ждём перед повторной попыткой
            logger.info("Повторная попытка через 30 секунд...")
            await asyncio.sleep(30)


def main():
    """Главная функция"""
    logger.info("=" * 60)
    logger.info("Job Notification Bot - Multi-Channel")
    logger.info("=" * 60)

    # Запускаем веб-интерфейс в отдельном потоке (работает всегда)
    web_thread = Thread(target=run_web_interface, daemon=True)
    web_thread.start()

    port = os.environ.get("WEB_PORT", os.environ.get("PORT", 8080))
    logger.info(f"Веб-интерфейс запущен: http://localhost:{port}")
    logger.info("Запуск Telegram бота...")

    # Запускаем бота в основном потоке
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки")
        bot_state["status"] = "stopped"

    logger.info("Система остановлена")


if __name__ == "__main__":
    main()
