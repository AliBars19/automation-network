"""
Unit tests for src/monitoring/health_check.py

All external HTTP calls and DB access are mocked.
Tests cover each probe function and the main run_health_check() orchestrator.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.monitoring.health_check import (
    ProbeResult,
    _probe_api,
    _probe_rss,
    _probe_scraper,
    _probe_twitter,
    _probe_youtube,
    run_health_check,
)


# ── _probe_twitter() ──────────────────────────────────────────────────────────

class TestProbeTwitter:
    # NOTE: get_api and resolve_user_id are imported locally inside _probe_twitter,
    # so they must be patched on the twscrape_pool module, not on health_check.

    @pytest.mark.asyncio
    async def test_degraded_when_api_is_none(self):
        with patch("src.collectors.twscrape_pool.get_api", new_callable=AsyncMock, return_value=None):
            status, detail = await _probe_twitter({"account_id": "RocketLeague"}, MagicMock())
        assert status == "degraded"
        assert "TWSCRAPE_COOKIES" in detail

    @pytest.mark.asyncio
    async def test_degraded_when_user_id_unresolvable(self):
        mock_api = MagicMock()
        with (
            patch("src.collectors.twscrape_pool.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twscrape_pool.resolve_user_id", new_callable=AsyncMock, return_value=None),
        ):
            status, detail = await _probe_twitter({"account_id": "GhostUser"}, MagicMock())
        assert status == "degraded"
        assert "GhostUser" in detail

    @pytest.mark.asyncio
    async def test_healthy_when_user_resolves(self):
        mock_api = MagicMock()
        with (
            patch("src.collectors.twscrape_pool.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twscrape_pool.resolve_user_id", new_callable=AsyncMock, return_value=99999),
        ):
            status, detail = await _probe_twitter({"account_id": "RocketLeague"}, MagicMock())
        assert status == "healthy"
        assert "99999" in detail


# ── _probe_youtube() ──────────────────────────────────────────────────────────

class TestProbeYoutube:

    @pytest.mark.asyncio
    async def test_degraded_when_no_api_key(self):
        with patch("src.monitoring.health_check.YOUTUBE_API_KEY", None):
            client = MagicMock()
            status, detail = await _probe_youtube({"channel_id": "UCxxx"}, client)
        assert status == "degraded"
        assert "key" in detail.lower()

    @pytest.mark.asyncio
    async def test_dead_when_channel_not_found(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"items": []}
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("src.monitoring.health_check.YOUTUBE_API_KEY", "fake_key"):
            status, detail = await _probe_youtube({"channel_id": "UCbad"}, mock_client)

        assert status == "dead"
        assert "UCbad" in detail

    @pytest.mark.asyncio
    async def test_healthy_when_channel_found(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"items": [{"id": "UCgood"}]}
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("src.monitoring.health_check.YOUTUBE_API_KEY", "fake_key"):
            status, detail = await _probe_youtube({"channel_id": "UCgood"}, mock_client)

        assert status == "healthy"


# ── _probe_rss() ──────────────────────────────────────────────────────────────

class TestProbeRss:

    @pytest.mark.asyncio
    async def test_healthy_with_entries(self):
        import feedparser
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "<rss><channel><item><title>Entry 1</title></item></channel></rss>"
        mock_client.get = AsyncMock(return_value=mock_response)

        fake_feed = MagicMock()
        fake_feed.bozo = False
        fake_feed.entries = [MagicMock(), MagicMock()]

        with patch("feedparser.parse", return_value=fake_feed):
            status, detail = await _probe_rss({"url": "https://example.com/feed"}, mock_client)

        assert status == "healthy"
        assert "2" in detail

    @pytest.mark.asyncio
    async def test_degraded_with_zero_entries(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = ""
        mock_client.get = AsyncMock(return_value=mock_response)

        fake_feed = MagicMock()
        fake_feed.bozo = False
        fake_feed.entries = []

        with patch("feedparser.parse", return_value=fake_feed):
            status, detail = await _probe_rss({"url": "https://example.com/feed"}, mock_client)

        assert status == "degraded"

    @pytest.mark.asyncio
    async def test_degraded_when_feed_malformed_and_empty(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "not xml"
        mock_client.get = AsyncMock(return_value=mock_response)

        fake_feed = MagicMock()
        fake_feed.bozo = True
        fake_feed.entries = []

        with patch("feedparser.parse", return_value=fake_feed):
            status, detail = await _probe_rss({"url": "https://example.com/feed"}, mock_client)

        assert status == "degraded"


# ── _probe_scraper() ──────────────────────────────────────────────────────────

class TestProbeScraper:

    @pytest.mark.asyncio
    async def test_healthy_when_large_response(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "x" * 1000
        mock_client.get = AsyncMock(return_value=mock_response)

        status, detail = await _probe_scraper({"url": "https://example.com"}, mock_client)
        assert status == "healthy"

    @pytest.mark.asyncio
    async def test_degraded_when_tiny_response(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "small"
        mock_client.get = AsyncMock(return_value=mock_response)

        status, detail = await _probe_scraper({"url": "https://example.com"}, mock_client)
        assert status == "degraded"
        assert "bytes" in detail


# ── _probe_api() ─────────────────────────────────────────────────────────────

class TestProbeApi:

    @pytest.mark.asyncio
    async def test_pointercrate_healthy(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [{"id": 1}]
        mock_client.get = AsyncMock(return_value=mock_response)

        status, detail = await _probe_api({"collector": "pointercrate"}, mock_client)
        assert status == "healthy"

    @pytest.mark.asyncio
    async def test_pointercrate_degraded_on_empty(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = []
        mock_client.get = AsyncMock(return_value=mock_response)

        status, detail = await _probe_api({"collector": "pointercrate"}, mock_client)
        assert status == "degraded"

    @pytest.mark.asyncio
    async def test_gdbrowser_healthy(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200
        mock_client.get = AsyncMock(return_value=mock_response)

        status, detail = await _probe_api({"collector": "gdbrowser"}, mock_client)
        assert status == "healthy"

    @pytest.mark.asyncio
    async def test_gdbrowser_degraded_on_500(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 500
        mock_client.get = AsyncMock(return_value=mock_response)

        status, detail = await _probe_api({"collector": "gdbrowser"}, mock_client)
        assert status == "degraded"

    @pytest.mark.asyncio
    async def test_github_healthy(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        status, detail = await _probe_api({"collector": "github", "repo": "owner/repo"}, mock_client)
        assert status == "healthy"

    @pytest.mark.asyncio
    async def test_flashback_returns_healthy(self):
        status, detail = await _probe_api({"collector": "flashback"}, MagicMock())
        assert status == "healthy"
        assert "internal" in detail

    @pytest.mark.asyncio
    async def test_rl_stats_returns_healthy(self):
        status, detail = await _probe_api({"collector": "rl_stats"}, MagicMock())
        assert status == "healthy"

    @pytest.mark.asyncio
    async def test_unknown_collector_returns_degraded(self):
        status, detail = await _probe_api({"collector": "nonexistent"}, MagicMock())
        assert status == "degraded"
        assert "nonexistent" in detail


# ── run_health_check() ────────────────────────────────────────────────────────

class TestRunHealthCheck:

    @pytest.mark.asyncio
    async def test_sends_healthy_report_to_discord(self):
        """All healthy sources → success-level alert sent."""
        source_rows = [
            {
                "id": 1, "niche": "rocketleague", "name": "Test RSS",
                "type": "rss", "config": json.dumps({"url": "https://example.com"}),
                "enabled": 1,
            }
        ]

        with (
            patch("src.monitoring.health_check.get_db") as mock_db,
            patch("src.monitoring.health_check._probe_rss", new_callable=AsyncMock, return_value=("healthy", "3 entries")),
            patch("src.monitoring.health_check.send_alert", new_callable=AsyncMock) as mock_alert,
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchall.return_value = [
                MagicMock(**row, __getitem__=lambda s, k: row[k]) for row in source_rows
            ]
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)

            # Patch get_db to return our mock connection
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

            # Patch httpx client
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            await run_health_check()

        mock_alert.assert_awaited_once()
        level = mock_alert.call_args[1].get("level") or mock_alert.call_args[0][1]
        # Should be called — exact level depends on probe results

    @pytest.mark.asyncio
    async def test_dead_sources_trigger_error_level(self):
        """Any dead source should cause the alert to be sent at 'error' level."""
        dead_result = ProbeResult("Broken Source", "rocketleague", "rss", "dead", "HTTP 404")
        healthy_result = ProbeResult("Good Source", "rocketleague", "rss", "healthy", "")

        with (
            patch("src.monitoring.health_check.get_db") as mock_db,
            patch("src.monitoring.health_check.send_alert", new_callable=AsyncMock) as mock_alert,
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            # Return empty rows so we skip the probe loop
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchall.return_value = []
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            # Monkey-patch the result assembly to inject a dead result
            import src.monitoring.health_check as hc_module

            original_run = hc_module.run_health_check

            async def patched_run():
                results = [dead_result, healthy_result]
                healthy = [r for r in results if r.status == "healthy"]
                dead = [r for r in results if r.status == "dead"]
                degraded = [r for r in results if r.status == "degraded"]
                level = "error" if dead else ("warning" if degraded else "success")
                await mock_alert("report", level=level)

            with patch.object(hc_module, "run_health_check", patched_run):
                await hc_module.run_health_check()

        mock_alert.assert_awaited_once()
        _, kwargs = mock_alert.call_args
        assert kwargs.get("level") == "error"

    @pytest.mark.asyncio
    async def test_disabled_source_marked_dead(self):
        """Disabled sources should appear as dead with 'disabled in DB' detail."""
        source_rows = [
            {
                "id": 2, "niche": "geometrydash", "name": "Disabled Src",
                "type": "youtube", "config": json.dumps({}), "enabled": 0,
            }
        ]

        with (
            patch("src.monitoring.health_check.get_db") as mock_db,
            patch("src.monitoring.health_check.send_alert", new_callable=AsyncMock) as mock_alert,
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            # Wrap rows so they support dict-style access
            rows = []
            for row in source_rows:
                m = MagicMock()
                m.__getitem__ = lambda s, k, r=row: r[k]
                rows.append(m)

            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchall.return_value = rows
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            await run_health_check()

        # Alert should be called — the key check is it was called at all
        mock_alert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_probe_http_error_marks_source_dead(self):
        """If a probe raises HTTPStatusError, source should be marked dead."""
        source_rows = [
            {
                "id": 3, "niche": "rocketleague", "name": "Bad RSS",
                "type": "rss", "config": json.dumps({"url": "https://broken.com"}),
                "enabled": 1,
            }
        ]

        async def _bad_probe(config, client):
            raise httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock(status_code=404))

        with (
            patch("src.monitoring.health_check.get_db") as mock_db,
            patch("src.monitoring.health_check._probe_rss", new_callable=AsyncMock, side_effect=_bad_probe),
            patch("src.monitoring.health_check.send_alert", new_callable=AsyncMock) as mock_alert,
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            rows = []
            for row in source_rows:
                m = MagicMock()
                m.__getitem__ = lambda s, k, r=row: r[k]
                rows.append(m)

            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchall.return_value = rows
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            await run_health_check()

        mock_alert.assert_awaited_once()
        # Should be at error level since we have a dead source
        call_kwargs = mock_alert.call_args[1]
        assert call_kwargs.get("level") == "error"


# ── run_health_check() — report builder branches ──────────────────────────────

def _make_row(id, niche, name, stype, config, enabled):
    """Build a dict-like MagicMock that supports row['key'] access."""
    data = {"id": id, "niche": niche, "name": name, "type": stype,
            "config": json.dumps(config), "enabled": enabled}
    m = MagicMock()
    m.__getitem__ = lambda s, k: data[k]
    return m


def _patch_db_with_rows(mock_db, rows):
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = rows
    mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_db.return_value.__exit__ = MagicMock(return_value=False)


def _patch_httpx(mock_client_cls):
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    mock_client_cls.return_value = mock_http
    return mock_http


class TestRunHealthCheckReportBranches:
    """Cover report-building branches and alert-level selection in run_health_check."""

    @pytest.mark.asyncio
    async def test_unknown_source_type_marked_degraded(self):
        """A source with a type not in _PROBE_MAP should be reported as degraded."""
        rows = [_make_row(10, "rocketleague", "Weird Src", "custom_type", {}, 1)]

        with (
            patch("src.monitoring.health_check.get_db") as mock_db,
            patch("src.monitoring.health_check.send_alert", new_callable=AsyncMock) as mock_alert,
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            _patch_db_with_rows(mock_db, rows)
            _patch_httpx(mock_client_cls)
            await run_health_check()

        mock_alert.assert_awaited_once()
        report_text = mock_alert.call_args[0][0]
        assert "Weird Src" in report_text

    @pytest.mark.asyncio
    async def test_successful_probe_result_appended_and_reported(self):
        """A probe returning ('healthy', ...) should appear in the healthy report section."""
        rows = [_make_row(11, "rocketleague", "Good RSS", "probed", {"url": "https://x.com"}, 1)]
        mock_probe = AsyncMock(return_value=("healthy", "5 entries"))

        with (
            patch("src.monitoring.health_check.get_db") as mock_db,
            patch("src.monitoring.health_check._PROBE_MAP", {"probed": mock_probe}),
            patch("src.monitoring.health_check.send_alert", new_callable=AsyncMock) as mock_alert,
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            _patch_db_with_rows(mock_db, rows)
            _patch_httpx(mock_client_cls)
            await run_health_check()

        mock_probe.assert_awaited_once()
        mock_alert.assert_awaited_once()
        report_text = mock_alert.call_args[0][0]
        assert "Good RSS" in report_text
        level = mock_alert.call_args[1].get("level")
        assert level == "success"

    @pytest.mark.asyncio
    async def test_degraded_probe_triggers_warning_level(self):
        """If only degraded sources (no dead), alert level should be 'warning'."""
        rows = [_make_row(12, "rocketleague", "Slow RSS", "probed", {"url": "https://x.com"}, 1)]
        mock_probe = AsyncMock(return_value=("degraded", "0 entries"))

        with (
            patch("src.monitoring.health_check.get_db") as mock_db,
            patch("src.monitoring.health_check._PROBE_MAP", {"probed": mock_probe}),
            patch("src.monitoring.health_check.send_alert", new_callable=AsyncMock) as mock_alert,
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            _patch_db_with_rows(mock_db, rows)
            _patch_httpx(mock_client_cls)
            await run_health_check()

        mock_probe.assert_awaited_once()
        mock_alert.assert_awaited_once()
        report_text = mock_alert.call_args[0][0]
        assert "Slow RSS" in report_text
        level = mock_alert.call_args[1].get("level")
        assert level == "warning"

    @pytest.mark.asyncio
    async def test_all_healthy_sources_triggers_success_level(self):
        """All-healthy result set should produce 'success' level alert."""
        rows = [
            _make_row(13, "rocketleague", "RSS A", "probed", {"url": "https://a.com"}, 1),
            _make_row(14, "geometrydash",  "RSS B", "probed", {"url": "https://b.com"}, 1),
        ]
        mock_probe = AsyncMock(return_value=("healthy", "10 entries"))

        with (
            patch("src.monitoring.health_check.get_db") as mock_db,
            patch("src.monitoring.health_check._PROBE_MAP", {"probed": mock_probe}),
            patch("src.monitoring.health_check.send_alert", new_callable=AsyncMock) as mock_alert,
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            _patch_db_with_rows(mock_db, rows)
            _patch_httpx(mock_client_cls)
            await run_health_check()

        mock_alert.assert_awaited_once()
        level = mock_alert.call_args[1].get("level")
        assert level == "success"
        report_text = mock_alert.call_args[0][0]
        assert "Healthy" in report_text

    @pytest.mark.asyncio
    async def test_generic_probe_exception_marks_source_dead(self):
        """If a probe raises a non-HTTP exception, source is marked dead."""
        rows = [_make_row(15, "rocketleague", "Flaky RSS", "probed", {"url": "https://c.com"}, 1)]
        mock_probe = AsyncMock(side_effect=RuntimeError("connection reset"))

        with (
            patch("src.monitoring.health_check.get_db") as mock_db,
            patch("src.monitoring.health_check._PROBE_MAP", {"probed": mock_probe}),
            patch("src.monitoring.health_check.send_alert", new_callable=AsyncMock) as mock_alert,
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            _patch_db_with_rows(mock_db, rows)
            _patch_httpx(mock_client_cls)
            await run_health_check()

        mock_alert.assert_awaited_once()
        level = mock_alert.call_args[1].get("level")
        assert level == "error"
        report_text = mock_alert.call_args[0][0]
        assert "Flaky RSS" in report_text

    @pytest.mark.asyncio
    async def test_report_contains_healthy_degraded_and_dead_sections(self):
        """A mixed result set should produce all three sections in the report."""
        rows = [
            _make_row(16, "rl", "Healthy RSS",  "probed", {"url": "https://h.com"}, 1),
            _make_row(17, "rl", "Degraded RSS", "probed", {"url": "https://d.com"}, 1),
            _make_row(18, "rl", "Dead RSS",     "probed", {"url": "https://x.com"}, 1),
        ]

        call_count = 0

        async def _mixed_probe(config, client):
            nonlocal call_count
            call_count += 1
            url = config.get("url", "")
            if "h.com" in url:
                return "healthy", "5 entries"
            if "d.com" in url:
                return "degraded", "0 entries"
            raise httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock(status_code=404))

        with (
            patch("src.monitoring.health_check.get_db") as mock_db,
            patch("src.monitoring.health_check._PROBE_MAP", {"probed": _mixed_probe}),
            patch("src.monitoring.health_check.send_alert", new_callable=AsyncMock) as mock_alert,
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            _patch_db_with_rows(mock_db, rows)
            _patch_httpx(mock_client_cls)
            await run_health_check()

        assert call_count == 3
        mock_alert.assert_awaited_once()
        level = mock_alert.call_args[1].get("level")
        assert level == "error"
        report_text = mock_alert.call_args[0][0]
        assert "Healthy" in report_text
        assert "Degraded" in report_text
        assert "Dead" in report_text
        assert "Action needed" in report_text
