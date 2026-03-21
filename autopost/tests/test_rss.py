"""
Unit tests for src/collectors/rss.py — RSSCollector and helpers.

All feedparser calls are mocked — no network access.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.base import RawContent
from src.collectors.rss import (
    RSSCollector,
    _detect_content_type,
    _strip_html,
    _unescape,
    _extract_image,
)


# ===========================================================================
# _strip_html
# ===========================================================================

class TestStripHtml:

    def test_removes_single_tag(self):
        assert _strip_html("<b>bold</b>") == "bold"

    def test_removes_nested_tags(self):
        assert _strip_html("<p><strong>Hello</strong> world</p>") == "Hello world"

    def test_collapses_whitespace(self):
        assert _strip_html("too   many    spaces") == "too many spaces"

    def test_empty_string_returns_empty(self):
        assert _strip_html("") == ""

    def test_no_html_passthrough(self):
        assert _strip_html("plain text") == "plain text"

    def test_strips_surrounding_whitespace(self):
        assert _strip_html("  trimmed  ") == "trimmed"

    def test_self_closing_tags_removed(self):
        result = _strip_html("line1<br/>line2")
        assert "<br/>" not in result
        assert "line1" in result
        assert "line2" in result

    def test_script_tags_removed(self):
        result = _strip_html('<script>alert("xss")</script>content')
        assert "script" not in result
        assert "content" in result


# ===========================================================================
# _unescape
# ===========================================================================

class TestUnescape:

    def test_ampersand(self):
        assert _unescape("Rock &amp; Roll") == "Rock & Roll"

    def test_less_than(self):
        assert _unescape("a &lt; b") == "a < b"

    def test_greater_than(self):
        assert _unescape("a &gt; b") == "a > b"

    def test_no_entities_passthrough(self):
        assert _unescape("plain text") == "plain text"

    def test_empty_string(self):
        assert _unescape("") == ""

    def test_quot_entity(self):
        assert _unescape("say &quot;hello&quot;") == 'say "hello"'


# ===========================================================================
# _extract_image
# ===========================================================================

class TestExtractImage:

    def _make_entry(self, media_content=None, media_thumbnail=None, enclosures=None):
        entry = MagicMock()
        entry.get = lambda key, default=None: {
            "media_content":   media_content   or [],
            "media_thumbnail": media_thumbnail or [],
            "enclosures":      enclosures      or [],
        }.get(key, default)
        return entry

    def test_returns_media_content_image_url(self):
        entry = self._make_entry(
            media_content=[{"medium": "image", "url": "https://img.example.com/photo.jpg"}]
        )
        assert _extract_image(entry) == "https://img.example.com/photo.jpg"

    def test_returns_media_content_by_image_type(self):
        entry = self._make_entry(
            media_content=[{"type": "image/jpeg", "url": "https://img.example.com/photo.jpg"}]
        )
        assert _extract_image(entry) == "https://img.example.com/photo.jpg"

    def test_falls_back_to_media_thumbnail(self):
        entry = self._make_entry(
            media_thumbnail=[{"url": "https://thumb.example.com/t.jpg"}]
        )
        assert _extract_image(entry) == "https://thumb.example.com/t.jpg"

    def test_falls_back_to_enclosure(self):
        entry = self._make_entry(
            enclosures=[{"type": "image/png", "href": "https://enc.example.com/pic.png"}]
        )
        assert _extract_image(entry) == "https://enc.example.com/pic.png"

    def test_returns_empty_when_no_media(self):
        entry = self._make_entry()
        assert _extract_image(entry) == ""

    def test_skips_non_image_enclosure(self):
        entry = self._make_entry(
            enclosures=[{"type": "audio/mp3", "href": "https://example.com/audio.mp3"}]
        )
        assert _extract_image(entry) == ""

    def test_skips_media_content_without_url(self):
        entry = self._make_entry(
            media_content=[{"medium": "image"}]  # no url key
        )
        assert _extract_image(entry) == ""


# ===========================================================================
# _detect_content_type
# ===========================================================================

class TestDetectContentType:

    # ── Rocket League ────────────────────────────────────────────────────────

    def test_rl_patch_notes_from_title(self):
        assert _detect_content_type("Patch note 5.21 live", "", "rocketleague") == "patch_notes"

    def test_rl_hotfix_in_title(self):
        assert _detect_content_type("Hotfix deployed today", "", "rocketleague") == "patch_notes"

    def test_rl_update_patch_in_summary(self):
        assert _detect_content_type("News", "patch is live", "rocketleague") == "patch_notes"

    def test_rl_season_start_keyword(self):
        assert _detect_content_type("Season 15 begins now!", "", "rocketleague") == "season_start"

    def test_rl_item_shop(self):
        assert _detect_content_type("Item shop today has painted wheels", "", "rocketleague") == "item_shop"

    def test_rl_collab_keyword(self):
        assert _detect_content_type("New collab incoming", "", "rocketleague") == "collab_announcement"

    def test_rl_esports_keyword(self):
        assert _detect_content_type("RLCS Major kicks off tomorrow", "", "rocketleague") == "event_announcement"

    def test_rl_roster_signs(self):
        # "signs " keyword triggers roster_change (note: "esports" in text would match event first)
        assert _detect_content_type("jstn signs with NRG", "", "rocketleague") == "roster_change"

    def test_rl_default_is_patch_notes(self):
        assert _detect_content_type("Something random", "no keywords here", "rocketleague") == "patch_notes"

    # ── Geometry Dash ────────────────────────────────────────────────────────

    def test_gd_top1_keyword(self):
        assert _detect_content_type("New top 1 demon!", "", "geometrydash") == "top1_verified"

    def test_gd_geode_mod_loader(self):
        assert _detect_content_type("Geode mod loader v2 released", "", "geometrydash") == "mod_update"

    def test_gd_game_update_2_2(self):
        assert _detect_content_type("GD 2.2 update is here", "", "geometrydash") == "game_update"

    def test_gd_verified_keyword(self):
        assert _detect_content_type("Tartarus verified by Dolphy", "", "geometrydash") == "level_verified"

    def test_gd_beaten_keyword(self):
        assert _detect_content_type("Slaughterhouse beaten: new victor", "", "geometrydash") == "level_beaten"

    def test_gd_demon_list_keyword(self):
        # "update" in text matches game_update before "demon list" — first match wins
        assert _detect_content_type("Demon list updated", "", "geometrydash") == "game_update"
        # Pure "demon list" without "update" hits the correct classifier
        assert _detect_content_type("Demon list reshuffled", "", "geometrydash") == "demon_list_update"

    def test_gd_rated_keyword(self):
        assert _detect_content_type("New level star rated", "", "geometrydash") == "level_rated"

    def test_gd_daily_keyword(self):
        assert _detect_content_type("Daily level is Deadlocked", "", "geometrydash") == "daily_level"

    def test_gd_weekly_demon_keyword(self):
        assert _detect_content_type("Weekly demon: Bloodbath", "", "geometrydash") == "weekly_demon"

    def test_gd_default_is_game_update(self):
        assert _detect_content_type("Something random", "", "geometrydash") == "game_update"

    def test_first_match_wins(self):
        # "patch note" appears before "update" in _RL_KEYWORDS, so patch_notes wins
        assert _detect_content_type("Patch note v2. update notes", "", "rocketleague") == "patch_notes"

    def test_case_insensitive_matching(self):
        assert _detect_content_type("PATCH NOTES FOR THE GAME", "", "rocketleague") == "patch_notes"

    def test_unknown_niche_falls_back_to_breaking_news(self):
        result = _detect_content_type("Some title", "", "unknown_niche")
        assert result == "breaking_news"


# ===========================================================================
# RSSCollector.collect()
# ===========================================================================

def _make_feed_entry(
    entry_id: str = "https://example.com/post-1",
    title: str = "Rocket League Season 15 starts now!",
    link: str = "https://example.com/post-1",
    summary: str = "The new season begins today.",
    author: str = "RL Team",
    media_content=None,
    tags=None,
    published: str = "2026-03-21T00:00:00Z",
) -> MagicMock:
    entry = MagicMock()
    entry.get = lambda key, default=None: {
        "id":              entry_id,
        "title":           title,
        "link":            link,
        "summary":         summary,
        "author":          author,
        "media_content":   media_content or [],
        "media_thumbnail": [],
        "enclosures":      [],
        "tags":            tags or [],
        "published":       published,
        "content":         [],
    }.get(key, default)
    return entry


def _make_feed(entries: list, bozo: bool = False) -> MagicMock:
    feed = MagicMock()
    feed.entries = entries
    feed.bozo = bozo
    return feed


def _make_collector(
    source_id: int = 1,
    url: str = "https://feeds.example.com/rl",
    niche: str = "rocketleague",
) -> RSSCollector:
    return RSSCollector(source_id=source_id, config={"url": url}, niche=niche)


class TestRSSCollectorCollect:

    @pytest.mark.asyncio
    async def test_happy_path_returns_rawcontent_items(self):
        entry = _make_feed_entry()
        feed = _make_feed([entry])

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector().collect()

        assert len(result) == 1
        assert isinstance(result[0], RawContent)

    @pytest.mark.asyncio
    async def test_source_id_propagated(self):
        entry = _make_feed_entry()
        feed = _make_feed([entry])

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector(source_id=99).collect()

        assert result[0].source_id == 99

    @pytest.mark.asyncio
    async def test_niche_propagated(self):
        entry = _make_feed_entry()
        feed = _make_feed([entry])

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector(niche="geometrydash").collect()

        assert result[0].niche == "geometrydash"

    @pytest.mark.asyncio
    async def test_bozo_feed_with_no_entries_returns_empty(self):
        feed = _make_feed(entries=[], bozo=True)

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector().collect()

        assert result == []

    @pytest.mark.asyncio
    async def test_bozo_feed_with_entries_still_processed(self):
        """A bozo feed that still has entries should be processed (feedparser behaviour)."""
        entry = _make_feed_entry()
        feed = _make_feed(entries=[entry], bozo=True)

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector().collect()

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_feed_returns_empty_list(self):
        feed = _make_feed(entries=[])

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector().collect()

        assert result == []

    @pytest.mark.asyncio
    async def test_exception_in_feedparser_returns_empty(self):
        with patch("src.collectors.rss.feedparser.parse", side_effect=Exception("parse error")):
            result = await _make_collector().collect()

        assert result == []

    @pytest.mark.asyncio
    async def test_external_id_uses_entry_id(self):
        entry = _make_feed_entry(entry_id="https://example.com/unique-123")
        feed = _make_feed([entry])

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector().collect()

        assert result[0].external_id == "https://example.com/unique-123"

    @pytest.mark.asyncio
    async def test_external_id_falls_back_to_link(self):
        entry = _make_feed_entry(entry_id=None, link="https://example.com/fallback")

        # Patch get so id returns None
        original_get = entry.get

        def patched_get(key, default=None):
            if key == "id":
                return None
            return original_get(key, default)

        entry.get = patched_get
        feed = _make_feed([entry])

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector().collect()

        assert result[0].external_id == "https://example.com/fallback"

    @pytest.mark.asyncio
    async def test_external_id_falls_back_to_md5_of_title(self):
        import hashlib

        entry = _make_feed_entry(entry_id=None, link=None, title="Unique Title")

        def patched_get(key, default=None):
            if key in ("id", "link"):
                return None
            return {
                "title": "Unique Title", "summary": "", "author": "",
                "media_content": [], "media_thumbnail": [], "enclosures": [],
                "tags": [], "published": "", "content": [],
            }.get(key, default)

        entry.get = patched_get
        feed = _make_feed([entry])

        expected_md5 = hashlib.md5("Unique Title".encode()).hexdigest()

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector().collect()

        assert result[0].external_id == expected_md5

    @pytest.mark.asyncio
    async def test_title_html_entities_unescaped(self):
        entry = _make_feed_entry(title="Rock &amp; Roll Season")
        feed = _make_feed([entry])

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector().collect()

        assert result[0].title == "Rock & Roll Season"

    @pytest.mark.asyncio
    async def test_summary_html_stripped(self):
        entry = _make_feed_entry(summary="<p>The <b>new</b> season begins.</p>")
        feed = _make_feed([entry])

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector().collect()

        assert "<p>" not in result[0].body
        assert "new" in result[0].body

    @pytest.mark.asyncio
    async def test_content_type_detected_from_keywords(self):
        entry = _make_feed_entry(title="Rocket League Patch Notes v5.20")
        feed = _make_feed([entry])

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector(niche="rocketleague").collect()

        assert result[0].content_type == "patch_notes"

    @pytest.mark.asyncio
    async def test_gd_entry_classified_correctly(self):
        entry = _make_feed_entry(title="New top 1: Tartarus verified by Dolphy!")
        feed = _make_feed([entry])

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector(niche="geometrydash").collect()

        assert result[0].content_type == "top1_verified"

    @pytest.mark.asyncio
    async def test_image_extracted_from_media_content(self):
        entry = _make_feed_entry(
            media_content=[{"medium": "image", "url": "https://cdn.example.com/img.jpg"}]
        )
        feed = _make_feed([entry])

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector().collect()

        assert result[0].image_url == "https://cdn.example.com/img.jpg"

    @pytest.mark.asyncio
    async def test_author_propagated(self):
        entry = _make_feed_entry(author="RL Staff Writer")
        feed = _make_feed([entry])

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector().collect()

        assert result[0].author == "RL Staff Writer"

    @pytest.mark.asyncio
    async def test_metadata_contains_published(self):
        entry = _make_feed_entry(published="Fri, 21 Mar 2026 12:00:00 +0000")
        feed = _make_feed([entry])

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector().collect()

        assert result[0].metadata["published"] == "Fri, 21 Mar 2026 12:00:00 +0000"

    @pytest.mark.asyncio
    async def test_tags_in_metadata(self):
        tag_mock = MagicMock()
        tag_mock.get = lambda k, d=None: "rocketleague" if k == "term" else d

        entry = _make_feed_entry(tags=[tag_mock])
        feed = _make_feed([entry])

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector().collect()

        assert "rocketleague" in result[0].metadata["tags"]

    @pytest.mark.asyncio
    async def test_multiple_entries_all_returned(self):
        entries = [_make_feed_entry(entry_id=f"id-{i}", title=f"Post {i}") for i in range(5)]
        feed = _make_feed(entries)

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector().collect()

        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_url_set_from_entry_link(self):
        entry = _make_feed_entry(link="https://rocketleague.com/news/patch-520")
        feed = _make_feed([entry])

        with patch("src.collectors.rss.feedparser.parse", return_value=feed):
            result = await _make_collector().collect()

        assert result[0].url == "https://rocketleague.com/news/patch-520"

    @pytest.mark.asyncio
    async def test_asyncio_to_thread_used_for_feedparser(self):
        """Verify feedparser is called via asyncio.to_thread (not directly in event loop)."""
        entry = _make_feed_entry()
        feed = _make_feed([entry])

        with patch("src.collectors.rss.asyncio.to_thread", new_callable=AsyncMock, return_value=feed) as mock_thread:
            result = await _make_collector().collect()

        mock_thread.assert_awaited_once()
