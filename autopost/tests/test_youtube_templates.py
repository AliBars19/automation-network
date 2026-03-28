"""
Tests for YouTube video template formatting — verifying templates are now
active (not [None]) and produce valid tweets for both niches.
"""
import pytest

from src.collectors.base import RawContent
from src.formatter.formatter import format_tweet
from src.formatter.templates import TEMPLATES


class TestYouTubeTemplatesEnabled:

    def test_rl_youtube_template_is_not_none(self):
        """RL youtube_video templates should NOT be [None] anymore."""
        templates = TEMPLATES["rocketleague"]["youtube_video"]
        assert templates != [None]
        assert all(t is not None for t in templates)

    def test_gd_youtube_template_is_not_none(self):
        """GD youtube_video templates should NOT be [None] anymore."""
        templates = TEMPLATES["geometrydash"]["youtube_video"]
        assert templates != [None]
        assert all(t is not None for t in templates)

    def test_rl_youtube_formats_correctly(self):
        content = RawContent(
            source_id=1, external_id="vid001", niche="rocketleague",
            content_type="youtube_video",
            title="INSANE FLIP RESET DOUBLE TAP",
            url="https://youtu.be/abc123",
            body="Watch this crazy clip from ranked",
            author="SunlessKhan", score=0,
            metadata={"creator": "SunlessKhan", "video_title": "INSANE FLIP RESET DOUBLE TAP"},
        )
        result = format_tweet(content)
        assert result is not None
        assert "SunlessKhan" in result
        assert "INSANE FLIP RESET DOUBLE TAP" in result
        assert "youtu.be" in result
        assert len(result) <= 280

    def test_gd_youtube_formats_correctly(self):
        content = RawContent(
            source_id=1, external_id="vid002", niche="geometrydash",
            content_type="youtube_video",
            title="I Beat The HARDEST Level in GD",
            url="https://youtu.be/xyz789",
            body="After 50000 attempts...",
            author="GD Colon", score=0,
            metadata={"creator": "GD Colon", "video_title": "I Beat The HARDEST Level in GD"},
        )
        result = format_tweet(content)
        assert result is not None
        assert "GD Colon" in result
        assert "I Beat The HARDEST Level in GD" in result
        assert len(result) <= 280

    def test_youtube_tweet_within_280_chars_long_title(self):
        content = RawContent(
            source_id=1, external_id="vid003", niche="rocketleague",
            content_type="youtube_video",
            title="A" * 250,
            url="https://youtu.be/longvid",
            body="Description", author="Creator", score=0,
            metadata={"creator": "Creator", "video_title": "A" * 250},
        )
        result = format_tweet(content)
        assert result is not None
        assert len(result) <= 280

    def test_youtube_multiple_variants_exist(self):
        """There should be at least 2 template variants for variety."""
        for niche in ("rocketleague", "geometrydash"):
            templates = TEMPLATES[niche]["youtube_video"]
            assert len(templates) >= 2, f"{niche} needs at least 2 youtube variants"
