"""
Exhaustive template variant tests — verifies every template in both niches
produces valid output with various input combinations.
"""
import pytest
from src.collectors.base import RawContent
from src.formatter.formatter import format_tweet, _build_context, _try_format, _append_hashtag
from src.formatter.templates import TEMPLATES, RL_TEMPLATES, GD_TEMPLATES


def _rc(**kw):
    d = {"source_id": 1, "external_id": "t1", "niche": "rocketleague",
         "content_type": "breaking_news", "title": "Test", "url": "", "body": "",
         "image_url": "", "author": "", "score": 0, "metadata": {}}
    d.update(kw)
    return RawContent(**d)


# Full metadata that satisfies every possible template placeholder
_FULL_META = {
    "player": "Squishy", "creator": "Viprin", "winner": "Team BDS",
    "loser": "G2 Esports", "score1": "4", "score2": "2",
    "event": "RLCS World Championship", "event_short": "RLCS Worlds",
    "stage": "Grand Final", "team": "Team BDS", "team1": "BDS",
    "team2": "G2", "old_team": "NRG", "season": "Season 15",
    "roster_list": "Player1, Player2, Player3", "date": "2026-04-01",
    "items": "Fennec, Octane, Dominus", "number": "15",
    "day": "2", "rank": "Grand Champion",
    "level": "Acheron", "level_name": "Acheron", "position": "1",
    "difficulty": "Extreme Demon", "stars": "10", "attempts": "150000",
    "victor_number": "2nd", "version": "v2.68",
    "video_title": "INSANE FLIP RESET IN RLCS", "url": "https://youtu.be/abc",
    "mod_name": "Geode v3.0", "category": "any%",
    "time": "12:34.56", "prev_time": "12:40.00",
    "top1": "Acheron", "top2": "Tidal Wave", "top3": "Hard Machine",
    "top4": "Abyss of Darkness", "top5": "Sakupen Circles",
    "old_position": "10", "brand": "Nike",
    "headline": "Big News Headline", "details": "Detailed description here",
    "years_ago": "3", "year": "2023",
    "highlights": "New arena, ranked changes", "highlight1": "Arena",
    "highlight2": "Ranked", "highlight3": "Items",
    "achievement": "SSL reached",
}


# ═══════════════════════════════════════════════════════════════════════════════
# Test every RL template variant with full context
# ═══════════════════════════════════════════════════════════════════════════════

class TestRLTemplateVariantsFullContext:
    @pytest.mark.parametrize("ctype", list(RL_TEMPLATES.keys()))
    def test_each_type_with_full_meta(self, ctype):
        variants = RL_TEMPLATES[ctype]
        if variants == [None]:
            return  # retweet signal, skip
        content = _rc(
            niche="rocketleague", content_type=ctype,
            title="Test Headline for RL News",
            url="https://example.com/rl",
            body="Detailed body text with multiple lines.",
            author="TestPlayer",
            metadata=_FULL_META,
        )
        result = format_tweet(content)
        if result is not None:
            assert len(result) <= 280

    @pytest.mark.parametrize("ctype,variant_idx", [
        (ctype, i) for ctype, variants in RL_TEMPLATES.items()
        for i, v in enumerate(variants) if v is not None
    ])
    def test_each_variant_individually(self, ctype, variant_idx):
        variant = RL_TEMPLATES[ctype][variant_idx]
        content = _rc(
            niche="rocketleague", content_type=ctype,
            title="RL Test Title", url="https://rl.com",
            body="Body text.", author="Player",
            metadata=_FULL_META,
        )
        ctx = _build_context(content)
        result = _try_format(variant, ctx)
        # Either fills cleanly or fails gracefully (returns None)
        if result is not None:
            assert len(result) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Test every GD template variant with full context
# ═══════════════════════════════════════════════════════════════════════════════

