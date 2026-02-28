"""
GitHub Releases collector — polls the GitHub public API for new releases.

Config fields (from YAML):
  repo:  owner/repo  e.g. "geode-sdk/geode"

No authentication required for public repos.
Unauthenticated rate limit: 60 requests/hour per IP (ample for 3600s polling).
Pre-releases and draft releases are skipped — only stable releases are queued.

API reference: https://docs.github.com/en/rest/releases/releases
"""
import httpx
from loguru import logger

from src.collectors.base import BaseCollector, RawContent

_BASE_URL  = "https://api.github.com"
_HEADERS   = {
    "Accept":     "application/vnd.github+json",
    "User-Agent": "AutoPost/1.0",
}
_MAX_RELEASES = 5   # only look at the most recent releases per poll


class GitHubCollector(BaseCollector):
    """Surfaces new stable GitHub releases as RawContent mod_update items."""

    def __init__(self, source_id: int, config: dict, niche: str = "geometrydash"):
        super().__init__(source_id, config)
        self.niche = niche
        self.repo  = config.get("repo", "")

    async def collect(self) -> list[RawContent]:
        if not self.repo:
            logger.warning("[GitHub] no repo configured")
            return []

        releases = await _fetch_releases(self.repo)
        if not releases:
            return []

        items: list[RawContent] = []
        repo_name = self.repo.split("/")[-1]

        for release in releases[:_MAX_RELEASES]:
            # Skip pre-releases and drafts
            if release.get("prerelease") or release.get("draft"):
                continue

            tag        = release.get("tag_name", "")
            name       = (release.get("name") or tag).strip()
            body_raw   = release.get("body") or ""
            url        = release.get("html_url", "")
            release_id = str(release.get("id", ""))

            # Trim body to first 5 lines for the tweet template
            body_short = "\n".join(
                line for line in body_raw.split("\n")[:6] if line.strip()
            )[:300]

            items.append(RawContent(
                source_id    = self.source_id,
                external_id  = f"gh_{release_id}",
                niche        = self.niche,
                content_type = "mod_update",
                title        = f"{name} ({repo_name})",
                url          = url,
                body         = body_short,
                image_url    = "",
                author       = "",
                score        = 0,
                metadata     = {
                    "mod_name":     name,
                    "version":      tag,
                    "download_url": url,
                    "description":  body_short or f"{repo_name} {tag} released",
                },
            ))

        logger.info(f"[GitHub] {self.repo}: {len(items)} releases fetched")
        return items


# ── API helper ────────────────────────────────────────────────────────────────

async def _fetch_releases(repo: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
        try:
            resp = await client.get(f"{_BASE_URL}/repos/{repo}/releases")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.error(f"[GitHub] HTTP error fetching {repo}: {exc}")
            return []
