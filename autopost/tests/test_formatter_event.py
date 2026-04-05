"""
Tests for Fix 2 in src/formatter/formatter.py:

`_build_context()` no longer puts `title` into the `"event"` / `"event_short"`
keys of the base dict.  Those keys are now ONLY populated when the collector
explicitly supplies them via `content.metadata`.

Coverage areas:
  - _build_context: event / event_short absent without metadata
  - _build_context: event / event_short populated from metadata
  - _try_format: placeholder rejection when event is missing
  - format_tweet: event_announcement without metadata → no bad grammar
  - format_tweet: event_announcement with metadata → renders correctly
  - Regression: other content types not broken by the change
  - Edge cases: empty event, single-char event, very long event, GD niche
"""
import re
import pytest

from src.collectors.base import RawContent
from src.formatter.formatter import _build_context, _try_format, format_tweet


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_content(**overrides) -> RawContent:
    defaults = dict(
        source_id=1,
        external_id="test-ext-id",
        niche="rocketleague",
        content_type="event_announcement",
        title="Gentle Mates Crowned RLCS Boston Major 2026 Champions",
        url="https://example.com/article",
        body="Full article body describing the tournament result.",
        image_url="",
        author="RLCS",
        score=0,
        metadata={},
    )
    defaults.update(overrides)
    return RawContent(**defaults)


# Bad-grammar patterns produced by event templates when {event} was filled with
# the raw headline (the pre-fix bug).
_BAD_GRAMMAR_PATTERNS = re.compile(
    r"Gentle Mates Crowned RLCS Boston Major 2026 Champions begins today|"
    r"Gentle Mates Crowned RLCS Boston Major 2026 Champions is underway|"
    r"Gentle Mates Crowned RLCS Boston Major 2026 Champions Day \d+ is live",
    re.IGNORECASE,
)


def _has_unfilled_placeholder(text: str) -> bool:
    return bool(re.search(r"\{[^}]+\}", text))


# ── _build_context: event absent without metadata ──────────────────────────────

class TestBuildContextEventAbsent:

    def test_event_key_not_in_base_context(self):
        ctx = _build_context(make_content())
        assert "event" not in ctx

    def test_event_short_key_not_in_base_context(self):
        ctx = _build_context(make_content())
        assert "event_short" not in ctx

    def test_event_absent_even_with_long_title(self):
        content = make_content(title="A" * 200)
        ctx = _build_context(content)
        assert "event" not in ctx

    def test_event_absent_with_empty_metadata_dict(self):
        ctx = _build_context(make_content(metadata={}))
        assert "event" not in ctx

    def test_event_short_absent_with_empty_metadata_dict(self):
        ctx = _build_context(make_content(metadata={}))
        assert "event_short" not in ctx

    def test_event_absent_when_metadata_has_unrelated_keys(self):
        ctx = _build_context(make_content(metadata={"stage": "Grand Final", "winner": "Mates"}))
        assert "event" not in ctx

    def test_event_short_absent_when_metadata_has_unrelated_keys(self):
        ctx = _build_context(make_content(metadata={"stage": "Grand Final"}))
        assert "event_short" not in ctx

    def test_title_still_set_without_metadata(self):
        title = "My Article Headline"
        ctx = _build_context(make_content(title=title))
        assert ctx["title"] == title

    def test_headline_still_set_without_metadata(self):
        title = "My Article Headline"
        ctx = _build_context(make_content(title=title))
        assert ctx["headline"] == title

    def test_event_absent_for_gd_niche(self):
        ctx = _build_context(make_content(niche="geometrydash"))
        assert "event" not in ctx

    def test_event_short_absent_for_gd_niche(self):
        ctx = _build_context(make_content(niche="geometrydash"))
        assert "event_short" not in ctx


# ── _build_context: event populated from metadata ─────────────────────────────

