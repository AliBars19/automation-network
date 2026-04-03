"""
Rate limiter — enforces minimum post intervals, monthly tweet cap,
posting window (08:00–22:00 UTC), and failure backoff.

Breaking news (priority == 1) bypasses the window and the minimum gap
so urgent content posts immediately regardless of time of day.
The failure backoff is always enforced — even for breaking news — because
if the API is down, hammering it won't help.

Per-niche posting config is read from config/<niche>.yaml (posting: section).
Global defaults below are used when the YAML doesn't override them.
"""
import random
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import yaml
from loguru import logger

from src.database.db import get_db

# ── Global defaults ───────────────────────────────────────────────────────────
MIN_INTERVAL_S       = 1200   # 20 min minimum between normal posts
MIN_INTERVAL_BURST_S = 300    # 5 min minimum during match-day burst mode
MAX_INTERVAL_S       = 3600   # 60 min maximum
JITTER_MAX_S         = 120    # 0–120 s extra randomness
MONTHLY_LIMIT        = 1500   # X Free tier: 1,500 tweets/month per app

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


@lru_cache(maxsize=8)
def _posting_config(niche: str) -> dict:
    """Load posting config from config/<niche>.yaml, falling back to global defaults."""
    yaml_path = _CONFIG_DIR / f"{niche}.yaml"
    if yaml_path.exists():
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            return data.get("posting", {})
        except Exception:
            pass
    return {}


def _min_interval(niche: str) -> int:
    return int(_posting_config(niche).get("min_interval_seconds", MIN_INTERVAL_S))


def _max_interval(niche: str) -> int:
    return int(_posting_config(niche).get("max_interval_seconds", MAX_INTERVAL_S))


def _max_daily(niche: str) -> int:
    return int(_posting_config(niche).get("max_daily_posts", 0))  # 0 = no limit
# Posting window shifted to cover gaming peak hours.
# Gaming audiences peak 7-11 PM EST = 23:00-03:00 UTC.
# Window: 14:00-04:00 UTC = 9 AM - 11 PM EST (covers afternoon + prime time)
POSTING_WINDOW_START = 14     # UTC hour (inclusive) — 14:00 (9 AM EST)
POSTING_WINDOW_END   = 4      # UTC hour (exclusive) — 04:00 (11 PM EST)

# Failure backoff: wait 2^N minutes after N consecutive failures, capped at 60 min
_BACKOFF_BASE_S  = 120   # 2 minutes after first failure
_BACKOFF_CAP_S   = 3600  # max 60 minutes between retries
_BACKOFF_ALERT_N = 3     # send Discord alert after this many consecutive failures


# Burst mode: activated when queue has 10+ items pending, indicating a live
# event (RLCS match day, major GD update, etc.). Reduces the minimum posting
# gap from 20 min to 5 min so time-sensitive content doesn't get stale.
_BURST_QUEUE_THRESHOLD = 10


