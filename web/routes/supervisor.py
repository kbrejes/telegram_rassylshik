"""
Supervisor AI Chat - Multi-provider AI assistant for monitoring and improving the bot.
Supports: Anthropic (Claude), OpenAI, Ollama
"""
import json
import logging
import aiosqlite
import httpx
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter
from pydantic import BaseModel

# Try importing AI providers
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

from src.config import config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/supervisor", tags=["supervisor"])

# Paths
BASE_DIR = Path(__file__).parent.parent.parent
DB_PATH = BASE_DIR / "jobs.db"
PROMPTS_DIR = BASE_DIR / "prompts"
CONFIGS_DIR = BASE_DIR / "configs"
CONVERSATION_STATES_DIR = BASE_DIR / "data" / "conversation_states"
WORKING_MEMORY_DIR = BASE_DIR / "data" / "working_memory"
PROVIDER_CONFIG_FILE = CONFIGS_DIR / "supervisor_provider.json"


class ChatRequest(BaseModel):
    message: str
    provider: Optional[str] = None  # Override default provider


class ProviderConfig(BaseModel):
    provider: str
    model: Optional[str] = None


# ==================== Provider Definitions ====================

PROVIDERS = {
    "anthropic": {
        "name": "Claude (Anthropic)",
        "models": [
            {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4"},
            {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet"},
            {"id": "claude-3-haiku-20240307", "name": "Claude 3 Haiku (Fast)"},
        ],
        "default_model": "claude-sonnet-4-20250514",
        "requires_key": "ANTHROPIC_API_KEY"
    },
    "openai": {
        "name": "OpenAI",
        "models": [
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini (Fast)"},
            {"id": "gpt-4-turbo", "name": "GPT-4 Turbo"},
        ],
        "default_model": "gpt-4o",
        "requires_key": "OPENAI_API_KEY"
    },
    "ollama": {
        "name": "Ollama (Local)",
        "models": [
            {"id": "llama3.1:8b", "name": "Llama 3.1 8B"},
            {"id": "llama3.1:70b", "name": "Llama 3.1 70B"},
            {"id": "qwen2.5:7b", "name": "Qwen 2.5 7B"},
            {"id": "qwen2.5:32b", "name": "Qwen 2.5 32B"},
            {"id": "mistral:7b", "name": "Mistral 7B"},
        ],
        "default_model": "llama3.1:8b",
        "requires_key": None
    }
}


# ==================== Tools Definition ====================

SUPERVISOR_TOOLS = [
    {
        "name": "get_active_conversations",
        "description": "Get list of all active conversations with their current state (phase, message count, etc.)",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_conversation_messages",
        "description": "Get full message history for a specific conversation by contact_id",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {
                    "type": "string",
                    "description": "The contact ID to get messages for"
                }
            },
            "required": ["contact_id"]
        }
    },
    {
        "name": "get_filter_prompt",
        "description": "Get the current vacancy filter prompt used for AI filtering",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_phase_prompts",
        "description": "Get all conversation phase prompts (discovery, engagement, call_ready, etc.)",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_base_context",
        "description": "Get the base persona/context prompt",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_ai_stats",
        "description": "Get AI performance statistics (conversation outcomes, success rates)",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_recent_vacancies",
        "description": "Get recent processed vacancies with their filter outcomes",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of vacancies to return (default 20)"
                }
            },
            "required": []
        }
    },
    {
        "name": "edit_filter_prompt",
        "description": "Update the vacancy filter prompt",
        "input_schema": {
            "type": "object",
            "properties": {
                "new_prompt": {
                    "type": "string",
                    "description": "The new filter prompt content"
                }
            },
            "required": ["new_prompt"]
        }
    },
    {
        "name": "edit_phase_prompt",
        "description": "Update a specific phase prompt",
        "input_schema": {
            "type": "object",
            "properties": {
                "phase": {
                    "type": "string",
                    "description": "Phase name: discovery, engagement, call_ready, call_pending, or call_declined"
                },
                "new_prompt": {
                    "type": "string",
                    "description": "The new prompt content"
                }
            },
            "required": ["phase", "new_prompt"]
        }
    },
    {
        "name": "edit_base_context",
        "description": "Update the base persona/context prompt",
        "input_schema": {
            "type": "object",
            "properties": {
                "new_content": {
                    "type": "string",
                    "description": "The new base context content"
                }
            },
            "required": ["new_content"]
        }
    }
]

