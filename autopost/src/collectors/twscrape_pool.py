"""
TwitterAPI.io health check helper.

Provides a simple probe function used by health_check.py to verify
the Twitter monitoring API is reachable.
"""
import httpx
from loguru import logger

from config.settings import TWITTERAPI_IO_KEY

_ENDPOINT = "https://api.twitterapi.io/twitter/user/last_tweets"


async def probe_twitter_api(username: str = "RocketLeague") -> tuple[bool, str]:
    """Quick probe to verify TwitterAPI.io is working for a given account.

    Returns (success, detail_message).
    """
    if not TWITTERAPI_IO_KEY:
        return False, "TWITTERAPI_IO_KEY not set"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                _ENDPOINT,
                headers={"X-API-Key": TWITTERAPI_IO_KEY},
                params={"userName": username},
            )
            if resp.status_code == 200:
                data = resp.json()
                count = len(data.get("tweets", []))
                return True, f"{count} tweets returned"
            return False, f"HTTP {resp.status_code}"
    except Exception as exc:
        logger.debug(f"[TwitterAPI.io] probe failed for @{username}: {exc}")
        return False, str(exc)
