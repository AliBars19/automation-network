"""
Unit tests for src/poster/rate_limiter.py — DB-dependent functions.

Tests the functions that query the post_log / post_log table:
can_post(), failure_backoff_ok(), consecutive_failure_count(),
monthly_post_count(), within_monthly_limit().

Uses an in-memory SQLite DB for isolation.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from src.poster.rate_limiter import (
    MONTHLY_LIMIT,
    MIN_INTERVAL_S,
    can_post,
    consecutive_failure_count,
    failure_backoff_ok,
    monthly_post_count,
    within_daily_limit,
    within_monthly_limit,
    _last_post_time,
    _min_interval,
    _max_interval,
    _posting_config,
)


# ── In-memory DB fixture ──────────────────────────────────────────────────────

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "database" / "schema.sql"


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()
    return conn


@contextmanager
def _ctx(conn):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _log_success(conn, niche: str, posted_at: str, tweet_id: str = "tweet_1"):
    """Insert a success row into post_log (tweet_id set)."""
    conn.execute(
        """INSERT INTO post_log (niche, tweet_id, tweet_text, posted_at)
           VALUES (?, ?, 'text', ?)""",
        (niche, tweet_id, posted_at),
    )
    conn.commit()


def _log_failure(conn, niche: str, posted_at: str):
    """Insert a failure row into post_log (tweet_id NULL)."""
    conn.execute(
        """INSERT INTO post_log (niche, tweet_id, tweet_text, posted_at, error)
           VALUES (?, NULL, 'text', ?, 'API error')""",
        (niche, posted_at),
    )
    conn.commit()


def _utc(offset_minutes: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(minutes=offset_minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── can_post() ────────────────────────────────────────────────────────────────

class TestCanPost:

    def test_returns_true_when_no_history(self):
        conn = _make_db()
        with patch("src.poster.rate_limiter.get_db", side_effect=lambda: _ctx(conn)):
            assert can_post("rocketleague") is True

    def test_returns_true_when_last_post_is_old_enough(self):
        conn = _make_db()
        old_time = _utc(offset_minutes=25)  # 25 min ago, well past any default min interval
        _log_success(conn, "rocketleague", old_time)
        # Patch _posting_config so the test uses global defaults (MIN_INTERVAL_S=1200s/20min)
        # rather than whatever the rocketleague.yaml currently specifies.
        with patch("src.poster.rate_limiter.get_db", side_effect=lambda: _ctx(conn)), \
             patch("src.poster.rate_limiter._posting_config", return_value={}):
            assert can_post("rocketleague") is True

    def test_returns_false_when_last_post_is_too_recent(self):
        conn = _make_db()
        recent_time = _utc(offset_minutes=5)  # only 5 min ago
        _log_success(conn, "rocketleague", recent_time)
        with patch("src.poster.rate_limiter.get_db", side_effect=lambda: _ctx(conn)):
            assert can_post("rocketleague") is False

    def test_different_niches_independent(self):
        conn = _make_db()
        recent_time = _utc(offset_minutes=5)
        _log_success(conn, "rocketleague", recent_time)
        # side_effect produces a fresh context manager for each get_db() call
        with patch("src.poster.rate_limiter.get_db", side_effect=lambda: _ctx(conn)):
            # rocketleague is rate limited
            assert can_post("rocketleague") is False
            # geometrydash has no history
            assert can_post("geometrydash") is True


# ── failure_backoff_ok() ──────────────────────────────────────────────────────

class TestFailureBackoffOk:

    def test_returns_true_when_no_history(self):
        conn = _make_db()
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            assert failure_backoff_ok("rocketleague") is True

    def test_returns_true_after_a_success(self):
        conn = _make_db()
        _log_success(conn, "rocketleague", _utc(5))
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            assert failure_backoff_ok("rocketleague") is True

    def test_returns_false_immediately_after_one_failure(self):
        """1 failure → 2-min backoff; should block within those 2 minutes."""
        conn = _make_db()
        just_now = _utc(offset_minutes=0)  # failure just happened
        _log_failure(conn, "rocketleague", just_now)
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            assert failure_backoff_ok("rocketleague") is False

    def test_returns_true_after_backoff_expires(self):
        """Failure happened 3 minutes ago, 2-min backoff is expired → allowed."""
        conn = _make_db()
        three_min_ago = _utc(offset_minutes=3)
        _log_failure(conn, "rocketleague", three_min_ago)
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            assert failure_backoff_ok("rocketleague") is True

    def test_exponential_backoff_increases_with_failures(self):
        """Multiple consecutive failures should use longer backoff."""
        conn = _make_db()
        # 3 consecutive failures, the last one was 5 min ago
        # Backoff for 3rd failure: 2^2 * 2min = 8min → still blocked
        _log_failure(conn, "rocketleague", _utc(7))  # 7 min ago (oldest, 3rd failure)
        _log_failure(conn, "rocketleague", _utc(4))  # 4 min ago (2nd failure)
        _log_failure(conn, "rocketleague", _utc(1))  # 1 min ago (most recent, 1st)
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            # 3 consecutive failures → backoff = min(2 * 2^2, 3600) = 8min
            # last failure was 1 min ago → still blocked
            assert failure_backoff_ok("rocketleague") is False

    def test_success_resets_failure_count(self):
        """A success in the middle should reset consecutive count."""
        conn = _make_db()
        # 2 failures, then a success, then 1 failure (most recent)
        _log_failure(conn, "rocketleague", _utc(30))
        _log_failure(conn, "rocketleague", _utc(20))
        _log_success(conn, "rocketleague", _utc(10))
        _log_failure(conn, "rocketleague", _utc(0))  # 1 consecutive failure just now
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            # Only 1 consecutive failure → 2-min backoff → blocked (just happened)
            assert failure_backoff_ok("rocketleague") is False


# ── consecutive_failure_count() ───────────────────────────────────────────────

class TestConsecutiveFailureCount:

    def test_zero_when_no_history(self):
        conn = _make_db()
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            assert consecutive_failure_count("rocketleague") == 0

    def test_zero_when_last_was_success(self):
        conn = _make_db()
        _log_failure(conn, "rocketleague", _utc(10))
        _log_success(conn, "rocketleague", _utc(5))
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            assert consecutive_failure_count("rocketleague") == 0

    def test_counts_consecutive_failures(self):
        conn = _make_db()
        _log_failure(conn, "rocketleague", _utc(10))
        _log_failure(conn, "rocketleague", _utc(8))
        _log_failure(conn, "rocketleague", _utc(6))
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            assert consecutive_failure_count("rocketleague") == 3

    def test_stops_counting_at_success(self):
        conn = _make_db()
        _log_success(conn, "rocketleague", _utc(15))
        _log_failure(conn, "rocketleague", _utc(10))
        _log_failure(conn, "rocketleague", _utc(5))
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            assert consecutive_failure_count("rocketleague") == 2


# ── monthly_post_count() / within_monthly_limit() ────────────────────────────

class TestMonthlyLimit:

    def test_zero_count_when_no_history(self):
        conn = _make_db()
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            assert monthly_post_count("rocketleague") == 0

    def test_counts_only_this_months_successes(self):
        conn = _make_db()
        now = datetime.now(timezone.utc)
        this_month = now.strftime("%Y-%m-%dT12:00:00Z")
        # Post from last month
        last_month_dt = now.replace(day=1) - timedelta(days=1)
        last_month = last_month_dt.strftime("%Y-%m-%dT12:00:00Z")

        _log_success(conn, "rocketleague", this_month, tweet_id="t1")
        _log_success(conn, "rocketleague", this_month, tweet_id="t2")
        _log_success(conn, "rocketleague", last_month, tweet_id="t3")

        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            assert monthly_post_count("rocketleague") == 2

    def test_failures_not_counted(self):
        conn = _make_db()
        this_month = datetime.now(timezone.utc).strftime("%Y-%m-%dT12:00:00Z")
        _log_success(conn, "rocketleague", this_month, tweet_id="t1")
        _log_failure(conn, "rocketleague", this_month)
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            assert monthly_post_count("rocketleague") == 1

    def test_within_limit_when_below_cap(self):
        conn = _make_db()
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            assert within_monthly_limit("rocketleague") is True

    def test_over_limit_returns_false(self):
        """Simulate having reached the monthly cap."""
        conn = _make_db()
        with (
            patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)),
            patch("src.poster.rate_limiter.monthly_post_count", return_value=MONTHLY_LIMIT),
        ):
            assert within_monthly_limit("rocketleague") is False


# ── within_daily_limit() ──────────────────────────────────────────────────────

class TestWithinDailyLimit:

    def test_returns_true_when_no_cap_configured(self):
        """A daily_cap of 0 (not set) means unlimited."""
        conn = _make_db()
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)), \
             patch("src.poster.rate_limiter._posting_config", return_value={}):
            assert within_daily_limit("geometrydash") is True

    def test_returns_true_when_below_cap(self):
        conn = _make_db()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT12:00:00Z")
        _log_success(conn, "geometrydash", now)
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)), \
             patch("src.poster.rate_limiter._posting_config", return_value={"max_daily_posts": 5}):
            assert within_daily_limit("geometrydash") is True

    def test_returns_false_when_cap_reached(self):
        conn = _make_db()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT12:00:00Z")
        for i in range(5):
            _log_success(conn, "geometrydash", now, tweet_id=f"tid{i}")
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)), \
             patch("src.poster.rate_limiter._posting_config", return_value={"max_daily_posts": 5}):
            assert within_daily_limit("geometrydash") is False

    def test_yesterday_posts_do_not_count(self):
        """Posts from yesterday should not count toward today's cap."""
        conn = _make_db()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT12:00:00Z")
        for i in range(10):
            _log_success(conn, "geometrydash", yesterday, tweet_id=f"yt{i}")
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)), \
             patch("src.poster.rate_limiter._posting_config", return_value={"max_daily_posts": 5}):
            assert within_daily_limit("geometrydash") is True


# ── Per-niche config helpers ─────────────────────────────────────────────────

class TestPerNicheConfig:

    def test_min_interval_falls_back_to_global_default(self):
        with patch("src.poster.rate_limiter._posting_config", return_value={}):
            assert _min_interval("geometrydash") == MIN_INTERVAL_S

    def test_min_interval_reads_from_yaml_config(self):
        with patch("src.poster.rate_limiter._posting_config", return_value={"min_interval_seconds": 1800}):
            assert _min_interval("rocketleague") == 1800

    def test_max_interval_falls_back_to_global_default(self):
        from src.poster.rate_limiter import MAX_INTERVAL_S
        with patch("src.poster.rate_limiter._posting_config", return_value={}):
            assert _max_interval("geometrydash") == MAX_INTERVAL_S

    def test_max_interval_reads_from_yaml_config(self):
        with patch("src.poster.rate_limiter._posting_config", return_value={"max_interval_seconds": 4800}):
            assert _max_interval("rocketleague") == 4800
