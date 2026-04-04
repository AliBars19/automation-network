"""
E2E pipeline tests — end-to-end flows that exercise the full
collect → classify → format → queue → post path with in-memory SQLite.

Each test class maps to one critical pipeline path:

    1.  RSSPipeline          — RSS feed → queue with correct content_type/priority/niche
    2.  TwitterMonitorPipeline — twscrape GraphQL response → filtered → queued
    3.  GeodeIndexPipeline   — Geode REST API response → community_mod_update queued
    4.  DedupPipeline        — is_similar_story blocks near-duplicate tweets
    5.  QualityGatePipeline  — daily caps + engagement + stale-content gates
    6.  RateLimiterPipeline  — posting window, minimum interval, breaking-news bypass

All external I/O (feedparser, httpx, twscrape, tweepy) is mocked.
The database is always an in-memory SQLite instance — never touches the real DB file.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import formatdate
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import feedparser
import pytest

from src.collectors.base import BaseCollector, RawContent
from src.collectors.rss import RSSCollector, _detect_content_type, _is_on_topic
from src.collectors.apis.geode_index import GeodeIndexCollector
from src.collectors.twitter_monitor import TwitterMonitorCollector, _extract_tweets
from src.database.db import (
    add_to_queue,
    get_queued_tweets,
    insert_raw_content,
    is_similar_story,
    mark_posted,
    upsert_source,
)
from src.formatter.formatter import format_tweet
from src.poster.quality_gate import passes_quality_gate
from src.poster.queue import (
    _PRIORITY,
    collect_and_queue,
    post_next,
)
from src.poster.rate_limiter import within_posting_window

# ── Shared helpers ─────────────────────────────────────────────────────────────

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "database" / "schema.sql"


def _make_db() -> sqlite3.Connection:
    """Return an in-memory SQLite connection with the full schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


@contextmanager
def _ctx(conn: sqlite3.Connection):
    """Thin context manager that mimics get_db() for patching."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hours_ago_iso(hours: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _recent_rfc2822(hours_ago: float = 1.0) -> str:
    """Return a recent RFC 2822 date string (e.g. for tweet created_at fields).
    TwitterMonitorCollector rejects tweets older than 7 days, so tests must
    use recent timestamps."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return formatdate(dt.timestamp(), usegmt=True)


# ══════════════════════════════════════════════════════════════════════════════
# 1.  RSS → FORMAT → QUEUE
# ══════════════════════════════════════════════════════════════════════════════

