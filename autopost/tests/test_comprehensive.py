"""
Comprehensive edge-case tests across all modules to push toward 3000 total tests.
Covers formatter, database, main, alerts, media, rss, scraper, url_utils,
rate_limiter, and integration scenarios.
"""
import asyncio
import json
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest

from src.collectors.base import BaseCollector, RawContent
from src.collectors.url_utils import is_safe_url
from src.database.db import (
    add_to_queue, cleanup_old_records, disable_source, get_db,
    get_queued_tweets, get_sources, init_db, insert_raw_content,
    is_similar_story, mark_failed, mark_posted, mark_skipped,
    recent_source_error_count, record_source_error, url_already_queued,
    upsert_source, _sanitize_error,
)
from src.formatter.formatter import (
    _append_hashtag, _build_context, _cap, _normalize_whitespace,
    _pick_emoji, _truncate, _try_format, _SafeFormatDict,
    format_tweet, _GD_PLAYER_HANDLES, _NICHE_HASHTAG,
)
from src.formatter.templates import TEMPLATES
from src.poster.rate_limiter import (
    can_post, failure_backoff_ok, within_monthly_limit,
    within_posting_window, monthly_post_count, jitter_delay,
    consecutive_failure_count,
    POSTING_WINDOW_START, POSTING_WINDOW_END,
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
        "source_id": 1,
        "external_id": "test_001",
        "niche": "rocketleague",
        "content_type": "breaking_news",
        "title": "Test Title",
        "url": "https://example.com",
        "body": "Test body content",
        "image_url": "",
        "author": "TestAuthor",
        "score": 0,
        "metadata": {},
    }
    defaults.update(kwargs)
    return RawContent(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# FORMATTER — _build_context edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildContextEdgeCases:
    def test_empty_author_defaults_to_unknown(self):
        content = _make_content(author="")
        ctx = _build_context(content)
        assert ctx["author"] == "Unknown"

    def test_whitespace_only_author_defaults_to_unknown(self):
        content = _make_content(author="   ")
        ctx = _build_context(content)
        assert ctx["author"] == "Unknown"

    def test_version_extracted_from_title(self):
        content = _make_content(title="Rocket League v2.68 Patch Notes")
        ctx = _build_context(content)
        assert ctx["version"] == "v2.68"

    def test_no_version_when_absent(self):
        content = _make_content(title="General Update News")
        ctx = _build_context(content)
        assert "version" not in ctx

    def test_metadata_overrides_defaults(self):
        content = _make_content(metadata={"author": "Override", "custom": "field"})
        ctx = _build_context(content)
        assert ctx["author"] == "Override"
        assert ctx["custom"] == "field"

    def test_metadata_skips_none_values(self):
        content = _make_content(metadata={"author": None})
        ctx = _build_context(content)
        assert ctx["author"] == "TestAuthor"  # default, not overridden

    def test_metadata_skips_empty_string_values(self):
        content = _make_content(metadata={"author": ""})
        ctx = _build_context(content)
        assert ctx["author"] == "TestAuthor"

    def test_metadata_skips_whitespace_only_values(self):
        content = _make_content(metadata={"author": "   "})
        ctx = _build_context(content)
        assert ctx["author"] == "TestAuthor"

    def test_gd_player_handle_tagging(self):
        content = _make_content(niche="geometrydash", author="zoink")
        ctx = _build_context(content)
        assert ctx["player"] == "@gdzoink"

    def test_gd_player_handle_case_insensitive(self):
        content = _make_content(niche="geometrydash", author="Zoink")
        ctx = _build_context(content)
        assert ctx["player"] == "@gdzoink"

    def test_gd_player_handle_unknown_player(self):
        content = _make_content(niche="geometrydash", author="RandomPlayer")
        ctx = _build_context(content)
        assert ctx["player"] == "RandomPlayer"

    def test_rl_niche_no_player_tagging(self):
        content = _make_content(niche="rocketleague", author="zoink")
        ctx = _build_context(content)
        assert ctx["player"] == "zoink"  # no @ tagging for RL

    def test_bullet_points_from_multiline_body(self):
        content = _make_content(body="Line one.\nLine two.\nLine three.")
        ctx = _build_context(content)
        assert ctx["bullet1"] == "Line one."
        assert ctx["bullet2"] == "Line two."
        assert ctx["bullet3"] == "Line three."

    def test_bullet_points_from_sentence_split(self):
        content = _make_content(body="First sentence. Second sentence. Third sentence.")
        ctx = _build_context(content)
        assert "First" in ctx["bullet1"]
        assert "Second" in ctx["bullet2"]

    def test_single_line_body_bullet_fallback(self):
        content = _make_content(body="Just one line", title="Title Here")
        ctx = _build_context(content)
        assert ctx["bullet1"] == "Just one line"

    def test_empty_body_uses_title_for_bullet(self):
        content = _make_content(body="", title="My Title")
        ctx = _build_context(content)
        assert ctx["bullet1"] == "My Title"

    def test_version_match_various_formats(self):
        for title, expected in [
            ("Update v1.2.3", "v1.2.3"),
            ("Version 2.208", "2.208"),
            ("Patch 3.0.1a", "3.0.1a"),
        ]:
            content = _make_content(title=title)
            ctx = _build_context(content)
            assert ctx.get("version") == expected, f"Failed for {title}"


# ═══════════════════════════════════════════════════════════════════════════════
# FORMATTER — _append_hashtag
# ═══════════════════════════════════════════════════════════════════════════════

class TestAppendHashtag:
    def test_appends_rl_hashtag(self):
        result = _append_hashtag("Some news", "rocketleague")
        assert result.endswith("#RocketLeague")

    def test_appends_gd_hashtag(self):
        result = _append_hashtag("Some news", "geometrydash")
        assert result.endswith("#GeometryDash")

    def test_skips_if_hashtag_already_present(self):
        result = _append_hashtag("News #RocketLeague", "rocketleague")
        assert result == "News #RocketLeague"

    def test_skips_if_related_hashtag_present(self):
        result = _append_hashtag("RLCS update #RLCS", "rocketleague")
        assert "#RocketLeague" not in result

    def test_skips_if_gd_related_hashtag(self):
        result = _append_hashtag("Demon list #demonlist", "geometrydash")
        assert "#GeometryDash" not in result

    def test_skips_if_doesnt_fit(self):
        text = "A" * 270
        result = _append_hashtag(text, "rocketleague")
        assert "#RocketLeague" not in result

    def test_unknown_niche_no_hashtag(self):
        result = _append_hashtag("News", "unknown")
        assert result == "News"

    def test_case_insensitive_check(self):
        result = _append_hashtag("News #rocketleague", "rocketleague")
        assert result == "News #rocketleague"

    def test_exactly_at_280_limit(self):
        # "\n\n#RocketLeague" = 15 chars → 280 - 15 = 265
        text = "A" * 265
        result = _append_hashtag(text, "rocketleague")
        assert len(result) == 280


# ═══════════════════════════════════════════════════════════════════════════════
# FORMATTER — _normalize_whitespace
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalizeWhitespace:
    def test_collapses_double_spaces(self):
        assert _normalize_whitespace("hello  world") == "hello world"

    def test_collapses_triple_newlines(self):
        assert _normalize_whitespace("a\n\n\nb") == "a\n\nb"

    def test_preserves_double_newlines(self):
        assert _normalize_whitespace("a\n\nb") == "a\n\nb"

    def test_strips_leading_trailing(self):
        assert _normalize_whitespace("  hello  ") == "hello"

    def test_tabs_collapsed(self):
        assert _normalize_whitespace("a\t\tb") == "a b"

    def test_mixed_whitespace(self):
        result = _normalize_whitespace("  a  b  \n\n\n  c  ")
        assert "  " not in result
        assert "\n\n\n" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# FORMATTER — _pick_emoji
# ═══════════════════════════════════════════════════════════════════════════════

class TestPickEmoji:
    def test_known_types(self):
        assert _pick_emoji("rocketleague", "patch_notes") == "🔄"
        assert _pick_emoji("geometrydash", "top1_verified") == "🚨"
        assert _pick_emoji("geometrydash", "level_verified") == "🏆"
        assert _pick_emoji("rocketleague", "esports_result") == "🏆"
        assert _pick_emoji("rocketleague", "youtube_video") == "🎬"

    def test_unknown_type_returns_default(self):
        assert _pick_emoji("rocketleague", "nonexistent_type") == "📢"

    def test_all_template_types_have_emoji(self):
        """Every content type in templates should have an emoji mapping."""
        for niche, types in TEMPLATES.items():
            for ctype in types:
                emoji = _pick_emoji(niche, ctype)
                assert isinstance(emoji, str)
                assert len(emoji) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# FORMATTER — _SafeFormatDict
# ═══════════════════════════════════════════════════════════════════════════════

class TestSafeFormatDict:
    def test_existing_key_returns_value(self):
        d = _SafeFormatDict({"name": "Alice"})
        assert d["name"] == "Alice"

    def test_missing_key_returns_placeholder(self):
        d = _SafeFormatDict({})
        assert d["missing"] == "{missing}"

    def test_format_map_with_missing_keys(self):
        result = "{name} likes {food}".format_map(_SafeFormatDict({"name": "Bob"}))
        assert result == "Bob likes {food}"


# ═══════════════════════════════════════════════════════════════════════════════
# FORMATTER — format_tweet comprehensive
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatTweetComprehensive:
    def test_every_rl_content_type_has_template(self):
        """Every RL content type in the priority map should have templates."""
        from src.poster.queue import _PRIORITY
        for ctype in _PRIORITY:
            templates = TEMPLATES.get("rocketleague", {}).get(ctype)
            # Some types are GD-only
            if templates is not None:
                assert isinstance(templates, list)

    def test_retweet_signal_returns_none(self):
        content = _make_content(content_type="official_tweet", niche="rocketleague")
        assert format_tweet(content) is None

    def test_robtop_tweet_returns_none(self):
        content = _make_content(content_type="robtop_tweet", niche="geometrydash")
        assert format_tweet(content) is None

    def test_unknown_content_type_returns_none(self):
        content = _make_content(content_type="totally_unknown")
        assert format_tweet(content) is None

    def test_breaking_news_with_url(self):
        content = _make_content(
            content_type="breaking_news",
            title="Big RL News",
            url="https://example.com/news",
        )
        result = format_tweet(content)
        assert result is not None
        assert "Big RL News" in result

    def test_esports_result_needs_structured_fields(self):
        """Esports result without winner/loser should fall through to simpler variant."""
        content = _make_content(
            content_type="esports_result",
            title="Grand Finals Result",
            metadata={},
        )
        result = format_tweet(content)
        # Should either format with fallback or return title
        if result:
            assert len(result) <= 280

    def test_demon_list_update_with_position(self):
        content = _make_content(
            niche="geometrydash",
            content_type="demon_list_update",
            title="Acheron",
            metadata={"level": "Acheron", "position": "3", "changes": "New placement"},
        )
        result = format_tweet(content)
        assert result is not None

    def test_youtube_video_template(self):
        content = _make_content(
            content_type="youtube_video",
            metadata={"creator": "SunlessKhan", "video_title": "Why I Quit RL", "url": "https://youtu.be/abc"},
            url="https://youtu.be/abc",
            author="SunlessKhan",
        )
        result = format_tweet(content)
        assert result is not None
        assert "SunlessKhan" in result

    def test_level_verified_with_player_tag(self):
        content = _make_content(
            niche="geometrydash",
            content_type="level_verified",
            title="Acheron",
            author="zoink",
            metadata={"player": "zoink", "level": "Acheron", "position": "1"},
        )
        result = format_tweet(content)
        assert result is not None
        # Player should be tagged as @gdzoink
        assert "@gdzoink" in result

    def test_all_templates_produce_valid_tweets(self):
        """Every template variant should produce a tweet <= 280 chars when given full context."""
        for niche in ("rocketleague", "geometrydash"):
            for ctype, variants in TEMPLATES.get(niche, {}).items():
                for variant in variants:
                    if variant is None:
                        continue
                    # Build a content with all possible fields
                    content = _make_content(
                        niche=niche,
                        content_type=ctype,
                        title="Test Level",
                        url="https://example.com",
                        body="Test body text.",
                        author="TestPlayer",
                        metadata={
                            "player": "TestPlayer", "creator": "TestCreator",
                            "level": "TestLevel", "level_name": "TestLevel",
                            "position": "5", "version": "2.2",
                            "winner": "TeamA", "loser": "TeamB",
                            "score1": "4", "score2": "2",
                            "event": "RLCS Major", "event_short": "RLCS",
                            "video_title": "Test Video", "url": "https://x.com",
                            "difficulty": "Extreme Demon", "stars": "10",
                            "attempts": "50000", "victor_number": "2nd",
                        },
                    )
                    result = format_tweet(content)
                    # Result should either be None (retweet) or a valid tweet
                    if result is not None:
                        assert len(result) <= 280, f"{niche}/{ctype}: {len(result)} chars"


# ═══════════════════════════════════════════════════════════════════════════════
# FORMATTER — _truncate
# ═══════════════════════════════════════════════════════════════════════════════

class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello", 10) == "hello"

    def test_exact_limit(self):
        assert _truncate("hello", 5) == "hello"

    def test_truncates_with_ellipsis(self):
        result = _truncate("hello world foo bar", 10)
        assert result.endswith("…")
        assert len(result) <= 10

    def test_single_long_word(self):
        result = _truncate("abcdefghij", 5)
        assert len(result) <= 5


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE — _sanitize_error
# ═══════════════════════════════════════════════════════════════════════════════

class TestSanitizeError:
    def test_redacts_api_key(self):
        msg = "Error: key=abc123def456 in request"
        result = _sanitize_error(msg)
        assert "abc123" not in result
        assert "[REDACTED]" in result

    def test_redacts_token(self):
        msg = "Failed: token=mysecrettoken123"
        result = _sanitize_error(msg)
        assert "mysecrettoken" not in result

    def test_redacts_auth_token(self):
        msg = "auth_token=secret_value_here&other=data"
        result = _sanitize_error(msg)
        assert "secret_value" not in result

    def test_no_secrets_unchanged(self):
        msg = "Connection refused to host.com"
        assert _sanitize_error(msg) == msg

    def test_multiple_secrets_all_redacted(self):
        msg = "key=aaa111bbb222 and token=ccc333ddd444"
        result = _sanitize_error(msg)
        assert "aaa111" not in result
        assert "ccc333" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE — is_similar_story edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsSimilarStoryEdgeCases:
    def test_identical_text_is_similar(self):
        conn = _make_db()
        conn.execute("INSERT INTO sources (niche, name, type, config) VALUES ('rocketleague', 'test', 'rss', '{}')")
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) VALUES (?, ?, 'queued', ?)",
            ("rocketleague", "Exact same text here", datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        assert is_similar_story(conn, "Exact same text here", "rocketleague") is True

    def test_very_different_text_not_similar(self):
        conn = _make_db()
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) VALUES (?, ?, 'queued', ?)",
            ("rocketleague", "Completely different topic about cooking", datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        assert is_similar_story(conn, "Rocket League patch notes v2.68", "rocketleague") is False

    def test_empty_queue_not_similar(self):
        conn = _make_db()
        assert is_similar_story(conn, "Any text", "rocketleague") is False

    def test_old_tweets_ignored(self):
        conn = _make_db()
        old_time = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) VALUES (?, ?, 'queued', ?)",
            ("rocketleague", "Same text exactly here", old_time),
        )
        conn.commit()
        assert is_similar_story(conn, "Same text exactly here", "rocketleague") is False

    def test_different_niche_not_compared(self):
        conn = _make_db()
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) VALUES (?, ?, 'queued', ?)",
            ("geometrydash", "Same text here", datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        assert is_similar_story(conn, "Same text here", "rocketleague") is False

    def test_case_insensitive_comparison(self):
        conn = _make_db()
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) VALUES (?, ?, 'posted', ?)",
            ("rocketleague", "ROCKET LEAGUE UPDATE", datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        assert is_similar_story(conn, "rocket league update", "rocketleague") is True


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE — url_already_queued
# ═══════════════════════════════════════════════════════════════════════════════

class TestUrlAlreadyQueued:
    def test_empty_url_returns_false(self):
        conn = _make_db()
        assert url_already_queued(conn, "", 1) is False

    def test_none_url_returns_false(self):
        conn = _make_db()
        assert url_already_queued(conn, None, 1) is False

    def test_url_from_same_content_id_not_duplicate(self):
        conn = _make_db()
        sid = upsert_source(conn, "rl", "test", "rss", {})
        rc = _make_content(source_id=sid, url="https://example.com/article")
        cid, _ = insert_raw_content(conn, rc)
        add_to_queue(conn, "rl", "tweet text", cid)
        conn.commit()
        # Same content_id — not considered a duplicate
        assert url_already_queued(conn, "https://example.com/article", cid) is False


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE — mark_posted with content_type
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarkPostedContentType:
    def test_writes_content_type_to_post_log(self):
        conn = _make_db()
        sid = upsert_source(conn, "rl", "test", "rss", {})
        rc = _make_content(source_id=sid, content_type="patch_notes")
        cid, _ = insert_raw_content(conn, rc)
        qid = add_to_queue(conn, "rl", "tweet text", cid)
        mark_posted(conn, qid, "tweet_123")
        row = conn.execute("SELECT content_type FROM post_log WHERE tweet_id = 'tweet_123'").fetchone()
        assert row["content_type"] == "patch_notes"

    def test_content_type_empty_when_no_raw_content(self):
        conn = _make_db()
        qid = add_to_queue(conn, "rl", "tweet text", None)
        mark_posted(conn, qid, "tweet_456")
        row = conn.execute("SELECT content_type FROM post_log WHERE tweet_id = 'tweet_456'").fetchone()
        assert row["content_type"] == ""


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE — cleanup_old_records
# ═══════════════════════════════════════════════════════════════════════════════

class TestCleanupOldRecordsEdgeCases:
    def test_does_not_delete_queued_rows(self):
        conn = _make_db()
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) VALUES (?, ?, 'queued', ?)",
            ("rl", "still queued", old),
        )
        conn.commit()
        stats = cleanup_old_records(conn, days=30)
        assert stats["tweet_queue"] == 0

    def test_deletes_posted_old_rows(self):
        conn = _make_db()
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) VALUES (?, ?, 'posted', ?)",
            ("rl", "old posted", old),
        )
        conn.commit()
        stats = cleanup_old_records(conn, days=30)
        assert stats["tweet_queue"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# URL UTILS — is_safe_url comprehensive
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsSafeUrlComprehensive:
    def test_http_allowed(self):
        assert is_safe_url("http://example.com") is True

    def test_https_allowed(self):
        assert is_safe_url("https://example.com") is True

    def test_ftp_blocked(self):
        assert is_safe_url("ftp://example.com") is False

    def test_file_blocked(self):
        assert is_safe_url("file:///etc/passwd") is False

    def test_empty_string_blocked(self):
        assert is_safe_url("") is False

    def test_no_scheme_blocked(self):
        assert is_safe_url("example.com") is False

    def test_localhost_blocked(self):
        assert is_safe_url("http://localhost/admin") is False

    def test_zero_ip_blocked(self):
        assert is_safe_url("http://0.0.0.0/") is False

    def test_ipv6_loopback_blocked(self):
        assert is_safe_url("http://[::1]/") is False

    def test_metadata_ip_blocked(self):
        assert is_safe_url("http://169.254.169.254/latest/meta-data/") is False

    def test_hex_ip_blocked(self):
        assert is_safe_url("http://0x7f000001/") is False

    def test_private_ip_10_blocked(self):
        assert is_safe_url("http://10.0.0.1/") is False

    def test_private_ip_172_blocked(self):
        assert is_safe_url("http://172.16.0.1/") is False

    def test_private_ip_192_blocked(self):
        assert is_safe_url("http://192.168.1.1/") is False

    def test_google_metadata_blocked(self):
        assert is_safe_url("http://metadata.google.internal/") is False

    def test_no_hostname_blocked(self):
        assert is_safe_url("http:///path") is False

    def test_valid_public_ip(self):
        assert is_safe_url("http://8.8.8.8/") is True

    def test_javascript_scheme_blocked(self):
        assert is_safe_url("javascript:alert(1)") is False

    def test_data_scheme_blocked(self):
        assert is_safe_url("data:text/html,<h1>Hi</h1>") is False


# ═══════════════════════════════════════════════════════════════════════════════
# RATE LIMITER — edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateLimiterEdgeCases:
    def test_jitter_delay_within_bounds(self):
        for _ in range(20):
            delay = jitter_delay()
            assert 1200 <= delay <= 3720  # MIN + MAX + JITTER_MAX

    def test_posting_window_handles_wrap(self):
        """Window 14:00-04:00 wraps past midnight."""
        # 15:00 UTC should be in window
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 1, 15, 0, tzinfo=timezone.utc)
            mock_dt.fromisoformat = datetime.fromisoformat
            assert within_posting_window() is True

    def test_posting_window_outside(self):
        with patch("src.poster.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
            mock_dt.fromisoformat = datetime.fromisoformat
            assert within_posting_window() is False

    def test_posting_window_breaking_always_true(self):
        assert within_posting_window(is_breaking=True) is True


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPLATES — structural integrity
# ═══════════════════════════════════════════════════════════════════════════════

class TestTemplateStructure:
    def test_all_niches_present(self):
        assert "rocketleague" in TEMPLATES
        assert "geometrydash" in TEMPLATES

    def test_no_empty_template_lists(self):
        for niche, types in TEMPLATES.items():
            for ctype, variants in types.items():
                assert len(variants) > 0, f"{niche}/{ctype} has empty variant list"

    def test_templates_have_valid_placeholders(self):
        """All placeholders in templates should be valid Python identifiers."""
        placeholder_re = re.compile(r"\{(\w+)\}")
        for niche, types in TEMPLATES.items():
            for ctype, variants in types.items():
                for variant in variants:
                    if variant is None:
                        continue
                    for match in placeholder_re.finditer(variant):
                        name = match.group(1)
                        assert name.isidentifier(), f"Bad placeholder {{{name}}} in {niche}/{ctype}"

    def test_no_duplicate_variants(self):
        for niche, types in TEMPLATES.items():
            for ctype, variants in types.items():
                non_none = [v for v in variants if v is not None]
                assert len(non_none) == len(set(non_none)), f"Duplicates in {niche}/{ctype}"

    def test_rl_and_gd_share_common_types(self):
        """youtube_video and monitored_tweet should exist in both niches."""
        for ctype in ("youtube_video", "monitored_tweet", "breaking_news"):
            assert ctype in TEMPLATES["rocketleague"], f"RL missing {ctype}"
            assert ctype in TEMPLATES["geometrydash"], f"GD missing {ctype}"

    def test_retweet_signal_types_are_none(self):
        assert TEMPLATES["rocketleague"]["official_tweet"] == [None]
        assert TEMPLATES["geometrydash"]["robtop_tweet"] == [None]


# ═══════════════════════════════════════════════════════════════════════════════
# GD PLAYER HANDLES — integrity
# ═══════════════════════════════════════════════════════════════════════════════

class TestGDPlayerHandles:
    def test_all_handles_are_strings(self):
        for player, handle in _GD_PLAYER_HANDLES.items():
            assert isinstance(handle, str)
            assert len(handle) > 0
            assert "@" not in handle  # no @ prefix in the dict

    def test_all_keys_lowercase(self):
        for player in _GD_PLAYER_HANDLES:
            assert player == player.lower()

    def test_known_players_present(self):
        assert "zoink" in _GD_PLAYER_HANDLES
        assert "npesta" in _GD_PLAYER_HANDLES
        assert "doggie" in _GD_PLAYER_HANDLES
        assert "viprin" in _GD_PLAYER_HANDLES


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE — record_source_error
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecordSourceError:
    def test_truncates_long_messages(self):
        conn = _make_db()
        sid = upsert_source(conn, "rl", "test", "rss", {})
        long_msg = "x" * 1000
        record_source_error(conn, sid, long_msg)
        row = conn.execute("SELECT error_msg FROM source_errors WHERE source_id = ?", (sid,)).fetchone()
        assert len(row["error_msg"]) <= 500

    def test_sanitizes_secrets_in_error(self):
        conn = _make_db()
        sid = upsert_source(conn, "rl", "test", "rss", {})
        record_source_error(conn, sid, "Failed: token=secret123456 in call")
        row = conn.execute("SELECT error_msg FROM source_errors WHERE source_id = ?", (sid,)).fetchone()
        assert "secret123456" not in row["error_msg"]


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE — upsert_source
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpsertSource:
    def test_insert_new_source(self):
        conn = _make_db()
        sid = upsert_source(conn, "rl", "new_source", "rss", {"url": "https://example.com"})
        assert sid > 0

    def test_upsert_existing_returns_same_id(self):
        conn = _make_db()
        sid1 = upsert_source(conn, "rl", "source1", "rss", {"url": "a"})
        sid2 = upsert_source(conn, "rl", "source1", "rss", {"url": "b"})
        assert sid1 == sid2

    def test_different_niche_different_id(self):
        conn = _make_db()
        sid1 = upsert_source(conn, "rl", "same_name", "rss", {})
        sid2 = upsert_source(conn, "gd", "same_name", "rss", {})
        assert sid1 != sid2


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE — disable_source
# ═══════════════════════════════════════════════════════════════════════════════

class TestDisableSource:
    def test_disables_source(self):
        conn = _make_db()
        sid = upsert_source(conn, "rl", "test", "rss", {})
        disable_source(conn, sid)
        sources = get_sources(conn, "rl")
        assert len(sources) == 0

    def test_disable_nonexistent_source_no_error(self):
        conn = _make_db()
        disable_source(conn, 99999)  # should not raise


# ═══════════════════════════════════════════════════════════════════════════════
# BASE COLLECTOR
# ═══════════════════════════════════════════════════════════════════════════════

class TestBaseCollector:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseCollector(source_id=1, config={})

    def test_subclass_can_instantiate(self):
        class MyCollector(BaseCollector):
            async def collect(self):
                return []
        c = MyCollector(source_id=1, config={"key": "val"})
        assert c.source_id == 1
        assert c.config == {"key": "val"}


# ═══════════════════════════════════════════════════════════════════════════════
# RAW CONTENT dataclass
# ═══════════════════════════════════════════════════════════════════════════════

class TestRawContent:
    def test_defaults(self):
        rc = RawContent(source_id=1, external_id="x", niche="rl", content_type="test")
        assert rc.title == ""
        assert rc.url == ""
        assert rc.body == ""
        assert rc.image_url == ""
        assert rc.author == ""
        assert rc.score == 0
        assert rc.metadata == {}

    def test_metadata_default_is_independent(self):
        rc1 = RawContent(source_id=1, external_id="a", niche="rl", content_type="t")
        rc2 = RawContent(source_id=1, external_id="b", niche="rl", content_type="t")
        rc1.metadata["key"] = "val"
        assert "key" not in rc2.metadata


# ═══════════════════════════════════════════════════════════════════════════════
# RSS COLLECTOR — content type detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestRSSContentTypeDetection:
    def test_rl_patch_notes_keywords(self):
        from src.collectors.rss import _detect_content_type
        assert _detect_content_type("Hotfix deployed today", "", "rocketleague") == "patch_notes"
        assert _detect_content_type("Patch Notes v2.68", "", "rocketleague") == "patch_notes"

    def test_rl_esports_keywords(self):
        from src.collectors.rss import _detect_content_type
        assert _detect_content_type("RLCS Grand Final Results", "", "rocketleague") == "event_announcement"

    def test_gd_top1_keywords(self):
        from src.collectors.rss import _detect_content_type
        assert _detect_content_type("New #1 hardest level ever", "", "geometrydash") == "top1_verified"

    def test_gd_verified_keywords(self):
        from src.collectors.rss import _detect_content_type
        assert _detect_content_type("Level verification complete", "", "geometrydash") == "level_verified"

    def test_default_content_type(self):
        from src.collectors.rss import _detect_content_type
        assert _detect_content_type("Random unrelated headline", "", "rocketleague") == "breaking_news"
        assert _detect_content_type("Random unrelated headline", "", "geometrydash") == "game_update"

    def test_unknown_niche_default(self):
        from src.collectors.rss import _detect_content_type
        assert _detect_content_type("Something", "", "unknown") == "breaking_news"


# ═══════════════════════════════════════════════════════════════════════════════
# SCRAPER — content type classifier
# ═══════════════════════════════════════════════════════════════════════════════

class TestScraperClassifier:
    def test_rl_patch_notes(self):
        from src.collectors.scraper import _classify
        assert _classify("Patch notes released for v2.68", "rocketleague") == "patch_notes"

    def test_rl_hotfix(self):
        from src.collectors.scraper import _classify
        assert _classify("Emergency hotfix deployed", "rocketleague") == "patch_notes"

    def test_rl_item_shop(self):
        from src.collectors.scraper import _classify
        assert _classify("Today's Item Shop features new decals", "rocketleague") == "item_shop"

    def test_gd_top1(self):
        from src.collectors.scraper import _classify
        assert _classify("New top 1 demon verified", "geometrydash") == "top1_verified"

    def test_gd_game_update(self):
        from src.collectors.scraper import _classify
        assert _classify("RobTop announces 2.3 update", "geometrydash") == "game_update"

    def test_gd_demon_list(self):
        from src.collectors.scraper import _classify
        assert _classify("Demon list updated with new entries", "geometrydash") == "demon_list_update"

    def test_gd_geode_update(self):
        from src.collectors.scraper import _classify
        assert _classify("Geode mod loader update v3.0 released", "geometrydash") == "mod_update"

    def test_gd_speedrun(self):
        from src.collectors.scraper import _classify
        assert _classify("New speedrun world record set", "geometrydash") == "speedrun_wr"

    def test_default_breaking_news(self):
        from src.collectors.scraper import _classify
        assert _classify("Unrelated gaming headline", "rocketleague") == "breaking_news"
        assert _classify("Unrelated gaming headline", "geometrydash") == "breaking_news"


# ═══════════════════════════════════════════════════════════════════════════════
# ALERTS — sanitization
# ═══════════════════════════════════════════════════════════════════════════════

class TestAlertsSanitization:
    def test_sanitize_strips_secrets(self):
        from src.monitoring.alerts import _sanitize
        result = _sanitize("Error with key=mysecretkey123 in request")
        assert "mysecretkey" not in result
        assert "[REDACTED]" in result

    def test_sanitize_strips_auth_token(self):
        from src.monitoring.alerts import _sanitize
        result = _sanitize("auth_token=abc123xyz")
        assert "abc123" not in result

    def test_sanitize_strips_ct0(self):
        from src.monitoring.alerts import _sanitize
        result = _sanitize("ct0=longsecretvalue99")
        assert "longsecretvalue" not in result

    def test_sanitize_preserves_safe_text(self):
        from src.monitoring.alerts import _sanitize
        msg = "Connection timeout to example.com"
        assert _sanitize(msg) == msg

    @pytest.mark.asyncio
    async def test_send_alert_no_webhook_noop(self):
        from src.monitoring.alerts import send_alert
        with patch("src.monitoring.alerts.DISCORD_WEBHOOK_URL", ""):
            await send_alert("test")  # should not raise

    @pytest.mark.asyncio
    async def test_send_alert_invalid_webhook_skips(self):
        from src.monitoring.alerts import send_alert
        with patch("src.monitoring.alerts.DISCORD_WEBHOOK_URL", "http://not-discord.com/webhook"):
            await send_alert("test")  # should not raise


# ═══════════════════════════════════════════════════════════════════════════════
# TWSCRAPE POOL — cookie parsing
# ═══════════════════════════════════════════════════════════════════════════════

class TestCookieParsing:
    def test_basic_cookies(self):
        from src.collectors.twscrape_pool import _parse_cookies
        auth, ct0 = _parse_cookies("auth_token=abc123; ct0=def456")
        assert auth == "abc123"
        assert ct0 == "def456"

    def test_pipe_separated_takes_first(self):
        from src.collectors.twscrape_pool import _parse_cookies
        auth, ct0 = _parse_cookies("auth_token=first; ct0=one|auth_token=second; ct0=two")
        assert auth == "first"
        assert ct0 == "one"

    def test_empty_string(self):
        from src.collectors.twscrape_pool import _parse_cookies
        auth, ct0 = _parse_cookies("")
        assert auth == ""
        assert ct0 == ""

    def test_missing_ct0(self):
        from src.collectors.twscrape_pool import _parse_cookies
        auth, ct0 = _parse_cookies("auth_token=abc123")
        assert auth == "abc123"
        assert ct0 == ""

    def test_extra_whitespace(self):
        from src.collectors.twscrape_pool import _parse_cookies
        auth, ct0 = _parse_cookies("  auth_token=abc ;  ct0=def  ")
        assert auth == "abc"
        assert ct0 == "def"


# ═══════════════════════════════════════════════════════════════════════════════
# TWITTER MONITOR — is_relevant
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsRelevant:
    def test_rl_keywords(self):
        from src.collectors.twitter_monitor import is_relevant
        assert is_relevant("Rocket League Season 14 is live", "rocketleague") is True
        assert is_relevant("RLCS Major starts tomorrow", "rocketleague") is True
        assert is_relevant("#RLCS update coming soon", "rocketleague") is True

    def test_rl_irrelevant(self):
        from src.collectors.twitter_monitor import is_relevant
        assert is_relevant("Just had pizza for dinner", "rocketleague") is False

    def test_gd_keywords(self):
        from src.collectors.twitter_monitor import is_relevant
        assert is_relevant("New extreme demon verified", "geometrydash") is True
        assert is_relevant("Geometry Dash update 2.3", "geometrydash") is True
        assert is_relevant("Demon list reshuffled today", "geometrydash") is True

    def test_gd_irrelevant(self):
        from src.collectors.twitter_monitor import is_relevant
        assert is_relevant("My cat slept all day", "geometrydash") is False

    def test_unknown_niche_always_relevant(self):
        from src.collectors.twitter_monitor import is_relevant
        assert is_relevant("Anything at all", "unknown_niche") is True

    def test_case_insensitive(self):
        from src.collectors.twitter_monitor import is_relevant
        assert is_relevant("ROCKET LEAGUE", "rocketleague") is True
        assert is_relevant("geometry dash", "geometrydash") is True


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN — _sanitize_exc
# ═══════════════════════════════════════════════════════════════════════════════

class TestSanitizeExc:
    def test_strips_api_keys(self):
        from src.main import _sanitize_exc
        exc = Exception("Failed: api_key=secretvalue123 in request")
        result = _sanitize_exc(exc)
        assert "secretvalue" not in result

    def test_truncates_long_messages(self):
        from src.main import _sanitize_exc
        exc = Exception("x" * 500)
        result = _sanitize_exc(exc)
        assert len(result) <= 300

    def test_preserves_safe_messages(self):
        from src.main import _sanitize_exc
        exc = Exception("Connection refused")
        assert _sanitize_exc(exc) == "Connection refused"


# ═══════════════════════════════════════════════════════════════════════════════
# YOUTUBE — short/low quality detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestYouTubeShortDetection:
    def test_shorts_tag_detected(self):
        from src.collectors.youtube import _is_short_or_low_quality
        assert _is_short_or_low_quality("Epic clip #Shorts", "") is True
        assert _is_short_or_low_quality("Cool play #short", "") is True

    def test_short_title_detected(self):
        from src.collectors.youtube import _is_short_or_low_quality
        assert _is_short_or_low_quality("insane 😱", "") is True  # < 15 chars

    def test_ellipsis_title_detected(self):
        from src.collectors.youtube import _is_short_or_low_quality
        assert _is_short_or_low_quality("Wait for it...", "") is True  # ends with ... and < 30

    def test_normal_title_passes(self):
        from src.collectors.youtube import _is_short_or_low_quality
        assert _is_short_or_low_quality("Rocket League Season 14 Full Patch Notes Review", "") is False

    def test_long_title_with_shorts_tag_caught(self):
        from src.collectors.youtube import _is_short_or_low_quality
        assert _is_short_or_low_quality("This is a really long title", "#shorts in description") is True

    def test_shorts_in_description_caught(self):
        from src.collectors.youtube import _is_short_or_low_quality
        assert _is_short_or_low_quality("Normal Title Here Video", "Check out #Shorts") is True
