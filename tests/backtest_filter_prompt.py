"""
Backtesting script for vacancy filtering prompts.

Loads historical vacancy messages and tests different prompts
to find the best one for extracting telegram contacts.
"""

import asyncio
import json
import sqlite3
import re
import os
import sys
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_conversation.llm_client import UnifiedLLMClient
from src.config_manager import ConfigManager


@dataclass
class VacancyRecord:
    """A vacancy record from the database."""
    id: int
    message_id: int
    chat_id: int
    chat_title: str
    message_text: str
    was_relevant: bool  # Original is_relevant value


@dataclass
class TestResult:
    """Result of testing a vacancy with a prompt."""
    vacancy_id: int
    chat_title: str
    has_personal_contact: bool
    extracted_contact: Optional[str]
    contact_type: str  # "user", "bot", "none"
    should_pass: bool  # Based on new criteria
    was_passing: bool  # Original is_relevant
    is_correct: bool  # should_pass matches expected behavior
    message_preview: str  # First 100 chars
    llm_response: Optional[str] = None


# Channel/group username patterns to filter out
CHANNEL_PATTERNS = [
    'channel', 'group', 'chat', 'news', 'bot',
    'jobs', 'job', 'vacancy', 'work', 'career', 'hire', 'hiring',
    'remote', 'junior', 'senior', 'dev', 'developer',
    'marketing', 'design', 'frontend', 'backend', 'smm', 'digital', 'pr',
    'chiefs', 'chief', 'team', 'community', 'official', 'freelance',
    'канал', 'группа', 'чат', 'новости', 'бот',
    'вакансии', 'вакансия', 'работа', 'карьера',
    'удаленка', 'удалёнка', 'джуниор', 'сеньор', 'фриланс',
]


def load_vacancies(db_path: str, limit: int = 100) -> List[VacancyRecord]:
    """Load vacancy records from database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, message_id, chat_id, chat_title, message_text, is_relevant
        FROM processed_jobs
        WHERE message_text IS NOT NULL AND message_text != ''
        ORDER BY processed_at DESC
        LIMIT ?
    """, (limit,))

    vacancies = []
    for row in cursor.fetchall():
        vacancies.append(VacancyRecord(
            id=row[0],
            message_id=row[1],
            chat_id=row[2],
            chat_title=row[3] or "Unknown",
            message_text=row[4],
            was_relevant=bool(row[5])
        ))

    conn.close()
    return vacancies


def extract_contact_regex(text: str) -> tuple[Optional[str], str]:
    """
    Extract telegram contact using regex.
    Returns (contact_username, contact_type).
    """
    # First check for bot usernames
    bot_patterns = [
        r't\.me/([a-zA-Z][a-zA-Z0-9_]{4,}[Bb][Oo][Tt])',
        r'@([a-zA-Z][a-zA-Z0-9_]*[Bb][Oo][Tt])\b',
    ]
    for pattern in bot_patterns:
        match = re.search(pattern, text)
        if match:
            return (f"@{match.group(1)}", "bot")

    # Find all @usernames
    mentions = re.findall(r'@([a-zA-Z][a-zA-Z0-9_]{4,31})', text)
    if not mentions:
        return (None, "none")

    # Filter out channel/group patterns
    for username in mentions:
        username_lower = username.lower()
        if not any(p in username_lower for p in CHANNEL_PATTERNS):
            return (f"@{username}", "user")

    return (None, "none")


def has_personal_telegram_contact(text: str) -> tuple[bool, Optional[str], str]:
    """
    Check if text has a personal telegram contact.
    Returns (has_contact, username, contact_type).
    """
    contact, contact_type = extract_contact_regex(text)

    if contact_type == "user":
        return (True, contact, "user")
    elif contact_type == "bot":
        # Bots don't count as personal contacts
        return (False, contact, "bot")
    else:
        return (False, None, "none")


