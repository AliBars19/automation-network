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
from src.poster.quality_gate import passes_quality_gate
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
    "community_clip":      6,
    "reddit_clip":         6,
    "first_victor":        3,
    "viral_moment":        5,
    "community_event":     4,
    "flashback":           7,
    "stat_milestone":      7,
    "rank_milestone":      8,
}
_DEFAULT_PRIORITY = 5


# ── Pipeline: collect → format → enqueue ──────────────────────────────────────

# Hard cap: never queue more than this many items per collector per cycle.
# Prevents a broken collector from flooding the queue.
_MAX_ITEMS_PER_CYCLE = 5


async def collect_and_queue(collector: BaseCollector, niche: str) -> int:
    """
    Run one collector pass and enqueue any new content.
    Returns the number of new tweets added to the queue.

    Hard-capped at _MAX_ITEMS_PER_CYCLE items per cycle to prevent
    any single collector from flooding the queue (which caused the
    duplicate posting incident of 2026-03-28).
    """
    import asyncio

    try:
        items: list[RawContent] = await collector.collect()
    except Exception as exc:
        logger.error(f"[{niche}] collector {type(collector).__name__} raised: {exc}")
        return 0

    queued = 0
    with get_db() as conn:
        for item in items:
            # Hard cap: stop queueing if we've hit the per-cycle limit
            if queued >= _MAX_ITEMS_PER_CYCLE:
                logger.info(
                    f"[{niche}] hit per-cycle cap ({_MAX_ITEMS_PER_CYCLE}) — "
                    f"remaining {len(items) - queued} items deferred to next cycle"
                )
                break
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

            # Quality gate: community content must pass engagement + cap checks
            item_age = 0.0
            if hasattr(item, "metadata") and "created_at" in item.metadata:
                try:
                    from datetime import datetime as _dt
                    created = _dt.fromisoformat(
                        item.metadata["created_at"].replace("Z", "+00:00")
                    )
                    item_age = (
                        _dt.now(__import__("datetime").timezone.utc) - created
                    ).total_seconds() / 3600
                except Exception:
                    pass

            if not passes_quality_gate(
                content_type=item.content_type,
                niche=niche,
                score=item.score,
                age_hours=item_age,
                source_followers=item.metadata.get("followers", 0),
            ):
                continue

            tweet_text = format_tweet(item)

            if tweet_text is None:
                # Either a retweet signal or a content type with no template (skip)
                retweet_id = item.metadata.get("retweet_id")
                if not retweet_id:
                    continue  # no template and no retweet — skip entirely
                account = item.metadata.get("account", "")
                tweet_text = f"RETWEET:{retweet_id}:{account}"
                # Dedup: same tweet ID may arrive from multiple sources
                existing = conn.execute(
                    "SELECT 1 FROM tweet_queue WHERE tweet_text = ? AND status = 'queued' LIMIT 1",
                    (tweet_text,),
                ).fetchone()
                if existing:
                    continue
            else:
                # Exact-match dedup: prevent the same tweet text from being
                # queued twice (catches YouTube videos re-collected across polls)
                exact_dup = conn.execute(
                    "SELECT 1 FROM tweet_queue WHERE tweet_text = ? AND niche = ? AND status IN ('queued', 'posted') LIMIT 1",
                    (tweet_text, niche),
                ).fetchone()
                if exact_dup:
                    continue

                # Similarity check: too close to a recently queued tweet?
                if is_similar_story(conn, tweet_text, niche):
                    logger.debug(
                        f"[{niche}] similar story already queued, skipping:"
                        f" {tweet_text[:60]}"
                    )
                    continue

            priority   = _PRIORITY.get(item.content_type, _DEFAULT_PRIORITY)
            # Reddit clips carry their own pre-downloaded video in metadata
            reddit_media = item.metadata.get("media_path", "")
            if reddit_media and reddit_media.endswith(".mp4"):
                media_path = reddit_media
            elif item.image_url:
                # Run blocking I/O (HTTP download + Pillow resize) off the event loop
                media_path = await asyncio.to_thread(prepare_media, item.image_url)
            else:
                media_path = None
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

# Absolute safety net: never post more than this many tweets in a 30-min window.
# This is the final guard against any duplication or queue-flooding bug.
_MAX_POSTS_PER_30MIN = 3


def _posts_in_last_30min(niche: str) -> int:
    """Count tweets posted for this niche in the last 30 minutes."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS cnt FROM post_log
               WHERE niche = ? AND tweet_id IS NOT NULL
                 AND posted_at >= datetime('now', '-30 minutes')""",
            (niche,),
        ).fetchone()
    return row["cnt"]


