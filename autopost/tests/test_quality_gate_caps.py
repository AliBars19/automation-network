"""
Tests for Fixes 4 & 5: updated daily caps in quality_gate.py.

Fix 4: youtube_video cap raised from 4 → 6
Fix 5: monitored_tweet cap raised from 6 → 8

Tests cover:
- Cap constant assertions (new values and all other caps unchanged)
- passes_quality_gate() with in-memory SQLite via _within_daily_cap integration
- Cross-niche isolation (each niche counts independently)
- Cap reset behavior across UTC day boundaries (mocked datetime)
- Uncapped content types always pass
- DB integration: only today's rows, only matching niche, only matching type
- Stress tests: flood inserts confirm gate triggers and releases correctly
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest

from src.poster.quality_gate import (
    _DAILY_CAPS,
    _COMMUNITY_TYPES,
    _ENGAGEMENT_THRESHOLDS,
    _MAX_AGE_HOURS,
    passes_quality_gate,
    _within_daily_cap,
)

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "database" / "schema.sql"


# ── In-memory DB helpers ───────────────────────────────────────────────────────

def _make_db() -> sqlite3.Connection:
    """Create a fresh in-memory SQLite DB with the full schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()
    return conn


def _add_source(conn: sqlite3.Connection, niche: str = "geometrydash") -> int:
    """Insert a minimal source row and return its id."""
    cur = conn.execute(
        "INSERT INTO sources (niche, name, type, config) VALUES (?, ?, 'twitter', '{}')",
        (niche, f"test-source-{niche}-{id(conn)}"),
    )
    conn.commit()
    return cur.lastrowid


def _add_raw_content(
    conn: sqlite3.Connection,
    source_id: int,
    content_type: str,
    niche: str,
    external_id: str = None,
) -> int:
    """Insert a raw_content row and return its id."""
    if external_id is None:
        # Use a unique value based on object id to avoid UNIQUE constraint violations
        external_id = f"ext-{content_type}-{id(object())}-{conn.execute('SELECT COUNT(*) FROM raw_content').fetchone()[0]}"
    cur = conn.execute(
        """INSERT INTO raw_content
               (source_id, external_id, niche, content_type, title, url)
           VALUES (?, ?, ?, ?, 'title', 'https://example.com')""",
        (source_id, external_id, niche, content_type),
    )
    conn.commit()
    return cur.lastrowid


def _add_queue_row(
    conn: sqlite3.Connection,
    niche: str,
    raw_content_id: int,
    status: str = "queued",
    created_at: str = None,
) -> None:
    """Insert a tweet_queue row with a controlled created_at timestamp."""
    if created_at is None:
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """INSERT INTO tweet_queue
               (niche, raw_content_id, tweet_text, status, created_at)
           VALUES (?, ?, 'test tweet', ?, ?)""",
        (niche, raw_content_id, status, created_at),
    )
    conn.commit()


@contextmanager
def _patched_db(conn: sqlite3.Connection) -> Generator:
    """Patch get_db() to yield the provided in-memory connection."""
    @contextmanager
    def _fake_get_db():
        yield conn

    with patch("src.poster.quality_gate.get_db", _fake_get_db):
        yield


# ── 1. Cap constant assertions ─────────────────────────────────────────────────

