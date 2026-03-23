"""
Unit tests for collectors — Twitter monitor filtering,
scraper classifier, RSS content-type inference.
"""
import re
from datetime import datetime, timezone, timedelta

import pytest

from src.collectors.scraper import _classify


# ── Twitter monitor: age filter logic ─────────────────────────────────────────

class TestTwitterAgeFilter:
    """Tests for the 7-day tweet age filtering in twitter_monitor.py.
    We test the filtering logic directly rather than through the collector
    (which needs twscrape) to keep tests fast and isolated.

    The collector receives tweet.date as a datetime from twscrape and compares
    it directly — no string parsing involved."""

    @staticmethod
    def _is_within_7_days(tweet_time: datetime | None) -> bool:
        """Replicates the age filter logic from TwitterMonitorCollector.collect()."""
        if tweet_time is None:
            return True  # no date → let it through
        try:
            dt = tweet_time
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - dt
            return age <= timedelta(days=7)
        except Exception:
            return True  # unparseable → let it through

    def test_recent_tweet_accepted(self):
        """Tweet from 1 hour ago should pass."""
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        assert self._is_within_7_days(recent) is True

    def test_old_tweet_rejected(self):
        """Tweet from 30 days ago should be filtered out."""
        old = datetime.now(timezone.utc) - timedelta(days=30)
        assert self._is_within_7_days(old) is False

    def test_just_under_7_days_accepted(self):
        """Tweet from 6 days 23 hours ago should be accepted."""
        boundary = datetime.now(timezone.utc) - timedelta(days=6, hours=23)
        assert self._is_within_7_days(boundary) is True

    def test_8_days_rejected(self):
        """Tweet from 8 days ago should be filtered out."""
        old = datetime.now(timezone.utc) - timedelta(days=8)
        assert self._is_within_7_days(old) is False

    def test_none_date_passes_through(self):
        """None tweet.date should let the tweet through (defensive)."""
        assert self._is_within_7_days(None) is True

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetime (no tzinfo) should be treated as UTC."""
        recent_naive = datetime.utcnow() - timedelta(hours=2)
        assert self._is_within_7_days(recent_naive) is True


# ── Scraper: _classify() ─────────────────────────────────────────────────────

class TestScraperClassify:
    """Tests for content-type classification by headline keywords."""

    # ── Rocket League ────────────────────────────────────────────────────────

    def test_rl_patch_notes(self):
        assert _classify("Rocket League v2.40 Patch Notes", "rocketleague") == "patch_notes"

    def test_rl_hotfix(self):
        assert _classify("Hotfix deployed for competitive playlist", "rocketleague") == "patch_notes"

    def test_rl_esports_result(self):
        assert _classify(
            "Grand Final: Vitality wins RLCS World Championship",
            "rocketleague",
        ) == "esports_result"

    def test_rl_event_announcement_now_breaking(self):
        # Broad RLCS mentions → breaking_news (scraped articles lack structured data)
        assert _classify("RLCS Spring Major kicks off this weekend", "rocketleague") == "breaking_news"

    def test_rl_roster_change(self):
        assert _classify("jstn joins NRG after leaving Cloud9", "rocketleague") == "roster_change"

    def test_rl_season_start_now_breaking(self):
        # Season mentions → breaking_news (scraper can't provide {number} etc.)
        assert _classify("New season starts today with ranked rewards", "rocketleague") == "breaking_news"

    def test_rl_collab_now_breaking(self):
        # Collab mentions → breaking_news (scraper can't provide structured collab data)
        assert _classify("Rocket League x Hot Wheels collaboration announced", "rocketleague") == "breaking_news"

    def test_rl_item_shop(self):
        assert _classify("Item shop today: new painted decals available", "rocketleague") == "item_shop"

    def test_rl_generic_falls_to_breaking(self):
        assert _classify("Something completely unrelated happened", "rocketleague") == "breaking_news"

    # ── Geometry Dash ────────────────────────────────────────────────────────

    def test_gd_top1(self):
        assert _classify("New top 1 demon verified by Zoink", "geometrydash") == "top1_verified"

    def test_gd_game_update(self):
        assert _classify("Geometry Dash 2.2 update released", "geometrydash") == "game_update"

    def test_gd_level_verified(self):
        assert _classify("Tartarus verified by Dolphy", "geometrydash") == "level_verified"

    def test_gd_level_beaten(self):
        assert _classify("Bloodbath beaten: new victor!", "geometrydash") == "level_beaten"

    def test_gd_demon_list(self):
        assert _classify("Demon list shuffle: extreme demon moves up", "geometrydash") == "demon_list_update"

    def test_gd_rated(self):
        assert _classify("Sonic Wave just got star rated!", "geometrydash") == "level_rated"

    def test_gd_mod(self):
        assert _classify("Geode mod framework v3.0 released", "geometrydash") == "mod_update"

    def test_gd_speedrun(self):
        assert _classify("New world record on Stereo Madness any%", "geometrydash") == "speedrun_wr"

    def test_gd_generic_falls_to_breaking(self):
        assert _classify("Some random GD news article", "geometrydash") == "breaking_news"
