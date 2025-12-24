"""
Prompt Analyzer - LLM-based analysis of failed conversations.

Analyzes failed conversations to:
- Identify why the conversation failed
- Suggest specific prompt improvements
- Track patterns across failures
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ai_conversation.llm_client import UnifiedLLMClient
    from src.database import Database

logger = logging.getLogger(__name__)


@dataclass
class PromptSuggestion:
    """A suggested improvement to a prompt."""
    prompt_type: str
    prompt_name: str
    current_content: str
    suggested_content: str
    reasoning: str
    confidence_score: float
    failure_patterns: List[str]


@dataclass
class FailureAnalysis:
    """Analysis of a failed conversation."""
    contact_id: int
    failure_type: str  # 'declined', 'disengaged'
    failure_reason: str
    conversation_summary: str
    identified_issues: List[str]
    prompt_version_id: Optional[int]


class PromptAnalyzer:
    """
    Analyzes failed conversations and generates prompt improvements.

    Usage:
        analyzer = PromptAnalyzer(llm_client, database)
        suggestions = await analyzer.analyze_failures(limit=10)
    """

    # Minimum failures before analyzing
    MIN_FAILURES_FOR_ANALYSIS = 5

    def __init__(self, llm: "UnifiedLLMClient", db: "Database"):
        """
        Initialize prompt analyzer.

        Args:
            llm: LLM client for analysis
            db: Database for storing suggestions
        """
        self.llm = llm
        self.db = db

    async def analyze_single_failure(
        self,
        messages: List[Dict[str, str]],
        outcome: str,
        outcome_details: Dict[str, Any],
        contact_type: Optional[str] = None
    ) -> FailureAnalysis:
        """
        Analyze a single failed conversation.

        Args:
            messages: List of conversation messages
            outcome: Outcome type ('declined', 'disengaged')
            outcome_details: Details about the outcome
            contact_type: Optional contact classification

        Returns:
            FailureAnalysis with identified issues
        """
        # Build conversation text for analysis
        conv_text = self._format_conversation(messages)

        prompt = f"""Analyze this failed sales conversation.

Outcome: {outcome}
{f"Contact type: {contact_type}" if contact_type else ""}
{f"Details: {outcome_details}" if outcome_details else ""}

Conversation:
{conv_text}

Identify:
1. The exact point where the conversation went wrong
2. Specific issues with the AI's messages (tone, timing, content)
3. What could have been done differently

Return ONLY valid JSON:
{{
    "failure_reason": "one sentence explanation",
    "conversation_summary": "brief summary of what happened",
    "issues": [
        "specific issue 1",
        "specific issue 2"
    ],
    "turning_point": "message number or content where it went wrong"
}}"""

        try:
            response = await self.llm.achat([
                {"role": "system", "content": "You analyze sales conversations. Be specific and actionable."},
                {"role": "user", "content": prompt}
            ])

            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                return FailureAnalysis(
                    contact_id=0,  # Will be set by caller
                    failure_type=outcome,
                    failure_reason=data.get("failure_reason", "Unknown"),
                    conversation_summary=data.get("conversation_summary", ""),
                    identified_issues=data.get("issues", []),
                    prompt_version_id=None
                )
        except Exception as e:
            logger.warning(f"[PromptAnalyzer] Single failure analysis failed: {e}")

        return FailureAnalysis(
            contact_id=0,
            failure_type=outcome,
            failure_reason="Analysis failed",
            conversation_summary="",
            identified_issues=[],
            prompt_version_id=None
        )

    async def analyze_failure_patterns(
        self,
        failures: List[Dict[str, Any]]
    ) -> List[str]:
        """
        Analyze multiple failures to identify common patterns.

        Args:
            failures: List of failure records with messages and outcomes

        Returns:
            List of identified common patterns
        """
        if len(failures) < self.MIN_FAILURES_FOR_ANALYSIS:
            return []

        # Build summary of failures
        failure_summaries = []
        for i, failure in enumerate(failures[:20]):  # Limit to 20 for context
            messages = failure.get("messages", [])
            outcome = failure.get("outcome", "unknown")
            summary = self._summarize_conversation(messages)
            failure_summaries.append(f"{i+1}. [{outcome}] {summary}")

        prompt = f"""Analyze these {len(failure_summaries)} failed conversations and identify common patterns.

Failures:
{chr(10).join(failure_summaries)}

Identify:
1. Common reasons for failure
2. Patterns in AI behavior that led to failures
3. Timing issues (too aggressive, too passive)
4. Content issues (wrong topics, missing info)