# New simplified prompt - focused only on telegram contact extraction
NEW_SYSTEM_PROMPT = """You are a telegram contact extractor. Your ONLY task is to find a personal Telegram username (@username) in job postings.

RULES:
1. Look for @username of a PERSON (HR manager, recruiter, hiring manager) - someone you can write to apply for the job
2. The contact is usually near words: "писать", "резюме", "связь", "контакт", "HR", "обращаться", "откликнуться", "написать"

IGNORE and DO NOT return:
- Channel/group usernames containing: job, jobs, work, vacancy, career, remote, marketing, smm, digital, channel, chat, group, news, hire, hiring, freelance
- Bot usernames (ending with "bot" or "_bot")
- t.me/ links to channels or groups
- Any @username that doesn't look like a person's name

Personal usernames usually look like:
- Real names: @ivan_petrov, @anna_hr, @recruiter_kate, @maria_hiring
- Name + role: @hr_anna, @ceo_john, @pm_alex

If NO personal contact found, return null.
If MULTIPLE usernames found, pick the one that looks most like a person's real name.

Respond ONLY with valid JSON:
{"contact_username": "@username" or null, "reason": "brief explanation"}"""


NEW_USER_PROMPT_TEMPLATE = """Extract the personal Telegram contact from this job posting:

---
{text}
---

JSON response:"""


async def test_vacancy_with_llm(
    vacancy: VacancyRecord,
    llm: UnifiedLLMClient,
    system_prompt: str,
    user_prompt_template: str
) -> TestResult:
    """Test a single vacancy with the LLM prompt."""

    # Regex-based extraction (ground truth for comparison)
    has_contact, regex_contact, contact_type = has_personal_telegram_contact(vacancy.message_text)

    # LLM-based extraction
    llm_contact = None
    llm_response = None
    try:
        user_prompt = user_prompt_template.format(text=vacancy.message_text[:2000])
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await llm.achat(messages)
        llm_response = response

        # Parse JSON response
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            data = json.loads(json_match.group())
            llm_contact = data.get("contact_username")
            if llm_contact and not llm_contact.startswith("@"):
                llm_contact = f"@{llm_contact}"
    except Exception as e:
        llm_response = f"Error: {e}"

    # Determine if vacancy should pass (new criteria: has personal TG contact)
    # Use LLM result as primary, fallback to regex
    final_contact = llm_contact if llm_contact else regex_contact
    should_pass = final_contact is not None and contact_type != "bot"

    # For ground truth, we consider a vacancy "correct" if:
    # - It should pass AND it has a real personal contact in the text
    # - It should not pass AND it has no personal contact
    # We use regex as ground truth since it's deterministic

    return TestResult(
        vacancy_id=vacancy.id,
        chat_title=vacancy.chat_title,
        has_personal_contact=has_contact,
        extracted_contact=final_contact,
        contact_type=contact_type,
        should_pass=should_pass,
        was_passing=vacancy.was_relevant,
        is_correct=should_pass == has_contact,  # Compare LLM decision to regex ground truth
        message_preview=vacancy.message_text[:100].replace('\n', ' '),
        llm_response=llm_response,
    )


async def run_backtest(
    db_path: str,
    limit: int = 50,
    system_prompt: str = NEW_SYSTEM_PROMPT,
    user_prompt_template: str = NEW_USER_PROMPT_TEMPLATE,
) -> List[TestResult]:
    """Run backtest on historical vacancies."""

    print(f"Loading vacancies from {db_path}...")
    vacancies = load_vacancies(db_path, limit)
    print(f"Loaded {len(vacancies)} vacancies")

    # Initialize LLM
    config_manager = ConfigManager()
    llm = UnifiedLLMClient.from_config(
        providers_config=config_manager.llm_providers,
        provider_name="groq",
        model="llama-3.3-70b-versatile",
        temperature=0.1,
        max_tokens=256,
    )

    results = []
    for i, vacancy in enumerate(vacancies):
        print(f"Testing vacancy {i+1}/{len(vacancies)} (ID: {vacancy.id})...", end=" ")
        result = await test_vacancy_with_llm(vacancy, llm, system_prompt, user_prompt_template)
        results.append(result)

        status = "✓" if result.should_pass else "✗"
        contact = result.extracted_contact or "none"
        print(f"{status} Contact: {contact}")

        # Rate limit
        await asyncio.sleep(0.5)

    return results


