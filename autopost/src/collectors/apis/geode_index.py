"""
Geode Index API collector — surfaces popular/featured mod updates from the
official Geode mod index (api.geode-sdk.org).

The Geode mods website (geode-sdk.org/mods) is a SvelteKit SPA that renders
nothing server-side, so scraping it yields 0 results.  This collector hits
the underlying REST API directly.

Filtering logic (only post mods that matter to the community):
  - featured: true  → always post (curated by Geode team)
  - download_count >= 25_000  → popular enough to be newsworthy

External ID: "geode_mod_{mod_id}_{version}" — one entry per version so
the same mod can be posted again when a new version ships.

Config fields (from YAML):
  min_downloads: int  (default: 25000)
  max_items: int      (default: 3)
"""
import httpx
from loguru import logger

from src.collectors.base import BaseCollector, RawContent

_API_BASE = "https://api.geode-sdk.org/v1"
_TIMEOUT  = 15
_DEFAULT_MIN_DOWNLOADS = 25_000
_DEFAULT_MAX_ITEMS     = 3
_PAGE_SIZE             = 25   # fetch more than we need so filters have room to work


class GeodeIndexCollector(BaseCollector):
    """Fetches recently updated popular/featured Geode mods."""

    def __init__(self, source_id: int, config: dict, niche: str = "geometrydash"):
        super().__init__(source_id, config)
        self.niche        = niche
        self.min_downloads = int(config.get("min_downloads", _DEFAULT_MIN_DOWNLOADS))
        self.max_items     = int(config.get("max_items", _DEFAULT_MAX_ITEMS))

    async def collect(self) -> list[RawContent]:
        mods = await _fetch_recent_mods()
        if not mods:
            return []

        items: list[RawContent] = []
        for mod in mods:
            if len(items) >= self.max_items:
                break

            is_featured      = mod.get("featured", False)
            total_downloads  = mod.get("download_count", 0)

            if not is_featured and total_downloads < self.min_downloads:
                continue

            versions = mod.get("versions", [])
            if not versions:
                continue

            latest   = versions[0]
            mod_id   = mod.get("id", "")
            name     = latest.get("name", mod_id)
            version  = latest.get("version", "")
            desc     = latest.get("description", "")
            dl_link  = latest.get("download_link", "")

            if not mod_id or not version:
                continue

            # Build a human-friendly URL: source link > homepage > download link
            links       = mod.get("links") or {}
            source_url  = links.get("source", "") or links.get("homepage", "") or dl_link

            developers  = mod.get("developers", [])
            author      = developers[0].get("display_name", "") if developers else ""

            items.append(RawContent(
                source_id    = self.source_id,
                external_id  = f"geode_mod_{mod_id}_{version}",
                niche        = self.niche,
                content_type = "community_mod_update",
                title        = f"{name} {version} (Geode mod)",
                url          = source_url,
                body         = desc,
                image_url    = "",
                author       = author,
                score        = total_downloads,
                metadata     = {
                    "mod_name":    name,
                    "version":     version,
                    "description": desc[:200] if desc else f"{name} has been updated to {version}.",
                    "summary":     desc[:150] if desc else f"{name} — updated to {version}.",
                    "download_url": dl_link,
                },
            ))

        logger.info(
            f"[GeodeIndex] {len(items)} mod update(s) surfaced "
            f"(min_downloads={self.min_downloads})"
        )
        return items


async def _fetch_recent_mods() -> list[dict]:
    """Fetch the most recently updated accepted mods from the Geode Index API."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(
                f"{_API_BASE}/mods",
                params={
                    "status":   "accepted",
                    "per_page": _PAGE_SIZE,
                    "sort":     "recently_updated",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("payload", {}).get("data", [])
        except httpx.HTTPError as exc:
            logger.warning(f"[GeodeIndex] API fetch failed: {exc}")
            return []
        except Exception as exc:
            logger.warning(f"[GeodeIndex] unexpected error: {exc}")
            return []
