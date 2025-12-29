"""Relevance scoring for discovered channels"""
import math
from typing import Dict, Any


def calculate_relevance_score(
    channel: Dict[str, Any],
    discovery_counts: Dict[str, int],
    keyword_matches: int = 0
) -> float:
    """
    Calculate relevance score for a channel.

    Higher score = more relevant/valuable channel.

    Args:
        channel: Channel stats dict
        discovery_counts: How many times discovered by each method
            {'forward': N, 'mention': N, 'keyword': N}
        keyword_matches: Number of search keywords that found this channel

    Returns:
        Float score (typically 0-30 range)
    """
    score = 0.0

    # Discovery source scoring (discovered from multiple sources = more relevant)
    score += discovery_counts.get("forward", 0) * 5  # Forwards are high signal
    score += discovery_counts.get("mention", 0) * 3  # Mentions are good signal
    score += discovery_counts.get("keyword", 0) * 1  # Keyword matches
    score += keyword_matches * 0.5  # Bonus for matching multiple keywords

    # Subscriber sweet spot scoring
    # Prefer channels that are established but not massive
    subscribers = channel.get("subscribers", 0)
    if subscribers > 0:
        if 5000 <= subscribers <= 50000:
            score += 4  # Ideal range
        elif 1000 <= subscribers < 5000:
            score += 3  # Good range
        elif 50000 < subscribers <= 200000:
            score += 2  # Large but ok
        elif 500 <= subscribers < 1000:
            score += 1  # Small but active
        # Very small (<500) or very large (>200k) get no bonus

        # Log scale bonus for size (diminishing returns)
        score += math.log10(subscribers) * 0.5

    # Activity scoring
    posts_per_week = channel.get("posts_per_week", 0)
    if posts_per_week >= 7:
        score += 3  # Very active (daily+)
    elif posts_per_week >= 3:
        score += 2  # Active
    elif posts_per_week >= 1:
        score += 1  # Somewhat active
    # Inactive channels get no activity bonus

    # Engagement scoring
    engagement = channel.get("engagement_rate", 0)
    if engagement >= 50:
        score += 3  # High engagement
    elif engagement >= 20:
        score += 2  # Good engagement
    elif engagement >= 10:
        score += 1  # Decent engagement

    # Active channel bonus
    if channel.get("is_active", False):
        score += 2

    # Penalty for no username (can't be easily accessed)
    if not channel.get("username"):
        score -= 5

    return round(score, 2)


def categorize_score(score: float) -> str:
    """Categorize score into quality tier"""
    if score >= 15:
        return "excellent"
    elif score >= 10:
        return "good"
    elif score >= 5:
        return "moderate"
    else:
        return "low"
