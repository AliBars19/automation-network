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
    _engagement_followup,
    _retweet_context,
    _split_url,
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

    def test_monitored_tweet_priority_is_6(self):
        """monitored_tweet must have priority 6 (below youtube_video at 5)."""
        assert _PRIORITY["monitored_tweet"] == 6

    def test_monitored_tweet_lower_priority_than_youtube_video(self):
        """monitored_tweet posts after youtube_video."""
        assert _PRIORITY["monitored_tweet"] > _PRIORITY["youtube_video"]

    def test_monitored_tweet_lower_priority_than_official_tweet(self):
        """monitored_tweet posts after official_tweet (p2)."""
        assert _PRIORITY["monitored_tweet"] > _PRIORITY["official_tweet"]

    def test_all_expected_content_types_present(self):
        """Spot-check that the five changed content types are all mapped."""
        for ct in ("official_tweet", "robtop_tweet", "monitored_tweet", "breaking_news", "top1_verified"):
            assert ct in _PRIORITY, f"{ct} missing from _PRIORITY"


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
            metadata={"retweet_id": "99999", "account": "RLEsports"},
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
        assert row["tweet_text"] == "RETWEET:99999:RLEsports"

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

    @pytest.mark.asyncio
    async def test_skips_item_when_url_already_queued_from_other_source(self):
        """Cross-source URL dedup: item whose URL is already queued is skipped."""
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        item = RawContent(
            source_id=source_id,
            external_id="url_dup_001",
            niche="rocketleague",
            content_type="breaking_news",
            title="Duplicate URL story",
            url="https://example.com/already-queued",
        )

        class UrlDupCollector(BaseCollector):
            async def collect(self):
                return [item]

        collector = UrlDupCollector(source_id=source_id, config={})

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.url_already_queued", return_value=True),
        ):
            result = await collect_and_queue(collector, "rocketleague")

        assert result == 0
        rows = conn.execute("SELECT * FROM tweet_queue WHERE niche = 'rocketleague'").fetchall()
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_monitored_tweet_queued_with_priority_6(self):
        """monitored_tweet content type must be enqueued at priority 6."""
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        item = RawContent(
            source_id=source_id,
            external_id="mt_001",
            niche="rocketleague",
            content_type="monitored_tweet",
            title="Player says something",
            url="https://x.com/player/status/1",
        )

        class MonitoredCollector(BaseCollector):
            async def collect(self):
                return [item]

        collector = MonitoredCollector(source_id=source_id, config={})

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.format_tweet", return_value="Player says something\n\nhttps://x.com/player/status/1"),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.is_similar_story", return_value=False),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.passes_quality_gate", return_value=True),
        ):
            result = await collect_and_queue(collector, "rocketleague")

        assert result == 1
        row = conn.execute("SELECT priority FROM tweet_queue WHERE niche = 'rocketleague'").fetchone()
        assert row["priority"] == 6


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
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
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
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
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
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
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
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
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
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
            patch("src.poster.queue._check_failure_alert"),
        ):
            result = post_next("rocketleague", client)

        assert result is False
        row = conn.execute("SELECT status FROM tweet_queue WHERE niche = 'rocketleague'").fetchone()
        assert row["status"] == "failed"

    def test_dispatches_retweet_as_quote_tweet(self):
        """RETWEET:{id} should call client.quote_tweet() (not retweet) for better reach."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, text="RETWEET:55555", priority=2)
        client = MagicMock()
        client.quote_tweet.return_value = "qt_12345"

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("rocketleague", client)

        assert result is True
        client.quote_tweet.assert_called_once()
        client.retweet.assert_not_called()
        client.post_tweet.assert_not_called()

    def test_marks_failed_when_quote_retweet_fails(self):
        """Failed quote-tweet (from RETWEET signal) should mark row failed."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, text="RETWEET:66666", priority=2)
        client = MagicMock()
        client.quote_tweet.return_value = None

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
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
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=False),
        ):
            result = post_next("rocketleague", client)

        assert result is False
        client.post_tweet.assert_not_called()

    def test_marks_failed_on_invalid_retweet_id(self):
        """RETWEET: signal with a non-numeric ID must be marked failed without calling client."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, text="RETWEET:not-a-number", priority=2)
        client = MagicMock()

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("rocketleague", client)

        assert result is False
        client.retweet.assert_not_called()
        row = conn.execute("SELECT status FROM tweet_queue WHERE niche = 'rocketleague'").fetchone()
        assert row["status"] == "failed"

    def test_check_failure_alert_fires_at_threshold(self):
        """_check_failure_alert should call send_alert exactly when count hits _BACKOFF_ALERT_N."""
        from src.poster.queue import _check_failure_alert
        from src.poster.rate_limiter import _BACKOFF_ALERT_N

        with (
            patch("src.poster.queue.consecutive_failure_count", return_value=_BACKOFF_ALERT_N),
            patch("src.poster.queue.send_alert", create=True) as mock_send,
        ):
            # Import send_alert so the patch resolves; the function uses a local import
            import asyncio

            with patch("asyncio.get_running_loop") as mock_loop:
                mock_task = MagicMock()
                mock_loop.return_value.create_task = mock_task
                _check_failure_alert("rocketleague")
                mock_task.assert_called_once()

    def test_check_failure_alert_does_not_fire_below_threshold(self):
        """_check_failure_alert should do nothing when count is below _BACKOFF_ALERT_N."""
        from src.poster.queue import _check_failure_alert
        from src.poster.rate_limiter import _BACKOFF_ALERT_N

        with (
            patch("src.poster.queue.consecutive_failure_count", return_value=_BACKOFF_ALERT_N - 1),
            patch("asyncio.get_running_loop") as mock_loop,
        ):
            _check_failure_alert("rocketleague")
            mock_loop.assert_not_called()


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


# ── _split_url() ─────────────────────────────────────────────────────────────

class TestSplitUrl:

    def test_splits_url_from_long_text(self):
        """URL at the end of a long enough tweet should be extracted."""
        text = "Season 14 of Rocket League is now live with all-new cosmetics and ranking changes https://rocketleague.com/news/s14"
        main, url = _split_url(text)
        assert url == "https://rocketleague.com/news/s14"
        assert "https://" not in main
        assert len(main) >= 30

    def test_keeps_url_inline_when_remaining_text_too_short(self):
        """If stripping the URL would leave <30 chars, keep it inline."""
        text = "Short https://example.com/article"
        main, url = _split_url(text)
        assert url is None
        assert main == text

    def test_returns_none_url_when_no_url_present(self):
        """Plain text with no URL should return (text, None)."""
        text = "Rocket League just dropped a huge patch!"
        main, url = _split_url(text)
        assert url is None
        assert main == text

    def test_splits_last_url_when_multiple_urls(self):
        """Only the last URL should be extracted."""
        text = "First https://example.com/a then more context about this story that is long enough https://example.com/b"
        main, url = _split_url(text)
        assert url == "https://example.com/b"
        assert "https://example.com/a" in main

    def test_strips_trailing_whitespace_from_main(self):
        """Main text should not have leading or trailing whitespace after split."""
        text = "This is a well-formed announcement about Geometry Dash  https://example.com/gd"
        main, url = _split_url(text)
        assert main == main.strip()

    def test_empty_string_returns_empty_and_none(self):
        """Empty input should return empty string and None."""
        main, url = _split_url("")
        assert main == ""
        assert url is None


# ── _retweet_context() ────────────────────────────────────────────────────────

class TestRetweetContext:

    def test_returns_account_specific_context_when_account_known(self):
        """Known source_account returns a string from the account-specific list."""
        from src.poster.queue import _RT_CONTEXT_BY_ACCOUNT
        result = _retweet_context("rocketleague", source_account="rocketleague")
        assert result in _RT_CONTEXT_BY_ACCOUNT["rocketleague"]

    def test_returns_fallback_context_when_account_unknown(self):
        """Unknown source_account falls back to niche-level fallback list."""
        from src.poster.queue import _RT_CONTEXT_FALLBACK
        result = _retweet_context("rocketleague", source_account="somerandomperson")
        assert result in _RT_CONTEXT_FALLBACK["rocketleague"]

    def test_returns_fallback_when_no_account_given(self):
        """Empty source_account falls through to niche fallback."""
        from src.poster.queue import _RT_CONTEXT_FALLBACK
        result = _retweet_context("geometrydash", source_account="")
        assert result in _RT_CONTEXT_FALLBACK["geometrydash"]

    def test_returns_string_for_unknown_niche_with_no_account(self):
        """Unknown niche with no account returns the hardcoded 'News:' fallback."""
        result = _retweet_context("unknownniche", source_account="")
        assert result == "News:"

    def test_account_lookup_is_case_insensitive(self):
        """source_account matching should be lowercase-normalised."""
        from src.poster.queue import _RT_CONTEXT_BY_ACCOUNT
        result = _retweet_context("geometrydash", source_account="RobTopGames")
        assert result in _RT_CONTEXT_BY_ACCOUNT["robtopgames"]

    def test_all_known_accounts_return_non_empty_string(self):
        """Every account in _RT_CONTEXT_BY_ACCOUNT should yield a non-empty context."""
        from src.poster.queue import _RT_CONTEXT_BY_ACCOUNT
        for account in _RT_CONTEXT_BY_ACCOUNT:
            result = _retweet_context("rocketleague", source_account=account)
            assert isinstance(result, str) and result


# ── _engagement_followup() ────────────────────────────────────────────────────

class TestEngagementFollowup:

    def test_returns_string_for_rocketleague(self):
        """rocketleague niche should yield a non-empty CTA string."""
        from src.poster.queue import _ENGAGEMENT_FOLLOWUPS
        result = _engagement_followup("rocketleague")
        assert result in _ENGAGEMENT_FOLLOWUPS["rocketleague"]

    def test_returns_string_for_geometrydash(self):
        """geometrydash niche should yield a non-empty CTA string."""
        from src.poster.queue import _ENGAGEMENT_FOLLOWUPS
        result = _engagement_followup("geometrydash")
        assert result in _ENGAGEMENT_FOLLOWUPS["geometrydash"]

    def test_returns_none_for_unknown_niche(self):
        """Unknown niche should return None — never post an empty reply."""
        result = _engagement_followup("unknownniche")
        assert result is None

    def test_returns_none_for_empty_string_niche(self):
        """Empty string niche should return None."""
        result = _engagement_followup("")
        assert result is None


# ── collect_and_queue — per-cycle hard cap ────────────────────────────────────

class TestCollectAndQueueHardCap:

    @pytest.mark.asyncio
    async def test_hard_cap_stops_at_max_items_per_cycle(self):
        """collect_and_queue must not enqueue more than _MAX_ITEMS_PER_CYCLE items."""
        from src.poster.queue import _MAX_ITEMS_PER_CYCLE
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        # Build more items than the cap allows
        items = [
            RawContent(
                source_id=source_id,
                external_id=f"cap_{i}",
                niche="rocketleague",
                content_type="breaking_news",
                title=f"Story {i}",
                url=f"https://example.com/story-{i}",
            )
            for i in range(_MAX_ITEMS_PER_CYCLE + 3)
        ]

        class BulkCollector(BaseCollector):
            async def collect(self):
                return items

        collector = BulkCollector(source_id=source_id, config={})

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.format_tweet", side_effect=lambda item: f"Story text {item.external_id}"),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.is_similar_story", return_value=False),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.passes_quality_gate", return_value=True),
        ):
            result = await collect_and_queue(collector, "rocketleague")

        assert result == _MAX_ITEMS_PER_CYCLE
        rows = conn.execute("SELECT COUNT(*) AS cnt FROM tweet_queue").fetchone()
        assert rows["cnt"] == _MAX_ITEMS_PER_CYCLE

    @pytest.mark.asyncio
    async def test_exactly_max_items_allowed(self):
        """Exactly _MAX_ITEMS_PER_CYCLE items should all be queued without truncation."""
        from src.poster.queue import _MAX_ITEMS_PER_CYCLE
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        items = [
            RawContent(
                source_id=source_id,
                external_id=f"exact_{i}",
                niche="rocketleague",
                content_type="breaking_news",
                title=f"Exact {i}",
                url=f"https://example.com/exact-{i}",
            )
            for i in range(_MAX_ITEMS_PER_CYCLE)
        ]

        class ExactCollector(BaseCollector):
            async def collect(self):
                return items

        collector = ExactCollector(source_id=source_id, config={})

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.format_tweet", side_effect=lambda item: f"Exact text {item.external_id}"),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.is_similar_story", return_value=False),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.passes_quality_gate", return_value=True),
        ):
            result = await collect_and_queue(collector, "rocketleague")

        assert result == _MAX_ITEMS_PER_CYCLE


# ── collect_and_queue — quality gate integration ──────────────────────────────

class TestCollectAndQueueQualityGate:

    @pytest.mark.asyncio
    async def test_skips_item_that_fails_quality_gate(self):
        """Item rejected by passes_quality_gate must not be enqueued."""
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        item = RawContent(
            source_id=source_id,
            external_id="qg_fail_001",
            niche="rocketleague",
            content_type="community_clip",
            title="Low quality clip",
            url="https://example.com/clip",
            score=5,
        )

        class LowQualityCollector(BaseCollector):
            async def collect(self):
                return [item]

        collector = LowQualityCollector(source_id=source_id, config={})

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.format_tweet", return_value="Low quality clip"),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.is_similar_story", return_value=False),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.passes_quality_gate", return_value=False),
        ):
            result = await collect_and_queue(collector, "rocketleague")

        assert result == 0
        rows = conn.execute("SELECT COUNT(*) AS cnt FROM tweet_queue").fetchone()
        assert rows["cnt"] == 0

    @pytest.mark.asyncio
    async def test_age_calculated_from_metadata_created_at(self):
        """Age in hours is derived from metadata.created_at and passed to quality gate."""
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        item = RawContent(
            source_id=source_id,
            external_id="age_001",
            niche="rocketleague",
            content_type="community_clip",
            title="Recent clip",
            url="https://example.com/recent",
            score=500,
            metadata={"created_at": "2026-01-01T00:00:00Z"},
        )

        class AgedCollector(BaseCollector):
            async def collect(self):
                return [item]

        collector = AgedCollector(source_id=source_id, config={})

        captured_age = {}

        def _capture_gate(content_type, niche, score, age_hours, source_followers):
            captured_age["age_hours"] = age_hours
            return False  # reject so we don't need full DB flow

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.passes_quality_gate", side_effect=_capture_gate),
        ):
            await collect_and_queue(collector, "rocketleague")

        # The item was created in 2026-01-01; age must be > 0 hours
        assert "age_hours" in captured_age
        assert captured_age["age_hours"] > 0

    @pytest.mark.asyncio
    async def test_age_defaults_to_zero_when_metadata_missing_created_at(self):
        """Items with no created_at in metadata are treated as age 0 (fresh)."""
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        item = RawContent(
            source_id=source_id,
            external_id="no_ts_001",
            niche="rocketleague",
            content_type="community_clip",
            title="Clip without timestamp",
            url="https://example.com/no-ts",
            score=500,
        )

        class NoTsCollector(BaseCollector):
            async def collect(self):
                return [item]

        collector = NoTsCollector(source_id=source_id, config={})

        captured = {}

        def _capture_gate(content_type, niche, score, age_hours, source_followers):
            captured["age_hours"] = age_hours
            return False

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.passes_quality_gate", side_effect=_capture_gate),
        ):
            await collect_and_queue(collector, "rocketleague")

        assert captured.get("age_hours") == 0.0

    @pytest.mark.asyncio
    async def test_age_defaults_to_zero_on_invalid_created_at_format(self):
        """Malformed created_at string should not crash — age defaults to 0."""
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        item = RawContent(
            source_id=source_id,
            external_id="bad_ts_001",
            niche="rocketleague",
            content_type="community_clip",
            title="Clip with bad timestamp",
            url="https://example.com/bad-ts",
            score=500,
            metadata={"created_at": "not-a-date"},
        )

        class BadTsCollector(BaseCollector):
            async def collect(self):
                return [item]

        collector = BadTsCollector(source_id=source_id, config={})

        captured = {}

        def _capture_gate(content_type, niche, score, age_hours, source_followers):
            captured["age_hours"] = age_hours
            return False

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.passes_quality_gate", side_effect=_capture_gate),
        ):
            await collect_and_queue(collector, "rocketleague")

        assert captured.get("age_hours") == 0.0


# ── collect_and_queue — duplicate RETWEET signal dedup ───────────────────────

class TestCollectAndQueueRetweetDedup:

    @pytest.mark.asyncio
    async def test_skips_duplicate_retweet_signal_already_in_queue(self):
        """Second collection of the same tweet ID should not create a second queue row."""
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        # Pre-insert the RETWEET signal as if it was already queued
        _insert_queue_row(conn, text="RETWEET:77777:rocketleague", priority=2)

        item = RawContent(
            source_id=source_id,
            external_id="rt_dup_001",
            niche="rocketleague",
            content_type="official_tweet",
            title="",
            url="",
            metadata={"retweet_id": "77777", "account": "rocketleague"},
        )

        class RTDupCollector(BaseCollector):
            async def collect(self):
                return [item]

        collector = RTDupCollector(source_id=source_id, config={})

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.format_tweet", return_value=None),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
        ):
            result = await collect_and_queue(collector, "rocketleague")

        assert result == 0
        rows = conn.execute("SELECT COUNT(*) AS cnt FROM tweet_queue").fetchone()
        assert rows["cnt"] == 1  # only the pre-existing row

    @pytest.mark.asyncio
    async def test_skips_exact_duplicate_tweet_text(self):
        """Identical tweet text already in queue/posted should not be re-queued."""
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        # Pre-insert the tweet as already queued
        existing_text = "Season 14 of Rocket League is now live with brand-new ranks and cosmetics"
        _insert_queue_row(conn, text=existing_text, priority=2)

        item = RawContent(
            source_id=source_id,
            external_id="exact_dup_002",
            niche="rocketleague",
            content_type="breaking_news",
            title="Season 14",
            url="https://example.com/s14",
        )

        class ExactDupCollector(BaseCollector):
            async def collect(self):
                return [item]

        collector = ExactDupCollector(source_id=source_id, config={})

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.format_tweet", return_value=existing_text),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.is_similar_story", return_value=False),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.passes_quality_gate", return_value=True),
        ):
            result = await collect_and_queue(collector, "rocketleague")

        assert result == 0


# ── collect_and_queue — media path selection ─────────────────────────────────

class TestCollectAndQueueMediaPath:

    @pytest.mark.asyncio
    async def test_uses_reddit_media_path_when_mp4_present(self):
        """Items with media_path ending .mp4 in metadata skip prepare_media."""
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        item = RawContent(
            source_id=source_id,
            external_id="mp4_001",
            niche="rocketleague",
            content_type="community_clip",
            title="Reddit clip",
            url="",
            metadata={"media_path": "/tmp/clip.mp4"},
        )

        class Mp4Collector(BaseCollector):
            async def collect(self):
                return [item]

        collector = Mp4Collector(source_id=source_id, config={})

        mock_prepare = MagicMock()

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.format_tweet", return_value="Reddit clip"),
            patch("src.poster.queue.prepare_media", mock_prepare),
            patch("src.poster.queue.is_similar_story", return_value=False),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.passes_quality_gate", return_value=True),
        ):
            result = await collect_and_queue(collector, "rocketleague")

        assert result == 1
        mock_prepare.assert_not_called()
        row = conn.execute("SELECT media_path FROM tweet_queue").fetchone()
        assert row["media_path"] == "/tmp/clip.mp4"

    @pytest.mark.asyncio
    async def test_calls_prepare_media_when_image_url_present(self):
        """Items with image_url but no reddit mp4 path should call prepare_media."""
        conn = _make_in_memory_db()
        source_id = _insert_source(conn)

        item = RawContent(
            source_id=source_id,
            external_id="img_001",
            niche="rocketleague",
            content_type="breaking_news",
            title="Image story",
            url="https://example.com/story",
            image_url="https://example.com/image.jpg",
        )

        class ImageCollector(BaseCollector):
            async def collect(self):
                return [item]

        collector = ImageCollector(source_id=source_id, config={})

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.format_tweet", return_value="Image story text that is long enough here"),
            patch("src.poster.queue.prepare_media", return_value="/tmp/downloaded.jpg") as mock_prepare,
            patch("src.poster.queue.is_similar_story", return_value=False),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.passes_quality_gate", return_value=True),
        ):
            result = await collect_and_queue(collector, "rocketleague")

        assert result == 1
        mock_prepare.assert_called_once_with("https://example.com/image.jpg")
        row = conn.execute("SELECT media_path FROM tweet_queue").fetchone()
        assert row["media_path"] == "/tmp/downloaded.jpg"


# ── _posts_in_last_30min() ────────────────────────────────────────────────────

class TestPostsInLast30Min:

    def test_counts_recent_posts_only(self):
        """Only posts within the last 30 minutes with a tweet_id should be counted."""
        from src.poster.queue import _posts_in_last_30min

        conn = _make_in_memory_db()
        queue_id = _insert_queue_row(conn)

        # Insert a recent successful post (tweet_id present)
        conn.execute(
            """INSERT INTO post_log (tweet_queue_id, niche, tweet_id, tweet_text, posted_at)
               VALUES (?, 'rocketleague', 'tweet_recent', 'text', datetime('now', '-5 minutes'))""",
            (queue_id,),
        )
        # Insert an old post (outside 30-minute window)
        conn.execute(
            """INSERT INTO post_log (tweet_queue_id, niche, tweet_id, tweet_text, posted_at)
               VALUES (?, 'rocketleague', 'tweet_old', 'text', datetime('now', '-60 minutes'))""",
            (queue_id,),
        )
        # Insert a failed post (tweet_id is NULL — should not be counted)
        conn.execute(
            """INSERT INTO post_log (tweet_queue_id, niche, tweet_id, tweet_text, posted_at)
               VALUES (?, 'rocketleague', NULL, 'text', datetime('now', '-2 minutes'))""",
            (queue_id,),
        )
        conn.commit()

        with patch("src.poster.queue.get_db", return_value=_ctx(conn)):
            count = _posts_in_last_30min("rocketleague")

        assert count == 1

    def test_returns_zero_when_no_recent_posts(self):
        """Returns 0 when post_log is empty for the niche."""
        from src.poster.queue import _posts_in_last_30min

        conn = _make_in_memory_db()

        with patch("src.poster.queue.get_db", return_value=_ctx(conn)):
            count = _posts_in_last_30min("rocketleague")

        assert count == 0

    def test_counts_only_correct_niche(self):
        """Posts for a different niche must not be counted."""
        from src.poster.queue import _posts_in_last_30min

        conn = _make_in_memory_db()
        queue_id = _insert_queue_row(conn, niche="geometrydash")

        conn.execute(
            """INSERT INTO post_log (tweet_queue_id, niche, tweet_id, tweet_text, posted_at)
               VALUES (?, 'geometrydash', 'tweet_gd', 'text', datetime('now', '-1 minutes'))""",
            (queue_id,),
        )
        conn.commit()

        with patch("src.poster.queue.get_db", return_value=_ctx(conn)):
            count = _posts_in_last_30min("rocketleague")

        assert count == 0

    def test_iso8601_t_format_old_post_not_counted(self):
        """Posts stored with ISO 8601 'T' separator from hours ago must NOT be counted.

        Regression test: SQLite's datetime('now') uses space separator, but the
        app stores posted_at as '%Y-%m-%dT%H:%M:%SZ' (T separator).  Without
        datetime() normalization, 'T' > ' ' in ASCII causes ALL stored timestamps
        to appear > the cutoff, triggering the 30-min safety cap falsely.
        """
        from src.poster.queue import _posts_in_last_30min
        from datetime import datetime, timezone, timedelta

        conn = _make_in_memory_db()
        queue_id = _insert_queue_row(conn)

        # Insert a post from 8 hours ago using the T-format the app actually writes
        eight_hours_ago = (
            datetime.now(timezone.utc) - timedelta(hours=8)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            """INSERT INTO post_log (tweet_queue_id, niche, tweet_id, tweet_text, posted_at)
               VALUES (?, 'rocketleague', 'tweet_old_iso', 'text', ?)""",
            (queue_id, eight_hours_ago),
        )
        conn.commit()

        with patch("src.poster.queue.get_db", return_value=_ctx(conn)):
            count = _posts_in_last_30min("rocketleague")

        assert count == 0, (
            "An 8-hour-old ISO 8601 post should not be counted in the 30-min window"
        )


# ── post_next — 30-min safety cap ────────────────────────────────────────────

class TestPostNextSafetyCap:

    def test_blocks_posting_when_safety_cap_reached(self):
        """post_next returns False immediately when 3+ posts in last 30 min."""
        from src.poster.queue import _MAX_POSTS_PER_30MIN

        conn = _make_in_memory_db()
        _insert_queue_row(conn, priority=5, text="Should not post")
        client = MagicMock()

        with (
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=_MAX_POSTS_PER_30MIN),
        ):
            result = post_next("rocketleague", client)

        assert result is False
        client.post_tweet.assert_not_called()
        client.quote_tweet.assert_not_called()

    def test_blocks_posting_when_safety_cap_exceeded(self):
        """post_next returns False when count exceeds the 30-min cap."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, priority=5, text="Also should not post")
        client = MagicMock()

        with (
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=10),
        ):
            result = post_next("rocketleague", client)

        assert result is False

    def test_allows_posting_when_below_safety_cap(self):
        """post_next proceeds normally when recent posts are below the cap."""
        from src.poster.queue import _MAX_POSTS_PER_30MIN

        conn = _make_in_memory_db()
        _insert_queue_row(conn, priority=5, text="Should post fine")
        client = MagicMock()
        client.post_tweet.return_value = "tweet_ok"

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=_MAX_POSTS_PER_30MIN - 1),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
        ):
            result = post_next("rocketleague", client)

        assert result is True


