"""
Shared twscrape API pool — singleton across all TwitterMonitorCollector instances.

Manages cookie-based auth account pool and username → user_id resolution cache.
Set TWSCRAPE_COOKIES in .env with pipe-separated cookie strings:
    TWSCRAPE_COOKIES=auth_token=abc; ct0=def|auth_token=ghi; ct0=jkl

Extract cookies from browser DevTools → Application → Cookies → x.com.
Cookies expire periodically; refresh by updating the env var and restarting.
"""
import asyncio
from pathlib import Path

from loguru import logger
from twscrape import API

from config.settings import DATA_DIR, TWSCRAPE_COOKIES

_api: API | None = None
_init_lock = asyncio.Lock()
_user_id_cache: dict[str, int] = {}

_POOL_PATH = Path(DATA_DIR) / "twscrape.db"


async def get_api() -> API | None:
    """Return the shared twscrape API instance, initializing on first call."""
    global _api

    if _api is not None:
        return _api

    async with _init_lock:
        # Double-check after acquiring lock
        if _api is not None:
            return _api

        cookies_raw = TWSCRAPE_COOKIES or ""
        if not cookies_raw:
            logger.error(
                "[twscrape] TWSCRAPE_COOKIES not set — Twitter monitoring disabled"
            )
            return None

        DATA_DIR.mkdir(parents=True, exist_ok=True)

        # Fresh pool on each startup to ensure cookies match env var
        if _POOL_PATH.exists():
            _POOL_PATH.unlink()

        api = API(pool=str(_POOL_PATH))

        cookie_list = [c.strip() for c in cookies_raw.split("|") if c.strip()]
        added = 0
        for i, cookies in enumerate(cookie_list):
            try:
                await api.pool.add_account(
                    f"pool_account_{i}", "x", f"pool{i}@x.com", "x",
                    cookies=cookies,
                )
                added += 1
            except Exception as exc:
                logger.warning(f"[twscrape] failed to add account #{i}: {exc}")

        if added == 0:
            logger.error("[twscrape] no accounts added — Twitter monitoring disabled")
            return None

        _api = api
        logger.info(f"[twscrape] pool initialized with {added} account(s)")
        return _api


async def resolve_user_id(api: API, username: str) -> int | None:
    """Resolve a Twitter username to a numeric user ID, with in-memory cache."""
    key = username.lower()

    if key in _user_id_cache:
        return _user_id_cache[key]

    try:
        user = await api.user_by_login(username)
        if user and user.id:
            _user_id_cache[key] = user.id
            logger.debug(f"[twscrape] resolved @{username} → {user.id}")
            return user.id
    except Exception as exc:
        logger.error(f"[twscrape] failed to resolve @{username}: {exc}")

    return None