class TestRSSPipeline:
    """
    Full pipeline: mock feedparser response → RSSCollector.collect() →
    collect_and_queue() → assert correct content_type, priority, niche in DB.
    """

    def _fake_entry(
        self,
        title: str,
        link: str = "https://example.com/article",
        entry_id: str = "entry-001",
        summary: str = "",
        tags: list[dict] | None = None,
    ) -> MagicMock:
        entry = MagicMock()
        entry.get = lambda key, default=None: {
            "id": entry_id,
            "link": link,
            "title": title,
            "summary": summary,
            "author": "RSS Author",
            "published": "Mon, 01 Jan 2026 12:00:00 +0000",
            "tags": tags or [],
            "media_content": [],
            "media_thumbnail": [],
            "enclosures": [],
            "content": [],
            "entities": {},
        }.get(key, default)
        return entry

    def _fake_feed(self, entries: list) -> MagicMock:
        feed = MagicMock()
        feed.bozo = False
        feed.entries = entries
        return feed

    @pytest.mark.asyncio
    async def test_rss_game_update_queued_with_correct_content_type(self):
        """A GD Steam news entry about a patch should be classified as game_update."""
        conn = _make_db()
        sid = upsert_source(conn, "geometrydash", "steam_gd", "rss", {
            "url": "https://store.steampowered.com/feeds/news/app/322170/",
        })
        conn.commit()

        entry = self._fake_entry(
            title="Geometry Dash 2.206 Update — New Platformer Levels",
            link="https://store.steampowered.com/news/app/322170/view/42",
            entry_id="steam-gd-patch-2206",
            summary="This patch updates the platformer mode and fixes several bugs.",
        )
        fake_feed = self._fake_feed([entry])

        collector = RSSCollector(
            source_id=sid,
            config={"url": "https://store.steampowered.com/feeds/news/app/322170/"},
            niche="geometrydash",
        )

        with patch("src.collectors.rss.feedparser.parse", return_value=fake_feed):
            items = await collector.collect()

        assert len(items) == 1
        item = items[0]
        assert item.niche == "geometrydash"
        assert item.content_type == "game_update"
        assert item.source_id == sid
        assert item.external_id == "steam-gd-patch-2206"

        # Now run it through collect_and_queue
        class _FixedCollector(BaseCollector):
            def __init__(self, _items):
                super().__init__(sid, {})
                self._items = _items
            async def collect(self):
                return self._items

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.is_similar_story", return_value=False),
        ):
            count = await collect_and_queue(_FixedCollector(items), "geometrydash")

        assert count == 1
        rows = get_queued_tweets(conn, "geometrydash")
        assert len(rows) == 1
        assert rows[0]["niche"] == "geometrydash"
        # game_update maps to priority 2
        assert rows[0]["priority"] == _PRIORITY["game_update"]

    @pytest.mark.asyncio
    async def test_rss_breaking_news_queued_at_priority_1(self):
        """An RL Steam news entry with no matching keyword defaults to patch_notes (p2)."""
        conn = _make_db()
        sid = upsert_source(conn, "rocketleague", "steam_rl", "rss", {
            "url": "https://store.steampowered.com/feeds/news/app/252950/",
        })
        conn.commit()

        entry = self._fake_entry(
            title="RLCS World Championship is coming soon",
            link="https://store.steampowered.com/news/app/252950/view/1",
            entry_id="steam-rl-rlcs-worlds",
            summary="The RLCS major championship is arriving this summer.",
        )
        fake_feed = self._fake_feed([entry])

        collector = RSSCollector(
            source_id=sid,
            config={"url": "https://store.steampowered.com/feeds/news/app/252950/"},
            niche="rocketleague",
        )

        with patch("src.collectors.rss.feedparser.parse", return_value=fake_feed):
            items = await collector.collect()

        assert len(items) == 1
        assert items[0].content_type == "event_announcement"

    @pytest.mark.asyncio
    async def test_rss_top1_verified_detected(self):
        """A GD article containing 'new top 1' maps to top1_verified (priority 1)."""
        conn = _make_db()
        sid = upsert_source(conn, "geometrydash", "esports_news", "rss", {
            "url": "https://esports-news.co.uk/feed/",
        })
        conn.commit()

        entry = self._fake_entry(
            title="New Top 1 demon verified in Geometry Dash: Abyss of Darkness",
            link="https://esports-news.co.uk/new-top-1-abyss",
            entry_id="esn-top1-abyss",
            summary="A new top 1 demon has been verified. The hardest level ever created.",
            tags=[{"term": "geometry dash"}],
        )
        fake_feed = self._fake_feed([entry])

        collector = RSSCollector(
            source_id=sid,
            config={"url": "https://esports-news.co.uk/feed/"},
            niche="geometrydash",
        )

        with patch("src.collectors.rss.feedparser.parse", return_value=fake_feed):
            items = await collector.collect()

        assert len(items) == 1
        assert items[0].content_type == "top1_verified"
        assert _PRIORITY["top1_verified"] == 1

    @pytest.mark.asyncio
    async def test_rss_off_topic_entry_filtered_by_multi_topic_domain(self):
        """An off-topic Dexerto entry (not about Rocket League) should be skipped."""
        conn = _make_db()
        sid = upsert_source(conn, "rocketleague", "dexerto_rl", "rss", {
            "url": "https://www.dexerto.com/feed/",
        })
        conn.commit()

        off_topic_entry = self._fake_entry(
            title="Best Netflix shows to watch this weekend",
            link="https://dexerto.com/entertainment/best-shows-netflix",
            entry_id="dex-off-topic-001",
            summary="Here are our picks for the best Netflix shows.",
            tags=[{"term": "entertainment"}],
        )
        fake_feed = self._fake_feed([off_topic_entry])

        collector = RSSCollector(
            source_id=sid,
            config={"url": "https://www.dexerto.com/feed/"},
            niche="rocketleague",
        )

        with patch("src.collectors.rss.feedparser.parse", return_value=fake_feed):
            items = await collector.collect()

        assert items == []

    @pytest.mark.asyncio
    async def test_rss_top1000_title_does_not_trigger_top1_verified(self):
        """Regression: 'Top 1000' in a Steam article must NOT match 'top1_verified'."""
        entry = self._fake_entry(
            title="GeometryDash.com is Live! Play Stereo Madness in the Top 1000 leaderboard",
            entry_id="steam-gd-website-launch",
            summary="The official GD website launched today with browser play and leaderboards.",
        )
        content_type = _detect_content_type(entry.get("title"), entry.get("summary"), "geometrydash")
        assert content_type != "top1_verified"

    @pytest.mark.asyncio
    async def test_rss_patch_notes_keyword_detected(self):
        """An RL article with 'patch note' should become patch_notes."""
        entry = self._fake_entry(
            title="Rocket League v2.31 Patch Notes — Bug Fixes",
            summary="This patch includes fixes for aerial hitboxes and server stability.",
        )
        content_type = _detect_content_type(entry.get("title"), entry.get("summary"), "rocketleague")
        assert content_type == "patch_notes"

    @pytest.mark.asyncio
    async def test_rss_bozo_feed_with_no_entries_returns_empty(self):
        """A malformed bozo feed with no entries should return an empty list."""
        conn = _make_db()
        sid = upsert_source(conn, "rocketleague", "bad_feed", "rss", {
            "url": "https://broken.example.com/feed",
        })
        conn.commit()

        bozo_feed = MagicMock()
        bozo_feed.bozo = True
        bozo_feed.entries = []

        collector = RSSCollector(
            source_id=sid,
            config={"url": "https://broken.example.com/feed"},
            niche="rocketleague",
        )

        with patch("src.collectors.rss.feedparser.parse", return_value=bozo_feed):
            items = await collector.collect()

        assert items == []

    @pytest.mark.asyncio
    async def test_rss_queue_entry_has_correct_niche_and_priority(self):
        """Queued RSS item should have niche='geometrydash' and priority from _PRIORITY map."""
        conn = _make_db()
        sid = upsert_source(conn, "geometrydash", "steam_gd_2", "rss", {
            "url": "https://store.steampowered.com/feeds/news/app/322170/",
        })
        conn.commit()

        item = RawContent(
            source_id=sid,
            external_id="e2e-rss-001",
            niche="geometrydash",
            content_type="mod_update",
            title="Geode 5.6.0 Update Released",
            url="https://geode-sdk.org/changelog",
            body="New mod loader version with improved stability.",
            metadata={},
        )

        class _FixedCollector(BaseCollector):
            def __init__(self):
                super().__init__(sid, {})
            async def collect(self):
                return [item]

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.is_similar_story", return_value=False),
        ):
            count = await collect_and_queue(_FixedCollector(), "geometrydash")

        assert count == 1
        rows = get_queued_tweets(conn, "geometrydash")
        assert rows[0]["niche"] == "geometrydash"
        assert rows[0]["priority"] == _PRIORITY["mod_update"]
        assert rows[0]["priority"] == 4


# ══════════════════════════════════════════════════════════════════════════════
# 2.  TWITTER MONITOR → FORMAT → QUEUE
# ══════════════════════════════════════════════════════════════════════════════

