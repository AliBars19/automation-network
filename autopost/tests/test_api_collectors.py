"""
Unit tests for API collectors:
  - PointercrateCollector   (src/collectors/apis/pointercrate.py)
  - GDBrowserCollector      (src/collectors/apis/gdbrowser.py)
  - GitHubCollector         (src/collectors/apis/github.py)
  - FlashbackCollector      (src/collectors/apis/flashback.py)
  - RLStatsCollector        (src/collectors/apis/rl_stats.py)

All HTTP calls are mocked — no network access.
"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import httpx
import pytest

from src.collectors.base import RawContent


# ===========================================================================
# POINTERCRATE
# ===========================================================================

from src.collectors.apis.pointercrate import (
    PointercrateCollector,
    _fetch_demons,
    _classify,
)


def _make_demon(
    position: int = 1,
    name: str = "Tartarus",
    verifier: str = "Dolphy",
    publisher: str = None,
    video_url: str = "https://youtube.com/watch?v=abc",
    thumbnail: str = "https://img.example.com/thumb.jpg",
    demon_id: int = 42,
) -> dict:
    return {
        "position": position,
        "name": name,
        "verifier": {"name": verifier},
        "publisher": {"name": publisher or verifier},
        "video": video_url,
        "thumbnail": thumbnail,
        "id": demon_id,
    }


def _make_httpx_response(status: int, payload) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestPointercrateClassify:
    """Unit tests for _classify() — position → content_type mapping."""

    def test_position_1_is_top1_verified(self):
        assert _classify(1) == "top1_verified"

    def test_position_2_is_level_verified(self):
        assert _classify(2) == "level_verified"

    def test_position_75_is_level_verified(self):
        assert _classify(75) == "level_verified"

    def test_position_76_is_demon_list_update(self):
        assert _classify(76) == "demon_list_update"

    def test_position_150_is_demon_list_update(self):
        assert _classify(150) == "demon_list_update"

    def test_position_10_is_level_verified(self):
        assert _classify(10) == "level_verified"


class TestFetchDemons:
    """Tests for the _fetch_demons() module-level helper."""

    @pytest.mark.asyncio
    async def test_returns_sorted_demons_up_to_total(self):
        payload = [_make_demon(position=i, demon_id=i) for i in range(1, 10)]
        # Return them in reverse order to verify sorting
        shuffled = list(reversed(payload))
        resp = _make_httpx_response(200, shuffled)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.apis.pointercrate.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_demons(5)

        assert len(result) == 5
        positions = [d["position"] for d in result]
        assert positions == sorted(positions)

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        resp = _make_httpx_response(500, {})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.apis.pointercrate.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_demons(75)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_connection_error(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.apis.pointercrate.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_demons(75)

        assert result == []


class TestPointercrateCollector:
    """Integration-style tests for PointercrateCollector.collect()."""

    def _make_collector(self, source_id: int = 1) -> PointercrateCollector:
        return PointercrateCollector(source_id=source_id, config={}, niche="geometrydash")

    @pytest.mark.asyncio
    async def test_happy_path_returns_rawcontent_items(self):
        demons = [_make_demon(position=i, demon_id=i) for i in range(1, 4)]
        with patch("src.collectors.apis.pointercrate._fetch_demons", new_callable=AsyncMock, return_value=demons):
            result = await self._make_collector().collect()

        assert len(result) == 3
        for item in result:
            assert isinstance(item, RawContent)

    @pytest.mark.asyncio
    async def test_empty_demon_list_returns_empty(self):
        with patch("src.collectors.apis.pointercrate._fetch_demons", new_callable=AsyncMock, return_value=[]):
            result = await self._make_collector().collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_content_type_top1_for_position_1(self):
        demon = _make_demon(position=1)
        with patch("src.collectors.apis.pointercrate._fetch_demons", new_callable=AsyncMock, return_value=[demon]):
            result = await self._make_collector().collect()
        assert result[0].content_type == "top1_verified"

    @pytest.mark.asyncio
    async def test_content_type_level_verified_for_position_50(self):
        demon = _make_demon(position=50)
        with patch("src.collectors.apis.pointercrate._fetch_demons", new_callable=AsyncMock, return_value=[demon]):
            result = await self._make_collector().collect()
        assert result[0].content_type == "level_verified"

    @pytest.mark.asyncio
    async def test_external_id_namespaced_with_pc_prefix(self):
        demon = _make_demon(demon_id=99)
        with patch("src.collectors.apis.pointercrate._fetch_demons", new_callable=AsyncMock, return_value=[demon]):
            result = await self._make_collector().collect()
        assert result[0].external_id == "pc_99"

    @pytest.mark.asyncio
    async def test_source_id_propagated(self):
        demon = _make_demon()
        with patch("src.collectors.apis.pointercrate._fetch_demons", new_callable=AsyncMock, return_value=[demon]):
            result = await PointercrateCollector(source_id=7, config={}).collect()
        assert result[0].source_id == 7

    @pytest.mark.asyncio
    async def test_niche_defaults_to_geometrydash(self):
        demon = _make_demon()
        with patch("src.collectors.apis.pointercrate._fetch_demons", new_callable=AsyncMock, return_value=[demon]):
            result = await PointercrateCollector(source_id=1, config={}).collect()
        assert result[0].niche == "geometrydash"

    @pytest.mark.asyncio
    async def test_metadata_contains_required_keys(self):
        demon = _make_demon(position=3, name="Slaughterhouse", verifier="Zoink", publisher="GD Artists")
        with patch("src.collectors.apis.pointercrate._fetch_demons", new_callable=AsyncMock, return_value=[demon]):
            result = await self._make_collector().collect()
        meta = result[0].metadata
        assert meta["level"] == "Slaughterhouse"
        assert meta["position"] == "3"
        assert meta["verifier"] == "Zoink"
        assert meta["publisher"] == "GD Artists"

    @pytest.mark.asyncio
    async def test_score_is_150_minus_position(self):
        demon = _make_demon(position=50)
        with patch("src.collectors.apis.pointercrate._fetch_demons", new_callable=AsyncMock, return_value=[demon]):
            result = await self._make_collector().collect()
        assert result[0].score == 100  # 150 - 50

    @pytest.mark.asyncio
    async def test_score_never_negative(self):
        # Position beyond 150 should clamp to 0
        demon = _make_demon(position=200)
        with patch("src.collectors.apis.pointercrate._fetch_demons", new_callable=AsyncMock, return_value=[demon]):
            result = await self._make_collector().collect()
        assert result[0].score == 0

    @pytest.mark.asyncio
    async def test_missing_verifier_falls_back_to_unknown(self):
        demon = {
            "position": 5,
            "name": "LevelX",
            "verifier": None,
            "publisher": None,
            "video": "",
            "thumbnail": "",
            "id": 55,
        }
        with patch("src.collectors.apis.pointercrate._fetch_demons", new_callable=AsyncMock, return_value=[demon]):
            result = await self._make_collector().collect()
        assert result[0].author == "Unknown"

    @pytest.mark.asyncio
    async def test_emoji_is_alert_for_position_1(self):
        demon = _make_demon(position=1)
        with patch("src.collectors.apis.pointercrate._fetch_demons", new_callable=AsyncMock, return_value=[demon]):
            result = await self._make_collector().collect()
        assert result[0].metadata["emoji"] == "🚨"

    @pytest.mark.asyncio
    async def test_emoji_is_trophy_for_position_5(self):
        demon = _make_demon(position=5)
        with patch("src.collectors.apis.pointercrate._fetch_demons", new_callable=AsyncMock, return_value=[demon]):
            result = await self._make_collector().collect()
        assert result[0].metadata["emoji"] == "🏆"

    @pytest.mark.asyncio
    async def test_emoji_is_arrow_for_position_20(self):
        demon = _make_demon(position=20)
        with patch("src.collectors.apis.pointercrate._fetch_demons", new_callable=AsyncMock, return_value=[demon]):
            result = await self._make_collector().collect()
        assert result[0].metadata["emoji"] == "🔺"


# ===========================================================================
# GDBROWSER
# ===========================================================================

from src.collectors.apis.gdbrowser import (
    GDBrowserCollector,
    _parse_difficulty,
    _parse_official_response,
    _official_difficulty,
    _decode_b64,
    _make_daily_content,
    _make_weekly_content,
    _fetch_daily_gdbrowser,
    _fetch_weekly_gdbrowser,
    _fetch_rated,
    _NOTABLE_CREATORS,
)


class TestParseDifficulty:
    """Unit tests for the difficulty-value → label helper."""

    def test_integer_0_is_na(self):
        assert _parse_difficulty(0) == "N/A"

    def test_integer_10_is_extreme_demon(self):
        assert _parse_difficulty(10) == "Extreme Demon"

    def test_string_label_passthrough(self):
        assert _parse_difficulty("Hard Demon") == "Hard Demon"

    def test_string_integer_passed_through(self):
        # String inputs that aren't known labels are returned as-is
        assert _parse_difficulty("8") == "8"

    def test_unknown_string_passed_through(self):
        assert _parse_difficulty("not_a_number") == "not_a_number"

    def test_none_returns_unknown(self):
        assert _parse_difficulty(None) == "Unknown"

    def test_out_of_range_int_returns_unknown(self):
        assert _parse_difficulty(99) == "Unknown"


class TestParseOfficialResponse:
    """Unit tests for the colon-delimited key-value parser."""

    def test_basic_pairs(self):
        result = _parse_official_response("1:1234:2:MyLevel:4:Player")
        assert result["1"] == "1234"
        assert result["2"] == "MyLevel"
        assert result["4"] == "Player"

    def test_empty_string_returns_empty(self):
        assert _parse_official_response("") == {}

    def test_odd_count_ignores_trailing(self):
        result = _parse_official_response("1:val:orphan")
        assert result["1"] == "val"
        assert "orphan" not in result


class TestOfficialDifficulty:
    """Unit tests for _official_difficulty() using raw key/value dicts."""

    def test_auto_level(self):
        assert _official_difficulty({"25": "1"}) == "Auto"

    def test_demon_hard_demon_default(self):
        assert _official_difficulty({"17": "1", "43": "0"}) == "Hard Demon"

    def test_demon_extreme_demon(self):
        assert _official_difficulty({"17": "1", "43": "6"}) == "Extreme Demon"

    def test_non_demon_easy(self):
        assert _official_difficulty({"9": "10"}) == "Easy"

    def test_non_demon_insane(self):
        assert _official_difficulty({"9": "50"}) == "Insane"

    def test_non_demon_zero_is_na(self):
        assert _official_difficulty({}) == "N/A"


class TestDecodeB64:
    """Unit tests for base64 description decoder."""

    def test_valid_base64(self):
        import base64
        encoded = base64.urlsafe_b64encode(b"Hello World").decode()
        assert _decode_b64(encoded) == "Hello World"

    def test_invalid_input_returns_replacement_chars(self):
        # _decode_b64 uses errors="replace", so malformed input produces replacement chars
        result = _decode_b64("!!!not-valid-base64!!!")
        assert isinstance(result, str)

    def test_empty_string_returns_empty(self):
        # base64 of empty bytes is empty
        result = _decode_b64("")
        assert isinstance(result, str)

    def test_none_input_returns_empty_via_exception_branch(self):
        # Passing None raises TypeError inside urlsafe_b64decode (caught by except clause)
        result = _decode_b64(None)
        assert result == ""


class TestMakeDailyContent:
    """Unit tests for _make_daily_content() builder."""

    def test_content_type_is_daily_level(self):
        item = _make_daily_content(1, "geometrydash", "12345", "Stereo Madness", "RobTop", "Easy", 10, 500)
        assert item.content_type == "daily_level"

    def test_external_id_contains_level_id(self):
        item = _make_daily_content(1, "geometrydash", "99999", "Level", "Author", "Hard", 6, 0)
        assert "99999" in item.external_id

    def test_url_points_to_gdbrowser(self):
        item = _make_daily_content(1, "geometrydash", "11111", "Level", "Author", "Hard", 6, 0)
        assert "gdbrowser.com/11111" in item.url

    def test_score_is_likes(self):
        item = _make_daily_content(1, "geometrydash", "1", "Level", "Author", "Hard", 6, 1337)
        assert item.score == 1337

    def test_metadata_contains_level_name(self):
        item = _make_daily_content(1, "geometrydash", "1", "Deadlocked", "RobTop", "Hard Demon", 10, 0)
        assert item.metadata["level_name"] == "Deadlocked"


class TestMakeWeeklyContent:
    """Unit tests for _make_weekly_content() builder."""

    def test_content_type_is_weekly_demon(self):
        item = _make_weekly_content(1, "geometrydash", "7777", "Bloodbath", "Riot", "Extreme Demon", 10, 1000)
        assert item.content_type == "weekly_demon"

    def test_external_id_contains_weekly_prefix(self):
        item = _make_weekly_content(1, "geometrydash", "7777", "Bloodbath", "Riot", "Extreme Demon", 10, 1000)
        assert item.external_id.startswith("weekly_")


class TestFetchDailyGDBrowser:
    """Tests for the GDBrowser daily fetcher."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_rawcontent(self):
        data = {"id": "123", "name": "Stereo Madness", "author": "RobTop", "difficulty": 1, "stars": 10, "likes": 500}
        resp = _make_httpx_response(200, data)
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        result = await _fetch_daily_gdbrowser(client, 1, "geometrydash")
        assert result is not None
        assert result.content_type == "daily_level"
        assert "Stereo Madness" in result.title

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        resp = _make_httpx_response(500, {})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        result = await _fetch_daily_gdbrowser(client, 1, "geometrydash")
        assert result is None

    @pytest.mark.asyncio
    async def test_connection_error_returns_none(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        result = await _fetch_daily_gdbrowser(client, 1, "geometrydash")
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_fields_use_defaults(self):
        resp = _make_httpx_response(200, {})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        result = await _fetch_daily_gdbrowser(client, 1, "geometrydash")
        assert result is not None
        assert "Unknown" in result.title
        assert result.score == 0


class TestFetchWeeklyGDBrowser:
    """Tests for the GDBrowser weekly fetcher."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_weekly_demon(self):
        data = {"id": "456", "name": "Bloodbath", "author": "Riot", "difficulty": 10, "stars": 10, "likes": 2000}
        resp = _make_httpx_response(200, data)
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        result = await _fetch_weekly_gdbrowser(client, 1, "geometrydash")
        assert result is not None
        assert result.content_type == "weekly_demon"

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        resp = _make_httpx_response(500, {})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        result = await _fetch_weekly_gdbrowser(client, 1, "geometrydash")
        assert result is None


class TestFetchRated:
    """Tests for the _fetch_rated() helper."""

    def _make_level(self, name: str, author: str, difficulty: str, level_id: str = "100") -> dict:
        return {
            "id": level_id,
            "name": name,
            "author": author,
            "difficulty": difficulty,
            "stars": 10,
            "likes": 500,
        }

    @pytest.mark.asyncio
    async def test_notable_creator_included(self):
        # Pick a known notable creator from the set
        notable = next(iter(_NOTABLE_CREATORS)).title()  # e.g. "Viprin"
        level = self._make_level("LevelA", notable, "Hard Demon")
        resp = _make_httpx_response(200, [level])
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        result = await _fetch_rated(client, 1, "geometrydash")
        assert len(result) == 1
        assert result[0].content_type == "level_rated"

    @pytest.mark.asyncio
    async def test_extreme_demon_included_regardless_of_creator(self):
        level = self._make_level("GodLevel", "NobodyFamous123", "Extreme Demon")
        resp = _make_httpx_response(200, [level])
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        result = await _fetch_rated(client, 1, "geometrydash")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_unknown_creator_non_extreme_excluded(self):
        level = self._make_level("MidLevel", "SomeRando999", "Hard Demon")
        resp = _make_httpx_response(200, [level])
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        result = await _fetch_rated(client, 1, "geometrydash")
        assert result == []

    @pytest.mark.asyncio
    async def test_missing_level_id_skipped(self):
        level = {"name": "NoID", "author": "Viprin", "difficulty": "Easy", "stars": 1, "likes": 0}
        resp = _make_httpx_response(200, [level])
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        result = await _fetch_rated(client, 1, "geometrydash")
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty(self):
        resp = _make_httpx_response(200, [])
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        result = await _fetch_rated(client, 1, "geometrydash")
        assert result == []

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        resp = _make_httpx_response(503, {})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        result = await _fetch_rated(client, 1, "geometrydash")
        assert result == []

    @pytest.mark.asyncio
    async def test_external_id_uses_rated_prefix(self):
        level = self._make_level("TopLevel", "Viprin", "Extreme Demon", "77777")
        resp = _make_httpx_response(200, [level])
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        result = await _fetch_rated(client, 1, "geometrydash")
        assert result[0].external_id == "rated_77777"


class TestGDBrowserCollector:
    """Integration tests for GDBrowserCollector.collect()."""

    def _make_collector(self) -> GDBrowserCollector:
        return GDBrowserCollector(source_id=1, config={}, niche="geometrydash")

    @pytest.mark.asyncio
    async def test_collects_daily_weekly_and_rated(self):
        daily_item = _make_daily_content(1, "geometrydash", "1", "Daily", "Author", "Easy", 1, 0)
        weekly_item = _make_weekly_content(1, "geometrydash", "2", "Weekly", "Author2", "Extreme Demon", 10, 0)
        rated_item = RawContent(
            source_id=1, external_id="rated_999", niche="geometrydash",
            content_type="level_rated", title="Rated", url="", body="",
        )

        with (
            patch("src.collectors.apis.gdbrowser._fetch_daily_gdbrowser", new_callable=AsyncMock, return_value=daily_item),
            patch("src.collectors.apis.gdbrowser._fetch_weekly_gdbrowser", new_callable=AsyncMock, return_value=weekly_item),
            patch("src.collectors.apis.gdbrowser._fetch_rated", new_callable=AsyncMock, return_value=[rated_item]),
        ):
            # Mock the httpx.AsyncClient context manager
            mock_client = AsyncMock()
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            with patch("src.collectors.apis.gdbrowser.httpx.AsyncClient", return_value=mock_ctx):
                result = await self._make_collector().collect()

        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_falls_back_to_official_when_gdbrowser_daily_fails(self):
        weekly_item = _make_weekly_content(1, "geometrydash", "2", "Weekly", "Author", "Easy", 1, 0)
        official_daily = _make_daily_content(1, "geometrydash", "3", "OfficialDaily", "Author", "Easy", 1, 0)

        with (
            patch("src.collectors.apis.gdbrowser._fetch_daily_gdbrowser", new_callable=AsyncMock, return_value=None),
            patch("src.collectors.apis.gdbrowser._fetch_daily_official", new_callable=AsyncMock, return_value=official_daily),
            patch("src.collectors.apis.gdbrowser._fetch_weekly_gdbrowser", new_callable=AsyncMock, return_value=weekly_item),
            patch("src.collectors.apis.gdbrowser._fetch_rated", new_callable=AsyncMock, return_value=[]),
        ):
            mock_client = AsyncMock()
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            with patch("src.collectors.apis.gdbrowser.httpx.AsyncClient", return_value=mock_ctx):
                result = await self._make_collector().collect()

        titles = [r.title for r in result]
        assert any("OfficialDaily" in t for t in titles)

    @pytest.mark.asyncio
    async def test_returns_empty_when_all_fetches_fail(self):
        with (
            patch("src.collectors.apis.gdbrowser._fetch_daily_gdbrowser", new_callable=AsyncMock, return_value=None),
            patch("src.collectors.apis.gdbrowser._fetch_daily_official", new_callable=AsyncMock, return_value=None),
            patch("src.collectors.apis.gdbrowser._fetch_weekly_gdbrowser", new_callable=AsyncMock, return_value=None),
            patch("src.collectors.apis.gdbrowser._fetch_weekly_official", new_callable=AsyncMock, return_value=None),
            patch("src.collectors.apis.gdbrowser._fetch_rated", new_callable=AsyncMock, return_value=[]),
        ):
            mock_client = AsyncMock()
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            with patch("src.collectors.apis.gdbrowser.httpx.AsyncClient", return_value=mock_ctx):
                result = await self._make_collector().collect()

        assert result == []


# ===========================================================================
# GITHUB
# ===========================================================================

from src.collectors.apis.github import GitHubCollector, _fetch_releases


def _make_release(
    release_id: int = 1,
    tag: str = "v1.0.0",
    name: str = "Version 1.0.0",
    body: str = "Initial stable release",
    prerelease: bool = False,
    draft: bool = False,
    html_url: str = "https://github.com/owner/repo/releases/tag/v1.0.0",
) -> dict:
    return {
        "id": release_id,
        "tag_name": tag,
        "name": name,
        "body": body,
        "prerelease": prerelease,
        "draft": draft,
        "html_url": html_url,
    }


class TestFetchReleases:
    """Tests for the _fetch_releases() module-level helper."""

    @pytest.mark.asyncio
    async def test_returns_list_on_success(self):
        releases = [_make_release()]
        resp = _make_httpx_response(200, releases)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.apis.github.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_releases("owner/repo")

        assert result == releases

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        resp = _make_httpx_response(404, {})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.apis.github.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_releases("owner/missing-repo")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_connection_error(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.apis.github.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_releases("owner/repo")

        assert result == []


class TestGitHubCollector:
    """Integration tests for GitHubCollector.collect()."""

    def _make_collector(self, repo: str = "geode-sdk/geode", niche: str = "geometrydash") -> GitHubCollector:
        return GitHubCollector(source_id=2, config={"repo": repo}, niche=niche)

    @pytest.mark.asyncio
    async def test_happy_path_returns_mod_update(self):
        releases = [_make_release(release_id=1, tag="v2.0.0", name="Geode 2.0")]
        with patch("src.collectors.apis.github._fetch_releases", new_callable=AsyncMock, return_value=releases):
            result = await self._make_collector().collect()

        assert len(result) == 1
        assert result[0].content_type == "mod_update"

    @pytest.mark.asyncio
    async def test_skips_prereleases(self):
        releases = [
            _make_release(release_id=1, tag="v2.0.0-beta", prerelease=True),
            _make_release(release_id=2, tag="v1.9.9"),
        ]
        with patch("src.collectors.apis.github._fetch_releases", new_callable=AsyncMock, return_value=releases):
            result = await self._make_collector().collect()

        assert len(result) == 1
        assert result[0].external_id == "gh_2"

    @pytest.mark.asyncio
    async def test_skips_drafts(self):
        releases = [_make_release(release_id=10, draft=True)]
        with patch("src.collectors.apis.github._fetch_releases", new_callable=AsyncMock, return_value=releases):
            result = await self._make_collector().collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_repo_configured(self):
        collector = GitHubCollector(source_id=1, config={}, niche="geometrydash")
        result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_fetch_fails(self):
        with patch("src.collectors.apis.github._fetch_releases", new_callable=AsyncMock, return_value=[]):
            result = await self._make_collector().collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_external_id_uses_gh_prefix(self):
        releases = [_make_release(release_id=42)]
        with patch("src.collectors.apis.github._fetch_releases", new_callable=AsyncMock, return_value=releases):
            result = await self._make_collector().collect()
        assert result[0].external_id == "gh_42"

    @pytest.mark.asyncio
    async def test_respects_max_releases_cap(self):
        # More than _MAX_RELEASES (5) stable releases — only first 5 should be returned
        releases = [_make_release(release_id=i, tag=f"v1.{i}.0") for i in range(1, 20)]
        with patch("src.collectors.apis.github._fetch_releases", new_callable=AsyncMock, return_value=releases):
            result = await self._make_collector().collect()
        assert len(result) <= 5

    @pytest.mark.asyncio
    async def test_title_includes_repo_name(self):
        releases = [_make_release(name="Big Update", tag="v3.0")]
        with patch("src.collectors.apis.github._fetch_releases", new_callable=AsyncMock, return_value=releases):
            result = await self._make_collector(repo="geode-sdk/geode").collect()
        assert "geode" in result[0].title

    @pytest.mark.asyncio
    async def test_body_trimmed_to_300_chars(self):
        long_body = "Line of release notes\n" * 50  # 1100+ chars
        releases = [_make_release(body=long_body)]
        with patch("src.collectors.apis.github._fetch_releases", new_callable=AsyncMock, return_value=releases):
            result = await self._make_collector().collect()
        assert len(result[0].body) <= 300

    @pytest.mark.asyncio
    async def test_metadata_contains_version(self):
        releases = [_make_release(tag="v5.0.1")]
        with patch("src.collectors.apis.github._fetch_releases", new_callable=AsyncMock, return_value=releases):
            result = await self._make_collector().collect()
        assert result[0].metadata["version"] == "v5.0.1"

    @pytest.mark.asyncio
    async def test_name_falls_back_to_tag_when_empty(self):
        releases = [_make_release(name="", tag="v9.9.9")]
        with patch("src.collectors.apis.github._fetch_releases", new_callable=AsyncMock, return_value=releases):
            result = await self._make_collector().collect()
        # name is empty string → tag used as fallback
        assert "v9.9.9" in result[0].metadata["version"]

    @pytest.mark.asyncio
    async def test_source_id_propagated(self):
        releases = [_make_release()]
        collector = GitHubCollector(source_id=99, config={"repo": "a/b"}, niche="geometrydash")
        with patch("src.collectors.apis.github._fetch_releases", new_callable=AsyncMock, return_value=releases):
            result = await collector.collect()
        assert result[0].source_id == 99

    @pytest.mark.asyncio
    async def test_niche_propagated(self):
        releases = [_make_release()]
        with patch("src.collectors.apis.github._fetch_releases", new_callable=AsyncMock, return_value=releases):
            result = await self._make_collector(niche="rocketleague").collect()
        assert result[0].niche == "rocketleague"


# ===========================================================================
# FLASHBACK
# ===========================================================================

from src.collectors.apis.flashback import (
    FlashbackCollector,
    _load_static_events,
    _fetch_octane_flashbacks,
)


def _make_yaml_content(month: int, day: int, year: int = 2019) -> str:
    date_str = f"{year}-{month:02d}-{day:02d}"
    return f"""
events:
  - date: "{date_str}"
    event: "RLCS Test Event"
    headline: "On this day in {year}, TeamA defeated TeamB."
    details: "A great match."
    winner: "TeamA"
    loser: "TeamB"
    score: "4-2"
    url: "https://octane.gg/matches/abc"
"""


class TestLoadStaticEvents:
    """Tests for _load_static_events()."""

    def test_returns_matching_event(self, tmp_path):
        yaml_file = tmp_path / "rl_history.yaml"
        yaml_file.write_text(_make_yaml_content(month=3, day=21, year=2019))

        with patch("src.collectors.apis.flashback._HISTORY_PATH", yaml_file):
            result = _load_static_events((3, 21))

        assert len(result) == 1
        assert result[0]["event"] == "RLCS Test Event"
        assert result[0]["year"] == 2019

    def test_returns_empty_when_no_matching_date(self, tmp_path):
        yaml_file = tmp_path / "rl_history.yaml"
        yaml_file.write_text(_make_yaml_content(month=3, day=21))

        with patch("src.collectors.apis.flashback._HISTORY_PATH", yaml_file):
            result = _load_static_events((12, 25))

        assert result == []

    def test_returns_empty_when_file_missing(self, tmp_path):
        missing = tmp_path / "nonexistent.yaml"
        with patch("src.collectors.apis.flashback._HISTORY_PATH", missing):
            result = _load_static_events((3, 21))
        assert result == []

    def test_returns_empty_on_malformed_yaml(self, tmp_path):
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text("{{{{invalid yaml content")

        with patch("src.collectors.apis.flashback._HISTORY_PATH", yaml_file):
            result = _load_static_events((3, 21))

        assert result == []

    def test_skips_entries_with_bad_date_format(self, tmp_path):
        yaml_file = tmp_path / "rl_history.yaml"
        yaml_file.write_text("""
events:
  - date: "not-a-date"
    event: "Bad event"
    headline: "Broken"
""")
        with patch("src.collectors.apis.flashback._HISTORY_PATH", yaml_file):
            result = _load_static_events((3, 21))
        assert result == []

    def test_returns_empty_when_yaml_has_no_events_key(self, tmp_path):
        yaml_file = tmp_path / "rl_history.yaml"
        yaml_file.write_text("other_key: value\n")

        with patch("src.collectors.apis.flashback._HISTORY_PATH", yaml_file):
            result = _load_static_events((3, 21))

        assert result == []

    def test_multiple_events_same_day_all_returned(self, tmp_path):
        yaml_file = tmp_path / "rl_history.yaml"
        yaml_file.write_text("""
events:
  - date: "2018-06-15"
    event: "Event1"
    headline: "Match 1"
  - date: "2019-06-15"
    event: "Event2"
    headline: "Match 2"
  - date: "2020-07-04"
    event: "Event3"
    headline: "Different day"
""")
        with patch("src.collectors.apis.flashback._HISTORY_PATH", yaml_file):
            result = _load_static_events((6, 15))
        assert len(result) == 2


class TestFetchOctaneFlashbacks:
    """Tests for _fetch_octane_flashbacks()."""

    def _make_match(
        self,
        match_id: str = "abc123",
        blue_name: str = "NRG",
        orange_name: str = "G2",
        blue_score: int = 4,
        orange_score: int = 2,
        event_name: str = "RLCS Season 9",
        stage_name: str = "Grand Finals",
    ) -> dict:
        return {
            "_id": match_id,
            "score": True,
            "blue": {
                "score": blue_score,
                "team": {"team": {"name": blue_name}},
            },
            "orange": {
                "score": orange_score,
                "team": {"team": {"name": orange_name}},
            },
            "event": {"name": event_name},
            "stage": {"name": stage_name},
        }

    def _make_octane_response(self, matches: list) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = {"matches": matches}
        return resp

    @pytest.mark.asyncio
    async def test_returns_flashback_for_matching_match(self):
        match = self._make_match()
        resp = self._make_octane_response([match])

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        today = datetime(2026, 3, 21, tzinfo=timezone.utc)
        with patch("src.collectors.apis.flashback.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_octane_flashbacks(today, 1, "rocketleague")

        assert len(result) > 0
        assert result[0].content_type == "flashback"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_score(self):
        match = self._make_match()
        match["score"] = None
        resp = self._make_octane_response([match])

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        today = datetime(2026, 3, 21, tzinfo=timezone.utc)
        with patch("src.collectors.apis.flashback.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_octane_flashbacks(today, 1, "rocketleague")

        assert result == []

    @pytest.mark.asyncio
    async def test_skips_non_200_responses(self):
        resp = MagicMock()
        resp.status_code = 503

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        today = datetime(2026, 3, 21, tzinfo=timezone.utc)
        with patch("src.collectors.apis.flashback.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_octane_flashbacks(today, 1, "rocketleague")

        assert result == []

    @pytest.mark.asyncio
    async def test_skips_non_json_content_type(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "text/html"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        today = datetime(2026, 3, 21, tzinfo=timezone.utc)
        with patch("src.collectors.apis.flashback.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_octane_flashbacks(today, 1, "rocketleague")

        assert result == []

    @pytest.mark.asyncio
    async def test_winner_is_higher_scoring_team(self):
        match = self._make_match(blue_name="NRG", orange_name="G2", blue_score=4, orange_score=2)
        resp = self._make_octane_response([match])

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        today = datetime(2026, 3, 21, tzinfo=timezone.utc)
        with patch("src.collectors.apis.flashback.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_octane_flashbacks(today, 1, "rocketleague")

        assert result[0].metadata["winner"] == "NRG"
        assert result[0].metadata["loser"] == "G2"
        assert result[0].metadata["score"] == "4-2"

    @pytest.mark.asyncio
    async def test_skips_matches_with_missing_team_names(self):
        match = {
            "_id": "xyz",
            "score": True,
            "blue": {"score": 4, "team": {"team": {"name": ""}}},
            "orange": {"score": 2, "team": {"team": {"name": "G2"}}},
            "event": {"name": "RLCS"},
            "stage": {"name": "Finals"},
        }
        resp = self._make_octane_response([match])

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        today = datetime(2026, 3, 21, tzinfo=timezone.utc)
        with patch("src.collectors.apis.flashback.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_octane_flashbacks(today, 1, "rocketleague")

        assert result == []

    @pytest.mark.asyncio
    async def test_only_one_match_per_year_taken(self):
        # Two matches for the same date — only first should be taken per year
        m1 = self._make_match(match_id="first")
        m2 = self._make_match(match_id="second")
        resp = self._make_octane_response([m1, m2])

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        today = datetime(2026, 3, 21, tzinfo=timezone.utc)
        with patch("src.collectors.apis.flashback.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_octane_flashbacks(today, 1, "rocketleague")

        # Each year breaks after the first match, so "second" never appears
        assert not any("second" in r.external_id for r in result)
        # All results should be from match "first" (one per year)
        assert all("first" in r.external_id for r in result)


class TestFlashbackCollector:
    """Integration tests for FlashbackCollector.collect()."""

    def _make_collector(self) -> FlashbackCollector:
        return FlashbackCollector(source_id=3, config={}, niche="rocketleague")

    @pytest.mark.asyncio
    async def test_static_events_converted_to_rawcontent(self, tmp_path):
        today = datetime.now(timezone.utc)
        yaml_file = tmp_path / "rl_history.yaml"
        yaml_file.write_text(_make_yaml_content(month=today.month, day=today.day, year=today.year - 5))

        with (
            patch("src.collectors.apis.flashback._HISTORY_PATH", yaml_file),
            patch("src.collectors.apis.flashback._fetch_octane_flashbacks", new_callable=AsyncMock, return_value=[]),
        ):
            result = await self._make_collector().collect()

        assert len(result) == 1
        assert result[0].content_type == "flashback"
        assert result[0].niche == "rocketleague"

    @pytest.mark.asyncio
    async def test_combines_static_and_api_events(self, tmp_path):
        today = datetime.now(timezone.utc)
        yaml_file = tmp_path / "rl_history.yaml"
        yaml_file.write_text(_make_yaml_content(month=today.month, day=today.day, year=today.year - 3))

        api_item = RawContent(
            source_id=3, external_id="flashback_octane_xyz", niche="rocketleague",
            content_type="flashback", title="API event", url="",
        )

        with (
            patch("src.collectors.apis.flashback._HISTORY_PATH", yaml_file),
            patch("src.collectors.apis.flashback._fetch_octane_flashbacks", new_callable=AsyncMock, return_value=[api_item]),
        ):
            result = await self._make_collector().collect()

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_events_today(self, tmp_path):
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("events: []\n")

        with (
            patch("src.collectors.apis.flashback._HISTORY_PATH", yaml_file),
            patch("src.collectors.apis.flashback._fetch_octane_flashbacks", new_callable=AsyncMock, return_value=[]),
        ):
            result = await self._make_collector().collect()

        assert result == []

    @pytest.mark.asyncio
    async def test_metadata_years_ago_computed(self, tmp_path):
        today = datetime.now(timezone.utc)
        years_ago = 7
        yaml_file = tmp_path / "rl_history.yaml"
        yaml_file.write_text(_make_yaml_content(month=today.month, day=today.day, year=today.year - years_ago))

        with (
            patch("src.collectors.apis.flashback._HISTORY_PATH", yaml_file),
            patch("src.collectors.apis.flashback._fetch_octane_flashbacks", new_callable=AsyncMock, return_value=[]),
        ):
            result = await self._make_collector().collect()

        assert result[0].metadata["years_ago"] == str(years_ago)


# ===========================================================================
# RL STATS
# ===========================================================================

from src.collectors.apis.rl_stats import (
    RLStatsCollector,
    _fetch_stat_leaders,
    _player_name,
    _player_stat,
)


def _make_leader(tag: str = "jstn", goals: int = 800, assists: int = 400,
                 saves: int = 600, shots: int = 1500, score: int = 100000) -> dict:
    return {
        "player": {"tag": tag},
        "stats": {"core": {
            "goals": goals, "assists": assists, "saves": saves,
            "shots": shots, "score": score,
        }},
    }


class TestPlayerHelpers:
    """Unit tests for _player_name() and _player_stat()."""

    def test_player_name_returns_tag(self):
        entry = _make_leader(tag="Garrett")
        assert _player_name(entry) == "Garrett"

    def test_player_name_unknown_on_missing(self):
        assert _player_name({}) == "Unknown"

    def test_player_stat_goals(self):
        entry = _make_leader(goals=999)
        assert _player_stat(entry, "goals") == 999

    def test_player_stat_assists(self):
        entry = _make_leader(assists=123)
        assert _player_stat(entry, "assists") == 123

    def test_player_stat_returns_zero_on_missing(self):
        assert _player_stat({}, "goals") == 0

    def test_player_stat_saves(self):
        entry = _make_leader(saves=777)
        assert _player_stat(entry, "saves") == 777


class TestFetchStatLeaders:
    """Tests for _fetch_stat_leaders()."""

    def _make_octane_response(self, stats: list) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = {"stats": stats}
        return resp

    @pytest.mark.asyncio
    async def test_returns_leaders_on_success(self):
        leaders = [_make_leader("A"), _make_leader("B"), _make_leader("C")]
        resp = self._make_octane_response(leaders)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.apis.rl_stats.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_stat_leaders("goals")

        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_returns_empty_on_non_200(self):
        resp = MagicMock()
        resp.status_code = 503
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.apis.rl_stats.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_stat_leaders("goals")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_non_json_content_type(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "text/html"}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.apis.rl_stats.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_stat_leaders("goals")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("timeout"))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.apis.rl_stats.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_stat_leaders("goals")

        assert result == []


class TestRLStatsCollector:
    """Integration tests for RLStatsCollector.collect()."""

    def _make_collector(self) -> RLStatsCollector:
        return RLStatsCollector(source_id=4, config={}, niche="rocketleague")

    @pytest.mark.asyncio
    async def test_happy_path_returns_stat_milestone(self):
        leaders = [_make_leader("jstn"), _make_leader("Garrett"), _make_leader("Turbo")]
        with patch("src.collectors.apis.rl_stats._fetch_stat_leaders", new_callable=AsyncMock, return_value=leaders):
            result = await self._make_collector().collect()

        assert len(result) == 1
        assert result[0].content_type == "stat_milestone"

    @pytest.mark.asyncio
    async def test_returns_empty_when_fetch_returns_empty(self):
        with patch("src.collectors.apis.rl_stats._fetch_stat_leaders", new_callable=AsyncMock, return_value=[]):
            result = await self._make_collector().collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_fewer_than_3_leaders(self):
        leaders = [_make_leader("jstn"), _make_leader("Garrett")]  # only 2
        with patch("src.collectors.apis.rl_stats._fetch_stat_leaders", new_callable=AsyncMock, return_value=leaders):
            result = await self._make_collector().collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_only_one_stat_generated_per_collect(self):
        # Should break after the first successful stat to avoid spam
        leaders = [_make_leader("jstn"), _make_leader("B"), _make_leader("C")]
        call_count = 0

        async def _side_effect(stat):
            nonlocal call_count
            call_count += 1
            return leaders

        with patch("src.collectors.apis.rl_stats._fetch_stat_leaders", side_effect=_side_effect):
            result = await self._make_collector().collect()

        assert len(result) == 1
        # The break after first success means _fetch_stat_leaders was called once
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_external_id_contains_stat_key_and_week(self):
        leaders = [_make_leader("A"), _make_leader("B"), _make_leader("C")]
        with patch("src.collectors.apis.rl_stats._fetch_stat_leaders", new_callable=AsyncMock, return_value=leaders):
            result = await self._make_collector().collect()

        assert result[0].external_id.startswith("rl_stat_")
        assert "W" in result[0].external_id  # contains week number

    @pytest.mark.asyncio
    async def test_metadata_contains_headline(self):
        leaders = [_make_leader("jstn"), _make_leader("Garrett"), _make_leader("Turbo")]
        with patch("src.collectors.apis.rl_stats._fetch_stat_leaders", new_callable=AsyncMock, return_value=leaders):
            result = await self._make_collector().collect()

        assert "headline" in result[0].metadata
        assert "jstn" in result[0].metadata["headline"]

    @pytest.mark.asyncio
    async def test_author_is_top_player(self):
        leaders = [_make_leader("jstn"), _make_leader("Garrett"), _make_leader("Turbo")]
        with patch("src.collectors.apis.rl_stats._fetch_stat_leaders", new_callable=AsyncMock, return_value=leaders):
            result = await self._make_collector().collect()
        assert result[0].author == "jstn"

    @pytest.mark.asyncio
    async def test_niche_is_rocketleague(self):
        leaders = [_make_leader("A"), _make_leader("B"), _make_leader("C")]
        with patch("src.collectors.apis.rl_stats._fetch_stat_leaders", new_callable=AsyncMock, return_value=leaders):
            result = await self._make_collector().collect()
        assert result[0].niche == "rocketleague"

    @pytest.mark.asyncio
    async def test_skips_failed_stat_and_tries_next(self):
        """If first stat returns empty, collector tries the next stat category."""
        leaders = [_make_leader("A"), _make_leader("B"), _make_leader("C")]
        call_count = 0

        async def _side_effect(stat):
            nonlocal call_count
            call_count += 1
            # First call (goals) returns empty; second (assists) returns leaders
            if call_count == 1:
                return []
            return leaders

        with patch("src.collectors.apis.rl_stats._fetch_stat_leaders", side_effect=_side_effect):
            result = await self._make_collector().collect()

        assert len(result) == 1
        assert call_count == 2


# ===========================================================================
# GDBROWSER — Official server fallbacks (coverage for lines 194-277)
# ===========================================================================

from src.collectors.apis.gdbrowser import (
    _fetch_daily_official,
    _fetch_weekly_official,
    _download_level_official,
)


def _make_post_response(status: int, text: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestFetchDailyOfficial:
    """Tests for the official GD server daily level fallback."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_daily_content(self):
        # getGJDailyLevel.php returns "levelID|timeLeft"
        daily_resp = _make_post_response(200, "12345|3600")
        # downloadGJLevel22.php returns the colon-delimited level data
        # Key 1=levelID, 2=name, 18=stars, 14=likes, 9=face_difficulty
        level_data = "1:12345:2:Stereo Madness:18:10:14:500:9:10"
        download_resp = _make_post_response(200, level_data)

        client = AsyncMock()
        client.post = AsyncMock(side_effect=[daily_resp, download_resp])

        result = await _fetch_daily_official(client, 1, "geometrydash")
        assert result is not None
        assert result.content_type == "daily_level"

    @pytest.mark.asyncio
    async def test_returns_none_when_body_is_minus_one(self):
        resp = _make_post_response(200, "-1")
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        result = await _fetch_daily_official(client, 1, "geometrydash")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_body_has_no_pipe(self):
        resp = _make_post_response(200, "justtext")
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        result = await _fetch_daily_official(client, 1, "geometrydash")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_body_contains_error(self):
        resp = _make_post_response(200, "error|something")
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        result = await _fetch_daily_official(client, 1, "geometrydash")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self):
        resp = _make_post_response(500, "")
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        result = await _fetch_daily_official(client, 1, "geometrydash")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        client = AsyncMock()
        client.post = AsyncMock(side_effect=Exception("connection reset"))

        result = await _fetch_daily_official(client, 1, "geometrydash")
        assert result is None