Return ONLY valid JSON:
{{
    "patterns": [
        "pattern 1 description",
        "pattern 2 description"
    ],
    "most_common_issue": "the most frequent problem",
    "recommendations": [
        "recommendation 1",
        "recommendation 2"
    ]
}}"""

        try:
            response = await self.llm.achat([
                {"role": "system", "content": "You analyze patterns in sales conversations. Be specific."},
                {"role": "user", "content": prompt}
            ])

            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                return data.get("patterns", [])
        except Exception as e:
            logger.warning(f"[PromptAnalyzer] Pattern analysis failed: {e}")

        return []

    async def generate_prompt_suggestion(
        self,
        prompt_type: str,
        prompt_name: str,
        current_content: str,
        failure_patterns: List[str],
        example_failures: List[Dict[str, Any]]
    ) -> Optional[PromptSuggestion]:
        """
        Generate a suggested improvement for a prompt.

        Args:
            prompt_type: Type of prompt ('phase', 'base_context')
            prompt_name: Name of the prompt
            current_content: Current prompt content
            failure_patterns: Identified failure patterns
            example_failures: Example failed conversations

        Returns:
            PromptSuggestion if improvement is possible
        """
        # Format example failures
        examples_text = ""
        for i, failure in enumerate(example_failures[:3]):
            conv = self._format_conversation(failure.get("messages", []))
            examples_text += f"\nExample {i+1} ({failure.get('outcome', 'failed')}):\n{conv}\n"

        prompt = f"""You are improving an AI sales prompt based on failure analysis.

PROMPT TYPE: {prompt_type}
PROMPT NAME: {prompt_name}

CURRENT PROMPT:
{current_content}

IDENTIFIED FAILURE PATTERNS:
{chr(10).join(f"- {p}" for p in failure_patterns)}

EXAMPLE FAILED CONVERSATIONS:
{examples_text}

Your task:
1. Analyze how the current prompt contributed to failures
2. Create an improved version that addresses the issues
3. Keep the core intent but fix the problems

Return ONLY valid JSON:
{{
    "analysis": "how the current prompt causes issues",
    "changes": [
        "specific change 1",
        "specific change 2"
    ],
    "improved_prompt": "the full improved prompt text",
    "confidence": 0.0 to 1.0,
    "reasoning": "why this improvement will help"
}}"""

        try:
            response = await self.llm.achat([
                {"role": "system", "content": "You improve AI prompts based on failure analysis. Make targeted, specific improvements."},
                {"role": "user", "content": prompt}
            ])

            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                improved = data.get("improved_prompt", "")
                confidence = data.get("confidence", 0)

                if improved and confidence >= 0.5:
                    return PromptSuggestion(
                        prompt_type=prompt_type,
                        prompt_name=prompt_name,
                        current_content=current_content,
                        suggested_content=improved,
                        reasoning=data.get("reasoning", ""),
                        confidence_score=confidence,
                        failure_patterns=failure_patterns
                    )
        except Exception as e:
            logger.warning(f"[PromptAnalyzer] Prompt suggestion failed: {e}")

        return None

    async def save_suggestion(self, suggestion: PromptSuggestion, prompt_version_id: int) -> int:
        """
        Save a suggestion to the database.

        Args:
            suggestion: The suggestion to save
            prompt_version_id: ID of the current prompt version

        Returns:
            ID of the saved suggestion
        """
        return await self.db.create_prompt_suggestion(
            prompt_version_id=prompt_version_id,
            suggested_content=suggestion.suggested_content,
            reasoning=suggestion.reasoning,
            confidence_score=suggestion.confidence_score
        )

    async def get_pending_suggestions(self) -> List[Dict]:
        """
        Get all pending suggestions awaiting approval.

        Returns:
            List of pending suggestions
        """
        return await self.db.get_pending_suggestions()

    async def approve_suggestion(self, suggestion_id: int) -> Optional[int]:
        """
        Approve a suggestion and create a new prompt version.

        Args:
            suggestion_id: ID of the suggestion to approve

        Returns:
            ID of the new prompt version, or None if failed
        """
        return await self.db.approve_suggestion(suggestion_id)

    def _format_conversation(self, messages: List[Dict[str, str]], max_messages: int = 10) -> str:
        """Format conversation messages for analysis."""
        formatted = []
        for msg in messages[-max_messages:]:
            role = "User" if msg.get("role") == "user" else "AI"
            content = msg.get("content", "")[:200]  # Truncate long messages
            formatted.append(f"{role}: {content}")
        return "\n".join(formatted)

    def _summarize_conversation(self, messages: List[Dict[str, str]]) -> str:
        """Create a brief summary of a conversation."""
        if not messages:
            return "Empty conversation"

        user_count = sum(1 for m in messages if m.get("role") == "user")
        ai_count = len(messages) - user_count

        first_user = next(
            (m["content"][:50] for m in messages if m.get("role") == "user"),
            "No user message"
        )
        last_msg = messages[-1].get("content", "")[:50] if messages else ""

        return f"{user_count} user msgs, {ai_count} AI msgs. Started: '{first_user}...' Ended: '{last_msg}...'"


class ContactLearner:
    """
    Learns patterns specific to contact types (developer, hr, founder, etc.).

    Usage:
        learner = ContactLearner(llm_client, database)
        contact_type = await learner.classify_contact(messages)
        insights = await learner.get_insights_for_type(contact_type)
    """

    CONTACT_TYPES = ["developer", "hr", "founder", "manager", "recruiter", "other"]

    def __init__(self, llm: "UnifiedLLMClient", db: "Database"):
        """
        Initialize contact learner.

        Args:
            llm: LLM client for classification
            db: Database for storing learnings
        """
        self.llm = llm
        self.db = db

    async def classify_contact(self, messages: List[Dict[str, str]]) -> str:
        """
        Classify the contact based on their messages.

        Args:
            messages: Conversation messages

        Returns:
            Contact type classification
        """
        # Get user messages only
        user_messages = [m["content"] for m in messages if m.get("role") == "user"]
        if not user_messages:
            return "other"

        prompt = f"""Classify this person based on their messages.

