"""
Bulk parametrized tests — high-volume, low-overhead tests to verify
consistency across all data paths. Targets 1000+ tests from this file alone.
"""
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.collectors.base import RawContent
from src.collectors.url_utils import is_safe_url
from src.database.db import (
    add_to_queue, get_queued_tweets, upsert_source,
    insert_raw_content, is_similar_story,
)
from src.formatter.formatter import (
    _append_hashtag, _build_context, _cap, _normalize_whitespace,
    _pick_emoji, _truncate, _try_format, format_tweet,
    _GD_PLAYER_HANDLES,
)
from src.formatter.templates import TEMPLATES, RL_TEMPLATES, GD_TEMPLATES
from src.poster.queue import _split_url, _retweet_context, _PRIORITY

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


_FULL_META = {
    "player": "Squishy", "creator": "Viprin", "winner": "Team BDS",
    "loser": "G2", "score1": "4", "score2": "2",
    "event": "RLCS Worlds", "event_short": "RLCS",
    "stage": "Grand Final", "team": "BDS", "team1": "BDS", "team2": "G2",
    "old_team": "NRG", "season": "S15", "roster_list": "P1, P2, P3",
    "date": "2026-04-01", "items": "Fennec, Octane", "number": "15",
    "day": "2", "rank": "GC", "level": "Acheron", "level_name": "Acheron",
    "position": "1", "difficulty": "Extreme Demon", "stars": "10",
    "attempts": "150000", "victor_number": "2nd", "version": "v2.68",
    "video_title": "INSANE FLIP", "url": "https://youtu.be/x",
    "mod_name": "Geode v3", "category": "any%", "time": "12:34",
    "prev_time": "12:40", "top1": "A", "top2": "B", "top3": "C",
    "top4": "D", "top5": "E", "old_position": "10", "brand": "Nike",
    "headline": "Big News", "details": "Details here",
    "years_ago": "3", "year": "2023", "highlights": "Arena, Ranked",
    "highlight1": "Arena", "highlight2": "Ranked", "highlight3": "Items",
    "achievement": "SSL",
}


# ═══════════════════════════════════════════════════════════════════════════════
# TRUNCATION — 100 lengths
# ═══════════════════════════════════════════════════════════════════════════════

class TestTruncateBulk:
    @pytest.mark.parametrize("limit", list(range(1, 101)))
    def test_truncate_at_limit(self, limit):
        text = "The quick brown fox jumps over the lazy dog and continues running across the meadow forever and ever"
        result = _truncate(text, limit)
        assert len(result) <= limit


# ═══════════════════════════════════════════════════════════════════════════════
# CAP — 100 lengths
# ═══════════════════════════════════════════════════════════════════════════════

class TestCapBulk:
    @pytest.mark.parametrize("limit", list(range(1, 101)))
    def test_cap_at_limit(self, limit):
        text = "Rocket League Season 15 has arrived with new arenas ranked changes and more content than ever before"
        result = _cap(text, limit)
        assert len(result) <= limit


