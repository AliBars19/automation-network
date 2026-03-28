"""
Unit tests for src/formatter/formatter.py — template formatting, truncation,
emoji selection, and context building.
"""
import pytest

from src.collectors.base import RawContent
from src.formatter.formatter import (
    _cap,
    _pick_emoji,
    _truncate,
    _try_format,
    _SafeFormatDict,
    _build_context,
    format_tweet,
)


# ── _cap() ────────────────────────────────────────────────────────────────────

class TestCap:
    def test_short_text_unchanged(self):
        assert _cap("hello world", 50) == "hello world"

    def test_exact_limit_unchanged(self):
        text = "x" * 100
        assert _cap(text, 100) == text

    def test_truncates_at_word_boundary(self):
        text = "The quick brown fox jumps over the lazy dog"
        result = _cap(text, 20)
        assert len(result) <= 20
        assert result.endswith("…")

    def test_never_exceeds_limit(self):
        """Regression: _cap() previously could exceed limit when no spaces exist."""
        text = "abcdefghijklmnopqrstuvwxyz"
        result = _cap(text, 10)
        assert len(result) <= 10
        assert result.endswith("…")

    def test_strips_trailing_punctuation_before_ellipsis(self):
        text = "Hello, world, this is a long sentence"
        result = _cap(text, 14)
        # Should not end with ",…" — comma should be stripped
        assert ",…" not in result
        assert result.endswith("…")

    def test_empty_string(self):
        assert _cap("", 50) == ""

    def test_single_word_longer_than_limit(self):
        result = _cap("superlongword", 5)
        assert len(result) <= 5
        assert result.endswith("…")


# ── _truncate() ───────────────────────────────────────────────────────────────

class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello", 280) == "hello"

    def test_exact_280_unchanged(self):
        text = "x" * 280
        assert _truncate(text, 280) == text

    def test_long_text_truncated_with_ellipsis(self):
        text = "word " * 100  # 500 chars
        result = _truncate(text, 280)
        assert len(result) <= 280
        assert result.endswith("…")

    def test_truncates_at_word_boundary(self):
        text = "The quick brown fox " * 20
        result = _truncate(text, 50)
        assert len(result) <= 50
        # Should end on a word boundary, not mid-word
        assert result[-1] == "…"


# ── _pick_emoji() ─────────────────────────────────────────────────────────────

class TestPickEmoji:
    def test_known_content_type(self):
        assert _pick_emoji("rocketleague", "patch_notes") == "🔄"
        assert _pick_emoji("geometrydash", "top1_verified") == "🚨"
        assert _pick_emoji("geometrydash", "daily_level") == "📅"

    def test_unknown_content_type_returns_default(self):
        assert _pick_emoji("rocketleague", "nonexistent_type") == "📢"

    def test_youtube_video_emoji(self):
        assert _pick_emoji("geometrydash", "youtube_video") == "🎬"

    def test_breaking_news_emoji(self):
        assert _pick_emoji("rocketleague", "breaking_news") == "🚨"


# ── _SafeFormatDict ──────────────────────────────────────────────────────────

class TestSafeFormatDict:
    def test_existing_key_returned(self):
        d = _SafeFormatDict({"name": "Alice"})
        assert d["name"] == "Alice"

    def test_missing_key_returns_placeholder(self):
        d = _SafeFormatDict({})
        assert d["missing"] == "{missing}"


# ── _try_format() ─────────────────────────────────────────────────────────────

class TestTryFormat:
    def test_all_placeholders_filled(self):
        result = _try_format("{title} by {author}", {"title": "Hello", "author": "Bob"})
        assert result == "Hello by Bob"

    def test_missing_placeholder_returns_none(self):
        result = _try_format("{title} by {author}", {"title": "Hello"})
        assert result is None  # {author} unfilled

    def test_empty_template(self):
        result = _try_format("", {})
        assert result is None  # empty results are rejected


# ── _build_context() ──────────────────────────────────────────────────────────

class TestBuildContext:
    def _make_content(self, **overrides):
        defaults = dict(
            source_id=1, external_id="test", niche="rocketleague",
            content_type="patch_notes", title="Patch v2.40",
            url="https://example.com", body="Bug fixes and improvements",
            image_url="", author="Psyonix", score=0, metadata={},
        )
        defaults.update(overrides)
        return RawContent(**defaults)

    def test_title_in_context(self):
        ctx = _build_context(self._make_content(title="Big Update"))
        assert ctx["title"] == "Big Update"
        assert ctx["headline"] == "Big Update"

    def test_metadata_overrides_defaults(self):
        ctx = _build_context(self._make_content(
            metadata={"winner": "NRG", "loser": "Vitality"}
        ))
        assert ctx["winner"] == "NRG"
        assert ctx["loser"] == "Vitality"

    def test_version_extracted_from_title(self):
        ctx = _build_context(self._make_content(title="Rocket League v2.40 Patch Notes"))
        assert ctx["version"] == "v2.40"

    def test_version_absent_when_not_in_title(self):
        ctx = _build_context(self._make_content(title="Big Update Coming"))
        assert "version" not in ctx  # no version = templates requiring it are skipped

    def test_author_fallback(self):
        ctx = _build_context(self._make_content(author=""))
        assert ctx["author"] == "Unknown"

    def test_emoji_set_by_content_type(self):
        ctx = _build_context(self._make_content(content_type="top1_verified"))
        assert ctx["emoji"] == "🚨"


# ── format_tweet() ────────────────────────────────────────────────────────────

