"""
Unit tests for src/collectors/twscrape_pool.py

All twscrape and DB I/O is mocked — no network, no file system access.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.collectors.twscrape_pool as pool_module


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reset_pool():
    """Reset module-level singletons between tests."""
    pool_module._api = None
    pool_module._user_id_cache.clear()


# ── get_api() ─────────────────────────────────────────────────────────────────

class TestGetApi:
    """Tests for the singleton pool initialisation."""

    @pytest.fixture(autouse=True)
    def reset(self):
        _reset_pool()
        yield
        _reset_pool()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_cookies(self):
        """get_api() should return None and log when TWSCRAPE_COOKIES is unset."""
        with patch.object(pool_module, "TWSCRAPE_COOKIES", None):
            result = await pool_module.get_api()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_cookies_empty_string(self):
        """Empty string cookie var should behave the same as unset."""
        with patch.object(pool_module, "TWSCRAPE_COOKIES", ""):
            result = await pool_module.get_api()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_cached_api_on_second_call(self):
        """Second call should return the already-initialised API without re-init."""
        mock_api = MagicMock()
        pool_module._api = mock_api
        result = await pool_module.get_api()
        assert result is mock_api

    @pytest.mark.asyncio
    async def test_initialises_pool_with_valid_cookies(self):
        """A valid cookie string should initialise the pool and return an API."""
        fake_api = MagicMock()
        fake_api.pool.add_account = AsyncMock()

        with (
            patch.object(pool_module, "TWSCRAPE_COOKIES", "auth_token=abc; ct0=def"),
            patch.object(pool_module, "DATA_DIR", MagicMock(mkdir=MagicMock())),
            patch.object(pool_module, "_POOL_PATH", MagicMock(exists=MagicMock(return_value=False))),
            patch("src.collectors.twscrape_pool.API", return_value=fake_api),
        ):
            result = await pool_module.get_api()

        assert result is fake_api
        fake_api.pool.add_account.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deletes_existing_pool_db_before_init(self):
        """If pool DB already exists, it should be deleted (fresh cookie reload)."""
        fake_api = MagicMock()
        fake_api.pool.add_account = AsyncMock()
        mock_path = MagicMock()
        mock_path.exists.return_value = True

        with (
            patch.object(pool_module, "TWSCRAPE_COOKIES", "auth_token=abc; ct0=def"),
            patch.object(pool_module, "DATA_DIR", MagicMock(mkdir=MagicMock())),
            patch.object(pool_module, "_POOL_PATH", mock_path),
            patch("src.collectors.twscrape_pool.API", return_value=fake_api),
        ):
            await pool_module.get_api()

        mock_path.unlink.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_all_accounts_fail_to_add(self):
        """If every account add raises, no accounts added → return None."""
        fake_api = MagicMock()
        fake_api.pool.add_account = AsyncMock(side_effect=Exception("cookie rejected"))

        with (
            patch.object(pool_module, "TWSCRAPE_COOKIES", "auth_token=bad; ct0=bad"),
            patch.object(pool_module, "DATA_DIR", MagicMock(mkdir=MagicMock())),
            patch.object(pool_module, "_POOL_PATH", MagicMock(exists=MagicMock(return_value=False))),
            patch("src.collectors.twscrape_pool.API", return_value=fake_api),
        ):
            result = await pool_module.get_api()

        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_cookies_parsed_by_pipe(self):
        """Pipe-separated cookies should produce one account per segment."""
        fake_api = MagicMock()
        fake_api.pool.add_account = AsyncMock()

        cookies = "auth_token=a; ct0=b|auth_token=c; ct0=d|auth_token=e; ct0=f"

        with (
            patch.object(pool_module, "TWSCRAPE_COOKIES", cookies),
            patch.object(pool_module, "DATA_DIR", MagicMock(mkdir=MagicMock())),
            patch.object(pool_module, "_POOL_PATH", MagicMock(exists=MagicMock(return_value=False))),
            patch("src.collectors.twscrape_pool.API", return_value=fake_api),
        ):
            result = await pool_module.get_api()

        assert result is fake_api
        assert fake_api.pool.add_account.await_count == 3

    @pytest.mark.asyncio
    async def test_partial_account_failure_still_succeeds(self):
        """If at least one account adds successfully, pool init succeeds."""
        fake_api = MagicMock()
        call_count = 0

        async def _add_account(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("first one fails")
            # second one succeeds

        fake_api.pool.add_account = _add_account

        with (
            patch.object(pool_module, "TWSCRAPE_COOKIES", "auth_token=a; ct0=b|auth_token=c; ct0=d"),
            patch.object(pool_module, "DATA_DIR", MagicMock(mkdir=MagicMock())),
            patch.object(pool_module, "_POOL_PATH", MagicMock(exists=MagicMock(return_value=False))),
            patch("src.collectors.twscrape_pool.API", return_value=fake_api),
        ):
            result = await pool_module.get_api()

        assert result is fake_api


# ── resolve_user_id() ─────────────────────────────────────────────────────────

class TestResolveUserId:
    """Tests for the username → user-ID resolver."""

    @pytest.fixture(autouse=True)
    def reset(self):
        _reset_pool()
        yield
        _reset_pool()

    @pytest.mark.asyncio
    async def test_returns_user_id_on_success(self):
        """Happy path: user resolves to a numeric ID."""
        mock_api = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 123456789
        mock_api.user_by_login = AsyncMock(return_value=mock_user)

        result = await pool_module.resolve_user_id(mock_api, "RocketLeague")

        assert result == 123456789
        mock_api.user_by_login.assert_awaited_once_with("RocketLeague")

    @pytest.mark.asyncio
    async def test_caches_result(self):
        """Second lookup of the same username should use the cache."""
        mock_api = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 999
        mock_api.user_by_login = AsyncMock(return_value=mock_user)

        await pool_module.resolve_user_id(mock_api, "CachedUser")
        await pool_module.resolve_user_id(mock_api, "CachedUser")

        # Should only call the API once
        assert mock_api.user_by_login.await_count == 1

    @pytest.mark.asyncio
    async def test_cache_key_is_case_insensitive(self):
        """Cache lookup uses lower-cased key, so 'User' and 'user' share cache."""
        mock_api = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 777
        mock_api.user_by_login = AsyncMock(return_value=mock_user)

        await pool_module.resolve_user_id(mock_api, "TestUser")
        result = await pool_module.resolve_user_id(mock_api, "testuser")

        assert result == 777
        assert mock_api.user_by_login.await_count == 1

    @pytest.mark.asyncio
    async def test_returns_none_when_api_raises(self):
        """If user_by_login raises, return None gracefully."""
        mock_api = MagicMock()
        mock_api.user_by_login = AsyncMock(side_effect=Exception("rate limited"))

        result = await pool_module.resolve_user_id(mock_api, "BrokenUser")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_user_is_none(self):
        """If user_by_login returns None, return None."""
        mock_api = MagicMock()
        mock_api.user_by_login = AsyncMock(return_value=None)

        result = await pool_module.resolve_user_id(mock_api, "GhostUser")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_user_id_is_falsy(self):
        """If user.id is 0 or falsy, return None."""
        mock_api = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 0
        mock_api.user_by_login = AsyncMock(return_value=mock_user)

        result = await pool_module.resolve_user_id(mock_api, "ZeroUser")

        assert result is None