# Convert to OpenAI format
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"]
        }
    }
    for tool in SUPERVISOR_TOOLS
]

SYSTEM_PROMPT = """You are a supervisor AI for a Telegram job notification bot called "Лови Лидов" (Catch Leads).

Your role is to monitor conversations, analyze performance, and improve prompts to increase success rates.

## What you can do:
1. **VIEW**: See all active conversations, their messages, phases, and outcomes
2. **ANALYZE**: Review AI performance stats, vacancy filtering results
3. **EDIT**: Modify prompts (filter prompt, phase prompts, base context) to improve performance

## Context about the bot:
- It monitors Telegram channels for job postings
- Filters vacancies using AI to find relevant ones
- Automatically responds to contacts extracted from vacancies
- Uses phase-based conversation flow: discovery → engagement → call_ready → call_pending
- Goal is to schedule calls with potential clients

## When analyzing:
- Look for patterns in failed conversations
- Identify common rejection reasons
- Check if filter prompt is too strict or too loose
- Review phase transitions and timing

## When editing prompts:
- Explain what you're changing and why
- Make incremental, targeted changes
- Consider A/B testing major changes
- Use Russian language for prompts (target audience)

Always be concise and actionable in your responses."""


# ==================== Provider Config ====================

def load_provider_config() -> Dict[str, str]:
    """Load saved provider configuration."""
    if PROVIDER_CONFIG_FILE.exists():
        try:
            with open(PROVIDER_CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {"provider": "anthropic", "model": None}


def save_provider_config(provider: str, model: Optional[str] = None):
    """Save provider configuration."""
    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROVIDER_CONFIG_FILE, 'w') as f:
        json.dump({"provider": provider, "model": model}, f)


# ==================== Database ====================

async def get_db():
    """Get async database connection."""
    return await aiosqlite.connect(str(DB_PATH))


# ==================== Tool Implementations ====================

async def tool_get_active_conversations() -> str:
    """Get all active conversations with their states."""
    conversations = []

    if CONVERSATION_STATES_DIR.exists():
        for f in CONVERSATION_STATES_DIR.glob("*.json"):
            try:
                with open(f, 'r') as fp:
                    data = json.load(fp)
                    conversations.append({
                        "contact_id": f.stem,
                        "phase": data.get("current_phase", "unknown"),
                        "total_messages": data.get("total_messages", 0),
                        "call_offered": data.get("call_offered", False),
                        "call_scheduled": data.get("call_scheduled", False),
                        "last_interaction": data.get("last_interaction")
                    })
            except Exception as e:
                logger.error(f"Error reading {f}: {e}")

    return json.dumps(conversations, indent=2, ensure_ascii=False)


async def tool_get_conversation_messages(contact_id: str) -> str:
    """Get message history for a contact."""
    memory_file = WORKING_MEMORY_DIR / f"{contact_id}.json"
    state_file = CONVERSATION_STATES_DIR / f"{contact_id}.json"

    result = {"contact_id": contact_id, "messages": [], "state": None}

    if memory_file.exists():
        try:
            with open(memory_file, 'r') as f:
                result["messages"] = json.load(f)
        except Exception as e:
            result["error"] = f"Error reading messages: {e}"

    if state_file.exists():
        try:
            with open(state_file, 'r') as f:
                result["state"] = json.load(f)
        except Exception as e:
            pass

    return json.dumps(result, indent=2, ensure_ascii=False)


async def tool_get_filter_prompt() -> str:
    """Get current filter prompt."""
    from web.utils import load_filter_prompt
    from src.job_analyzer import JobAnalyzer

    custom = load_filter_prompt()
    analyzer = JobAnalyzer(providers_config={}, min_salary_rub=70000)
    current = analyzer._get_system_prompt()
    default = analyzer._get_default_system_prompt()

    return json.dumps({
        "is_custom": custom is not None,
        "current_prompt": current,
        "default_prompt": default
    }, indent=2, ensure_ascii=False)


async def tool_get_phase_prompts() -> str:
    """Get all phase prompts."""
    phases_dir = PROMPTS_DIR / "phases"
    prompts = {}

    if phases_dir.exists():
        for f in phases_dir.glob("*.txt"):
            try:
                prompts[f.stem] = f.read_text(encoding='utf-8')
            except Exception as e:
                prompts[f.stem] = f"Error: {e}"

    return json.dumps(prompts, indent=2, ensure_ascii=False)