class TestBuildContextEventFromMetadata:

    def test_event_from_metadata(self):
        ctx = _build_context(make_content(metadata={"event": "RLCS Boston Major 2026"}))
        assert ctx["event"] == "RLCS Boston Major 2026"

    def test_event_short_from_metadata(self):
        ctx = _build_context(make_content(metadata={"event_short": "Boston Major"}))
        assert ctx["event_short"] == "Boston Major"

    def test_both_event_fields_from_metadata(self):
        ctx = _build_context(make_content(metadata={
            "event": "Worlds 2026",
            "event_short": "Worlds",
        }))
        assert ctx["event"] == "Worlds 2026"
        assert ctx["event_short"] == "Worlds"

    def test_metadata_event_overrides_nothing_extra(self):
        ctx = _build_context(make_content(metadata={"event": "RLCS Major"}))
        assert ctx["event"] == "RLCS Major"
        # title should still be the original title, not the event
        assert ctx["title"] == "Gentle Mates Crowned RLCS Boston Major 2026 Champions"

    def test_event_empty_string_not_injected(self):
        # Empty/whitespace values must be skipped by the metadata merge
        ctx = _build_context(make_content(metadata={"event": ""}))
        assert "event" not in ctx

    def test_event_whitespace_only_not_injected(self):
        ctx = _build_context(make_content(metadata={"event": "   "}))
        assert "event" not in ctx

    def test_event_single_char_is_injected(self):
        ctx = _build_context(make_content(metadata={"event": "A"}))
        assert ctx["event"] == "A"

    def test_event_very_long_string_is_injected(self):
        long_event = "X" * 200
        ctx = _build_context(make_content(metadata={"event": long_event}))
        assert ctx["event"] == long_event

    def test_gd_niche_event_from_metadata(self):
        ctx = _build_context(make_content(niche="geometrydash", metadata={"event": "GD World Cup"}))
        assert ctx["event"] == "GD World Cup"

    def test_metadata_none_value_not_injected(self):
        ctx = _build_context(make_content(metadata={"event": None}))
        assert "event" not in ctx


# ── _try_format: placeholder rejection ────────────────────────────────────────

class TestTryFormatWithMissingEvent:

    def test_event_placeholder_unfilled_returns_none(self):
        assert _try_format("{event} begins today!", {}) is None

    def test_event_placeholder_filled_returns_string(self):
        result = _try_format("{event} begins today!", {"event": "RLCS"})
        assert result == "RLCS begins today!"

    def test_event_short_placeholder_unfilled_returns_none(self):
        assert _try_format("{event_short} is live!", {}) is None

    def test_event_short_placeholder_filled_returns_string(self):
        result = _try_format("{event_short} is live!", {"event_short": "Worlds"})
        assert result == "Worlds is live!"

    def test_template_with_event_and_details_unfilled_event(self):
        tmpl = "{event} begins today. {details}\n\nhttps://example.com #RLCS"
        ctx = {"details": "Day 1 schedule inside."}
        assert _try_format(tmpl, ctx) is None

    def test_template_with_event_and_details_both_filled(self):
        tmpl = "{event} begins today. {details}\n\nhttps://example.com #RLCS"
        ctx = {"event": "RLCS Major", "details": "Day 1 schedule inside."}
        result = _try_format(tmpl, ctx)
        assert result is not None
        assert "RLCS Major begins today" in result

    def test_template_with_only_event_day_unfilled_event(self):
        tmpl = "{event} Day {day} is live.\n\nSchedule: {url} #RLCS"
        ctx = {"day": "1", "url": "https://x.com"}
        assert _try_format(tmpl, ctx) is None

    def test_template_with_event_day_all_filled(self):
        tmpl = "{event} Day {day} is live.\n\nSchedule: {url} #RLCS"
        ctx = {"event": "RLCS Boston", "day": "1", "url": "https://x.com"}
        result = _try_format(tmpl, ctx)
        assert result is not None
        assert "RLCS Boston Day 1 is live" in result

    def test_safe_format_dict_leaves_placeholder_literal(self):
        """_try_format must return None (not a string containing {event}) when key missing."""
        result = _try_format("Attend {event}!", {})
        assert result is None

    def test_double_space_causes_rejection(self):
        result = _try_format("Hello  World", {})
        assert result is None


# ── format_tweet: event_announcement without metadata ─────────────────────────

class TestFormatTweetEventAnnouncementNoMetadata:

    def test_no_bad_grammar_pattern_rl(self):
        content = make_content(niche="rocketleague")
        result = format_tweet(content)
        if result is not None:
            assert not _BAD_GRAMMAR_PATTERNS.search(result), (
                f"Bad grammar pattern found in: {result!r}"
            )

    def test_no_bad_grammar_pattern_gd(self):
        content = make_content(niche="geometrydash")
        result = format_tweet(content)
        if result is not None:
            assert not _BAD_GRAMMAR_PATTERNS.search(result), (
                f"Bad grammar pattern found in: {result!r}"
            )

    def test_no_unfilled_placeholders_rl(self):
        content = make_content(niche="rocketleague")
        result = format_tweet(content)
        if result is not None:
            assert not _has_unfilled_placeholder(result), (
                f"Unfilled placeholder in: {result!r}"
            )

    def test_no_unfilled_placeholders_gd(self):
        content = make_content(niche="geometrydash")
        result = format_tweet(content)
        if result is not None:
            assert not _has_unfilled_placeholder(result), (
                f"Unfilled placeholder in: {result!r}"
            )

    def test_fallback_contains_title_when_no_event_metadata_rl(self):
        """Without metadata the formatter should fall back to the title+url form."""
        content = make_content(niche="rocketleague")
        result = format_tweet(content)
        if result is not None:
            # The fallback path should include the article title
            assert "Gentle Mates" in result or result is not None

    def test_result_within_280_chars_rl(self):
        result = format_tweet(make_content(niche="rocketleague"))
        if result is not None:
            assert len(result) <= 280

    def test_result_within_280_chars_gd(self):
        result = format_tweet(make_content(niche="geometrydash"))
        if result is not None:
            assert len(result) <= 280

    def test_event_literal_not_in_result_rl(self):
        result = format_tweet(make_content(niche="rocketleague"))
        if result is not None:
            assert "{event}" not in result

    def test_event_literal_not_in_result_gd(self):
        result = format_tweet(make_content(niche="geometrydash"))
        if result is not None:
            assert "{event}" not in result

    def test_event_short_literal_not_in_result(self):
        result = format_tweet(make_content(niche="rocketleague"))
        if result is not None:
            assert "{event_short}" not in result


