#!/usr/bin/env python3
"""
Conversation Playground

Interactive testing environment for AI conversations.
Claude (or human) plays the contact, system AI responds.

Usage:
    python playground.py                    # Interactive mode
    python playground.py --contact 12345    # Use specific contact ID
    python playground.py --reset            # Clear working memory first
    python playground.py --show-memory      # Show memory after each turn
"""

import asyncio
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from ai_conversation.handler import AIConversationHandler, AIConfig
from ai_conversation.memory import WorkingMemoryStorage
from src.config_manager import config_manager


# ANSI colors for terminal output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'


def print_header():
    """Print playground header."""
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}   CONVERSATION PLAYGROUND{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.DIM}You are the CONTACT. AI will respond as the sales assistant.{Colors.ENDC}")
    print(f"{Colors.DIM}Commands: /quit, /reset, /memory, /state, /help{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'='*60}{Colors.ENDC}\n")


def print_message(role: str, content: str, turn: int = 0):
    """Print a conversation message with formatting."""
    if role == "contact":
        prefix = f"{Colors.GREEN}[YOU]{Colors.ENDC}"
        color = Colors.GREEN
    elif role == "ai":
        prefix = f"{Colors.BLUE}[AI]{Colors.ENDC}"
        color = Colors.BLUE
    else:
        prefix = f"{Colors.YELLOW}[{role.upper()}]{Colors.ENDC}"
        color = Colors.YELLOW

    turn_str = f"{Colors.DIM}#{turn}{Colors.ENDC} " if turn else ""
    print(f"\n{turn_str}{prefix} {color}{content}{Colors.ENDC}")


def print_memory(messages: list):
    """Print working memory contents."""
    print(f"\n{Colors.YELLOW}{'─'*40}{Colors.ENDC}")
    print(f"{Colors.YELLOW}WORKING MEMORY ({len(messages)} messages):{Colors.ENDC}")
    for i, msg in enumerate(messages):
        role = msg.get('role', '?')
        content = msg.get('content', '')[:100]
        if len(msg.get('content', '')) > 100:
            content += "..."
        print(f"  {Colors.DIM}{i+1}. [{role}]{Colors.ENDC} {content}")
    print(f"{Colors.YELLOW}{'─'*40}{Colors.ENDC}\n")


def print_state(state):
    """Print conversation state."""
    if not state:
        print(f"{Colors.DIM}No state available{Colors.ENDC}")
        return

    print(f"\n{Colors.CYAN}{'─'*40}{Colors.ENDC}")
    print(f"{Colors.CYAN}CONVERSATION STATE:{Colors.ENDC}")
    print(f"  Phase: {Colors.BOLD}{state.current_phase}{Colors.ENDC}")
    print(f"  Messages in phase: {state.messages_in_phase}")
    print(f"  Total messages: {state.total_messages}")
    print(f"  Call offered: {state.call_offered}")
    print(f"  Call declined: {state.call_declined}")
    print(f"  Call scheduled: {state.call_scheduled}")
    print(f"{Colors.CYAN}{'─'*40}{Colors.ENDC}\n")


