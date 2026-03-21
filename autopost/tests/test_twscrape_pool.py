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


# ── TwitterGQLClient.gql_get() ────────────────────────────────────────────────

def _make_client(query_ids: dict | None = None) -> pool_module.TwitterGQLClient:
    """Build a TwitterGQLClient with a mocked internal httpx.AsyncClient."""
    ids = query_ids if query_ids is not None else {
        "UserByScreenName": "qid_ubsn",
        "UserTweets": "qid_ut",
    }
    return pool_module.TwitterGQLClient(
        auth_token="test_auth",
        ct0="test_ct0",
        query_ids=ids,
    )


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    """Return a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    return resp


class TestGqlGet:
    """Tests for TwitterGQLClient.gql_get()."""

    @pytest.mark.asyncio
    async def test_raises_for_unknown_operation(self):
        client = _make_client()
        with pytest.raises(ValueError, match="Unknown GraphQL operation"):
            await client.gql_get("NonExistentOp", {})

    @pytest.mark.asyncio
    async def test_returns_json_on_success(self):
        client = _make_client()
        expected = {"data": {"user": {"result": {"rest_id": "42"}}}}
        mock_resp = _mock_response(200, expected)

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.gql_get("UserByScreenName", {"screen_name": "foo"})

        assert result == expected

    @pytest.mark.asyncio
    async def test_returns_empty_dict_on_429(self):
        client = _make_client()
        mock_resp = _mock_response(429)
        # 429 should NOT call raise_for_status — verify it returns {} instead
        mock_resp.raise_for_status = MagicMock(side_effect=AssertionError("must not raise"))

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.gql_get("UserTweets", {"userId": "1"})

        assert result == {}

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self):
        client = _make_client()
        mock_resp = _mock_response(403)

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(httpx.HTTPStatusError):
                await client.gql_get("UserByScreenName", {"screen_name": "foo"})

    @pytest.mark.asyncio
    async def test_features_param_is_omitted_when_none(self):
        """When features=None the 'features' key must not appear in the request params."""
        client = _make_client()
        mock_resp = _mock_response(200, {})
        captured_kwargs: dict = {}

        async def _fake_get(url, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_resp

        with patch.object(client._http, "get", side_effect=_fake_get):
            await client.gql_get("UserByScreenName", {"screen_name": "bar"})

        assert "features" not in captured_kwargs.get("params", {})

    @pytest.mark.asyncio
    async def test_features_param_is_included_when_provided(self):
        """When features dict is given it is JSON-encoded into the params."""
        client = _make_client()
        mock_resp = _mock_response(200, {})
        captured_kwargs: dict = {}

        async def _fake_get(url, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_resp

        features = {"hidden_profile_subscriptions_enabled": True}
        with patch.object(client._http, "get", side_effect=_fake_get):
            await client.gql_get("UserByScreenName", {"screen_name": "bar"}, features=features)

        import json as _json
        assert "features" in captured_kwargs.get("params", {})
        assert _json.loads(captured_kwargs["params"]["features"]) == features

    @pytest.mark.asyncio
    async def test_correct_url_is_called(self):
        """URL must embed the query ID and operation name."""
        client = _make_client()
        mock_resp = _mock_response(200, {})
        captured_url: list[str] = []

        async def _fake_get(url, **kwargs):
            captured_url.append(url)
            return mock_resp

        with patch.object(client._http, "get", side_effect=_fake_get):
            await client.gql_get("UserByScreenName", {})

        assert captured_url[0] == "https://x.com/i/api/graphql/qid_ubsn/UserByScreenName"


# ── resolve_user_id() — cache eviction ───────────────────────────────────────

class TestCacheEviction:
    """Line 157: oldest entry is evicted when cache reaches _MAX_CACHE_SIZE."""

    @pytest.fixture(autouse=True)
    def reset(self):
        _reset_pool()
        yield
        _reset_pool()

    @pytest.mark.asyncio
    async def test_evicts_oldest_entry_when_cache_full(self):
        # Pre-fill the cache to exactly _MAX_CACHE_SIZE entries.
        for i in range(pool_module._MAX_CACHE_SIZE):
            pool_module._user_id_cache[f"user_{i}"] = i

        assert len(pool_module._user_id_cache) == pool_module._MAX_CACHE_SIZE
        first_key = next(iter(pool_module._user_id_cache))

        mock_client = MagicMock()
        mock_client.gql_get = AsyncMock(return_value={
            "data": {"user": {"result": {"rest_id": "99999"}}}
        })

        result = await pool_module.resolve_user_id(mock_client, "new_user")

        assert result == 99999
        # The first inserted entry must have been evicted.
        assert first_key not in pool_module._user_id_cache
        # Cache size must not grow beyond _MAX_CACHE_SIZE.
        assert len(pool_module._user_id_cache) == pool_module._MAX_CACHE_SIZE
        # The new entry must be present.
        assert pool_module._user_id_cache["new_user"] == 99999

    @pytest.mark.asyncio
    async def test_no_eviction_when_cache_below_limit(self):
        """When the cache is not full, no entries should be removed."""
        pool_module._user_id_cache["existing"] = 1

        mock_client = MagicMock()
        mock_client.gql_get = AsyncMock(return_value={
            "data": {"user": {"result": {"rest_id": "2"}}}
        })

        await pool_module.resolve_user_id(mock_client, "another_user")

        assert pool_module._user_id_cache["existing"] == 1
        assert pool_module._user_id_cache["another_user"] == 2


# ── _fetch_query_ids() ────────────────────────────────────────────────────────

import httpx  # noqa: E402 — placed here to keep existing import block clean


class TestFetchQueryIds:
    """Tests for the internal _fetch_query_ids() helper (lines 186-228)."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_bundle_response(text: str) -> MagicMock:
        resp = MagicMock()
        resp.text = text
        resp.status_code = 200
        return resp

    @staticmethod
    def _make_homepage_response(js_urls: list[str]) -> MagicMock:
        """Build a fake x.com homepage response embedding the given JS URLs."""
        body = " ".join(
            f'src="{url}"' for url in js_urls
        )
        resp = MagicMock()
        resp.text = body
        resp.status_code = 200
        return resp

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_no_js_urls_found(self):
        homepage = self._make_homepage_response([])  # no abs.twimg.com JS URLs

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=homepage)

            result = await pool_module._fetch_query_ids("tok", "ct0")

        assert result == {}

    @pytest.mark.asyncio
    async def test_parses_query_ids_from_js_bundle(self):
        js_url = "https://abs.twimg.com/responsive-web/client-web/main.abc123.js"
        homepage = self._make_homepage_response([js_url])
        bundle_text = (
            'queryId:"QID_UBSN",operationName:"UserByScreenName" '
            'queryId:"QID_UT",operationName:"UserTweets"'
        )
        bundle_resp = self._make_bundle_response(bundle_text)

        responses = [homepage, bundle_resp]
        call_count = 0

        async def _fake_get(url, **kwargs):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get = _fake_get

            result = await pool_module._fetch_query_ids("tok", "ct0")

        assert result["UserByScreenName"] == "QID_UBSN"
        assert result["UserTweets"] == "QID_UT"

    @pytest.mark.asyncio
    async def test_skips_failed_bundle_fetches(self):
        """Bundles that raise exceptions are silently skipped."""
        js_urls = [
            "https://abs.twimg.com/responsive-web/client-web/a.js",
            "https://abs.twimg.com/responsive-web/client-web/b.js",
        ]
        homepage = self._make_homepage_response(js_urls)
        good_bundle = self._make_bundle_response(
            'queryId:"GOOD_QID",operationName:"UserTweets"'
        )

        responses_iter = iter([homepage])
        bundle_resps = [Exception("timeout"), good_bundle]
        bundle_call = 0

        async def _fake_get(url, **kwargs):
            nonlocal bundle_call
            if "abs.twimg.com" in url:
                resp = bundle_resps[bundle_call]
                bundle_call += 1
                if isinstance(resp, Exception):
                    raise resp
                return resp
            return next(responses_iter)

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            # asyncio.gather is used, so we patch gather to call the fake directly
            instance.get = _fake_get

            result = await pool_module._fetch_query_ids("tok", "ct0")

        # The good bundle's operation must be present even though the first failed.
        assert result.get("UserTweets") == "GOOD_QID"

    @pytest.mark.asyncio
    async def test_returns_empty_dict_on_top_level_network_error(self):
        """If the initial x.com GET raises, the function must return {} not raise."""
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get = AsyncMock(side_effect=httpx.ConnectError("unreachable"))

            result = await pool_module._fetch_query_ids("tok", "ct0")

        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_bundles_have_no_matching_patterns(self):
        """Bundles that contain no queryId/operationName pairs yield an empty dict."""
        js_url = "https://abs.twimg.com/responsive-web/client-web/chunk.xyz.js"
        homepage = self._make_homepage_response([js_url])
        empty_bundle = self._make_bundle_response("var x = 1; function foo() {}")

        responses = [homepage, empty_bundle]
        call_count = 0

        async def _fake_get(url, **kwargs):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get = _fake_get

            result = await pool_module._fetch_query_ids("tok", "ct0")

        assert result == {}

    @pytest.mark.asyncio
    async def test_fetches_at_most_eight_bundles(self):
        """Even if x.com lists more than 8 JS URLs, only the first 8 are fetched."""
        js_urls = [
            f"https://abs.twimg.com/responsive-web/client-web/chunk_{i}.js"
            for i in range(12)
        ]
        homepage = self._make_homepage_response(js_urls)
        bundle = self._make_bundle_response("")  # empty — just counting calls

        fetched_bundle_urls: list[str] = []

        async def _fake_get(url, **kwargs):
            if "abs.twimg.com" in url:
                fetched_bundle_urls.append(url)
                return bundle
            return homepage

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get = _fake_get

            await pool_module._fetch_query_ids("tok", "ct0")

        assert len(fetched_bundle_urls) == 8