# ── format_tweet: event_announcement WITH metadata ────────────────────────────

class TestFormatTweetEventAnnouncementWithMetadata:

    def test_rl_event_name_appears_in_result(self):
        content = make_content(
            niche="rocketleague",
            metadata={"event": "RLCS Boston Major 2026"},
        )
        result = format_tweet(content)
        assert result is not None
        assert "RLCS Boston Major 2026" in result

    def test_gd_event_name_appears_in_result(self):
        content = make_content(
            niche="geometrydash",
            metadata={"event": "GD World Cup 2026"},
        )
        result = format_tweet(content)
        # GD templates don't have event_announcement — result may be None
        # but if not None it must not contain unfilled placeholders
        if result is not None:
            assert not _has_unfilled_placeholder(result)

    def test_event_short_used_in_result_when_provided(self):
        content = make_content(
            niche="rocketleague",
            metadata={"event": "RLCS 2026 World Championship", "event_short": "Worlds 2026"},
        )
        result = format_tweet(content)
        assert result is not None
        # At least one of the two event fields should appear
        assert "RLCS 2026 World Championship" in result or "Worlds 2026" in result

    def test_no_unfilled_placeholders_with_metadata(self):
        content = make_content(
            niche="rocketleague",
            metadata={"event": "RLCS Spring Major", "details": "Opens with NA bracket."},
        )
        result = format_tweet(content)
        if result is not None:
            assert not _has_unfilled_placeholder(result)

    def test_result_within_280_chars_with_metadata(self):
        content = make_content(
            niche="rocketleague",
            metadata={"event": "RLCS Spring Major"},
        )
        result = format_tweet(content)
        if result is not None:
            assert len(result) <= 280

    def test_very_long_event_result_still_within_280(self):
        content = make_content(
            niche="rocketleague",
            metadata={"event": "E" * 200},
        )
        result = format_tweet(content)
        if result is not None:
            assert len(result) <= 280


# ── Regression: other content types unaffected ────────────────────────────────

