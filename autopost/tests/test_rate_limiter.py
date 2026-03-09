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
    def test_within_window(self):
        """Hour 12 UTC should be within 08–22 window."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() is True

    def test_outside_window_early(self):
        """Hour 3 UTC should be outside 08–22 window."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 9, 3, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() is False

    def test_outside_window_late(self):
        """Hour 23 UTC should be outside 08–22 window."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 9, 23, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() is False

    def test_breaking_news_bypasses_window(self):
        """Breaking news should always return True, even at 3 AM."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 9, 3, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window(is_breaking=True) is True

    def test_window_start_boundary_included(self):
        """Exactly 08:00 should be within window."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 9, 8, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() is True

    def test_window_end_boundary_excluded(self):
        """Exactly 22:00 should be outside window (exclusive end)."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 9, 22, 0, tzinfo=timezone.utc)
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
        assert 0 < POSTING_WINDOW_END <= 24
        assert POSTING_WINDOW_START < POSTING_WINDOW_END

    def test_monthly_limit_positive(self):
        assert MONTHLY_LIMIT > 0

    def test_interval_ordering(self):
        assert MIN_INTERVAL_S < MAX_INTERVAL_S

    def test_backoff_cap_exceeds_base(self):
        assert _BACKOFF_CAP_S > _BACKOFF_BASE_S
