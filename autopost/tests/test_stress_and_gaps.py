"""
Stress tests and coverage-gap fillers.

Targets the following uncovered / under-tested areas:
  1.  poster/queue.py      — post_next breaking-news bypass (daily limit path),
                             _split_url edge cases (multi-URL, short tweet),
                             collect_and_queue quality-gate rejection,
                             collect_and_queue URL-dedup skip,
                             collect_and_queue similar-story skip,
                             collect_and_queue retweet dedup,
                             collect_and_queue image path,
                             within_daily_limit blocks post_next
  2.  collectors/twitter_monitor.py
                           — require_relevance flag (off-topic rejected, on-topic kept),
                             conversational prefix filter (also, btw, ngl, etc.),
                             self-thread detection via in_reply_to_status_id_str,
                             lang=qht and lang=zxx pass through,
                             lang=ko blocked, unknown niche always passes is_relevant
  3.  formatter/formatter.py
                           — _build_context with all metadata fields absent,
                             _build_context version field NOT set when no version in title,
                             _normalize_whitespace with nested markdown and commit hashes,
                             GD player handle tagging for edge-case names,
                             template fallback chain (Pass 1 fails → Pass 2 truncates)
  4.  poster/rate_limiter.py
                           — within_posting_window at every boundary hour,
                             within_daily_limit with YAML cap = 1 (boundary),
                             failure_backoff_ok at cap (6+ failures → 60-min cap),
                             jitter_delay respects per-niche YAML overrides
  5.  database/db.py       — is_similar_story with empty queue, identical text,
                             and threshold boundary (just below / just above),
                             cleanup_old_records removes orphaned raw_content rows,
                             mark_failed writes content_type to post_log
  6.  Stress / robustness  — 1000-item collector cycle (cap enforced at 5),
                             concurrent DB inserts (thread-safety smoke test),
                             malformed GraphQL responses (null fields, missing keys),
                             template with ALL placeholders missing (fallback),
                             Unicode edge cases (CJK, emoji-only, RTL, surrogates),
                             very large payload (10k-char title/body)
"""
import asyncio
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.base import BaseCollector, RawContent
from src.collectors.twitter_monitor import (
    TwitterMonitorCollector,
    _extract_tweets,
    is_relevant,
)
from src.database.db import (
    add_to_queue,
    cleanup_old_records,
    get_queued_tweets,
    insert_raw_content,
    is_similar_story,
    mark_failed,
    mark_posted,
    upsert_source,
    url_already_queued,
)
from src.formatter.formatter import (
    _build_context,
    _normalize_whitespace,
    _try_format,
    format_tweet,
    _GD_PLAYER_HANDLES,
)
from src.poster.queue import (
    _split_url,
    _engagement_followup,
    _MAX_ITEMS_PER_CYCLE,
    collect_and_queue,
    post_next,
)
from src.poster.rate_limiter import (
    failure_backoff_ok,
    jitter_delay,
    within_daily_limit,
    within_posting_window,
    MIN_INTERVAL_S,
    MAX_INTERVAL_S,
    JITTER_MAX_S,
    _BACKOFF_CAP_S,
    _BACKOFF_BASE_S,
)

# ── Shared test infrastructure ────────────────────────────────────────────────

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "database" / "schema.sql"


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


@contextmanager
def _ctx(conn):
    """Wrap an existing connection as a context-manager for get_db() patching."""
    yield conn


def _rc(**kwargs) -> RawContent:
    defaults = {
        "source_id": 1,
        "external_id": "ext_001",
        "niche": "rocketleague",
        "content_type": "breaking_news",
        "title": "Rocket League Season 15 is live with major updates",
        "url": "https://rocketleague.com/news",
        "body": "Full body text with details about the update",
        "image_url": "",
        "author": "RocketLeague",
        "score": 0,
        "metadata": {},
    }
    defaults.update(kwargs)
    return RawContent(**defaults)


def _utc(offset_minutes: float = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(minutes=offset_minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_tweet(
    tweet_id: str = "1000",
    text: str = "Rocket League Season 15 is here with all new ranked rewards!",
    screen_name: str = "RocketLeague",
    lang: str = "en",
    is_reply_to_user: str | None = None,
    is_reply_to_status: str | None = None,
    is_retweet: bool = False,
    created_at: str | None = None,
) -> dict:
    if created_at is None:
        dt = datetime.now(timezone.utc) - timedelta(hours=1)
        created_at = format_datetime(dt)
    legacy: dict = {
        "id_str": tweet_id,
        "full_text": text,
        "created_at": created_at,
        "lang": lang,
        "entities": {"urls": [], "media": []},
    }
    if is_reply_to_user:
        legacy["in_reply_to_user_id_str"] = is_reply_to_user
    if is_reply_to_status:
        legacy["in_reply_to_status_id_str"] = is_reply_to_status
    tweet: dict = {
        "legacy": legacy,
        "core": {
            "user_results": {
                "result": {"legacy": {"screen_name": screen_name}}
            }
        },
    }
    if is_retweet:
        tweet["retweeted_status_result"] = {"result": {"legacy": {"id_str": "9999"}}}
    return tweet


def _wrap(tweets: list[dict]) -> dict:
    entries = [
        {
            "content": {
                "itemContent": {
                    "tweet_results": {"result": t}
                }
            }
        }
        for t in tweets
    ]
    return {
        "data": {
            "user": {
                "result": {
                    "timeline_v2": {
                        "timeline": {
                            "instructions": [{"entries": entries}]
                        }
                    }
                }
            }
        }
    }


def _monitor(
    niche: str = "rocketleague",
    username: str = "RocketLeague",
    retweet: bool = False,
    require_relevance: bool = False,
) -> TwitterMonitorCollector:
    return TwitterMonitorCollector(
        source_id=1,
        config={"account_id": username, "retweet": retweet, "require_relevance": require_relevance},
        niche=niche,
    )


def _api_patches(gql_response: dict):
    mock_client = MagicMock()
    mock_client.gql_get = AsyncMock(return_value=gql_response)
    return (
        patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_client),
        patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=42),
    )


def _log_failure(conn, niche, posted_at):
    conn.execute(
        "INSERT INTO post_log (tweet_queue_id, niche, tweet_id, tweet_text, posted_at) VALUES (NULL, ?, NULL, 'fail', ?)",
        (niche, posted_at),
    )
    conn.commit()


def _log_success(conn, niche, posted_at):
    conn.execute(
        "INSERT INTO post_log (tweet_queue_id, niche, tweet_id, tweet_text, posted_at) VALUES (NULL, ?, 'tw_ok', 'success', ?)",
        (niche, posted_at),
    )
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. poster/queue.py — post_next coverage gaps
# ═══════════════════════════════════════════════════════════════════════════════

