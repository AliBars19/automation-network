"""
Queue runner — two responsibilities:

  1. collect_and_queue(collector, niche)
       Runs a collector, formats new items, inserts them into tweet_queue.
       Skips duplicates (insert_raw_content returns is_new=False).

  2. post_next(niche, client)
       Pulls the highest-priority queued tweet and posts it via TwitterClient.
       Enforces rate limits and the monthly cap before every post.
"""
from loguru import logger

from src.collectors.base import BaseCollector, RawContent
from src.database.db import (
    add_to_queue,
    get_db,
    get_queued_tweets,
    insert_raw_content,
    is_similar_story,
    mark_failed,
    mark_posted,
    mark_skipped,
    url_already_queued,
)
from src.formatter.formatter import format_tweet
from src.formatter.media import prepare_media
from src.poster.client import TwitterClient
from src.poster.rate_limiter import (
    can_post,
    consecutive_failure_count,
    failure_backoff_ok,
    within_monthly_limit,
    within_posting_window,
    _BACKOFF_ALERT_N,
)

# Priority map: lower number = posted sooner
_PRIORITY: dict[str, int] = {
    "top1_verified":       1,
    "breaking_news":       1,
    "robtop_tweet":        1,   # RobTop tweets are always notable
    "official_tweet":      2,   # official RL/GD account tweets
    "patch_notes":         2,
    "game_update":         2,
    "season_start":        2,
    "event_announcement":  2,
    "esports_result":      3,
    "roster_change":       3,
    "demon_list_update":   3,
    "level_verified":      3,
    "collab_announcement": 3,
    "item_shop":           4,
    "daily_level":         4,
    "weekly_demon":        4,
    "mod_update":          4,
    "level_rated":         4,
    "esports_matchup":     4,
    "level_beaten":        5,
    "youtube_video":       4,
    "creator_spotlight":   5,
    "speedrun_wr":         5,
    "monitored_tweet":     6,
    "community_clip":      7,
    "flashback":           7,
    "stat_milestone":      7,
    "rank_milestone":      8,
}
_DEFAULT_PRIORITY = 5


# ── Pipeline: collect → format → enqueue ──────────────────────────────────────

async def collect_and_queue(collector: BaseCollector, niche: str) -> int:
    """
    Run one collector pass and enqueue any new content.
    Returns the number of new tweets added to the queue.
    """
    try:
        items: list[RawContent] = await collector.collect()
    except Exception as exc:
        logger.error(f"[{niche}] collector {type(collector).__name__} raised: {exc}")
        return 0

    queued = 0
    with get_db() as conn:
        for item in items:
            content_id, is_new = insert_raw_content(conn, item)
            if not is_new:
                continue  # already seen from this source — dedup

            # Cross-source URL dedup: same article already queued from a different source?
            if item.url and url_already_queued(conn, item.url, content_id):
                logger.debug(
                    f"[{niche}] URL already queued from another source, skipping:"
                    f" {item.url[:70]}"
                )
                continue

            tweet_text = format_tweet(item)

            if tweet_text is None:
                # Either a retweet signal or a content type with no template (skip)
                retweet_id = item.metadata.get("retweet_id")
                if not retweet_id:
                    continue  # no template and no retweet — skip entirely
                # Queue as "RETWEET:{id}" — post_next will call client.retweet()
                tweet_text = f"RETWEET:{retweet_id}"
            else:
                # Similarity check: too close to a recently queued tweet?
                if is_similar_story(conn, tweet_text, niche):
                    logger.debug(
                        f"[{niche}] similar story already queued, skipping:"
                        f" {tweet_text[:60]}"
                    )
                    continue

            priority   = _PRIORITY.get(item.content_type, _DEFAULT_PRIORITY)
            media_path = prepare_media(item.image_url) if item.image_url else None
            add_to_queue(
                conn, niche, tweet_text, content_id,
                media_path=media_path, priority=priority,
            )
            logger.info(
                f"[{niche}] queued [{item.content_type}] p{priority}"
                f"{' +img' if media_path else ''}: {tweet_text[:60]}…"
            )
            queued += 1

    return queued


