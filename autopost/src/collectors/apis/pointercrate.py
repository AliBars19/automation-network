"""
Pointercrate collector — polls the official GD demon list API.
Public API, no authentication required.

Detects:
  - New #1 demon                 → top1_verified   (priority 1 / breaking)
  - New demon in top 75          → level_verified
  - New demon ranked 76–150      → demon_list_update
  - First victor on a demon      → first_victor    (high community interest)
  - Periodic top-5 recap         → demon_list_update

API reference: https://pointercrate.com/documentation/demons/
"""
import httpx
from loguru import logger

from src.collectors.base import BaseCollector, RawContent

_BASE_URL  = "https://pointercrate.com/api/v2"
_TOP_N     = 75    # how many demons to track (main list only)


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
                body         = f"No. {position} on the Pointercrate Demon List. Verified by {verifier}.",
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
                    "details":   f"No. {position} on the Demon List — verified by {verifier}",
                    "description": f"No. {position} on the Demon List",
                    "emoji":     "🚨" if position == 1 else ("🏆" if position <= 10 else "🔺"),
                },
            ))

        # Check for first victors on top-50 demons
        first_victors = await _detect_first_victors(demons[:50])
        for fv in first_victors:
            items.append(RawContent(
                source_id    = self.source_id,
                external_id  = f"fv_{fv['demon_id']}_{fv['player']}",
                niche        = self.niche,
                content_type = "first_victor",
                title        = fv["level"],
                url          = fv.get("video", ""),
                body         = f"First victor on \"{fv['level']}\" — {fv['player']}",
                image_url    = "",
                author       = fv["player"],
                score        = max(0, 150 - fv["position"]),
                metadata     = {
                    "level":    fv["level"],
                    "player":   fv["player"],
                    "position": str(fv["position"]),
                },
            ))

        logger.info(
            f"[Pointercrate] fetched {len(items)} items "
            f"(top {_TOP_N} demons + {len(first_victors)} first victors)"
        )
        return items


# ── API helpers ───────────────────────────────────────────────────────────────

async def _fetch_demons(total: int) -> list[dict]:
    """
    Fetch the current top demons sorted by position using the /listed endpoint.
    The /demons/ endpoint paginates by internal ID (not position), so it returns
    demons in ID order rather than position order. /listed returns them correctly
    sorted by position up to 75 entries.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(
                f"{_BASE_URL}/demons/listed",
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            demons = resp.json()
            # Sort by position to be safe, then take top _TOP_N
            return sorted(demons, key=lambda d: d.get("position", 9999))[:total]
        except httpx.HTTPError as exc:
            logger.error(f"[Pointercrate] HTTP error: {exc}")
            return []


async def _detect_first_victors(demons: list[dict]) -> list[dict]:
    """
    Check the records endpoint for each demon. If a demon has exactly 1
    approved record (just the verifier), any new record is a first victor.

    We only check demons that have been on the list long enough to have
    the verifier's record processed. Returns a list of first-victor dicts.
    """
    results: list[dict] = []
    async with httpx.AsyncClient(timeout=15) as client:
        for demon in demons[:20]:  # limit API calls — top 20 only
            demon_id = demon.get("id", 0)
            if not demon_id:
                continue
            try:
                resp = await client.get(
                    f"{_BASE_URL}/demons/{demon_id}/records",
                    headers={"Accept": "application/json"},
                    params={"status": "approved", "limit": 5},
                )
                if resp.status_code != 200:
                    continue
                records = resp.json()
                # First victor = exactly 2 approved records (verifier + 1 person)
                # We only flag this when there are exactly 2 — meaning the second
                # person JUST completed it
                if len(records) == 2:
                    verifier_name = (demon.get("verifier") or {}).get("name", "")
                    for rec in records:
                        player = (rec.get("player") or {}).get("name", "")
                        if player and player != verifier_name:
                            results.append({
                                "demon_id": demon_id,
                                "level":    demon.get("name", "Unknown"),
                                "player":   player,
                                "position": demon.get("position", 999),
                                "video":    rec.get("video") or "",
                            })
            except Exception:
                continue
    return results


def _classify(position: int) -> str:
    if position == 1:
        return "top1_verified"
    if position <= 75:
        return "level_verified"
    return "demon_list_update"