def print_report(results: List[TestResult]):
    """Print backtest report."""

    total = len(results)
    should_pass = sum(1 for r in results if r.should_pass)
    should_reject = total - should_pass

    # Accuracy metrics
    correct = sum(1 for r in results if r.is_correct)
    accuracy = correct / total if total > 0 else 0

    # Changes from original behavior
    was_passing_now_reject = sum(1 for r in results if r.was_passing and not r.should_pass)
    was_reject_now_pass = sum(1 for r in results if not r.was_passing and r.should_pass)

    print("\n" + "="*60)
    print("BACKTEST REPORT")
    print("="*60)
    print(f"Total vacancies tested: {total}")
    print(f"Should PASS (has TG contact): {should_pass} ({should_pass/total*100:.1f}%)")
    print(f"Should REJECT (no TG contact): {should_reject} ({should_reject/total*100:.1f}%)")
    print(f"\nAccuracy (LLM vs Regex): {accuracy*100:.1f}%")
    print(f"\nBehavior changes:")
    print(f"  Was passing, now REJECT: {was_passing_now_reject}")
    print(f"  Was rejected, now PASS: {was_reject_now_pass}")

    # Show examples of changes
    if was_passing_now_reject > 0:
        print("\n--- Examples: Was passing, now REJECT ---")
        for r in results:
            if r.was_passing and not r.should_pass:
                print(f"  ID {r.vacancy_id} | {r.chat_title[:30]} | Contact: {r.extracted_contact or 'none'} | {r.message_preview[:50]}...")
                if was_passing_now_reject > 5:
                    break

    # Show false positives (should reject but LLM says pass)
    false_positives = [r for r in results if not r.has_personal_contact and r.extracted_contact]
    if false_positives:
        print("\n--- FALSE POSITIVES (No real contact, but LLM extracted one) ---")
        for r in false_positives[:5]:
            print(f"  ID {r.vacancy_id} | Extracted: {r.extracted_contact} | {r.message_preview[:50]}...")

    # Show false negatives (should pass but LLM missed)
    false_negatives = [r for r in results if r.has_personal_contact and not r.extracted_contact]
    if false_negatives:
        print("\n--- FALSE NEGATIVES (Has contact, but LLM missed) ---")
        for r in false_negatives[:5]:
            print(f"  ID {r.vacancy_id} | {r.chat_title[:30]} | {r.message_preview[:50]}...")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Backtest vacancy filter prompts")
    parser.add_argument("--db", default="jobs.db", help="Path to database")
    parser.add_argument("--limit", type=int, default=50, help="Number of vacancies to test")
    parser.add_argument("--regex-only", action="store_true", help="Only test regex extraction (no LLM)")
    args = parser.parse_args()

    if args.regex_only:
        print("Running regex-only analysis...")
        vacancies = load_vacancies(args.db, args.limit)

        has_contact = 0
        no_contact = 0
        bots = 0

        for v in vacancies:
            hc, contact, ctype = has_personal_telegram_contact(v.message_text)
            if ctype == "bot":
                bots += 1
                print(f"ID {v.id} | BOT | {contact} | {v.chat_title[:30]}")
            elif hc:
                has_contact += 1
                print(f"ID {v.id} | USER | {contact} | {v.chat_title[:30]}")
            else:
                no_contact += 1
                print(f"ID {v.id} | NONE | - | {v.chat_title[:30]}")

        print(f"\nSummary: {has_contact} with contact, {no_contact} without, {bots} bots")
        print(f"Would pass: {has_contact} ({has_contact/len(vacancies)*100:.1f}%)")
        print(f"Would reject: {no_contact + bots} ({(no_contact+bots)/len(vacancies)*100:.1f}%)")
    else:
        results = asyncio.run(run_backtest(args.db, args.limit))
        print_report(results)


if __name__ == "__main__":
    main()
