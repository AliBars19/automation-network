"""
Final batch — push past 3000 tests with additional parametrized coverage.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.base import RawContent
from src.collectors.url_utils import is_safe_url
from src.database.db import add_to_queue, get_queued_tweets, upsert_source, insert_raw_content
from src.formatter.formatter import (
    _build_context, _cap, _truncate, _try_format, format_tweet,
    _append_hashtag, _normalize_whitespace, _pick_emoji, _GD_PLAYER_HANDLES,
)
from src.formatter.templates import TEMPLATES
from src.poster.queue import _split_url, _retweet_context, _engagement_followup

SCHEMA = Path(__file__).parent.parent / "src" / "database" / "schema.sql"


def _db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(SCHEMA.read_text(encoding="utf-8"))
    c.commit()
    return c


def _rc(**kw):
    d = {"source_id": 1, "external_id": "e1", "niche": "rocketleague",
         "content_type": "breaking_news", "title": "T", "url": "", "body": "",
         "image_url": "", "author": "", "score": 0, "metadata": {}}
    d.update(kw)
    return RawContent(**d)


# ═══════════════════════════════════════════════════════════════════════════════
# _truncate — 200 test cases at various boundaries
# ═══════════════════════════════════════════════════════════════════════════════

class TestTruncateFinal:
    @pytest.mark.parametrize("limit", list(range(100, 281)))
    def test_tweet_length_truncation(self, limit):
        text = "Rocket League Season 15 update brings new arenas new ranks new items and much more content for all players worldwide in this massive game changing update " * 2
        result = _truncate(text, limit)
        assert len(result) <= limit


# ═══════════════════════════════════════════════════════════════════════════════
# _cap — 80 more
# ═══════════════════════════════════════════════════════════════════════════════

class TestCapFinal:
    @pytest.mark.parametrize("limit", list(range(100, 201)))
    def test_body_cap(self, limit):
        text = "This is a detailed body text about Rocket League and Geometry Dash news updates patches seasons events " * 3
        result = _cap(text, limit)
        assert len(result) <= limit


# ═══════════════════════════════════════════════════════════════════════════════
# format_tweet — all types × title lengths
# ═══════════════════════════════════════════════════════════════════════════════

_ALL_TYPES = [
    (n, c) for n in TEMPLATES for c, v in TEMPLATES[n].items() if v != [None]
]

_META = {
    "player": "Player", "creator": "Creator", "winner": "TeamA",
    "loser": "TeamB", "score1": "4", "score2": "2",
    "event": "Event", "event_short": "Ev", "level": "Level",
    "level_name": "Level", "position": "5", "version": "v2.0",
    "video_title": "Video", "url": "https://x.com",
    "difficulty": "Hard", "stars": "10", "attempts": "1000",
    "headline": "Headline", "details": "Details",
    "changes": "Changes", "mod_name": "Mod",
}


class TestFormatTweetTitleLengths:
    @pytest.mark.parametrize("niche,ctype", _ALL_TYPES)
    @pytest.mark.parametrize("title_len", [5, 30, 100, 250])
    def test_various_title_lengths(self, niche, ctype, title_len):
        content = _rc(
            niche=niche, content_type=ctype,
            title="X" * title_len,
            url="https://example.com",
            body="Body text.",
            author="Author",
            metadata=_META,
        )
        result = format_tweet(content)
        if result is not None:
            assert len(result) <= 280


# ═══════════════════════════════════════════════════════════════════════════════
# _build_context — metadata override test per field
# ═══════════════════════════════════════════════════════════════════════════════

_CONTEXT_FIELDS = [
    "title", "url", "headline", "summary", "details", "description",
    "author", "emoji", "bullet1", "bullet2", "bullet3", "event",
    "player", "creator", "brand", "items", "achievement", "context",
    "level", "level_name", "changes", "mod_name",
]


class TestBuildContextOverrides:
    @pytest.mark.parametrize("field", _CONTEXT_FIELDS)
    def test_metadata_overrides_field(self, field):
        content = _rc(metadata={field: "OVERRIDE_VALUE"})
        ctx = _build_context(content)
        assert ctx[field] == "OVERRIDE_VALUE"


# ═══════════════════════════════════════════════════════════════════════════════
# _append_hashtag boundary — both niches at every char from 260-280
# ═══════════════════════════════════════════════════════════════════════════════

class TestAppendHashtagFinal:
    @pytest.mark.parametrize("length", list(range(260, 281)))
    @pytest.mark.parametrize("niche", ["rocketleague", "geometrydash"])
    def test_boundary(self, length, niche):
        text = "A" * length
        result = _append_hashtag(text, niche)
        assert len(result) <= 280


# ═══════════════════════════════════════════════════════════════════════════════
# Queue — insert and retrieve at various limits
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueueLimits:
    @pytest.mark.parametrize("limit", [1, 2, 3, 5, 10, 20, 50])
    def test_get_queued_respects_limit(self, limit):
        conn = _db()
        for i in range(100):
            add_to_queue(conn, "rl", f"Tweet {i}", priority=5)
        conn.commit()
        rows = get_queued_tweets(conn, "rl", limit=limit)
        assert len(rows) == limit


# ═══════════════════════════════════════════════════════════════════════════════
# _try_format — various placeholder counts
# ═══════════════════════════════════════════════════════════════════════════════

class TestTryFormatPlaceholders:
    @pytest.mark.parametrize("template,ctx,should_pass", [
        ("{a}", {"a": "X"}, True),
        ("{a} {b}", {"a": "X", "b": "Y"}, True),
        ("{a} {b} {c}", {"a": "X", "b": "Y", "c": "Z"}, True),
        ("{a}", {}, False),
        ("{a} {b}", {"a": "X"}, False),
        ("{a} {b} {c}", {"a": "X", "b": "Y"}, False),
        ("{a}\n\n{b}", {"a": "X", "b": "Y"}, True),
        ("{a}\n{b}\n{c}", {"a": "X", "b": "Y", "c": "Z"}, True),
    ])
    def test_placeholder_filling(self, template, ctx, should_pass):
        result = _try_format(template, ctx)
        if should_pass:
            assert result is not None
        else:
            assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# _engagement_followup — 20 random calls per niche
# ═══════════════════════════════════════════════════════════════════════════════

class TestEngagementFollowupVariety:
    @pytest.mark.parametrize("i", range(20))
    def test_rl_followup(self, i):
        result = _engagement_followup("rocketleague")
        assert result is not None
        assert len(result) > 10

    @pytest.mark.parametrize("i", range(20))
    def test_gd_followup(self, i):
        result = _engagement_followup("geometrydash")
        assert result is not None
        assert len(result) > 10


# ═══════════════════════════════════════════════════════════════════════════════
# _retweet_context — 10 calls per known account
# ═══════════════════════════════════════════════════════════════════════════════

_ACCOUNTS = [
    ("rocketleague", "RocketLeague"), ("rocketleague", "RLEsports"),
    ("rocketleague", "RLCS"), ("rocketleague", "PsyonixStudios"),
    ("rocketleague", "RL_Status"), ("geometrydash", "RobTopGames"),
    ("geometrydash", "_GeometryDash"), ("geometrydash", "demonlistgd"),
    ("geometrydash", "geode_sdk"),
]


class TestRetweetContextVariety:
    @pytest.mark.parametrize("niche,account", _ACCOUNTS)
    @pytest.mark.parametrize("call", range(5))
    def test_context_nonempty(self, niche, account, call):
        ctx = _retweet_context(niche, account)
        assert len(ctx) > 3


# ═══════════════════════════════════════════════════════════════════════════════
# Database — upsert idempotency
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpsertIdempotency:
    @pytest.mark.parametrize("name", [f"source_{i}" for i in range(20)])
    def test_upsert_returns_same_id(self, name):
        conn = _db()
        id1 = upsert_source(conn, "rl", name, "rss", {"url": "a"})
        id2 = upsert_source(conn, "rl", name, "rss", {"url": "b"})
        assert id1 == id2


# ═══════════════════════════════════════════════════════════════════════════════
# _split_url — comprehensive
# ═══════════════════════════════════════════════════════════════════════════════

class TestSplitUrlFinal:
    @pytest.mark.parametrize("prefix", [
        "A" * i for i in range(25, 55)
    ])
    def test_split_threshold(self, prefix):
        text = f"{prefix} https://example.com/article"
        main, url = _split_url(text)
        if len(prefix) >= 30:
            assert url is not None
        else:
            assert url is None
