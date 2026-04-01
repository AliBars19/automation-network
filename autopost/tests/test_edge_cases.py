"""
Edge case tests for maximum coverage — targets uncovered lines and
boundary conditions across all modules.
"""
import json
import sqlite3
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.collectors.base import RawContent
from src.database.db import (
    add_to_queue, get_queued_tweets, insert_raw_content,
    upsert_source, mark_posted, mark_failed, mark_skipped,
    cleanup_old_records, is_similar_story, url_already_queued,
    record_source_error, recent_source_error_count,
)
from src.formatter.formatter import format_tweet, _build_context, _cap
from src.poster.queue import _split_url

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "database" / "schema.sql"


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def _rc(**kw):
    d = {"source_id": 1, "external_id": "e1", "niche": "rocketleague",
         "content_type": "breaking_news", "title": "T", "url": "", "body": "",
         "image_url": "", "author": "", "score": 0, "metadata": {}}
    d.update(kw)
    return RawContent(**d)


# ═══════════════════════════════════════════════════════════════════════════════
# MEDIA — resize edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestMediaResize:
    def test_prepare_media_empty_url_returns_none(self):
        from src.formatter.media import prepare_media
        assert prepare_media("") is None

    def test_prepare_media_none_returns_none(self):
        from src.formatter.media import prepare_media
        assert prepare_media(None) is None

    def test_dest_path_deterministic(self):
        from src.formatter.media import _dest_path
        p1 = _dest_path("https://example.com/img.jpg")
        p2 = _dest_path("https://example.com/img.jpg")
        assert p1 == p2

    def test_dest_path_different_urls(self):
        from src.formatter.media import _dest_path
        p1 = _dest_path("https://a.com/1.jpg")
        p2 = _dest_path("https://b.com/2.jpg")
        assert p1 != p2

    def test_resize_too_small_returns_none(self):
        from src.formatter.media import _resize
        # 1x1 pixel image
        from PIL import Image
        img = Image.new("RGB", (10, 10), "red")
        buf = BytesIO()
        img.save(buf, format="JPEG")
        result = _resize(buf.getvalue())
        assert result is None  # too small (10x10 < 400x300)

    def test_resize_valid_image(self):
        from src.formatter.media import _resize
        from PIL import Image
        img = Image.new("RGB", (1920, 1080), "blue")
        buf = BytesIO()
        img.save(buf, format="JPEG")
        result = _resize(buf.getvalue())
        assert result is not None
        assert len(result) > 0

    def test_resize_medium_image_no_upscale(self):
        from src.formatter.media import _resize
        from PIL import Image
        img = Image.new("RGB", (800, 600), "green")
        buf = BytesIO()
        img.save(buf, format="JPEG")
        result = _resize(buf.getvalue())
        assert result is not None

    def test_resize_corrupt_data_returns_none(self):
        from src.formatter.media import _resize
        result = _resize(b"not an image at all")
        assert result is None

    def test_cleanup_old_media(self):
        from src.formatter.media import cleanup_old_media
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("src.formatter.media.MEDIA_DIR", Path(tmpdir)):
                # Create some fake files
                for i in range(10):
                    (Path(tmpdir) / f"test_{i}.jpg").write_bytes(b"fake")
                deleted = cleanup_old_media(max_files=5)
                assert deleted == 5
                remaining = list(Path(tmpdir).glob("*.jpg"))
                assert len(remaining) == 5


# ═══════════════════════════════════════════════════════════════════════════════
# GDBrowser — difficulty parsing
# ═══════════════════════════════════════════════════════════════════════════════

