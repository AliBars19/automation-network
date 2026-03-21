"""
Twitter GraphQL client — cookie-based auth, no third-party scraping library.

Fetches current GraphQL query IDs from Twitter's JS bundles on startup,
then uses them to resolve usernames and fetch timelines. This avoids
depending on twscrape/twikit which break whenever Twitter rotates hashes.

Set TWSCRAPE_COOKIES in .env:
    TWSCRAPE_COOKIES=auth_token=abc; ct0=def

Extract cookies from browser DevTools → Application → Cookies → x.com.
Cookies expire periodically; refresh by updating the env var and restarting.
"""
import asyncio
import json
import re

import httpx
from loguru import logger

from config.settings import TWSCRAPE_COOKIES

# ── Constants ────────────────────────────────────────────────────────────────

OP_USER_BY_SCREEN_NAME = "UserByScreenName"
OP_USER_TWEETS = "UserTweets"
_REQUIRED_OPS = {OP_USER_BY_SCREEN_NAME, OP_USER_TWEETS}

_MAX_CACHE_SIZE = 500

_BEARER = (
    "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# ── Singleton state ──────────────────────────────────────────────────────────

_client: "TwitterGQLClient | None" = None
_init_lock = asyncio.Lock()
_user_id_cache: dict[str, int] = {}


class TwitterGQLClient:
    """Thin wrapper around Twitter's internal GraphQL API using cookie auth."""

    def __init__(self, auth_token: str, ct0: str, query_ids: dict[str, str]):
        self.cookies = {"auth_token": auth_token, "ct0": ct0}
        self.headers = {
            "authorization": _BEARER,
            "x-csrf-token": ct0,
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }
        self.query_ids = query_ids
        self._http = httpx.AsyncClient(timeout=15)

    async def gql_get(self, operation: str, variables: dict, features: dict | None = None) -> dict:
        """Execute a GraphQL GET request and return the JSON response."""
        qid = self.query_ids.get(operation)
        if not qid:
            raise ValueError(f"Unknown GraphQL operation: {operation}")

        params = {"variables": json.dumps(variables)}
        if features:
            params["features"] = json.dumps(features)

        resp = await self._http.get(
            f"https://x.com/i/api/graphql/{qid}/{operation}",
            headers=self.headers,
            cookies=self.cookies,
            params=params,
        )
        if resp.status_code == 429:
            logger.debug(f"[TwitterGQL] rate-limited on {operation}")
            return {}
        resp.raise_for_status()
        return resp.json()


# ── Public API (same signatures as old twscrape_pool) ────────────────────────

async def get_api() -> TwitterGQLClient | None:
    """Return the shared TwitterGQLClient, initializing on first call."""
    global _client

    if _client is not None:
        return _client

    async with _init_lock:
        if _client is not None:
            return _client

        cookies_raw = TWSCRAPE_COOKIES or ""
        if not cookies_raw:
            logger.error(
                "[TwitterGQL] TWSCRAPE_COOKIES not set — Twitter monitoring disabled"
            )
            return None

        auth_token, ct0 = _parse_cookies(cookies_raw)
        if not auth_token or not ct0:
            logger.error(
                "[TwitterGQL] TWSCRAPE_COOKIES missing auth_token or ct0 — "
                "Twitter monitoring disabled"
            )
            return None

        query_ids = await _fetch_query_ids(auth_token, ct0)
        if not query_ids:
            logger.error("[TwitterGQL] failed to fetch GraphQL query IDs")
            return None

        missing = _REQUIRED_OPS - set(query_ids)
        if missing:
            logger.error(f"[TwitterGQL] missing query IDs: {missing}")
            return None

        _client = TwitterGQLClient(auth_token, ct0, query_ids)
        logger.info(
            f"[TwitterGQL] initialized — {len(query_ids)} query IDs loaded"
        )
        return _client


async def resolve_user_id(client: TwitterGQLClient, username: str) -> int | None:
    """Resolve a Twitter username to a numeric user ID, with in-memory cache."""
    key = username.lower()

    if key in _user_id_cache:
        return _user_id_cache[key]

    try:
        data = await client.gql_get(
            OP_USER_BY_SCREEN_NAME,
            {"screen_name": username, "withSafetyModeUserFields": True},
            {
                "hidden_profile_subscriptions_enabled": True,
                "responsive_web_graphql_exclude_directive_enabled": True,
                "verified_phone_label_enabled": False,
                "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                "responsive_web_graphql_timeline_navigation_enabled": True,
            },
        )
        rest_id = (
            data.get("data", {})
            .get("user", {})
            .get("result", {})
            .get("rest_id")
        )
        if rest_id:
            uid = int(rest_id)
            if len(_user_id_cache) >= _MAX_CACHE_SIZE:
                _user_id_cache.pop(next(iter(_user_id_cache)))
            _user_id_cache[key] = uid
            logger.debug(f"[TwitterGQL] resolved @{username} → {uid}")
            return uid
    except Exception as exc:
        logger.error(f"[TwitterGQL] failed to resolve @{username}: {exc}")

    return None


# ── Internal helpers ─────────────────────────────────────────────────────────

def _parse_cookies(raw: str) -> tuple[str, str]:
    """Extract auth_token and ct0 from the first cookie segment."""
    # Take the first pipe-separated segment
    segment = raw.split("|")[0].strip()
    auth_token = ""
    ct0 = ""
    for part in segment.split(";"):
        part = part.strip()
        if part.startswith("auth_token="):
            auth_token = part.split("=", 1)[1].strip()
        elif part.startswith("ct0="):
            ct0 = part.split("=", 1)[1].strip()
    return auth_token, ct0


async def _fetch_query_ids(auth_token: str, ct0: str) -> dict[str, str]:
    """Fetch current GraphQL query IDs from Twitter's JS bundles."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            # Load x.com to find JS bundle URLs
            resp = await http.get(
                "https://x.com",
                cookies={"auth_token": auth_token, "ct0": ct0},
                headers={
                    "user-agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36"
                    ),
                },
                follow_redirects=True,
            )
            js_urls = re.findall(
                r"https://abs\.twimg\.com/responsive-web/client-web[^\"]+\.js",
                resp.text,
            )
            if not js_urls:
                logger.error("[TwitterGQL] no JS bundle URLs found on x.com")
                return {}

            # Scan bundles for queryId:operationName pairs (parallel fetch)
            _QID_RE = re.compile(r'queryId:"([^"]+)",operationName:"(\w+)"')
            bundle_resps = await asyncio.gather(
                *(http.get(url, timeout=15) for url in js_urls[:8]),
                return_exceptions=True,
            )
            query_ids: dict[str, str] = {}
            for resp_or_exc in bundle_resps:
                if isinstance(resp_or_exc, Exception):
                    continue
                for match in _QID_RE.finditer(resp_or_exc.text):
                    query_ids[match.group(2)] = match.group(1)

            logger.info(
                f"[TwitterGQL] scraped {len(query_ids)} query IDs from "
                f"{len(js_urls)} JS bundles"
            )
            return query_ids
    except Exception as exc:
        logger.error(f"[TwitterGQL] failed to fetch query IDs: {exc}")
        return {}