class TestGDTemplateVariantsFullContext:
    @pytest.mark.parametrize("ctype", list(GD_TEMPLATES.keys()))
    def test_each_type_with_full_meta(self, ctype):
        variants = GD_TEMPLATES[ctype]
        if variants == [None]:
            return
        content = _rc(
            niche="geometrydash", content_type=ctype,
            title="Test GD Level Name",
            url="https://example.com/gd",
            body="Description of the level or update.",
            author="TestCreator",
            metadata=_FULL_META,
        )
        result = format_tweet(content)
        if result is not None:
            assert len(result) <= 280

    @pytest.mark.parametrize("ctype,variant_idx", [
        (ctype, i) for ctype, variants in GD_TEMPLATES.items()
        for i, v in enumerate(variants) if v is not None
    ])
    def test_each_variant_individually(self, ctype, variant_idx):
        variant = GD_TEMPLATES[ctype][variant_idx]
        content = _rc(
            niche="geometrydash", content_type=ctype,
            title="GD Test", url="https://gd.com",
            body="Body.", author="Creator",
            metadata=_FULL_META,
        )
        ctx = _build_context(content)
        result = _try_format(variant, ctx)
        if result is not None:
            assert len(result) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Test templates with MINIMAL context (only title/url)
# ═══════════════════════════════════════════════════════════════════════════════

class TestTemplatesMinimalContext:
    @pytest.mark.parametrize("niche,ctype", [
        (n, c) for n in ("rocketleague", "geometrydash")
        for c in TEMPLATES.get(n, {})
        if TEMPLATES[n][c] != [None]
    ])
    def test_minimal_context_doesnt_crash(self, niche, ctype):
        content = _rc(
            niche=niche, content_type=ctype,
            title="Minimal Title", url="", body="", author="",
        )
        result = format_tweet(content)
        # Should either produce a valid tweet or fall back to title
        if result is not None:
            assert len(result) <= 280
            assert len(result) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Test templates with LONG titles (truncation path)
# ═══════════════════════════════════════════════════════════════════════════════

class TestTemplatesLongTitles:
    @pytest.mark.parametrize("niche,ctype", [
        (n, c) for n in ("rocketleague", "geometrydash")
        for c in TEMPLATES.get(n, {})
        if TEMPLATES[n][c] != [None]
    ])
    def test_long_title_truncated(self, niche, ctype):
        content = _rc(
            niche=niche, content_type=ctype,
            title="A" * 300,
            url="https://example.com",
            body="B" * 300,
            author="LongAuthor",
            metadata=_FULL_META,
        )
        result = format_tweet(content)
        if result is not None:
            assert len(result) <= 280


# ═══════════════════════════════════════════════════════════════════════════════
# Test templates with special characters
# ═══════════════════════════════════════════════════════════════════════════════

class TestTemplatesSpecialChars:
    @pytest.mark.parametrize("title", [
        'Level "with quotes" verified',
        "Level with 'apostrophes' here",
        "Update — dash and em-dash",
        "Results: 4-2 (overtime)",
        "Player1 & Player2 collab",
        "🔥 BREAKING: New #1 demon 🔥",
        "Update v2.68.1-beta3",
        "Team (formerly OtherTeam) wins",
        "100% verified — first victor!",
    ])
    def test_special_chars_in_breaking_news(self, title):
        content = _rc(content_type="breaking_news", title=title)
        result = format_tweet(content)
        assert result is not None
        assert len(result) <= 280


# ═══════════════════════════════════════════════════════════════════════════════
# Test _append_hashtag doesn't double-add
# ═══════════════════════════════════════════════════════════════════════════════

class TestHashtagIdempotent:
    @pytest.mark.parametrize("niche", ["rocketleague", "geometrydash"])
    def test_double_append_doesnt_duplicate(self, niche):
        text = "Some news about the game"
        result1 = _append_hashtag(text, niche)
        result2 = _append_hashtag(result1, niche)
        # Second append should not add another hashtag
        assert result1 == result2


