"""
Unit tests for src/database/db.py

Uses an in-memory SQLite database and patches DB_PATH to avoid touching
the real database. Tests cover all public functions.
"""
import json
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.database.db import (
    add_to_queue,
    cleanup_old_records,
    disable_source,
    get_queued_tweets,
    get_sources,
    init_db,
    insert_raw_content,
    is_similar_story,
    mark_failed,
    mark_posted,
    mark_skipped,
    recent_source_error_count,
    record_source_error,
    upsert_source,
    url_already_queued,
)
from src.collectors.base import RawContent

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "database" / "schema.sql"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()
    return conn


def _make_raw_content(source_id: int, external_id: str = "ext_1", niche: str = "rocketleague") -> RawContent:
    return RawContent(
        source_id=source_id,
        external_id=external_id,
        niche=niche,
        content_type="breaking_news",
        title="Test title",
        url="https://example.com/test",
        body="Test body",
        image_url="",
        author="TestAuthor",
        score=0,
        metadata={"key": "value"},
    )


# ── init_db() ─────────────────────────────────────────────────────────────────

class TestInitDb:
    def test_creates_tables_idempotently(self, tmp_path):
        """init_db() should create tables and be safe to call twice."""
        db_file = tmp_path / "test.db"
        with patch("src.database.db.DB_PATH", db_file):
            init_db()
            init_db()  # second call should not raise
        # Verify tables exist
        conn = sqlite3.connect(str(db_file))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "sources" in tables
        assert "raw_content" in tables
        assert "tweet_queue" in tables
        assert "post_log" in tables


# ── upsert_source() / get_sources() ──────────────────────────────────────────

class TestSources:

    def test_upsert_inserts_and_returns_id(self):
        conn = _make_db()
        sid = upsert_source(conn, "rocketleague", "Test RSS", "rss", {"url": "https://example.com"})
        assert isinstance(sid, int)
        assert sid > 0

    def test_upsert_is_idempotent(self):
        """Calling upsert twice with the same niche+name returns the same ID."""
        conn = _make_db()
        sid1 = upsert_source(conn, "rocketleague", "My RSS", "rss", {})
        sid2 = upsert_source(conn, "rocketleague", "My RSS", "rss", {})
        assert sid1 == sid2

    def test_get_sources_returns_enabled_only(self):
        conn = _make_db()
        upsert_source(conn, "rocketleague", "Enabled Source", "rss", {})
        upsert_source(conn, "rocketleague", "Disabled Source", "rss", {})
        # Disable the second source
        conn.execute("UPDATE sources SET enabled = 0 WHERE name = 'Disabled Source'")
        conn.commit()

        rows = get_sources(conn, "rocketleague")
        names = [r["name"] for r in rows]
        assert "Enabled Source" in names
        assert "Disabled Source" not in names

    def test_get_sources_filters_by_niche(self):
        conn = _make_db()
        upsert_source(conn, "rocketleague", "RL Source", "rss", {})
        upsert_source(conn, "geometrydash", "GD Source", "rss", {})

        rows = get_sources(conn, "rocketleague")
        assert all(r["niche"] == "rocketleague" for r in rows)
        assert len(rows) == 1


# ── insert_raw_content() ──────────────────────────────────────────────────────

class TestInsertRawContent:

    def test_inserts_new_content(self):
        conn = _make_db()
        source_id = upsert_source(conn, "rocketleague", "Test", "rss", {})
        item = _make_raw_content(source_id)
        rid, is_new = insert_raw_content(conn, item)
        assert is_new is True
        assert rid > 0

    def test_duplicate_returns_is_new_false(self):
        conn = _make_db()
        source_id = upsert_source(conn, "rocketleague", "Test", "rss", {})
        item = _make_raw_content(source_id, external_id="dup_ext")
        rid1, is_new1 = insert_raw_content(conn, item)
        rid2, is_new2 = insert_raw_content(conn, item)
        assert is_new1 is True
        assert is_new2 is False
        assert rid1 == rid2

    def test_metadata_stored_as_json(self):
        conn = _make_db()
        source_id = upsert_source(conn, "rocketleague", "Test", "rss", {})
        item = _make_raw_content(source_id, external_id="json_ext")
        item.metadata = {"foo": "bar", "num": 42}
        insert_raw_content(conn, item)
        row = conn.execute("SELECT metadata FROM raw_content WHERE external_id = 'json_ext'").fetchone()
        parsed = json.loads(row["metadata"])
        assert parsed["foo"] == "bar"
        assert parsed["num"] == 42