# ── Poster: dequeue → post ────────────────────────────────────────────────────

def post_next(niche: str, client: TwitterClient) -> bool:
    """
    Post the next queued tweet for `niche`.
    Returns True if a tweet was posted (or dry-run logged), False otherwise.

    Priority-1 items (breaking news) bypass:
      - The posting window (08:00–22:00 UTC) — posts at any hour
      - The 20-min minimum gap — posts immediately
    The monthly cap and failure backoff are always enforced regardless of priority.
    """
    if not within_monthly_limit(niche):
        return False

    # Always respect failure backoff — if the API is down, hammering won't help
    if not failure_backoff_ok(niche):
        return False

    with get_db() as conn:
        rows = get_queued_tweets(conn, niche, limit=1)
        if not rows:
            logger.debug(f"[{niche}] queue is empty")
            return False

        row         = rows[0]
        queue_id    = row["id"]
        text        = row["tweet_text"]
        is_breaking = (row["priority"] == 1)

        # Non-breaking: enforce posting window + minimum gap
        if not is_breaking:
            if not within_posting_window():
                return False
            if not can_post(niche):
                return False
        else:
            logger.info(f"[{niche}] breaking news (p1) — bypassing window + rate limit")

        # Dispatch: retweet signal vs quote tweet vs normal tweet
        if text.startswith("RETWEET:"):
            original_id = text.split(":", 1)[1].strip()
            if not original_id.isdigit():
                mark_failed(conn, queue_id, f"invalid retweet ID: {original_id!r}")
                return False
            success = client.retweet(original_id)
            if success:
                mark_posted(conn, queue_id, original_id)
                return True
            else:
                mark_failed(conn, queue_id, f"retweet {original_id} failed")
                _check_failure_alert(niche)
                return False

        if text.startswith("QUOTE:"):
            # Format: QUOTE:{tweet_id}:{commentary text}
            remainder = text[len("QUOTE:"):]
            sep_idx = remainder.find(":")
            if sep_idx == -1:
                mark_failed(conn, queue_id, f"malformed QUOTE signal: {text[:60]!r}")
                return False
            original_id = remainder[:sep_idx].strip()
            commentary = remainder[sep_idx + 1:].strip()
            if not original_id.isdigit() or not commentary:
                mark_failed(conn, queue_id, f"invalid QUOTE id/text: {original_id!r}")
                return False
            new_id = client.quote_tweet(original_id, commentary)
            if new_id:
                mark_posted(conn, queue_id, new_id)
                return True
            else:
                mark_failed(conn, queue_id, f"quote-tweet {original_id} failed")
                _check_failure_alert(niche)
                return False

        tweet_id = client.post_tweet(text=text, media_path=row["media_path"])
        if tweet_id:
            mark_posted(conn, queue_id, tweet_id)
            return True
        else:
            mark_failed(conn, queue_id, "TwitterClient.post_tweet returned None")
            _check_failure_alert(niche)
            return False


def _check_failure_alert(niche: str) -> None:
    """Send a Discord alert when consecutive failures hit the threshold."""
    count = consecutive_failure_count(niche)
    if count == _BACKOFF_ALERT_N:
        try:
            import asyncio
            from src.monitoring.alerts import send_alert
            loop = asyncio.get_running_loop()
            loop.create_task(send_alert(
                f"**Poster [{niche}]**: {count} consecutive failures — "
                f"backing off. Check X API access (402 = tier issue).",
                level="error",
            ))
        except Exception:
            pass


def skip_stale(niche: str, max_age_hours: int = 6) -> int:
    """
    Mark old queued tweets as skipped so they don't clog the queue.
    Returns count of rows skipped.
    """
    from datetime import datetime, timezone, timedelta
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id FROM tweet_queue
               WHERE niche = ? AND status = 'queued'
                 AND created_at <= ?""",
            (niche, cutoff),
        ).fetchall()
        for row in rows:
            mark_skipped(conn, row["id"])
        if rows:
            logger.info(f"[{niche}] skipped {len(rows)} stale queue items")
    return len(rows)