class TestCapConstants:
    """Fix 4 & 5: assert the new cap values are present in the constants dict."""

    def test_youtube_video_cap_is_six(self):
        """Fix 4: youtube_video raised from 4 → 6."""
        assert _DAILY_CAPS["youtube_video"] == 6

    def test_monitored_tweet_cap_is_eight(self):
        """Fix 5: monitored_tweet raised from 6 → 8."""
        assert _DAILY_CAPS["monitored_tweet"] == 8

    # Other caps must be unchanged

    def test_community_clip_cap_unchanged(self):
        assert _DAILY_CAPS["community_clip"] == 3

    def test_reddit_clip_cap_unchanged(self):
        assert _DAILY_CAPS["reddit_clip"] == 4

    def test_rank_milestone_cap_unchanged(self):
        assert _DAILY_CAPS["rank_milestone"] == 1

    def test_stat_milestone_cap_unchanged(self):
        assert _DAILY_CAPS["stat_milestone"] == 1

    def test_creator_spotlight_cap_unchanged(self):
        assert _DAILY_CAPS["creator_spotlight"] == 2

    def test_viral_moment_cap_unchanged(self):
        assert _DAILY_CAPS["viral_moment"] == 1

    def test_official_tweet_cap_unchanged(self):
        assert _DAILY_CAPS["official_tweet"] == 6

    def test_robtop_tweet_cap_unchanged(self):
        assert _DAILY_CAPS["robtop_tweet"] == 6

    def test_flashback_cap_unchanged(self):
        assert _DAILY_CAPS["flashback"] == 1

    def test_demon_list_update_cap_unchanged(self):
        assert _DAILY_CAPS["demon_list_update"] == 4

    def test_level_verified_cap_unchanged(self):
        assert _DAILY_CAPS["level_verified"] == 4

    def test_level_beaten_cap_unchanged(self):
        assert _DAILY_CAPS["level_beaten"] == 3

    def test_first_victor_cap_unchanged(self):
        assert _DAILY_CAPS["first_victor"] == 2

    def test_community_mod_update_cap_unchanged(self):
        assert _DAILY_CAPS["community_mod_update"] == 4

    def test_all_cap_values_are_positive_integers(self):
        """Every entry in _DAILY_CAPS must be a positive integer."""
        for key, value in _DAILY_CAPS.items():
            assert isinstance(value, int), f"{key} cap is not int: {type(value)}"
            assert value > 0, f"{key} cap is not positive: {value}"

    def test_caps_dict_is_not_empty(self):
        assert len(_DAILY_CAPS) > 0

    def test_caps_dict_contains_expected_keys(self):
        expected_keys = {
            "community_clip", "reddit_clip", "monitored_tweet", "rank_milestone",
            "stat_milestone", "creator_spotlight", "viral_moment", "official_tweet",
            "robtop_tweet", "youtube_video", "flashback", "demon_list_update",
            "level_verified", "level_beaten", "first_victor", "community_mod_update",
        }
        for key in expected_keys:
            assert key in _DAILY_CAPS, f"Expected key '{key}' missing from _DAILY_CAPS"


# ── 2. passes_quality_gate with in-memory DB ───────────────────────────────────