# ═══════════════════════════════════════════════════════════════════════════════
# Test GD player tagging in templates
# ═══════════════════════════════════════════════════════════════════════════════

class TestGDPlayerTagging:
    @pytest.mark.parametrize("player,expected_handle", [
        ("zoink", "@gdzoink"),
        ("trick", "@GmdTrick"),
        ("doggie", "@DasherDoggie"),
        ("npesta", "@zNpesta__"),
        ("viprin", "@vipringd"),
        ("wulzy", "@1wulz"),
        ("colon", "@TheRealGDColon"),
        ("sunix", "@SunixGD"),
    ])
    def test_player_tagged_in_context(self, player, expected_handle):
        content = _rc(
            niche="geometrydash", content_type="level_verified",
            author=player,
            metadata={"level": "TestLevel", "position": "5"},
        )
        ctx = _build_context(content)
        assert ctx["player"] == expected_handle

    @pytest.mark.parametrize("player", [
        "UnknownPlayer", "RandomGuy", "NotInTheList", "",
    ])
    def test_unknown_player_not_tagged(self, player):
        content = _rc(
            niche="geometrydash", content_type="level_verified",
            author=player if player else "SomePlayer",
        )
        ctx = _build_context(content)
        assert not ctx["player"].startswith("@") or player == ""


# ═══════════════════════════════════════════════════════════════════════════════
# Test format_tweet variety (same content shouldn't always produce same result)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatTweetVariety:
    def test_youtube_video_has_multiple_variants(self):
        results = set()
        for _ in range(50):
            content = _rc(
                content_type="youtube_video",
                author="SunlessKhan",
                metadata={
                    "creator": "SunlessKhan",
                    "video_title": "Why I Quit Rocket League",
                    "url": "https://youtu.be/abc123",
                },
                url="https://youtu.be/abc123",
            )
            result = format_tweet(content)
            if result:
                # Strip hashtag for comparison
                results.add(result.split("\n\n#")[0])
        assert len(results) >= 2, f"Only {len(results)} unique variants found"

    def test_monitored_tweet_has_variants(self):
        results = set()
        for _ in range(50):
            content = _rc(
                content_type="monitored_tweet",
                title="RLCS Season 15 starts today with major changes!",
                author="ShiftRLE",
            )
            result = format_tweet(content)
            if result:
                results.add(result.split("\n\n#")[0])
        assert len(results) >= 2

    def test_breaking_news_consistent(self):
        """Breaking news should always include the title."""
        for _ in range(20):
            content = _rc(
                content_type="breaking_news",
                title="Major RLCS Announcement",
                url="https://example.com/news",
            )
            result = format_tweet(content)
            assert result is not None
            assert "Major RLCS Announcement" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Test all content_type → emoji mappings are consistent
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmojiConsistency:
    @pytest.mark.parametrize("niche,ctype", [
        (n, c) for n in TEMPLATES for c in TEMPLATES[n]
    ])
    def test_emoji_in_context(self, niche, ctype):
        from src.formatter.formatter import _pick_emoji
        emoji = _pick_emoji(niche, ctype)
        assert isinstance(emoji, str)
        assert len(emoji) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Test _build_context bullet points
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildContextBullets:
    @pytest.mark.parametrize("body,expected_bullet_count", [
        ("One line", 1),
        ("Line one.\nLine two.", 2),
        ("A.\nB.\nC.", 3),
        ("First. Second. Third.", 3),
        ("", 0),
    ])
    def test_bullet_extraction(self, body, expected_bullet_count):
        content = _rc(body=body, title="Fallback Title")
        ctx = _build_context(content)
        # bullet1 is always present (falls back to title)
        assert "bullet1" in ctx
        if expected_bullet_count >= 2:
            assert ctx["bullet2"] != "See full details"
        if expected_bullet_count >= 3:
            assert ctx["bullet3"] != ""