def _is_burst_mode(niche: str) -> bool:
    """Return True if the queue has enough pending items to justify burst posting."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM tweet_queue WHERE niche = ? AND status = 'queued'",
            (niche,),
        ).fetchone()
    return row["cnt"] >= _BURST_QUEUE_THRESHOLD


def can_post(niche: str) -> bool:
    """True if enough time has passed since the last successful post."""
    last = _last_post_time(niche)
    if last is None:
        return True
    elapsed = (datetime.now(timezone.utc) - last).total_seconds()
    # Burst mode: reduced gap on match days (10+ items queued).
    # Safe because post_next() enforces a hard cap of 3 posts per 30 min
    # regardless of burst mode — so even if burst fires faster, the hard
    # cap prevents runaway posting.
    min_gap = MIN_INTERVAL_BURST_S if _is_burst_mode(niche) else _min_interval(niche)
    if elapsed < min_gap:
        logger.debug(
            f"[{niche}] rate limited — {int(min_gap - elapsed)}s remaining"
            f"{' (burst mode)' if min_gap == MIN_INTERVAL_BURST_S else ''}"
        )
        return False
    return True


def failure_backoff_ok(niche: str) -> bool:
    """
    Return True if enough time has passed since the last failed post attempt.
    Uses exponential backoff based on consecutive failure count so the poster
    doesn't hammer a broken API every 2 minutes.
    """
    with get_db() as conn:
        # Count consecutive recent failures (no successes in between)
        rows = conn.execute(
            """SELECT tweet_id, posted_at FROM post_log
               WHERE niche = ?
               ORDER BY posted_at DESC LIMIT 20""",
            (niche,),
        ).fetchall()

    if not rows:
        return True  # no history at all

    # Count consecutive failures from the top
    consecutive = 0
    last_failure_at = None
    for row in rows:
        if row["tweet_id"] is not None:
            break  # hit a success — stop counting
        consecutive += 1
        if last_failure_at is None:
            last_failure_at = row["posted_at"]

    if consecutive == 0:
        return True  # last attempt was a success

    # Exponential backoff: 2min, 4min, 8min, 16min, 32min, 60min cap
    delay = min(_BACKOFF_BASE_S * (2 ** (consecutive - 1)), _BACKOFF_CAP_S)
    last_dt = datetime.fromisoformat(last_failure_at.replace("Z", "+00:00"))
    elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()

    if elapsed < delay:
        remaining = int(delay - elapsed)
        logger.debug(
            f"[{niche}] failure backoff — {consecutive} consecutive failures,"
            f" waiting {remaining}s (backoff {int(delay)}s)"
        )
        return False

    return True


def consecutive_failure_count(niche: str) -> int:
    """Return the number of consecutive posting failures (0 if last was a success)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT tweet_id FROM post_log
               WHERE niche = ?
               ORDER BY posted_at DESC LIMIT 20""",
            (niche,),
        ).fetchall()
    count = 0
    for row in rows:
        if row["tweet_id"] is not None:
            break
        count += 1
    return count


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
    Return True if the current UTC hour is within the posting window.
    Supports windows that wrap past midnight (e.g. 14:00–04:00 UTC).
    Breaking news (is_breaking=True) always returns True.
    """
    if is_breaking:
        return True
    hour = datetime.now(timezone.utc).hour
    if POSTING_WINDOW_START < POSTING_WINDOW_END:
        in_window = POSTING_WINDOW_START <= hour < POSTING_WINDOW_END
    else:
        # Window wraps past midnight (e.g. 14-04 means 14:00..23:59 + 00:00..03:59)
        in_window = hour >= POSTING_WINDOW_START or hour < POSTING_WINDOW_END
    if not in_window:
        logger.debug(
            f"Outside posting window (UTC {hour:02d}:xx "
            f"— window {POSTING_WINDOW_START:02d}:00–{POSTING_WINDOW_END:02d}:00 UTC)"
        )
    return in_window


def within_daily_limit(niche: str) -> bool:
    """Return True if today's post count is below the per-niche daily cap.

    A daily cap of 0 (default when not set in YAML) means unlimited.
    """
    daily_cap = _max_daily(niche)
    if daily_cap <= 0:
        return True  # no limit configured
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS cnt FROM post_log
               WHERE niche = ? AND tweet_id IS NOT NULL
                 AND posted_at >= ?""",
            (niche, today_start.strftime("%Y-%m-%dT%H:%M:%SZ")),
        ).fetchone()
    count = row["cnt"] if row else 0
    if count >= daily_cap:
        logger.debug(f"[{niche}] daily cap reached ({count}/{daily_cap})")
        return False
    return True


def jitter_delay(niche: str = "") -> float:
    """Return a random delay in seconds to use between posts.

    Uses per-niche min/max from YAML if niche is provided.
    """
    lo = _min_interval(niche) if niche else MIN_INTERVAL_S
    hi = _max_interval(niche) if niche else MAX_INTERVAL_S
    return random.uniform(lo, hi) + random.uniform(0, JITTER_MAX_S)


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