class TestYouTubeVideoCap:
    """Fix 4: youtube_video cap is 6 — test boundary behavior with real DB."""

    def test_youtube_video_at_count_0_passes(self):
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")
        with _patched_db(conn):
            assert passes_quality_gate("youtube_video", "geometrydash") is True

    def test_youtube_video_at_count_5_passes(self):
        """5 posted today — still under cap of 6."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")
        for i in range(5):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"yt-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("youtube_video", "geometrydash") is True

    def test_youtube_video_at_count_6_blocked(self):
        """6 posted today — exactly at cap, must be blocked."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")
        for i in range(6):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"yt-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("youtube_video", "geometrydash") is False

    def test_youtube_video_at_count_7_blocked(self):
        """7 posted today — over cap, must be blocked."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")
        for i in range(7):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"yt-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="queued")
        with _patched_db(conn):
            assert passes_quality_gate("youtube_video", "geometrydash") is False

    def test_youtube_video_at_count_4_passes_with_new_cap(self):
        """4 posted — was blocked under old cap of 4, now passes under cap of 6."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")
        for i in range(4):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"yt-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("youtube_video", "geometrydash") is True

    def test_youtube_video_at_count_3_passes(self):
        """3 posted — well under cap."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")
        for i in range(3):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"yt-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("youtube_video", "geometrydash") is True


class TestMonitoredTweetCap:
    """Fix 5: monitored_tweet cap is 8 — test boundary behavior with real DB."""

    def test_monitored_tweet_at_count_0_passes(self):
        conn = _make_db()
        source_id = _add_source(conn, "rocketleague")
        with _patched_db(conn):
            assert passes_quality_gate("monitored_tweet", "rocketleague") is True

    def test_monitored_tweet_at_count_7_passes(self):
        """7 posted today — under new cap of 8."""
        conn = _make_db()
        source_id = _add_source(conn, "rocketleague")
        for i in range(7):
            rc_id = _add_raw_content(conn, source_id, "monitored_tweet", "rocketleague", f"tw-{i}")
            _add_queue_row(conn, "rocketleague", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("monitored_tweet", "rocketleague") is True

    def test_monitored_tweet_at_count_8_blocked(self):
        """8 posted today — exactly at cap of 8, must be blocked."""
        conn = _make_db()
        source_id = _add_source(conn, "rocketleague")
        for i in range(8):
            rc_id = _add_raw_content(conn, source_id, "monitored_tweet", "rocketleague", f"tw-{i}")
            _add_queue_row(conn, "rocketleague", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("monitored_tweet", "rocketleague") is False

    def test_monitored_tweet_at_count_9_blocked(self):
        """9 posted — over cap."""
        conn = _make_db()
        source_id = _add_source(conn, "rocketleague")
        for i in range(9):
            rc_id = _add_raw_content(conn, source_id, "monitored_tweet", "rocketleague", f"tw-{i}")
            _add_queue_row(conn, "rocketleague", rc_id, status="queued")
        with _patched_db(conn):
            assert passes_quality_gate("monitored_tweet", "rocketleague") is False

    def test_monitored_tweet_at_count_6_passes_with_new_cap(self):
        """6 posted — was at old cap of 6, now passes under new cap of 8."""
        conn = _make_db()
        source_id = _add_source(conn, "rocketleague")
        for i in range(6):
            rc_id = _add_raw_content(conn, source_id, "monitored_tweet", "rocketleague", f"tw-{i}")
            _add_queue_row(conn, "rocketleague", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("monitored_tweet", "rocketleague") is True

    def test_monitored_tweet_at_count_5_passes(self):
        conn = _make_db()
        source_id = _add_source(conn, "rocketleague")
        for i in range(5):
            rc_id = _add_raw_content(conn, source_id, "monitored_tweet", "rocketleague", f"tw-{i}")
            _add_queue_row(conn, "rocketleague", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("monitored_tweet", "rocketleague") is True


# ── 3. Cross-niche isolation ───────────────────────────────────────────────────

class TestCrossNicheIsolation:
    """Counts for one niche must not affect the other niche."""

    def test_youtube_video_gd_at_cap_does_not_block_rl(self):
        """6 GD youtube_video posted → GD blocked but RL still passes."""
        conn = _make_db()
        gd_source = _add_source(conn, "geometrydash")
        rl_source = _add_source(conn, "rocketleague")

        for i in range(6):
            rc_id = _add_raw_content(conn, gd_source, "youtube_video", "geometrydash", f"gd-yt-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted")

        # Add 3 RL youtube_video rows — well under cap
        for i in range(3):
            rc_id = _add_raw_content(conn, rl_source, "youtube_video", "rocketleague", f"rl-yt-{i}")
            _add_queue_row(conn, "rocketleague", rc_id, status="posted")

        with _patched_db(conn):
            assert passes_quality_gate("youtube_video", "geometrydash") is False
            assert passes_quality_gate("youtube_video", "rocketleague") is True

    def test_monitored_tweet_gd_at_cap_does_not_block_rl(self):
        conn = _make_db()
        gd_source = _add_source(conn, "geometrydash")
        rl_source = _add_source(conn, "rocketleague")

        for i in range(8):
            rc_id = _add_raw_content(conn, gd_source, "monitored_tweet", "geometrydash", f"gd-tw-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted")

        with _patched_db(conn):
            assert passes_quality_gate("monitored_tweet", "geometrydash") is False
            assert passes_quality_gate("monitored_tweet", "rocketleague") is True

    def test_different_content_types_do_not_interfere(self):
        """monitored_tweet count must not affect youtube_video cap."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")

        # Fill monitored_tweet to its cap
        for i in range(8):
            rc_id = _add_raw_content(conn, source_id, "monitored_tweet", "geometrydash", f"tw-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted")

        with _patched_db(conn):
            # monitored_tweet blocked
            assert passes_quality_gate("monitored_tweet", "geometrydash") is False
            # youtube_video has 0 today — must still pass
            assert passes_quality_gate("youtube_video", "geometrydash") is True

    def test_rl_youtube_video_count_zero_while_gd_at_cap(self):
        conn = _make_db()
        gd_source = _add_source(conn, "geometrydash")
        for i in range(6):
            rc_id = _add_raw_content(conn, gd_source, "youtube_video", "geometrydash", f"yt-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("youtube_video", "rocketleague") is True


# ── 4. Cap reset at UTC midnight ───────────────────────────────────────────────

class TestCapReset:
    """Cap counts must reset when the UTC date rolls over."""

    def test_youtube_video_resets_after_midnight(self):
        """Post 6 youtube_video today → blocked. Mock to tomorrow → passes."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for i in range(6):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"yt-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted", created_at=today_str)

        with _patched_db(conn):
            assert passes_quality_gate("youtube_video", "geometrydash") is False

        # Mock datetime to tomorrow UTC
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        tomorrow = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        with patch("src.poster.quality_gate.datetime") as mock_dt:
            mock_dt.now.return_value = tomorrow
            mock_dt.now.side_effect = lambda tz=None: tomorrow
            with _patched_db(conn):
                # The today_start will be tomorrow's midnight, so no rows qualify
                assert _within_daily_cap("geometrydash", "youtube_video", 6) is True

    def test_monitored_tweet_resets_after_midnight(self):
        """Post 8 monitored_tweet today → blocked. Simulate day roll → passes."""
        conn = _make_db()
        source_id = _add_source(conn, "rocketleague")

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for i in range(8):
            rc_id = _add_raw_content(conn, source_id, "monitored_tweet", "rocketleague", f"tw-{i}")
            _add_queue_row(conn, "rocketleague", rc_id, status="posted", created_at=today_str)

        with _patched_db(conn):
            assert passes_quality_gate("monitored_tweet", "rocketleague") is False

        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        tomorrow = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        with patch("src.poster.quality_gate.datetime") as mock_dt:
            mock_dt.now.return_value = tomorrow
            mock_dt.now.side_effect = lambda tz=None: tomorrow
            with _patched_db(conn):
                assert _within_daily_cap("rocketleague", "monitored_tweet", 8) is True

    def test_yesterday_rows_not_counted_in_todays_cap(self):
        """Rows from yesterday must not count toward today's cap."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for i in range(6):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"old-yt-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted", created_at=yesterday)

        with _patched_db(conn):
            # Yesterday's 6 rows must not block today
            assert passes_quality_gate("youtube_video", "geometrydash") is True


# ── 5. Uncapped content types ──────────────────────────────────────────────────

class TestUncappedContentTypes:
    """Content types absent from _DAILY_CAPS must always pass the cap check."""

    def test_patch_notes_not_in_daily_caps(self):
        assert "patch_notes" not in _DAILY_CAPS

    def test_breaking_news_not_in_daily_caps(self):
        assert "breaking_news" not in _DAILY_CAPS

    def test_game_update_not_in_daily_caps(self):
        assert "game_update" not in _DAILY_CAPS

    def test_patch_notes_always_passes_regardless_of_count(self):
        """_within_daily_cap is never called for patch_notes."""
        with patch("src.poster.quality_gate._within_daily_cap") as mock_cap:
            result = passes_quality_gate("patch_notes", "rocketleague")
        mock_cap.assert_not_called()
        assert result is True

    def test_breaking_news_always_passes(self):
        with patch("src.poster.quality_gate._within_daily_cap") as mock_cap:
            result = passes_quality_gate("breaking_news", "rocketleague")
        mock_cap.assert_not_called()
        assert result is True

    def test_game_update_always_passes(self):
        with patch("src.poster.quality_gate._within_daily_cap") as mock_cap:
            result = passes_quality_gate("game_update", "geometrydash")
        mock_cap.assert_not_called()
        assert result is True

    def test_uncapped_type_passes_with_any_score(self):
        """patch_notes passes even when score=0 and age_hours=99."""
        assert passes_quality_gate("patch_notes", "rocketleague", score=0, age_hours=99) is True


# ── 6. DB integration: row filtering ──────────────────────────────────────────

class TestDBIntegration:
    """Verify that _within_daily_cap correctly filters by date, niche, content_type."""

    def test_only_todays_rows_count(self):
        """Rows from other days must not count toward the cap."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")

        # Insert 6 rows from yesterday
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for i in range(6):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"old-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted", created_at=yesterday)

        with _patched_db(conn):
            result = _within_daily_cap("geometrydash", "youtube_video", 6)
        assert result is True  # Yesterday's rows don't count

    def test_only_matching_niche_rows_count(self):
        """RL rows must not inflate the GD count."""
        conn = _make_db()
        rl_source = _add_source(conn, "rocketleague")

        for i in range(6):
            rc_id = _add_raw_content(conn, rl_source, "youtube_video", "rocketleague", f"rl-{i}")
            _add_queue_row(conn, "rocketleague", rc_id, status="posted")

        with _patched_db(conn):
            result = _within_daily_cap("geometrydash", "youtube_video", 6)
        assert result is True

    def test_only_matching_content_type_rows_count(self):
        """monitored_tweet rows must not count toward youtube_video cap."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")

        for i in range(6):
            rc_id = _add_raw_content(conn, source_id, "monitored_tweet", "geometrydash", f"mt-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted")

        with _patched_db(conn):
            result = _within_daily_cap("geometrydash", "youtube_video", 6)
        assert result is True

    def test_queued_and_posted_rows_both_count(self):
        """Status 'queued' and 'posted' both count toward the cap."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")

        # 3 queued + 3 posted = 6 total → at cap
        for i in range(3):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"q-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="queued")
        for i in range(3):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"p-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted")

        with _patched_db(conn):
            result = _within_daily_cap("geometrydash", "youtube_video", 6)
        assert result is False

    def test_failed_rows_do_not_count(self):
        """Status 'failed' must not count toward the cap."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")

        for i in range(6):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"fail-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="failed")

        with _patched_db(conn):
            result = _within_daily_cap("geometrydash", "youtube_video", 6)
        assert result is True  # Failed rows don't count

    def test_skipped_rows_do_not_count(self):
        """Status 'skipped' must not count toward the cap."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")

        for i in range(6):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"skip-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="skipped")

        with _patched_db(conn):
            result = _within_daily_cap("geometrydash", "youtube_video", 6)
        assert result is True

    def test_mixed_statuses_only_active_count(self):
        """3 posted + 3 failed = 3 active. Cap is 6 → should pass."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")

        for i in range(3):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"p-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted")
        for i in range(3):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"f-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="failed")

        with _patched_db(conn):
            result = _within_daily_cap("geometrydash", "youtube_video", 6)
        assert result is True