class TestTwitterMonitorPipeline:
    """
    Full pipeline: mock twscrape GraphQL response → TwitterMonitorCollector.collect()
    → format → queue. Verifies filters (language, substance, relevance, filler).
    """

    def _graphql_response(
        self,
        tweet_id: str,
        text: str,
        screen_name: str = "GDWTHub",
        lang: str = "en",
        created_at: str | None = None,
    ) -> dict:
        # Default to 1 hour ago so the 7-day filter never rejects test tweets.
        if created_at is None:
            created_at = _recent_rfc2822(hours_ago=1.0)
        """Build a minimal twscrape-style GraphQL response for a single tweet."""
        legacy = {
            "id_str": tweet_id,
            "full_text": text,
            "lang": lang,
            "created_at": created_at,
            "in_reply_to_user_id_str": None,
            "in_reply_to_status_id_str": None,
            "extended_entities": {},
            "entities": {"urls": [], "media": []},
        }
        tweet_result = {
            "legacy": legacy,
            "core": {
                "user_results": {
                    "result": {
                        "legacy": {"screen_name": screen_name}
                    }
                }
            },
        }
        return {
            "data": {
                "user": {
                    "result": {
                        "timeline_v2": {
                            "timeline": {
                                "instructions": [
                                    {
                                        "entries": [
                                            {
                                                "content": {
                                                    "itemContent": {
                                                        "tweet_results": {
                                                            "result": tweet_result
                                                        }
                                                    }
                                                }
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        }

    @pytest.mark.asyncio
    async def test_gd_tweet_passes_all_filters_and_is_queued(self):
        """
        A well-formed GD tournament tweet should pass every filter and land in queue
        as content_type='monitored_tweet'.
        """
        conn = _make_db()
        sid = upsert_source(conn, "geometrydash", "@GDWTHub", "twitter", {
            "account_id": "GDWTHub",
        })
        conn.commit()

        tweet_text = (
            "Geometry Dash World Tournament Season 3 bracket is now live! "
            "Check the bracket at https://gdwt.example.com/bracket #GeometryDash"
        )
        graphql_data = self._graphql_response(
            tweet_id="9990000000001",
            text=tweet_text,
            screen_name="GDWTHub",
        )

        mock_client = AsyncMock()
        mock_client.gql_get = AsyncMock(return_value=graphql_data)

        collector = TwitterMonitorCollector(
            source_id=sid,
            config={"account_id": "GDWTHub"},
            niche="geometrydash",
        )

        with (
            patch("src.collectors.twitter_monitor.get_api", return_value=mock_client),
            patch("src.collectors.twitter_monitor.resolve_user_id", return_value=99901),
        ):
            items = await collector.collect()

        assert len(items) == 1
        item = items[0]
        assert item.niche == "geometrydash"
        assert item.content_type == "monitored_tweet"
        assert item.external_id == "9990000000001"
        assert item.author == "GDWTHub"
        # URL should be expanded in the body
        assert "gdwt.example.com" in item.body or tweet_text[:50] in item.body

    @pytest.mark.asyncio
    async def test_french_tweet_is_filtered_out(self):
        """A French-language tweet (fr lang + French words) must be blocked."""
        conn = _make_db()
        sid = upsert_source(conn, "rocketleague", "@RLEsports", "twitter", {
            "account_id": "RLEsports",
        })
        conn.commit()

        french_text = (
            "C'est incroyable! Les équipes sont prêtes pour la grande finale. "
            "Allez les champions! C'est magnifique pour notre équipe ce soir."
        )
        graphql_data = self._graphql_response(
            tweet_id="9990000000002",
            text=french_text,
            screen_name="RLEsports",
            lang="fr",
        )

        mock_client = AsyncMock()
        mock_client.gql_get = AsyncMock(return_value=graphql_data)

        collector = TwitterMonitorCollector(
            source_id=sid,
            config={"account_id": "RLEsports"},
            niche="rocketleague",
        )

        with (
            patch("src.collectors.twitter_monitor.get_api", return_value=mock_client),
            patch("src.collectors.twitter_monitor.resolve_user_id", return_value=99902),
        ):
            items = await collector.collect()

        assert items == []

    @pytest.mark.asyncio
    async def test_filler_tweet_is_blocked(self):
        """An emoji-only / filler tweet like 'Hmm...' must be blocked."""
        conn = _make_db()
        sid = upsert_source(conn, "rocketleague", "@SomePro", "twitter", {
            "account_id": "SomePro",
        })
        conn.commit()

        graphql_data = self._graphql_response(
            tweet_id="9990000000003",
            text="Hmmmmm...",
            screen_name="SomePro",
        )

        mock_client = AsyncMock()
        mock_client.gql_get = AsyncMock(return_value=graphql_data)

        collector = TwitterMonitorCollector(
            source_id=sid,
            config={"account_id": "SomePro"},
            niche="rocketleague",
        )

        with (
            patch("src.collectors.twitter_monitor.get_api", return_value=mock_client),
            patch("src.collectors.twitter_monitor.resolve_user_id", return_value=99903),
        ):
            items = await collector.collect()

        assert items == []

    @pytest.mark.asyncio
    async def test_tweet_by_wrong_author_is_rejected(self):
        """
        A tweet embedded in the timeline of @GDWTHub but authored by a different
        account must be discarded (embedded-tweet leak prevention).
        """
        conn = _make_db()
        sid = upsert_source(conn, "geometrydash", "@GDWTHub", "twitter", {
            "account_id": "GDWTHub",
        })
        conn.commit()

        # screen_name is different from the monitored account_id
        graphql_data = self._graphql_response(
            tweet_id="9990000000004",
            text="Geometry Dash tournament results are in! Amazing matches today! #GeometryDash",
            screen_name="OtherAccount",  # not GDWTHub
        )

        mock_client = AsyncMock()
        mock_client.gql_get = AsyncMock(return_value=graphql_data)

        collector = TwitterMonitorCollector(
            source_id=sid,
            config={"account_id": "GDWTHub"},
            niche="geometrydash",
        )

        with (
            patch("src.collectors.twitter_monitor.get_api", return_value=mock_client),
            patch("src.collectors.twitter_monitor.resolve_user_id", return_value=99904),
        ):
            items = await collector.collect()

        assert items == []

    @pytest.mark.asyncio
    async def test_require_relevance_blocks_off_topic_player_tweet(self):
        """
        A player account with require_relevance=True posting about Hatsune Miku
        must be filtered out as off-topic.
        """
        conn = _make_db()
        sid = upsert_source(conn, "geometrydash", "@gdzoink", "twitter", {
            "account_id": "gdzoink",
            "require_relevance": True,
        })
        conn.commit()

        graphql_data = self._graphql_response(
            tweet_id="9990000000005",
            text="Just finished listening to the new Hatsune Miku album, incredible production",
            screen_name="gdzoink",
        )

        mock_client = AsyncMock()
        mock_client.gql_get = AsyncMock(return_value=graphql_data)

        collector = TwitterMonitorCollector(
            source_id=sid,
            config={"account_id": "gdzoink", "require_relevance": True},
            niche="geometrydash",
        )

        with (
            patch("src.collectors.twitter_monitor.get_api", return_value=mock_client),
            patch("src.collectors.twitter_monitor.resolve_user_id", return_value=99905),
        ):
            items = await collector.collect()

        assert items == []

    @pytest.mark.asyncio
    async def test_require_relevance_allows_gd_completion_tweet(self):
        """
        A player account with require_relevance=True posting a GD completion
        (e.g. '100% verified') must pass the filter.
        """
        conn = _make_db()
        sid = upsert_source(conn, "geometrydash", "@gdzoink", "twitter", {
            "account_id": "gdzoink",
            "require_relevance": True,
        })
        conn.commit()

        graphql_data = self._graphql_response(
            tweet_id="9990000000006",
            text="FINALLY 100% Abyss of Darkness after 45000 attempts. New hardest demon!",
            screen_name="gdzoink",
        )

        mock_client = AsyncMock()
        mock_client.gql_get = AsyncMock(return_value=graphql_data)

        collector = TwitterMonitorCollector(
            source_id=sid,
            config={"account_id": "gdzoink", "require_relevance": True},
            niche="geometrydash",
        )

        with (
            patch("src.collectors.twitter_monitor.get_api", return_value=mock_client),
            patch("src.collectors.twitter_monitor.resolve_user_id", return_value=99905),
        ):
            items = await collector.collect()

        assert len(items) == 1
        assert items[0].content_type == "monitored_tweet"

    @pytest.mark.asyncio
    async def test_monitored_tweet_formatted_and_queued(self):
        """
        A valid monitored tweet should flow through collect_and_queue and land in
        tweet_queue with the correct niche, priority, and non-empty tweet_text.
        """
        conn = _make_db()
        sid = upsert_source(conn, "geometrydash", "@GDWTHub", "twitter", {
            "account_id": "GDWTHub",
        })
        conn.commit()

        item = RawContent(
            source_id=sid,
            external_id="tw-e2e-001",
            niche="geometrydash",
            content_type="monitored_tweet",
            title="GD World Tournament bracket announced",
            url="https://x.com/GDWTHub/status/9990000000007",
            body="GD World Tournament Season 3 bracket announced! #GeometryDash",
            author="GDWTHub",
            metadata={"account": "GDWTHub", "tweet_url": "https://x.com/GDWTHub/status/9990000000007"},
        )

        class _FixedCollector(BaseCollector):
            def __init__(self):
                super().__init__(sid, {})
            async def collect(self):
                return [item]

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.is_similar_story", return_value=False),
        ):
            count = await collect_and_queue(_FixedCollector(), "geometrydash")

        assert count == 1
        rows = get_queued_tweets(conn, "geometrydash")
        assert len(rows) == 1
        assert rows[0]["niche"] == "geometrydash"
        assert rows[0]["priority"] == _PRIORITY["monitored_tweet"]
        assert len(rows[0]["tweet_text"]) > 10

    def test_extract_tweets_skips_embedded_retweet(self):
        """_extract_tweets must not surface tweets nested inside retweeted_status_result."""
        data = {
            "data": {
                "result": {
                    "legacy": {"id_str": "outer-tweet", "full_text": "Outer tweet"},
                    "retweeted_status_result": {
                        "result": {
                            "legacy": {
                                "id_str": "embedded-tweet",
                                "full_text": "I am embedded and must be excluded",
                            }
                        }
                    },
                }
            }
        }
        tweets = _extract_tweets(data)
        tweet_ids = [t["legacy"]["id_str"] for t in tweets]
        assert "outer-tweet" in tweet_ids
        assert "embedded-tweet" not in tweet_ids


# ══════════════════════════════════════════════════════════════════════════════
# 3.  GEODE INDEX API → FORMAT → QUEUE
# ══════════════════════════════════════════════════════════════════════════════

class TestGeodeIndexPipeline:
    """
    Full pipeline: mock httpx response from Geode Index REST API →
    GeodeIndexCollector.collect() → format → queue.
    Verifies content_type='community_mod_update', meme-name filter, and metadata fields.
    """

    def _api_payload(self, mods: list[dict]) -> dict:
        return {"payload": {"data": mods}}

    def _mod_entry(
        self,
        mod_id: str = "click-between-frames",
        name: str = "Click Between Frames",
        version: str = "1.5.0",
        download_count: int = 80_000,
        featured: bool = False,
        description: str = "Record inputs between frames for TAS-style gameplay.",
        source_url: str = "https://github.com/user/cbf",
    ) -> dict:
        return {
            "id": mod_id,
            "featured": featured,
            "download_count": download_count,
            "versions": [
                {
                    "name": name,
                    "version": version,
                    "description": description,
                    "download_link": f"https://api.geode-sdk.org/v1/mods/{mod_id}/versions/{version}/download",
                }
            ],
            "links": {"source": source_url},
            "developers": [{"display_name": "AlRado"}],
        }

    @pytest.mark.asyncio
    async def test_popular_mod_collected_as_community_mod_update(self):
        """A mod with ≥25k downloads should be surfaced as community_mod_update."""
        mod = self._mod_entry(
            mod_id="click-between-frames",
            name="Click Between Frames",
            version="1.5.0",
            download_count=80_000,
        )
        payload = self._api_payload([mod])

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=payload)

        collector = GeodeIndexCollector(
            source_id=1,
            config={"min_downloads": 25_000, "max_items": 3},
            niche="geometrydash",
        )

        with patch("src.collectors.apis.geode_index.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            items = await collector.collect()

        assert len(items) == 1
        item = items[0]
        assert item.content_type == "community_mod_update"
        assert item.niche == "geometrydash"
        assert item.external_id == "geode_mod_click-between-frames_1.5.0"
        assert "Click Between Frames" in item.title
        assert "1.5.0" in item.title

    @pytest.mark.asyncio
    async def test_low_download_unfeatured_mod_skipped(self):
        """A mod with <25k downloads and featured=False must be skipped."""
        mod = self._mod_entry(
            mod_id="tiny-mod",
            name="Tiny Mod",
            version="0.1.0",
            download_count=100,
            featured=False,
        )
        payload = self._api_payload([mod])

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=payload)

        collector = GeodeIndexCollector(
            source_id=1,
            config={"min_downloads": 25_000, "max_items": 3},
            niche="geometrydash",
        )

        with patch("src.collectors.apis.geode_index.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            items = await collector.collect()

        assert items == []

    @pytest.mark.asyncio
    async def test_featured_mod_collected_regardless_of_downloads(self):
        """A featured mod with only 500 downloads should still be collected."""
        mod = self._mod_entry(
            mod_id="featured-new-mod",
            name="Featured Awesome Mod",
            version="0.3.0",
            download_count=500,
            featured=True,
        )
        payload = self._api_payload([mod])

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=payload)

        collector = GeodeIndexCollector(
            source_id=1,
            config={"min_downloads": 25_000, "max_items": 3},
            niche="geometrydash",
        )

        with patch("src.collectors.apis.geode_index.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            items = await collector.collect()

        assert len(items) == 1
        assert items[0].content_type == "community_mod_update"

    @pytest.mark.asyncio
    async def test_meme_name_mod_is_skipped(self):
        """
        A mod whose name contains a meme signal (e.g. 'game of the year') must
        be skipped even if it has millions of downloads.
        """
        mod = self._mod_entry(
            mod_id="click-sounds-meme",
            name="Click Sounds Mega Neo Full Ultra S26 Deluxe Game of the Year Edition",
            version="9.99.0",
            download_count=5_000_000,
        )
        payload = self._api_payload([mod])

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=payload)

        collector = GeodeIndexCollector(
            source_id=1,
            config={"min_downloads": 25_000, "max_items": 3},
            niche="geometrydash",
        )

        with patch("src.collectors.apis.geode_index.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            items = await collector.collect()

        assert items == []

    @pytest.mark.asyncio
    async def test_meme_name_over_length_limit_is_skipped(self):
        """A mod name longer than 55 chars should be skipped by the length check."""
        long_name = "A" * 60  # 60 chars > 55 limit
        mod = self._mod_entry(
            mod_id="long-name-mod",
            name=long_name,
            version="1.0.0",
            download_count=500_000,
        )
        payload = self._api_payload([mod])

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=payload)

        collector = GeodeIndexCollector(
            source_id=1,
            config={"min_downloads": 25_000, "max_items": 3},
            niche="geometrydash",
        )

        with patch("src.collectors.apis.geode_index.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            items = await collector.collect()

        assert items == []

    @pytest.mark.asyncio
    async def test_mod_metadata_fields_populated_correctly(self):
        """Collected mod item should have mod_name, version, description in metadata."""
        mod = self._mod_entry(
            mod_id="better-progress-bar",
            name="Better Progress Bar",
            version="2.1.0",
            download_count=50_000,
            description="Replaces the default progress bar with a better one.",
        )
        payload = self._api_payload([mod])

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=payload)

        collector = GeodeIndexCollector(
            source_id=1,
            config={"min_downloads": 25_000, "max_items": 3},
            niche="geometrydash",
        )

        with patch("src.collectors.apis.geode_index.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            items = await collector.collect()

        assert len(items) == 1
        meta = items[0].metadata
        assert meta["mod_name"] == "Better Progress Bar"
        assert meta["version"] == "2.1.0"
        # description comes from the API's description field, not the mod name
        assert "progress bar" in meta["description"].lower()

    @pytest.mark.asyncio
    async def test_formatted_mod_tweet_contains_mod_name_and_version(self):
        """The formatted tweet for a community_mod_update must include name and version."""
        item = RawContent(
            source_id=1,
            external_id="geode_mod_better-progress-bar_2.1.0",
            niche="geometrydash",
            content_type="community_mod_update",
            title="Better Progress Bar 2.1.0 (Geode mod)",
            url="https://github.com/user/bpb",
            body="Replaces the default progress bar with a better one.",
            metadata={
                "mod_name": "Better Progress Bar",
                "version": "2.1.0",
                "description": "Replaces the default progress bar.",
                "summary": "Better Progress Bar — updated to 2.1.0.",
            },
        )
        tweet = format_tweet(item)
        assert tweet is not None
        assert "Better Progress Bar" in tweet or "2.1.0" in tweet

    @pytest.mark.asyncio
    async def test_geode_mod_queued_at_priority_4(self):
        """community_mod_update should land in queue at priority 4."""
        conn = _make_db()
        sid = upsert_source(conn, "geometrydash", "geode_index", "api", {
            "min_downloads": 25000,
        })
        conn.commit()

        item = RawContent(
            source_id=sid,
            external_id="geode_mod_cbf_1.5.0",
            niche="geometrydash",
            content_type="community_mod_update",
            title="Click Between Frames 1.5.0 (Geode mod)",
            url="https://github.com/user/cbf",
            body="Record inputs between frames.",
            score=80_000,
            metadata={
                "mod_name": "Click Between Frames",
                "version": "1.5.0",
                "description": "Record inputs between frames.",
                "summary": "Click Between Frames — updated to 1.5.0.",
            },
        )

        class _FixedCollector(BaseCollector):
            def __init__(self):
                super().__init__(sid, {})
            async def collect(self):
                return [item]

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.is_similar_story", return_value=False),
        ):
            count = await collect_and_queue(_FixedCollector(), "geometrydash")

        assert count == 1
        rows = get_queued_tweets(conn, "geometrydash")
        assert rows[0]["priority"] == _PRIORITY["community_mod_update"]
        assert rows[0]["priority"] == 4


# ══════════════════════════════════════════════════════════════════════════════
# 4.  DEDUP PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

class TestDedupPipeline:
    """
    Verifies is_similar_story() blocks near-duplicate tweets using
    difflib.SequenceMatcher with a 0.45 threshold over a 48-hour window.
    """

    def test_identical_tweet_is_flagged_as_similar(self):
        """An exact duplicate must be caught by the similarity check."""
        conn = _make_db()
        text = "Rocket League Season 15 is now live with new Arena and Ranked changes"
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) "
            "VALUES ('rocketleague', ?, 'queued', ?)",
            (text, _now_iso()),
        )
        conn.commit()

        assert is_similar_story(conn, text, "rocketleague") is True

    def test_near_duplicate_above_threshold_blocked(self):
        """
        A tweet that differs by a few words (ratio > 0.45) should be blocked.
        """
        conn = _make_db()
        original = (
            "Rocket League Season 15 drops today — new Arena, Ranked reset, "
            "and Seasonal Challenges are live."
        )
        near_dup = (
            "Rocket League Season 15 is now live — new Arena, Ranked reset, "
            "and Seasonal Challenges available."
        )
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) "
            "VALUES ('rocketleague', ?, 'queued', ?)",
            (original, _now_iso()),
        )
        conn.commit()

        assert is_similar_story(conn, near_dup, "rocketleague") is True

    def test_completely_different_tweet_is_not_blocked(self):
        """A tweet about a completely different topic must not be considered similar."""
        conn = _make_db()
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) "
            "VALUES ('rocketleague', ?, 'queued', ?)",
            ("Rocket League Season 15 has arrived with new maps", _now_iso()),
        )
        conn.commit()

        different = (
            "Geometry Dash 2.3 has been officially announced by RobTop. "
            "New levels, platformer updates, and a ton of new features coming soon."
        )
        assert is_similar_story(conn, different, "rocketleague") is False

    def test_old_tweet_outside_48h_window_does_not_block(self):
        """
        A similar tweet that was queued more than 48 hours ago must NOT block
        a new one — the dedup window is 48 hours.
        """
        conn = _make_db()
        old_time = _hours_ago_iso(49)
        original = "Rocket League Season 15 is now live with new Arena and Ranked changes"
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) "
            "VALUES ('rocketleague', ?, 'queued', ?)",
            (original, old_time),
        )
        conn.commit()

        near_dup = "Rocket League Season 15 is now live with new Arena and Ranked changes today"
        assert is_similar_story(conn, near_dup, "rocketleague") is False

    def test_different_niche_does_not_block(self):
        """
        A similar tweet queued under a different niche must NOT block a new
        tweet for a different niche (the window is per-niche).
        """
        conn = _make_db()
        text = "Major update has arrived with tons of new features and improvements"
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) "
            "VALUES ('rocketleague', ?, 'queued', ?)",
            (text, _now_iso()),
        )
        conn.commit()

        # Same text but querying for geometrydash niche
        assert is_similar_story(conn, text, "geometrydash") is False

    @pytest.mark.asyncio
    async def test_collect_and_queue_blocks_similar_via_pipeline(self):
        """
        Two separate collectors returning very similar tweet text:
        the second one should be blocked by is_similar_story in collect_and_queue.
        """
        conn = _make_db()
        sid1 = upsert_source(conn, "geometrydash", "src-a", "rss", {})
        sid2 = upsert_source(conn, "geometrydash", "src-b", "rss", {})
        conn.commit()

        original_text = (
            "Geometry Dash 2.3 announced by RobTop — new levels, "
            "new mechanics, and a release date confirmed."
        )
        similar_text = (
            "Geometry Dash 2.3 revealed by RobTop with new levels, "
            "new mechanics, and an official release date."
        )

        item_a = RawContent(
            source_id=sid1, external_id="dedup-a-001", niche="geometrydash",
            content_type="game_update",
            title=original_text[:80],
            url="https://source-a.com/gd23",
            body="Full announcement details here.",
        )
        item_b = RawContent(
            source_id=sid2, external_id="dedup-b-001", niche="geometrydash",
            content_type="game_update",
            title=similar_text[:80],
            url="https://source-b.com/gd23",
            body="Full announcement details here.",
        )

        class CollectorA(BaseCollector):
            def __init__(self):
                super().__init__(sid1, {})
            async def collect(self):
                return [item_a]

        class CollectorB(BaseCollector):
            def __init__(self):
                super().__init__(sid2, {})
            async def collect(self):
                return [item_b]

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
        ):
            count_a = await collect_and_queue(CollectorA(), "geometrydash")

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
        ):
            count_b = await collect_and_queue(CollectorB(), "geometrydash")

        assert count_a == 1
        assert count_b == 0  # blocked by is_similar_story


