"""
Response Evaluator - Evaluates bot responses for quality.

Checks for:
- Tone of voice (friendly, professional)
- Relevance to question
- Grammar and clarity
- Appropriate length
- Sales/engagement effectiveness
"""

import json
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    """Result of evaluating a bot response."""
    score: float  # 1-10
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    explanation: str = ""


class ResponseEvaluator:
    """
    Evaluates bot responses for quality and appropriateness.

    Criteria:
    1. Tone - friendly, professional, not robotic
    2. Relevance - answers the question/addresses concerns
    3. Clarity - easy to understand
    4. Length - appropriate for messenger
    5. Engagement - moves conversation forward
    6. No hallucinations - doesn't make up facts
    """

    def __init__(self, llm_client):
        """
        Args:
            llm_client: UnifiedLLMClient instance
        """
        self.llm = llm_client

    async def evaluate(
        self,
        client_message: str,
        bot_response: str,
        conversation_history: List[Dict[str, str]],
        scenario: str = "",
    ) -> EvaluationResult:
        """
        Evaluate a bot response.

        Args:
            client_message: What the client said
            bot_response: What the bot responded
            conversation_history: Previous messages
            scenario: Context about the client/situation

        Returns:
            EvaluationResult with score and feedback
        """
        system_prompt = """Ты эксперт по оценке качества переписок в бизнесе.

Твоя задача - оценить ответ бота/менеджера клиенту по шкале от 1 до 10.

КРИТЕРИИ ОЦЕНКИ:

1. ТОН (1-10):
   - Дружелюбный, но профессиональный
   - Не слишком формальный и не слишком панибратский
   - Не роботизированный
   - Эмпатичный

2. РЕЛЕВАНТНОСТЬ (1-10):
   - Отвечает на вопрос клиента
   - Не уходит от темы
   - Учитывает контекст разговора

3. ЯСНОСТЬ (1-10):
   - Понятный текст
   - Без сложных терминов
   - Правильная грамматика

4. ДЛИНА (1-10):
   - Не слишком короткий (отписка)
   - Не слишком длинный (простыня)
   - Подходит для мессенджера

5. ВОВЛЕЧЁННОСТЬ (1-10):
   - Продвигает разговор вперёд
   - Задаёт вопросы где уместно
   - Проявляет интерес к клиенту

ФОРМАТ ОТВЕТА (строго JSON):
{
    "score": 7,
    "issues": ["проблема 1", "проблема 2"],
    "suggestions": ["как улучшить 1", "как улучшить 2"],
    "explanation": "краткое объяснение оценки"
}

ВАЖНО:
- Будь строгим, но справедливым
- Указывай конкретные проблемы
- Предлагай конкретные улучшения
- Отвечай ТОЛЬКО JSON, без markdown"""

        # Build context
        history_text = ""
        if conversation_history:
            history_text = "\n".join([
                f"{'Клиент' if m['role'] == 'user' else 'Бот'}: {m['content']}"
                for m in conversation_history[-6:]  # Last 6 messages
            ])

        user_prompt = f"""КОНТЕКСТ СЦЕНАРИЯ:
{scenario if scenario else "Обычный клиент"}

ИСТОРИЯ ПЕРЕПИСКИ:
{history_text if history_text else "(начало разговора)"}

ТЕКУЩЕЕ СООБЩЕНИЕ КЛИЕНТА:
{client_message}

ОТВЕТ БОТА (ОЦЕНИ ЕГО):
{bot_response}

Оцени ответ бота. Ответь строго в формате JSON."""

        try:
            response = await self.llm.achat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,  # More consistent evaluations
                max_tokens=500,
            )

            # Parse JSON response
            response = response.strip()

            # Try to extract JSON if wrapped in markdown
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                response = response.split("```")[1].split("```")[0]

            data = json.loads(response)

            return EvaluationResult(
                score=float(data.get("score", 5)),
                issues=data.get("issues", []),
                suggestions=data.get("suggestions", []),
                explanation=data.get("explanation", ""),
            )

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse evaluation JSON: {e}")
            logger.debug(f"Raw response: {response}")
            return EvaluationResult(
                score=5,
                issues=["Failed to parse evaluation"],
                explanation=response[:200] if response else "No response",
            )
        except Exception as e:
            logger.error(f"Error evaluating response: {e}")
            return EvaluationResult(
                score=5,
                issues=[f"Evaluation error: {str(e)}"],
            )
