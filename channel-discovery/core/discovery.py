"""Main discovery service - orchestrates channel discovery"""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable
from collections import defaultdict

from sqlalchemy.orm import Session

import sys
sys.path.append(str(__file__).rsplit("/", 2)[0])

from core.telegram_client import get_client, DiscoveryClient
from core.scoring import calculate_relevance_score
from db.models import DiscoveredChannel, SearchJob, SeedChannel
from db.database import get_db_session

logger = logging.getLogger(__name__)


class DiscoveryService:
    """Orchestrates channel discovery from multiple sources"""

    def __init__(self):
        self.client: Optional[DiscoveryClient] = None

    async def _get_client(self) -> DiscoveryClient:
        """Get telegram client"""
        if not self.client:
            self.client = await get_client()
        return self.client

    async def run_search_job(
        self,
        job_id: str,
        keywords: List[str],
        min_subscribers: int = 500,
        max_subscribers: int = 500000,
        min_posts_per_week: float = 0,
        use_seed_channels: bool = False,
        seed_usernames: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Run a complete discovery job.

        Args:
            job_id: Unique job identifier
            keywords: List of keywords to search
            min_subscribers: Minimum subscriber count filter
            max_subscribers: Maximum subscriber count filter
            min_posts_per_week: Minimum posting frequency filter
            use_seed_channels: Whether to analyze seed channels
            seed_usernames: List of seed channel usernames
            progress_callback: Callback(progress_percent, current_step)

        Returns:
            Job results dict
        """
        client = await self._get_client()
        discovered: Dict[str, Dict[str, Any]] = {}  # username -> channel data
        discovery_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        keyword_matches: Dict[str, int] = defaultdict(int)

        total_steps = len(keywords) + (len(seed_usernames) if seed_usernames else 0)
        current_step = 0

        def update_progress(step_name: str):
            nonlocal current_step
            current_step += 1
            progress = int((current_step / max(total_steps, 1)) * 80)  # 80% for discovery
            if progress_callback:
                progress_callback(progress, step_name)

        # 1. Keyword search
        for keyword in keywords:
            update_progress(f"Searching: {keyword}")
            try:
                results = await client.search_channels(keyword, limit=100)
                for ch in results:
                    username = ch.get("username")
                    if username:
                        username = username.lower()
                        if username not in discovered:
                            discovered[username] = ch
                        discovery_counts[username]["keyword"] += 1
                        keyword_matches[username] += 1
            except Exception as e:
                logger.error(f"Keyword search failed for '{keyword}': {e}")

            await asyncio.sleep(1)  # Rate limit buffer

        # 2. Seed channel analysis (forwards + mentions)
        if use_seed_channels and seed_usernames:
            for seed_username in seed_usernames:
                update_progress(f"Analyzing: @{seed_username}")
                try:
                    posts = await client.get_channel_posts(seed_username, limit=50)

                    for post in posts:
                        # Extract forwards
                        fwd = post.get("fwd_from")
                        if fwd and fwd.get("channel_id"):
                            try:
                                fwd_username = await client.resolve_channel_id(
                                    fwd["channel_id"]
                                )
                                if fwd_username:
                                    fwd_username = fwd_username.lower()
                                    if fwd_username not in discovered:
                                        discovered[fwd_username] = {
                                            "username": fwd_username,
                                            "discovered_from": seed_username
                                        }
                                    discovery_counts[fwd_username]["forward"] += 1
                            except Exception:
                                pass

                        # Extract mentions
                        for mention in post.get("mentions", []):
                            mention = mention.lower()
                            if mention not in discovered:
                                discovered[mention] = {
                                    "username": mention,
                                    "discovered_from": seed_username
                                }
                            discovery_counts[mention]["mention"] += 1

                except Exception as e:
                    logger.error(f"Seed analysis failed for @{seed_username}: {e}")

                await asyncio.sleep(1)

        # 3. Enrich with stats and filter
        if progress_callback:
            progress_callback(80, "Enriching channel stats...")

        enriched_channels = []
        total_to_enrich = len(discovered)

        for i, (username, channel_data) in enumerate(discovered.items()):
            try:
                stats = await client.get_channel_stats(username)
                if stats:
                    channel_data.update(stats)

                    # Apply filters
                    subs = stats.get("subscribers", 0)
                    ppw = stats.get("posts_per_week", 0)

                    if min_subscribers <= subs <= max_subscribers:
                        if ppw >= min_posts_per_week:
                            # Calculate score
                            score = calculate_relevance_score(
                                stats,
                                dict(discovery_counts[username]),
                                keyword_matches.get(username, 0)
                            )
                            channel_data["relevance_score"] = score
                            channel_data["discovery_counts"] = dict(discovery_counts[username])
                            enriched_channels.append(channel_data)

                # Progress update every 10 channels
                if i % 10 == 0 and progress_callback:
                    progress = 80 + int((i / max(total_to_enrich, 1)) * 15)
                    progress_callback(progress, f"Stats: {i}/{total_to_enrich}")

            except Exception as e:
                logger.error(f"Failed to enrich @{username}: {e}")

            await asyncio.sleep(0.5)  # Rate limit buffer

        # 4. Save to database
        if progress_callback:
            progress_callback(95, "Saving results...")

        new_count = 0
        with get_db_session() as db:
            for ch in enriched_channels:
                existing = db.query(DiscoveredChannel).filter(
                    DiscoveredChannel.username == ch["username"]
                ).first()

                if existing:
                    # Update existing
                    existing.subscribers = ch.get("subscribers", existing.subscribers)
                    existing.posts_per_week = ch.get("posts_per_week", existing.posts_per_week)
                    existing.avg_views = ch.get("avg_views", existing.avg_views)
                    existing.engagement_rate = ch.get("engagement_rate", existing.engagement_rate)
                    existing.last_post_date = ch.get("last_post_date", existing.last_post_date)
                    existing.is_active = ch.get("is_active", existing.is_active)
                    existing.relevance_score = max(
                        existing.relevance_score, ch.get("relevance_score", 0)
                    )
                    existing.discovery_count += 1
                    existing.last_checked = datetime.utcnow()
                    existing.stats_updated = datetime.utcnow()
                else:
                    # Create new
                    counts = ch.get("discovery_counts", {})
                    source = "keyword"
                    if counts.get("forward", 0) > 0:
                        source = "forward"
                    elif counts.get("mention", 0) > 0:
                        source = "mention"

                    new_channel = DiscoveredChannel(
                        telegram_id=ch.get("telegram_id"),
                        username=ch["username"],
                        title=ch.get("title"),
                        description=ch.get("description"),
                        subscribers=ch.get("subscribers", 0),
                        posts_per_week=ch.get("posts_per_week", 0),
                        avg_views=ch.get("avg_views", 0),
                        engagement_rate=ch.get("engagement_rate", 0),
                        last_post_date=ch.get("last_post_date"),
                        is_active=ch.get("is_active", True),
                        discovery_source=source,
                        discovered_from=ch.get("discovered_from"),
                        discovery_keywords=keywords,
                        relevance_score=ch.get("relevance_score", 0),
                        discovery_count=1,
                        stats_updated=datetime.utcnow()
                    )
                    db.add(new_channel)
                    new_count += 1

        if progress_callback:
            progress_callback(100, "Complete")

        return {
            "job_id": job_id,
            "status": "completed",
            "channels_found": len(enriched_channels),
            "channels_new": new_count,
            "keywords_used": keywords,
        }

    async def quick_search(
        self,
        keywords: List[str],
        min_subscribers: int = 1000,
        max_subscribers: int = 100000,
        limit_per_keyword: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Quick search without full enrichment.
        Returns basic results for preview.
        """
        client = await self._get_client()
        results = []
        seen = set()

        for keyword in keywords:
            try:
                channels = await client.search_channels(keyword, limit=limit_per_keyword)
                for ch in channels:
                    username = ch.get("username", "").lower()
                    if username and username not in seen:
                        subs = ch.get("subscribers", 0)
                        if min_subscribers <= subs <= max_subscribers:
                            seen.add(username)
                            results.append(ch)
            except Exception as e:
                logger.error(f"Quick search error for '{keyword}': {e}")

            await asyncio.sleep(0.5)

        return sorted(results, key=lambda x: x.get("subscribers", 0), reverse=True)


# Service instance
discovery_service = DiscoveryService()
