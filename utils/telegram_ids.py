"""
Утилиты для работы с Telegram ID

Telegram использует разные форматы ID в разных контекстах:
- Bot API: -100XXXXXXXXXX (отрицательные с префиксом 100)
- Telethon: XXXXXXXXXX (положительные, без префикса)
- MTProto: разные форматы для разных типов сущностей

Этот модуль предоставляет функции для конвертации между форматами.
"""


def bot_api_to_telethon(bot_api_id: int) -> int:
    """
    Конвертирует ID из формата Bot API в формат Telethon.

    Bot API использует отрицательные ID с префиксом -100 для каналов/супергрупп:
    -1001234567890 -> 1234567890

    Args:
        bot_api_id: ID в формате Bot API (например -1001234567890)

    Returns:
        ID в формате Telethon (например 1234567890)

    Examples:
        >>> bot_api_to_telethon(-1001234567890)
        1234567890
        >>> bot_api_to_telethon(123456)  # Обычный user ID
        123456
    """
    if bot_api_id >= 0:
        return bot_api_id

    # Отрицательный ID - проверяем формат -100XXXXXXXXXX
    id_str = str(abs(bot_api_id))
    if id_str.startswith('100') and len(id_str) > 10:
        # Убираем префикс '100'
        return int(id_str[3:])

    return abs(bot_api_id)


def telethon_to_bot_api(telethon_id: int, is_channel: bool = True) -> int:
    """
    Конвертирует ID из формата Telethon в формат Bot API.

    Args:
        telethon_id: ID в формате Telethon (например 1234567890)
        is_channel: True если это канал/супергруппа

    Returns:
        ID в формате Bot API (например -1001234567890)

    Examples:
        >>> telethon_to_bot_api(1234567890, is_channel=True)
        -1001234567890
        >>> telethon_to_bot_api(123456, is_channel=False)
        123456
    """
    if not is_channel:
        return telethon_id

    # Для каналов/супергрупп добавляем префикс -100
    return -1000000000000 - telethon_id


def normalize_channel_id(channel_id: int) -> int:
    """
    Нормализует ID канала к формату Telethon (положительный без префикса).

    Args:
        channel_id: ID в любом формате

    Returns:
        Нормализованный ID для использования с Telethon
    """
    if channel_id < 0:
        return bot_api_to_telethon(channel_id)
    return channel_id


def is_bot_api_format(channel_id: int) -> bool:
    """
    Check if ID is in Bot API format (-100XXXXXXXXXX).

    Args:
        channel_id: ID to check

    Returns:
        True if ID is in Bot API format
    """
    if channel_id >= 0:
        return False

    id_str = str(abs(channel_id))
    return id_str.startswith('100') and len(id_str) > 10


def extract_topic_id_from_message(message) -> int | None:
    """
    Extract topic_id from a Telethon message object.

    Telegram forum topics use reply_to fields to indicate which topic
    a message belongs to. This function checks multiple attributes
    to find the topic ID.

    Args:
        message: Telethon Message object

    Returns:
        Topic ID if found, None otherwise

    Note:
        This only handles attribute extraction. For cache/API fallbacks,
        see ConversationManager._handle_group_message()
    """
    topic_id = None

    # Method 1: via reply_to.reply_to_top_id
    if hasattr(message, 'reply_to') and message.reply_to:
        topic_id = getattr(message.reply_to, 'reply_to_top_id', None)
        if not topic_id:
            # Check if this is a forum topic
            is_forum_topic = getattr(message.reply_to, 'forum_topic', False)
            if is_forum_topic:
                reply_to_msg_id = getattr(message.reply_to, 'reply_to_msg_id', None)
                if reply_to_msg_id:
                    topic_id = reply_to_msg_id

    # Method 2: direct message attribute
    if not topic_id:
        topic_id = getattr(message, 'reply_to_top_id', None)

    # Method 3: via message_thread_id
    if not topic_id:
        topic_id = getattr(message, 'message_thread_id', None)

    return topic_id