# ── add_to_queue() / get_queued_tweets() ──────────────────────────────────────

class TestQueue:

    def test_add_and_retrieve(self):
        conn = _make_db()
        source_id = upsert_source(conn, "rocketleague", "Src", "rss", {})
        item = _make_raw_content(source_id)
        content_id, _ = insert_raw_content(conn, item)

        qid = add_to_queue(conn, "rocketleague", "Hello tweet!", raw_content_id=content_id, priority=3)
        assert qid > 0

        rows = get_queued_tweets(conn, "rocketleague", limit=5)
        assert len(rows) == 1
        assert rows[0]["tweet_text"] == "Hello tweet!"

    def test_get_queued_ordered_by_priority(self):
        conn = _make_db()
        add_to_queue(conn, "rocketleague", "Low priority", priority=5)
        add_to_queue(conn, "rocketleague", "High priority", priority=1)

        rows = get_queued_tweets(conn, "rocketleague", limit=10)
        assert rows[0]["tweet_text"] == "High priority"
        assert rows[1]["tweet_text"] == "Low priority"

    def test_get_queued_skips_future_scheduled(self):
        """Rows with a future scheduled_at should not be returned."""
        conn = _make_db()
        add_to_queue(conn, "rocketleague", "Future tweet", scheduled_at="2099-01-01T00:00:00Z")
        rows = get_queued_tweets(conn, "rocketleague")
        assert len(rows) == 0

    def test_get_queued_includes_past_scheduled(self):
        """Rows with a past scheduled_at should be included."""
        conn = _make_db()
        add_to_queue(conn, "rocketleague", "Past tweet", scheduled_at="2020-01-01T00:00:00Z")
        rows = get_queued_tweets(conn, "rocketleague")
        assert len(rows) == 1

    def test_get_queued_filters_by_niche(self):
        conn = _make_db()
        add_to_queue(conn, "rocketleague", "RL tweet")
        add_to_queue(conn, "geometrydash", "GD tweet")
        rows = get_queued_tweets(conn, "rocketleague")
        assert len(rows) == 1
        assert rows[0]["tweet_text"] == "RL tweet"


# ── mark_posted() / mark_failed() / mark_skipped() ───────────────────────────

class TestMarkFunctions:

    def test_mark_posted_updates_status(self):
        conn = _make_db()
        qid = add_to_queue(conn, "rocketleague", "Posted tweet")
        mark_posted(conn, qid, "tweet_id_123")
        row = conn.execute("SELECT status, posted_at FROM tweet_queue WHERE id = ?", (qid,)).fetchone()
        assert row["status"] == "posted"
        assert row["posted_at"] is not None

    def test_mark_posted_writes_to_post_log(self):
        conn = _make_db()
        qid = add_to_queue(conn, "rocketleague", "Logged tweet")
        mark_posted(conn, qid, "tweet_id_456")
        log_row = conn.execute("SELECT tweet_id FROM post_log WHERE tweet_queue_id = ?", (qid,)).fetchone()
        assert log_row["tweet_id"] == "tweet_id_456"

    def test_mark_failed_updates_status(self):
        conn = _make_db()
        qid = add_to_queue(conn, "rocketleague", "Failed tweet")
        mark_failed(conn, qid, "API returned 403")
        row = conn.execute("SELECT status FROM tweet_queue WHERE id = ?", (qid,)).fetchone()
        assert row["status"] == "failed"

    def test_mark_failed_writes_to_post_log_with_null_tweet_id(self):
        conn = _make_db()
        qid = add_to_queue(conn, "rocketleague", "Failed tweet")
        mark_failed(conn, qid, "some error")
        log_row = conn.execute("SELECT tweet_id, error FROM post_log WHERE tweet_queue_id = ?", (qid,)).fetchone()
        assert log_row["tweet_id"] is None
        assert "some error" in log_row["error"]

    def test_mark_skipped_updates_status(self):
        conn = _make_db()
        qid = add_to_queue(conn, "rocketleague", "Skipped tweet")
        mark_skipped(conn, qid)
        row = conn.execute("SELECT status FROM tweet_queue WHERE id = ?", (qid,)).fetchone()
        assert row["status"] == "skipped"


# ── Source error tracking ─────────────────────────────────────────────────────