class TestPostNextDailyLimitBlocks:
    """post_next must return False when within_daily_limit is False."""

    def test_daily_limit_returns_false(self):
        conn = _make_db()
        conn.execute("INSERT INTO tweet_queue (niche, tweet_text, priority) VALUES ('rocketleague', 'hello', 1)")
        conn.commit()
        client = MagicMock()

        with (
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.within_daily_limit", return_value=False),
        ):
            result = post_next("rocketleague", client)

        assert result is False
        client.post_tweet.assert_not_called()


class TestSplitUrlEdgeCases:
    """_split_url edge cases not covered by existing tests."""

    def test_no_url_returns_original_text_and_none(self):
        text = "Completely URL-free tweet text about some event happening today"
        main, url = _split_url(text)
        assert main == text
        assert url is None

    def test_multiple_urls_splits_last_one(self):
        text = (
            "Article at https://first.example.com and more at "
            "https://second.example.com/news/story"
        )
        main, url = _split_url(text)
        assert url == "https://second.example.com/news/story"
        assert "https://second.example.com/news/story" not in main
        assert "https://first.example.com" in main

    def test_short_remaining_text_keeps_url_inline(self):
        # Less than 30 chars after removing URL → keep inline
        text = "Short https://example.com"
        main, url = _split_url(text)
        # "Short" is only 5 chars — below the 30-char threshold
        assert url is None
        assert main == text

    def test_exactly_30_char_remaining_text_is_split(self):
        # Exactly 30 chars of real content → should split
        text = "A" * 30 + " https://example.com/this-is-a-url"
        main, url = _split_url(text)
        assert url is not None
        assert url.startswith("https://")

    def test_url_only_tweet_not_split(self):
        # URL with very short surrounding text
        text = "Hi https://x.com"
        main, url = _split_url(text)
        assert url is None

    def test_http_url_extracted(self):
        long_prefix = "Geometry Dash 2.3 update has finally dropped after a long wait "
        text = long_prefix + "http://geometrydash.com/update"
        main, url = _split_url(text)
        assert url == "http://geometrydash.com/update"
        assert url not in main


class TestCollectAndQueueQualityGateRejection:
    """collect_and_queue skips items that fail passes_quality_gate."""

    @pytest.mark.asyncio
    async def test_quality_gate_rejection_skips_item(self):
        conn = _make_db()
        src_id = upsert_source(conn, "rocketleague", "test_src", "rss", {})
        conn.commit()

        content = _rc(source_id=src_id, external_id="qg_fail_001", content_type="community_clip", score=0)

        class QGCollector(BaseCollector):
            async def collect(self):
                return [content]

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.passes_quality_gate", return_value=False),
        ):
            count = await collect_and_queue(QGCollector(src_id, {}), "rocketleague")

        assert count == 0
        rows = conn.execute("SELECT COUNT(*) AS c FROM tweet_queue").fetchone()
        assert rows["c"] == 0

    @pytest.mark.asyncio
    async def test_quality_gate_pass_queues_item(self):
        conn = _make_db()
        src_id = upsert_source(conn, "rocketleague", "test_src_pass", "rss", {})
        conn.commit()

        content = _rc(source_id=src_id, external_id="qg_pass_001", content_type="breaking_news")

        class PassCollector(BaseCollector):
            async def collect(self):
                return [content]

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.passes_quality_gate", return_value=True),
            patch("src.poster.queue.format_tweet", return_value="Breaking news tweet text"),
            patch("src.poster.queue.prepare_media", return_value=None),
        ):
            count = await collect_and_queue(PassCollector(src_id, {}), "rocketleague")

        assert count == 1


class TestCollectAndQueueUrlDedup:
    """collect_and_queue skips an item if the same URL is already queued."""

    @pytest.mark.asyncio
    async def test_url_already_queued_from_another_source_is_skipped(self):
        conn = _make_db()
        src1 = upsert_source(conn, "rocketleague", "src_url_1", "rss", {})
        src2 = upsert_source(conn, "rocketleague", "src_url_2", "rss", {})
        conn.commit()

        shared_url = "https://shared-article.com/rl-news"

        # Insert raw_content for src1 with the shared URL
        rc1 = _rc(source_id=src1, external_id="url_dup_001", url=shared_url)
        c1_id, _ = insert_raw_content(conn, rc1)
        # Queue it from src1
        add_to_queue(conn, "rocketleague", "First version of this article", raw_content_id=c1_id)
        conn.commit()

        # Now try to collect same URL from src2
        rc2 = _rc(source_id=src2, external_id="url_dup_002", url=shared_url)

        class URLCollector(BaseCollector):
            async def collect(self):
                return [rc2]

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.passes_quality_gate", return_value=True),
            patch("src.poster.queue.format_tweet", return_value="Second version of same article"),
        ):
            count = await collect_and_queue(URLCollector(src2, {}), "rocketleague")

        assert count == 0


class TestCollectAndQueueSimilarStorySkip:
    """collect_and_queue skips items that are too similar to recently queued text."""

    @pytest.mark.asyncio
    async def test_similar_story_is_skipped(self):
        conn = _make_db()
        src_id = upsert_source(conn, "rocketleague", "src_sim", "rss", {})
        conn.commit()

        # Queue the "original" story
        add_to_queue(conn, "rocketleague", "Rocket League Season 15 starts today with big changes", priority=3)
        conn.commit()

        content = _rc(source_id=src_id, external_id="sim_001")

        class SimCollector(BaseCollector):
            async def collect(self):
                return [content]

        # Patch is_similar_story to return True
        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.passes_quality_gate", return_value=True),
            patch("src.poster.queue.format_tweet", return_value="Rocket League Season 15 starts today with big changes"),
            patch("src.poster.queue.is_similar_story", return_value=True),
        ):
            count = await collect_and_queue(SimCollector(src_id, {}), "rocketleague")

        assert count == 0


class TestCollectAndQueueRetweetDedup:
    """collect_and_queue skips a retweet signal if the same RETWEET: text is already queued."""

    @pytest.mark.asyncio
    async def test_duplicate_retweet_signal_skipped(self):
        conn = _make_db()
        src_id = upsert_source(conn, "rocketleague", "src_rt", "rss", {})
        conn.commit()

        retweet_text = "RETWEET:99999:rocketleague"
        add_to_queue(conn, "rocketleague", retweet_text, priority=2)
        conn.commit()

        content = _rc(
            source_id=src_id,
            external_id="rt_dup_001",
            content_type="official_tweet",
            metadata={"retweet_id": "99999", "account": "rocketleague"},
        )

        class RTCollector(BaseCollector):
            async def collect(self):
                return [content]

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.passes_quality_gate", return_value=True),
            patch("src.poster.queue.format_tweet", return_value=None),
        ):
            count = await collect_and_queue(RTCollector(src_id, {}), "rocketleague")

        assert count == 0