class TestGDBrowserDifficulty:
    def test_parse_numeric_difficulty(self):
        from src.collectors.apis.gdbrowser import _parse_difficulty
        assert _parse_difficulty(0) == "N/A"
        assert _parse_difficulty(5) == "Insane"
        assert _parse_difficulty(10) == "Extreme Demon"

    def test_parse_string_difficulty(self):
        from src.collectors.apis.gdbrowser import _parse_difficulty
        assert _parse_difficulty("Hard Demon") == "Hard Demon"
        assert _parse_difficulty("Easy") == "Easy"

    def test_parse_unknown_difficulty(self):
        from src.collectors.apis.gdbrowser import _parse_difficulty
        assert _parse_difficulty(99) == "Unknown"
        assert _parse_difficulty("NotADifficulty") == "NotADifficulty"

    def test_official_difficulty_auto(self):
        from src.collectors.apis.gdbrowser import _official_difficulty
        assert _official_difficulty({"25": "1"}) == "Auto"

    def test_official_difficulty_demon(self):
        from src.collectors.apis.gdbrowser import _official_difficulty
        assert _official_difficulty({"17": "1", "43": "6"}) == "Extreme Demon"

    def test_official_difficulty_normal(self):
        from src.collectors.apis.gdbrowser import _official_difficulty
        assert _official_difficulty({"9": "20"}) == "Normal"

    def test_parse_official_response(self):
        from src.collectors.apis.gdbrowser import _parse_official_response
        result = _parse_official_response("1:12345:2:TestLevel:3:desc")
        assert result["1"] == "12345"
        assert result["2"] == "TestLevel"

    def test_decode_b64(self):
        from src.collectors.apis.gdbrowser import _decode_b64
        import base64
        encoded = base64.urlsafe_b64encode(b"Hello World").decode()
        assert _decode_b64(encoded) == "Hello World"

    def test_decode_b64_invalid(self):
        from src.collectors.apis.gdbrowser import _decode_b64
        # Invalid base64 either returns empty or a decoded string (with replace errors)
        result = _decode_b64("!!!invalid!!!")
        assert isinstance(result, str)  # should not raise


# ═══════════════════════════════════════════════════════════════════════════════
# Pointercrate — classifier
# ═══════════════════════════════════════════════════════════════════════════════

class TestPointercrateClassifier:
    def test_position_1_is_top1(self):
        from src.collectors.apis.pointercrate import _classify
        assert _classify(1) == "top1_verified"

    def test_position_50_is_level_verified(self):
        from src.collectors.apis.pointercrate import _classify
        assert _classify(50) == "level_verified"

    def test_position_75_is_level_verified(self):
        from src.collectors.apis.pointercrate import _classify
        assert _classify(75) == "level_verified"

    def test_position_76_is_demon_list_update(self):
        from src.collectors.apis.pointercrate import _classify
        assert _classify(76) == "demon_list_update"

    def test_position_150_is_demon_list_update(self):
        from src.collectors.apis.pointercrate import _classify
        assert _classify(150) == "demon_list_update"


# ═══════════════════════════════════════════════════════════════════════════════
# GitHub — repo validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestGitHubRepoValidation:
    def test_valid_repo_format(self):
        from src.collectors.apis.github import _REPO_RE
        assert _REPO_RE.fullmatch("owner/repo") is not None
        assert _REPO_RE.fullmatch("geode-sdk/geode") is not None

    def test_invalid_repo_format(self):
        from src.collectors.apis.github import _REPO_RE
        assert _REPO_RE.fullmatch("no-slash") is None
        assert _REPO_RE.fullmatch("too/many/slashes") is None
        assert _REPO_RE.fullmatch("") is None


# ═══════════════════════════════════════════════════════════════════════════════
# RSS — helper functions
# ═══════════════════════════════════════════════════════════════════════════════

class TestRSSHelpers:
    def test_strip_html(self):
        from src.collectors.rss import _strip_html
        assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"
        assert _strip_html("no tags") == "no tags"
        assert _strip_html("<div>  spaced  </div>") == "spaced"

    def test_unescape_html(self):
        from src.collectors.rss import _unescape
        assert _unescape("&amp;") == "&"
        assert _unescape("&lt;tag&gt;") == "<tag>"
        assert _unescape("plain text") == "plain text"

    def test_extract_image_media_content(self):
        from src.collectors.rss import _extract_image
        entry = MagicMock()
        entry.get = lambda k, d=[]: {
            "media_content": [{"medium": "image", "url": "https://img.com/photo.jpg"}],
            "media_thumbnail": [],
            "enclosures": [],
        }.get(k, d)
        assert _extract_image(entry) == "https://img.com/photo.jpg"

    def test_extract_image_none(self):
        from src.collectors.rss import _extract_image
        entry = MagicMock()
        entry.get = lambda k, d=[]: {
            "media_content": [],
            "media_thumbnail": [],
            "enclosures": [],
        }.get(k, d)
        assert _extract_image(entry) == ""


# ═══════════════════════════════════════════════════════════════════════════════
# Scraper — URL parsing
# ═══════════════════════════════════════════════════════════════════════════════

