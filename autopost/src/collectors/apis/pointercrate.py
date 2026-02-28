"""
Pointercrate collector — polls the official GD demon list API.
Public API, no authentication required.

Detects:
  - New #1 demon                 → top1_verified   (priority 1 / breaking)
  - New demon in top 75          → level_verified
  - New demon ranked 76–150      → demon_list_update
  - Periodic top-5 recap         → demon_list_update

API reference: https://pointercrate.com/documentation/demons/
"""
import httpx
from loguru import logger

from src.collectors.base import BaseCollector, RawContent

_BASE_URL  = "https://pointercrate.com/api/v2"
_TOP_N     = 150   # how many demons to fetch per poll
_BATCH     = 100   # max Pointercrate allows per request


class PointercrateCollector(BaseCollector):
    """
    Fetches the current demon list and surfaces new entries as RawContent.
    The dedup layer (insert_raw_content UNIQUE constraint) ensures each
    demon is only queued once — position changes are not re-queued.
    """

    def __init__(self, source_id: int, config: dict, niche: str = "geometrydash"):
        super().__init__(source_id, config)
        self.niche = niche

    async def collect(self) -> list[RawContent]:
        demons = await _fetch_demons(_TOP_N)
        if not demons:
            return []

        items: list[RawContent] = []
        for demon in demons:
            position  = demon.get("position", 999)
            name      = demon.get("name", "Unknown")
            verifier  = (demon.get("verifier") or {}).get("name", "Unknown")
            publisher = (demon.get("publisher") or {}).get("name", verifier)
            video_url = demon.get("video") or ""
            thumbnail = demon.get("thumbnail") or ""
            demon_id  = demon.get("id", 0)

            content_type = _classify(position)

            # Use "pc_{id}" so it's namespaced and never collides with other sources
            external_id = f"pc_{demon_id}"

            items.append(RawContent(
                source_id    = self.source_id,
                external_id  = external_id,
                niche        = self.niche,
                content_type = content_type,
                title        = name,
                url          = video_url,
                body         = f"#{position} on the Pointercrate Demon List. Verified by {verifier}.",
                image_url    = thumbnail,
                author       = verifier,
                score        = max(0, 150 - position),   # higher = more notable
                metadata     = {
                    "level":     name,
                    "level_name": name,
                    "position":  str(position),
                    "player":    verifier,
                    "creator":   publisher,
                    "verifier":  verifier,
                    "publisher": publisher,
                    "details":   f"#{position} on the Demon List — verified by {verifier}",
                    "description": f"#{position} on the Demon List",
                    "emoji":     "🚨" if position == 1 else ("🏆" if position <= 10 else "🔺"),
                },
            ))

        logger.info(f"[Pointercrate] fetched {len(items)} demons (top {_TOP_N})")
        return items


# ── API helpers ───────────────────────────────────────────────────────────────

async def _fetch_demons(total: int) -> list[dict]:
    """Fetch up to `total` demons in batches, returning a flat list."""
    demons: list[dict] = []
    fetched = 0

    async with httpx.AsyncClient(timeout=15) as client:
        while fetched < total:
            limit = min(_BATCH, total - fetched)
            try:
                resp = await client.get(
                    f"{_BASE_URL}/demons/",
                    params={"limit": limit, "after": fetched},
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.error(f"[Pointercrate] HTTP error: {exc}")
                break

            batch = resp.json()
            if not batch:
                break
            demons.extend(batch)
            fetched += len(batch)

    return demons


def _classify(position: int) -> str:
    if position == 1:
        return "top1_verified"
    if position <= 75:
        return "level_verified"
    return "demon_list_update"