async def tool_get_base_context() -> str:
    """Get base context prompt."""
    base_file = PROMPTS_DIR / "base_context.txt"

    if base_file.exists():
        return base_file.read_text(encoding='utf-8')
    return "Base context file not found"


async def tool_get_ai_stats() -> str:
    """Get AI performance statistics."""
    conn = await get_db()

    try:
        cursor = await conn.execute("""
            SELECT outcome, COUNT(*) as count
            FROM conversation_outcomes
            GROUP BY outcome
        """)
        outcomes = {row[0]: row[1] for row in await cursor.fetchall()}

        cursor = await conn.execute("SELECT COUNT(*) FROM conversation_outcomes")
        total = (await cursor.fetchone())[0]

        cursor = await conn.execute("""
            SELECT contact_id, outcome, total_messages, created_at
            FROM conversation_outcomes
            ORDER BY created_at DESC
            LIMIT 10
        """)
        recent = [
            {"contact_id": r[0], "outcome": r[1], "messages": r[2], "date": r[3]}
            for r in await cursor.fetchall()
        ]

        await conn.close()

        return json.dumps({
            "total_conversations": total,
            "outcomes": outcomes,
            "recent": recent
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        await conn.close()
        return json.dumps({"error": str(e)})


async def tool_get_recent_vacancies(limit: int = 20) -> str:
    """Get recent vacancies."""
    conn = await get_db()

    try:
        cursor = await conn.execute(f"""
            SELECT id, chat_title, is_relevant, ai_reason, processed_at
            FROM processed_jobs
            ORDER BY processed_at DESC
            LIMIT {limit}
        """)
        vacancies = [
            {
                "id": r[0],
                "source": r[1],
                "passed": bool(r[2]),
                "reason": r[3],
                "date": r[4]
            }
            for r in await cursor.fetchall()
        ]
        await conn.close()

        return json.dumps(vacancies, indent=2, ensure_ascii=False)
    except Exception as e:
        await conn.close()
        return json.dumps({"error": str(e)})


async def tool_edit_filter_prompt(new_prompt: str) -> str:
    """Edit the filter prompt."""
    from web.utils import save_filter_prompt

    try:
        save_filter_prompt(new_prompt)
        return json.dumps({"success": True, "message": "Filter prompt updated"})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


async def tool_edit_phase_prompt(phase: str, new_prompt: str) -> str:
    """Edit a phase prompt."""
    valid_phases = ["discovery", "engagement", "call_ready", "call_pending", "call_declined"]

    if phase not in valid_phases:
        return json.dumps({"success": False, "error": f"Invalid phase. Valid: {valid_phases}"})

    phase_file = PROMPTS_DIR / "phases" / f"{phase}.txt"

    try:
        phase_file.parent.mkdir(parents=True, exist_ok=True)
        phase_file.write_text(new_prompt, encoding='utf-8')
        return json.dumps({"success": True, "message": f"Phase prompt '{phase}' updated"})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


async def tool_edit_base_context(new_content: str) -> str:
    """Edit base context."""
    base_file = PROMPTS_DIR / "base_context.txt"

    try:
        base_file.parent.mkdir(parents=True, exist_ok=True)
        base_file.write_text(new_content, encoding='utf-8')
        return json.dumps({"success": True, "message": "Base context updated"})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


# Tool dispatcher
TOOL_HANDLERS = {
    "get_active_conversations": lambda _: tool_get_active_conversations(),
    "get_conversation_messages": lambda args: tool_get_conversation_messages(args.get("contact_id", "")),
    "get_filter_prompt": lambda _: tool_get_filter_prompt(),
    "get_phase_prompts": lambda _: tool_get_phase_prompts(),
    "get_base_context": lambda _: tool_get_base_context(),
    "get_ai_stats": lambda _: tool_get_ai_stats(),
    "get_recent_vacancies": lambda args: tool_get_recent_vacancies(args.get("limit", 20)),
    "edit_filter_prompt": lambda args: tool_edit_filter_prompt(args.get("new_prompt", "")),
    "edit_phase_prompt": lambda args: tool_edit_phase_prompt(args.get("phase", ""), args.get("new_prompt", "")),
    "edit_base_context": lambda args: tool_edit_base_context(args.get("new_content", ""))
}


async def execute_tool(name: str, args: dict) -> str:
    """Execute a tool and return the result."""
    handler = TOOL_HANDLERS.get(name)
    if handler:
        return await handler(args)
    return f"Unknown tool: {name}"


# ==================== Provider Implementations ====================

async def chat_with_anthropic(messages: List[Dict], model: str) -> tuple[str, List[Dict]]:
    """Chat using Anthropic Claude API."""
    if not ANTHROPIC_AVAILABLE:
        raise Exception("anthropic package not installed")
    if not config.ANTHROPIC_API_KEY:
        raise Exception("ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=SUPERVISOR_TOOLS,
        messages=messages
    )

    tool_calls_made = []

    # Process tool calls
    while response.stop_reason == "tool_use":
        tool_calls = [block for block in response.content if block.type == "tool_use"]
        tool_calls_made.extend([{"name": t.name, "input": t.input} for t in tool_calls])

        # Execute tools
        tool_results = []
        for tool_call in tool_calls:
            result = await execute_tool(tool_call.name, tool_call.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_call.id,
                "content": result
            })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=SUPERVISOR_TOOLS,
            messages=messages
        )

    # Extract text response
    text_response = ""
    for block in response.content:
        if hasattr(block, 'text'):
            text_response += block.text

    return text_response, tool_calls_made