class TestScraperUrlParsing:
    def test_relative_url_resolution(self):
        from src.collectors.scraper import _parse
        html = '<article><h2><a href="/news/article">Headline Here Test</a></h2></article>'
        items = _parse(html, "https://example.com", 1, "rocketleague")
        assert len(items) == 1
        assert items[0].url == "https://example.com/news/article"

    def test_protocol_relative_url(self):
        from src.collectors.scraper import _parse
        html = '<article><h2><a href="//cdn.example.com/news">Protocol Relative Link</a></h2></article>'
        items = _parse(html, "https://example.com", 1, "rocketleague")
        assert len(items) == 1
        assert items[0].url.startswith("https://")

    def test_skips_short_titles(self):
        from src.collectors.scraper import _parse
        html = '<article><h2><a href="/x">Short</a></h2></article>'
        items = _parse(html, "https://example.com", 1, "rocketleague")
        assert len(items) == 0  # "Short" < 15 chars

    def test_dedup_same_url(self):
        from src.collectors.scraper import _parse
        html = '''
        <article><h2><a href="/news/same">Headline One Here Now</a></h2></article>
        <article><h2><a href="/news/same">Headline Two Here Now</a></h2></article>
        '''
        items = _parse(html, "https://example.com", 1, "rocketleague")
        assert len(items) == 1

    def test_max_items_cap(self):
        from src.collectors.scraper import _parse
        html = "".join(
            f'<article><h2><a href="/news/{i}">Headline number {i} is long enough</a></h2></article>'
            for i in range(20)
        )
        items = _parse(html, "https://example.com", 1, "rocketleague")
        assert len(items) <= 10

    def test_empty_html(self):
        from src.collectors.scraper import _parse
        items = _parse("", "https://example.com", 1, "rocketleague")
        assert items == []

    def test_no_articles_fallback_to_headings(self):
        from src.collectors.scraper import _parse
        html = '<h2><a href="/news/1">Heading Without Article Wrapper Here</a></h2>'
        items = _parse(html, "https://example.com", 1, "rocketleague")
        assert len(items) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Twitter monitor — _extract_tweets with embedded content
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractTweetsEmbedded:
    def test_skips_retweeted_status_result(self):
        from src.collectors.twitter_monitor import _extract_tweets
        data = {
            "timeline": {
                "tweet_result": {
                    "legacy": {"id_str": "1", "full_text": "Main tweet"},
                    "retweeted_status_result": {
                        "result": {
                            "legacy": {"id_str": "2", "full_text": "Embedded RT"},
                        }
                    }
                }
            }
        }
        tweets = _extract_tweets(data)
        ids = {t["legacy"]["id_str"] for t in tweets}
        assert "1" in ids
        assert "2" not in ids  # embedded RT should be skipped

    def test_skips_quoted_status_result(self):
        from src.collectors.twitter_monitor import _extract_tweets
        data = {
            "tweet": {
                "legacy": {"id_str": "10", "full_text": "Quote tweet"},
                "quoted_status_result": {
                    "result": {
                        "legacy": {"id_str": "20", "full_text": "Quoted original"},
                    }
                }
            }
        }
        tweets = _extract_tweets(data)
        ids = {t["legacy"]["id_str"] for t in tweets}
        assert "10" in ids
        assert "20" not in ids  # embedded quote should be skipped

    def test_deduplicates_same_tweet_id(self):
        from src.collectors.twitter_monitor import _extract_tweets
        tweet = {"legacy": {"id_str": "99", "full_text": "Same tweet"}}
        data = {"a": tweet, "b": {"nested": tweet}}
        tweets = _extract_tweets(data)
        assert len(tweets) == 1

    def test_handles_empty_data(self):
        from src.collectors.twitter_monitor import _extract_tweets
        assert _extract_tweets({}) == []
        assert _extract_tweets({"a": "b"}) == []

    def test_handles_lists_in_data(self):
        from src.collectors.twitter_monitor import _extract_tweets
        data = {
            "entries": [
                {"legacy": {"id_str": "1", "full_text": "Tweet 1"}},
                {"legacy": {"id_str": "2", "full_text": "Tweet 2"}},
            ]
        }
        tweets = _extract_tweets(data)
        assert len(tweets) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Database — insert_raw_content dedup
# ═══════════════════════════════════════════════════════════════════════════════