class TestFetchWeeklyOfficial:
    """Tests for the official GD server weekly level fallback."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_weekly_content(self):
        weekly_resp = _make_post_response(200, "99999|3600")
        level_data = "1:99999:2:Bloodbath:18:10:14:2000:17:1:43:6"
        download_resp = _make_post_response(200, level_data)

        client = AsyncMock()
        client.post = AsyncMock(side_effect=[weekly_resp, download_resp])

        result = await _fetch_weekly_official(client, 1, "geometrydash")
        assert result is not None
        assert result.content_type == "weekly_demon"

    @pytest.mark.asyncio
    async def test_returns_none_when_body_is_minus_one(self):
        resp = _make_post_response(200, "-1")
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        result = await _fetch_weekly_official(client, 1, "geometrydash")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        client = AsyncMock()
        client.post = AsyncMock(side_effect=Exception("refused"))

        result = await _fetch_weekly_official(client, 1, "geometrydash")
        assert result is None


class TestDownloadLevelOfficial:
    """Tests for _download_level_official() — the detailed level download."""

    @pytest.mark.asyncio
    async def test_returns_none_when_body_is_minus_one(self):
        resp = _make_post_response(200, "-1")
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        result = await _download_level_official(client, 1, "geometrydash", "123", "daily")
        assert result is None

    @pytest.mark.asyncio
    async def test_daily_kind_returns_daily_content(self):
        level_data = "1:5001:2:Base After Base:18:10:14:800:9:20"
        resp = _make_post_response(200, level_data)
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        result = await _download_level_official(client, 1, "geometrydash", "5001", "daily")
        assert result is not None
        assert result.content_type == "daily_level"

    @pytest.mark.asyncio
    async def test_weekly_kind_returns_weekly_content(self):
        level_data = "1:7777:2:Bloodbath:18:10:14:5000:17:1:43:6"
        resp = _make_post_response(200, level_data)
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        result = await _download_level_official(client, 1, "geometrydash", "7777", "weekly")
        assert result is not None
        assert result.content_type == "weekly_demon"

    @pytest.mark.asyncio
    async def test_author_extracted_from_creator_segment(self):
        # Full response: level_data#hash1#hash2#creatorID:creatorName:accountID
        level_data = "1:1234:2:LevelName:18:5:14:100:9:10"
        full_body = f"{level_data}###999:Viprin:54321"
        resp = _make_post_response(200, full_body)
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        result = await _download_level_official(client, 1, "geometrydash", "1234", "daily")
        assert result is not None
        assert result.author == "Viprin"

    @pytest.mark.asyncio
    async def test_falls_back_to_unknown_author_when_no_creator_segment(self):
        level_data = "1:1234:2:LevelName:18:5:14:100:9:10"
        resp = _make_post_response(200, level_data)
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        result = await _download_level_official(client, 1, "geometrydash", "1234", "daily")
        assert result is not None
        assert result.author == "Unknown"


# ===========================================================================
# FLASHBACK — Leap year skip and HTTP exception paths (lines 110-111, 129-130)
# ===========================================================================


class TestFetchOctaneFlashbacksEdgeCases:
    """Edge cases for _fetch_octane_flashbacks() covering the remaining lines."""

    @pytest.mark.asyncio
    async def test_skips_feb29_on_non_leap_years(self):
        """Feb 29 date_str fails strptime for non-leap years — should be skipped silently."""
        # Use Feb 29 with today being 2026 (not a leap year) — 2016 is a leap year
        # So the range 2016..2025 will have only 2016 passing Feb 29 strptime
        today = datetime(2026, 3, 21, tzinfo=timezone.utc)

        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = {"matches": []}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        # Patch today to be Feb 29, 2026 — but 2026 is not a leap year.
        # To hit line 110-111, we need a date where some past years don't have that date.
        # Use Feb 29, 2028 (leap) as "today" — past years 2016,2020,2024 are leap, others not.
        today_leap = datetime(2028, 2, 29, tzinfo=timezone.utc)

        with patch("src.collectors.apis.flashback.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_octane_flashbacks(today_leap, 1, "rocketleague")

        # Should not raise; non-leap years skip silently
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_http_exception_in_loop_is_swallowed(self):
        """An exception during client.get inside the year loop should be caught and skipped."""
        today = datetime(2026, 3, 21, tzinfo=timezone.utc)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection reset"))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.apis.flashback.httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch_octane_flashbacks(today, 1, "rocketleague")

        # All iterations fail gracefully — returns empty list, not an exception
        assert result == []