# ═══════════════════════════════════════════════════════════════════════════════
# is_safe_url — 50 URLs
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsSafeUrlBulk:
    @pytest.mark.parametrize("url,expected", [
        ("https://google.com", True),
        ("https://example.com/path", True),
        ("https://sub.domain.co.uk", True),
        ("http://8.8.8.8", True),
        ("https://cloudflare.com", True),
        ("https://api.github.com", True),
        ("https://youtube.com/watch?v=x", True),
        ("https://x.com/user/status/123", True),
        ("https://reddit.com/r/test", True),
        ("https://discord.com/api/webhooks/123/abc", True),
        ("https://store.steampowered.com", True),
        ("https://gdbrowser.com/api/level/1", True),
        ("https://pointercrate.com/api/v2", True),
        ("https://shiftrle.gg/feed/", True),
        ("https://blast.tv/rl/news", True),
        ("http://zsr.octane.gg/matches", True),
        ("https://www.dexerto.com/rocket-league/", True),
        ("https://www.theloadout.com", True),
        ("https://esports-news.co.uk", True),
        ("https://www.oneesports.gg", True),
        # Blocked
        ("http://localhost", False),
        ("http://127.0.0.1", False),
        ("http://0.0.0.0", False),
        ("http://[::1]", False),
        ("http://10.0.0.1", False),
        ("http://10.255.255.255", False),
        ("http://172.16.0.1", False),
        ("http://172.31.255.255", False),
        ("http://192.168.0.1", False),
        ("http://192.168.255.255", False),
        ("http://169.254.169.254", False),
        ("http://metadata.google.internal", False),
        ("http://0x7f000001", False),
        ("ftp://files.com", False),
        ("file:///etc/passwd", False),
        ("javascript:alert(1)", False),
        ("data:text/html,hi", False),
        ("", False),
        ("not-a-url", False),
        ("://missing-scheme", False),
        ("http://", False),
        ("https://", False),
    ])
    def test_url(self, url, expected):
        assert is_safe_url(url) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# _normalize_whitespace — 30 cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalizeWhitespaceBulk:
    @pytest.mark.parametrize("inp,exp", [
        ("a", "a"), ("", ""), (" ", ""), ("  ", ""),
        ("a b", "a b"), ("a  b", "a b"), ("a   b", "a b"),
        ("a\tb", "a\tb"), ("a\t\tb", "a b"),
        ("a\nb", "a\nb"), ("a\n\nb", "a\n\nb"),
        ("a\n\n\nb", "a\n\nb"), ("a\n\n\n\nb", "a\n\nb"),
        (" a ", "a"), ("\n\na\n\n", "a"),
        ("a  b  c", "a b c"), ("a\t b", "a b"),
        ("hello   world   foo", "hello world foo"),
        ("a\n\n\n\n\n\nb", "a\n\nb"),
        ("mixed  spaces and tabs", "mixed spaces and tabs"),
        ("trailing  ", "trailing"), ("  leading", "leading"),
        ("  both  ", "both"),
        ("\n\n\nstart", "start"), ("end\n\n\n", "end"),
        ("ok", "ok"), ("a b c d e", "a b c d e"),
        ("x" * 100, "x" * 100),
        ("a " * 50, ("a " * 50).strip()),
    ])
    def test_normalize(self, inp, exp):
        assert _normalize_whitespace(inp) == exp


# ═══════════════════════════════════════════════════════════════════════════════
# _pick_emoji — all known types + unknown
# ═══════════════════════════════════════════════════════════════════════════════

_ALL_EMOJI_TYPES = [
    "patch_notes", "season_start", "item_shop", "collab_announcement",
    "event_announcement", "esports_result", "esports_matchup", "roster_change",
    "community_clip", "rank_milestone", "pro_player_content", "top1_verified",
    "level_verified", "level_beaten", "demon_list_update", "game_update",
    "mod_update", "level_rated", "daily_level", "weekly_demon", "youtube_video",
    "creator_spotlight", "speedrun_wr", "breaking_news", "flashback",
    "stat_milestone",
]


class TestPickEmojiBulk:
    @pytest.mark.parametrize("ctype", _ALL_EMOJI_TYPES)
    def test_known_type_returns_emoji(self, ctype):
        emoji = _pick_emoji("any", ctype)
        assert len(emoji) >= 1
        assert emoji != "📢"  # not the default

    @pytest.mark.parametrize("ctype", ["unknown_" + str(i) for i in range(20)])
    def test_unknown_types_return_default(self, ctype):
        assert _pick_emoji("any", ctype) == "📢"


# ═══════════════════════════════════════════════════════════════════════════════
# GD Player handles — all entries
# ═══════════════════════════════════════════════════════════════════════════════

class TestGDPlayerHandlesBulk:
    @pytest.mark.parametrize("player,handle", list(_GD_PLAYER_HANDLES.items()))
    def test_handle_format(self, player, handle):
        assert player == player.lower()
        assert "@" not in handle
        assert len(handle) > 0

    @pytest.mark.parametrize("player,handle", list(_GD_PLAYER_HANDLES.items()))
    def test_tagging_in_context(self, player, handle):
        content = _rc(niche="geometrydash", author=player)
        ctx = _build_context(content)
        assert ctx["player"] == f"@{handle}"


