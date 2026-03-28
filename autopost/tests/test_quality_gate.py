"""
Unit tests for src/poster/quality_gate.py — engagement thresholds, daily caps,
age filtering, and official content bypass.
"""
from unittest.mock import patch

import pytest

from src.poster.quality_gate import passes_quality_gate, _COMMUNITY_TYPES


class TestOfficialContentBypass:
    def test_patch_notes_always_passes(self):
        assert passes_quality_gate("patch_notes", "rocketleague", score=0) is True

    def test_game_update_always_passes(self):
        assert passes_quality_gate("game_update", "geometrydash", score=0) is True

    def test_top1_verified_always_passes(self):
        assert passes_quality_gate("top1_verified", "geometrydash", score=0) is True

    def test_esports_result_always_passes(self):
        assert passes_quality_gate("esports_result", "rocketleague", score=0) is True

    def test_breaking_news_always_passes(self):
        assert passes_quality_gate("breaking_news", "rocketleague", score=0) is True


class TestEngagementThreshold:
    def test_community_clip_below_threshold_rejected(self):
        assert passes_quality_gate(
            "community_clip", "rocketleague", score=10, source_followers=5000
        ) is False

    def test_community_clip_above_threshold_passes(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate(
                "community_clip", "rocketleague", score=100, source_followers=5000
            ) is True

    def test_large_account_needs_more_engagement(self):
        # 100 likes from a 100K follower account is not enough (threshold=200)
        assert passes_quality_gate(
            "community_clip", "rocketleague", score=100, source_followers=100_000
        ) is False

    def test_large_account_passes_at_200(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate(
                "community_clip", "rocketleague", score=200, source_followers=100_000
            ) is True

    def test_medium_account_threshold(self):
        # 10K-50K followers → threshold 100 (using creator_spotlight as community type)
        assert passes_quality_gate(
            "creator_spotlight", "rocketleague", score=50, source_followers=20_000
        ) is False
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate(
                "creator_spotlight", "rocketleague", score=100, source_followers=20_000
            ) is True


class TestAgeFilter:
    def test_stale_content_rejected(self):
        assert passes_quality_gate(
            "community_clip", "rocketleague", score=500, age_hours=13
        ) is False

    def test_fresh_content_passes(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate(
                "community_clip", "rocketleague", score=500, age_hours=5
            ) is True


class TestDailyCaps:
    def test_rejects_when_cap_reached(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=False):
            assert passes_quality_gate(
                "reddit_clip", "rocketleague", score=1000
            ) is False

    def test_passes_when_under_cap(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate(
                "reddit_clip", "rocketleague", score=1000
            ) is True


class TestCommunityTypes:
    def test_all_community_types_defined(self):
        for ct in ("community_clip", "reddit_clip",
                    "rank_milestone", "stat_milestone", "viral_moment"):
            assert ct in _COMMUNITY_TYPES

    def test_monitored_tweet_bypasses_gate(self):
        """monitored_tweet is NOT in _COMMUNITY_TYPES — always passes."""
        assert "monitored_tweet" not in _COMMUNITY_TYPES
        assert passes_quality_gate("monitored_tweet", "rocketleague", score=0) is True
