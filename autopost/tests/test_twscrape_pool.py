"""
Unit tests for src/collectors/twscrape_pool.py (TwitterGQLClient).

All HTTP calls are mocked — no network access.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.collectors.twscrape_pool as pool_module


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reset_pool():
    """Reset module-level singletons between tests."""
    pool_module._client = None
    pool_module._user_id_cache.clear()


# ── get_api() ─────────────────────────────────────────────────────────────────

class TestGetApi:
    """Tests for the singleton client initialisation."""

    @pytest.fixture(autouse=True)
    def reset(self):
        _reset_pool()
        yield
        _reset_pool()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_cookies(self):
        with patch.object(pool_module, "TWSCRAPE_COOKIES", None):
            result = await pool_module.get_api()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_cookies_empty_string(self):
        with patch.object(pool_module, "TWSCRAPE_COOKIES", ""):
            result = await pool_module.get_api()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_cached_client_on_second_call(self):
        mock_client = MagicMock()
        pool_module._client = mock_client
        result = await pool_module.get_api()
        assert result is mock_client

    @pytest.mark.asyncio
    async def test_returns_none_when_cookies_missing_auth_token(self):
        with patch.object(pool_module, "TWSCRAPE_COOKIES", "ct0=abc123"):
            result = await pool_module.get_api()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_cookies_missing_ct0(self):
        with patch.object(pool_module, "TWSCRAPE_COOKIES", "auth_token=abc123"):
            result = await pool_module.get_api()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_query_ids_empty(self):
        with (
            patch.object(pool_module, "TWSCRAPE_COOKIES", "auth_token=abc; ct0=def"),
            patch.object(pool_module, "_fetch_query_ids", new_callable=AsyncMock, return_value={}),
        ):
            result = await pool_module.get_api()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_missing_required_query_ids(self):
        with (
            patch.object(pool_module, "TWSCRAPE_COOKIES", "auth_token=abc; ct0=def"),
            patch.object(pool_module, "_fetch_query_ids", new_callable=AsyncMock, return_value={"UserByScreenName": "abc"}),
        ):
            result = await pool_module.get_api()
        assert result is None

    @pytest.mark.asyncio
    async def test_initialises_client_with_valid_cookies_and_query_ids(self):
        query_ids = {"UserByScreenName": "abc", "UserTweets": "def", "SearchTimeline": "ghi"}
        with (
            patch.object(pool_module, "TWSCRAPE_COOKIES", "auth_token=tok123; ct0=csrf456"),
            patch.object(pool_module, "_fetch_query_ids", new_callable=AsyncMock, return_value=query_ids),
        ):
            result = await pool_module.get_api()
        assert result is not None
        assert isinstance(result, pool_module.TwitterGQLClient)
        assert result.cookies["auth_token"] == "tok123"
        assert result.cookies["ct0"] == "csrf456"
        assert result.query_ids == query_ids

    @pytest.mark.asyncio
    async def test_double_checked_lock_returns_existing_client(self):
        sentinel = MagicMock(name="existing_client")
        pool_module._client = None

        class _FakeLock:
            async def __aenter__(self_inner):
                pool_module._client = sentinel
                return self_inner
            async def __aexit__(self_inner, *args):
                pass

        with patch.object(pool_module, "_init_lock", _FakeLock()):
            result = await pool_module.get_api()
        assert result is sentinel


# ── resolve_user_id() ─────────────────────────────────────────────────────────

class TestResolveUserId:

    @pytest.fixture(autouse=True)
    def reset(self):
        _reset_pool()
        yield
        _reset_pool()

    @pytest.mark.asyncio
    async def test_returns_user_id_on_success(self):
        mock_client = MagicMock()
        mock_client.gql_get = AsyncMock(return_value={
            "data": {"user": {"result": {"rest_id": "123456789"}}}
        })
        result = await pool_module.resolve_user_id(mock_client, "RocketLeague")
        assert result == 123456789

    @pytest.mark.asyncio
    async def test_caches_result(self):
        mock_client = MagicMock()
        mock_client.gql_get = AsyncMock(return_value={
            "data": {"user": {"result": {"rest_id": "999"}}}
        })
        await pool_module.resolve_user_id(mock_client, "CachedUser")
        await pool_module.resolve_user_id(mock_client, "CachedUser")
        assert mock_client.gql_get.await_count == 1

    @pytest.mark.asyncio
    async def test_cache_key_is_case_insensitive(self):
        mock_client = MagicMock()
        mock_client.gql_get = AsyncMock(return_value={
            "data": {"user": {"result": {"rest_id": "777"}}}
        })
        await pool_module.resolve_user_id(mock_client, "TestUser")
        result = await pool_module.resolve_user_id(mock_client, "testuser")
        assert result == 777
        assert mock_client.gql_get.await_count == 1

    @pytest.mark.asyncio
    async def test_returns_none_when_api_raises(self):
        mock_client = MagicMock()
        mock_client.gql_get = AsyncMock(side_effect=Exception("rate limited"))
        result = await pool_module.resolve_user_id(mock_client, "BrokenUser")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_rest_id(self):
        mock_client = MagicMock()
        mock_client.gql_get = AsyncMock(return_value={
            "data": {"user": {"result": {}}}
        })
        result = await pool_module.resolve_user_id(mock_client, "GhostUser")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_empty_response(self):
        mock_client = MagicMock()
        mock_client.gql_get = AsyncMock(return_value={})
        result = await pool_module.resolve_user_id(mock_client, "EmptyUser")
        assert result is None


# ── _parse_cookies() ─────────────────────────────────────────────────────────

class TestParseCookies:

    def test_parses_single_segment(self):
        auth, ct0 = pool_module._parse_cookies("auth_token=abc123; ct0=def456")
        assert auth == "abc123"
        assert ct0 == "def456"

    def test_takes_first_pipe_segment(self):
        auth, ct0 = pool_module._parse_cookies("auth_token=a; ct0=b|auth_token=c; ct0=d")
        assert auth == "a"
        assert ct0 == "b"

    def test_returns_empty_on_missing_fields(self):
        auth, ct0 = pool_module._parse_cookies("some_other_cookie=value")
        assert auth == ""
        assert ct0 == ""

    def test_handles_extra_whitespace(self):
        auth, ct0 = pool_module._parse_cookies("  auth_token=abc ;  ct0=def  ")
        assert auth == "abc"
        assert ct0 == "def"