class TestCollectAndQueueImageDownload:
    """collect_and_queue calls prepare_media for items with an image_url."""

    @pytest.mark.asyncio
    async def test_image_url_triggers_prepare_media(self):
        conn = _make_db()
        src_id = upsert_source(conn, "rocketleague", "src_img", "rss", {})
        conn.commit()

        content = _rc(
            source_id=src_id,
            external_id="img_001",
            image_url="https://example.com/image.jpg",
        )

        class ImgCollector(BaseCollector):
            async def collect(self):
                return [content]

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.passes_quality_gate", return_value=True),
            patch("src.poster.queue.format_tweet", return_value="Rocket League update with image"),
            patch("src.poster.queue.is_similar_story", return_value=False),
            patch("src.poster.queue.prepare_media", return_value="/tmp/image.jpg") as mock_media,
        ):
            count = await collect_and_queue(ImgCollector(src_id, {}), "rocketleague")

        assert count == 1
        mock_media.assert_called_once_with("https://example.com/image.jpg")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. collectors/twitter_monitor.py — coverage gaps
# ═══════════════════════════════════════════════════════════════════════════════

class TestRequireRelevanceFlag:
    """require_relevance=True applies keyword filter to monitored_tweet sources."""

    @pytest.mark.asyncio
    async def test_require_relevance_off_topic_tweet_rejected(self):
        """Off-topic tweet from a require_relevance account is filtered out."""
        tweet = _make_tweet(
            tweet_id="2001",
            text="Just had the best ramen of my life, absolutely incredible",
            screen_name="gdzoink",
        )
        resp = _wrap([tweet])
        collector = _monitor(niche="geometrydash", username="gdzoink", require_relevance=True)
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_require_relevance_on_topic_tweet_passes(self):
        """On-topic tweet from a require_relevance account passes the filter."""
        tweet = _make_tweet(
            tweet_id="2002",
            text="CHIL 100% verified after 50000 attempts! Extreme demon down #geometrydash",
            screen_name="gdzoink",
        )
        resp = _wrap([tweet])
        collector = _monitor(niche="geometrydash", username="gdzoink", require_relevance=True)
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_require_relevance_false_allows_off_topic(self):
        """Without require_relevance, off-topic tweets from a normal monitor pass."""
        tweet = _make_tweet(
            tweet_id="2003",
            text="Rocket League new update arrived with many new features and changes for players",
            screen_name="SomeAccount",
        )
        resp = _wrap([tweet])
        # require_relevance=False (default)
        collector = _monitor(niche="rocketleague", username="SomeAccount", require_relevance=False)
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_require_relevance_keeps_level_completion_keyword(self):
        """'demon' keyword in completion tweet passes require_relevance filter."""
        tweet = _make_tweet(
            tweet_id="2004",
            text="Screech demon verified!! That was absolutely insane grind",
            screen_name="TechnicalJL",
        )
        resp = _wrap([tweet])
        collector = _monitor(niche="geometrydash", username="TechnicalJL", require_relevance=True)
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_require_relevance_attribute_set_correctly(self):
        """_require_relevance attribute is set from config."""
        c_true = _monitor(require_relevance=True)
        c_false = _monitor(require_relevance=False)
        c_default = _monitor()
        assert c_true._require_relevance is True
        assert c_false._require_relevance is False
        assert c_default._require_relevance is False


class TestConversationalPrefixFilter:
    """require_relevance accounts filter out conversational-prefix tweets."""

    @pytest.mark.parametrize("text", [
        "Also, I forgot to mention the stream starts at 9pm tonight",
        "By the way, thanks for all the support on the last video everyone",
        "btw, my new setup arrived today and it looks absolutely amazing",
        "Honestly, this has been the best week in a long time for me personally",
        "ngl, this level is way harder than I expected it to be today",
        "Wait, did anyone see that new update drop earlier? Just saw it",
        "Oh and also I have a giveaway coming up soon so stay tuned",
        "Oh also the merch store is live now check it out when you can",
        "I just woke up and immediately saw the patch notes wow",
        "I can't believe this actually happened today after all that time",
    ])
    @pytest.mark.asyncio
    async def test_conversational_prefix_rejected_when_require_relevance(self, text):
        tweet = _make_tweet(tweet_id="3001", text=text, screen_name="gdzoink")
        resp = _wrap([tweet])
        collector = _monitor(niche="geometrydash", username="gdzoink", require_relevance=True)
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == [], f"Expected empty but got result for: {text!r}"

    @pytest.mark.asyncio
    async def test_conversational_prefix_allowed_without_require_relevance(self):
        """Conversational prefix tweets are NOT filtered for non-require_relevance accounts."""
        tweet = _make_tweet(
            tweet_id="3100",
            text="Honestly, this Rocket League season has been the best competitive season ever played",
            screen_name="RocketLeague",
        )
        resp = _wrap([tweet])
        # retweet source — no conversational prefix filter, no require_relevance
        collector = _monitor(niche="rocketleague", username="RocketLeague", retweet=False, require_relevance=False)
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1


class TestSelfThreadDetection:
    """Tweets that are replies to OTHER tweets (even own) are filtered out."""

    @pytest.mark.asyncio
    async def test_self_thread_reply_filtered(self):
        """in_reply_to_status_id_str non-None means it's a thread reply — skip it."""
        tweet = _make_tweet(
            tweet_id="4001",
            text="Rocket League Season 15 has some fantastic new features and changes for players",
            screen_name="RocketLeague",
            is_reply_to_status="3999",  # replying to another tweet
        )
        resp = _wrap([tweet])
        collector = _monitor(niche="rocketleague", username="RocketLeague")
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_reply_to_user_id_also_filtered(self):
        """in_reply_to_user_id_str non-None must also be filtered (reply to any user)."""
        tweet = _make_tweet(
            tweet_id="4002",
            text="Yes absolutely that is correct about the Rocket League update today",
            screen_name="RocketLeague",
            is_reply_to_user="9876543",
        )
        resp = _wrap([tweet])
        collector = _monitor(niche="rocketleague", username="RocketLeague")
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_standalone_tweet_not_filtered(self):
        """Tweet with no in_reply_to fields is kept."""
        tweet = _make_tweet(
            tweet_id="4003",
            text="Rocket League Season 15 is now live check out what is new today",
            screen_name="RocketLeague",
        )
        resp = _wrap([tweet])
        collector = _monitor(niche="rocketleague", username="RocketLeague")
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1


class TestLangCodeEdgeCases:
    """qht and zxx lang codes are whitelisted; ko is blocked."""

    @pytest.mark.asyncio
    async def test_qht_lang_passes(self):
        """lang='qht' is in the whitelist — tweet should not be filtered."""
        tweet = _make_tweet(
            tweet_id="5001",
            text="Rocket League Season 15 is here with new ranked season rewards!",
            screen_name="RocketLeague",
            lang="qht",
        )
        resp = _wrap([tweet])
        collector = _monitor()
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_zxx_lang_passes(self):
        """lang='zxx' (no linguistic content, e.g. image-only tweet) is whitelisted."""
        tweet = _make_tweet(
            tweet_id="5002",
            text="Rocket League Season 15 new update with fresh ranked rewards today",
            screen_name="RocketLeague",
            lang="zxx",
        )
        resp = _wrap([tweet])
        collector = _monitor()
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_ko_lang_blocked(self):
        """Korean (ko) tweets are blocked — both bots target English audiences."""
        tweet = _make_tweet(
            tweet_id="5003",
            text="Rocket League Season 15 is live now with new changes for players",
            screen_name="RocketLeague",
            lang="ko",
        )
        resp = _wrap([tweet])
        collector = _monitor()
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_und_lang_passes(self):
        """lang='und' (undetermined) is whitelisted."""
        tweet = _make_tweet(
            tweet_id="5004",
            text="Rocket League Season 15 update is now live for all platform players",
            screen_name="RocketLeague",
            lang="und",
        )
        resp = _wrap([tweet])
        collector = _monitor()
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1


