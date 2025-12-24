# Local Development Setup

This guide explains how to set up and run the Job Notification Bot locally for development and testing.

## Quick Start

```bash
# Run the setup script
./scripts/dev_setup.sh

# Activate venv
source venv/bin/activate

# Start web interface only (no Telegram)
python -m uvicorn web.app:app --reload --port 8080

# Open http://localhost:8080
```

## Prerequisites

- Python 3.9+
- Docker (optional, for Ollama/Weaviate)
- Telegram API credentials (for full bot testing)

## Setup Options

### Option 1: Automated Setup

```bash
./scripts/dev_setup.sh
```

This will:
- Create virtual environment
- Install dependencies
- Copy `.env.example` to `.env`
- Create required directories
- Optionally start Docker services

### Option 2: Manual Setup

```bash
# Create venv
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-mock

# Setup config
cp .env.example .env
cp configs/channels_config.example.json configs/channels_config.json

# Create directories
mkdir -p data logs sessions configs
```

## Running Modes

### 1. Web Interface Only (Recommended for UI Development)

No Telegram connection needed. Good for testing the web UI and channel configuration.

```bash
source venv/bin/activate
python -m uvicorn web.app:app --reload --port 8080
```

Access: http://localhost:8080

### 2. AI Conversation Testing (No Telegram)

Test AI prompts and conversation flow without Telegram:

```bash
source venv/bin/activate
python tests/conversation_tester.py
```

This runs simulated conversations against different scenarios (skeptical client, busy person, etc.)

### 3. Full Bot (Requires Telegram Auth)

```bash
source venv/bin/activate
python main_multi.py
```

On first run, you'll need to authenticate via the web interface at `/auth`.

## LLM Configuration

The bot supports multiple LLM providers. Configure in `.env`:

### Cloud Providers (Recommended for Dev)

| Provider | Free Tier | Get API Key |
|----------|-----------|-------------|
| **Groq** | Yes (generous) | https://console.groq.com |
| Gemini | 15 RPM, 1M tokens/mo | https://aistudio.google.com/apikey |
| Together | Yes | https://api.together.ai |
| OpenRouter | Free models (:free suffix) | https://openrouter.ai/keys |
| OpenAI | No | https://platform.openai.com/api-keys |

**Recommended for development**: Groq (fast, free tier)

```env
GROQ_API_KEY=gsk_your_key_here
```

### Local LLM (Ollama)

Run your own LLM locally:

```bash
# Start Ollama via Docker
docker-compose -f docker-compose.dev.yml up -d ollama

# Pull a model
docker exec dev_ollama ollama pull qwen2.5:3b

# Or install Ollama directly: https://ollama.ai
```

Configure in `.env`:
```env
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:3b
```

## Docker Services

### Start Individual Services

```bash
# Ollama (local LLM)
docker-compose -f docker-compose.dev.yml up -d ollama

# Weaviate (vector database for semantic memory)
docker-compose -f docker-compose.dev.yml up -d weaviate

# Both
docker-compose -f docker-compose.dev.yml up -d ollama weaviate
```

### Full Bot in Docker

```bash
docker-compose -f docker-compose.dev.yml up bot
```

### Stop Services

```bash
docker-compose -f docker-compose.dev.yml down
```

## Testing

### Run All Tests

```bash
source venv/bin/activate
python -m pytest tests/ -v
```

### Run Specific Test File

```bash
python -m pytest tests/test_self_correction.py -v
```

### Run with Coverage

```bash
python -m pytest tests/ --cov=ai_conversation --cov=src
```

## Project Structure

```
.
├── ai_conversation/     # AI conversation system
│   ├── handler.py       # Main conversation handler
│   ├── llm_client.py    # LLM provider abstraction
│   ├── state_analyzer.py # Conversation phase detection
│   ├── phase_prompts.py  # Phase-specific prompts
│   └── ...
├── src/                 # Core bot logic
│   ├── database.py      # SQLite database
│   ├── config.py        # Environment config
│   └── ...
├── web/                 # FastAPI web interface
│   ├── app.py           # Main app
│   └── routes/          # API routes
├── configs/             # Channel configurations
├── prompts/             # AI prompts
├── tests/               # Test files
└── sessions/            # Telegram session files
```

## Common Tasks

### Add a New Channel

1. Open web interface: http://localhost:8080
2. Go to Channels tab
3. Click "Add Channel"
4. Configure input sources and filters

### Test AI Conversation

```bash
# Run conversation tester
python tests/conversation_tester.py

# Test specific scenario
python tests/conversation_tester.py --scenario skeptical_client
```

### Debug LLM Responses

```python
# Quick test in Python
from ai_conversation.llm_client import UnifiedLLMClient, LLMProviderConfig

client = UnifiedLLMClient(LLMProviderConfig.groq())
response = client.chat([{"role": "user", "content": "Hello!"}])
print(response)
```

### View Logs

```bash
# Bot logs
tail -f logs/bot_multi.log

# Real-time with filtering
tail -f logs/bot_multi.log | grep -E "(ERROR|WARNING)"
```

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `API_ID` | Yes* | Telegram API ID |
| `API_HASH` | Yes* | Telegram API Hash |
| `PHONE` | Yes* | Phone number for auth |
| `NOTIFICATION_USER_ID` | Yes | Your Telegram user ID |
| `GROQ_API_KEY` | Recommended | Groq API key (free tier) |
| `WEB_PORT` | No | Web interface port (default: 8080) |
| `DATABASE_PATH` | No | SQLite path (default: jobs.db) |
| `DEBUG` | No | Enable debug logging |

*Not required for web-only mode

## Troubleshooting

### "FloodWaitError" from Telegram

You're being rate limited. Wait the specified time or use a different account.

### "No module named X"

Make sure venv is activated:
```bash
source venv/bin/activate
```

### LLM not responding

1. Check API key is set in `.env`
2. Check the provider is reachable
3. Try a different provider

### Web interface not loading

Check port is available:
```bash
lsof -i :8080
```

### Session errors

Delete old session and re-authenticate:
```bash
rm sessions/bot_session.session
python main_multi.py
```
