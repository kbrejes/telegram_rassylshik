# Development Rules for Claude

**IMPORTANT: Always communicate in English in this project.**

## When creating/modifying code, ALWAYS:

### 1. Edge Cases
- Check boundary conditions (empty strings, None, 0, empty lists)
- Handle timeouts and network errors
- Consider race conditions in async code

### 2. Error Handling
- Catch specific exceptions, not bare `except Exception`
- Log errors with context (what was being done, with what data)
- Return clear messages to the user

### 3. Tests
- Every function > 10 lines should have a unit test
- Mock external APIs (Telegram, LLM, databases)
- Test edge cases: empty data, network errors, timeouts
- Tests go in `tests/` directory

---

## Telethon Sessions - CRITICALLY IMPORTANT

### Session Rules:

1. **One session file = one client**
   - Never open one session file from two places
   - Use `get_or_create_agent()` from `agent_pool.py`

2. **Use TimeoutSQLiteSession**
   ```python
   from auth.base import TimeoutSQLiteSession

   session = TimeoutSQLiteSession(session_path)
   client = TelegramClient(session, api_id, api_hash)
   ```
   - 30 second timeout for waiting on DB unlock
   - WAL mode for concurrent access

3. **StringSession for web operations**
   - If main bot uses SQLite session, web should use StringSession
   - See `web/utils.py` for example

4. **Session paths**
   - Always use `session_config.py`:
     - `get_bot_session_path()` - for main bot
     - `get_agent_session_path(name)` - for agents
   - Never use relative paths

5. **Session error handling**
   ```python
   from telethon import errors

   try:
       await client.connect()
   except errors.AuthKeyDuplicatedError:
       # Session used from different IP - delete and recreate
       delete_session_file(session_path)
   except Exception as e:
       if "database is locked" in str(e):
           # Session is locked by another process
           pass
   ```

6. **Proper disconnection**
   ```python
   await client.disconnect()
   ```

---

## Project Architecture

### Key Components:

- `bot_multi.py` - main bot, channel monitoring
- `agent_pool.py` - global agent registry (ALWAYS use `get_or_create_agent()`)
- `conversation_manager.py` - forum topic management
- `ai_conversation/` - AI conversation processing
- `auth/` - Telegram authentication
- `web/` - FastAPI interface

### Configuration:
- `session_config.py` - session paths
- `config.py` - environment variables
- `config_manager.py` - dynamic configs (channels, templates)

---

## Telethon Event Loops - CRITICAL

**From official Telethon docs: https://docs.telethon.dev/en/stable/concepts/asyncio.html**

### The Rule
**Each thread must have its own TelegramClient instance. You CANNOT share clients across threads/event loops.**

Telethon clients are bound to the event loop where they connected. Using a client from a different event loop causes:
```
RuntimeError: The asyncio event loop must not change after connection
```

### Our Architecture
- **Main thread**: `asyncio.run(run_bot())` - bot client + agents connected here
- **Web thread**: uvicorn creates its own event loop

### NEVER DO THIS
```python
# In web route (uvicorn thread):
from src.agent_pool import get_existing_agent
agent = await get_existing_agent(session_name)  # Connected in bot thread!
await agent.client.send_message(...)  # ERROR: different event loop!
```

### CORRECT APPROACH
```python
# In web route: create a SEPARATE temporary client
from web.utils import get_agent_client
agent_client, should_disconnect = await get_agent_client(session_name)
try:
    await agent_client.send_message(...)
finally:
    if should_disconnect:
        await agent_client.disconnect()
```

### Summary
- **Bot thread agents** (`agent_pool.py`): Used only by bot's message handlers
- **Web thread operations**: Create temporary clients via `get_agent_client()`
- **Never share clients between threads**

---

## Common Mistakes - DO NOT

1. **DO NOT** create `TelegramClient` directly without `TimeoutSQLiteSession`
2. **DO NOT** use relative paths for sessions
3. **DO NOT** ignore `AuthKeyDuplicatedError`
4. **DO NOT** open one session file from multiple processes
5. **DO NOT** forget `await client.disconnect()` on shutdown
6. **DO NOT** share TelegramClient instances between threads/event loops

---

## Server Access

**ALWAYS use gcloud for server access, NEVER use direct SSH:**
```bash
# CORRECT - use gcloud with IAP tunnel:
gcloud compute ssh telegram-rassylshik-bot --zone=us-central1-a --tunnel-through-iap

# With command:
gcloud compute ssh telegram-rassylshik-bot --zone=us-central1-a --tunnel-through-iap --command="sudo <command>"

# WRONG - never use direct SSH:
ssh root@35.188.128.163  # DO NOT USE
```

**Project path on server:**
```
/home/brejestovski_kirill/telegram_rassylshik/
```

**Check logs on server:**
```bash
gcloud compute ssh telegram-rassylshik-bot --zone=us-central1-a --tunnel-through-iap --command="sudo tail -100 /home/brejestovski_kirill/telegram_rassylshik/logs/bot_multi.log"
```

---

## Deployment - TWO SEPARATE SERVICES

**CRITICAL: The bot and web API run as separate processes. You must restart BOTH when deploying changes.**

| Service | Command | What it runs | Log file |
|---------|---------|--------------|----------|
| `bot_multi.service` | `python3 bot_multi.py` | Telegram monitoring, CRM, agents | `logs/bot_multi.log` |
| `web.service` | `uvicorn web.app:app` | Web API (FastAPI), all `/api/*` routes | `logs/uvicorn.log` |

**Deploy changes to server:**
```bash
# 1. Pull latest code
gcloud compute ssh telegram-rassylshik-bot --zone=us-central1-a --tunnel-through-iap --command="cd /home/brejestovski_kirill/telegram_rassylshik && sudo git pull origin feature/self-correcting-prompts"

# 2. Restart BOTH services
gcloud compute ssh telegram-rassylshik-bot --zone=us-central1-a --tunnel-through-iap --command="sudo systemctl restart bot_multi web"
```

**Which service to restart:**
- Changed `bot_multi.py`, `src/`, `ai_conversation/` → restart `bot_multi`
- Changed `web/` (routes, templates, API) → restart `web`
- Changed both → restart both: `sudo systemctl restart bot_multi web`

**Check service status:**
```bash
gcloud compute ssh telegram-rassylshik-bot --zone=us-central1-a --tunnel-through-iap --command="sudo systemctl status bot_multi web --no-pager"
```

---

## Stable Version

**Current stable:** `stable-2025-12-25`
- Commit: `de35244`
- Branch: `feature/self-correcting-prompts`

**Features:**
- Navigation menu on all pages (Channels, Agents, AI Stats, Bot Auth)
- Agent spam limitation handling with automatic fallback/rotation
- Spam limit UI on agents page (badge + summary)
- Bot connection status on /auth page
- AI self-correction system enabled
- Agents management page (`/agents`) - connect, disconnect, add agents
- Connection status tracking (`src/connection_status.py`)
- Command queue for web-to-bot communication (`src/command_queue.py`)
- Channel edit with agent link/unlink UI

**Rollback to stable:**
```bash
git checkout stable-2025-12-25
```

**Deploy stable to server:**
```bash
gcloud compute ssh telegram-rassylshik-bot --zone=us-central1-a --tunnel-through-iap --command="cd /home/brejestovski_kirill/telegram_rassylshik && sudo git fetch --tags && sudo git checkout stable-2025-12-25"
```