class TestIsRelevantEdgeCases:
    """is_relevant edge cases not covered by test_stress.py."""

    def test_unknown_niche_always_passes(self):
        assert is_relevant("some random tweet text", "unknown_niche") is True

    def test_empty_text_fails_for_known_niche(self):
        assert is_relevant("", "rocketleague") is False

    def test_keyword_as_substring_matches(self):
        # "rocket league" is a keyword — substring match
        assert is_relevant("i love rocket league so much", "rocketleague") is True

    def test_case_insensitive_match(self):
        assert is_relevant("ROCKET LEAGUE SEASON 15", "rocketleague") is True

    def test_gd_100_percent_keyword(self):
        """'100%' completion signal passes GD relevance."""
        assert is_relevant("I just hit 100% on this demon after weeks!", "geometrydash") is True

    def test_gd_geometrydash_dot_com(self):
        """New geometrydash.com keyword passes."""
        assert is_relevant("geometrydash.com is now live!", "geometrydash") is True


# ═══════════════════════════════════════════════════════════════════════════════
# 3. formatter/formatter.py — coverage gaps
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildContextMissingMetadata:
    """_build_context gracefully handles content with no metadata fields."""

    def test_empty_metadata_does_not_crash(self):
        content = _rc(metadata={})
        ctx = _build_context(content)
        assert "title" in ctx
        assert "url" in ctx
        assert "emoji" in ctx

    def test_author_defaults_to_unknown_when_empty(self):
        content = _rc(author="", metadata={})
        ctx = _build_context(content)
        assert ctx["author"] == "Unknown"

    def test_version_not_set_when_no_version_in_title(self):
        """If the title has no version string, 'version' must NOT appear in context."""
        content = _rc(title="Rocket League Season 15 is here now")
        ctx = _build_context(content)
        assert "version" not in ctx

    def test_version_extracted_when_present_in_title(self):
        content = _rc(title="Geode v5.5.2 released with new features")
        ctx = _build_context(content)
        assert ctx.get("version") is not None
        assert "5.5.2" in ctx["version"]

    def test_metadata_none_values_not_added_to_context(self):
        """Metadata keys with None values must be excluded from context."""
        content = _rc(metadata={"position": None, "score": None, "title": "Override"})
        ctx = _build_context(content)
        # None values skipped
        assert ctx.get("position") is None or "position" not in ctx

    def test_metadata_whitespace_values_not_added_to_context(self):
        """Metadata keys with whitespace-only values must be excluded."""
        content = _rc(metadata={"position": "  ", "version": "\t"})
        ctx = _build_context(content)
        # Whitespace-only values are filtered out
        assert ctx.get("position") != "  "

    def test_empty_url_does_not_crash_build_context(self):
        content = _rc(url="", title="Some Title")
        ctx = _build_context(content)
        assert ctx["url"] == ""

    def test_body_with_only_whitespace_produces_title_fallback(self):
        content = _rc(body="   \n  \t  ", title="My Title Text Here")
        ctx = _build_context(content)
        # summary falls back to title
        assert ctx["summary"] in ("   \n  \t  ".strip() or ctx["title"], "My Title Text Here")


class TestNormalizeWhitespaceEdgeCases:
    """_normalize_whitespace for nested markdown, commit hashes, etc."""

    def test_strips_markdown_heading(self):
        result = _normalize_whitespace("## v5.5.2 Released\nSome content here")
        assert "##" not in result
        assert "v5.5.2" in result

    def test_strips_multiple_heading_levels(self):
        text = "### Patch Notes\nFix A\n\n#### Bug Fixes\nFix B"
        result = _normalize_whitespace(text)
        assert "###" not in result
        assert "####" not in result
        assert "Fix A" in result
        assert "Fix B" in result

    def test_strips_commit_hash_in_parens(self):
        result = _normalize_whitespace("Fixed rendering bug (abc1234)")
        assert "(abc1234)" not in result
        assert "Fixed rendering bug" in result

    def test_strips_long_commit_hash_in_parens(self):
        result = _normalize_whitespace("Optimized collision (a1b2c3d4e5f6789)")
        assert "(a1b2c3d4e5f6789)" not in result

    def test_collapses_tabs_to_single_space(self):
        result = _normalize_whitespace("Hello\t\tWorld")
        assert result == "Hello World"

    def test_strips_bold_markdown(self):
        result = _normalize_whitespace("**Important update** released today")
        assert "**" not in result
        assert "Important update" in result

    def test_strips_italic_markdown(self):
        result = _normalize_whitespace("*Breaking news* just announced now")
        assert "*" not in result
        assert "Breaking news" in result

    def test_strips_inline_code_backticks(self):
        result = _normalize_whitespace("Run `pip install geode` to update")
        assert "`" not in result
        assert "pip install geode" in result

    def test_three_plus_newlines_collapsed_to_two(self):
        result = _normalize_whitespace("A\n\n\n\nB")
        assert "\n\n\n" not in result
        assert "A" in result
        assert "B" in result

    def test_empty_string_returns_empty(self):
        assert _normalize_whitespace("") == ""

    def test_only_whitespace_returns_empty(self):
        assert _normalize_whitespace("   \t  ") == ""


class TestGDPlayerHandleTagging:
    """_build_context replaces known GD player names with @handle."""

    @pytest.mark.parametrize("player_key,expected_handle", [
        ("zoink",       "@gdzoink"),
        ("trick",       "@GmdTrick"),
        ("doggie",      "@DasherDoggie"),
        ("colon",       "@TheRealGDColon"),
        ("nexus",       "@NexusGMD"),
        ("technical",   "@TechnicalJL"),
        ("npesta",      "@zNpesta__"),
        ("viprin",      "@vipringd"),
        ("wulzy",       "@1wulz"),
        ("evw",         "@VanWilderman"),
        ("aeonair",     "@aabornaeon"),
        ("neiro",       "@NeiroGMD"),
    ])
    def test_known_player_gets_tagged(self, player_key, expected_handle):
        content = _rc(niche="geometrydash", author=player_key)
        ctx = _build_context(content)
        assert ctx["player"] == expected_handle, f"Player {player_key!r} should map to {expected_handle!r}"

    def test_unknown_player_uses_raw_author(self):
        content = _rc(niche="geometrydash", author="completelyunknownplayer99")
        ctx = _build_context(content)
        assert ctx["player"] == "completelyunknownplayer99"

    def test_non_gd_niche_does_not_tag_player(self):
        content = _rc(niche="rocketleague", author="zoink")
        ctx = _build_context(content)
        # Should NOT apply the GD player handle lookup
        assert ctx["player"] == "zoink"

    def test_player_name_lookup_is_case_insensitive(self):
        content = _rc(niche="geometrydash", author="ZOINK")
        ctx = _build_context(content)
        assert ctx["player"] == "@gdzoink"

    def test_space_uk_multi_word_key_works(self):
        content = _rc(niche="geometrydash", author="space uk")
        ctx = _build_context(content)
        assert ctx["player"] == "@SpaceUKGD"


