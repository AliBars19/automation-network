"""
Unit tests for collectors — Twitter monitor parsing/filtering,
scraper classifier, RSS content-type inference.
"""
import json
import re
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime

import pytest

from src.collectors.scraper import _classify


# ── Twitter monitor: age filter logic ─────────────────────────────────────────

class TestTwitterAgeFilter:
    """Tests for the 7-day tweet age filtering in twitter_monitor.py.
    We test the filtering logic directly rather than through the collector
    (which needs HTTP) to keep tests fast and isolated."""

    @staticmethod
    def _is_within_7_days(created_at: str) -> bool:
        """Replicates the age filter logic from TwitterMonitorCollector.collect()."""
        from email.utils import parsedate_to_datetime
        if not created_at:
            return True  # no date → let it through
        try:
            tweet_time = parsedate_to_datetime(created_at)
            age = datetime.now(timezone.utc) - tweet_time
            return age <= timedelta(days=7)
        except Exception:
            return True  # unparseable → let it through

    def test_recent_tweet_accepted(self):
        """Tweet from 1 hour ago should pass."""
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        created_at = format_datetime(recent)
        assert self._is_within_7_days(created_at) is True

    def test_old_tweet_rejected(self):
        """Tweet from 30 days ago should be filtered out."""
        old = datetime.now(timezone.utc) - timedelta(days=30)
        created_at = format_datetime(old)
        assert self._is_within_7_days(created_at) is False

    def test_just_under_7_days_accepted(self):
        """Tweet from 6 days 23 hours ago should be accepted."""
        boundary = datetime.now(timezone.utc) - timedelta(days=6, hours=23)
        created_at = format_datetime(boundary)
        assert self._is_within_7_days(created_at) is True

    def test_8_days_rejected(self):
        """Tweet from 8 days ago should be filtered out."""
        old = datetime.now(timezone.utc) - timedelta(days=8)
        created_at = format_datetime(old)
        assert self._is_within_7_days(created_at) is False

    def test_empty_date_passes_through(self):
        """Empty created_at should let the tweet through (defensive)."""
        assert self._is_within_7_days("") is True

    def test_garbage_date_passes_through(self):
        """Unparseable date should let the tweet through (defensive)."""
        assert self._is_within_7_days("not-a-date") is True

    def test_twitter_format_date(self):
        """Test with actual Twitter date format: 'Fri Mar 06 17:14:51 +0000 2026'."""
        recent = datetime.now(timezone.utc) - timedelta(hours=3)
        twitter_fmt = recent.strftime("%a %b %d %H:%M:%S %z %Y")
        assert self._is_within_7_days(twitter_fmt) is True


# ── Twitter monitor: __NEXT_DATA__ parsing ────────────────────────────────────

class TestTwitterParsing:
    """Tests for the regex and JSON extraction from syndication HTML."""

    NEXT_DATA_RE = re.compile(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
    )

    def test_extracts_json_from_html(self):
        payload = json.dumps({"props": {"pageProps": {"timeline": {"entries": []}}}})
        html = f'<html><script id="__NEXT_DATA__" type="application/json">{payload}</script></html>'
        m = self.NEXT_DATA_RE.search(html)
        assert m is not None
        data = json.loads(m.group(1))
        assert data["props"]["pageProps"]["timeline"]["entries"] == []

    def test_no_script_tag_returns_none(self):
        html = "<html><body>No script here</body></html>"
        m = self.NEXT_DATA_RE.search(html)
        assert m is None

    def test_handles_nested_json(self):
        payload = json.dumps({
            "props": {"pageProps": {"timeline": {"entries": [
                {"type": "tweet", "content": {"tweet": {
                    "conversation_id_str": "123",
                    "text": "Hello world",
                    "created_at": "Fri Mar 06 17:14:51 +0000 2026",
                    "user": {"screen_name": "TestUser"},
                }}}
            ]}}}
        })
        html = f'<script id="__NEXT_DATA__">{payload}</script>'
        m = self.NEXT_DATA_RE.search(html)
        data = json.loads(m.group(1))
        entries = data["props"]["pageProps"]["timeline"]["entries"]
        assert len(entries) == 1
        assert entries[0]["content"]["tweet"]["text"] == "Hello world"


# ── Twitter monitor: reply/retweet filtering ──────────────────────────────────

class TestTwitterFiltering:
    """Tests for tweet filtering rules (skip replies, skip retweets)."""

    @staticmethod
    def _should_include(tweet: dict) -> bool:
        """Replicates the filtering logic from TwitterMonitorCollector.collect()."""
        if tweet.get("retweeted_tweet"):
            return False
        text = tweet.get("text", "")
        if not text:
            return False
        if text.startswith("@"):
            return False
        tweet_id = tweet.get("conversation_id_str", "")
        if not tweet_id:
            return False
        return True

    def test_normal_tweet_included(self):
        tweet = {
            "conversation_id_str": "123",
            "text": "Rocket League Season 14 starts now!",
        }
        assert self._should_include(tweet) is True

    def test_retweet_excluded(self):
        tweet = {
            "conversation_id_str": "123",
            "text": "RT @someone: Great news",
            "retweeted_tweet": {"id": "456"},
        }
        assert self._should_include(tweet) is False

    def test_reply_excluded(self):
        tweet = {
            "conversation_id_str": "123",
            "text": "@someone Thanks for the update!",
        }
        assert self._should_include(tweet) is False

    def test_empty_text_excluded(self):
        tweet = {"conversation_id_str": "123", "text": ""}
        assert self._should_include(tweet) is False

    def test_no_tweet_id_excluded(self):
        tweet = {"conversation_id_str": "", "text": "Hello world"}
        assert self._should_include(tweet) is False


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
