#!/usr/bin/env python3
"""
Conversation Tester - Automated AI conversation testing loop.

Uses Groq to simulate clients and evaluate bot responses.
Automatically identifies issues and can suggest prompt fixes.

Usage:
    python tests/conversation_tester.py [--scenario job_seeker] [--rounds 10]
"""

import asyncio
import json
import logging
import argparse
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_conversation.handler import AIConversationHandler, AIConfig
from ai_conversation.llm_client import UnifiedLLMClient
from tests.client_simulator import ClientSimulator
from tests.response_evaluator import ResponseEvaluator, EvaluationResult

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class ConversationTurn:
    """Single turn in conversation."""
    client_message: str
    bot_response: str
    evaluation: Optional[EvaluationResult] = None


@dataclass
class TestResult:
    """Result of a test conversation."""
    scenario: str
    turns: List[ConversationTurn] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    score: float = 0.0


class ConversationTester:
    """
    Main test harness for AI conversation testing.

    Loop:
    1. Client simulator sends message (based on scenario)
    2. Bot generates response
    3. Evaluator checks quality
    4. If issues found, log and optionally fix
    """

    def __init__(
        self,
        groq_api_key: str,
        groq_model: str = "llama-3.3-70b-versatile",
        bot_config: Optional[AIConfig] = None,
    ):
        self.groq_api_key = groq_api_key
        self.groq_model = groq_model

        # Bot under test
        self.bot_config = bot_config or AIConfig(
            llm_provider="groq",
            llm_model=groq_model,
            mode="auto",
            use_state_analyzer=True,
        )
        self.bot: Optional[AIConversationHandler] = None

        # Test components
        self.client_simulator: Optional[ClientSimulator] = None
        self.evaluator: Optional[ResponseEvaluator] = None

        # Results
        self.results: List[TestResult] = []

    async def initialize(self):
        """Initialize all components."""
        providers_config = {
            "groq": {
                "base_url": "https://api.groq.com/openai/v1",
                "api_key": self.groq_api_key,
                "default_model": self.groq_model,
            }
        }

        # Initialize bot
        self.bot = AIConversationHandler(
            config=self.bot_config,
            providers_config=providers_config,
            channel_id="test_channel",
        )
        await self.bot.initialize()

        # Initialize simulator and evaluator (share LLM client)
        llm_client = UnifiedLLMClient.from_config(
            provider_name="groq",
            model=self.groq_model,
            providers_config=providers_config,
        )

        self.client_simulator = ClientSimulator(llm_client)
        self.evaluator = ResponseEvaluator(llm_client)

        logger.info("ConversationTester initialized")

    async def run_conversation(
        self,
        scenario: str,
        max_turns: int = 10,
        contact_id: int = 999999,
    ) -> TestResult:
        """
        Run a single test conversation.

        Args:
            scenario: Scenario name (loads from tests/scenarios/{scenario}.txt)
            max_turns: Maximum conversation turns
            contact_id: Fake contact ID for this test
        """
        result = TestResult(scenario=scenario)

        # Load scenario
        scenario_path = Path(__file__).parent / "scenarios" / f"{scenario}.txt"
        if scenario_path.exists():
            scenario_prompt = scenario_path.read_text()
        else:
            scenario_prompt = f"You are a potential client interested in services. Scenario: {scenario}"

        # Initialize client simulator with scenario
        self.client_simulator.set_scenario(scenario_prompt)

        # Conversation history for context
        conversation_history: List[Dict[str, str]] = []

        logger.info(f"\n{'='*60}")
        logger.info(f"Starting test: {scenario}")
        logger.info(f"{'='*60}\n")

        for turn_num in range(max_turns):
            # 1. Client sends message
            client_message = await self.client_simulator.generate_message(
                conversation_history=conversation_history,
                turn_number=turn_num,
            )

            if not client_message or client_message.strip().lower() in ["[end]", "[конец]", ""]:
                logger.info(f"Client ended conversation at turn {turn_num}")
                break

            logger.info(f"\n--- Turn {turn_num + 1} ---")
            logger.info(f"CLIENT: {client_message}")

            # 2. Bot responds
            bot_response = await self.bot.handle_message(
                contact_id=contact_id,
                message=client_message,
                contact_name="Test Client",
            )

            if not bot_response:
                bot_response = "[No response]"

            logger.info(f"BOT: {bot_response}")

            # 3. Evaluate response
            evaluation = await self.evaluator.evaluate(
                client_message=client_message,
                bot_response=bot_response,
                conversation_history=conversation_history,
                scenario=scenario_prompt,
            )

            # Log evaluation
            if evaluation.score < 7:
                logger.warning(f"LOW SCORE ({evaluation.score}/10): {evaluation.issues}")
                result.issues.extend(evaluation.issues)
                if evaluation.suggestions:
                    result.suggestions.extend(evaluation.suggestions)
            else:
                logger.info(f"Score: {evaluation.score}/10")

            # Record turn
            turn = ConversationTurn(
                client_message=client_message,
                bot_response=bot_response,
                evaluation=evaluation,
            )
            result.turns.append(turn)

            # Update history
            conversation_history.append({"role": "user", "content": client_message})
            conversation_history.append({"role": "assistant", "content": bot_response})

            # Delay between turns to avoid rate limits
            await asyncio.sleep(2.0)

        # Calculate overall score
        if result.turns:
            scores = [t.evaluation.score for t in result.turns if t.evaluation]
            result.score = sum(scores) / len(scores) if scores else 0

        logger.info(f"\n{'='*60}")
        logger.info(f"Test completed: {scenario}")
        logger.info(f"Overall score: {result.score:.1f}/10")
        logger.info(f"Issues found: {len(result.issues)}")
        logger.info(f"{'='*60}\n")

        return result

    async def run_all_scenarios(self, max_turns: int = 10) -> List[TestResult]:
        """Run all available scenarios."""
        scenarios_dir = Path(__file__).parent / "scenarios"
        scenarios = [f.stem for f in scenarios_dir.glob("*.txt")]

        if not scenarios:
            logger.warning("No scenarios found. Creating default scenarios...")
            await self._create_default_scenarios()
            scenarios = [f.stem for f in scenarios_dir.glob("*.txt")]

        results = []
        for i, scenario in enumerate(scenarios):
            result = await self.run_conversation(
                scenario=scenario,
                max_turns=max_turns,
                contact_id=1000000 + i,
            )
            results.append(result)
            self.results.append(result)

        return results

    async def _create_default_scenarios(self):
        """Create default test scenarios."""
        scenarios_dir = Path(__file__).parent / "scenarios"
        scenarios_dir.mkdir(exist_ok=True)

        # Will be created by separate file
        pass

    def print_summary(self):
        """Print summary of all test results."""
        if not self.results:
            print("No results to summarize")
            return

        print("\n" + "="*60)
        print("TEST SUMMARY")
        print("="*60)

        total_score = 0
        all_issues = []
        all_suggestions = []

        for result in self.results:
            print(f"\n{result.scenario}:")
            print(f"  Score: {result.score:.1f}/10")
            print(f"  Turns: {len(result.turns)}")
            print(f"  Issues: {len(result.issues)}")

            total_score += result.score
            all_issues.extend(result.issues)
            all_suggestions.extend(result.suggestions)

        avg_score = total_score / len(self.results)

        print(f"\n{'='*60}")
        print(f"OVERALL: {avg_score:.1f}/10")
        print(f"Total issues: {len(all_issues)}")

        if all_issues:
            print(f"\nTop issues:")
            # Count unique issues
            from collections import Counter
            issue_counts = Counter(all_issues)
            for issue, count in issue_counts.most_common(5):
                print(f"  - {issue} (x{count})")

        if all_suggestions:
            print(f"\nSuggestions for improvement:")
            unique_suggestions = list(set(all_suggestions))[:5]
            for suggestion in unique_suggestions:
                print(f"  - {suggestion}")


async def main():
    parser = argparse.ArgumentParser(description="Test AI conversations")
    parser.add_argument("--scenario", type=str, help="Specific scenario to run")
    parser.add_argument("--rounds", type=int, default=10, help="Max conversation turns")
    parser.add_argument("--all", action="store_true", help="Run all scenarios")
    args = parser.parse_args()

    # Load config
    from dotenv import load_dotenv
    import os
    load_dotenv()

    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        print("ERROR: GROQ_API_KEY not found in .env")
        return

    tester = ConversationTester(groq_api_key=groq_api_key)
    await tester.initialize()

    if args.all:
        await tester.run_all_scenarios(max_turns=args.rounds)
    elif args.scenario:
        await tester.run_conversation(scenario=args.scenario, max_turns=args.rounds)
    else:
        # Default: run first available scenario or job_seeker
        await tester.run_conversation(scenario="job_seeker", max_turns=args.rounds)

    tester.print_summary()


if __name__ == "__main__":
    asyncio.run(main())
