"""
Quality gate — filters community content to prevent noise from reaching the feed.

Only content that passes engagement thresholds and content-type caps gets queued.
Official news (priority 1-3) bypasses the gate entirely — it's always newsworthy.

The gate enforces:
  1. Engagement thresholds based on source follower count
  2. Per-day content type caps (e.g. max 2 community clips/day)
  3. Stale content penalty (>12 hours old → skip)
"""
from datetime import datetime, timezone, timedelta

from loguru import logger

from src.database.db import get_db

# Content types that require quality filtering (community content).
# Official content types (patch_notes, game_update, etc.) bypass the gate.
_COMMUNITY_TYPES: set[str] = {
    "community_clip",
    "reddit_clip",
    "rank_milestone",
    "stat_milestone",
    "creator_spotlight",
    "viral_moment",
}
# NOTE: monitored_tweet is intentionally NOT in _COMMUNITY_TYPES.
# These come from curated Twitter sources (ShiftRLE, LiquipediaRL, etc.)
# that are already quality-filtered at the config level. Gating them
# on engagement score would silently block all community Twitter content
# since twscrape doesn't provide like counts at collection time.

# Max posts per content type per day (resets at midnight UTC)
_DAILY_CAPS: dict[str, int] = {
    "community_clip": 2,
    "reddit_clip":    3,
    "monitored_tweet": 8,
    "rank_milestone":  1,
    "stat_milestone":  1,
    "creator_spotlight": 2,
    "viral_moment":    1,
}

# Engagement thresholds: minimum likes for community tweets to be considered
# newsworthy. Scaled by source follower tier.
_ENGAGEMENT_THRESHOLDS: dict[str, int] = {
    "small":   50,    # accounts with < 10K followers
    "medium":  100,   # accounts with 10K-50K followers
    "large":   200,   # accounts with 50K+ followers
}

# Maximum age for community content (hours)
_MAX_AGE_HOURS = 12


def passes_quality_gate(
    content_type: str,
    niche: str,
    score: int = 0,
    age_hours: float = 0,
    source_followers: int = 0,
) -> bool:
    """
    Return True if community content passes all quality checks.

    Official content types (not in _COMMUNITY_TYPES) always pass.
    Community content must pass engagement threshold + daily cap + age check.
    """
    # Official content always passes
    if content_type not in _COMMUNITY_TYPES:
        return True

    # Stale content check
    if age_hours > _MAX_AGE_HOURS:
        logger.debug(
            f"[QualityGate] {content_type} rejected: too old ({age_hours:.1f}h > {_MAX_AGE_HOURS}h)"
        )
        return False

    # Engagement threshold check (score = likes, upvotes, etc.)
    if source_followers >= 50_000:
        threshold = _ENGAGEMENT_THRESHOLDS["large"]
    elif source_followers >= 10_000:
        threshold = _ENGAGEMENT_THRESHOLDS["medium"]
    else:
        threshold = _ENGAGEMENT_THRESHOLDS["small"]

    if score < threshold:
        logger.debug(
            f"[QualityGate] {content_type} rejected: score {score} < threshold {threshold}"
        )
        return False

    # Daily cap check
    cap = _DAILY_CAPS.get(content_type)
    if cap is not None and not _within_daily_cap(niche, content_type, cap):
        logger.debug(
            f"[QualityGate] {content_type} rejected: daily cap ({cap}) reached for {niche}"
        )
        return False

    return True


def _within_daily_cap(niche: str, content_type: str, cap: int) -> bool:
    """Return True if fewer than `cap` items of this type were posted today."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS cnt FROM tweet_queue tq
               JOIN raw_content rc ON tq.raw_content_id = rc.id
               WHERE tq.niche = ? AND tq.status IN ('queued', 'posted')
                 AND tq.created_at >= ?
                 AND rc.content_type = ?""",
            (niche, today_start, content_type),
        ).fetchone()
    return row["cnt"] < cap
