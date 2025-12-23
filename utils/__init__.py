"""
Utilities for Job Notification Bot
"""
from .retry import (
    FloodWaitTracker,
    calculate_backoff,
    format_wait_time,
    retry_on_flood,
    with_retry,
    wait_for_flood_clear
)
from .telegram_ids import (
    bot_api_to_telethon,
    telethon_to_bot_api,
    normalize_channel_id,
    is_bot_api_format,
    extract_topic_id_from_message
)

__all__ = [
    # Retry utilities
    'FloodWaitTracker',
    'calculate_backoff',
    'format_wait_time',
    'retry_on_flood',
    'with_retry',
    'wait_for_flood_clear',
    # Telegram ID utilities
    'bot_api_to_telethon',
    'telethon_to_bot_api',
    'normalize_channel_id',
    'is_bot_api_format',
    'extract_topic_id_from_message',
]