# ── 7. Stress tests ────────────────────────────────────────────────────────────

class TestStress:
    """High-volume insertion confirms gate triggers and releases at correct boundary."""

    def test_100_youtube_video_rows_blocked(self):
        """Insert 100 youtube_video rows → gate must return False."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")
        for i in range(100):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"yt-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("youtube_video", "geometrydash") is False

    def test_5_youtube_video_passes_6th_also_passes_7th_blocked(self):
        """Insert 5 → pass, insert 6th queued → blocked on 7th."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")
        for i in range(5):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"yt-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("youtube_video", "geometrydash") is True

        rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", "yt-5")
        _add_queue_row(conn, "geometrydash", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("youtube_video", "geometrydash") is False

    def test_100_monitored_tweet_rows_blocked(self):
        conn = _make_db()
        source_id = _add_source(conn, "rocketleague")
        for i in range(100):
            rc_id = _add_raw_content(conn, source_id, "monitored_tweet", "rocketleague", f"tw-{i}")
            _add_queue_row(conn, "rocketleague", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("monitored_tweet", "rocketleague") is False

    def test_7_monitored_tweets_passes_8th_blocked(self):
        """7 posted → pass. Insert 8th → blocked."""
        conn = _make_db()
        source_id = _add_source(conn, "rocketleague")
        for i in range(7):
            rc_id = _add_raw_content(conn, source_id, "monitored_tweet", "rocketleague", f"tw-{i}")
            _add_queue_row(conn, "rocketleague", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("monitored_tweet", "rocketleague") is True

        rc_id = _add_raw_content(conn, source_id, "monitored_tweet", "rocketleague", "tw-7")
        _add_queue_row(conn, "rocketleague", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("monitored_tweet", "rocketleague") is False

    def test_mixed_types_stress_independent_caps(self):
        """Fill youtube_video to cap; monitored_tweet at 0 must still pass."""
        conn = _make_db()
        source_id = _add_source(conn, "geometrydash")
        for i in range(6):
            rc_id = _add_raw_content(conn, source_id, "youtube_video", "geometrydash", f"yt-{i}")
            _add_queue_row(conn, "geometrydash", rc_id, status="posted")
        with _patched_db(conn):
            assert passes_quality_gate("youtube_video", "geometrydash") is False
            assert passes_quality_gate("monitored_tweet", "geometrydash") is True
