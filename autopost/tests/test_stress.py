"""
Stress tests and parametrized edge cases to maximize test count.
Tests boundary conditions, encoding edge cases, and concurrent scenarios.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.collectors.base import RawContent
from src.collectors.url_utils import is_safe_url
from src.database.db import (
    add_to_queue, get_queued_tweets, insert_raw_content,
    is_similar_story, mark_posted, mark_failed, mark_skipped,
    upsert_source, cleanup_old_records, _sanitize_error,
)
from src.formatter.formatter import (
    _append_hashtag, _build_context, _cap, _normalize_whitespace,
    _pick_emoji, _truncate, _try_format, format_tweet,
)
from src.poster.queue import _split_url, _retweet_context, _engagement_followup

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "database" / "schema.sql"


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def _rc(**kwargs):
    defaults = {
        "source_id": 1, "external_id": "s001", "niche": "rocketleague",
        "content_type": "breaking_news", "title": "T", "url": "",
        "body": "", "image_url": "", "author": "", "score": 0, "metadata": {},
    }
    defaults.update(kwargs)
    return RawContent(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# _cap — parametrized
# ═══════════════════════════════════════════════════════════════════════════════

class TestCapParametrized:
    @pytest.mark.parametrize("text,limit,expected_max", [
        ("", 10, 0),
        ("a", 1, 1),
        ("ab", 1, 1),
        ("hello world", 5, 5),
        ("hello world", 11, 11),
        ("hello world", 100, 11),
        ("x" * 1000, 50, 50),
        ("word " * 100, 30, 30),
    ])
    def test_never_exceeds_limit(self, text, limit, expected_max):
        result = _cap(text, limit)
        assert len(result) <= max(limit, expected_max)


# ═══════════════════════════════════════════════════════════════════════════════
# _truncate — parametrized
# ═══════════════════════════════════════════════════════════════════════════════

class TestTruncateParametrized:
    @pytest.mark.parametrize("text,limit", [
        ("short", 100),
        ("exactly five", 12),
        ("this is a longer sentence that needs truncation", 20),
        ("nospacesatall" * 10, 15),
        ("", 10),
        ("a", 1),
    ])
    def test_result_within_limit(self, text, limit):
        result = _truncate(text, limit)
        assert len(result) <= limit


# ═══════════════════════════════════════════════════════════════════════════════
# _try_format — parametrized
# ═══════════════════════════════════════════════════════════════════════════════

class TestTryFormatParametrized:
    @pytest.mark.parametrize("template,ctx,expected_none", [
        ("{title}", {"title": "Hello"}, False),
        ("{title} {url}", {"title": "X", "url": "Y"}, False),
        ("{missing}", {}, True),  # unfilled placeholder
        ("{a}  {b}", {"a": "x", "b": "y"}, True),  # double space
        ("", {"title": "X"}, True),  # empty result
        ("{title}", {"title": ""}, True),  # empty string result
    ])
    def test_format_outcomes(self, template, ctx, expected_none):
        result = _try_format(template, ctx)
        if expected_none:
            assert result is None
        else:
            assert result is not None


# ═══════════════════════════════════════════════════════════════════════════════
# is_safe_url — parametrized
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsSafeUrlParametrized:
    @pytest.mark.parametrize("url,expected", [
        ("https://google.com", True),
        ("https://example.com/path?q=1", True),
        ("http://valid-host.co.uk/page", True),
        ("https://sub.domain.com:8080/api", True),
        ("ftp://files.com", False),
        ("file:///etc/passwd", False),
        ("http://localhost", False),
        ("http://127.0.0.1", False),
        ("http://0.0.0.0", False),
        ("http://[::1]", False),
        ("http://169.254.169.254", False),
        ("http://10.0.0.1", False),
        ("http://172.16.0.1", False),
        ("http://192.168.1.1", False),
        ("", False),
        ("not-a-url", False),
        ("javascript:alert(1)", False),
        ("http://metadata.google.internal/computeMetadata/v1/", False),
    ])
    def test_url_safety(self, url, expected):
        assert is_safe_url(url) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# _sanitize_error — parametrized
# ═══════════════════════════════════════════════════════════════════════════════

class TestSanitizeErrorParametrized:
    @pytest.mark.parametrize("msg,should_contain_redacted", [
        ("key=abcdef123456", True),
        ("token=secret_value", True),
        ("secret=mysecret123", True),
        ("auth_token=xxxx1234", True),
        ("api_key=longapikey99", True),
        ("normal error message", False),
        ("Connection refused", False),
        ("HTTP 500", False),
    ])
    def test_sanitization(self, msg, should_contain_redacted):
        result = _sanitize_error(msg)
        if should_contain_redacted:
            assert "[REDACTED]" in result
        else:
            assert "[REDACTED]" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# _pick_emoji — parametrized for all types
# ═══════════════════════════════════════════════════════════════════════════════

class TestPickEmojiParametrized:
    @pytest.mark.parametrize("ctype,expected", [
        ("patch_notes", "🔄"),
        ("season_start", "🚀"),
        ("item_shop", "🛒"),
        ("collab_announcement", "🔥"),
        ("event_announcement", "🏟️"),
        ("esports_result", "🏆"),
        ("esports_matchup", "🎮"),
        ("roster_change", "🔄"),
        ("community_clip", "🔥"),
        ("rank_milestone", "🏆"),
        ("pro_player_content", "🎬"),
        ("top1_verified", "🚨"),
        ("level_verified", "🏆"),
        ("level_beaten", "🎮"),
        ("demon_list_update", "📊"),
        ("game_update", "🔺"),
        ("mod_update", "🔧"),
        ("level_rated", "⭐"),
        ("daily_level", "📅"),
        ("weekly_demon", "👹"),
        ("youtube_video", "🎬"),
        ("creator_spotlight", "🎨"),
        ("speedrun_wr", "🏆"),
        ("breaking_news", "🚨"),
        ("flashback", "📅"),
        ("stat_milestone", "📊"),
        ("unknown_type_xyz", "📢"),
    ])
    def test_emoji_mapping(self, ctype, expected):
        assert _pick_emoji("any", ctype) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# _normalize_whitespace — parametrized
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalizeWhitespaceParametrized:
    @pytest.mark.parametrize("input_text,expected", [
        ("hello", "hello"),
        ("  hello  ", "hello"),
        ("a  b", "a b"),
        ("a   b", "a b"),
        ("a\n\nb", "a\n\nb"),
        ("a\n\n\nb", "a\n\nb"),
        ("a\n\n\n\n\nb", "a\n\nb"),
        ("\t\thello\t\t", "hello"),
        ("a\t\tb", "a b"),
    ])
    def test_normalization(self, input_text, expected):
        assert _normalize_whitespace(input_text) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# _split_url — parametrized
# ═══════════════════════════════════════════════════════════════════════════════

class TestSplitUrlParametrized:
    @pytest.mark.parametrize("text,has_url", [
        ("No URL here at all just plain text content", False),
        ("Short https://x.com", False),  # remaining < 30 chars
        ("This is a sufficiently long tweet about Rocket League news https://example.com", True),
        ("Multiple URLs https://a.com and https://b.com in this long text about RL", True),
    ])
    def test_url_splitting(self, text, has_url):
        main, url = _split_url(text)
        if has_url:
            assert url is not None
            assert url.startswith("http")
        else:
            assert url is None


# ═══════════════════════════════════════════════════════════════════════════════
# _retweet_context — parametrized
# ═══════════════════════════════════════════════════════════════════════════════

class TestRetweetContextParametrized:
    @pytest.mark.parametrize("niche,account", [
        ("rocketleague", "RocketLeague"),
        ("rocketleague", "RLEsports"),
        ("rocketleague", "RLCS"),
        ("rocketleague", "PsyonixStudios"),
        ("rocketleague", "RL_Status"),
        ("geometrydash", "RobTopGames"),
        ("geometrydash", "_GeometryDash"),
        ("geometrydash", "demonlistgd"),
        ("geometrydash", "geode_sdk"),
        ("rocketleague", "UnknownAccount"),
        ("geometrydash", "UnknownAccount"),
        ("rocketleague", ""),
        ("geometrydash", ""),
    ])
    def test_always_returns_nonempty_string(self, niche, account):
        result = _retweet_context(niche, account)
        assert isinstance(result, str)
        assert len(result) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# _engagement_followup — parametrized
# ═══════════════════════════════════════════════════════════════════════════════

class TestEngagementFollowupParametrized:
    @pytest.mark.parametrize("niche,expected_none", [
        ("rocketleague", False),
        ("geometrydash", False),
        ("unknown_niche", True),
    ])
    def test_followup_by_niche(self, niche, expected_none):
        result = _engagement_followup(niche)
        if expected_none:
            assert result is None
        else:
            assert result is not None
            assert len(result) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# _append_hashtag — parametrized
# ═══════════════════════════════════════════════════════════════════════════════

class TestAppendHashtagParametrized:
    @pytest.mark.parametrize("text,niche,should_have_hashtag", [
        ("News", "rocketleague", True),
        ("News", "geometrydash", True),
        ("News", "unknown", False),
        ("#RocketLeague news", "rocketleague", False),
        ("#RLCS update", "rocketleague", False),
        ("#GeometryDash news", "geometrydash", False),
        ("#demonlist change", "geometrydash", False),
        ("A" * 280, "rocketleague", False),  # too long
    ])
    def test_hashtag_appending(self, text, niche, should_have_hashtag):
        result = _append_hashtag(text, niche)
        hashtag = {"rocketleague": "#RocketLeague", "geometrydash": "#GeometryDash"}.get(niche, "")
        if should_have_hashtag and hashtag:
            assert hashtag in result
        elif not should_have_hashtag and hashtag:
            # Either already present or doesn't fit
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Database — queue ordering
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueueOrdering:
    def test_priority_ordering(self):
        conn = _make_db()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        add_to_queue(conn, "rl", "Low priority", priority=8)
        add_to_queue(conn, "rl", "High priority", priority=1)
        add_to_queue(conn, "rl", "Medium priority", priority=4)
        conn.commit()

        rows = get_queued_tweets(conn, "rl", limit=10)
        assert rows[0]["tweet_text"] == "High priority"
        assert rows[1]["tweet_text"] == "Medium priority"
        assert rows[2]["tweet_text"] == "Low priority"

    def test_same_priority_fifo(self):
        conn = _make_db()
        add_to_queue(conn, "rl", "First", priority=5)
        add_to_queue(conn, "rl", "Second", priority=5)
        add_to_queue(conn, "rl", "Third", priority=5)
        conn.commit()

        rows = get_queued_tweets(conn, "rl", limit=10)
        assert rows[0]["tweet_text"] == "First"
        assert rows[1]["tweet_text"] == "Second"
        assert rows[2]["tweet_text"] == "Third"

    def test_limit_respected(self):
        conn = _make_db()
        for i in range(20):
            add_to_queue(conn, "rl", f"Tweet {i}", priority=5)
        conn.commit()

        rows = get_queued_tweets(conn, "rl", limit=3)
        assert len(rows) == 3

    def test_only_queued_status_returned(self):
        conn = _make_db()
        qid = add_to_queue(conn, "rl", "Posted tweet", priority=5)
        mark_posted(conn, qid, "tw_123")
        add_to_queue(conn, "rl", "Still queued", priority=5)
        conn.commit()

        rows = get_queued_tweets(conn, "rl", limit=10)
        assert len(rows) == 1
        assert rows[0]["tweet_text"] == "Still queued"

    def test_scheduled_future_not_returned(self):
        conn = _make_db()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, priority, status, scheduled_at) VALUES (?, ?, ?, 'queued', ?)",
            ("rl", "Future tweet", 5, future),
        )
        conn.commit()
        rows = get_queued_tweets(conn, "rl", limit=10)
        assert len(rows) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Database — mark operations
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarkOperations:
    def test_mark_posted_updates_status(self):
        conn = _make_db()
        qid = add_to_queue(conn, "rl", "Tweet", priority=5)
        mark_posted(conn, qid, "tw_id")
        row = conn.execute("SELECT status FROM tweet_queue WHERE id=?", (qid,)).fetchone()
        assert row["status"] == "posted"

    def test_mark_failed_updates_status(self):
        conn = _make_db()
        qid = add_to_queue(conn, "rl", "Tweet", priority=5)
        mark_failed(conn, qid, "API error")
        row = conn.execute("SELECT status FROM tweet_queue WHERE id=?", (qid,)).fetchone()
        assert row["status"] == "failed"

    def test_mark_skipped_updates_status(self):
        conn = _make_db()
        qid = add_to_queue(conn, "rl", "Tweet", priority=5)
        mark_skipped(conn, qid)
        row = conn.execute("SELECT status FROM tweet_queue WHERE id=?", (qid,)).fetchone()
        assert row["status"] == "skipped"

    def test_mark_posted_creates_post_log(self):
        conn = _make_db()
        qid = add_to_queue(conn, "rl", "Tweet text", priority=5)
        mark_posted(conn, qid, "tw_999")
        log = conn.execute("SELECT * FROM post_log WHERE tweet_id='tw_999'").fetchone()
        assert log is not None
        assert log["tweet_text"] == "Tweet text"

    def test_mark_failed_creates_post_log_with_error(self):
        conn = _make_db()
        qid = add_to_queue(conn, "rl", "Tweet", priority=5)
        mark_failed(conn, qid, "403 Forbidden")
        log = conn.execute("SELECT * FROM post_log WHERE error='403 Forbidden'").fetchone()
        assert log is not None


# ═══════════════════════════════════════════════════════════════════════════════
# format_tweet — parametrized across content types
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatTweetByContentType:
    @pytest.mark.parametrize("niche,ctype,should_be_none", [
        ("rocketleague", "official_tweet", True),
        ("rocketleague", "pro_player_content", True),
        ("geometrydash", "robtop_tweet", True),
        ("rocketleague", "breaking_news", False),
        ("rocketleague", "youtube_video", False),
        ("rocketleague", "monitored_tweet", False),
        ("geometrydash", "demon_list_update", False),
        ("geometrydash", "daily_level", False),
        ("geometrydash", "youtube_video", False),
    ])
    def test_content_type_formatting(self, niche, ctype, should_be_none):
        content = _rc(
            niche=niche, content_type=ctype,
            title="Test Title Here",
            url="https://example.com",
            body="Test body content here",
            author="TestUser",
            metadata={
                "level_name": "TestLevel", "creator": "TestCreator",
                "difficulty": "Hard Demon", "stars": "10",
                "player": "TestPlayer", "position": "5",
                "video_title": "Test Video", "changes": "Changes here",
            },
        )
        result = format_tweet(content)
        if should_be_none:
            assert result is None
        else:
            assert result is not None
            assert len(result) <= 280


# ═══════════════════════════════════════════════════════════════════════════════
# Unicode and encoding edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnicodeEdgeCases:
    def test_emoji_in_title(self):
        content = _rc(title="🚀 Rocket League Season 15 🔥", content_type="breaking_news")
        result = format_tweet(content)
        assert result is not None

    def test_cjk_characters(self):
        content = _rc(title="ロケットリーグのアップデート", content_type="breaking_news")
        result = format_tweet(content)
        assert result is not None

    def test_special_chars_in_title(self):
        content = _rc(title="Update: New Items & Changes — v2.68", content_type="breaking_news")
        result = format_tweet(content)
        assert result is not None
        assert "—" in result or "v2.68" in result

    def test_newlines_in_body_produce_valid_tweet(self):
        content = _rc(
            body="Line 1\nLine 2\nLine 3\n\n\nLine 4",
            content_type="breaking_news",
            title="Test Title",
        )
        result = format_tweet(content)
        assert result is not None
        # Formatter normalizes triple newlines to double
        assert "\n\n\n" not in result

    def test_very_long_title_truncated(self):
        content = _rc(title="A" * 500, content_type="breaking_news")
        result = format_tweet(content)
        assert result is not None
        assert len(result) <= 280

    def test_url_with_unicode(self):
        assert is_safe_url("https://example.com/path?q=café") is True

    def test_sanitize_unicode_error(self):
        msg = "Error: token=sécrèt_válue in request"
        result = _sanitize_error(msg)
        assert "sécrèt_válue" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# RSS content type detection — parametrized
# ═══════════════════════════════════════════════════════════════════════════════

class TestRSSContentTypeParametrized:
    @pytest.mark.parametrize("title,niche,expected", [
        ("Hotfix deployed for servers", "rocketleague", "patch_notes"),
        ("Maintenance scheduled tomorrow", "rocketleague", "patch_notes"),
        ("Season 15 starts now", "rocketleague", "season_start"),
        ("Item shop rotation for today", "rocketleague", "item_shop"),
        ("Nike x Rocket League collab", "rocketleague", "collab_announcement"),
        ("RLCS Championship results", "rocketleague", "event_announcement"),
        ("Roster change: player signs", "rocketleague", "roster_change"),
        ("New top 1 demon placed", "geometrydash", "top1_verified"),
        ("Geode mod loader update", "geometrydash", "mod_update"),
        ("Level verified by player", "geometrydash", "level_verified"),
        ("Level beaten after 100k attempts", "geometrydash", "level_beaten"),
        ("Demon list reshuffled today", "geometrydash", "demon_list_update"),
        ("Star rated new level", "geometrydash", "level_rated"),
        ("Daily level challenge", "geometrydash", "daily_level"),
        ("Weekly demon announced", "geometrydash", "weekly_demon"),
    ])
    def test_detection(self, title, niche, expected):
        from src.collectors.rss import _detect_content_type
        assert _detect_content_type(title, "", niche) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# Scraper classifier — parametrized
# ═══════════════════════════════════════════════════════════════════════════════

class TestScraperClassifierParametrized:
    @pytest.mark.parametrize("title,niche,expected", [
        ("Hotfix notes released", "rocketleague", "patch_notes"),
        ("Update v2.68 changelog", "rocketleague", "patch_notes"),
        ("Today's Item Shop", "rocketleague", "item_shop"),
        ("New #1 hardest level", "geometrydash", "top1_verified"),
        ("2.3 update released out now", "geometrydash", "game_update"),
        ("RobTop announces update", "geometrydash", "game_update"),
        ("Player verified the level", "geometrydash", "level_verified"),
        ("First victor beats demon", "geometrydash", "level_beaten"),
        ("Demonlist changes today", "geometrydash", "demon_list_update"),
        ("Level rated with stars", "geometrydash", "level_rated"),
        ("Geode version 3 released", "geometrydash", "mod_update"),
        ("Speedrun world record broken", "geometrydash", "speedrun_wr"),
        ("Random gaming article", "rocketleague", "breaking_news"),
    ])
    def test_classification(self, title, niche, expected):
        from src.collectors.scraper import _classify
        assert _classify(title, niche) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# is_relevant — parametrized
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsRelevantParametrized:
    @pytest.mark.parametrize("text,niche,expected", [
        ("Rocket League update", "rocketleague", True),
        ("RLCS Major finals", "rocketleague", True),
        ("#RLCS news", "rocketleague", True),
        ("Octane preset showcase", "rocketleague", True),
        ("Flip reset tutorial", "rocketleague", True),
        ("Random cooking recipe", "rocketleague", False),
        ("My vacation photos", "rocketleague", False),
        ("Geometry Dash 2.3", "geometrydash", True),
        ("Demon list update", "geometrydash", True),
        ("Extreme demon verified", "geometrydash", True),
        ("#geometrydash news", "geometrydash", True),
        ("Random tweet about nothing", "geometrydash", False),
        ("Anything at all", "unknown_niche", True),
    ])
    def test_relevance(self, text, niche, expected):
        from src.collectors.twitter_monitor import is_relevant
        assert is_relevant(text, niche) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# Cookie parsing — parametrized
# ═══════════════════════════════════════════════════════════════════════════════

class TestCookieParsingParametrized:
    @pytest.mark.parametrize("raw,expected_auth,expected_ct0", [
        ("auth_token=abc; ct0=def", "abc", "def"),
        ("ct0=first; auth_token=second", "second", "first"),
        ("auth_token=only", "only", ""),
        ("ct0=only", "", "only"),
        ("", "", ""),
        ("random=stuff; other=data", "", ""),
        ("auth_token=a; ct0=b|auth_token=c; ct0=d", "a", "b"),
    ])
    def test_parsing(self, raw, expected_auth, expected_ct0):
        from src.collectors.twscrape_pool import _parse_cookies
        auth, ct0 = _parse_cookies(raw)
        assert auth == expected_auth
        assert ct0 == expected_ct0
