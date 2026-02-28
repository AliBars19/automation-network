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
from src.poster.rate_limiter import can_post, within_monthly_limit, within_posting_window

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
    "youtube_video":       5,
    "pro_player_content":  5,
    "creator_spotlight":   5,
    "speedrun_wr":         5,
    "reddit_highlight":    7,
    "community_clip":      7,
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
                # Retweet signal — check if the collector supplied a retweet_id
                retweet_id = item.metadata.get("retweet_id")
                if not retweet_id:
                    logger.debug(f"[{niche}] skipping retweet-signal with no id: {item.external_id}")
                    continue
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
    The monthly cap is always enforced regardless of priority.
    """
    if not within_monthly_limit(niche):
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

        # Dispatch: retweet signal vs normal tweet
        if text.startswith("RETWEET:"):
            original_id = text.split(":", 1)[1].strip()
            success = client.retweet(original_id)
            if success:
                mark_posted(conn, queue_id, original_id)
                return True
            else:
                mark_failed(conn, queue_id, f"retweet {original_id} failed")
                return False

        tweet_id = client.post_tweet(text=text, media_path=row["media_path"])
        if tweet_id:
            mark_posted(conn, queue_id, tweet_id)
            return True
        else:
            mark_failed(conn, queue_id, "TwitterClient.post_tweet returned None")
            return False


def skip_stale(niche: str, max_age_hours: int = 6) -> int:
    """
    Mark old queued tweets as skipped so they don't clog the queue.
    Returns count of rows skipped.
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id FROM tweet_queue
               WHERE niche = ? AND status = 'queued'
                 AND created_at <= datetime('now', ? || ' hours')""",
            (niche, f"-{max_age_hours}"),
        ).fetchall()
        for row in rows:
            mark_skipped(conn, row["id"])
        if rows:
            logger.info(f"[{niche}] skipped {len(rows)} stale queue items")
    return len(rows)
