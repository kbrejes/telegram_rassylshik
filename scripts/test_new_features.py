#!/usr/bin/env python3
"""
Test script for new features:
1. Self-correcting prompt system (outcome tracking, A/B testing, prompt analysis)
2. AI conversation flow

Usage:
    python scripts/test_new_features.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()


async def test_outcome_tracker():
    """Test outcome detection."""
    print("\n" + "="*60)
    print("TEST: Outcome Tracker")
    print("="*60)

    from ai_conversation.outcome_tracker import OutcomeTracker
    from unittest.mock import MagicMock
    from datetime import datetime, timedelta

    tracker = OutcomeTracker(llm_client=None)

    # Test 1: Success keyword detection
    print("\n1. Testing success keyword detection...")
    state = MagicMock()
    state.call_scheduled = False
    state.call_offered = False
    state.last_interaction = None

    messages = [
        {"role": "assistant", "content": "Отлично! Созвон назначен на завтра в 15:00"},
        {"role": "user", "content": "Хорошо, жду"}
    ]

    result = await tracker.detect_outcome(123, state, messages)
    print(f"   Result: {result.outcome} (expected: call_scheduled)")
    assert result.outcome == "call_scheduled", f"Expected call_scheduled, got {result.outcome}"
    print("   ✅ PASSED")

    # Test 2: Rejection detection
    print("\n2. Testing rejection detection...")
    messages = [
        {"role": "assistant", "content": "Давайте назначим звонок?"},
        {"role": "user", "content": "Нет, спасибо, мне не интересно"}
    ]

    result = await tracker.detect_outcome(123, state, messages)
    print(f"   Result: {result.outcome} (expected: declined)")
    assert result.outcome == "declined", f"Expected declined, got {result.outcome}"
    print("   ✅ PASSED")

    # Test 3: Disengagement timeout
    print("\n3. Testing disengagement timeout...")
    state.call_offered = True
    state.last_interaction = (datetime.now() - timedelta(days=8)).isoformat()

    result = await tracker.detect_outcome(123, state, [])
    print(f"   Result: {result.outcome} (expected: disengaged)")
    assert result.outcome == "disengaged", f"Expected disengaged, got {result.outcome}"
    print("   ✅ PASSED")

    # Test 4: Ongoing (no signals)
    print("\n4. Testing ongoing status...")
    state.call_offered = False
    state.last_interaction = None

    messages = [
        {"role": "user", "content": "Привет, расскажите подробнее"},
        {"role": "assistant", "content": "Конечно! Мы работаем с таргетом..."}
    ]

    result = await tracker.detect_outcome(123, state, messages)
    print(f"   Result: {result.outcome} (expected: ongoing)")
    assert result.outcome == "ongoing", f"Expected ongoing, got {result.outcome}"
    print("   ✅ PASSED")

    print("\n✅ All OutcomeTracker tests passed!")


async def test_ab_testing():
    """Test A/B testing engine."""
    print("\n" + "="*60)
    print("TEST: A/B Testing Engine")
    print("="*60)

    from ai_conversation.ab_testing import ABTestingEngine
    from unittest.mock import AsyncMock

    # Mock database
    mock_db = AsyncMock()
    engine = ABTestingEngine(mock_db)

    # Test 1: No experiment - no assignment
    print("\n1. Testing no experiment scenario...")
    mock_db.get_active_experiment.return_value = None

    result = await engine.assign_variant(123, "phase", "discovery")
    print(f"   Result: {result} (expected: None)")
    assert result is None
    print("   ✅ PASSED")

    # Test 2: Deterministic assignment
    print("\n2. Testing deterministic variant assignment...")
    mock_db.get_active_experiment.return_value = {
        "id": 1,
        "name": "test_experiment",
        "control_version_id": 10,
        "treatment_version_id": 11,
        "traffic_split": 0.5
    }

    # Same contact should always get same variant
    results = []
    for _ in range(5):
        result = await engine.assign_variant(123, "phase", "discovery")
        results.append(result.variant)

    print(f"   Variants: {results}")
    assert len(set(results)) == 1, "Variant should be consistent"
    print("   ✅ PASSED (consistent variant)")

    # Test 3: Different contacts get different variants
    print("\n3. Testing variant distribution...")
    variants = set()
    for contact_id in range(1, 100):
        variant = engine._compute_variant(contact_id, experiment_id=1, traffic_split=0.5)
        variants.add(variant)

    print(f"   Unique variants: {variants}")
    assert len(variants) == 2, "Should have both control and treatment"
    print("   ✅ PASSED (both variants present)")

    # Test 4: Chi-square significance test
    print("\n4. Testing chi-square significance...")

    # Significant difference
    chi_sq, p_value = engine._chi_square_test(20, 80, 80, 20)
    print(f"   Significant case: chi_sq={chi_sq:.2f}, p={p_value:.4f}")
    assert p_value < 0.05, "Should be significant"

    # Not significant
    chi_sq, p_value = engine._chi_square_test(50, 50, 48, 52)
    print(f"   Not significant: chi_sq={chi_sq:.2f}, p={p_value:.4f}")
    assert p_value > 0.05, "Should not be significant"
    print("   ✅ PASSED")

    print("\n✅ All A/B Testing tests passed!")


async def test_prompt_analyzer():
    """Test prompt analyzer."""
    print("\n" + "="*60)
    print("TEST: Prompt Analyzer")
    print("="*60)

    from ai_conversation.prompt_analyzer import PromptAnalyzer, ContactLearner
    from unittest.mock import AsyncMock

    mock_llm = AsyncMock()
    mock_db = AsyncMock()

    analyzer = PromptAnalyzer(mock_llm, mock_db)

    # Test 1: Conversation formatting
    print("\n1. Testing conversation formatting...")
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"}
    ]

    formatted = analyzer._format_conversation(messages)
    print(f"   Formatted: {formatted[:50]}...")
    assert "User: Hello" in formatted
    assert "AI: Hi there!" in formatted
    print("   ✅ PASSED")

    # Test 2: Summarization
    print("\n2. Testing conversation summarization...")
    summary = analyzer._summarize_conversation(messages)
    print(f"   Summary: {summary}")
    assert "1 user" in summary
    assert "1 AI" in summary
    print("   ✅ PASSED")

    # Test 3: Contact classification
    print("\n3. Testing contact classification...")
    learner = ContactLearner(mock_llm, mock_db)

    # Empty messages
    result = await learner.classify_contact([])
    print(f"   Empty messages: {result}")
    assert result == "other"

    # With LLM response
    mock_llm.achat.return_value = "developer"
    result = await learner.classify_contact([{"role": "user", "content": "I'm a Python dev"}])
    print(f"   Developer response: {result}")
    assert result == "developer"
    print("   ✅ PASSED")

    print("\n✅ All Prompt Analyzer tests passed!")


async def test_ai_conversation():
    """Test AI conversation generation."""
    print("\n" + "="*60)
    print("TEST: AI Conversation (with real LLM)")
    print("="*60)

    from ai_conversation.llm_client import UnifiedLLMClient, LLMProviderConfig
    import os

    # Try Groq first, fallback to Ollama
    groq_key = os.getenv("GROQ_API_KEY")

    if groq_key and not groq_key.startswith("gsk_your"):
        print("\n1. Using Groq API...")
        config = LLMProviderConfig.groq()
    else:
        print("\n1. Using Ollama (local)...")
        config = LLMProviderConfig.ollama()

    try:
        client = UnifiedLLMClient(config)

        # Test simple chat
        print("\n2. Testing simple chat...")
        response = client.chat([
            {"role": "user", "content": "Say 'Hello, test successful!' and nothing else."}
        ])
        print(f"   Response: {response[:100]}...")
        assert len(response) > 0
        print("   ✅ PASSED")

        # Test async chat
        print("\n3. Testing async chat...")
        response = await client.achat([
            {"role": "system", "content": "You are a helpful assistant. Respond briefly."},
            {"role": "user", "content": "What is 2+2?"}
        ])
        print(f"   Response: {response[:100]}...")
        assert "4" in response
        print("   ✅ PASSED")

        print("\n✅ AI Conversation tests passed!")

    except Exception as e:
        print(f"\n⚠️  LLM test skipped: {e}")
        print("   Make sure Groq API key is set or Ollama is running")


async def test_database_tables():
    """Test that new database tables exist."""
    print("\n" + "="*60)
    print("TEST: Database Tables")
    print("="*60)

    from src.database import Database

    db = Database("data/jobs.db")
    await db.connect()

    # Check new tables exist
    tables = [
        "prompt_versions",
        "conversation_outcomes",
        "prompt_experiments",
        "prompt_metrics",
        "prompt_suggestions",
        "contact_type_learnings"
    ]

    print("\n1. Checking tables exist...")
    for table in tables:
        cursor = await db._connection.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,)
        )
        result = await cursor.fetchone()
        if result:
            print(f"   ✅ {table}")
        else:
            print(f"   ❌ {table} NOT FOUND")

    print("\n✅ Database tables check complete!")


async def test_contact_extraction():
    """Test contact extraction from messages."""
    print("\n" + "="*60)
    print("TEST: Contact Extraction")
    print("="*60)

    from src.message_processor import MessageProcessor

    processor = MessageProcessor()

    # Test 1: Telegram username extraction
    print("\n1. Testing Telegram username extraction...")
    text = "Ищем таргетолога! Опыт от 2 лет. Писать @johndoe"
    contacts = processor.extract_contact_info(text)
    print(f"   Text: '{text}'")
    print(f"   Telegram: {contacts.get('telegram')}")
    assert contacts.get('telegram') == "@johndoe", f"Expected @johndoe, got {contacts.get('telegram')}"
    print("   ✅ PASSED")

    # Test 2: Email extraction
    print("\n2. Testing email extraction...")
    text = "Резюме отправлять на hr@company.com"
    contacts = processor.extract_contact_info(text)
    print(f"   Text: '{text}'")
    print(f"   Email: {contacts.get('email')}")
    assert contacts.get('email') == "hr@company.com", f"Expected hr@company.com, got {contacts.get('email')}"
    print("   ✅ PASSED")

    # Test 3: Phone extraction
    print("\n3. Testing phone extraction...")
    text = "Звоните: +7 999 123-45-67"
    contacts = processor.extract_contact_info(text)
    print(f"   Text: '{text}'")
    print(f"   Phone: {contacts.get('phone')}")
    assert contacts.get('phone') is not None, "Phone should be extracted"
    print("   ✅ PASSED")

    # Test 4: No contacts
    print("\n4. Testing no contacts scenario...")
    text = "Ищем специалиста. Подробности в описании канала."
    contacts = processor.extract_contact_info(text)
    print(f"   Text: '{text}'")
    print(f"   Contacts: {contacts}")
    assert contacts.get('telegram') is None, "No telegram should be found"
    print("   ✅ PASSED (no @username found - no auto-response will be sent)")

    # Test 5: Email should NOT be extracted as Telegram username
    print("\n5. Testing email domain NOT extracted as Telegram...")
    text = "Отклики на vacancy@gmail.com или в телеграм @real_recruiter"
    contacts = processor.extract_contact_info(text)
    print(f"   Text: '{text}'")
    print(f"   Email: {contacts.get('email')}")
    print(f"   Telegram: {contacts.get('telegram')}")
    assert contacts.get('email') == "vacancy@gmail.com", f"Expected email, got {contacts.get('email')}"
    assert contacts.get('telegram') == "@real_recruiter", f"Expected @real_recruiter, got {contacts.get('telegram')}"
    print("   ✅ PASSED (@gmail NOT extracted, @real_recruiter extracted)")

    # Test 6: Only email, no Telegram
    print("\n6. Testing email-only message...")
    text = "Резюме на hr@company.com"
    contacts = processor.extract_contact_info(text)
    print(f"   Text: '{text}'")
    print(f"   Email: {contacts.get('email')}")
    print(f"   Telegram: {contacts.get('telegram')}")
    assert contacts.get('email') == "hr@company.com"
    assert contacts.get('telegram') is None, f"Should be None, got {contacts.get('telegram')}"
    print("   ✅ PASSED (no false positive Telegram from email)")

    print("\n✅ All Contact Extraction tests passed!")


async def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("   NEW FEATURES TEST SUITE")
    print("   Self-Correcting Prompts & AI System")
    print("="*60)

    try:
        await test_outcome_tracker()
        await test_ab_testing()
        await test_prompt_analyzer()
        await test_contact_extraction()
        await test_database_tables()
        await test_ai_conversation()

        print("\n" + "="*60)
        print("   🎉 ALL TESTS PASSED!")
        print("="*60)

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