async def run_playground(
    contact_id: int = 99999999,
    reset: bool = False,
    show_memory: bool = False,
    channel_id: str = None,
):
    """Run interactive playground."""
    print_header()

    # Load config and get channels
    config_manager.load()
    channels = config_manager.channels
    if not channels:
        print(f"{Colors.RED}No channels configured!{Colors.ENDC}")
        return

    # Use first channel or specified one
    channel = None
    if channel_id:
        channel = next((c for c in channels if c.id == channel_id), None)
    if not channel:
        channel = channels[0]

    print(f"{Colors.DIM}Using channel: {channel.name} ({channel.id}){Colors.ENDC}")
    print(f"{Colors.DIM}Contact ID: {contact_id}{Colors.ENDC}")

    # Reset working memory if requested
    if reset:
        storage = WorkingMemoryStorage()
        storage.clear(contact_id)
        print(f"{Colors.YELLOW}Working memory cleared.{Colors.ENDC}")

    # Create AI handler using channel's AI config
    ch_ai = channel.ai_config  # This is already an AIConfig dataclass
    ai_config = AIConfig(
        mode="auto",
        llm_provider=ch_ai.llm_provider,
        llm_model=ch_ai.llm_model,
        persona_file=ch_ai.persona_file,
        use_state_analyzer=True,
        prompts_dir="prompts",
        states_dir="data/conversation_states",
        context_window_messages=ch_ai.context_window_messages,
        use_weaviate=ch_ai.use_weaviate,
        weaviate_host=ch_ai.weaviate_host,
        weaviate_port=ch_ai.weaviate_port,
    )

    handler = AIConversationHandler(
        config=ai_config,
        providers_config=config_manager.llm_providers,
        channel_id=channel.id,
    )

    print(f"{Colors.DIM}Initializing AI handler...{Colors.ENDC}")
    await handler.initialize()
    print(f"{Colors.GREEN}Ready! Start typing as the contact.{Colors.ENDC}\n")

    turn = 0

    # Main interaction loop
    while True:
        try:
            # Get input
            user_input = input(f"{Colors.GREEN}You: {Colors.ENDC}").strip()

            if not user_input:
                continue

            # Handle commands
            if user_input.startswith('/'):
                cmd = user_input.lower()

                if cmd == '/quit' or cmd == '/q':
                    print(f"\n{Colors.YELLOW}Goodbye!{Colors.ENDC}")
                    break

                elif cmd == '/reset':
                    handler.memory.clear_working_memory(contact_id)
                    if handler.state_analyzer:
                        handler.reset_state(contact_id)
                    turn = 0
                    print(f"{Colors.YELLOW}Conversation reset.{Colors.ENDC}")
                    continue

                elif cmd == '/memory':
                    messages = handler.memory.get_working_memory(contact_id)
                    print_memory(messages)
                    continue

                elif cmd == '/state':
                    state = handler.get_state(contact_id)
                    print_state(state)
                    continue

                elif cmd == '/help':
                    print(f"""
{Colors.CYAN}Commands:{Colors.ENDC}
  /quit, /q   - Exit playground
  /reset      - Clear memory and state
  /memory     - Show working memory
  /state      - Show conversation state
  /help       - Show this help
""")
                    continue

                else:
                    print(f"{Colors.RED}Unknown command: {user_input}{Colors.ENDC}")
                    continue

            turn += 1
            print_message("contact", user_input, turn)

            # Generate AI response
            print(f"{Colors.DIM}Generating response...{Colors.ENDC}", end='\r')

            response = await handler._generate_with_state_analyzer(
                contact_id=contact_id,
                message=user_input,
            )

            if response:
                print_message("ai", response, turn)

                # Show state after response
                state = handler.get_state(contact_id)
                if state:
                    print(f"{Colors.DIM}Phase: {state.current_phase} | "
                          f"Msgs: {state.total_messages} | "
                          f"Call offered: {state.call_offered}{Colors.ENDC}")
            else:
                print(f"{Colors.RED}[No response generated]{Colors.ENDC}")

            # Show memory if requested
            if show_memory:
                messages = handler.memory.get_working_memory(contact_id)
                print_memory(messages)

        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Interrupted. Goodbye!{Colors.ENDC}")
            break
        except Exception as e:
            print(f"{Colors.RED}Error: {e}{Colors.ENDC}")
            import traceback
            traceback.print_exc()

    # Cleanup
    handler.close()


def main():
    parser = argparse.ArgumentParser(description="Conversation Playground")
    parser.add_argument("--contact", type=int, default=99999999,
                       help="Contact ID to use (default: 99999999)")
    parser.add_argument("--channel", type=str, default=None,
                       help="Channel ID to use")
    parser.add_argument("--reset", action="store_true",
                       help="Reset working memory before starting")
    parser.add_argument("--show-memory", action="store_true",
                       help="Show memory after each turn")

    args = parser.parse_args()

    asyncio.run(run_playground(
        contact_id=args.contact,
        reset=args.reset,
        show_memory=args.show_memory,
        channel_id=args.channel,
    ))


if __name__ == "__main__":
    main()