# ── post_next — URL self-reply ────────────────────────────────────────────────

class TestPostNextUrlSelfReply:

    def test_url_is_posted_as_self_reply(self):
        """When tweet text has a URL, main text is posted first then URL as reply."""
        conn = _make_in_memory_db()
        long_tweet = "Rocket League Season 14 is now live — major rank reset, new cosmetics, and cross-platform parties https://rocketleague.com/news/s14"
        _insert_queue_row(conn, priority=3, text=long_tweet)
        client = MagicMock()
        client.post_tweet.return_value = "tweet_main_id"

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("rocketleague", client)

        assert result is True
        assert client.post_tweet.call_count == 2
        # First call: main text without URL
        first_call_text = client.post_tweet.call_args_list[0].kwargs.get(
            "text", client.post_tweet.call_args_list[0].args[0] if client.post_tweet.call_args_list[0].args else ""
        )
        # Second call: self-reply with URL
        second_call_kwargs = client.post_tweet.call_args_list[1].kwargs
        assert second_call_kwargs.get("reply_to") == "tweet_main_id"
        assert "Read more:" in second_call_kwargs.get("text", "")
        assert "https://rocketleague.com/news/s14" in second_call_kwargs.get("text", "")

    def test_no_self_reply_when_tweet_has_no_url(self):
        """Tweets without a URL should not trigger a self-reply call."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, priority=3, text="Rocket League is looking great this season")
        client = MagicMock()
        client.post_tweet.return_value = "tweet_no_url"

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("rocketleague", client)

        assert result is True
        # Only one post_tweet call — no reply for URL-less tweet at non-breaking priority
        assert client.post_tweet.call_count == 1


# ── post_next — engagement followup ─────────────────────────────────────────

class TestPostNextEngagementFollowup:

    def test_breaking_news_without_url_gets_followup_reply(self):
        """Breaking news (priority=1) without URL triggers engagement followup reply."""
        conn = _make_in_memory_db()
        _insert_queue_row(
            conn, niche="geometrydash", priority=1,
            text="BREAKING: Top 1 in Geometry Dash just verified by a new player"
        )
        client = MagicMock()
        client.post_tweet.return_value = "tweet_breaking"

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
            patch("src.poster.queue._engagement_followup", return_value="Follow @gd_wire for updates."),
        ):
            result = post_next("geometrydash", client)

        assert result is True
        assert client.post_tweet.call_count == 2
        followup_call = client.post_tweet.call_args_list[1].kwargs
        assert followup_call.get("reply_to") == "tweet_breaking"
        assert "Follow" in followup_call.get("text", "")

    def test_breaking_news_followup_skipped_when_engagement_followup_returns_none(self):
        """If _engagement_followup returns None, no second post_tweet call is made."""
        conn = _make_in_memory_db()
        _insert_queue_row(
            conn, niche="rocketleague", priority=1,
            text="BREAKING: Something happened without a follow-up CTA available"
        )
        client = MagicMock()
        client.post_tweet.return_value = "tweet_no_followup"

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
            patch("src.poster.queue._engagement_followup", return_value=None),
        ):
            result = post_next("rocketleague", client)

        assert result is True
        assert client.post_tweet.call_count == 1

    def test_non_breaking_tweet_without_url_does_not_get_followup(self):
        """Non-breaking (priority > 1) tweets without URL must not get a followup reply."""
        conn = _make_in_memory_db()
        _insert_queue_row(
            conn, niche="rocketleague", priority=4,
            text="New weekly demon posted in Rocket League community today"
        )
        client = MagicMock()
        client.post_tweet.return_value = "tweet_nonbreak"

        mock_followup = MagicMock(return_value="Follow us!")

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
            patch("src.poster.queue._engagement_followup", mock_followup),
        ):
            result = post_next("rocketleague", client)

        assert result is True
        assert client.post_tweet.call_count == 1
        mock_followup.assert_not_called()


# ── post_next — source-aware RETWEET:{id}:{account} format ───────────────────

class TestPostNextRetweetWithAccount:

    def test_retweet_with_account_passes_account_to_retweet_context(self):
        """RETWEET:{id}:{account} format extracts account and passes it to _retweet_context."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, text="RETWEET:12345:rocketleague", priority=2)
        client = MagicMock()
        client.quote_tweet.return_value = "qt_with_account"

        captured = {}

        def _capture_context(niche, source_account=""):
            captured["source_account"] = source_account
            return "From @RocketLeague:"

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
            patch("src.poster.queue._retweet_context", side_effect=_capture_context),
        ):
            result = post_next("rocketleague", client)

        assert result is True
        assert captured.get("source_account") == "rocketleague"
        client.quote_tweet.assert_called_once_with("12345", "From @RocketLeague:")

    def test_retweet_without_account_uses_empty_string(self):
        """RETWEET:{id} without third segment passes empty source_account."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, text="RETWEET:99999", priority=2)
        client = MagicMock()
        client.quote_tweet.return_value = "qt_no_account"

        captured = {}

        def _capture_context(niche, source_account=""):
            captured["source_account"] = source_account
            return "Rocket League news:"

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
            patch("src.poster.queue._retweet_context", side_effect=_capture_context),
        ):
            result = post_next("rocketleague", client)

        assert result is True
        assert captured.get("source_account") == ""


# ── post_next — QUOTE: signal ────────────────────────────────────────────────

class TestPostNextQuoteSignal:

    def test_quote_signal_calls_quote_tweet_with_commentary(self):
        """QUOTE:{id}:{text} should call client.quote_tweet with extracted commentary."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, text="QUOTE:11111:This is huge news for RL esports!", priority=2)
        client = MagicMock()
        client.quote_tweet.return_value = "qt_quote_ok"

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("rocketleague", client)

        assert result is True
        client.quote_tweet.assert_called_once_with("11111", "This is huge news for RL esports!")
        row = conn.execute("SELECT status FROM tweet_queue").fetchone()
        assert row["status"] == "posted"

    def test_quote_signal_marks_failed_when_quote_tweet_returns_none(self):
        """QUOTE: signal where quote_tweet fails should mark row failed."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, text="QUOTE:22222:Big news dropping soon for fans", priority=2)
        client = MagicMock()
        client.quote_tweet.return_value = None

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
            patch("src.poster.queue._check_failure_alert"),
        ):
            result = post_next("rocketleague", client)

        assert result is False
        row = conn.execute("SELECT status FROM tweet_queue").fetchone()
        assert row["status"] == "failed"

    def test_quote_signal_marks_failed_on_malformed_no_colon_separator(self):
        """QUOTE: signal with no second colon separator should be marked failed."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, text="QUOTE:malformed-no-sep", priority=2)
        client = MagicMock()

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("rocketleague", client)

        assert result is False
        client.quote_tweet.assert_not_called()
        row = conn.execute("SELECT status FROM tweet_queue").fetchone()
        assert row["status"] == "failed"

    def test_quote_signal_marks_failed_on_non_numeric_id(self):
        """QUOTE: signal with non-numeric tweet ID should be marked failed."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, text="QUOTE:not-a-number:Some commentary here", priority=2)
        client = MagicMock()

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("rocketleague", client)

        assert result is False
        client.quote_tweet.assert_not_called()
        row = conn.execute("SELECT status FROM tweet_queue").fetchone()
        assert row["status"] == "failed"

    def test_quote_signal_marks_failed_on_empty_commentary(self):
        """QUOTE: signal with empty commentary text should be marked failed."""
        conn = _make_in_memory_db()
        _insert_queue_row(conn, text="QUOTE:33333:", priority=2)
        client = MagicMock()

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("rocketleague", client)

        assert result is False
        client.quote_tweet.assert_not_called()
        row = conn.execute("SELECT status FROM tweet_queue").fetchone()
        assert row["status"] == "failed"


# ── _check_failure_alert — exception swallow ─────────────────────────────────

class TestCheckFailureAlertExceptionSwallow:

    def test_exception_inside_alert_is_silently_swallowed(self):
        """Any exception raised inside _check_failure_alert must not propagate."""
        from src.poster.queue import _check_failure_alert
        from src.poster.rate_limiter import _BACKOFF_ALERT_N

        with (
            patch("src.poster.queue.consecutive_failure_count", return_value=_BACKOFF_ALERT_N),
            patch("asyncio.get_running_loop", side_effect=RuntimeError("no event loop")),
        ):
            # Must not raise
            _check_failure_alert("rocketleague")


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
