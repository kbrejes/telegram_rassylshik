# Architecture Overview

## System Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              TELEGRAM PLATFORM                               │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                   │
│  │ Source       │    │ Source       │    │ CRM Group    │                   │
│  │ Channel 1    │    │ Channel 2    │    │ (Forum)      │                   │
│  └──────┬───────┘    └──────┬───────┘    └──────▲───────┘                   │
│         │                   │                   │                           │
└─────────┼───────────────────┼───────────────────┼───────────────────────────┘
          │                   │                   │
          ▼                   ▼                   │
┌─────────────────────────────────────────────────┼───────────────────────────┐
│                        BOT PROCESS              │                           │
│  ┌────────────────────────────────────────────┐ │                           │
│  │              bot_multi.py                  │ │                           │
│  │  ┌─────────────────────────────────────┐   │ │                           │
│  │  │   TelegramClient (Userbot)          │   │ │                           │
│  │  │   - Monitors source channels        │───┼─┘                           │
│  │  │   - Receives new messages           │   │                             │
│  │  └───────────────┬─────────────────────┘   │                             │
│  │                  │                         │                             │
│  │                  ▼                         │                             │
│  │  ┌─────────────────────────────────────┐   │                             │
│  │  │   MessageProcessor                  │   │                             │
│  │  │   - Filters messages                │   │                             │
│  │  │   - Extracts contacts, keywords     │   │                             │
│  │  └───────────────┬─────────────────────┘   │                             │
│  │                  │                         │                             │
│  │                  ▼                         │                             │
│  │  ┌─────────────────────────────────────┐   │                             │
│  │  │   JobAnalyzer (LLM)                 │   │                             │
│  │  │   - AI relevance check              │   │                             │
│  │  │   - Extract TG username             │   │                             │
│  │  └───────────────┬─────────────────────┘   │                             │
│  │                  │                         │                             │
│  │         ┌────────┴────────┐                │                             │
│  │         ▼                 ▼                │                             │
│  │  ┌─────────────┐   ┌─────────────────┐     │                             │
│  │  │ Database    │   │  CRMHandler     │     │                             │
│  │  │ (SQLite)    │   │  - Auto-respond │     │                             │
│  │  └─────────────┘   │  - Create topic │     │                             │
│  │                    └────────┬────────┘     │                             │
│  │                             │              │                             │
│  │                             ▼              │                             │
│  │                    ┌─────────────────┐     │                             │
│  │                    │   AgentPool     │     │                             │
│  │                    │   - Agent 1     │─────┼─────► Send to contact       │
│  │                    │   - Agent 2     │     │                             │
│  │                    │   - Agent N     │     │                             │
│  │                    └────────┬────────┘     │                             │
│  │                             │              │                             │
│  │                             ▼              │                             │
│  │                    ┌─────────────────┐     │                             │
│  │                    │ AIHandler       │     │                             │
│  │                    │ - Generate reply│     │                             │
│  │                    │ - State machine │     │                             │
│  │                    └─────────────────┘     │                             │
│  │                                            │                             │
│  │  ┌─────────────────────────────────────┐   │                             │
│  │  │   CommandHandler                    │   │                             │
│  │  │   - Process web commands            │◄──┼────── Command Queue         │
│  │  │   - Connect/disconnect agents       │   │                             │
│  │  └─────────────────────────────────────┘   │                             │
│  └────────────────────────────────────────────┘                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                         WEB PROCESS (uvicorn)                               │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                         web/app.py (FastAPI)                           │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │ │
│  │  │ /channels    │  │ /agents      │  │ /api/*       │                  │ │
│  │  │ Channel mgmt │  │ Agent mgmt   │  │ REST API     │                  │ │
│  │  └──────────────┘  └──────────────┘  └──────────────┘                  │ │
│  │                                                                        │ │
│  │  ┌────────────────────────────────────────────────────────────────┐    │ │
│  │  │  ConfigManager (reads/writes channels_config.json)             │    │ │
│  │  └────────────────────────────────────────────────────────────────┘    │ │
│  │                                                                        │ │
│  │  ┌────────────────────────────────────────────────────────────────┐    │ │
│  │  │  CommandQueue (sends commands to bot process)                  │────┼─┤
│  │  └────────────────────────────────────────────────────────────────┘    │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. Message Processing Flow

```
New Message in Source Channel
         │
         ▼
    ┌────────────┐
    │ Filter:    │
    │ - Has text │ ──No──► Ignore
    │ - > 50 chr │
    │ - < 24h old│
    └────┬───────┘
         │ Yes
         ▼
    ┌────────────┐
    │ Duplicate? │ ──Yes─► Ignore
    └────┬───────┘
         │ No
         ▼
    ┌────────────┐
    │ LLM Check  │
    │ (optional) │ ──Not relevant─► Save as filtered
    └────┬───────┘
         │ Relevant
         ▼
    ┌────────────┐
    │ Match      │
    │ Outputs    │ ──No match─► Save as not relevant
    └────┬───────┘
         │ Has matches
         ▼
    ┌────────────┐      ┌────────────┐
    │ Save to DB │      │ Send       │
    │            │      │ Notification│
    └────────────┘      └────────────┘
         │
         ▼
    ┌────────────┐
    │ CRM        │
    │ Workflow   │
    └────┬───────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌────────┐
│ Auto-  │ │ Create │
│ respond│ │ Topic  │
└────────┘ └────────┘
```

### 2. CRM Conversation Flow

```
Contact responds to auto-message
         │
         ▼
    ┌────────────┐
    │ Agent      │
    │ receives   │
    └────┬───────┘
         │
    ┌────┴────────────┐
    │                 │
    ▼                 ▼
┌────────────┐  ┌────────────┐
│ AI Handler │  │ Mirror to  │
│ - Analyze  │  │ CRM Topic  │
│ - Generate │  │            │
│   reply    │  │            │
└────┬───────┘  └────────────┘
     │
     ▼
┌────────────┐
│ Send AI    │
│ response   │
└────────────┘
```

## Module Responsibilities

| Module | Responsibility |
|--------|---------------|
| `bot_multi.py` | Main entry point, event loop, orchestration |
| `src/bot_commands.py` | Command handlers (connect/disconnect agents) |
| `src/crm_handler.py` | CRM workflow: auto-response, topic creation |
| `src/agent_pool.py` | Manage pool of Telegram agent accounts |
| `src/agent_account.py` | Individual agent account wrapper |
| `src/message_processor.py` | Message filtering, contact extraction |
| `src/job_analyzer.py` | LLM-based job relevance analysis |
| `src/database.py` | SQLite persistence |
| `src/config_manager.py` | Load/save channel configuration |
| `src/conversation_manager.py` | Manage CRM forum topics |
| `ai_conversation/handler.py` | AI response generation |
| `ai_conversation/llm_client.py` | LLM provider abstraction |
| `web/app.py` | FastAPI web interface |

## Thread Safety

**Critical**: The bot and web run in separate event loops.

```
BOT THREAD                          WEB THREAD
───────────                         ──────────
asyncio.run(bot.run())              uvicorn.run(app)
     │                                   │
     │ TelegramClient                    │ FastAPI
     │ connected here                    │
     │                                   │
     │ AgentPool                         │ CANNOT use
     │ connected here                    │ bot's agents!
     │                                   │
     ▼                                   ▼
Use agents directly              Create temp client
                                 via get_agent_client()
```

**Rule**: Never share TelegramClient between threads. Web routes must create temporary clients.

## Configuration

```
.env                          # API keys, phone numbers
     │
     ▼
configs/
├── channels_config.json      # Channel definitions
│   ├── output channels
│   ├── input sources
│   ├── agents
│   └── filters
│
└── llm_providers.json        # LLM settings
```
