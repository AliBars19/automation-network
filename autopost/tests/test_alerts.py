"""
Unit tests for src/monitoring/alerts.py

All HTTP calls are mocked. Tests verify payload construction and
that failures never propagate to callers.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.monitoring.alerts as alerts_module
from src.monitoring.alerts import (
    alert_collector_failure,
    alert_dry_spell,
    alert_poster_failure,
    alert_startup,
    send_alert,
)


# ── send_alert() ──────────────────────────────────────────────────────────────

class TestSendAlert:

    @pytest.mark.asyncio
    async def test_no_op_when_webhook_url_unset(self):
        """send_alert() should silently do nothing if DISCORD_WEBHOOK_URL is empty."""
        with patch.object(alerts_module, "DISCORD_WEBHOOK_URL", ""):
            # Should not raise and should not make any HTTP call
            with patch("src.monitoring.alerts._post") as mock_post:
                await send_alert("Hello")
            mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_posts_when_webhook_url_set(self):
        """send_alert() should call _post with an embeds payload."""
        with (
            patch.object(alerts_module, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/123/abc"),
            patch("src.monitoring.alerts._post", new_callable=AsyncMock) as mock_post,
        ):
            await send_alert("Test alert", level="error")

        mock_post.assert_awaited_once()
        payload = mock_post.call_args[0][0]
        assert "embeds" in payload
        assert payload["embeds"][0]["description"] == "Test alert"

    @pytest.mark.asyncio
    async def test_correct_color_for_each_level(self):
        """Each alert level should produce a distinct embed colour."""
        colours_seen = {}
        for level in ("error", "warning", "success", "info"):
            with (
                patch.object(alerts_module, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x/abc"),
                patch("src.monitoring.alerts._post", new_callable=AsyncMock) as mock_post,
            ):
                await send_alert("msg", level=level)
            payload = mock_post.call_args[0][0]
            colours_seen[level] = payload["embeds"][0]["color"]

        # All four levels must have distinct colours
        assert len(set(colours_seen.values())) == 4

    @pytest.mark.asyncio
    async def test_unknown_level_falls_back_to_error_color(self):
        """An unrecognised level should use the error colour."""
        from src.monitoring.alerts import _COLOUR
        with (
            patch.object(alerts_module, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/hook"),
            patch("src.monitoring.alerts._post", new_callable=AsyncMock) as mock_post,
        ):
            await send_alert("msg", level="totally_unknown")
        payload = mock_post.call_args[0][0]
        assert payload["embeds"][0]["color"] == _COLOUR["error"]


# ── Helper alert functions ────────────────────────────────────────────────────

class TestHelperAlerts:

    @pytest.mark.asyncio
    async def test_alert_collector_failure(self):
        with (
            patch.object(alerts_module, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/hook"),
            patch("src.monitoring.alerts._post", new_callable=AsyncMock) as mock_post,
        ):
            await alert_collector_failure("RSSCollector", "rocketleague", "timeout error")

        payload = mock_post.call_args[0][0]
        desc = payload["embeds"][0]["description"]
        assert "RSSCollector" in desc
        assert "rocketleague" in desc
        assert "timeout error" in desc

    @pytest.mark.asyncio
    async def test_alert_poster_failure(self):
        with (
            patch.object(alerts_module, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/hook"),
            patch("src.monitoring.alerts._post", new_callable=AsyncMock) as mock_post,
        ):
            await alert_poster_failure("geometrydash", "403 Forbidden")

        payload = mock_post.call_args[0][0]
        desc = payload["embeds"][0]["description"]
        assert "geometrydash" in desc
        assert "403 Forbidden" in desc

    @pytest.mark.asyncio
    async def test_alert_dry_spell(self):
        with (
            patch.object(alerts_module, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/hook"),
            patch("src.monitoring.alerts._post", new_callable=AsyncMock) as mock_post,
        ):
            await alert_dry_spell("rocketleague", hours=6)

        payload = mock_post.call_args[0][0]
        desc = payload["embeds"][0]["description"]
        assert "6" in desc
        assert "rocketleague" in desc

    @pytest.mark.asyncio
    async def test_alert_startup_dry_run(self):
        with (
            patch.object(alerts_module, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/hook"),
            patch("src.monitoring.alerts._post", new_callable=AsyncMock) as mock_post,
        ):
            await alert_startup(dry_run=True)

        payload = mock_post.call_args[0][0]
        assert "DRY RUN" in payload["embeds"][0]["description"]

    @pytest.mark.asyncio
    async def test_alert_startup_live_mode(self):
        with (
            patch.object(alerts_module, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/hook"),
            patch("src.monitoring.alerts._post", new_callable=AsyncMock) as mock_post,
        ):
            await alert_startup(dry_run=False)

        payload = mock_post.call_args[0][0]
        assert "LIVE" in payload["embeds"][0]["description"]


# ── _post() resilience ────────────────────────────────────────────────────────

class TestPostResilience:

    @pytest.mark.asyncio
    async def test_http_failure_does_not_propagate(self):
        """_post() should catch all exceptions and never raise."""
        import httpx
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
            mock_client_cls.return_value = mock_client

            # Should not raise
            from src.monitoring.alerts import _post
            await _post({"embeds": []})

    @pytest.mark.asyncio
    async def test_http_status_error_does_not_propagate(self):
        """4xx/5xx response should be caught silently."""
        import httpx
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "404", request=MagicMock(), response=MagicMock()
            )
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            from src.monitoring.alerts import _post
            await _post({"embeds": []})  # should not raise
