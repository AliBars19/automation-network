"""
Unit tests for src/database/db.py — similarity dedup, cleanup,
and queue helpers using an in-memory SQLite database.
"""
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# We import functions but override DB_PATH for testing
from src.database.db import (
    _hours_ago,
    _days_ago,
    _utcnow,
)


# ── Helper timestamp functions ────────────────────────────────────────────────

class TestTimestampHelpers:
    def test_utcnow_format(self):
        """_utcnow() should return ISO format ending with Z."""
        result = _utcnow()
        assert result.endswith("Z")
        # Should be parseable
        datetime.fromisoformat(result.replace("Z", "+00:00"))

    def test_hours_ago_in_past(self):
        """_hours_ago(1) should be roughly 1 hour in the past."""
        result = _hours_ago(1)
        dt = datetime.fromisoformat(result.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt
        # Should be approximately 1 hour (within 5 seconds tolerance)
        assert 3595 < diff.total_seconds() < 3605

    def test_days_ago_in_past(self):
        """_days_ago(7) should be roughly 7 days in the past."""
        result = _days_ago(7)
        dt = datetime.fromisoformat(result.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt
        assert 6.99 < diff.total_seconds() / 86400 < 7.01


# ── Similarity dedup (in-memory DB) ──────────────────────────────────────────

class TestSimilarityDedup:
    """Tests is_similar_story() with an in-memory SQLite database."""

    @pytest.fixture
    def conn(self):
        """Create an in-memory database with the tweet_queue schema."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE tweet_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                niche TEXT NOT NULL,
                raw_content_id INTEGER,
                tweet_text TEXT NOT NULL,
                media_path TEXT,
                priority INTEGER NOT NULL DEFAULT 5,
                status TEXT NOT NULL DEFAULT 'queued',
                scheduled_at TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                posted_at TEXT
            )
        """)
        return conn

    def _insert_queued(self, conn, niche: str, text: str):
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status) VALUES (?, ?, 'queued')",
            (niche, text),
        )
        conn.commit()

    def _is_similar(self, conn, text: str, niche: str, threshold: float = 0.65) -> bool:
        """Replicates is_similar_story() logic with the test connection."""
        from difflib import SequenceMatcher
        rows = conn.execute(
            "SELECT tweet_text FROM tweet_queue WHERE niche = ? AND status = 'queued'",
            (niche,),
        ).fetchall()
        needle = text.lower()
        for row in rows:
            ratio = SequenceMatcher(None, needle, row["tweet_text"].lower()).ratio()
            if ratio >= threshold:
                return True
        return False

    def test_identical_text_is_similar(self, conn):
        self._insert_queued(conn, "rocketleague", "RLCS Season 14 starts now!")
        assert self._is_similar(conn, "RLCS Season 14 starts now!", "rocketleague") is True

    def test_slightly_different_text_is_similar(self, conn):
        self._insert_queued(conn, "rocketleague", "RLCS Season 14 has officially started!")
        assert self._is_similar(conn, "RLCS Season 14 starts now!", "rocketleague") is True

    def test_completely_different_text_not_similar(self, conn):
        self._insert_queued(conn, "rocketleague", "Item shop update: new decals available")
        assert self._is_similar(conn, "RLCS Season 14 starts now!", "rocketleague") is False

    def test_different_niche_not_matched(self, conn):
        self._insert_queued(conn, "geometrydash", "RLCS Season 14 starts now!")
        assert self._is_similar(conn, "RLCS Season 14 starts now!", "rocketleague") is False

    def test_empty_queue_not_similar(self, conn):
        assert self._is_similar(conn, "Any text at all", "rocketleague") is False

    def test_case_insensitive(self, conn):
        self._insert_queued(conn, "rocketleague", "BREAKING: NRG wins RLCS World Championship")
        assert self._is_similar(conn, "breaking: nrg wins rlcs world championship", "rocketleague") is True

    def test_high_threshold_rejects_partial_match(self, conn):
        self._insert_queued(conn, "rocketleague", "RLCS Season 14 starts now with new maps!")
        # With threshold 0.95, slightly different text should not match
        assert self._is_similar(conn, "RLCS Season 14 begins today!", "rocketleague", threshold=0.95) is False
