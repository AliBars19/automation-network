"""
Unit tests for src/poster/queue.py

Uses an in-memory SQLite database and mocks for external dependencies
(TwitterClient, twscrape, format_tweet, prepare_media).
"""
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.base import BaseCollector, RawContent
from src.poster.queue import (
    _PRIORITY,
    _DEFAULT_PRIORITY,
    collect_and_queue,
    post_next,
    skip_stale,
)


# ── In-memory DB fixture ──────────────────────────────────────────────────────

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "database" / "schema.sql"


def _make_in_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()
    return conn


def _insert_source(conn, niche="rocketleague", name="test_source", type_="rss") -> int:
    conn.execute(
        "INSERT INTO sources (niche, name, type, config) VALUES (?, ?, ?, '{}')",
        (niche, name, type_),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()
    return row["id"]


def _insert_queue_row(conn, niche="rocketleague", text="Hello tweet", priority=5, status="queued") -> int:
    conn.execute(
        "INSERT INTO tweet_queue (niche, tweet_text, priority, status) VALUES (?, ?, ?, ?)",
        (niche, text, priority, status),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM tweet_queue WHERE tweet_text = ? ORDER BY id DESC LIMIT 1",
        (text,),
    ).fetchone()
    return row["id"]


def _insert_post_log(conn, niche="rocketleague", tweet_id=None, queue_id=None, posted_at=None) -> None:
    now = posted_at or "2026-03-18T12:00:00Z"
    conn.execute(
        """INSERT INTO post_log (tweet_queue_id, niche, tweet_id, tweet_text, posted_at)
           VALUES (?, ?, ?, 'text', ?)""",
        (queue_id, niche, tweet_id, now),
    )
    conn.commit()


# ── _PRIORITY map sanity ──────────────────────────────────────────────────────

class TestPriorityMap:

    def test_breaking_news_is_priority_1(self):
        assert _PRIORITY["breaking_news"] == 1

    def test_top1_verified_is_priority_1(self):
        assert _PRIORITY["top1_verified"] == 1

    def test_youtube_video_lower_than_patch_notes(self):
        assert _PRIORITY["youtube_video"] > _PRIORITY["patch_notes"]

    def test_default_priority_is_5(self):
        assert _DEFAULT_PRIORITY == 5


# ── collect_and_queue() ───────────────────────────────────────────────────────

class TestCollectAndQueue:

    @pytest.mark.asyncio
    async def test_returns_zero_when_collector_raises(self):
        """If the collector raises, collect_and_queue returns 0."""
        class BrokenCollector(BaseCollector):
            async def collect(self):
                raise RuntimeError("network error")

        collector = BrokenCollector(source_id=1, config={})
        with patch("src.poster.queue.get_db") as mock_get_db:
            result = await collect_and_queue(collector, "rocketleague")

        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_items(self):
        """Empty collect() result returns 0 queued."""
        class EmptyCollector(BaseCollector):
            async def collect(self):
                return []

        collector = EmptyCollector(source_id=1, config={})
        with patch("src.poster.queue.get_db") as mock_get_db:
            result = await collect_and_queue(collector, "rocketleague")

        assert result == 0

    @pytest.mark.asyncio
    async def test_queues_new_item(self):
        """A new item with a valid template should be inserted into queue."""
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        item = RawContent(
            source_id=source_id,
            external_id="ext_001",
            niche="rocketleague",
            content_type="breaking_news",
            title="Season 14 is live",
            url="https://rocketleague.com/news/s14",
            body="Season 14 has launched.",
        )

        class OneItemCollector(BaseCollector):
            async def collect(self):
                return [item]

        collector = OneItemCollector(source_id=source_id, config={})

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.format_tweet", return_value="Season 14 is live! https://rocketleague.com/news/s14"),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.is_similar_story", return_value=False),
            patch("src.poster.queue.url_already_queued", return_value=False),
        ):
            result = await collect_and_queue(collector, "rocketleague")

        assert result == 1
        rows = conn.execute("SELECT * FROM tweet_queue WHERE niche = 'rocketleague'").fetchall()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_skips_duplicate_external_id(self):
        """Second run with same external_id should not double-queue."""
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        item = RawContent(
            source_id=source_id,
            external_id="dup_001",
            niche="rocketleague",
            content_type="breaking_news",
            title="Duplicate title",
            url="https://example.com/dup",
        )

        class SameItemCollector(BaseCollector):
            async def collect(self):
                return [item]

        collector = SameItemCollector(source_id=source_id, config={})

        # Use side_effect so each call to get_db() produces a fresh context manager
        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.format_tweet", return_value="Duplicate title"),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.is_similar_story", return_value=False),
            patch("src.poster.queue.url_already_queued", return_value=False),
        ):
            first = await collect_and_queue(collector, "rocketleague")
            second = await collect_and_queue(collector, "rocketleague")

        assert first == 1
        assert second == 0

    @pytest.mark.asyncio
    async def test_queues_retweet_signal_when_format_returns_none(self):
        """Content with retweet_id but no template → queues RETWEET:id signal."""
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        item = RawContent(
            source_id=source_id,
            external_id="rt_001",
            niche="rocketleague",
            content_type="official_tweet",
            title="",
            url="",
            metadata={"retweet_id": "99999"},
        )

        class RTCollector(BaseCollector):
            async def collect(self):
                return [item]

        collector = RTCollector(source_id=source_id, config={})

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.format_tweet", return_value=None),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
        ):
            result = await collect_and_queue(collector, "rocketleague")

        assert result == 1
        row = conn.execute("SELECT tweet_text FROM tweet_queue WHERE niche = 'rocketleague'").fetchone()
        assert row["tweet_text"] == "RETWEET:99999"

    @pytest.mark.asyncio
    async def test_skips_item_with_no_template_and_no_retweet_id(self):
        """No template + no retweet_id → item is discarded silently."""
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        item = RawContent(
            source_id=source_id,
            external_id="noop_001",
            niche="rocketleague",
            content_type="unknown_type",
            title="Ignored",
        )

        class NoopCollector(BaseCollector):
            async def collect(self):
                return [item]

        collector = NoopCollector(source_id=source_id, config={})

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.format_tweet", return_value=None),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
        ):
            result = await collect_and_queue(collector, "rocketleague")

        assert result == 0

    @pytest.mark.asyncio
    async def test_skips_similar_story(self):
        """Item that passes dedup but is similar to existing queued tweet is skipped."""
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        item = RawContent(
            source_id=source_id,
            external_id="similar_001",
            niche="rocketleague",
            content_type="breaking_news",
            title="RLCS starts now",
            url="https://example.com/rlcs",
        )

        class SimilarCollector(BaseCollector):
            async def collect(self):
                return [item]

        collector = SimilarCollector(source_id=source_id, config={})

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.format_tweet", return_value="RLCS starts now!"),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.is_similar_story", return_value=True),
            patch("src.poster.queue.url_already_queued", return_value=False),
        ):
            result = await collect_and_queue(collector, "rocketleague")

        assert result == 0


