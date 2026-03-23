"""
Additional tests for src/database/db.py covering missing lines:
  - Lines 28-30: get_db() rollback path (exception during yield)
  - Lines 223-236: is_similar_story() actual function (not a replica)
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from src.database.db import is_similar_story

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "database" / "schema.sql"


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()
    return conn


# ── get_db() rollback path (lines 28-30) ──────────────────────────────────────

class TestGetDbRollback:
    """Verify that get_db() rolls back on exception and re-raises."""

    def test_rollback_on_exception(self, tmp_path):
        from src.database.db import get_db, add_to_queue
        db_file = tmp_path / "test_rollback.db"

        with patch("src.database.db.DB_PATH", db_file):
            from src.database.db import init_db
            init_db()

        # Insert inside a failing context — should be rolled back
        with patch("src.database.db.DB_PATH", db_file):
            try:
                with get_db() as conn:
                    conn.execute(
                        "INSERT INTO tweet_queue (niche, tweet_text) VALUES (?, ?)",
                        ("rocketleague", "Should be rolled back"),
                    )
                    raise ValueError("deliberate test error")
            except ValueError:
                pass

        # Verify the row was NOT committed (rollback happened)
        verify_conn = sqlite3.connect(str(db_file))
        count = verify_conn.execute("SELECT COUNT(*) FROM tweet_queue").fetchone()[0]
        verify_conn.close()
        assert count == 0

    def test_exception_re_raised_after_rollback(self, tmp_path):
        from src.database.db import get_db
        db_file = tmp_path / "test_reraise.db"

        with patch("src.database.db.DB_PATH", db_file):
            from src.database.db import init_db
            init_db()

        with patch("src.database.db.DB_PATH", db_file):
            with pytest.raises(RuntimeError, match="test exception"):
                with get_db() as conn:
                    raise RuntimeError("test exception")


# ── is_similar_story() — actual function (lines 223-236) ─────────────────────

class TestIsSimilarStory:
    """Tests calling is_similar_story() directly (not a replicated version)."""

    def _insert_queued(self, conn: sqlite3.Connection, niche: str, text: str) -> None:
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status) VALUES (?, ?, 'queued')",
            (niche, text),
        )
        conn.commit()

    def test_identical_text_is_similar(self):
        conn = _make_db()
        self._insert_queued(conn, "rocketleague", "RLCS Season 14 starts now!")
        assert is_similar_story(conn, "RLCS Season 14 starts now!", "rocketleague") is True

    def test_slightly_different_text_is_similar(self):
        conn = _make_db()
        self._insert_queued(conn, "rocketleague", "RLCS Season 14 has officially started!")
        assert is_similar_story(conn, "RLCS Season 14 starts now!", "rocketleague") is True

    def test_completely_different_text_not_similar(self):
        conn = _make_db()
        self._insert_queued(conn, "rocketleague", "Item shop update: new decals available")
        assert is_similar_story(conn, "RLCS Season 14 starts now!", "rocketleague") is False

    def test_different_niche_not_matched(self):
        conn = _make_db()
        self._insert_queued(conn, "geometrydash", "RLCS Season 14 starts now!")
        assert is_similar_story(conn, "RLCS Season 14 starts now!", "rocketleague") is False

    def test_empty_queue_returns_false(self):
        conn = _make_db()
        assert is_similar_story(conn, "Any text at all", "rocketleague") is False

    def test_case_insensitive_matching(self):
        conn = _make_db()
        self._insert_queued(conn, "rocketleague", "BREAKING: NRG wins RLCS World Championship")
        assert is_similar_story(conn, "breaking: nrg wins rlcs world championship", "rocketleague") is True

    def test_high_threshold_rejects_partial_match(self):
        conn = _make_db()
        self._insert_queued(conn, "rocketleague", "RLCS Season 14 starts now with new maps!")
        assert is_similar_story(conn, "RLCS Season 14 begins today!", "rocketleague", threshold=0.95) is False

    def test_only_queued_status_checked(self):
        """Rows with status != 'queued' should not be compared."""
        conn = _make_db()
        # Insert a posted row with identical text
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status) VALUES (?, ?, 'posted')",
            ("rocketleague", "RLCS Season 14 starts now!"),
        )
        conn.commit()
        # Should not match posted rows
        assert is_similar_story(conn, "RLCS Season 14 starts now!", "rocketleague") is False

    def test_custom_threshold_parameter(self):
        """With a low threshold (0.1), near-anything should match."""
        conn = _make_db()
        self._insert_queued(conn, "rocketleague", "Hello world")
        # Very low threshold — any text with common chars will match
        result = is_similar_story(conn, "Hello world", "rocketleague", threshold=0.1)
        assert result is True

    def test_multiple_queued_items_any_match_returns_true(self):
        conn = _make_db()
        self._insert_queued(conn, "rocketleague", "Unrelated news article here")
        self._insert_queued(conn, "rocketleague", "RLCS Season 14 starts now!")
        # Should find the matching one among multiple
        assert is_similar_story(conn, "RLCS Season 14 starts now!", "rocketleague") is True