class TestInsertRawContentDedup:
    def test_first_insert_returns_new(self):
        conn = _db()
        sid = upsert_source(conn, "rl", "s", "rss", {})
        rc = _rc(source_id=sid, external_id="unique_001")
        _, is_new = insert_raw_content(conn, rc)
        assert is_new is True

    def test_second_insert_returns_not_new(self):
        conn = _db()
        sid = upsert_source(conn, "rl", "s", "rss", {})
        rc = _rc(source_id=sid, external_id="dup_001")
        insert_raw_content(conn, rc)
        _, is_new = insert_raw_content(conn, rc)
        assert is_new is False

    def test_same_external_id_different_source_both_new(self):
        conn = _db()
        sid1 = upsert_source(conn, "rl", "s1", "rss", {})
        sid2 = upsert_source(conn, "rl", "s2", "rss", {})
        rc1 = _rc(source_id=sid1, external_id="shared_id")
        rc2 = _rc(source_id=sid2, external_id="shared_id")
        _, is_new1 = insert_raw_content(conn, rc1)
        _, is_new2 = insert_raw_content(conn, rc2)
        assert is_new1 is True
        assert is_new2 is True  # different source_id


# ═══════════════════════════════════════════════════════════════════════════════
# Database — similarity at various thresholds
# ═══════════════════════════════════════════════════════════════════════════════

