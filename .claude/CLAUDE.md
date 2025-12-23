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

## Common Mistakes - DO NOT

1. **DO NOT** create `TelegramClient` directly without `TimeoutSQLiteSession`
2. **DO NOT** use relative paths for sessions
3. **DO NOT** ignore `AuthKeyDuplicatedError`
4. **DO NOT** open one session file from multiple processes
5. **DO NOT** forget `await client.disconnect()` on shutdown