async def chat_with_openai(messages: List[Dict], model: str) -> tuple[str, List[Dict]]:
    """Chat using OpenAI API."""
    if not OPENAI_AVAILABLE:
        raise Exception("openai package not installed")

    api_key = getattr(config, 'OPENAI_API_KEY', None) or ""
    if not api_key:
        raise Exception("OPENAI_API_KEY not configured")

    client = openai.OpenAI(api_key=api_key)

    # Convert messages to OpenAI format
    oai_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in messages:
        if msg["role"] == "user":
            if isinstance(msg["content"], str):
                oai_messages.append({"role": "user", "content": msg["content"]})
            elif isinstance(msg["content"], list):
                # Tool results
                for item in msg["content"]:
                    if item.get("type") == "tool_result":
                        oai_messages.append({
                            "role": "tool",
                            "tool_call_id": item["tool_use_id"],
                            "content": item["content"]
                        })
        elif msg["role"] == "assistant":
            if isinstance(msg["content"], str):
                oai_messages.append({"role": "assistant", "content": msg["content"]})
            elif isinstance(msg["content"], list):
                # Assistant message with tool calls
                tool_calls = []
                text_content = ""
                for block in msg["content"]:
                    if hasattr(block, 'type') and block.type == "tool_use":
                        tool_calls.append({
                            "id": block.id,
                            "type": "function",
                            "function": {
                                "name": block.name,
                                "arguments": json.dumps(block.input)
                            }
                        })
                    elif hasattr(block, 'text'):
                        text_content += block.text

                oai_msg = {"role": "assistant", "content": text_content or None}
                if tool_calls:
                    oai_msg["tool_calls"] = tool_calls
                oai_messages.append(oai_msg)

    response = client.chat.completions.create(
        model=model,
        messages=oai_messages,
        tools=OPENAI_TOOLS,
        max_tokens=4096
    )

    tool_calls_made = []

    # Process tool calls
    while response.choices[0].message.tool_calls:
        assistant_msg = response.choices[0].message
        oai_messages.append(assistant_msg.model_dump())

        for tool_call in assistant_msg.tool_calls:
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)
            tool_calls_made.append({"name": tool_name, "input": tool_args})

            result = await execute_tool(tool_name, tool_args)
            oai_messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result
            })

        response = client.chat.completions.create(
            model=model,
            messages=oai_messages,
            tools=OPENAI_TOOLS,
            max_tokens=4096
        )

    text_response = response.choices[0].message.content or ""
    return text_response, tool_calls_made


async def chat_with_ollama(messages: List[Dict], model: str) -> tuple[str, List[Dict]]:
    """Chat using Ollama API (no tool support, simpler mode)."""
    ollama_url = config.OLLAMA_URL or "http://localhost:11434"

    # Build simple message list for Ollama
    ollama_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in messages:
        if isinstance(msg.get("content"), str):
            ollama_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{ollama_url}/api/chat",
            json={
                "model": model,
                "messages": ollama_messages,
                "stream": False
            }
        )
        response.raise_for_status()
        data = response.json()

    text_response = data.get("message", {}).get("content", "")
    return text_response, []  # Ollama doesn't support tools