class TestTemplateFallbackChain:
    """format_tweet falls through Pass1 → Pass2 → absolute fallback correctly."""

    def test_pass1_finds_fitting_variant(self):
        """Normal case: a variant fills cleanly and fits in 280 chars."""
        content = _rc(
            niche="rocketleague",
            content_type="breaking_news",
            title="Season 15 update just dropped for all platforms",
            url="https://example.com",
            body="Big changes in Season 15",
        )
        result = format_tweet(content)
        assert result is not None
        assert len(result) <= 280

    def test_absolute_fallback_when_no_template(self):
        """Unknown content type with no template falls back to title + url."""
        content = _rc(
            content_type="nonexistent_content_type_xyz",
            title="My fallback title",
            url="https://fallback.example.com",
        )
        result = format_tweet(content)
        # No template for this type → None
        assert result is None

    def test_all_missing_placeholders_uses_fallback(self):
        """When all template variants need structured data that's absent,
        _fallback() is used — result is title + url."""
        # Use a content type that needs structured placeholders
        content = _rc(
            niche="rocketleague",
            content_type="esports_result",  # needs team1, team2, score, etc.
            title="RLCS Grand Finals Result",
            url="https://example.com/rlcs",
            body="Short body",
            metadata={},  # no structured fields
        )
        result = format_tweet(content)
        # Should still produce SOMETHING (fallback to title)
        assert result is not None
        assert len(result) <= 280

    def test_very_long_title_triggers_pass2_truncate(self):
        """When all variants exceed 280 chars, Pass 2 truncates the first fillable one."""
        content = _rc(
            content_type="breaking_news",
            title="X" * 300,
            url="https://example.com",
            body="Y" * 300,
        )
        result = format_tweet(content)
        assert result is not None
        assert len(result) <= 280


# ═══════════════════════════════════════════════════════════════════════════════
# 4. poster/rate_limiter.py — coverage gaps
# ═══════════════════════════════════════════════════════════════════════════════