Messages:
{chr(10).join(f"- {msg[:200]}" for msg in user_messages[-5:])}

Categories:
- developer: Software engineer, programmer, tech person
- hr: Human resources, people operations
- founder: CEO, founder, co-founder, business owner
- manager: Team lead, project manager, department head
- recruiter: External recruiter, headhunter
- other: Cannot determine

Return ONLY the category name (one word)."""

        try:
            response = await self.llm.achat([
                {"role": "system", "content": "You classify contacts. Return only the category name."},
                {"role": "user", "content": prompt}
            ])

            # Extract single word response
            contact_type = response.strip().lower().split()[0]
            if contact_type in self.CONTACT_TYPES:
                return contact_type
        except Exception as e:
            logger.warning(f"[ContactLearner] Classification failed: {e}")

        return "other"

    async def learn_from_outcome(
        self,
        contact_type: str,
        outcome: str,
        messages: List[Dict[str, str]]
    ):
        """
        Learn from a conversation outcome for a specific contact type.

        Args:
            contact_type: Type of contact
            outcome: Conversation outcome
            messages: Conversation messages
        """
        if outcome not in ["call_scheduled", "declined"]:
            return  # Only learn from clear outcomes

        prompt = f"""Analyze this {'successful' if outcome == 'call_scheduled' else 'failed'} conversation with a {contact_type}.

Conversation:
{self._format_messages(messages)}

What {'worked well' if outcome == 'call_scheduled' else 'went wrong'} specifically for communicating with a {contact_type}?
Return a brief, specific insight (1-2 sentences)."""

        try:
            response = await self.llm.achat([
                {"role": "system", "content": "You extract communication insights. Be specific and actionable."},
                {"role": "user", "content": prompt}
            ])

            insight = response.strip()[:500]  # Limit length
            if insight:
                await self.db.add_contact_type_learning(
                    contact_type=contact_type,
                    learning=insight,
                    confidence_score=0.7 if outcome == "call_scheduled" else 0.5
                )
                logger.info(f"[ContactLearner] Learned for {contact_type}: {insight[:50]}...")

        except Exception as e:
            logger.warning(f"[ContactLearner] Learning failed: {e}")

    async def get_insights_for_type(self, contact_type: str) -> List[str]:
        """
        Get accumulated insights for a contact type.

        Args:
            contact_type: Type of contact

        Returns:
            List of insights
        """
        learnings = await self.db.get_contact_type_learnings(contact_type)
        return [l["learning"] for l in learnings]

    async def get_type_specific_prompt_additions(self, contact_type: str) -> str:
        """
        Generate prompt additions based on learnings for a contact type.

        Args:
            contact_type: Type of contact

        Returns:
            Additional prompt text
        """
        insights = await self.get_insights_for_type(contact_type)
        if not insights:
            return ""

        # Take top 3 insights
        top_insights = insights[:3]
        return f"\n\nSpecific guidance for {contact_type}:\n" + "\n".join(f"- {i}" for i in top_insights)

    def _format_messages(self, messages: List[Dict[str, str]]) -> str:
        """Format messages for analysis."""
        formatted = []
        for msg in messages[-10:]:
            role = "User" if msg.get("role") == "user" else "AI"
            formatted.append(f"{role}: {msg.get('content', '')[:200]}")
        return "\n".join(formatted)
