"""
Unit tests for src/collectors/scraper.py
ScraperCollector, _fetch, _parse, _classify.
All HTTP calls are mocked — no network access.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from src.collectors.scraper import (
    ScraperCollector,
    _classify,
    _fetch,
    _parse,
    _MAX_ITEMS,
    _MIN_TITLE,
)
from src.collectors.base import RawContent


# ── _classify() — rocketleague ─────────────────────────────────────────────────

class TestClassifyRocketLeague:

    def test_default_is_breaking_news(self):
        assert _classify("Some random article headline", "rocketleague") == "breaking_news"

    def test_patch_notes_keyword(self):
        assert _classify("Rocket League patch notes v2.44 released", "rocketleague") == "patch_notes"

    def test_hotfix_keyword(self):
        assert _classify("Hotfix for crashes now live", "rocketleague") == "patch_notes"

    def test_update_with_version_number(self):
        assert _classify("Rocket League update v2.44 is now available", "rocketleague") == "patch_notes"

    def test_update_without_version_not_patch(self):
        assert _classify("Big update coming this season", "rocketleague") == "breaking_news"

    def test_esports_result_with_grand_final_and_wins(self):
        assert _classify("NRG wins Grand Final sweep at RLCS", "rocketleague") == "esports_result"

    def test_esports_result_with_bracket_and_beats(self):
        assert _classify("G2 beats NRG in qualifier bracket", "rocketleague") == "esports_result"

    def test_esports_result_requires_outcome_word(self):
        """grand final alone without outcome word should not be esports_result."""
        assert _classify("Grand Final preview: who will win?", "rocketleague") == "breaking_news"

    def test_roster_change_signs(self):
        assert _classify("Team Liquid signs new player", "rocketleague") == "roster_change"

    def test_roster_change_parts_ways(self):
        assert _classify("NRG parts ways with Jstn", "rocketleague") == "roster_change"

    def test_roster_change_joins(self):
        assert _classify("Turbo joins Team Vitality", "rocketleague") == "roster_change"

    def test_roster_change_released_from(self):
        assert _classify("Player released from roster", "rocketleague") == "roster_change"

    def test_roster_change_roster_change_keyword(self):
        assert _classify("Roster change announced by G2", "rocketleague") == "roster_change"

    def test_item_shop_keyword(self):
        assert _classify("New item shop rotation this week", "rocketleague") == "item_shop"

    def test_item_shop_case_insensitive(self):
        assert _classify("ITEM SHOP update now live", "rocketleague") == "item_shop"

    def test_esports_eliminat_keyword(self):
        assert _classify("Team eliminated in Grand Final qualifier", "rocketleague") == "esports_result"

    def test_esports_advance_keyword(self):
        assert _classify("NRG advance through qualifier bracket", "rocketleague") == "esports_result"

    def test_esports_champion_keyword(self):
        assert _classify("G2 champion in Grand Final", "rocketleague") == "esports_result"


# ── _classify() — geometrydash ─────────────────────────────────────────────────

class TestClassifyGeometryDash:

    def test_default_is_breaking_news(self):
        assert _classify("Some unrelated article", "geometrydash") == "breaking_news"

    def test_top1_keyword(self):
        assert _classify("New top 1 level verified on the list", "geometrydash") == "top1_verified"

    def test_new_1_keyword(self):
        assert _classify("New #1 demon verified by player", "geometrydash") == "top1_verified"

    def test_hardest_level_keyword(self):
        assert _classify("Hardest level ever beaten by speedrunner", "geometrydash") == "top1_verified"

    def test_game_update_2_2_with_update(self):
        assert _classify("Geometry Dash 2.2 update released", "geometrydash") == "game_update"

    def test_game_update_2_3_out_now(self):
        assert _classify("GD 2.3 is out now", "geometrydash") == "game_update"

    def test_2_2_without_update_word(self):
        """2.2 alone without update/patch/released/out now should not be game_update."""
        assert _classify("Level uses 2.2 mechanics", "geometrydash") == "breaking_news"

    def test_robtop_announce(self):
        assert _classify("RobTop announces new update for the game", "geometrydash") == "game_update"

    def test_robtop_without_action_word(self):
        assert _classify("RobTop commented on the stream", "geometrydash") == "breaking_news"

    def test_verified_keyword(self):
        assert _classify("Demon level verified after months of attempts", "geometrydash") == "level_verified"

    def test_verification_keyword(self):
        assert _classify("Full verification of Tartarus complete", "geometrydash") == "level_verified"

    def test_beaten_keyword(self):
        assert _classify("Top demon beaten for the first time", "geometrydash") == "level_beaten"

    def test_new_victor_keyword(self):
        assert _classify("New victor on Slaughterhouse", "geometrydash") == "level_beaten"

    def test_first_victor_keyword(self):
        assert _classify("First victor achieves completion", "geometrydash") == "level_beaten"

    def test_demon_list_keyword(self):
        assert _classify("Demon list update: three new levels added", "geometrydash") == "demon_list_update"

    def test_demonlist_keyword(self):
        assert _classify("Pointercrate demonlist has been updated", "geometrydash") == "demon_list_update"

    def test_rated_level(self):
        assert _classify("New star rated level added this week", "geometrydash") == "level_rated"

    def test_rated_without_level_or_star(self):
        assert _classify("Player has been rated highly", "geometrydash") == "breaking_news"

    def test_geode_update(self):
        assert _classify("Geode mod loader version 2.0 released", "geometrydash") == "mod_update"

    def test_geode_without_action(self):
        assert _classify("Geode compatibility info", "geometrydash") == "breaking_news"

    def test_speedrun_keyword(self):
        assert _classify("New world record speedrun on Deadlocked", "geometrydash") == "speedrun_wr"

    def test_world_record_keyword(self):
        assert _classify("World record broken on Electroman Adventures", "geometrydash") == "speedrun_wr"


# ── _fetch() ───────────────────────────────────────────────────────────────────

class TestFetch:

    @pytest.mark.asyncio
    async def test_returns_html_on_success(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "<html><body>Content</body></html>"
        mock_response.is_redirect = False

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch("https://example.com/news")

        assert result == "<html><body>Content</body></html>"

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch("https://example.com/missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_connect_error(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch("https://unreachable.example.com")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch("https://slow.example.com")

        assert result is None


# ── _parse() ───────────────────────────────────────────────────────────────────

class TestParse:

    def test_extracts_article_headlines(self):
        html = """
        <html><body>
          <article>
            <h2>RLCS World Championship Preview</h2>
            <a href="/articles/rlcs-preview">Read more</a>
          </article>
        </body></html>
        """
        items = _parse(html, "https://example.com", source_id=1, niche="rocketleague")
        assert len(items) == 1
        assert "RLCS World Championship Preview" in items[0].title

    def test_extracts_heading_with_link(self):
        html = """
        <html><body>
          <h2><a href="/news/item">Big Rocket League Update Released</a></h2>
        </body></html>
        """
        items = _parse(html, "https://example.com", source_id=1, niche="rocketleague")
        assert len(items) >= 1
        assert any("Big Rocket League Update" in i.title for i in items)

    def test_ignores_headings_shorter_than_min_title(self):
        short = "A" * (_MIN_TITLE - 1)
        html = f"""
        <html><body>
          <article>
            <h2>{short}</h2>
            <a href="/short">Read</a>
          </article>
        </body></html>
        """
        items = _parse(html, "https://example.com", source_id=1, niche="rocketleague")
        assert len(items) == 0

    def test_resolves_relative_url(self):
        html = """
        <html><body>
          <article>
            <h2>Rocket League Season 14 is here</h2>
            <a href="/news/season-14">Read</a>
          </article>
        </body></html>
        """
        items = _parse(html, "https://example.com", source_id=1, niche="rocketleague")
        assert items[0].url == "https://example.com/news/season-14"

    def test_resolves_protocol_relative_url(self):
        html = """
        <html><body>
          <article>
            <h2>Top Rocket League Player Announced</h2>
            <a href="//cdn.example.com/news/player">Link</a>
          </article>
        </body></html>
        """
        items = _parse(html, "https://example.com", source_id=1, niche="rocketleague")
        assert items[0].url.startswith("https://cdn.example.com")

    def test_skips_non_http_relative_links(self):
        html = """
        <html><body>
          <article>
            <h2>Rocket League Championship Series starts today</h2>
            <a href="javascript:void(0)">Read</a>
          </article>
        </body></html>
        """
        items = _parse(html, "https://example.com", source_id=1, niche="rocketleague")
        assert len(items) == 0

    def test_deduplicates_same_url(self):
        html = """
        <html><body>
          <article>
            <h2>RLCS Season Results Are In Now</h2>
            <a href="/article/rlcs">Read</a>
          </article>
          <article>
            <h2>RLCS Season Results Are In Now</h2>
            <a href="/article/rlcs">Duplicate</a>
          </article>
        </body></html>
        """
        items = _parse(html, "https://example.com", source_id=1, niche="rocketleague")
        urls = [i.url for i in items]
        assert len(set(urls)) == len(urls)

    def test_strips_url_anchor(self):
        html = """
        <html><body>
          <article>
            <h2>New Rocket League update brings big changes</h2>
            <a href="/article/update#section">Read</a>
          </article>
        </body></html>
        """
        items = _parse(html, "https://example.com", source_id=1, niche="rocketleague")
        assert "#" not in items[0].url

    def test_strips_trailing_slash(self):
        html = """
        <html><body>
          <article>
            <h2>Grand Final results from last weekend</h2>
            <a href="/results/">Read</a>
          </article>
        </body></html>
        """
        items = _parse(html, "https://example.com", source_id=1, niche="rocketleague")
        assert not items[0].url.endswith("/")

    def test_caps_at_max_items(self):
        articles = ""
        for i in range(_MAX_ITEMS + 5):
            articles += f"""
            <article>
              <h2>Rocket League News Article Number {i:03d}</h2>
              <a href="/article/{i}">Read</a>
            </article>
            """
        html = f"<html><body>{articles}</body></html>"
        items = _parse(html, "https://example.com", source_id=1, niche="rocketleague")
        assert len(items) == _MAX_ITEMS

    def test_external_id_is_md5_based(self):
        html = """
        <html><body>
          <article>
            <h2>Rocket League Championship Series preview</h2>
            <a href="https://example.com/rlcs">Read</a>
          </article>
        </body></html>
        """
        items = _parse(html, "https://example.com", source_id=1, niche="rocketleague")
        assert items[0].external_id.startswith("scrape_")

    def test_metadata_contains_title(self):
        html = """
        <html><body>
          <article>
            <h2>Big Rocket League announcement today</h2>
            <a href="/article/big-news">Read</a>
          </article>
        </body></html>
        """
        items = _parse(html, "https://example.com", source_id=1, niche="rocketleague")
        assert items[0].metadata["title"] == "Big Rocket League announcement today"

    def test_returns_empty_list_for_empty_html(self):
        items = _parse("", "https://example.com", source_id=1, niche="rocketleague")
        assert items == []

    def test_h3_fallback_strategy(self):
        """When fewer than 3 article elements, falls back to h2/h3 strategy."""
        html = """
        <html><body>
          <h3><a href="/news/article-one">Rocket League major update arrives this week</a></h3>
          <h3><a href="/news/article-two">RLCS Season 14 grand final recap today</a></h3>
        </body></html>
        """
        items = _parse(html, "https://example.com", source_id=1, niche="rocketleague")
        assert len(items) >= 1

    def test_parent_anchor_link_used(self):
        """Heading inside <a> should use parent link."""
        html = """
        <html><body>
          <a href="/news/wrapped-heading">
            <h2>Rocket League patch update is now live</h2>
          </a>
        </body></html>
        """
        items = _parse(html, "https://example.com", source_id=1, niche="rocketleague")
        assert len(items) >= 1
        assert items[0].url == "https://example.com/news/wrapped-heading"

    def test_niche_and_source_id_set(self):
        html = """
        <html><body>
          <article>
            <h2>Geometry Dash new level verification done</h2>
            <a href="/gd/level">Link</a>
          </article>
        </body></html>
        """
        items = _parse(html, "https://example.com", source_id=42, niche="geometrydash")
        assert items[0].source_id == 42
        assert items[0].niche == "geometrydash"

    def test_absolute_url_preserved(self):
        html = """
        <html><body>
          <article>
            <h2>Top Rocket League team wins championship event</h2>
            <a href="https://otherdomain.com/article">Read</a>
          </article>
        </body></html>
        """
        items = _parse(html, "https://example.com", source_id=1, niche="rocketleague")
        assert items[0].url == "https://otherdomain.com/article"


# ── ScraperCollector.collect() ─────────────────────────────────────────────────

class TestScraperCollector:

    def test_init_stores_url_and_niche(self):
        collector = ScraperCollector(
            source_id=1,
            config={"url": "https://example.com"},
            niche="rocketleague",
        )
        assert collector.url == "https://example.com"
        assert collector.niche == "rocketleague"
        assert collector.source_id == 1

    def test_init_missing_url_is_empty_string(self):
        collector = ScraperCollector(source_id=1, config={}, niche="rocketleague")
        assert collector.url == ""

    @pytest.mark.asyncio
    async def test_collect_returns_empty_when_no_url_configured(self):
        collector = ScraperCollector(source_id=1, config={}, niche="rocketleague")
        items = await collector.collect()
        assert items == []

    @pytest.mark.asyncio
    async def test_collect_returns_empty_when_fetch_fails(self):
        collector = ScraperCollector(
            source_id=1,
            config={"url": "https://example.com"},
            niche="rocketleague",
        )
        with patch("src.collectors.scraper._fetch", AsyncMock(return_value=None)):
            items = await collector.collect()
        assert items == []

    @pytest.mark.asyncio
    async def test_collect_returns_items_when_successful(self):
        html = """
        <html><body>
          <article>
            <h2>RLCS World Championship starts this weekend</h2>
            <a href="/rlcs-championship">Read</a>
          </article>
        </body></html>
        """
        collector = ScraperCollector(
            source_id=1,
            config={"url": "https://example.com"},
            niche="rocketleague",
        )
        with patch("src.collectors.scraper._fetch", AsyncMock(return_value=html)):
            items = await collector.collect()
        assert len(items) == 1
        assert "RLCS World Championship" in items[0].title

    @pytest.mark.asyncio
    async def test_collect_warns_when_zero_items_from_large_html(self):
        """With large HTML but 0 matches, a warning should be emitted but no crash."""
        large_empty_html = "<html><body>" + "x" * 2000 + "</body></html>"
        collector = ScraperCollector(
            source_id=1,
            config={"url": "https://example.com"},
            niche="rocketleague",
        )
        with patch("src.collectors.scraper._fetch", AsyncMock(return_value=large_empty_html)):
            items = await collector.collect()
        assert items == []

    @pytest.mark.asyncio
    async def test_collect_logs_item_count_on_success(self):
        html = """
        <html><body>
          <article>
            <h2>Rocket League new update with changes</h2>
            <a href="/news">Read</a>
          </article>
        </body></html>
        """
        collector = ScraperCollector(
            source_id=1,
            config={"url": "https://example.com/news"},
            niche="rocketleague",
        )
        with patch("src.collectors.scraper._fetch", AsyncMock(return_value=html)):
            items = await collector.collect()
        assert len(items) == 1
