"""
Unit tests for src/collectors/twscrape_pool.py (TwitterAPI.io probe).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.collectors.twscrape_pool as pool_module


class TestProbeTwitterApi:
    """Tests for the TwitterAPI.io health probe."""

    @pytest.mark.asyncio
    async def test_returns_false_when_no_api_key(self):
        with patch.object(pool_module, "TWITTERAPI_IO_KEY", None):
            ok, detail = await pool_module.probe_twitter_api()
            assert ok is False
            assert "not set" in detail

    @pytest.mark.asyncio
    async def test_returns_true_on_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"tweets": [{"id": "1"}, {"id": "2"}]}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(pool_module, "TWITTERAPI_IO_KEY", "test-key"):
            with patch.object(pool_module, "httpx") as mock_httpx:
                mock_httpx.AsyncClient.return_value = mock_client
                ok, detail = await pool_module.probe_twitter_api("TestUser")
                assert ok is True
                assert "2 tweets" in detail

    @pytest.mark.asyncio
    async def test_returns_false_on_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(pool_module, "TWITTERAPI_IO_KEY", "test-key"):
            with patch.object(pool_module, "httpx") as mock_httpx:
                mock_httpx.AsyncClient.return_value = mock_client
                ok, detail = await pool_module.probe_twitter_api()
                assert ok is False
                assert "401" in detail

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self):
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("connection timeout")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(pool_module, "TWITTERAPI_IO_KEY", "test-key"):
            with patch.object(pool_module, "httpx") as mock_httpx:
                mock_httpx.AsyncClient.return_value = mock_client
                ok, detail = await pool_module.probe_twitter_api()
                assert ok is False
                assert "timeout" in detail

    @pytest.mark.asyncio
    async def test_uses_provided_username(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"tweets": []}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(pool_module, "TWITTERAPI_IO_KEY", "test-key"):
            with patch.object(pool_module, "httpx") as mock_httpx:
                mock_httpx.AsyncClient.return_value = mock_client
                await pool_module.probe_twitter_api("CustomUser")
                call_kwargs = mock_client.get.call_args
                assert call_kwargs[1]["params"]["userName"] == "CustomUser"