def post_next(niche: str, client: TwitterClient) -> bool:
    """
    Post the next queued tweet for `niche`.
    Returns True if a tweet was posted (or dry-run logged), False otherwise.

    Safety layers (all enforced, never bypassed):
      1. Monthly cap (1,500/month)
      2. Failure backoff (exponential)
      3. 30-minute hard cap (max 3 posts per 30 min per niche)

    Priority-1 items (breaking news) bypass:
      - The posting window (14:00–04:00 UTC) — posts at any hour
      - The 20-min minimum gap — posts immediately
    """
    if not within_monthly_limit(niche):
        return False

    # Always respect failure backoff — if the API is down, hammering won't help
    if not failure_backoff_ok(niche):
        return False

    # Hard safety net: never exceed 3 posts in any 30-minute window
    recent = _posts_in_last_30min(niche)
    if recent >= _MAX_POSTS_PER_30MIN:
        logger.warning(
            f"[{niche}] safety cap hit: {recent} posts in last 30 min "
            f"(max {_MAX_POSTS_PER_30MIN}) — throttling"
        )
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
        # NOTE: Pure retweets are algorithmically worthless on X (lowest
        # engagement format). We convert them to quote tweets with brief
        # context so the algorithm treats them as original content.
        if text.startswith("RETWEET:"):
            parts = text.split(":", 2)
            original_id = parts[1].strip() if len(parts) > 1 else ""
            source_account = parts[2].strip() if len(parts) > 2 else ""
            if not original_id.isdigit():
                mark_failed(conn, queue_id, f"invalid retweet ID: {original_id!r}")
                return False
            # Quote-tweet with a source-aware context line instead of pure RT
            context = _retweet_context(niche, source_account)
            new_id = client.quote_tweet(original_id, context)
            if new_id:
                mark_posted(conn, queue_id, new_id)
                return True
            else:
                mark_failed(conn, queue_id, f"quote-tweet {original_id} failed")
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

        # Post as a single tweet with URL inline. Self-reply URL pattern was
        # removed because the reply tweets clutter the profile timeline as
        # standalone naked links, which looks worse than the URL penalty.
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


import random
import re

_URL_RE = re.compile(r"https?://\S+")

# Source-aware context lines for quote tweets.
# Keyed by account handle (lowercase) → list of context lines.
_RT_CONTEXT_BY_ACCOUNT: dict[str, list[str]] = {
    "rocketleague":    ["From @RocketLeague:", "Rocket League update:"],
    "rlesports":       ["From @RLEsports:", "#RLCS update:"],
    "rlcs":            ["From @RLCS:", "#RLCS news:"],
    "psyonixstudios":  ["From @PsyonixStudios:", "Psyonix update:"],
    "rl_status":       ["Server status:", "From @RL_Status:"],
    "robtopgames":     ["From @RobTopGames:", "RobTop update:"],
    "_geometrydash":   ["From @_GeometryDash:", "Geometry Dash news:"],
    "demonlistgd":     ["Demon List update:", "From @demonlistgd:"],
    "geode_sdk":       ["Geode update:", "From @geode_sdk:"],
}

_RT_CONTEXT_FALLBACK: dict[str, list[str]] = {
    "rocketleague": ["Rocket League news:", "#RLCS update:"],
    "geometrydash": ["Geometry Dash news:", "GD update:"],
}


def _retweet_context(niche: str, source_account: str = "") -> str:
    """Pick a context line for a quote tweet, tailored to the source account."""
    if source_account:
        options = _RT_CONTEXT_BY_ACCOUNT.get(source_account.lower())
        if options:
            return random.choice(options)
    fallback = _RT_CONTEXT_FALLBACK.get(niche, ["News:"])
    return random.choice(fallback)


def _split_url(text: str) -> tuple[str, str | None]:
    """
    Extract the last URL from tweet text for self-reply posting.
    Returns (main_text, url_or_none).

    If the tweet is short enough that removing the URL would leave
    less than 30 chars of content, keep it inline (not worth splitting).
    """
    urls = _URL_RE.findall(text)
    if not urls:
        return text, None

    last_url = urls[-1]
    # Use rfind to replace only the LAST occurrence (str.replace hits all)
    pos = text.rfind(last_url)
    stripped = (text[:pos] + text[pos + len(last_url):]).strip().rstrip("\n")

    # Don't split if the remaining text is too short to stand alone
    if len(stripped) < 30:
        return text, None

    return stripped, last_url


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