# ── post_next() ───────────────────────────────────────────────────────────────

class TestPostNext:

    def test_returns_false_when_queue_empty(self):
        conn = _make_in_memory_db()
        client = MagicMock()

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
        ):
            result = post_next("rocketleague", client)

        assert result is False

    def test_returns_false_when_monthly_limit_reached(self):
        conn = _make_in_memory_db()
        _insert_queue_row(conn)
        client = MagicMock()

        with (
            patch("src.poster.queue.within_monthly_limit", return_value=False),
        ):
            result = post_next("rocketleague", client)

        assert result is False

    def test_returns_false_when_failure_backoff_active(self):
        conn = _make_in_memory_db()
        _insert_queue_row(conn)
        client = MagicMock()

        with (
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=False),
        ):
            result = post_next("rocketleague", client)

        assert result is False

    def test_returns_false_outside_posting_window(self):
        conn = _make_in_memory_db()
        _insert_queue_row(conn, priority=5)
        client = MagicMock()

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=False),
            patch("src.poster.queue.can_post", return_value=True),
        ):
            result = post_next("rocketleague", client)

        assert result is False

    def test_breaking_news_bypasses_window_check(self):
        """Priority-1 items should skip the window and can_post checks."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, priority=1, text="BREAKING: Top 1 verified!")
        client = MagicMock()
        client.post_tweet.return_value = "tweet_123"

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window") as mock_win,
            patch("src.poster.queue.can_post") as mock_can,
        ):
            result = post_next("rocketleague", client)

        assert result is True
        mock_win.assert_not_called()
        mock_can.assert_not_called()

    def test_posts_tweet_and_marks_posted(self):
        """Successful post_tweet should mark row as posted and return True."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, priority=5, text="Normal tweet")
        client = MagicMock()
        client.post_tweet.return_value = "tweet_999"

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
        ):
            result = post_next("rocketleague", client)

        assert result is True
        row = conn.execute("SELECT status FROM tweet_queue WHERE niche = 'rocketleague'").fetchone()
        assert row["status"] == "posted"

    def test_marks_failed_when_post_tweet_returns_none(self):
        """If post_tweet returns None, row should be marked failed."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, priority=5, text="Failing tweet")
        client = MagicMock()
        client.post_tweet.return_value = None

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._check_failure_alert"),
        ):
            result = post_next("rocketleague", client)

        assert result is False
        row = conn.execute("SELECT status FROM tweet_queue WHERE niche = 'rocketleague'").fetchone()
        assert row["status"] == "failed"

    def test_dispatches_retweet_signal(self):
        """RETWEET:{id} text should call client.retweet() not post_tweet()."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, text="RETWEET:55555", priority=2)
        client = MagicMock()
        client.retweet.return_value = True

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
        ):
            result = post_next("rocketleague", client)

        assert result is True
        client.retweet.assert_called_once_with("55555")
        client.post_tweet.assert_not_called()

    def test_marks_failed_when_retweet_fails(self):
        """Failed retweet should mark row failed and return False."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, text="RETWEET:66666", priority=2)
        client = MagicMock()
        client.retweet.return_value = False

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._check_failure_alert"),
        ):
            result = post_next("rocketleague", client)

        assert result is False
        row = conn.execute("SELECT status FROM tweet_queue WHERE niche = 'rocketleague'").fetchone()
        assert row["status"] == "failed"

    def test_returns_false_when_rate_limited(self):
        """can_post() returning False should short-circuit posting."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, priority=5)
        client = MagicMock()

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=False),
        ):
            result = post_next("rocketleague", client)

        assert result is False
        client.post_tweet.assert_not_called()


