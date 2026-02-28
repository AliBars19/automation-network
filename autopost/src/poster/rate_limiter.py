"""
Rate limiter — enforces minimum post intervals, monthly tweet cap,
and posting window (08:00–22:00 UTC).

Breaking news (priority == 1) bypasses the window and the minimum gap
so urgent content posts immediately regardless of time of day.
"""
import random
from datetime import datetime, timezone

from loguru import logger

from src.database.db import get_db

# ── Constants ─────────────────────────────────────────────────────────────────
MIN_INTERVAL_S       = 1200   # 20 min minimum between normal posts
MAX_INTERVAL_S       = 3600   # 60 min maximum
JITTER_MAX_S         = 120    # 0–120 s extra randomness
MONTHLY_LIMIT        = 1500   # X Free tier: 1,500 tweets/month per app
POSTING_WINDOW_START = 8      # UTC hour (inclusive) — 08:00
POSTING_WINDOW_END   = 22     # UTC hour (exclusive) — 22:00


def can_post(niche: str) -> bool:
    """True if enough time has passed since the last successful post."""
    last = _last_post_time(niche)
    if last is None:
        return True
    elapsed = (datetime.now(timezone.utc) - last).total_seconds()
    if elapsed < MIN_INTERVAL_S:
        logger.debug(
            f"[{niche}] rate limited — {int(MIN_INTERVAL_S - elapsed)}s remaining"
        )
        return False
    return True


def monthly_post_count(niche: str) -> int:
    """Count successful posts this calendar month."""
    now         = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS cnt FROM post_log
               WHERE niche = ? AND tweet_id IS NOT NULL
                 AND posted_at >= ?""",
            (niche, month_start.strftime("%Y-%m-%dT%H:%M:%SZ")),
        ).fetchone()
    return row["cnt"] if row else 0


def within_monthly_limit(niche: str) -> bool:
    count = monthly_post_count(niche)
    if count >= MONTHLY_LIMIT:
        logger.warning(f"[{niche}] monthly tweet cap reached ({count}/{MONTHLY_LIMIT})")
        return False
    return True


def within_posting_window(is_breaking: bool = False) -> bool:
    """
    Return True if the current UTC hour is within 08:00–22:00.
    Breaking news (is_breaking=True) always returns True — no blackout period
    for priority-1 items (new top-1 demon, RobTop tweet, etc.).
    """
    if is_breaking:
        return True
    hour = datetime.now(timezone.utc).hour
    in_window = POSTING_WINDOW_START <= hour < POSTING_WINDOW_END
    if not in_window:
        logger.debug(f"Outside posting window (UTC {hour:02d}:xx — window 08–22)")
    return in_window


def jitter_delay() -> float:
    """Return a random delay in seconds to use between posts."""
    return random.uniform(MIN_INTERVAL_S, MAX_INTERVAL_S) + random.uniform(0, JITTER_MAX_S)


# ── Internal ──────────────────────────────────────────────────────────────────

def _last_post_time(niche: str) -> datetime | None:
    with get_db() as conn:
        row = conn.execute(
            """SELECT posted_at FROM post_log
               WHERE niche = ? AND tweet_id IS NOT NULL
               ORDER BY posted_at DESC LIMIT 1""",
            (niche,),
        ).fetchone()
    if not row:
        return None
    return datetime.fromisoformat(row["posted_at"].replace("Z", "+00:00"))