# ══════════════════════════════════════════════════════════════════════════════
# 5.  QUALITY GATE PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

class TestQualityGatePipeline:
    """
    Verifies the quality gate enforces:
      - Daily caps per content_type
      - Community engagement thresholds
      - Stale content rejection (>12 hours)
    """

    def _seed_post_log(
        self,
        conn: sqlite3.Connection,
        niche: str,
        content_type: str,
        count: int,
    ) -> None:
        """
        Seed tweet_queue + raw_content rows to simulate `count` posts of
        a given content_type today (used to trip the daily cap check).
        """
        sid = upsert_source(conn, niche, f"_seed_src_{content_type}", "rss", {})
        conn.commit()
        for i in range(count):
            rc_cur = conn.execute(
                """INSERT INTO raw_content
                   (source_id, external_id, niche, content_type, title)
                   VALUES (?, ?, ?, ?, ?)""",
                (sid, f"seed-{content_type}-{i}", niche, content_type, f"Seed {i}"),
            )
            rc_id = rc_cur.lastrowid
            conn.execute(
                """INSERT INTO tweet_queue
                   (niche, raw_content_id, tweet_text, priority, status, created_at)
                   VALUES (?, ?, ?, 6, 'queued', ?)""",
                (niche, rc_id, f"Seed tweet {i}", _now_iso()),
            )
        conn.commit()

    def test_daily_cap_blocks_seventh_monitored_tweet(self):
        """
        After 6 monitored_tweets are queued today, a 7th must be rejected by
        passes_quality_gate. Daily cap for monitored_tweet is 6.
        """
        conn = _make_db()
        self._seed_post_log(conn, "rocketleague", "monitored_tweet", 6)

        with patch("src.poster.quality_gate.get_db", return_value=_ctx(conn)):
            result = passes_quality_gate(
                content_type="monitored_tweet",
                niche="rocketleague",
                score=0,
            )
        assert result is False

    def test_daily_cap_allows_sixth_monitored_tweet(self):
        """5 queued so far → the 6th must still pass."""
        conn = _make_db()
        self._seed_post_log(conn, "rocketleague", "monitored_tweet", 5)

        with patch("src.poster.quality_gate.get_db", return_value=_ctx(conn)):
            result = passes_quality_gate(
                content_type="monitored_tweet",
                niche="rocketleague",
                score=0,
            )
        assert result is True

    def test_community_clip_below_engagement_threshold_rejected(self):
        """
        A community_clip with score=10 from a small account (<10k followers)
        must fail the engagement threshold (minimum=25).
        """
        conn = _make_db()
        with patch("src.poster.quality_gate.get_db", return_value=_ctx(conn)):
            result = passes_quality_gate(
                content_type="community_clip",
                niche="rocketleague",
                score=10,
                source_followers=5_000,
            )
        assert result is False

    def test_community_clip_above_engagement_threshold_passes(self):
        """A community_clip with score=30 from a small account must pass."""
        conn = _make_db()
        with patch("src.poster.quality_gate.get_db", return_value=_ctx(conn)):
            result = passes_quality_gate(
                content_type="community_clip",
                niche="rocketleague",
                score=30,
                source_followers=5_000,
            )
        assert result is True

    def test_stale_content_older_than_12h_rejected(self):
        """A community_clip that is 13 hours old must be rejected by the age gate."""
        conn = _make_db()
        with patch("src.poster.quality_gate.get_db", return_value=_ctx(conn)):
            result = passes_quality_gate(
                content_type="community_clip",
                niche="rocketleague",
                score=1000,
                age_hours=13.0,
                source_followers=5_000,
            )
        assert result is False

    def test_stale_content_exactly_at_12h_passes(self):
        """Content at exactly 12 hours old is NOT stale (strict >12h comparison)."""
        conn = _make_db()
        with patch("src.poster.quality_gate.get_db", return_value=_ctx(conn)):
            result = passes_quality_gate(
                content_type="community_clip",
                niche="rocketleague",
                score=1000,
                age_hours=12.0,
                source_followers=5_000,
            )
        assert result is True

    def test_official_content_bypasses_engagement_gate(self):
        """
        patch_notes / game_update (official content) should always pass,
        regardless of score or age.
        """
        conn = _make_db()
        with patch("src.poster.quality_gate.get_db", return_value=_ctx(conn)):
            assert passes_quality_gate("patch_notes", "rocketleague", score=0) is True
            assert passes_quality_gate("game_update", "geometrydash", score=0, age_hours=100) is True
            assert passes_quality_gate("top1_verified", "geometrydash", score=0) is True

    def test_youtube_video_daily_cap_enforced(self):
        """After 4 youtube_videos queued today, a 5th must be rejected (cap=4)."""
        conn = _make_db()
        self._seed_post_log(conn, "geometrydash", "youtube_video", 4)

        with patch("src.poster.quality_gate.get_db", return_value=_ctx(conn)):
            result = passes_quality_gate(
                content_type="youtube_video",
                niche="geometrydash",
                score=0,
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_quality_gate_blocks_over_cap_in_collect_and_queue(self):
        """
        collect_and_queue must respect the daily cap gate: if cap is reached,
        subsequent items of that content_type are dropped before queuing.
        """
        conn = _make_db()
        sid = upsert_source(conn, "geometrydash", "comm-clips-src", "rss", {})
        conn.commit()
        self._seed_post_log(conn, "geometrydash", "community_mod_update", 4)

        # 5th community_mod_update — should be rejected (cap=4)
        item = RawContent(
            source_id=sid,
            external_id="cap-test-005",
            niche="geometrydash",
            content_type="community_mod_update",
            title="Some Mod 1.0.0 (Geode mod)",
            url="https://example.com/mod",
            body="Some mod description.",
            score=30_000,
            metadata={
                "mod_name": "Some Mod",
                "version": "1.0.0",
                "description": "A mod.",
                "summary": "Some Mod — updated.",
            },
        )

        class _FixedCollector(BaseCollector):
            def __init__(self):
                super().__init__(sid, {})
            async def collect(self):
                return [item]

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.quality_gate.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.prepare_media", return_value=None),
            patch("src.poster.queue.url_already_queued", return_value=False),
            patch("src.poster.queue.is_similar_story", return_value=False),
        ):
            count = await collect_and_queue(_FixedCollector(), "geometrydash")

        assert count == 0  # cap=4, 5th item must be rejected