class TestFormatTweet:
    def _make_content(self, **overrides):
        defaults = dict(
            source_id=1, external_id="test", niche="rocketleague",
            content_type="patch_notes", title="Patch v2.40",
            url="https://example.com", body="Bug fixes and improvements",
            image_url="", author="Psyonix", score=0, metadata={},
        )
        defaults.update(overrides)
        return RawContent(**defaults)

    def test_returns_string_for_known_template(self):
        result = format_tweet(self._make_content())
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_none_for_retweet_signal(self):
        """official_tweet template is [None] — returns None for retweet handling."""
        result = format_tweet(self._make_content(content_type="official_tweet"))
        assert result is None

    def test_returns_none_for_unknown_content_type(self):
        result = format_tweet(self._make_content(content_type="totally_unknown_type"))
        assert result is None

    def test_result_within_280_chars(self):
        result = format_tweet(self._make_content(
            title="A" * 200,
            body="B" * 500,
            url="https://example.com/very/long/url",
        ))
        assert result is not None
        assert len(result) <= 280

    def test_gd_template_works(self):
        result = format_tweet(self._make_content(
            niche="geometrydash",
            content_type="top1_verified",
            title="Abyss of Darkness",
            metadata={"level": "Abyss of Darkness", "player": "Zoink"},
        ))
        assert result is not None
        assert len(result) <= 280

    def test_youtube_video_returns_text_rl(self):
        """YouTube video templates are now enabled — should return formatted text."""
        result = format_tweet(self._make_content(content_type="youtube_video"))
        assert result is not None
        assert isinstance(result, str)
        assert len(result) <= 280

    def test_youtube_video_returns_text_gd(self):
        """YouTube video templates are now enabled for GD too."""
        result = format_tweet(self._make_content(
            niche="geometrydash", content_type="youtube_video",
        ))
        assert result is not None
        assert isinstance(result, str)
        assert len(result) <= 280

    def test_pro_player_content_returns_none(self):
        """Legacy pro_player_content is disabled."""
        result = format_tweet(self._make_content(content_type="pro_player_content"))
        assert result is None

    def test_no_emoji_in_rl_patch_notes(self):
        """RL templates should contain zero emoji characters."""
        import re
        result = format_tweet(self._make_content(
            content_type="patch_notes",
            title="Rocket League v2.40 Patch Notes",
            body="Fixed bugs. Improved performance. New arena.",
            url="https://rocketleague.com/news/patch",
            metadata={"version": "2.40"},
        ))
        assert result is not None
        # Match common emoji unicode ranges
        emoji_pattern = re.compile(
            "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
            "\U00002702-\U000027B0\U0001F900-\U0001F9FF"
            "\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
            "\U00002600-\U000026FF]"
        )
        assert not emoji_pattern.search(result), f"Found emoji in: {result}"

    def test_gd_templates_only_niche_hashtag(self):
        """GD templates should only contain #GeometryDash (appended by formatter).
        No other social hashtags like #RLCS should appear."""
        import re
        result = format_tweet(self._make_content(
            niche="geometrydash",
            content_type="top1_verified",
            title="Thinking Space II",
            metadata={"level": "Thinking Space II", "player": "Zoink"},
        ))
        assert result is not None
        assert "#GeometryDash" in result
        # Remove the expected hashtag, then check no others remain
        stripped = result.replace("#GeometryDash", "")
        social_hashtag_re = re.compile(r"#[A-Za-z]")
        assert not social_hashtag_re.search(stripped), f"Found unexpected hashtag in: {result}"

    def test_monitored_tweet_rl_produces_text_not_none(self):
        """monitored_tweet for RL should produce text with title, not return None."""
        result = format_tweet(self._make_content(
            niche="rocketleague",
            content_type="monitored_tweet",
            title="Some player post",
            url="https://x.com/player/status/123",
        ))
        assert result is not None
        assert "Some player post" in result

    def test_monitored_tweet_gd_produces_text_not_none(self):
        """monitored_tweet for GD should produce text with title, not return None."""
        result = format_tweet(self._make_content(
            niche="geometrydash",
            content_type="monitored_tweet",
            title="New GD level incoming",
            url="https://x.com/gdcreator/status/456",
        ))
        assert result is not None
        assert "New GD level incoming" in result

    def test_monitored_tweet_within_280_chars(self):
        """monitored_tweet output must respect the 280 character limit."""
        result = format_tweet(self._make_content(
            niche="rocketleague",
            content_type="monitored_tweet",
            title="X" * 300,
            url="https://x.com/player/status/999",
        ))
        assert result is not None
        assert len(result) <= 280


class TestTryFormatEdgeCases:
    """Tests for uncovered branches inside _try_format."""

    def test_format_map_exception_returns_none(self):
        """If format_map raises (e.g. invalid format spec), _try_format returns None."""
        # A template with a malformed format spec triggers ValueError in format_map
        result = _try_format("{title!q}", {"title": "hello"})
        assert result is None

    def test_double_space_in_result_returns_none(self):
        """A result containing double spaces is rejected (sign of empty placeholder fill)."""
        # Build a template where an empty-string value leaves a double space
        result = _try_format("{a}  {b}", {"a": "hello", "b": "world"})
        assert result is None

    def test_pass2_truncation_path(self):
        """format_tweet falls back to Pass 2 (truncation) when Pass 1 result exceeds 280."""
        # monitored_tweet template: "{title}\\n\\n{url}"
        # Supply a title that makes the result > 280 chars, forcing Pass 2 truncation
        long_title = "A" * 260
        url = "https://x.com/u/status/1"
        content = RawContent(
            source_id=1, external_id="t", niche="rocketleague",
            content_type="monitored_tweet",
            title=long_title,
            url=url,
            body=long_title,
            image_url="", author="u", score=0, metadata={},
        )
        result = format_tweet(content)
        assert result is not None
        assert len(result) <= 280
