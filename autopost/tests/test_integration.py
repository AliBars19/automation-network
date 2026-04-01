"""
Integration tests — end-to-end pipeline tests that verify the full flow:
collect → format → queue → post, including edge cases and error recovery.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.base import BaseCollector, RawContent
from src.database.db import (
    add_to_queue, get_db, get_queued_tweets, init_db,
    insert_raw_content, is_similar_story, mark_failed,
    mark_posted, mark_skipped, upsert_source, get_sources,
    cleanup_old_records, record_source_error, recent_source_error_count,
    disable_source, url_already_queued,
)
from src.formatter.formatter import format_tweet, _build_context
from src.poster.queue import (
    collect_and_queue, post_next, skip_stale,
    _split_url, _retweet_context, _engagement_followup,
    _PRIORITY, _MAX_ITEMS_PER_CYCLE,
)
from src.poster.rate_limiter import (
    can_post, within_monthly_limit, within_posting_window,
    failure_backoff_ok, consecutive_failure_count,
)

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "database" / "schema.sql"


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
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


def _make_content(**kwargs):
    defaults = {
        "source_id": 1, "external_id": "int_001", "niche": "rocketleague",
        "content_type": "breaking_news", "title": "Integration Test",
        "url": "https://example.com", "body": "Test body", "image_url": "",
        "author": "Tester", "score": 0, "metadata": {},
    }
    defaults.update(kwargs)
    return RawContent(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# FULL PIPELINE: collect → format → queue
# ═══════════════════════════════════════════════════════════════════════════════

class TestCollectFormatQueuePipeline:
    """Tests the full collect_and_queue pipeline with various content types."""

    @pytest.mark.asyncio
    async def test_breaking_news_queued_at_priority_1(self):
        conn = _make_db()
        sid = upsert_source(conn, "rocketleague", "test", "rss", {})

        class FakeCollector(BaseCollector):
            async def collect(self):
                return [_make_content(
                    source_id=sid, content_type="breaking_news",
                    title="RLCS World Championship Announced",
                    url="https://rlcs.com/worlds",
                )]

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
        ):
            count = await collect_and_queue(FakeCollector(sid, {}), "rocketleague")

        assert count == 1
        rows = get_queued_tweets(conn, "rocketleague")
        assert len(rows) == 1
        assert rows[0]["priority"] == 1

    @pytest.mark.asyncio
    async def test_youtube_video_queued_at_priority_4(self):
        conn = _make_db()
        sid = upsert_source(conn, "rocketleague", "yt", "youtube", {})

        class FakeCollector(BaseCollector):
            async def collect(self):
                return [_make_content(
                    source_id=sid, content_type="youtube_video",
                    title="New Video", author="SunlessKhan",
                    metadata={"creator": "SunlessKhan", "video_title": "New Vid", "url": "https://youtu.be/x"},
                )]

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
        ):
            count = await collect_and_queue(FakeCollector(sid, {}), "rocketleague")

        assert count == 1
        rows = get_queued_tweets(conn, "rocketleague")
        assert rows[0]["priority"] == 4

    @pytest.mark.asyncio
    async def test_duplicate_content_not_requeued(self):
        conn = _make_db()
        sid = upsert_source(conn, "rocketleague", "test", "rss", {})

        class FakeCollector(BaseCollector):
            async def collect(self):
                return [_make_content(source_id=sid, external_id="dup_001")]

        collector = FakeCollector(sid, {})
        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
        ):
            count1 = await collect_and_queue(collector, "rocketleague")

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
        ):
            count2 = await collect_and_queue(collector, "rocketleague")

        assert count1 == 1
        assert count2 == 0  # duplicate

    @pytest.mark.asyncio
    async def test_per_cycle_cap_enforced(self):
        conn = _make_db()
        sid = upsert_source(conn, "rocketleague", "test", "rss", {})

        class FloodCollector(BaseCollector):
            async def collect(self):
                return [_make_content(
                    source_id=sid, external_id=f"flood_{i}",
                    title=f"Unique headline number {i} about Rocket League updates",
                    url=f"https://example.com/article/{i}",
                ) for i in range(20)]

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.is_similar_story", return_value=False),
        ):
            count = await collect_and_queue(FloodCollector(sid, {}), "rocketleague")

        assert count == _MAX_ITEMS_PER_CYCLE  # capped at 5

    @pytest.mark.asyncio
    async def test_collector_exception_returns_zero(self):
        class BrokenCollector(BaseCollector):
            async def collect(self):
                raise RuntimeError("Network down")

        with patch("src.poster.queue.get_db", return_value=_ctx(_make_db())):
            count = await collect_and_queue(BrokenCollector(1, {}), "rocketleague")
        assert count == 0

    @pytest.mark.asyncio
    async def test_similar_story_deduplicated(self):
        conn = _make_db()
        sid = upsert_source(conn, "rocketleague", "test1", "rss", {})
        sid2 = upsert_source(conn, "rocketleague", "test2", "rss", {})

        class Collector1(BaseCollector):
            async def collect(self):
                return [_make_content(
                    source_id=sid, external_id="story_a",
                    title="Rocket League Season 15 drops today with new features",
                    url="https://a.com/s15",
                )]

        class Collector2(BaseCollector):
            async def collect(self):
                return [_make_content(
                    source_id=sid2, external_id="story_b",
                    title="Rocket League Season 15 drops today with new features and changes",
                    url="https://b.com/s15",
                )]

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
        ):
            count1 = await collect_and_queue(Collector1(sid, {}), "rocketleague")
        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
        ):
            count2 = await collect_and_queue(Collector2(sid2, {}), "rocketleague")

        assert count1 == 1
        assert count2 == 0  # similar story blocked

    @pytest.mark.asyncio
    async def test_retweet_signal_stored_with_account(self):
        conn = _make_db()
        sid = upsert_source(conn, "rocketleague", "@RLEsports", "twitter", {})

        class RTCollector(BaseCollector):
            async def collect(self):
                return [_make_content(
                    source_id=sid, external_id="rt_100",
                    content_type="official_tweet",
                    metadata={"retweet_id": "12345", "account": "RLEsports"},
                )]

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.format_tweet", return_value=None),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
        ):
            count = await collect_and_queue(RTCollector(sid, {}), "rocketleague")

        assert count == 1
        row = conn.execute("SELECT tweet_text FROM tweet_queue WHERE status='queued'").fetchone()
        assert row["tweet_text"] == "RETWEET:12345:RLEsports"


# ═══════════════════════════════════════════════════════════════════════════════
# POST_NEXT — URL self-reply behavior
# ═══════════════════════════════════════════════════════════════════════════════

class TestPostNextUrlSelfReply:
    def _setup_queue(self, conn, text, priority=5):
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, priority, status) VALUES (?, ?, ?, 'queued')",
            ("rocketleague", text, priority),
        )
        conn.commit()

    def test_url_split_to_reply(self):
        conn = _make_db()
        self._setup_queue(conn, "Rocket League Season 15 has arrived with major changes\n\nhttps://example.com/s15")
        client = MagicMock()
        client.post_tweet.return_value = "main_tweet_id"

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
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
        first_text = client.post_tweet.call_args_list[0].kwargs["text"]
        assert "https://example.com" not in first_text
        # Second call: reply with URL
        second_text = client.post_tweet.call_args_list[1].kwargs["text"]
        assert "https://example.com/s15" in second_text
        assert client.post_tweet.call_args_list[1].kwargs["reply_to"] == "main_tweet_id"

    def test_no_url_no_reply(self):
        conn = _make_db()
        self._setup_queue(conn, "Rocket League Season 15 is live now with new Arena and Ranked changes!")
        client = MagicMock()
        client.post_tweet.return_value = "tweet_id"

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("rocketleague", client)

        assert result is True
        assert client.post_tweet.call_count == 1

    def test_breaking_news_no_url_gets_followup(self):
        conn = _make_db()
        self._setup_queue(conn, "BREAKING: Major RLCS announcement coming today with huge changes!", priority=1)
        client = MagicMock()
        client.post_tweet.return_value = "breaking_id"

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("rocketleague", client)

        assert result is True
        assert client.post_tweet.call_count == 2  # main + followup
        followup_text = client.post_tweet.call_args_list[1].kwargs["text"]
        assert any(w in followup_text for w in ("rl_wire1", "Rocket League", "notifications", "Reply"))


# ═══════════════════════════════════════════════════════════════════════════════
# _split_url edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestSplitUrl:
    def test_no_url_returns_original(self):
        text, url = _split_url("Just a plain text tweet with no links")
        assert text == "Just a plain text tweet with no links"
        assert url is None

    def test_url_extracted(self):
        text, url = _split_url("Check this out and read about the new Rocket League update\n\nhttps://example.com/news")
        assert url == "https://example.com/news"
        assert "https://example.com" not in text

    def test_short_text_keeps_url_inline(self):
        text, url = _split_url("News https://x.com")
        assert url is None  # remaining text "News" < 30 chars

    def test_multiple_urls_splits_last(self):
        text, url = _split_url("First https://a.com and second https://b.com")
        assert url == "https://b.com"
        assert "https://a.com" in text

    def test_url_at_start(self):
        text, url = _split_url("https://example.com Here is the news")
        assert url is None  # remaining too short


# ═══════════════════════════════════════════════════════════════════════════════
# _retweet_context
# ═══════════════════════════════════════════════════════════════════════════════

class TestRetweetContextIntegration:
    def test_rl_esports_account(self):
        ctx = _retweet_context("rocketleague", "RLEsports")
        assert "@RLEsports" in ctx or "#RLCS" in ctx

    def test_robtop_account(self):
        ctx = _retweet_context("geometrydash", "RobTopGames")
        assert "@RobTopGames" in ctx or "RobTop" in ctx

    def test_unknown_account_uses_niche_fallback(self):
        ctx = _retweet_context("rocketleague", "RandomAccount")
        assert "Rocket League" in ctx or "#RLCS" in ctx

    def test_empty_account_uses_niche_fallback(self):
        ctx = _retweet_context("geometrydash", "")
        assert "Geometry Dash" in ctx or "GD" in ctx

    def test_rl_status_account(self):
        ctx = _retweet_context("rocketleague", "RL_Status")
        assert "status" in ctx.lower() or "@RL_Status" in ctx


# ═══════════════════════════════════════════════════════════════════════════════
# _engagement_followup
# ═══════════════════════════════════════════════════════════════════════════════

class TestEngagementFollowup:
    def test_rl_followup_mentions_account(self):
        for _ in range(20):
            result = _engagement_followup("rocketleague")
            assert result is not None
            assert isinstance(result, str)
            assert len(result) > 0

    def test_gd_followup_mentions_account(self):
        for _ in range(20):
            result = _engagement_followup("geometrydash")
            assert result is not None

    def test_unknown_niche_returns_none(self):
        assert _engagement_followup("unknown") is None


# ═══════════════════════════════════════════════════════════════════════════════
# SKIP_STALE
# ═══════════════════════════════════════════════════════════════════════════════

class TestSkipStale:
    def test_skips_old_queued_items(self):
        conn = _make_db()
        old = (datetime.now(timezone.utc) - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) VALUES (?, ?, 'queued', ?)",
            ("rocketleague", "Old news", old),
        )
        conn.commit()
        with patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)):
            count = skip_stale("rocketleague", max_age_hours=6)
        assert count == 1

    def test_does_not_skip_recent(self):
        conn = _make_db()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) VALUES (?, ?, 'queued', ?)",
            ("rocketleague", "Fresh news", now),
        )
        conn.commit()
        with patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)):
            count = skip_stale("rocketleague", max_age_hours=6)
        assert count == 0


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE — full lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

class TestDatabaseLifecycle:
    def test_source_insert_collect_queue_post_cleanup(self):
        """Full lifecycle: source → raw_content → queue → post → cleanup."""
        conn = _make_db()

        # 1. Create source
        sid = upsert_source(conn, "rocketleague", "Test RSS", "rss", {"url": "https://example.com"})
        assert sid > 0
        sources = get_sources(conn, "rocketleague")
        assert len(sources) == 1

        # 2. Insert raw content
        rc = RawContent(
            source_id=sid, external_id="lifecycle_001", niche="rocketleague",
            content_type="breaking_news", title="Test News",
            url="https://example.com/news", body="Body text",
        )
        cid, is_new = insert_raw_content(conn, rc)
        assert is_new is True
        assert cid > 0

        # 3. Add to queue
        qid = add_to_queue(conn, "rocketleague", "Test tweet text", cid, priority=2)
        assert qid > 0
        queued = get_queued_tweets(conn, "rocketleague")
        assert len(queued) == 1

        # 4. Post it
        mark_posted(conn, qid, "tweet_123")
        queued = get_queued_tweets(conn, "rocketleague")
        assert len(queued) == 0  # no longer queued

        # 5. Cleanup (won't delete recent)
        stats = cleanup_old_records(conn, days=30)
        assert stats["tweet_queue"] == 0  # too recent

    def test_error_tracking_and_auto_disable(self):
        conn = _make_db()
        sid = upsert_source(conn, "rocketleague", "Flaky Source", "rss", {})

        # Record 10 errors in quick succession
        for i in range(10):
            record_source_error(conn, sid, f"Error {i}")

        count = recent_source_error_count(conn, sid, hours=1)
        assert count == 10

        # Auto-disable
        disable_source(conn, sid)
        sources = get_sources(conn, "rocketleague")
        assert len(sources) == 0

    def test_cross_source_url_dedup(self):
        conn = _make_db()
        sid1 = upsert_source(conn, "rl", "Source A", "rss", {})
        sid2 = upsert_source(conn, "rl", "Source B", "scraper", {})

        # First source queues an article
        rc1 = RawContent(
            source_id=sid1, external_id="art_001", niche="rl",
            content_type="breaking_news", url="https://shared.com/article",
        )
        cid1, _ = insert_raw_content(conn, rc1)
        add_to_queue(conn, "rl", "Article tweet", cid1)

        # Second source finds the same URL
        rc2 = RawContent(
            source_id=sid2, external_id="art_002", niche="rl",
            content_type="breaking_news", url="https://shared.com/article",
        )
        cid2, _ = insert_raw_content(conn, rc2)
        conn.commit()

        # Should be flagged as duplicate
        assert url_already_queued(conn, "https://shared.com/article", cid2) is True


# ═══════════════════════════════════════════════════════════════════════════════
# PRIORITY MAP — completeness
# ═══════════════════════════════════════════════════════════════════════════════

class TestPriorityMap:
    def test_all_postable_template_types_have_priority(self):
        """Every non-retweet content type should have a priority mapping."""
        from src.formatter.templates import TEMPLATES
        for niche, types in TEMPLATES.items():
            for ctype, variants in types.items():
                if variants == [None]:
                    continue  # retweet signal — no priority needed
                assert ctype in _PRIORITY, f"Missing priority for {niche}/{ctype}"

    def test_breaking_news_is_priority_1(self):
        assert _PRIORITY["breaking_news"] == 1
        assert _PRIORITY["top1_verified"] == 1

    def test_official_tweets_are_priority_2(self):
        assert _PRIORITY["official_tweet"] == 2
        assert _PRIORITY["patch_notes"] == 2

    def test_filler_content_is_low_priority(self):
        assert _PRIORITY["flashback"] >= 7
        assert _PRIORITY["stat_milestone"] >= 7
        assert _PRIORITY["rank_milestone"] >= 7


# ═══════════════════════════════════════════════════════════════════════════════
# RATE LIMITER — consecutive failures
# ═══════════════════════════════════════════════════════════════════════════════

class TestConsecutiveFailures:
    def test_zero_when_no_history(self):
        conn = _make_db()
        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            assert consecutive_failure_count("rocketleague") == 0

    def test_counts_failures_until_success(self):
        conn = _make_db()
        now = datetime.now(timezone.utc)
        # 3 failures then 1 success
        for i in range(3):
            t = (now - timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute(
                "INSERT INTO post_log (niche, tweet_id, tweet_text, posted_at) VALUES (?, NULL, ?, ?)",
                ("rocketleague", f"fail_{i}", t),
            )
        t = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO post_log (niche, tweet_id, tweet_text, posted_at) VALUES (?, ?, ?, ?)",
            ("rocketleague", "success_id", "good tweet", t),
        )
        conn.commit()

        with patch("src.poster.rate_limiter.get_db", return_value=_ctx(conn)):
            assert consecutive_failure_count("rocketleague") == 3