# ══════════════════════════════════════════════════════════════════════════════
# 6.  RATE LIMITER PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

class TestRateLimiterPipeline:
    """
    Verifies posting window enforcement, minimum interval between posts,
    and breaking-news bypass of both constraints.
    """

    def _setup_queue(
        self,
        conn: sqlite3.Connection,
        text: str,
        niche: str = "geometrydash",
        priority: int = 5,
    ) -> int:
        cur = conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, priority, status) "
            "VALUES (?, ?, ?, 'queued')",
            (niche, text, priority),
        )
        conn.commit()
        return cur.lastrowid

    def _seed_recent_post(
        self,
        conn: sqlite3.Connection,
        niche: str,
        minutes_ago: int = 5,
    ) -> None:
        """Insert a recent post_log row to trip the minimum-interval check."""
        posted_at = (
            datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO post_log (niche, tweet_id, tweet_text, posted_at) "
            "VALUES (?, 'prev_tweet', 'Previous tweet content', ?)",
            (niche, posted_at),
        )
        conn.commit()

    # ── Posting window tests ──────────────────────────────────────────────────

    def test_within_window_at_18_utc(self):
        """18:00 UTC is inside the 14:00–04:00 window."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 4, 18, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() is True

    def test_within_window_at_02_utc_wraps_midnight(self):
        """02:00 UTC (after midnight) is inside the 14:00–04:00 window."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 5, 2, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() is True

    def test_outside_window_at_08_utc(self):
        """08:00 UTC is outside the 14:00–04:00 window."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 4, 8, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() is False

    def test_outside_window_at_10_utc(self):
        """10:00 UTC is outside the 14:00–04:00 window."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 4, 10, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() is False

    def test_boundary_exactly_at_14_utc_is_inside(self):
        """14:00 UTC is the start of the window — must be included."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 4, 14, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() is True

    def test_boundary_exactly_at_04_utc_is_outside(self):
        """04:00 UTC is the exclusive end of the window — must be excluded."""
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 5, 4, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert within_posting_window() is False

    # ── Breaking news bypasses window ─────────────────────────────────────────

    def test_breaking_news_bypasses_posting_window(self):
        """
        A priority-1 (breaking news) tweet in post_next must be posted even
        outside the posting window (08:00 UTC).
        """
        conn = _make_db()
        self._setup_queue(
            conn,
            "BREAKING: Rocket League Season 15 just announced with brand new map!",
            niche="rocketleague",
            priority=1,
        )
        client = MagicMock()
        client.post_tweet.return_value = "tweet_breaking_001"

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.within_daily_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
            # NOT patching within_posting_window or can_post:
            # breaking news (p1) skips both checks entirely in post_next
        ):
            result = post_next("rocketleague", client)

        assert result is True
        client.post_tweet.assert_called()

    # ── Minimum interval enforcement ─────────────────────────────────────────

    def test_post_blocked_when_too_soon_after_last_post(self):
        """
        A normal-priority tweet posted only 5 minutes after the previous one
        must be blocked by can_post (min interval = 1200s = 20 min).
        """
        conn = _make_db()
        self._setup_queue(
            conn,
            "Rocket League new update is live with many improvements today!",
            niche="rocketleague",
            priority=5,
        )
        self._seed_recent_post(conn, "rocketleague", minutes_ago=5)
        client = MagicMock()

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.within_daily_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
            patch("src.poster.rate_limiter.get_db", side_effect=lambda: _ctx(conn)),
        ):
            result = post_next("rocketleague", client)

        assert result is False
        client.post_tweet.assert_not_called()

    def test_post_allowed_after_sufficient_interval(self):
        """
        A normal-priority tweet posted 25 minutes after the previous one must
        be allowed through (25 min > 20 min min interval for geometrydash).

        Uses geometrydash niche: its YAML min_interval_seconds=1200 (20 min).
        The rocketleague YAML has 1800s (shadowban recovery config), which would
        make this test fail for that niche.
        """
        from src.poster.rate_limiter import _posting_config
        _posting_config.cache_clear()  # ensure stale lru_cache doesn't affect result

        conn = _make_db()
        self._setup_queue(
            conn,
            "Geometry Dash community: major demon list update this week with new entries!",
            niche="geometrydash",
            priority=5,
        )
        self._seed_recent_post(conn, "geometrydash", minutes_ago=25)
        client = MagicMock()
        client.post_tweet.return_value = "tweet_interval_ok"

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.within_daily_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
            patch("src.poster.rate_limiter.get_db", side_effect=lambda: _ctx(conn)),
        ):
            result = post_next("geometrydash", client)

        assert result is True
        client.post_tweet.assert_called_once()

    def test_outside_window_blocks_normal_tweet_in_post_next(self):
        """
        post_next must return False for a normal-priority tweet when we are
        outside the posting window.
        """
        conn = _make_db()
        self._setup_queue(
            conn,
            "Geometry Dash community update: new demon list entries this week!",
            niche="geometrydash",
            priority=5,
        )
        client = MagicMock()

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.within_daily_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=False),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("geometrydash", client)

        assert result is False
        client.post_tweet.assert_not_called()

    def test_30min_safety_cap_blocks_post(self):
        """
        If 3 posts have been sent in the last 30 minutes (hard safety cap),
        post_next must return False.
        """
        conn = _make_db()
        self._setup_queue(
            conn,
            "Geometry Dash community update: major demon list movement this week!",
            niche="geometrydash",
            priority=5,
        )
        client = MagicMock()

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.within_daily_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=3),  # at cap
        ):
            result = post_next("geometrydash", client)

        assert result is False
        client.post_tweet.assert_not_called()

    def test_empty_queue_returns_false(self):
        """post_next with an empty queue must return False without posting."""
        conn = _make_db()
        client = MagicMock()

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.within_daily_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("geometrydash", client)

        assert result is False
        client.post_tweet.assert_not_called()