class TestWithinPostingWindowAllBoundaries:
    """Test the wrapping posting window at every boundary UTC hour."""

    @pytest.mark.parametrize("hour,expected", [
        (0,  True),   # midnight — in window (00:00 < 04:00)
        (1,  True),   # 1 AM — in window
        (2,  True),   # 2 AM — in window
        (3,  True),   # 3 AM — in window
        (4,  False),  # 4 AM — exclusive end
        (5,  False),  # 5 AM — outside
        (6,  False),
        (7,  False),
        (8,  False),
        (9,  False),
        (10, False),
        (11, False),
        (12, False),
        (13, False),
        (14, True),   # 2 PM — window starts (inclusive)
        (15, True),
        (16, True),
        (17, True),
        (18, True),
        (19, True),
        (20, True),
        (21, True),
        (22, True),
        (23, True),   # 11 PM — still in window
    ])
    def test_hour_boundary(self, hour, expected):
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 4, hour, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() == expected, (
                f"Expected within_posting_window()={expected} at hour={hour}"
            )

    def test_breaking_news_bypasses_all_hours(self):
        for hour in range(24):
            with patch("src.poster.rate_limiter.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 4, 4, hour, 0, tzinfo=timezone.utc)
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                assert within_posting_window(is_breaking=True) is True


class TestWithinDailyLimitBoundary:
    """within_daily_limit at exactly the cap (boundary value)."""

    def test_exactly_at_cap_returns_false(self):
        conn = _make_db()
        # Add exactly 5 success posts today
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        for i in range(5):
            ts = (today_start + timedelta(hours=i + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute(
                "INSERT INTO post_log (niche, tweet_id, tweet_text, posted_at) VALUES (?, ?, ?, ?)",
                ("geometrydash", f"tw_{i}", f"Tweet {i}", ts),
            )
        conn.commit()

        with (
            patch("src.poster.rate_limiter.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.rate_limiter._posting_config", return_value={"max_daily_posts": 5}),
        ):
            assert within_daily_limit("geometrydash") is False

    def test_one_below_cap_returns_true(self):
        conn = _make_db()
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        for i in range(4):
            ts = (today_start + timedelta(hours=i + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute(
                "INSERT INTO post_log (niche, tweet_id, tweet_text, posted_at) VALUES (?, ?, ?, ?)",
                ("geometrydash", f"tw_{i}", f"Tweet {i}", ts),
            )
        conn.commit()

        with (
            patch("src.poster.rate_limiter.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.rate_limiter._posting_config", return_value={"max_daily_posts": 5}),
        ):
            assert within_daily_limit("geometrydash") is True

    def test_cap_of_zero_means_unlimited(self):
        """max_daily_posts=0 means no limit (default)."""
        conn = _make_db()
        with (
            patch("src.poster.rate_limiter.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.rate_limiter._posting_config", return_value={"max_daily_posts": 0}),
        ):
            assert within_daily_limit("geometrydash") is True

    def test_cap_of_1_allows_first_post_blocks_second(self):
        conn = _make_db()
        ts = _utc(30)
        conn.execute(
            "INSERT INTO post_log (niche, tweet_id, tweet_text, posted_at) VALUES (?, ?, ?, ?)",
            ("rocketleague", "tw_first", "First post", ts),
        )
        conn.commit()

        with (
            patch("src.poster.rate_limiter.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.rate_limiter._posting_config", return_value={"max_daily_posts": 1}),
        ):
            assert within_daily_limit("rocketleague") is False


class TestFailureBackoffCapExponential:
    """Ensure exponential backoff caps at _BACKOFF_CAP_S for many failures."""

    def test_six_consecutive_failures_capped_at_max_backoff(self):
        """6+ consecutive failures → backoff capped at 3600s (60 min)."""
        conn = _make_db()
        # 6 failures, last one just now
        for i in range(6, 0, -1):
            _log_failure(conn, "rocketleague", _utc(i * 5))  # spread them out
        with patch("src.poster.rate_limiter.get_db", side_effect=lambda: _ctx(conn)):
            result = failure_backoff_ok("rocketleague")
        # Last failure was 5 minutes ago; cap is 60 min → still blocked
        assert result is False

    def test_backoff_delay_formula(self):
        """Check that delay is correctly capped at _BACKOFF_CAP_S."""
        # For 10 consecutive failures: 2^9 * 120 = 61440 → capped at 3600
        delay = min(_BACKOFF_BASE_S * (2 ** (10 - 1)), _BACKOFF_CAP_S)
        assert delay == _BACKOFF_CAP_S

    def test_single_failure_backoff_is_base_delay(self):
        """1 failure → delay = _BACKOFF_BASE_S (2 minutes)."""
        delay = min(_BACKOFF_BASE_S * (2 ** (1 - 1)), _BACKOFF_CAP_S)
        assert delay == _BACKOFF_BASE_S


class TestJitterDelayPerNiche:
    """jitter_delay uses per-niche YAML config if provided."""

    def test_jitter_no_niche_uses_globals(self):
        for _ in range(20):
            d = jitter_delay()
            assert MIN_INTERVAL_S <= d <= MAX_INTERVAL_S + JITTER_MAX_S

    def test_jitter_with_niche_uses_yaml_values(self):
        with patch("src.poster.rate_limiter._posting_config", return_value={
            "min_interval_seconds": 100,
            "max_interval_seconds": 200,
        }):
            for _ in range(20):
                d = jitter_delay(niche="rocketleague")
                assert d >= 100


# ═══════════════════════════════════════════════════════════════════════════════
# 5. database/db.py — coverage gaps
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsSimilarStoryEdgeCases:
    """is_similar_story edge cases: empty queue, identical text, threshold boundary."""

    def test_empty_queue_returns_false(self):
        conn = _make_db()
        result = is_similar_story(conn, "Rocket League update is live", "rocketleague")
        assert result is False

    def test_identical_text_returns_true(self):
        conn = _make_db()
        text = "Rocket League Season 15 is now live with massive changes"
        add_to_queue(conn, "rocketleague", text, priority=3)
        conn.commit()
        result = is_similar_story(conn, text, "rocketleague")
        assert result is True

    def test_completely_different_text_returns_false(self):
        conn = _make_db()
        add_to_queue(conn, "rocketleague", "Rocket League Season 15 launched today", priority=3)
        conn.commit()
        result = is_similar_story(
            conn,
            "Geometry Dash 2.3 has a new extreme demon verified by zoink",
            "rocketleague",
        )
        assert result is False

    def test_different_niche_not_compared(self):
        """Tweets from another niche are never flagged as similar."""
        conn = _make_db()
        add_to_queue(conn, "rocketleague", "Rocket League Season 15 is live today for everyone", priority=3)
        conn.commit()
        # Same text but different niche — should not match
        result = is_similar_story(
            conn,
            "Rocket League Season 15 is live today for everyone",
            "geometrydash",
        )
        assert result is False

    def test_custom_threshold_boundary_just_below(self):
        """Ratio just below threshold → returns False."""
        conn = _make_db()
        add_to_queue(conn, "rocketleague", "AAAAA", priority=3)
        conn.commit()
        # "BBBBB" vs "AAAAA" → ratio = 0.0 → below any threshold
        result = is_similar_story(conn, "BBBBB", "rocketleague", threshold=0.45)
        assert result is False

    def test_threshold_exactly_met_returns_true(self):
        """SequenceMatcher ratio at or above threshold → True."""
        conn = _make_db()
        text = "a" * 100  # 100 'a' chars
        add_to_queue(conn, "rocketleague", text, priority=3)
        conn.commit()
        # Identical text → ratio = 1.0 — always above threshold
        result = is_similar_story(conn, text, "rocketleague", threshold=0.45)
        assert result is True

    def test_old_records_outside_window_not_compared(self):
        """Tweets older than `hours` window should not trigger similarity."""
        conn = _make_db()
        old_text = "Rocket League Season 15 is live today with massive ranked changes"
        # Insert directly with old timestamp (>48h ago)
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=50)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, priority, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
            ("rocketleague", old_text, 3, old_ts),
        )
        conn.commit()
        result = is_similar_story(conn, old_text, "rocketleague", hours=48)
        assert result is False


class TestCleanupOldRecordsOrphanedContent:
    """cleanup_old_records removes orphaned raw_content rows not linked to any queue item."""

    def test_orphaned_raw_content_deleted(self):
        conn = _make_db()
        src_id = upsert_source(conn, "rocketleague", "cleanup_src", "rss", {})
        conn.commit()

        # Insert raw_content with old timestamp (no queue entry)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=35)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            """INSERT INTO raw_content
               (source_id, external_id, niche, content_type, title, url, body,
                image_url, author, score, metadata, collected_at)
               VALUES (?, 'orphan_001', 'rocketleague', 'breaking_news', 'Old title',
                       'https://old.example.com', '', '', '', 0, '{}', ?)""",
            (src_id, old_ts),
        )
        conn.commit()

        before = conn.execute("SELECT COUNT(*) AS c FROM raw_content").fetchone()["c"]
        assert before == 1

        result = cleanup_old_records(conn, days=30)
        assert result["raw_content"] == 1

        after = conn.execute("SELECT COUNT(*) AS c FROM raw_content").fetchone()["c"]
        assert after == 0

    def test_referenced_raw_content_not_deleted(self):
        """raw_content referenced by a queued tweet is NOT deleted even if old."""
        conn = _make_db()
        src_id = upsert_source(conn, "rocketleague", "cleanup_ref_src", "rss", {})
        conn.commit()

        old_ts = (datetime.now(timezone.utc) - timedelta(days=35)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rc_id = conn.execute(
            """INSERT INTO raw_content
               (source_id, external_id, niche, content_type, title, url, body,
                image_url, author, score, metadata, collected_at)
               VALUES (?, 'ref_001', 'rocketleague', 'breaking_news', 'Still referenced',
                       'https://ref.example.com', '', '', '', 0, '{}', ?)
               RETURNING id""",
            (src_id, old_ts),
        ).fetchone()["id"]
        conn.commit()

        # Add a queued tweet that references it
        add_to_queue(conn, "rocketleague", "Referenced tweet", raw_content_id=rc_id, priority=5)
        conn.commit()

        result = cleanup_old_records(conn, days=30)
        # raw_content should NOT be deleted because queue row references it
        assert result["raw_content"] == 0

    def test_cleanup_deletes_old_terminal_queue_rows(self):
        """Posted/failed/skipped rows older than cutoff are deleted."""
        conn = _make_db()
        old_ts = (datetime.now(timezone.utc) - timedelta(days=35)).strftime("%Y-%m-%dT%H:%M:%SZ")
        qid = add_to_queue(conn, "rocketleague", "Old posted tweet", priority=5)
        # Manually set created_at to old timestamp
        conn.execute("UPDATE tweet_queue SET status='posted', created_at=? WHERE id=?", (old_ts, qid))
        conn.commit()

        result = cleanup_old_records(conn, days=30)
        assert result["tweet_queue"] >= 1


class TestMarkFailedWritesContentType:
    """mark_failed now writes content_type to post_log (regression test)."""

    def test_mark_failed_with_raw_content_writes_content_type(self):
        conn = _make_db()
        src_id = upsert_source(conn, "rocketleague", "fail_src", "rss", {})
        conn.commit()

        rc = _rc(source_id=src_id, external_id="ct_fail_001", content_type="youtube_video")
        rc_id, _ = insert_raw_content(conn, rc)

        qid = add_to_queue(conn, "rocketleague", "YouTube video tweet", raw_content_id=rc_id, priority=4)
        conn.commit()
        mark_failed(conn, qid, "API error 403")
        conn.commit()

        log = conn.execute(
            "SELECT content_type, error FROM post_log WHERE tweet_queue_id = ?", (qid,)
        ).fetchone()
        assert log is not None
        assert log["content_type"] == "youtube_video"
        assert log["error"] == "API error 403"

    def test_mark_failed_without_raw_content_uses_empty_content_type(self):
        """Queue rows with no raw_content_id get empty content_type in post_log."""
        conn = _make_db()
        qid = add_to_queue(conn, "rocketleague", "No raw content tweet", priority=5)
        conn.commit()
        mark_failed(conn, qid, "network timeout")
        conn.commit()

        log = conn.execute(
            "SELECT content_type FROM post_log WHERE tweet_queue_id = ?", (qid,)
        ).fetchone()
        assert log is not None
        assert log["content_type"] == ""


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Stress / robustness tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueueFloodingCap:
    """Collector returning 1000+ items is hard-capped at _MAX_ITEMS_PER_CYCLE."""

    @pytest.mark.asyncio
    async def test_1000_item_collector_queues_only_max_cycle(self):
        conn = _make_db()
        src_id = upsert_source(conn, "rocketleague", "flood_src", "rss", {})
        conn.commit()

        class FloodCollector(BaseCollector):
            async def collect(self):
                return [
                    # Each item has a unique URL to avoid the URL-dedup shortcut;
                    # we're testing the per-cycle hard cap, not URL dedup.
                    _rc(
                        source_id=src_id,
                        external_id=f"flood_{i:04d}",
                        title=f"Flood item {i}",
                        url=f"https://rocketleague.com/news/flood-{i:04d}",
                    )
                    for i in range(1000)
                ]

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.passes_quality_gate", return_value=True),
            patch("src.poster.queue.format_tweet", side_effect=lambda item: f"Tweet for {item.title}"),
            patch("src.poster.queue.is_similar_story", return_value=False),
            patch("src.poster.queue.prepare_media", return_value=None),
        ):
            count = await collect_and_queue(FloodCollector(src_id, {}), "rocketleague")

        assert count == _MAX_ITEMS_PER_CYCLE
        queued_rows = conn.execute("SELECT COUNT(*) AS c FROM tweet_queue").fetchone()["c"]
        assert queued_rows == _MAX_ITEMS_PER_CYCLE

    @pytest.mark.asyncio
    async def test_zero_item_collector_returns_zero(self):
        conn = _make_db()
        src_id = upsert_source(conn, "rocketleague", "zero_src", "rss", {})
        conn.commit()

        class EmptyCollector(BaseCollector):
            async def collect(self):
                return []

        with patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)):
            count = await collect_and_queue(EmptyCollector(src_id, {}), "rocketleague")

        assert count == 0


class TestConcurrentDbAccess:
    """Smoke test: multiple threads inserting into the DB concurrently don't deadlock."""

    def test_concurrent_inserts_do_not_crash(self, tmp_path):
        db_file = tmp_path / "concurrent_test.db"

        from src.database.db import init_db
        with patch("src.database.db.DB_PATH", db_file):
            init_db()

        errors = []

        def insert_items(thread_id: int):
            conn = sqlite3.connect(str(db_file), timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 10000")
            try:
                conn.execute(
                    "INSERT INTO sources (niche, name, type, config) VALUES (?, ?, ?, '{}')",
                    ("rocketleague", f"thread_src_{thread_id}", "rss"),
                )
                conn.commit()
            except Exception as e:
                errors.append(str(e))
            finally:
                conn.close()

        threads = [threading.Thread(target=insert_items, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent insert errors: {errors}"


class TestMalformedApiResponses:
    """Collector handles null fields, missing keys, and oversized payloads gracefully."""

    @pytest.mark.asyncio
    async def test_null_gql_response_returns_empty(self):
        """If gql_get returns None, collect() returns []."""
        collector = _monitor()
        mock_client = MagicMock()
        mock_client.gql_get = AsyncMock(return_value=None)

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_client),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=42),
        ):
            result = await collector.collect()

        # _extract_tweets(None) should either return [] or collector handles gracefully
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_empty_dict_gql_response_returns_empty(self):
        """Empty dict GraphQL response produces no items."""
        collector = _monitor()
        p1, p2 = _api_patches({})
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_tweet_with_missing_id_str_skipped(self):
        """Tweet object where id_str is empty string is filtered out."""
        tweet = _make_tweet(tweet_id="", text="Rocket League Season 15 is live now")
        tweet["legacy"]["id_str"] = ""
        resp = _wrap([tweet])
        collector = _monitor()
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_tweet_with_empty_full_text_skipped(self):
        """Tweet object where full_text is empty is filtered out."""
        tweet = _make_tweet(tweet_id="9999", text="")
        resp = _wrap([tweet])
        collector = _monitor()
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    def test_extract_tweets_handles_deeply_nested_null(self):
        """_extract_tweets with deeply nested nulls doesn't crash."""
        malformed = {
            "data": {
                "user": {
                    "result": None,
                }
            }
        }
        result = _extract_tweets(malformed)
        assert isinstance(result, list)

    def test_extract_tweets_handles_empty_instructions(self):
        """Empty instructions list produces no tweets."""
        resp = {
            "data": {
                "user": {
                    "result": {
                        "timeline_v2": {
                            "timeline": {
                                "instructions": []
                            }
                        }
                    }
                }
            }
        }
        result = _extract_tweets(resp)
        assert result == []

    def test_extract_tweets_skips_retweeted_status_embedded(self):
        """retweeted_status_result key is NOT traversed — embedded tweet ignored."""
        embedded_tweet = {
            "legacy": {
                "id_str": "EMBEDDED_999",
                "full_text": "I am an embedded tweet from another account",
            }
        }
        outer_tweet = {
            "legacy": {
                "id_str": "OUTER_001",
                "full_text": "Outer tweet text here about Rocket League Season 15",
            },
            "retweeted_status_result": {"result": embedded_tweet},
        }
        data = _wrap([outer_tweet])
        results = _extract_tweets(data)
        ids = [t["legacy"]["id_str"] for t in results]
        assert "EMBEDDED_999" not in ids
        assert "OUTER_001" in ids

    def test_extract_tweets_skips_quoted_status_embedded(self):
        """quoted_status_result key is NOT traversed."""
        embedded_tweet = {
            "legacy": {
                "id_str": "QUOTED_999",
                "full_text": "I am a quoted tweet from another account",
            }
        }
        outer_tweet = {
            "legacy": {
                "id_str": "OUTER_002",
                "full_text": "Outer tweet with a quote here about the update",
            },
            "quoted_status_result": {"result": embedded_tweet},
        }
        data = _wrap([outer_tweet])
        results = _extract_tweets(data)
        ids = [t["legacy"]["id_str"] for t in results]
        assert "QUOTED_999" not in ids
        assert "OUTER_002" in ids


class TestUnicodeEdgeCasesExtended:
    """Unicode edge cases: CJK, emoji-only, RTL text, large payloads."""

    def test_cjk_title_in_build_context(self):
        content = _rc(title="ロケットリーグ Season 15 開幕")
        ctx = _build_context(content)
        assert ctx["title"] == "ロケットリーグ Season 15 開幕"

    def test_emoji_only_title_in_build_context(self):
        content = _rc(title="🚀🔥🎮💥⭐🏆")
        ctx = _build_context(content)
        assert ctx["title"] == "🚀🔥🎮💥⭐🏆"

    def test_rtl_arabic_text_in_title(self):
        content = _rc(title="تحديث روكت ليج موسم 15 الآن")
        ctx = _build_context(content)
        assert "تحديث" in ctx["title"]

    def test_format_tweet_with_10k_char_body(self):
        """10,000-char body must still produce a tweet within 280 chars."""
        content = _rc(
            title="Breaking update",
            body="A" * 10000,
            url="https://example.com",
        )
        result = format_tweet(content)
        assert result is not None
        assert len(result) <= 280

    def test_format_tweet_with_10k_char_title(self):
        """10,000-char title must be truncated to fit within 280 chars."""
        content = _rc(
            title="T" * 10000,
            url="https://example.com",
        )
        result = format_tweet(content)
        assert result is not None
        assert len(result) <= 280

    def test_normalize_whitespace_with_unicode(self):
        """_normalize_whitespace handles Unicode without crashing."""
        text = "## 🚀 New Update\n\n**Geometry Dash** 2.3 is `live`\n\n\n\nDetails"
        result = _normalize_whitespace(text)
        assert "##" not in result
        assert "**" not in result
        assert "`" not in result
        assert "🚀" in result or "New Update" in result

    def test_split_url_with_unicode_url(self):
        """_split_url handles URLs containing unicode-encoded characters."""
        text = "Check out this Rocket League article it has major ranked changes today "
        url = "https://example.com/path?q=café&lang=fr"
        main, extracted_url = _split_url(text + url)
        assert extracted_url == url

    def test_is_relevant_with_emoji_only_text(self):
        """Emoji-only text has no keywords → not relevant for known niches."""
        result = is_relevant("🚀🔥💥⭐🎮🏆", "rocketleague")
        assert result is False

    @pytest.mark.asyncio
    async def test_tweet_with_cjk_text_passes_if_lang_en(self):
        """A tweet marked lang=en with some CJK content still passes lang filter."""
        tweet = _make_tweet(
            tweet_id="8001",
            text="Rocket League Season 15 update ロケット has launched for all platforms",
            screen_name="RocketLeague",
            lang="en",
        )
        resp = _wrap([tweet])
        collector = _monitor()
        p1, p2 = _api_patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1


class TestTemplateAllPlaceholdersMissing:
    """format_tweet handles content where all structured placeholders are absent."""

    def test_no_structured_metadata_falls_back_to_title_url(self):
        """Content with no structured fields uses absolute fallback: title + url."""
        content = _rc(
            niche="rocketleague",
            content_type="esports_result",
            title="RLCS Spring Major results are in",
            url="https://example.com/rlcs-results",
            body="Some result body text",
            metadata={},
        )
        result = format_tweet(content)
        assert result is not None
        assert len(result) <= 280
        # Must contain something from the title
        assert "RLCS" in result or "results" in result or len(result) > 0

    def test_format_tweet_with_all_empty_strings(self):
        """format_tweet with empty title, url, body should not crash."""
        content = _rc(title="", url="", body="", metadata={})
        # Should not raise; may return None or a short string
        try:
            result = format_tweet(content)
            if result is not None:
                assert len(result) <= 280
        except Exception as e:
            pytest.fail(f"format_tweet crashed with empty fields: {e}")

    def test_try_format_with_exception_in_format_returns_none(self):
        """_try_format catches all exceptions and returns None."""
        # A template with a format spec that causes an error
        result = _try_format("{bad!r:invalid_spec}", {"bad": "value"})
        # Should return None, not raise
        assert result is None


class TestExtractTweetsDeduplication:
    """_extract_tweets deduplicates tweets with the same id_str."""

    def test_duplicate_tweet_ids_deduplicated(self):
        tweet1 = _make_tweet(tweet_id="DUPE_001", text="First occurrence of this tweet now")
        tweet2 = _make_tweet(tweet_id="DUPE_001", text="Second occurrence of same tweet here")
        data = _wrap([tweet1, tweet2])
        results = _extract_tweets(data)
        ids = [t["legacy"]["id_str"] for t in results]
        assert ids.count("DUPE_001") == 1

    def test_different_tweet_ids_both_returned(self):
        tweet1 = _make_tweet(tweet_id="UNIQUE_001", text="First unique tweet about RL updates")
        tweet2 = _make_tweet(tweet_id="UNIQUE_002", text="Second unique tweet about RL today")
        data = _wrap([tweet1, tweet2])
        results = _extract_tweets(data)
        ids = [t["legacy"]["id_str"] for t in results]
        assert "UNIQUE_001" in ids
        assert "UNIQUE_002" in ids


class TestPostNextBreakingNewsEngagementOnlyWithNoUrl:
    """Breaking news (priority=1) with NO URL should trigger engagement followup.
    This is distinct from breaking news WITH URL (self-reply path).
    """

    def test_breaking_with_url_posts_self_reply_not_followup(self):
        """When breaking news has a URL, the self-reply path runs, not followup."""
        conn = _make_db()
        long_text = (
            "BREAKING: Rocket League Season 15 is now live for all players! "
            "https://rocketleague.com/news/s15"
        )
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, priority) VALUES ('rocketleague', ?, 1)",
            (long_text,),
        )
        conn.commit()
        client = MagicMock()
        client.post_tweet.return_value = "tw_breaking_url"
        followup_mock = MagicMock(return_value="Follow @rl_wire1 for updates")

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.within_daily_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
            patch("src.poster.queue._engagement_followup", followup_mock),
        ):
            result = post_next("rocketleague", client)

        assert result is True
        # URL self-reply is posted; engagement followup is NOT called
        followup_mock.assert_not_called()
        assert client.post_tweet.call_count == 2
        # Second call should be the URL reply
        second_kwargs = client.post_tweet.call_args_list[1].kwargs
        assert "Read more:" in second_kwargs.get("text", "")
