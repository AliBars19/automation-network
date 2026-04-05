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
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from src.collectors.base import BaseCollector, NICHE_TOPIC_WORDS, RawContent
from src.collectors.url_utils import is_safe_url as _is_safe_url

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

        # Filter off-topic headlines — applied universally to catch entertainment
        # crossover articles (e.g. "The Boys Season 5") on any scraped source.
        before = len(items)
        items = [item for item in items if _is_on_topic(item.title, item.body, self.niche)]
        if before != len(items):
            logger.info(
                f"[Scraper] {self.url[:60]}: topic filter removed "
                f"{before - len(items)}/{before} off-topic headlines"
            )

        if not items and len(html) > 1000:
            logger.warning(f"[Scraper] {self.url[:60]}: 0 headlines from {len(html)} bytes — HTML structure may have changed")
        else:
            logger.info(f"[Scraper] {self.url[:60]}: {len(items)} headlines extracted")
        return items


# ── Fetch ─────────────────────────────────────────────────────────────────────

async def _fetch(url: str) -> str | None:
    if not _is_safe_url(url):
        logger.warning(f"[Scraper] blocked unsafe URL: {url[:80]}")
        return None
    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=False,
            headers=_HEADERS,
        ) as client:
            current_url = url
            for _ in range(5):  # max redirect hops
                resp = await client.get(current_url)
                if resp.is_redirect:
                    next_url = resp.headers.get("location", "")
                    if not _is_safe_url(next_url):
                        logger.warning(f"[Scraper] blocked unsafe redirect: {next_url[:80]}")
                        return None
                    current_url = next_url
                    continue
                break
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
    """Classify scraped headline into a content type.

    Conservative: only classify when the headline clearly indicates the type.
    Scraped articles lack structured metadata, so templates for misclassified
    types would produce broken tweets.  When in doubt, fall through to
    breaking_news (simple title + url template).
    """
    lower = text.lower()

    if niche == "rocketleague":
        # Patch notes — require explicit patch/hotfix language or version number
        if any(k in lower for k in ("patch notes", "hotfix")):
            return "patch_notes"
        if "update" in lower and re.search(r"v?\d+\.\d+", lower):
            return "patch_notes"
        # Esports result — only if title describes an actual match outcome
        if any(k in lower for k in ("grand final", "bracket", "qualifier")):
            if any(w in lower for w in ("wins", "defeat", "beats", "sweep",
                                         "eliminat", "advance", "champion")):
                return "esports_result"
        # Roster changes — require specific transfer verbs
        if any(k in lower for k in ("signs ", "roster change", "joins ",
                                     "released from", "parts ways")):
            return "roster_change"
        # Item shop — very specific keywords only
        if "item shop" in lower:
            return "item_shop"

    else:  # geometrydash
        if any(k in lower for k in ("top 1", "new #1", "new top 1", "hardest level")):
            return "top1_verified"
        # Game update — require GD-specific context
        if any(k in lower for k in ("2.2", "2.3")):
            if any(k in lower for k in ("update", "patch", "released", "out now")):
                return "game_update"
        if "robtop" in lower and any(k in lower for k in ("update", "release", "announce")):
            return "game_update"
        if any(k in lower for k in ("verified", "verification")):
            return "level_verified"
        if any(k in lower for k in ("beaten", "new victor", "first victor")):
            return "level_beaten"
        if any(k in lower for k in ("demon list", "demonlist")):
            return "demon_list_update"
        if "rated" in lower and any(k in lower for k in ("level", "star")):
            return "level_rated"
        if "geode" in lower and any(k in lower for k in ("update", "release", "version")):
            return "mod_update"
        if any(k in lower for k in ("speedrun", "world record")):
            return "speedrun_wr"

    # Default — simple title + url template, always safe for scraped content
    return "breaking_news"


# ── Topic filter ─────────────────────────────────────────────────────────────

def _is_on_topic(title: str, body: str, niche: str) -> bool:
    """Return True if a scraped headline is relevant to the niche.

    Applied universally — not just to known multi-topic domains — to catch
    off-topic entertainment crossover articles (e.g. "The Boys Season 5")
    from any scraped source.
    """
    keywords = NICHE_TOPIC_WORDS.get(niche)
    if not keywords:
        return True
    haystack = (title + " " + body).lower()
    return any(kw in haystack for kw in keywords)