# ═══════════════════════════════════════════════════════════════════════════════
# Priority map — all entries
# ═══════════════════════════════════════════════════════════════════════════════

class TestPriorityBulk:
    @pytest.mark.parametrize("ctype,priority", list(_PRIORITY.items()))
    def test_priority_is_valid(self, ctype, priority):
        assert 1 <= priority <= 8

    @pytest.mark.parametrize("ctype,priority", list(_PRIORITY.items()))
    def test_priority_type(self, ctype, priority):
        assert isinstance(priority, int)
        assert isinstance(ctype, str)


# ═══════════════════════════════════════════════════════════════════════════════
# _retweet_context — all known accounts
# ═══════════════════════════════════════════════════════════════════════════════

_ALL_RT_ACCOUNTS = [
    ("rocketleague", "RocketLeague"),
    ("rocketleague", "RLEsports"),
    ("rocketleague", "RLCS"),
    ("rocketleague", "PsyonixStudios"),
    ("rocketleague", "RL_Status"),
    ("geometrydash", "RobTopGames"),
    ("geometrydash", "_GeometryDash"),
    ("geometrydash", "demonlistgd"),
    ("geometrydash", "geode_sdk"),
]


class TestRetweetContextBulk:
    @pytest.mark.parametrize("niche,account", _ALL_RT_ACCOUNTS)
    def test_known_account_returns_specific_context(self, niche, account):
        ctx = _retweet_context(niche, account)
        assert isinstance(ctx, str)
        assert len(ctx) > 3  # more than just ":"

    @pytest.mark.parametrize("niche", ["rocketleague", "geometrydash"])
    def test_unknown_account_uses_fallback(self, niche):
        ctx = _retweet_context(niche, "TotallyUnknownAccount")
        assert isinstance(ctx, str)
        assert len(ctx) > 0

    @pytest.mark.parametrize("niche,account", _ALL_RT_ACCOUNTS)
    def test_context_contains_at_or_hashtag(self, niche, account):
        ctx = _retweet_context(niche, account)
        assert "@" in ctx or "#" in ctx or any(
            w in ctx.lower() for w in ("rocket", "rlcs", "robtop", "geometry", "demon", "geode", "psyonix", "status", "news", "update")
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Template count verification
# ═══════════════════════════════════════════════════════════════════════════════

class TestTemplateCountsBulk:
    @pytest.mark.parametrize("niche,ctype,variants", [
        (n, c, v) for n in TEMPLATES for c, v in TEMPLATES[n].items()
    ])
    def test_variant_count_positive(self, niche, ctype, variants):
        assert len(variants) >= 1

    @pytest.mark.parametrize("niche,ctype,variants", [
        (n, c, v) for n in TEMPLATES for c, v in TEMPLATES[n].items()
    ])
    def test_variant_types(self, niche, ctype, variants):
        for v in variants:
            assert v is None or isinstance(v, str)


# ═══════════════════════════════════════════════════════════════════════════════
# format_tweet with all RL types × 3 author variations
# ═══════════════════════════════════════════════════════════════════════════════

_RL_POSTABLE = [c for c, v in RL_TEMPLATES.items() if v != [None]]
_GD_POSTABLE = [c for c, v in GD_TEMPLATES.items() if v != [None]]


class TestFormatTweetRLAuthors:
    @pytest.mark.parametrize("ctype", _RL_POSTABLE)
    @pytest.mark.parametrize("author", ["ShiftRLE", "", "A" * 50])
    def test_rl_type_with_author(self, ctype, author):
        content = _rc(
            niche="rocketleague", content_type=ctype,
            title="Test Headline", url="https://example.com",
            body="Body text content here.", author=author,
            metadata=_FULL_META,
        )
        result = format_tweet(content)
        if result is not None:
            assert len(result) <= 280


class TestFormatTweetGDAuthors:
    @pytest.mark.parametrize("ctype", _GD_POSTABLE)
    @pytest.mark.parametrize("author", ["zoink", "Viprin", "", "LongName" * 5])
    def test_gd_type_with_author(self, ctype, author):
        content = _rc(
            niche="geometrydash", content_type=ctype,
            title="Test Level", url="https://gd.com",
            body="Description.", author=author,
            metadata=_FULL_META,
        )
        result = format_tweet(content)
        if result is not None:
            assert len(result) <= 280


# ═══════════════════════════════════════════════════════════════════════════════
# format_tweet with various URL lengths
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatTweetUrlLengths:
    @pytest.mark.parametrize("url_len", [0, 10, 23, 50, 100, 200])
    def test_rl_breaking_news_url_lengths(self, url_len):
        url = f"https://example.com/{'x' * max(0, url_len - 24)}" if url_len else ""
        content = _rc(
            content_type="breaking_news",
            title="Major Rocket League Update",
            url=url,
        )
        result = format_tweet(content)
        assert result is not None
        assert len(result) <= 280

    @pytest.mark.parametrize("url_len", [0, 10, 23, 50, 100, 200])
    def test_gd_breaking_news_url_lengths(self, url_len):
        url = f"https://example.com/{'x' * max(0, url_len - 24)}" if url_len else ""
        content = _rc(
            niche="geometrydash", content_type="breaking_news",
            title="Geometry Dash Major Update",
            url=url,
        )
        result = format_tweet(content)
        assert result is not None
        assert len(result) <= 280


# ═══════════════════════════════════════════════════════════════════════════════
# _split_url with various text lengths
# ═══════════════════════════════════════════════════════════════════════════════

class TestSplitUrlBulk:
    @pytest.mark.parametrize("prefix_len", [0, 10, 20, 29, 30, 31, 50, 100, 200])
    def test_split_by_prefix_length(self, prefix_len):
        prefix = "A" * prefix_len
        text = f"{prefix} https://example.com/article" if prefix_len else "https://example.com/article"
        main, url = _split_url(text)
        if prefix_len >= 30:
            assert url is not None
        else:
            assert url is None  # too short to stand alone


# ═══════════════════════════════════════════════════════════════════════════════
# _append_hashtag with various text lengths near 280
# ═══════════════════════════════════════════════════════════════════════════════

class TestAppendHashtagBulk:
    @pytest.mark.parametrize("text_len", list(range(250, 281)))
    def test_rl_hashtag_at_boundary(self, text_len):
        text = "A" * text_len
        result = _append_hashtag(text, "rocketleague")
        assert len(result) <= 280

    @pytest.mark.parametrize("text_len", list(range(250, 281)))
    def test_gd_hashtag_at_boundary(self, text_len):
        text = "A" * text_len
        result = _append_hashtag(text, "geometrydash")
        assert len(result) <= 280


# ═══════════════════════════════════════════════════════════════════════════════
# Database — queue priorities 1-8
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueuePriorityBulk:
    @pytest.mark.parametrize("priority", list(range(1, 9)))
    def test_priority_stored_correctly(self, priority):
        conn = _db()
        qid = add_to_queue(conn, "rl", f"Tweet at priority {priority}", priority=priority)
        conn.commit()
        rows = get_queued_tweets(conn, "rl")
        assert rows[0]["priority"] == priority

    @pytest.mark.parametrize("priority", list(range(1, 9)))
    def test_higher_priority_served_first(self, priority):
        conn = _db()
        add_to_queue(conn, "rl", "Low priority tweet", priority=8)
        add_to_queue(conn, "rl", f"Priority {priority} tweet", priority=priority)
        conn.commit()
        rows = get_queued_tweets(conn, "rl", limit=1)
        assert rows[0]["priority"] == min(priority, 8)


# ═══════════════════════════════════════════════════════════════════════════════
# RSS content type detection — bulk
# ═══════════════════════════════════════════════════════════════════════════════

class TestRSSDetectionBulk:
    @pytest.mark.parametrize("title,niche,expected", [
        ("Hotfix v1.2", "rocketleague", "patch_notes"),
        ("Patch Notes for today", "rocketleague", "patch_notes"),
        ("Maintenance window", "rocketleague", "patch_notes"),
        ("Season 15 Launch", "rocketleague", "season_start"),
        ("Item shop daily rotation", "rocketleague", "item_shop"),
        ("Nike x RL Crossover", "rocketleague", "collab_announcement"),
        ("RLCS Championship Day 3", "rocketleague", "event_announcement"),
        ("Roster: Team signs player", "rocketleague", "roster_change"),
        ("New #1 hardest level", "geometrydash", "top1_verified"),
        ("New top 1 extreme demon", "geometrydash", "top1_verified"),
        ("Geode mod loader v3", "geometrydash", "mod_update"),
        ("Game update 2.2 patch", "geometrydash", "game_update"),
        ("Level verified by player", "geometrydash", "level_verified"),
        ("Beaten for the first time", "geometrydash", "level_beaten"),
        ("Demon list position changes", "geometrydash", "demon_list_update"),
        ("Star rated level today", "geometrydash", "level_rated"),
        ("Daily level announced", "geometrydash", "daily_level"),
        ("Weekly demon challenge", "geometrydash", "weekly_demon"),
        ("Random unrelated", "rocketleague", "patch_notes"),  # default
        ("Random unrelated", "geometrydash", "game_update"),  # default
    ])
    def test_detection(self, title, niche, expected):
        from src.collectors.rss import _detect_content_type
        assert _detect_content_type(title, "", niche) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# is_relevant — bulk
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsRelevantBulk:
    @pytest.mark.parametrize("text,niche,expected", [
        ("Rocket League update", "rocketleague", True),
        ("RLCS major championship", "rocketleague", True),
        ("Psyonix studios news", "rocketleague", True),
        ("Octane preset review", "rocketleague", True),
        ("Flip reset tutorial guide", "rocketleague", True),
        ("Aerial training pack", "rocketleague", True),
        ("#RocketLeague trending", "rocketleague", True),
        ("#RLCS update today", "rocketleague", True),
        ("Grand Champ celebration", "rocketleague", True),
        ("Item shop daily rotation", "rocketleague", True),
        ("Battle pass season 15", "rocketleague", True),
        ("Cooking recipe blog", "rocketleague", False),
        ("My vacation photos", "rocketleague", False),
        ("Stock market update", "rocketleague", False),
        ("Geometry Dash news", "geometrydash", True),
        ("Demon list reshuffled", "geometrydash", True),
        ("Extreme demon verified", "geometrydash", True),
        ("Pointercrate update", "geometrydash", True),
        ("GD level showcase", "geometrydash", True),
        ("Robtop announces update", "geometrydash", True),
        ("#GeometryDash trending", "geometrydash", True),
        ("Daily level challenge", "geometrydash", True),
        ("Geode mod released", "geometrydash", True),
        ("Random cooking recipe", "geometrydash", False),
        ("Weather forecast today", "geometrydash", False),
        ("Anything goes", "unknown_niche", True),
    ])
    def test_relevance(self, text, niche, expected):
        from src.collectors.twitter_monitor import is_relevant
        assert is_relevant(text, niche) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# Similarity — bulk with parametrized thresholds
# ═══════════════════════════════════════════════════════════════════════════════

class TestSimilarityBulk:
    @pytest.mark.parametrize("text_a,text_b,expected", [
        ("RLCS World Championship Results", "RLCS World Championship Results", True),
        ("RLCS World Championship Results", "RLCS World Championship Final Results", True),
        ("Rocket League update v2.68", "Geometry Dash level verified", False),
        ("RLCS Major starts today", "RLCS Major starts today in EU", True),
        ("Completely different topic A", "Completely different topic B", True),
        ("Short", "An entirely unrelated long sentence about cooking", False),
    ])
    def test_similarity_pairs(self, text_a, text_b, expected):
        conn = _db()
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, status, created_at) VALUES (?, ?, 'queued', ?)",
            ("rl", text_a, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        assert is_similar_story(conn, text_b, "rl") == expected
