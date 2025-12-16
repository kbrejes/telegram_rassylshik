"""
Унифицированная логика retry и обработки FloodWait для Telegram API
"""
import asyncio
import time
import logging
from typing import Callable, Any, Optional, TypeVar
from functools import wraps
from telethon import errors

logger = logging.getLogger(__name__)

T = TypeVar('T')


class FloodWaitTracker:
    """Трекер FloodWait для отслеживания времени блокировки"""

    def __init__(self):
        self._flood_wait_until: Optional[float] = None

    @property
    def flood_wait_until(self) -> Optional[float]:
        """Время до которого действует FloodWait"""
        return self._flood_wait_until

    @property
    def is_blocked(self) -> bool:
        """Проверяет, активна ли блокировка FloodWait"""
        if not self._flood_wait_until:
            return False
        return time.time() < self._flood_wait_until

    @property
    def remaining_seconds(self) -> int:
        """Возвращает оставшееся время блокировки в секундах"""
        if not self._flood_wait_until:
            return 0
        remaining = int(self._flood_wait_until - time.time())
        return max(0, remaining)

    def set_flood_wait(self, seconds: int) -> None:
        """Устанавливает время блокировки"""
        self._flood_wait_until = time.time() + seconds
        logger.warning(f"FloodWait установлен на {seconds} секунд")

    def clear(self) -> None:
        """Сбрасывает блокировку"""
        self._flood_wait_until = None


def calculate_backoff(attempt: int, base: float = 1.0, max_delay: float = 60.0) -> float:
    """
    Вычисляет задержку для exponential backoff

    Args:
        attempt: Номер попытки (0-indexed)
        base: Базовая задержка в секундах
        max_delay: Максимальная задержка

    Returns:
        Задержка в секундах
    """
    delay = base * (2 ** attempt)
    return min(delay, max_delay)


def format_wait_time(seconds: int) -> str:
    """Форматирует время ожидания в человекочитаемый формат"""
    if seconds < 60:
        return f"{seconds}с"
    elif seconds < 3600:
        return f"{seconds // 60}м {seconds % 60}с"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}ч {minutes}м"


async def retry_on_flood(
    func: Callable[..., T],
    *args,
    max_retries: int = 3,
    max_wait: float = 60.0,
    base_delay: float = 1.0,
    flood_tracker: Optional[FloodWaitTracker] = None,
    **kwargs
) -> T:
    """
    Выполняет функцию с retry при FloodWait и других ошибках

    Args:
        func: Async функция для выполнения
        *args: Позиционные аргументы
        max_retries: Максимальное число попыток
        max_wait: Максимальное время ожидания FloodWait (секунды)
        base_delay: Базовая задержка для backoff
        flood_tracker: Опциональный трекер FloodWait
        **kwargs: Именованные аргументы

    Returns:
        Результат функции

    Raises:
        Exception: Если все попытки исчерпаны
    """
    last_exception = None

    for attempt in range(max_retries):
        try:
            # Проверяем, не заблокированы ли мы
            if flood_tracker and flood_tracker.is_blocked:
                wait_time = flood_tracker.remaining_seconds
                if wait_time > max_wait:
                    logger.warning(f"FloodWait слишком долгий ({wait_time}с), пропускаем")
                    raise errors.FloodWaitError(request=None, capture=wait_time)
                logger.info(f"Ожидание FloodWait: {format_wait_time(wait_time)}")
                await asyncio.sleep(wait_time)

            return await func(*args, **kwargs)

        except errors.FloodWaitError as e:
            last_exception = e
            wait_time = min(e.seconds, max_wait)

            if flood_tracker:
                flood_tracker.set_flood_wait(e.seconds)

            logger.warning(
                f"FloodWait: {e.seconds}с, ожидаем {wait_time}с "
                f"(попытка {attempt + 1}/{max_retries})"
            )

            if attempt < max_retries - 1:
                await asyncio.sleep(wait_time)
            else:
                raise

        except (errors.RPCError, ConnectionError, TimeoutError) as e:
            last_exception = e
            delay = calculate_backoff(attempt, base_delay, max_wait)

            logger.warning(
                f"Ошибка {type(e).__name__}: {e}, "
                f"повтор через {delay:.1f}с (попытка {attempt + 1}/{max_retries})"
            )

            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
            else:
                raise

    raise last_exception


def with_retry(
    max_retries: int = 3,
    max_wait: float = 60.0,
    base_delay: float = 1.0
):
    """
    Декоратор для автоматического retry с exponential backoff

    Args:
        max_retries: Максимальное число попыток
        max_wait: Максимальное время ожидания
        base_delay: Базовая задержка

    Usage:
        @with_retry(max_retries=3)
        async def send_message(...):
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            return await retry_on_flood(
                func, *args,
                max_retries=max_retries,
                max_wait=max_wait,
                base_delay=base_delay,
                **kwargs
            )
        return wrapper
    return decorator


async def wait_for_flood_clear(
    tracker: FloodWaitTracker,
    check_interval: float = 60.0,
    on_tick: Optional[Callable[[int], Any]] = None
) -> None:
    """
    Ожидает окончания FloodWait с периодическими проверками

    Args:
        tracker: FloodWait трекер
        check_interval: Интервал проверки в секундах
        on_tick: Callback вызываемый каждый интервал с оставшимся временем
    """
    while tracker.is_blocked:
        remaining = tracker.remaining_seconds
        if on_tick:
            on_tick(remaining)
        await asyncio.sleep(min(check_interval, remaining))