class TestSourceErrors:

    def test_record_and_count_errors(self):
        conn = _make_db()
        source_id = upsert_source(conn, "rocketleague", "ErrorSrc", "rss", {})
        record_source_error(conn, source_id, "connection timed out")
        count = recent_source_error_count(conn, source_id, hours=1)
        assert count == 1

    def test_zero_errors_initially(self):
        conn = _make_db()
        source_id = upsert_source(conn, "rocketleague", "CleanSrc", "rss", {})
        assert recent_source_error_count(conn, source_id, hours=1) == 0

    def test_disable_source(self):
        conn = _make_db()
        source_id = upsert_source(conn, "rocketleague", "ToDisable", "rss", {})
        disable_source(conn, source_id)
        row = conn.execute("SELECT enabled FROM sources WHERE id = ?", (source_id,)).fetchone()
        assert row["enabled"] == 0


# ── cleanup_old_records() ─────────────────────────────────────────────────────

class TestCleanup:

    def test_deletes_old_posted_rows(self):
        conn = _make_db()
        conn.execute(
            """INSERT INTO tweet_queue (niche, tweet_text, status, created_at)
               VALUES ('rocketleague', 'Old posted', 'posted', '2020-01-01T00:00:00Z')"""
        )
        conn.commit()
        result = cleanup_old_records(conn, days=30)
        assert result["tweet_queue"] == 1
        assert conn.execute("SELECT COUNT(*) FROM tweet_queue").fetchone()[0] == 0

    def test_does_not_delete_recent_rows(self):
        conn = _make_db()
        add_to_queue(conn, "rocketleague", "Recent posted")
        conn.execute("UPDATE tweet_queue SET status = 'posted'")
        conn.commit()
        result = cleanup_old_records(conn, days=30)
        assert result["tweet_queue"] == 0

    def test_deletes_skipped_and_failed_rows(self):
        conn = _make_db()
        for status in ("skipped", "failed"):
            conn.execute(
                f"""INSERT INTO tweet_queue (niche, tweet_text, status, created_at)
                   VALUES ('rocketleague', 'Old {status}', '{status}', '2020-01-01T00:00:00Z')"""
            )
        conn.commit()
        result = cleanup_old_records(conn, days=30)
        assert result["tweet_queue"] == 2

    def test_does_not_delete_queued_rows(self):
        """Active 'queued' rows should never be deleted by cleanup."""
        conn = _make_db()
        conn.execute(
            """INSERT INTO tweet_queue (niche, tweet_text, status, created_at)
               VALUES ('rocketleague', 'Still queued', 'queued', '2020-01-01T00:00:00Z')"""
        )
        conn.commit()
        result = cleanup_old_records(conn, days=30)
        assert result["tweet_queue"] == 0


# ── url_already_queued() ──────────────────────────────────────────────────────

class TestUrlAlreadyQueued:

    def test_returns_false_for_empty_url(self):
        conn = _make_db()
        assert url_already_queued(conn, "", 1) is False

    def test_returns_false_when_no_match(self):
        conn = _make_db()
        source_id = upsert_source(conn, "rocketleague", "Src", "rss", {})
        item = _make_raw_content(source_id, "ext_u1")
        content_id, _ = insert_raw_content(conn, item)
        # No queue entry yet
        assert url_already_queued(conn, "https://example.com/test", content_id) is False

    def test_returns_true_when_same_url_already_queued_by_different_source(self):
        conn = _make_db()
        src1 = upsert_source(conn, "rocketleague", "Src1", "rss", {})
        src2 = upsert_source(conn, "rocketleague", "Src2", "scraper", {})

        # Source 1 already queued this URL
        item1 = RawContent(
            source_id=src1, external_id="ext_a", niche="rocketleague",
            content_type="breaking_news", url="https://example.com/same-article",
        )
        cid1, _ = insert_raw_content(conn, item1)
        add_to_queue(conn, "rocketleague", "Article from Src1", raw_content_id=cid1)

        # Source 2 tries to queue the same URL
        item2 = RawContent(
            source_id=src2, external_id="ext_b", niche="rocketleague",
            content_type="breaking_news", url="https://example.com/same-article",
        )
        cid2, _ = insert_raw_content(conn, item2)

        assert url_already_queued(conn, "https://example.com/same-article", cid2) is True

    def test_returns_false_for_same_content_id(self):
        """Should not flag itself (id != content_id check)."""
        conn = _make_db()
        source_id = upsert_source(conn, "rocketleague", "Src", "rss", {})
        item = _make_raw_content(source_id, "ext_self")
        cid, _ = insert_raw_content(conn, item)
        add_to_queue(conn, "rocketleague", "Self-check", raw_content_id=cid)
        # Same content_id — should not be considered a duplicate
        assert url_already_queued(conn, item.url, cid) is False
