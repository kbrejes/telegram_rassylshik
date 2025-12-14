"""
Тестовая отправка сообщения в канал для проверки доступа
"""
import asyncio
from telethon import TelegramClient
from config import config
import sys

async def test_send():
    """Отправляет тестовое сообщение в канал"""
    
    if len(sys.argv) < 2:
        print("Использование: python test_send.py <channel_id>")
        print("Например: python test_send.py -1007795495001")
        return
    
    channel_id = int(sys.argv[1])
    
    print(f"🔄 Подключение к Telegram...")
    client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)
    await client.start(phone=config.PHONE)
    
    me = await client.get_me()
    print(f"✅ Авторизован как: {me.first_name} ({me.phone})")
    
    try:
        # Получаем информацию о канале
        print(f"\n🔍 Получение информации о канале ID: {channel_id}...")
        entity = await client.get_entity(channel_id)
        
        print(f"✅ Канал найден:")
        print(f"   Название: {entity.title}")
        print(f"   Username: @{entity.username if hasattr(entity, 'username') and entity.username else 'нет'}")
        print(f"   ID: {entity.id}")
        
        # Отправляем тестовое сообщение
        print(f"\n📤 Отправка тестового сообщения...")
        message = await client.send_message(
            entity,
            "🧪 **Тестовое сообщение**\n\nЭто тест отправки от Job Notification Bot.\nЕсли вы видите это - всё работает! ✅"
        )
        
        print(f"✅ Сообщение отправлено успешно!")
        print(f"   Message ID: {message.id}")
        print(f"   Время: {message.date}")
        print(f"\n💡 Проверьте канал - сообщение должно там быть!")
        
    except ValueError as e:
        print(f"❌ Ошибка: Канал не найден")
        print(f"   Возможные причины:")
        print(f"   - Неправильный ID")
        print(f"   - Бот не добавлен в канал")
        print(f"   - Канал приватный")
        print(f"\n   Детали: {e}")
        
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        print(f"\n   Возможные причины:")
        print(f"   - Нет прав на отправку сообщений")
        print(f"   - Бот забанен в канале")
        print(f"   - Проблема с интернетом")
    
    finally:
        await client.disconnect()
        print(f"\n👋 Отключено от Telegram")

if __name__ == "__main__":
    asyncio.run(test_send())