# ── skip_stale() ──────────────────────────────────────────────────────────────

class TestSkipStale:

    def test_skips_old_queued_rows(self):
        """Rows older than max_age_hours should be marked skipped."""
        conn = _make_in_memory_db()
        # Insert a row with an artificially old created_at
        conn.execute(
            """INSERT INTO tweet_queue (niche, tweet_text, status, created_at)
               VALUES ('rocketleague', 'Old tweet', 'queued', '2020-01-01T00:00:00Z')"""
        )
        conn.commit()

        with patch("src.poster.queue.get_db", return_value=_ctx(conn)):
            count = skip_stale("rocketleague", max_age_hours=6)

        assert count == 1
        row = conn.execute("SELECT status FROM tweet_queue").fetchone()
        assert row["status"] == "skipped"

    def test_does_not_skip_recent_rows(self):
        """Recent queued rows should not be marked skipped."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, text="Brand new tweet")

        with patch("src.poster.queue.get_db", return_value=_ctx(conn)):
            count = skip_stale("rocketleague", max_age_hours=6)

        assert count == 0
        row = conn.execute("SELECT status FROM tweet_queue").fetchone()
        assert row["status"] == "queued"

    def test_returns_zero_when_queue_empty(self):
        conn = _make_in_memory_db()

        with patch("src.poster.queue.get_db", return_value=_ctx(conn)):
            count = skip_stale("rocketleague", max_age_hours=6)

        assert count == 0


# ── Context manager helper ────────────────────────────────────────────────────

from contextlib import contextmanager


@contextmanager
def _ctx(conn):
    """Wrap an existing connection so get_db() context manager works with our test conn."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
