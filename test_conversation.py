#!/usr/bin/env python3
"""
Non-interactive conversation test.
Simulates a conversation to verify AI behavior issues.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ai_conversation.handler import AIConversationHandler, AIConfig
from ai_conversation.memory import WorkingMemoryStorage
from src.config_manager import config_manager
from src.human_behavior import human_behavior


async def test_conversation():
    """Test conversation flow to identify issues."""

    human_behavior.disable()
    contact_id = 77777777

    # Load config
    config_manager.load()
    channels = config_manager.channels
    if not channels:
        print("No channels configured!")
        return

    channel = channels[0]
    print(f"Using channel: {channel.name}")

    # Clear previous test data
    storage = WorkingMemoryStorage()
    storage.clear(contact_id)

    # Create handler
    ch_ai = channel.ai_config
    ai_config = AIConfig(
        mode="auto",
        llm_provider=ch_ai.llm_provider,
        llm_model=ch_ai.llm_model,
        persona_file=ch_ai.persona_file,
        use_state_analyzer=True,
        prompts_dir="prompts",
        states_dir="data/conversation_states",
        context_window_messages=ch_ai.context_window_messages,
        use_weaviate=False,  # Disable for test
    )

    handler = AIConversationHandler(
        config=ai_config,
        providers_config=config_manager.llm_providers,
        channel_id=channel.id,
    )

    print("Initializing...")
    await handler.initialize()
    print("Ready!\n")
    print("=" * 60)

    # Test messages - simulating a real conversation
    test_messages = [
        "привет, ищу таргетолога в команду",
        "какой у вас опыт?",
        "а сколько стоит?",
        ".",  # Bot test
        "интересно, расскажите подробнее",
        "ты бот?",
    ]

    for i, msg in enumerate(test_messages, 1):
        print(f"\n[USER #{i}]: {msg}")
        print("-" * 40)

        response = await handler._generate_with_state_analyzer(
            contact_id=contact_id,
            message=msg,
        )

        print(f"[AI #{i}]: {response}")

        # Show state
        state = handler.get_state(contact_id)
        if state:
            print(f"\n  Phase: {state.current_phase} | Total msgs: {state.total_messages} | Call offered: {state.call_offered}")

        print("=" * 60)

    # Show final working memory
    print("\n\nFINAL WORKING MEMORY:")
    print("-" * 40)
    messages = handler.memory.get_working_memory(contact_id)
    for i, m in enumerate(messages, 1):
        role = m.get('role', '?').upper()
        content = m.get('content', '')[:80]
        if len(m.get('content', '')) > 80:
            content += "..."
        print(f"{i}. [{role}] {content}")

    handler.close()
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(test_conversation())