# ==================== API Endpoints ====================

@router.get("/providers")
async def get_providers():
    """Get available providers and their status."""
    result = []

    for provider_id, provider_info in PROVIDERS.items():
        available = True
        status = "available"

        if provider_id == "anthropic":
            if not ANTHROPIC_AVAILABLE:
                available = False
                status = "package not installed"
            elif not config.ANTHROPIC_API_KEY:
                available = False
                status = "API key not configured"

        elif provider_id == "openai":
            if not OPENAI_AVAILABLE:
                available = False
                status = "package not installed"
            elif not getattr(config, 'OPENAI_API_KEY', None):
                available = False
                status = "API key not configured"

        elif provider_id == "ollama":
            # Check if Ollama is running
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    resp = await client.get(f"{config.OLLAMA_URL or 'http://localhost:11434'}/api/tags")
                    if resp.status_code != 200:
                        available = False
                        status = "Ollama not responding"
            except Exception:
                available = False
                status = "Ollama not reachable"

        result.append({
            "id": provider_id,
            "name": provider_info["name"],
            "models": provider_info["models"],
            "default_model": provider_info["default_model"],
            "available": available,
            "status": status
        })

    # Get current selection
    current = load_provider_config()

    return {
        "success": True,
        "providers": result,
        "current": current
    }


@router.put("/providers")
async def set_provider(config_data: ProviderConfig):
    """Set the active provider and model."""
    if config_data.provider not in PROVIDERS:
        return {"success": False, "error": f"Unknown provider: {config_data.provider}"}

    save_provider_config(config_data.provider, config_data.model)
    return {"success": True, "provider": config_data.provider, "model": config_data.model}


@router.post("/chat")
async def chat(request: ChatRequest):
    """Send a message and get AI response."""
    try:
        # Get provider config
        provider_config = load_provider_config()
        provider_id = request.provider or provider_config.get("provider", "anthropic")
        model = provider_config.get("model") or PROVIDERS[provider_id]["default_model"]

        # Load chat history
        conn = await get_db()
        cursor = await conn.execute("""
            SELECT role, content FROM supervisor_chat_history
            ORDER BY created_at ASC
            LIMIT 50
        """)
        history = [{"role": r[0], "content": r[1]} for r in await cursor.fetchall()]

        # Add new user message
        history.append({"role": "user", "content": request.message})

        # Save user message
        await conn.execute(
            "INSERT INTO supervisor_chat_history (role, content) VALUES (?, ?)",
            ("user", request.message)
        )
        await conn.commit()

        # Call the appropriate provider
        if provider_id == "anthropic":
            text_response, tool_calls_made = await chat_with_anthropic(history, model)
        elif provider_id == "openai":
            text_response, tool_calls_made = await chat_with_openai(history, model)
        elif provider_id == "ollama":
            text_response, tool_calls_made = await chat_with_ollama(history, model)
        else:
            raise Exception(f"Unknown provider: {provider_id}")

        # Save assistant response
        await conn.execute(
            "INSERT INTO supervisor_chat_history (role, content, tool_calls) VALUES (?, ?, ?)",
            ("assistant", text_response, json.dumps(tool_calls_made) if tool_calls_made else None)
        )
        await conn.commit()
        await conn.close()

        return {
            "success": True,
            "response": text_response,
            "tool_calls": tool_calls_made,
            "provider": provider_id,
            "model": model
        }

    except Exception as e:
        logger.error(f"Supervisor chat error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@router.get("/history")
async def get_history():
    """Get chat history."""
    try:
        conn = await get_db()
        cursor = await conn.execute("""
            SELECT id, role, content, tool_calls, created_at
            FROM supervisor_chat_history
            ORDER BY created_at ASC
        """)
        history = [
            {
                "id": r[0],
                "role": r[1],
                "content": r[2],
                "tool_calls": json.loads(r[3]) if r[3] else None,
                "created_at": r[4]
            }
            for r in await cursor.fetchall()
        ]
        await conn.close()

        return {"success": True, "history": history}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/history")
async def clear_history():
    """Clear chat history."""
    try:
        conn = await get_db()
        await conn.execute("DELETE FROM supervisor_chat_history")
        await conn.commit()
        await conn.close()

        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
