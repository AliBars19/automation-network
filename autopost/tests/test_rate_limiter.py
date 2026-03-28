"""
Unit tests for src/poster/rate_limiter.py — posting window, monthly limit,
failure backoff, and jitter.
"""
import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from src.poster.rate_limiter import (
    MONTHLY_LIMIT,
    POSTING_WINDOW_END,
    POSTING_WINDOW_START,
    _BACKOFF_BASE_S,
    _BACKOFF_CAP_S,
    jitter_delay,
    within_posting_window,
    MIN_INTERVAL_S,
    MAX_INTERVAL_S,
    JITTER_MAX_S,
)


# ── within_posting_window() ───────────────────────────────────────────────────

class TestPostingWindow:
    def test_within_window_afternoon(self):
        """Hour 18 UTC (1 PM EST) should be within 14–04 window."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 9, 18, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() is True

    def test_within_window_late_night(self):
        """Hour 1 UTC (8 PM EST) should be within 14–04 window (wraps past midnight)."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 9, 1, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() is True

    def test_outside_window_morning(self):
        """Hour 8 UTC (3 AM EST) should be outside 14–04 window."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 9, 8, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() is False

    def test_breaking_news_bypasses_window(self):
        """Breaking news should always return True, even at 8 AM UTC."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 9, 8, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window(is_breaking=True) is True

    def test_window_start_boundary_included(self):
        """Exactly 14:00 UTC should be within window."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 9, 14, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() is True

    def test_window_end_boundary_excluded(self):
        """Exactly 04:00 UTC should be outside window (exclusive end)."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 9, 4, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() is False


# ── jitter_delay() ────────────────────────────────────────────────────────────

class TestJitterDelay:
    def test_within_expected_range(self):
        """Jitter should be between MIN_INTERVAL and MAX_INTERVAL + JITTER_MAX."""
        for _ in range(50):
            delay = jitter_delay()
            assert delay >= MIN_INTERVAL_S
            assert delay <= MAX_INTERVAL_S + JITTER_MAX_S

    def test_produces_variety(self):
        """Multiple calls should produce different values (randomness check)."""
        delays = {jitter_delay() for _ in range(20)}
        assert len(delays) > 1  # at least 2 distinct values


# ── Constants sanity checks ───────────────────────────────────────────────────

class TestConstants:
    def test_window_hours_valid(self):
        assert 0 <= POSTING_WINDOW_START < 24
        assert 0 <= POSTING_WINDOW_END < 24
        # Window may wrap past midnight (e.g. 14-04), so START > END is valid
        assert POSTING_WINDOW_START != POSTING_WINDOW_END

    def test_monthly_limit_positive(self):
        assert MONTHLY_LIMIT > 0

    def test_interval_ordering(self):
        assert MIN_INTERVAL_S < MAX_INTERVAL_S

    def test_backoff_cap_exceeds_base(self):
        assert _BACKOFF_CAP_S > _BACKOFF_BASE_S
