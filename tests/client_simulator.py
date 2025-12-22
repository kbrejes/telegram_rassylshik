"""
Client Simulator - Simulates realistic client behavior using LLM.

Generates messages based on scenario and conversation history.
"""

import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class ClientSimulator:
    """
    Simulates a client/customer in conversation.

    Uses LLM to generate realistic messages based on:
    - Scenario (personality, goals, context)
    - Conversation history
    - Turn number (for natural progression)
    """

    def __init__(self, llm_client):
        """
        Args:
            llm_client: UnifiedLLMClient instance
        """
        self.llm = llm_client
        self.scenario: str = ""

    def set_scenario(self, scenario: str):
        """Set the scenario/personality for this client."""
        self.scenario = scenario

    async def generate_message(
        self,
        conversation_history: List[Dict[str, str]],
        turn_number: int = 0,
    ) -> str:
        """
        Generate next client message.

        Args:
            conversation_history: List of {"role": "user/assistant", "content": "..."}
            turn_number: Current turn number (0-indexed)

        Returns:
            Generated client message
        """
        system_prompt = f"""Ты симулируешь клиента в переписке с компанией.

ТВОЯ РОЛЬ:
{self.scenario}

ПРАВИЛА:
1. Пиши ТОЛЬКО сообщение клиента, без пояснений и меток
2. Пиши на том же языке, что и собеседник (обычно русский)
3. Пиши как реальный человек в мессенджере - коротко, иногда с опечатками
4. Следуй своей роли и целям
5. Если разговор естественно завершился, напиши только: [END]
6. Не будь слишком вежливым или формальным - пиши как обычный человек

ТЕКУЩАЯ СИТУАЦИЯ:
- Это сообщение #{turn_number + 1} в диалоге
- {"Это начало разговора, напиши первое сообщение" if turn_number == 0 else "Продолжи разговор естественно"}
"""

        # Build messages for LLM
        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history (swap roles - we're the user now)
        for msg in conversation_history:
            if msg["role"] == "user":
                # Client messages become assistant (what we said before)
                messages.append({"role": "assistant", "content": msg["content"]})
            else:
                # Bot messages become user (what they said)
                messages.append({"role": "user", "content": msg["content"]})

        # If no history, add a prompt to start
        if not conversation_history:
            messages.append({
                "role": "user",
                "content": "Начни разговор как клиент. Напиши первое сообщение."
            })
        else:
            messages.append({
                "role": "user",
                "content": "Напиши следующее сообщение клиента в ответ на последнее сообщение компании."
            })

        try:
            response = await self.llm.achat(
                messages=messages,
                temperature=0.8,  # More creative/varied
                max_tokens=200,
            )

            # Clean up response
            response = response.strip()

            # Remove common LLM artifacts
            for prefix in ["Клиент:", "Client:", "Сообщение:", "Message:"]:
                if response.startswith(prefix):
                    response = response[len(prefix):].strip()

            return response

        except Exception as e:
            logger.error(f"Error generating client message: {e}")
            return ""
