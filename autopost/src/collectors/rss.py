"""
RSS/Atom collector — fetches feeds with feedparser, maps entries to RawContent.
Content-type is inferred from entry title/tags via keyword matching.
"""
import asyncio
import hashlib
import re
import feedparser
from loguru import logger

from src.collectors.base import BaseCollector, RawContent


# ── Content-type keyword maps ──────────────────────────────────────────────────
# Checked in order; first match wins.  Comparison is lower-case title + summary.

_RL_KEYWORDS: list[tuple[list[str], str]] = [
    (["patch note", "hotfix", "maintenance", "v2.", "v1.", "update notes"], "patch_notes"),
    (["season "],                                                             "season_start"),
    (["item shop"],                                                           "item_shop"),
    (["collab", " x ", "crossover", "partnership"],                          "collab_announcement"),
    (["esports", "rlcs", "championship", "grand final", "major", "league"], "event_announcement"),
    (["roster", " signs ", "signed", "transfer", "free agent"],              "roster_change"),
    (["update", "patch"],                                                     "patch_notes"),
]

_GD_KEYWORDS: list[tuple[list[str], str]] = [
    (["top 1", "new #1", "new top 1", "hardest level"],                       "top1_verified"),
    (["geode", "mod loader"],                                                 "mod_update"),
    (["update", "patch", "new version", " 2.2", " 2.1"],                     "game_update"),
    (["verified", "verification", "two-player", "2-player", "2p", "collab"], "level_verified"),
    (["beaten", "new victor", "first victor", "completes"],                   "level_beaten"),
    (["demon list", "demonlist"],                                             "demon_list_update"),
    (["rated", "star rate"],                                                  "level_rated"),
    (["daily"],                                                               "daily_level"),
    (["weekly demon"],                                                        "weekly_demon"),
]

_DEFAULT_CONTENT_TYPE = {
    "rocketleague": "patch_notes",
    "geometrydash":  "game_update",
}


# ── Collector ──────────────────────────────────────────────────────────────────

class RSSCollector(BaseCollector):
    """One instance per RSS source row. Fetches and parses the feed."""

    # Multi-topic feeds that need per-entry relevance filtering.
    # Niche-specific feeds (Steam, ShiftRLE) skip the filter entirely.
    _MULTI_TOPIC_DOMAINS = {"dexerto.com", "theloadout.com", "esports-news.co.uk"}

    def __init__(self, source_id: int, config: dict, niche: str):
        super().__init__(source_id, config)
        self.niche = niche
        self.url: str = config["url"]
        self._needs_topic_filter = any(d in self.url for d in self._MULTI_TOPIC_DOMAINS)

    async def collect(self) -> list[RawContent]:
        logger.debug(f"[RSS] fetching {self.url}")
        try:
            feed = await asyncio.to_thread(feedparser.parse, self.url)
        except Exception as exc:
            logger.error(f"[RSS] failed to fetch {self.url}: {exc}")
            return []

        if feed.bozo and not feed.entries:
            logger.warning(f"[RSS] bozo feed (malformed) with no entries: {self.url}")
            return []

        items: list[RawContent] = []
        for entry in feed.entries:
            external_id = (
                entry.get("id")
                or entry.get("link")
                or hashlib.md5(entry.get("title", "").encode()).hexdigest()
            )
            title   = _unescape(entry.get("title", ""))
            summary = _strip_html(
                entry.get("summary", "")
                or (entry.get("content") or [{}])[0].get("value", "")
            )
            # Skip off-topic entries from multi-topic feeds (e.g. Dexerto)
            if self._needs_topic_filter and not _is_on_topic(title, summary, entry, self.niche):
                continue
            items.append(RawContent(
                source_id    = self.source_id,
                external_id  = external_id,
                niche        = self.niche,
                content_type = _detect_content_type(title, summary, self.niche),
                title        = title,
                url          = entry.get("link", ""),
                body         = summary,
                image_url    = _extract_image(entry),
                author       = entry.get("author", ""),
                metadata     = {
                    "published": entry.get("published", ""),
                    "tags":      [t.get("term", "") for t in entry.get("tags", [])],
                },
            ))

        logger.info(f"[RSS] {self.url} → {len(items)} entries")
        return items


# ── Helpers ────────────────────────────────────────────────────────────────────

_RL_TOPIC_WORDS = {
    "rocket league", "rlcs", "psyonix", "octane", "fennec", "dominus",
    "aerial", "flip reset", "grand champ", "supersonic legend",
    "item shop", "rocket pass",
}
_GD_TOPIC_WORDS = {
    "geometry dash", "geometrydash", "robtop", "demon list", "demonlist",
    "extreme demon", "pointercrate", "geode", "gdbrowser", "daily level",
    "weekly demon",
}
_NICHE_TOPIC: dict[str, set[str]] = {
    "rocketleague": _RL_TOPIC_WORDS,
    "geometrydash": _GD_TOPIC_WORDS,
}


def _is_on_topic(title: str, summary: str, entry, niche: str) -> bool:
    """Check if an RSS entry is relevant to the niche.

    Niche-specific feeds (Steam News, ShiftRLE) are always on-topic.
    General feeds (Dexerto, Loadout) need keyword validation to prevent
    off-topic articles (e.g. The Boys TV show) from being posted.
    """
    keywords = _NICHE_TOPIC.get(niche)
    if not keywords:
        return True  # unknown niche — let everything through
    haystack = (title + " " + summary).lower()
    # Check entry categories/tags (most reliable for multi-topic feeds)
    tags = [t.get("term", "").lower() for t in entry.get("tags", [])]
    for kw in keywords:
        if kw in haystack or any(kw in tag for tag in tags):
            return True
    return False


def _detect_content_type(title: str, summary: str, niche: str) -> str:
    haystack = (title + " " + summary).lower()
    keyword_map = _RL_KEYWORDS if niche == "rocketleague" else _GD_KEYWORDS
    for keywords, content_type in keyword_map:
        if any(kw in haystack for kw in keywords):
            return content_type
    return _DEFAULT_CONTENT_TYPE.get(niche, "breaking_news")


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _unescape(text: str) -> str:
    """Decode HTML entities (&amp; → &, etc.)."""
    import html
    return html.unescape(text)


def _extract_image(entry) -> str:
    """Return the best image URL from a feedparser entry, or empty string."""
    # media:content
    for m in entry.get("media_content", []):
        if m.get("medium") == "image" or m.get("type", "").startswith("image"):
            return m.get("url", "")
    # media:thumbnail
    for t in entry.get("media_thumbnail", []):
        if t.get("url"):
            return t["url"]
    # enclosures
    for enc in entry.get("enclosures", []):
        if enc.get("type", "").startswith("image"):
            return enc.get("href", "")
    return ""
