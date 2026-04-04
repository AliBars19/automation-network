"""
Tests for quality improvements in session 6:
  - YouTube off-topic title filter (merch, vlogs, other games)
  - GDBrowser "Unrated" difficulty filter
  - Geode mod description meme signal filter
  - Queue collect_and_queue secondary conversational prefix guard
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.youtube import _GD_OFF_TOPIC_RE, _is_short_or_low_quality
from src.collectors.apis.geode_index import _MEME_NAME_SIGNALS, _MAX_MOD_NAME_LENGTH
from src.collectors.apis.gdbrowser import _DIFFICULTY_STR
from src.poster.queue import _CONV_PREFIX_RE, collect_and_queue
from src.collectors.base import RawContent

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


# ── YouTube off-topic title filter ───────────────────────────────────────────

class TestYouTubeOffTopicFilter:
    """_GD_OFF_TOPIC_RE should catch non-GD content in YouTube titles."""

    def test_blocks_merch_drop(self):
        assert _GD_OFF_TOPIC_RE.search('"VERIFIED BY DOGGIE" New Merch Drop')

    def test_blocks_merch_standalone(self):
        assert _GD_OFF_TOPIC_RE.search("Buy the new merch collection now!")

    def test_blocks_merchandise(self):
        assert _GD_OFF_TOPIC_RE.search("Check out our new merchandise")

    def test_blocks_vlog(self):
        assert _GD_OFF_TOPIC_RE.search("Day In My Life vlog")

    def test_blocks_irl(self):
        assert _GD_OFF_TOPIC_RE.search("IRL Tournament Meetup")

    def test_blocks_minecraft(self):
        assert _GD_OFF_TOPIC_RE.search("Playing Minecraft with friends")

    def test_blocks_fortnite(self):
        assert _GD_OFF_TOPIC_RE.search("Fortnite Zero Build Gameplay")

    def test_blocks_valorant(self):
        assert _GD_OFF_TOPIC_RE.search("Valorant ranked grind ep 5")

    def test_blocks_room_tour(self):
        assert _GD_OFF_TOPIC_RE.search("my gaming room tour 2026")

    def test_allows_gd_content(self):
        assert not _GD_OFF_TOPIC_RE.search("HELIOPOLIS 100% — New Extreme Demon")

    def test_allows_stream_content(self):
        assert not _GD_OFF_TOPIC_RE.search("Silent Clubstep 54% // Stream 53")

    def test_allows_mod_related(self):
        assert not _GD_OFF_TOPIC_RE.search("Geode Mod Showcase — best mods of 2026")

    def test_allows_verified_level(self):
        assert not _GD_OFF_TOPIC_RE.search('"GeometryDash.com is Live!" has been verified')

    def test_case_insensitive(self):
        assert _GD_OFF_TOPIC_RE.search("MERCH STORE IS OPEN")
        assert _GD_OFF_TOPIC_RE.search("New VLOG")

    def test_word_boundary_merch(self):
        # "research" should NOT match merch pattern
        assert not _GD_OFF_TOPIC_RE.search("GD level research showcase")

    def test_word_boundary_irl(self):
        # "twirl" should NOT match irl pattern
        assert not _GD_OFF_TOPIC_RE.search("twirl level editor showcase")


# ── GDBrowser difficulty filter ───────────────────────────────────────────────

class TestGDBrowserDifficultyFilter:
    """Verify that _DIFFICULTY_STR excludes unrecognized values."""

    def test_na_is_in_difficulty_str(self):
        # "N/A" is a valid key so `not in _DIFFICULTY_STR` check covers it...
        # but filter also explicitly checks `difficulty == "N/A"`
        assert "N/A" in _DIFFICULTY_STR

    def test_unrated_not_in_difficulty_str(self):
        assert "Unrated" not in _DIFFICULTY_STR

    def test_unknown_not_in_difficulty_str(self):
        assert "Unknown" not in _DIFFICULTY_STR

    def test_valid_difficulties_present(self):
        valid = [
            "Easy", "Normal", "Hard", "Harder", "Insane",
            "Easy Demon", "Medium Demon", "Hard Demon",
            "Insane Demon", "Extreme Demon",
        ]
        for d in valid:
            assert d in _DIFFICULTY_STR, f"{d} should be in _DIFFICULTY_STR"


# ── Geode meme description filter ─────────────────────────────────────────────

class TestGeodeMemeDescriptionFilter:
    """_MEME_NAME_SIGNALS should catch meme content in descriptions."""

    def test_catches_game_of_the_year_in_desc(self):
        desc = "Click Sounds Mega Neo Full Ultra S26 Game of the Year Edition"
        assert any(sig in desc.lower() for sig in _MEME_NAME_SIGNALS)

    def test_catches_mega_neo(self):
        desc = "Now with Mega Neo support for your click sounds"
        assert any(sig in desc.lower() for sig in _MEME_NAME_SIGNALS)

    def test_catches_deluxe_edition(self):
        desc = "Ultimate Deluxe Edition click sound pack"
        assert any(sig in desc.lower() for sig in _MEME_NAME_SIGNALS)

    def test_catches_ultra_s(self):
        desc = "Ultra S tier click sound quality"
        assert any(sig in desc.lower() for sig in _MEME_NAME_SIGNALS)

    def test_allows_clean_desc(self):
        desc = "Customize your click sounds in Geometry Dash with this mod"
        assert not any(sig in desc.lower() for sig in _MEME_NAME_SIGNALS)

    def test_allows_eclipse_menu_desc(self):
        desc = "A next-generation mod menu for Geometry Dash with many features"
        assert not any(sig in desc.lower() for sig in _MEME_NAME_SIGNALS)


# ── Queue conversational prefix guard ─────────────────────────────────────────

class TestQueueConvPrefixRegex:
    """_CONV_PREFIX_RE should match the same patterns as twitter_monitor."""

    def test_also_dot(self):
        assert _CONV_PREFIX_RE.match("Also. THE CATACOMBS 100%")

    def test_also_comma(self):
        assert _CONV_PREFIX_RE.match("also, I forgot to mention")

    def test_by_the_way(self):
        assert _CONV_PREFIX_RE.match("by the way, the web demo loads GD data")

    def test_btw_comma(self):
        assert _CONV_PREFIX_RE.match("btw, I think this is huge")

    def test_honestly_dot(self):
        assert _CONV_PREFIX_RE.match("honestly. not sure what to think")

    def test_ngl_comma(self):
        assert _CONV_PREFIX_RE.match("ngl, this was easier than expected")

    def test_wait_exclamation(self):
        assert _CONV_PREFIX_RE.match("wait! I just realized something")

    def test_i_just(self):
        assert _CONV_PREFIX_RE.match("I just realized this was harder")

    def test_i_cant(self):
        assert _CONV_PREFIX_RE.match("I can't believe the response to this")

    def test_does_not_match_breaking_news(self):
        assert not _CONV_PREFIX_RE.match("BREAKING: GeometryDash.com is Live")

    def test_does_not_match_level_completion(self):
        assert not _CONV_PREFIX_RE.match("Heliopolis 100% after 47k attempts")

    def test_does_not_match_update_title(self):
        assert not _CONV_PREFIX_RE.match("Geometry Dash 2.209 is out now")

    def test_case_insensitive(self):
        assert _CONV_PREFIX_RE.match("ALSO. check this out")
        assert _CONV_PREFIX_RE.match("BTW, big update incoming")

    def test_update_colon(self):
        assert _CONV_PREFIX_RE.match("update: small patch deployed")


class TestQueueConvPrefixGuard:
    """collect_and_queue skips monitored_tweets that start with conv prefixes."""

    @pytest.mark.asyncio
    async def test_skips_conv_prefix_monitored_tweet(self):
        conn = _make_db()
        conn.execute(
            "INSERT INTO sources (name, type, niche, enabled, config) VALUES "
            "('test', 'twitter', 'geometrydash', 1, '{}')"
        )
        conn.commit()

        item = RawContent(
            source_id=1,
            external_id="test_conv_prefix_001",
            niche="geometrydash",
            content_type="monitored_tweet",
            title="Also. THE CATACOMBS 100% 1.3k attempts",
            url="https://x.com/gdzoink/status/12345",
            body="Also. THE CATACOMBS 100% 1.3k attempts",
            image_url="",
            author="gdzoink",
            score=0,
            metadata={},
        )

        mock_collector = AsyncMock()
        mock_collector.collect.return_value = [item]
        mock_collector.source_id = 1

        with patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)):
            count = await collect_and_queue(mock_collector, "geometrydash")

        assert count == 0

    @pytest.mark.asyncio
    async def test_allows_non_conv_prefix_monitored_tweet(self):
        conn = _make_db()
        conn.execute(
            "INSERT INTO sources (name, type, niche, enabled, config) VALUES "
            "('test', 'twitter', 'geometrydash', 1, '{}')"
        )
        conn.commit()

        item = RawContent(
            source_id=1,
            external_id="test_valid_tweet_002",
            niche="geometrydash",
            content_type="monitored_tweet",
            title="Heliopolis verified after 47k attempts — new Extreme Demon",
            url="https://x.com/dashword/status/999",
            body="Heliopolis verified after 47k attempts — new Extreme Demon",
            image_url="",
            author="DashwordGD",
            score=0,
            metadata={},
        )

        mock_collector = AsyncMock()
        mock_collector.collect.return_value = [item]
        mock_collector.source_id = 1

        with patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)):
            count = await collect_and_queue(mock_collector, "geometrydash")

        assert count == 1
