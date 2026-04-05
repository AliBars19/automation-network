"""
Unit tests for src/poster/quality_gate.py — engagement thresholds, daily caps,
age filtering, and official content bypass.
"""
from unittest.mock import patch

import pytest

from src.poster.quality_gate import (
    passes_quality_gate,
    _COMMUNITY_TYPES,
    _DAILY_CAPS,
    _ENGAGEMENT_THRESHOLDS,
    _MAX_AGE_HOURS,
)


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


# ---------------------------------------------------------------------------
# Daily caps on non-community content types
# ---------------------------------------------------------------------------

class TestDailyCapsNonCommunityContent:
    """Daily caps must be enforced for official content types too."""

    def test_official_tweet_capped_when_daily_limit_reached(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=False):
            assert passes_quality_gate("official_tweet", "rocketleague", score=0) is False

    def test_official_tweet_passes_under_daily_cap(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate("official_tweet", "rocketleague", score=0) is True

    def test_robtop_tweet_capped_when_daily_limit_reached(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=False):
            assert passes_quality_gate("robtop_tweet", "geometrydash", score=0) is False

    def test_robtop_tweet_passes_under_daily_cap(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate("robtop_tweet", "geometrydash", score=0) is True

    def test_youtube_video_capped_when_daily_limit_reached(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=False):
            assert passes_quality_gate("youtube_video", "rocketleague", score=0) is False

    def test_youtube_video_passes_under_daily_cap(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate("youtube_video", "rocketleague", score=0) is True

    def test_flashback_capped_when_daily_limit_reached(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=False):
            assert passes_quality_gate("flashback", "rocketleague", score=0) is False

    def test_flashback_passes_under_daily_cap(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate("flashback", "rocketleague", score=0) is True

    def test_monitored_tweet_capped_when_daily_limit_reached(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=False):
            assert passes_quality_gate("monitored_tweet", "rocketleague", score=0) is False

    def test_content_type_not_in_caps_always_passes(self):
        """Content types with no cap entry bypass the cap check entirely."""
        assert "patch_notes" not in _DAILY_CAPS
        with patch("src.poster.quality_gate._within_daily_cap") as mock_cap:
            result = passes_quality_gate("patch_notes", "rocketleague", score=0)
        mock_cap.assert_not_called()
        assert result is True


class TestDailyCapBoundaryValues:
    """Cap boundary: cap-1 items queued passes; cap items queued fails."""

    def test_official_tweet_cap_is_six(self):
        assert _DAILY_CAPS["official_tweet"] == 6

    def test_robtop_tweet_cap_is_six(self):
        assert _DAILY_CAPS["robtop_tweet"] == 6

    def test_youtube_video_cap_is_six(self):
        assert _DAILY_CAPS["youtube_video"] == 6

    def test_flashback_cap_is_one(self):
        assert _DAILY_CAPS["flashback"] == 1

    def test_community_clip_cap_is_three(self):
        assert _DAILY_CAPS["community_clip"] == 3

    def test_reddit_clip_cap_is_four(self):
        assert _DAILY_CAPS["reddit_clip"] == 4

    def test_rank_milestone_cap_is_one(self):
        assert _DAILY_CAPS["rank_milestone"] == 1

    def test_viral_moment_cap_is_one(self):
        assert _DAILY_CAPS["viral_moment"] == 1


# ---------------------------------------------------------------------------
# Engagement thresholds at each follower tier
# ---------------------------------------------------------------------------

class TestEngagementThresholdTiers:
    """Verify exact threshold values and boundary behaviour for all three tiers."""

    def test_small_threshold_is_25(self):
        assert _ENGAGEMENT_THRESHOLDS["small"] == 25

    def test_medium_threshold_is_100(self):
        assert _ENGAGEMENT_THRESHOLDS["medium"] == 100

    def test_large_threshold_is_200(self):
        assert _ENGAGEMENT_THRESHOLDS["large"] == 200

    # --- small tier (followers < 10K) ---

    def test_small_account_score_24_rejected(self):
        assert passes_quality_gate(
            "community_clip", "rocketleague", score=24, source_followers=9_999
        ) is False

    def test_small_account_score_25_passes(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate(
                "community_clip", "rocketleague", score=25, source_followers=9_999
            ) is True

    def test_small_account_zero_followers_uses_small_tier(self):
        assert passes_quality_gate(
            "community_clip", "rocketleague", score=0, source_followers=0
        ) is False

    # --- medium tier (10K <= followers < 50K) ---

    def test_medium_account_at_lower_boundary_score_99_rejected(self):
        assert passes_quality_gate(
            "community_clip", "rocketleague", score=99, source_followers=10_000
        ) is False

    def test_medium_account_at_lower_boundary_score_100_passes(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate(
                "community_clip", "rocketleague", score=100, source_followers=10_000
            ) is True

    def test_medium_account_at_upper_boundary_49999_followers(self):
        assert passes_quality_gate(
            "community_clip", "rocketleague", score=99, source_followers=49_999
        ) is False

    # --- large tier (followers >= 50K) ---

    def test_large_account_at_boundary_50000_followers_score_199_rejected(self):
        assert passes_quality_gate(
            "community_clip", "rocketleague", score=199, source_followers=50_000
        ) is False

    def test_large_account_at_boundary_50000_followers_score_200_passes(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate(
                "community_clip", "rocketleague", score=200, source_followers=50_000
            ) is True

    def test_large_account_very_high_followers(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate(
                "viral_moment", "rocketleague", score=500, source_followers=1_000_000
            ) is True


# ---------------------------------------------------------------------------
# Stale content edge cases
# ---------------------------------------------------------------------------

class TestStaleContentEdgeCases:
    """Stale check applies only to community content types."""

    def test_max_age_constant_is_12(self):
        assert _MAX_AGE_HOURS == 12

    def test_content_at_exactly_12h_passes(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate(
                "community_clip", "rocketleague", score=500, age_hours=12
            ) is True

    def test_content_at_12h_plus_epsilon_rejected(self):
        assert passes_quality_gate(
            "community_clip", "rocketleague", score=500, age_hours=12.01
        ) is False

    def test_content_at_0h_age_passes(self):
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate(
                "reddit_clip", "rocketleague", score=500, age_hours=0
            ) is True

    def test_stale_official_tweet_still_passes(self):
        """Non-community types are never stale-gated."""
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate(
                "official_tweet", "rocketleague", score=0, age_hours=100
            ) is True

    def test_stale_youtube_video_still_passes(self):
        """youtube_video is not a community type — age is irrelevant."""
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate(
                "youtube_video", "geometrydash", score=0, age_hours=48
            ) is True

    def test_stale_check_applies_to_all_community_types(self):
        """Every community type should be rejected when age > 12h."""
        for ct in _COMMUNITY_TYPES:
            result = passes_quality_gate(ct, "rocketleague", score=9999, age_hours=13)
            assert result is False, f"Expected {ct} to be stale-rejected but it passed"


# ---------------------------------------------------------------------------
# Non-community content bypasses engagement but respects daily caps
# ---------------------------------------------------------------------------

class TestNonCommunityTypesBehavior:
    """Content types outside _COMMUNITY_TYPES skip engagement + age checks."""

    @pytest.mark.parametrize("content_type", [
        "official_tweet",
        "robtop_tweet",
        "youtube_video",
        "flashback",
        "monitored_tweet",
        "patch_notes",
        "game_update",
        "top1_verified",
        "esports_result",
        "breaking_news",
        "first_victor",
    ])
    def test_non_community_type_passes_with_zero_score(self, content_type: str):
        """Non-community types pass with zero engagement score."""
        with patch("src.poster.quality_gate._within_daily_cap", return_value=True):
            assert passes_quality_gate(
                content_type, "rocketleague", score=0, age_hours=100
            ) is True

    @pytest.mark.parametrize("content_type", [
        "official_tweet",
        "robtop_tweet",
        "youtube_video",
        "flashback",
        "monitored_tweet",
    ])
    def test_non_community_type_in_caps_is_blocked_when_capped(self, content_type: str):
        """Non-community types that have a cap entry are still blocked when cap is reached."""
        with patch("src.poster.quality_gate._within_daily_cap", return_value=False):
            assert passes_quality_gate(
                content_type, "rocketleague", score=9999
            ) is False

    def test_community_types_are_not_in_non_community_group(self):
        """Sanity: none of the COMMUNITY_TYPES should bypass engagement."""
        non_community_bypassed = {
            "official_tweet", "robtop_tweet", "youtube_video",
            "flashback", "monitored_tweet",
        }
        for ct in _COMMUNITY_TYPES:
            assert ct not in non_community_bypassed, (
                f"{ct} is in _COMMUNITY_TYPES but would bypass engagement check"
            )