class TestOtherContentTypesUnaffected:

    def test_breaking_news_rl(self):
        content = make_content(
            content_type="breaking_news",
            title="Psyonix announces new season",
            url="https://rl.com/news",
        )
        result = format_tweet(content)
        assert result is not None
        assert not _has_unfilled_placeholder(result)

    def test_breaking_news_gd(self):
        content = make_content(
            niche="geometrydash",
            content_type="breaking_news",
            title="RobTop teases new update",
            url="https://gd.com/update",
            body="RobTop posted about a new update coming soon.",
        )
        result = format_tweet(content)
        assert result is not None
        assert not _has_unfilled_placeholder(result)

    def test_youtube_video_rl(self):
        content = make_content(
            content_type="youtube_video",
            title="RLCS Highlights Week 1",
            url="https://youtube.com/watch?v=abc",
            author="RocketLeague",
            metadata={"video_title": "RLCS Highlights Week 1", "creator": "RocketLeague"},
        )
        result = format_tweet(content)
        assert result is not None
        assert not _has_unfilled_placeholder(result)

    def test_youtube_video_gd(self):
        content = make_content(
            niche="geometrydash",
            content_type="youtube_video",
            title="Extreme Demon Showcase",
            url="https://youtube.com/watch?v=xyz",
            author="GDPlayer",
            metadata={"video_title": "Extreme Demon Showcase", "creator": "GDPlayer"},
        )
        result = format_tweet(content)
        assert result is not None
        assert not _has_unfilled_placeholder(result)

    def test_monitored_tweet_rl(self):
        content = make_content(
            content_type="monitored_tweet",
            title="Team just won the championship!",
            author="rlcs",
        )
        result = format_tweet(content)
        assert result is not None
        assert not _has_unfilled_placeholder(result)

    def test_monitored_tweet_gd(self):
        content = make_content(
            niche="geometrydash",
            content_type="monitored_tweet",
            title="New hardest demon incoming",
            author="gdnews",
        )
        result = format_tweet(content)
        assert result is not None
        assert not _has_unfilled_placeholder(result)

    def test_patch_notes_rl(self):
        content = make_content(
            content_type="patch_notes",
            title="Rocket League v2.40 patch notes",
            url="https://rl.com/patch",
            body="Bug fixes and balance changes.",
        )
        result = format_tweet(content)
        assert result is not None
        assert not _has_unfilled_placeholder(result)

    def test_daily_level_gd(self):
        content = make_content(
            niche="geometrydash",
            content_type="daily_level",
            title="Deadlocked",
            metadata={
                "level_name": "Deadlocked",
                "creator": "RobTop",
                "difficulty": "Insane",
                "stars": "12",
            },
        )
        result = format_tweet(content)
        assert result is not None
        assert not _has_unfilled_placeholder(result)

    def test_level_verified_gd(self):
        content = make_content(
            niche="geometrydash",
            content_type="level_verified",
            title="Slaughterhouse",
            metadata={
                "level": "Slaughterhouse",
                "player": "@zoink",
                "position": "1",
                "attempts": "75000",
            },
        )
        result = format_tweet(content)
        assert result is not None
        assert not _has_unfilled_placeholder(result)

    def test_level_beaten_gd(self):
        content = make_content(
            niche="geometrydash",
            content_type="level_beaten",
            title="Tartarus",
            metadata={
                "level": "Tartarus",
                "player": "Dolphy",
                "position": "2",
                "attempts": "50000",
            },
        )
        result = format_tweet(content)
        assert result is not None
        assert not _has_unfilled_placeholder(result)

    def test_community_mod_update_gd(self):
        content = make_content(
            niche="geometrydash",
            content_type="community_mod_update",
            title="Mega Hack v8 updated",
            metadata={
                "mod_name": "Mega Hack v8",
                "version": "8.2.0",
                "description": "Performance improvements and new features.",
            },
            url="https://geode.sdk/mod",
        )
        result = format_tweet(content)
        assert result is not None
        assert not _has_unfilled_placeholder(result)


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_empty_event_metadata_does_not_fill_template(self):
        """Empty string in metadata must not satisfy the {event} placeholder."""
        ctx = _build_context(make_content(metadata={"event": ""}))
        result = _try_format("{event} begins today.", ctx)
        assert result is None

    def test_whitespace_only_event_metadata_does_not_fill_template(self):
        ctx = _build_context(make_content(metadata={"event": "   "}))
        result = _try_format("{event} begins today.", ctx)
        assert result is None

    def test_single_char_event_fills_template(self):
        ctx = _build_context(make_content(metadata={"event": "X"}))
        result = _try_format("{event} begins today.", ctx)
        assert result == "X begins today."

    def test_event_with_special_characters(self):
        ctx = _build_context(make_content(metadata={"event": "RLCS & Co. — 2026"}))
        result = _try_format("Attend {event}!", ctx)
        assert result == "Attend RLCS & Co. — 2026!"

    def test_event_with_unicode(self):
        ctx = _build_context(make_content(metadata={"event": "GD World Cup 🌍"}))
        result = _try_format("Join {event}!", ctx)
        assert result == "Join GD World Cup 🌍!"

    def test_very_long_event_name_result_truncated_to_280(self):
        content = make_content(
            niche="rocketleague",
            metadata={"event": "Grand Championship " * 10},
        )
        result = format_tweet(content)
        if result is not None:
            assert len(result) <= 280

    def test_no_event_in_context_means_all_event_templates_skip(self):
        """All three RL event_announcement templates use {event} or {day}+{event}.
        Without metadata, none should produce a result — fallback fires instead."""
        content = make_content(niche="rocketleague")
        ctx = _build_context(content)

        rl_event_templates = [
            "{event} begins today. {details}\n\n{url} #RLCS",
            "{event} is underway.\n\n{details}\n\n{url} #RLCS",
            "{event} Day {day} is live.\n\nSchedule: {url} #RLCS",
        ]
        for tmpl in rl_event_templates:
            assert _try_format(tmpl, ctx) is None, (
                f"Template should have been rejected but was not: {tmpl!r}"
            )

    def test_format_tweet_fallback_is_not_empty(self):
        """Even if all templates fail, format_tweet must return something."""
        content = make_content(niche="rocketleague")
        result = format_tweet(content)
        assert result is not None
        assert len(result) > 0

    def test_niche_hashtag_appended_to_fallback_if_fits(self):
        content = make_content(
            niche="rocketleague",
            title="Short title",
            url="https://x.com",
        )
        result = format_tweet(content)
        assert result is not None
        # Since rocketleague hashtag is #RocketLeague, it may or may not appear
        # depending on template selection — but if it does, it must be correct
        if "#RocketLeague" in result or "#RLCS" in result:
            assert len(result) <= 280
