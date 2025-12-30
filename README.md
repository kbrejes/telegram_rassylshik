# Job Notification Bot

Telegram userbot for monitoring job vacancies with AI-powered responses and CRM functionality.

## Architecture Overview

```
                                    +------------------+
                                    |   Web Interface  |
                                    |  (FastAPI/Jinja) |
                                    +--------+---------+
                                             |
+----------------+                           v
|  Telegram      |    +-----------+    +------------+
|  Channels      +--->| bot_multi |<-->| configs/   |
|  (sources)     |    |   .py     |    | JSON files |
+----------------+    +-----+-----+    +------------+
                            |
              +-------------+-------------+
              |             |             |
              v             v             v
        +----------+  +-----------+  +----------+
        | Message  |  |    CRM    |  |   Job    |
        | Processor|  |  Handler  |  | Analyzer |
        +----------+  +-----+-----+  +----------+
                            |             |
              +-------------+             |
              |             |             |
              v             v             v
        +----------+  +-----------+  +----------+
        |  Agent   |  |    AI     |  | Database |
        |   Pool   |  | Handlers  |  | (SQLite) |
        +----------+  +-----------+  +----------+
```

## Project Structure

```
job_notification_bot/
├── bot_multi.py           # Main bot entry point (Telegram monitoring)
├── web/                   # Web interface (FastAPI)
│   ├── app.py            # FastAPI application
│   ├── routes/           # API endpoints
│   └── templates/        # Jinja2 HTML templates
├── src/                   # Core business logic
│   ├── config.py         # Environment config loader
│   ├── config_manager.py # Channel configuration manager
│   ├── config_models.py  # Configuration dataclasses
│   ├── database.py       # SQLite database operations
│   ├── crm_handler.py    # CRM: auto-responses, topics
│   ├── agent_pool.py     # Telegram agent management
│   ├── agent_account.py  # Individual agent accounts
│   ├── message_processor.py  # Message filtering
│   ├── job_analyzer.py   # LLM-based job analysis
│   └── conversation_manager.py  # Forum topic management
├── ai_conversation/       # AI conversation handling
│   ├── handler.py        # AI response generation
│   ├── llm_client.py     # LLM provider abstraction
│   └── memory.py         # Conversation memory
├── auth/                  # Telegram authentication
├── configs/               # Configuration files
│   └── channels_config.json  # Channel definitions
├── tests/                 # Test files
└── docs/                  # Documentation
```

## Key Concepts

| Term | Description |
|------|-------------|
| **Bot** | Main Telegram userbot that monitors channels |
| **Agent** | Separate Telegram account for sending auto-responses |
| **CRM Group** | Telegram forum where conversations are mirrored |
| **Topic** | Forum thread for each contact |
| **Channel** | Output destination for notifications |
| **Source** | Input Telegram channel/group to monitor |

## Quick Start

### Prerequisites

- Python 3.10+
- Telegram API credentials (from https://my.telegram.org)
- At least one Telegram account for the bot

### 1. Clone and Install

```bash
git clone <repo-url>
cd job_notification_bot
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your Telegram credentials
```

Required variables:
```env
API_ID=your_api_id
API_HASH=your_api_hash
PHONE=+1234567890
```

### 3. Configure Channels

Edit `configs/channels_config.json` or use the web interface.

### 4. Run

```bash
# Start the bot
python bot_multi.py

# In another terminal, start the web interface
uvicorn web.app:app --host 0.0.0.0 --port 8000
```

## Two Services

The application runs as two separate processes:

| Service | Command | Purpose |
|---------|---------|---------|
| `bot_multi.py` | `python bot_multi.py` | Telegram monitoring, CRM, agents |
| `web.app` | `uvicorn web.app:app` | Web UI, API endpoints |

## Configuration Files

| File | Purpose |
|------|---------|
| `.env` | Environment variables (API keys, phone) |
| `configs/channels_config.json` | Channel definitions, agents, filters |
| `configs/llm_providers.json` | LLM provider settings |

## Documentation

- [Quick Start Guide](docs/QUICKSTART.md)
- [Web Interface Guide](docs/WEB_INTERFACE_GUIDE.md)
- [Development Setup](docs/DEV_SETUP.md)
- [Development Rules](.claude/CLAUDE.md)

## Development

### Running Tests

```bash
pytest tests/ -v
```

### Code Style

- Python 3.10+ with type hints
- Async/await for I/O operations
- Docstrings in English

### Important: Event Loop Rules

Agents are bound to the event loop where they connect. **Never share agents between threads.**

```python
# WRONG - will crash
agent = await get_existing_agent(name)  # Connected in bot thread
await agent.send_message(...)  # Called from web thread - ERROR!

# CORRECT
from web.utils import get_agent_client
client, disconnect = await get_agent_client(name)
try:
    await client.send_message(...)
finally:
    if disconnect:
        await client.disconnect()
```

## License

MIT
