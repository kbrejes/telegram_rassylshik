#!/usr/bin/env python3
"""
Authenticate the discovery bot with Telegram.
Run this once to create the session file.
"""
import asyncio
from telethon import TelegramClient
from config import API_ID, API_HASH, SESSION_PATH, DATA_DIR

# Ensure data directory exists
DATA_DIR.mkdir(exist_ok=True)


async def main():
    print("=== Channel Discovery Bot Authentication ===")
    print(f"Session will be saved to: {SESSION_PATH}.session")
    print()

    if not API_ID or not API_HASH:
        print("ERROR: API_ID and API_HASH must be set in environment variables")
        print("  export API_ID=your_api_id")
        print("  export API_HASH=your_api_hash")
        return

    client = TelegramClient(
        str(SESSION_PATH),
        API_ID,
        API_HASH,
        system_version="4.16.30-vxCUSTOM"
    )

    await client.start()

    me = await client.get_me()
    print(f"\nAuthenticated as: {me.first_name} (@{me.username})")
    print(f"User ID: {me.id}")
    print("\nSession saved. You can now run the service.")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