class TestSimilarityThresholds:
    def _setup(self, conn, text):
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) VALUES (?, ?, 'queued', ?)",
            ("rl", text, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    @pytest.mark.parametrize("existing,candidate,expected", [
        # Identical
        ("Rocket League Season 15 is live now", "Rocket League Season 15 is live now", True),
        # Very similar
        ("Rocket League Season 15 is live!", "Rocket League Season 15 is live now!", True),
        # Completely different
        ("Rocket League update", "Geometry Dash demon verified", False),
        # Partial overlap but different enough
        ("RLCS Major starts in Europe today with 16 teams", "RLCS regionals complete, top 8 qualify for Major", False),
    ])
    def test_similarity(self, existing, candidate, expected):
        conn = _db()
        self._setup(conn, existing)
        result = is_similar_story(conn, candidate, "rl")
        assert result == expected


# ═══════════════════════════════════════════════════════════════════════════════
# _build_context — extensive field coverage
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildContextFields:
    def test_all_universal_fields_present(self):
        ctx = _build_context(_rc(title="T", url="U", body="B", author="A"))
        for field in ("title", "url", "headline", "summary", "details",
                      "description", "author", "emoji", "bullet1", "bullet2",
                      "bullet3", "event", "player", "creator", "brand",
                      "items", "achievement", "context", "level", "level_name",
                      "changes", "mod_name"):
            assert field in ctx, f"Missing field: {field}"

    def test_version_only_when_found(self):
        ctx = _build_context(_rc(title="No version here"))
        assert "version" not in ctx

        ctx = _build_context(_rc(title="Version 2.68 released"))
        assert ctx["version"] == "2.68"

    def test_metadata_integer_values_converted(self):
        ctx = _build_context(_rc(metadata={"position": 5, "stars": 10}))
        assert ctx["position"] == "5"
        assert ctx["stars"] == "10"


# ═══════════════════════════════════════════════════════════════════════════════
# Health check — probe functions
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthCheckProbes:
    @pytest.mark.asyncio
    async def test_probe_rss_healthy(self):
        from src.monitoring.health_check import _probe_rss
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.text = '<rss><channel><item><title>Test</title></item></channel></rss>'
        mock_resp.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        status, detail = await _probe_rss({"url": "https://example.com/feed"}, mock_client)
        assert status == "healthy"

    @pytest.mark.asyncio
    async def test_probe_scraper_healthy(self):
        from src.monitoring.health_check import _probe_scraper
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.text = "x" * 1000
        mock_resp.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        status, detail = await _probe_scraper({"url": "https://example.com"}, mock_client)
        assert status == "healthy"

    @pytest.mark.asyncio
    async def test_probe_scraper_tiny_response(self):
        from src.monitoring.health_check import _probe_scraper
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.text = "x" * 100
        mock_resp.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        status, detail = await _probe_scraper({"url": "https://example.com"}, mock_client)
        assert status == "degraded"

    @pytest.mark.asyncio
    async def test_probe_api_flashback_healthy(self):
        from src.monitoring.health_check import _probe_api
        status, detail = await _probe_api({"collector": "flashback"}, AsyncMock())
        assert status == "healthy"

    @pytest.mark.asyncio
    async def test_probe_api_unknown_collector(self):
        from src.monitoring.health_check import _probe_api
        status, detail = await _probe_api({"collector": "nonexistent"}, AsyncMock())
        assert status == "degraded"


# ═══════════════════════════════════════════════════════════════════════════════
# Main — collector factory
# ═══════════════════════════════════════════════════════════════════════════════

class TestCollectorFactory:
    def test_rss_collector(self):
        from src.main import _make_collector
        c = _make_collector(1, "rss", {"url": "https://example.com/feed"}, "rocketleague")
        assert c is not None
        assert type(c).__name__ == "RSSCollector"

    def test_scraper_collector(self):
        from src.main import _make_collector
        c = _make_collector(1, "scraper", {"url": "https://example.com"}, "rocketleague")
        assert c is not None
        assert type(c).__name__ == "ScraperCollector"

    def test_twitter_collector(self):
        from src.main import _make_collector
        c = _make_collector(1, "twitter", {"account_id": "RocketLeague"}, "rocketleague")
        assert c is not None
        assert type(c).__name__ == "TwitterMonitorCollector"

    def test_youtube_collector(self):
        from src.main import _make_collector
        c = _make_collector(1, "youtube", {"channel_id": "UCtest"}, "rocketleague")
        assert c is not None
        assert type(c).__name__ == "YouTubeCollector"

    def test_api_pointercrate(self):
        from src.main import _make_collector
        c = _make_collector(1, "api", {"collector": "pointercrate"}, "geometrydash")
        assert c is not None
        assert type(c).__name__ == "PointercrateCollector"

    def test_api_gdbrowser(self):
        from src.main import _make_collector
        c = _make_collector(1, "api", {"collector": "gdbrowser"}, "geometrydash")
        assert c is not None

    def test_api_github(self):
        from src.main import _make_collector
        c = _make_collector(1, "api", {"collector": "github", "repo": "geode-sdk/geode"}, "geometrydash")
        assert c is not None

    def test_api_flashback(self):
        from src.main import _make_collector
        c = _make_collector(1, "api", {"collector": "flashback"}, "rocketleague")
        assert c is not None

    def test_api_rl_stats(self):
        from src.main import _make_collector
        c = _make_collector(1, "api", {"collector": "rl_stats"}, "rocketleague")
        assert c is not None

    def test_unknown_type_returns_none(self):
        from src.main import _make_collector
        c = _make_collector(1, "unknown_type", {}, "rocketleague")
        assert c is None

    def test_unknown_api_collector_returns_none(self):
        from src.main import _make_collector
        c = _make_collector(1, "api", {"collector": "nonexistent"}, "rocketleague")
        assert c is None

    def test_reddit_clips_collector(self):
        from src.main import _make_collector
        c = _make_collector(1, "reddit_clips", {"subreddit": "RocketLeague"}, "rocketleague")
        assert c is not None
        assert type(c).__name__ == "RedditClipCollector"


# ═══════════════════════════════════════════════════════════════════════════════
# GD Notable creators
# ═══════════════════════════════════════════════════════════════════════════════

class TestGDNotableCreators:
    def test_notable_creators_set_is_lowercase(self):
        from src.collectors.apis.gdbrowser import _NOTABLE_CREATORS
        for name in _NOTABLE_CREATORS:
            assert name == name.lower()

    def test_known_creators_present(self):
        from src.collectors.apis.gdbrowser import _NOTABLE_CREATORS
        assert "viprin" in _NOTABLE_CREATORS
        assert "robtop" in _NOTABLE_CREATORS
        assert "knobbelboy" in _NOTABLE_CREATORS
        assert "npesta" in _NOTABLE_CREATORS

    def test_creator_count(self):
        from src.collectors.apis.gdbrowser import _NOTABLE_CREATORS
        assert len(_NOTABLE_CREATORS) >= 50


# ═══════════════════════════════════════════════════════════════════════════════
# _cap — edge cases for trailing punctuation stripping
# ═══════════════════════════════════════════════════════════════════════════════

class TestCapTrailingPunctuation:
    def test_strips_trailing_comma(self):
        result = _cap("Hello, world, this is a very long text", 15)
        assert not result.rstrip("…").endswith(",")

    def test_strips_trailing_semicolon(self):
        result = _cap("Data; more data; even more data here", 20)
        assert not result.rstrip("…").endswith(";")

    def test_strips_trailing_period(self):
        result = _cap("Sentence one. Sentence two. Sentence three.", 20)
        assert not result.rstrip("…").endswith(".")
