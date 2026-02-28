"""
GDBrowser collector — daily level, weekly demon, and recently rated levels.
Public API, no authentication required.

API reference: https://gdbrowser.com/api/
"""
from datetime import datetime, timezone

import httpx
from loguru import logger

from src.collectors.base import BaseCollector, RawContent

_BASE_URL = "https://gdbrowser.com/api"
_TIMEOUT  = 15

# Numeric difficulty → human-readable label
_DIFFICULTY: dict[int, str] = {
    0:  "N/A",
    1:  "Easy",
    2:  "Normal",
    3:  "Hard",
    4:  "Harder",
    5:  "Insane",
    6:  "Easy Demon",
    7:  "Medium Demon",
    8:  "Hard Demon",
    9:  "Insane Demon",
    10: "Extreme Demon",
}


class GDBrowserCollector(BaseCollector):
    """
    Collects three types of GD content per pass:
      - Daily level
      - Weekly demon
      - Recently rated levels (newest 10)
    """

    def __init__(self, source_id: int, config: dict, niche: str = "geometrydash"):
        super().__init__(source_id, config)
        self.niche = niche

    async def collect(self) -> list[RawContent]:
        items: list[RawContent] = []

        async with httpx.AsyncClient(
            base_url=_BASE_URL, timeout=_TIMEOUT
        ) as client:
            daily  = await _fetch_daily(client, self.source_id, self.niche)
            weekly = await _fetch_weekly(client, self.source_id, self.niche)
            rated  = await _fetch_rated(client, self.source_id, self.niche)

        if daily:
            items.append(daily)
        if weekly:
            items.append(weekly)
        items.extend(rated)

        logger.info(
            f"[GDBrowser] collected {len(items)} items "
            f"(daily={'yes' if daily else 'no'}, "
            f"weekly={'yes' if weekly else 'no'}, "
            f"rated={len(rated)})"
        )
        return items


# ── Fetchers ──────────────────────────────────────────────────────────────────

async def _fetch_daily(
    client: httpx.AsyncClient, source_id: int, niche: str
) -> RawContent | None:
    try:
        resp = await client.get("/daily")
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error(f"[GDBrowser] daily fetch failed: {exc}")
        return None

    today      = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    level_id   = data.get("id", "unknown")
    name       = data.get("name", "Unknown")
    author     = data.get("author", "Unknown")
    difficulty = _DIFFICULTY.get(int(data.get("difficulty", 0)), "Unknown")
    stars      = data.get("stars", 0)

    return RawContent(
        source_id    = source_id,
        external_id  = f"daily_{today}_{level_id}",
        niche        = niche,
        content_type = "daily_level",
        title        = f"Daily Level: {name} by {author}",
        url          = f"https://gdbrowser.com/{level_id}",
        body         = f"{difficulty} — {stars} stars",
        image_url    = "",
        author       = author,
        score        = int(data.get("likes", 0)),
        metadata     = {
            "level_name": name,
            "creator":    author,
            "difficulty": difficulty,
            "stars":      str(stars),
            "level_id":   str(level_id),
        },
    )


async def _fetch_weekly(
    client: httpx.AsyncClient, source_id: int, niche: str
) -> RawContent | None:
    try:
        resp = await client.get("/weekly")
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error(f"[GDBrowser] weekly fetch failed: {exc}")
        return None

    now        = datetime.now(timezone.utc)
    week_num   = now.strftime("%Y-W%W")
    level_id   = data.get("id", "unknown")
    name       = data.get("name", "Unknown")
    author     = data.get("author", "Unknown")
    difficulty = _DIFFICULTY.get(int(data.get("difficulty", 0)), "Unknown")
    stars      = data.get("stars", 0)

    return RawContent(
        source_id    = source_id,
        external_id  = f"weekly_{week_num}_{level_id}",
        niche        = niche,
        content_type = "weekly_demon",
        title        = f"Weekly Demon: {name} by {author}",
        url          = f"https://gdbrowser.com/{level_id}",
        body         = f"{difficulty} — {stars} stars",
        image_url    = "",
        author       = author,
        score        = int(data.get("likes", 0)),
        metadata     = {
            "level_name": name,
            "creator":    author,
            "difficulty": difficulty,
            "stars":      str(stars),
            "level_id":   str(level_id),
        },
    )


async def _fetch_rated(
    client: httpx.AsyncClient, source_id: int, niche: str
) -> list[RawContent]:
    """Fetch the 10 most recently rated levels."""
    try:
        resp = await client.get(
            "/search/*",
            params={"type": "recent", "diff": "1,2,3,4,5", "count": 10},
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception as exc:
        logger.error(f"[GDBrowser] rated fetch failed: {exc}")
        return []

    items: list[RawContent] = []
    for data in results:
        level_id   = data.get("id", "")
        name       = data.get("name", "Unknown")
        author     = data.get("author", "Unknown")
        difficulty = _DIFFICULTY.get(int(data.get("difficulty", 0)), "Unknown")
        stars      = data.get("stars", 0)

        if not level_id:
            continue

        items.append(RawContent(
            source_id    = source_id,
            external_id  = f"rated_{level_id}",
            niche        = niche,
            content_type = "level_rated",
            title        = f"{name} by {author}",
            url          = f"https://gdbrowser.com/{level_id}",
            body         = f"{difficulty} — {stars} stars",
            image_url    = "",
            author       = author,
            score        = int(data.get("likes", 0)),
            metadata     = {
                "level_name": name,
                "creator":    author,
                "difficulty": difficulty,
                "stars":      str(stars),
                "level_id":   str(level_id),
            },
        ))

    return items
