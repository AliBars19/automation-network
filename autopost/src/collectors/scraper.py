"""
Generic BeautifulSoup headline scraper.

Visits a URL, extracts news headline + link pairs from article/heading
elements, and surfaces them as RawContent.  No site-specific parsing —
works on any standard news/blog layout that uses <article>, <h2>, or <h3>
tags.  Activated for every source with type: scraper in the YAML configs.

Content-type is inferred from keyword matching (same approach as the RSS
collector).  The URL's MD5 hash is used as the stable external_id so the
dedup layer correctly ignores re-visits of the same article.
"""
import hashlib
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from src.collectors.base import BaseCollector, RawContent

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; AutoPost/1.0; "
        "+https://github.com/AliBars19/automation-network)"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_MAX_ITEMS = 10     # cap per scrape run
_MIN_TITLE = 15     # ignore headings shorter than this


class ScraperCollector(BaseCollector):
    """Fetches a URL and extracts headline/link pairs as RawContent items."""

    def __init__(self, source_id: int, config: dict, niche: str):
        super().__init__(source_id, config)
        self.niche = niche
        self.url   = config.get("url", "")

    async def collect(self) -> list[RawContent]:
        if not self.url:
            logger.warning("[Scraper] no URL configured for source")
            return []

        html = await _fetch(self.url)
        if not html:
            return []

        items = _parse(html, self.url, self.source_id, self.niche)
        logger.info(f"[Scraper] {self.url[:60]}: {len(items)} headlines extracted")
        return items


# ── Fetch ─────────────────────────────────────────────────────────────────────

async def _fetch(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers=_HEADERS,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPError as exc:
        logger.warning(f"[Scraper] fetch failed {url[:60]}: {exc}")
        return None


# ── Parse ─────────────────────────────────────────────────────────────────────

def _parse(html: str, base_url: str, source_id: int, niche: str) -> list[RawContent]:
    soup      = BeautifulSoup(html, "html.parser")
    parsed    = urlparse(base_url)
    origin    = f"{parsed.scheme}://{parsed.netloc}"
    candidates: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    # Strategy 1: <article> elements — most semantically correct for news sites
    for article in soup.find_all("article", limit=30):
        heading = article.find(["h1", "h2", "h3"])
        link    = article.find("a", href=True)
        if heading and link:
            candidates.append((heading.get_text(strip=True), link["href"]))

    # Strategy 2: headings that directly wrap or are adjacent to a link
    if len(candidates) < 3:
        for tag in soup.find_all(["h2", "h3"], limit=40):
            link = tag.find("a", href=True)
            if not link:
                # try the heading itself being inside an <a>
                parent_a = tag.find_parent("a", href=True)
                link = parent_a
            if link and link.get("href"):
                text = tag.get_text(strip=True)
                if len(text) >= _MIN_TITLE:
                    candidates.append((text, link["href"]))

    items: list[RawContent] = []
    for title, href in candidates:
        title = title.strip()
        if len(title) < _MIN_TITLE:
            continue

        # Resolve relative URLs
        if href.startswith("//"):
            href = f"{parsed.scheme}:{href}"
        elif href.startswith("/"):
            href = f"{origin}{href}"
        elif not href.startswith("http"):
            continue

        # Strip anchors / query params for stable dedup
        href = href.split("#")[0].rstrip("/")

        if href in seen_urls:
            continue
        seen_urls.add(href)

        external_id  = f"scrape_{hashlib.md5(href.encode()).hexdigest()[:16]}"
        content_type = _classify(title, niche)

        items.append(RawContent(
            source_id    = source_id,
            external_id  = external_id,
            niche        = niche,
            content_type = content_type,
            title        = title[:200],
            url          = href,
            body         = "",
            image_url    = "",
            author       = "",
            score        = 0,
            metadata     = {"title": title[:200]},
        ))

        if len(items) >= _MAX_ITEMS:
            break

    return items


# ── Content-type classifier ───────────────────────────────────────────────────

def _classify(text: str, niche: str) -> str:
    lower = text.lower()

    if niche == "rocketleague":
        if any(k in lower for k in ("patch", "update", "v2.", "hotfix", "fix")):
            return "patch_notes"
        if any(k in lower for k in ("rlcs", "major", "championship", "grand final",
                                     "tournament", "bracket", "qualifier")):
            return "esports_result"
        if any(k in lower for k in ("signs", "roster", "trade", "transfer",
                                     "leaves", "joins", "released")):
            return "roster_change"
        if any(k in lower for k in ("new season", "season start", "season launch")):
            return "season_start"
        if any(k in lower for k in ("collab", "collaboration", " x ", "crossover",
                                     "partnership")):
            return "collab_announcement"
        if any(k in lower for k in ("item shop", "shop update", "black market",
                                     "painted", "decal")):
            return "item_shop"
        if any(k in lower for k in ("event", "tournament", "cup", "championship")):
            return "event_announcement"

    else:  # geometrydash
        if any(k in lower for k in ("update", "geometry dash 2", "robtop", "2.2",
                                     "patch", "hotfix")):
            return "game_update"
        if any(k in lower for k in ("verified", "demon", "extreme", "top 1", "#1")):
            return "level_verified"
        if any(k in lower for k in ("geode", "mod", "plugin", "modding")):
            return "mod_update"
        if any(k in lower for k in ("rated", "rating", "star", "new level")):
            return "level_rated"
        if any(k in lower for k in ("speedrun", "world record", "wr", "any%")):
            return "speedrun_wr"

    # Generic fallback — formats as "{title}\n\n📎 {url}"
    return "reddit_highlight"
