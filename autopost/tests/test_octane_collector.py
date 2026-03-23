"""
Unit tests for src/collectors/apis/octane.py
OctaneCollector, _fetch_results, _fetch_upcoming, _today.
All HTTP calls are mocked — no network access.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from src.collectors.apis.octane import (
    OctaneCollector,
    _fetch_results,
    _fetch_upcoming,
    _today,
)
from src.collectors.base import RawContent


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_match(
    match_id: str = "abc123",
    blue_name: str = "NRG",
    orange_name: str = "G2",
    blue_score: int = 4,
    orange_score: int = 2,
    event_name: str = "RLCS World Championship",
    stage_name: str = "Grand Final",
    has_score: bool = True,
    date: str = "2024-11-15T18:00:00Z",
) -> dict:
    match = {
        "_id": match_id,
        "blue": {
            "team": {"team": {"name": blue_name}},
            "score": blue_score if has_score else None,
        },
        "orange": {
            "team": {"team": {"name": orange_name}},
            "score": orange_score if has_score else None,
        },
        "event": {"name": event_name},
        "stage": {"name": stage_name},
        "date": date,
    }
    if has_score:
        match["score"] = {"blue": blue_score, "orange": orange_score}
    return match


def _make_response(status: int, payload: dict, content_type: str = "application/json") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.headers = {"content-type": content_type}
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _make_async_client(response: MagicMock) -> MagicMock:
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return mock_ctx


# ── _today() ───────────────────────────────────────────────────────────────────

class TestToday:
    def test_returns_iso_format_with_z(self):
        result = _today()
        assert result.endswith("Z")
        assert "T" in result

    def test_format_matches_pattern(self):
        import re
        result = _today()
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", result)


# ── _fetch_results() ───────────────────────────────────────────────────────────

class TestFetchResults:

    @pytest.mark.asyncio
    async def test_returns_raw_content_for_completed_match(self):
        match = _make_match(
            match_id="m1",
            blue_name="NRG",
            orange_name="G2",
            blue_score=4,
            orange_score=2,
        )
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        assert len(results) == 1
        item = results[0]
        assert isinstance(item, RawContent)
        assert item.source_id == 1
        assert item.niche == "rocketleague"
        assert item.content_type == "esports_result"
        assert "m1" in item.external_id
        assert "NRG" in item.title
        assert "G2" in item.title

    @pytest.mark.asyncio
    async def test_skips_match_without_score(self):
        match = _make_match(has_score=False)
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        assert results == []

    @pytest.mark.asyncio
    async def test_winner_is_higher_scorer(self):
        match = _make_match(
            blue_name="NRG",
            orange_name="G2",
            blue_score=4,
            orange_score=1,
        )
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        item = results[0]
        assert item.metadata["winner"] == "NRG"
        assert item.metadata["loser"] == "G2"

    @pytest.mark.asyncio
    async def test_orange_wins_when_higher_score(self):
        match = _make_match(
            blue_name="NRG",
            orange_name="G2",
            blue_score=1,
            orange_score=4,
        )
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        item = results[0]
        assert item.metadata["winner"] == "G2"
        assert item.metadata["loser"] == "NRG"

    @pytest.mark.asyncio
    async def test_score_string_format(self):
        match = _make_match(blue_score=4, orange_score=2)
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        assert results[0].metadata["score"] == "4-2"

    @pytest.mark.asyncio
    async def test_event_name_truncated_to_20_chars(self):
        long_event = "RLCS 2024 World Championship Grand Finals"
        match = _make_match(event_name=long_event)
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        assert len(results[0].metadata["event_short"]) <= 20

    @pytest.mark.asyncio
    async def test_event_short_not_truncated_when_short(self):
        short_event = "RLCS"
        match = _make_match(event_name=short_event)
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        assert results[0].metadata["event_short"] == "RLCS"

    @pytest.mark.asyncio
    async def test_url_contains_match_id(self):
        match = _make_match(match_id="xyz789")
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        assert "xyz789" in results[0].url

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        resp = _make_response(500, {})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_non_json_content_type(self):
        resp = _make_response(200, {}, content_type="text/html")
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_network_exception(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_matches_key(self):
        resp = _make_response(200, {})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        assert results == []

    @pytest.mark.asyncio
    async def test_missing_team_data_uses_defaults(self):
        """Match missing blue/orange team nesting should fall back to Blue/Orange."""
        match = {
            "_id": "fallback_id",
            "score": {"blue": 3, "orange": 0},
            "blue": {"score": 3},
            "orange": {"score": 0},
            "event": {"name": "RLCS"},
            "stage": {"name": ""},
        }
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        assert len(results) == 1
        assert results[0].metadata["team1"] == "Blue"
        assert results[0].metadata["team2"] == "Orange"

    @pytest.mark.asyncio
    async def test_processes_multiple_matches(self):
        matches = [_make_match(match_id=f"m{i}") for i in range(5)]
        resp = _make_response(200, {"matches": matches})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_metadata_has_emoji(self):
        match = _make_match()
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        assert results[0].metadata["emoji"] == "🏆"

    @pytest.mark.asyncio
    async def test_external_id_prefix(self):
        match = _make_match(match_id="test_id")
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=1, niche="rocketleague")

        assert results[0].external_id == "octane_result_test_id"

    @pytest.mark.asyncio
    async def test_niche_preserved_in_content(self):
        match = _make_match()
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_results(client, source_id=99, niche="rocketleague")

        assert results[0].source_id == 99
        assert results[0].niche == "rocketleague"


# ── _fetch_upcoming() ──────────────────────────────────────────────────────────

class TestFetchUpcoming:

    @pytest.mark.asyncio
    async def test_returns_raw_content_for_upcoming_match(self):
        match = {
            "_id": "up1",
            "blue": {"team": {"team": {"name": "NRG"}}},
            "orange": {"team": {"team": {"name": "G2"}}},
            "event": {"name": "RLCS World Championship"},
            "stage": {"name": "Semifinals"},
            "date": "2024-12-01T15:00:00Z",
        }
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_upcoming(client, source_id=2, niche="rocketleague")

        assert len(results) == 1
        item = results[0]
        assert item.content_type == "esports_matchup"
        assert item.external_id == "octane_upcoming_up1"
        assert "NRG" in item.title
        assert "G2" in item.title
        assert item.source_id == 2

    @pytest.mark.asyncio
    async def test_date_formatted_correctly(self):
        match = {
            "_id": "up2",
            "blue": {},
            "orange": {},
            "event": {"name": "RLCS"},
            "stage": {"name": ""},
            "date": "2024-12-01T15:30:00Z",
        }
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_upcoming(client, source_id=1, niche="rocketleague")

        assert results[0].metadata["time"] == "2024-12-01 15:30 UTC"

    @pytest.mark.asyncio
    async def test_missing_date_shows_tbd(self):
        match = {
            "_id": "up3",
            "blue": {},
            "orange": {},
            "event": {"name": "RLCS"},
            "stage": {"name": ""},
        }
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_upcoming(client, source_id=1, niche="rocketleague")

        assert results[0].metadata["time"] == "TBD"

    @pytest.mark.asyncio
    async def test_missing_team_names_use_tbd(self):
        match = {
            "_id": "up4",
            "blue": {},
            "orange": {},
            "event": {"name": "RLCS"},
            "stage": {"name": ""},
            "date": "2024-12-01T10:00:00Z",
        }
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_upcoming(client, source_id=1, niche="rocketleague")

        assert results[0].metadata["team1"] == "TBD"
        assert results[0].metadata["team2"] == "TBD"

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        resp = _make_response(503, {})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_upcoming(client, source_id=1, niche="rocketleague")

        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_non_json_content_type(self):
        resp = _make_response(200, {}, content_type="text/html; charset=utf-8")
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_upcoming(client, source_id=1, niche="rocketleague")

        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_network_exception(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        results = await _fetch_upcoming(client, source_id=1, niche="rocketleague")

        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_matches_key(self):
        resp = _make_response(200, {})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_upcoming(client, source_id=1, niche="rocketleague")

        assert results == []

    @pytest.mark.asyncio
    async def test_url_contains_match_id(self):
        match = {
            "_id": "upmatch99",
            "blue": {},
            "orange": {},
            "event": {"name": "RLCS"},
            "stage": {"name": ""},
            "date": "2024-12-01T10:00:00Z",
        }
        resp = _make_response(200, {"matches": [match]})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_upcoming(client, source_id=1, niche="rocketleague")

        assert "upmatch99" in results[0].url

    @pytest.mark.asyncio
    async def test_processes_multiple_upcoming_matches(self):
        matches = [
            {
                "_id": f"u{i}",
                "blue": {},
                "orange": {},
                "event": {"name": "RLCS"},
                "stage": {"name": ""},
                "date": "2024-12-01T10:00:00Z",
            }
            for i in range(3)
        ]
        resp = _make_response(200, {"matches": matches})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        results = await _fetch_upcoming(client, source_id=1, niche="rocketleague")

        assert len(results) == 3


# ── OctaneCollector.collect() ──────────────────────────────────────────────────

class TestOctaneCollector:

    def test_init_default_niche(self):
        collector = OctaneCollector(source_id=1, config={})
        assert collector.niche == "rocketleague"
        assert collector.source_id == 1

    def test_init_custom_niche(self):
        collector = OctaneCollector(source_id=5, config={}, niche="custom")
        assert collector.niche == "custom"

    def test_base_url_defaults_to_http(self):
        collector = OctaneCollector(source_id=1, config={})
        assert collector.base_url.startswith("http://")

    def test_https_base_url_forced_to_http(self):
        collector = OctaneCollector(
            source_id=1, config={"base_url": "https://zsr.octane.gg"}
        )
        assert collector.base_url.startswith("http://")
        assert not collector.base_url.startswith("https://")

    def test_custom_http_base_url_preserved(self):
        collector = OctaneCollector(
            source_id=1, config={"base_url": "http://my-mirror.example.com"}
        )
        assert collector.base_url == "http://my-mirror.example.com"

    @pytest.mark.asyncio
    async def test_collect_combines_results_and_upcoming(self):
        result_match = _make_match(match_id="r1")
        upcoming_match = {
            "_id": "u1",
            "blue": {"team": {"team": {"name": "NRG"}}},
            "orange": {"team": {"team": {"name": "G2"}}},
            "event": {"name": "RLCS"},
            "stage": {"name": ""},
            "date": "2024-12-01T10:00:00Z",
        }

        results_resp = _make_response(200, {"matches": [result_match]})
        upcoming_resp = _make_response(200, {"matches": [upcoming_match]})

        call_count = 0

        async def mock_get(path, params=None):
            nonlocal call_count
            call_count += 1
            if "desc" in str(params):
                return results_resp
            return upcoming_resp

        mock_client = AsyncMock()
        mock_client.get = mock_get

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        collector = OctaneCollector(source_id=1, config={})

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            items = await collector.collect()

        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_collect_returns_empty_on_all_failures(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("down"))

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        collector = OctaneCollector(source_id=1, config={})

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            items = await collector.collect()

        assert items == []
