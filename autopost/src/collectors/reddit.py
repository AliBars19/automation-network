"""
Reddit collector — fetches hot posts from a subreddit via Reddit's public API.
No API credentials required.  Tries JSON endpoint first, falls back to RSS
when JSON is blocked (common on datacenter IPs).
Maps posts to reddit_highlight or community_clip content types.
"""
import re

import feedparser
import httpx
from loguru import logger

from src.collectors.base import BaseCollector, RawContent

# Browser-like UA — Reddit blocks bot-style user agents from server IPs
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Post types that suggest a video/clip rather than a discussion/image
_CLIP_FLAIRS  = {"clip", "highlight", "montage", "insane", "sick"}
_CLIP_DOMAINS = {"youtube.com", "youtu.be", "streamable.com", "medal.tv", "clips.twitch.tv"}

# Keywords that indicate high-priority GD/RL news — these posts bypass min_score
_BREAKING_KEYWORDS = {
    "verified", "top 1", "top 2", "top 3", "#1", "new top", "world record",
    "update", "patch", "robtop", "rlcs", "world championship", "grand final",
    "2-player", "two-player", "2p", "collab verified", "demon list",
    "roster", "signs", "joins", "transfer", "released", "benched",
    "free agent", "picked up", "dropped",
}

_JSON_URL = "https://old.reddit.com/r/{subreddit}/hot.json"
_RSS_URL  = "https://www.reddit.com/r/{subreddit}/.rss"

# Extract Reddit post ID from URL (e.g. /comments/1r6gp5e/...)
_POST_ID_RE = re.compile(r"/comments/(\w+)")


class RedditCollector(BaseCollector):
    """
    Fetches hot posts from one subreddit.
    Tries the JSON API first (has scores). Falls back to RSS (no scores,
    but ordered by Reddit's hot algorithm so top entries are the best).
    config keys (from YAML):
        subreddit     (str)  e.g. "RocketLeague"
        min_score     (int)  minimum upvotes required (JSON only)
        poll_interval (int)  seconds between polls (used by scheduler, not here)
        limit         (int)  max posts to fetch per pass (default 25)
    """

    def __init__(self, source_id: int, config: dict, niche: str):
        super().__init__(source_id, config)
        self.niche      = niche
        self.subreddit  = config["subreddit"]
        self.min_score  = int(config.get("min_score", 50))
        self.limit      = int(config.get("limit", 25))

    async def collect(self) -> list[RawContent]:
        # Try JSON first (has score data for filtering)
        items = await self._collect_json()
        if items is not None:
            return items

        # JSON blocked — fall back to RSS (no scores, hot-ordered)
        return await self._collect_rss()

    # ── JSON path (preferred) ────────────────────────────────────────────────

    async def _collect_json(self) -> list[RawContent] | None:
        """Fetch via JSON API. Returns None if blocked (403/401), list otherwise."""
        url = _JSON_URL.format(subreddit=self.subreddit)
        params = {"limit": self.limit, "raw_json": 1}

        try:
            async with httpx.AsyncClient(headers=_HEADERS, timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, params=params)
                if resp.status_code in (401, 403):
                    return None  # signal to try RSS
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error(f"[Reddit] r/{self.subreddit} JSON failed: {exc}")
            return None

        items: list[RawContent] = []
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})

            score = int(post.get("score", 0))
            if post.get("stickied", False):
                continue

            # Breaking news bypasses min_score if it has at least 10 upvotes
            title_lower = post.get("title", "").lower()
            is_breaking = any(kw in title_lower for kw in _BREAKING_KEYWORDS)
            effective_min = 10 if is_breaking else self.min_score
            if score < effective_min:
                continue

            items.append(RawContent(
                source_id    = self.source_id,
                external_id  = post["id"],
                niche        = self.niche,
                content_type = _detect_content_type_json(post),
                title        = post.get("title", ""),
                url          = f"https://reddit.com{post['permalink']}",
                body         = (post.get("selftext") or "")[:500],
                image_url    = _extract_image_json(post),
                author       = post.get("author", ""),
                score        = score,
                metadata     = {
                    "subreddit":    self.subreddit,
                    "flair":        post.get("link_flair_text") or "",
                    "post_url":     post.get("url", ""),
                    "is_video":     post.get("is_video", False),
                    "upvote_ratio": post.get("upvote_ratio", 0),
                },
            ))

        logger.info(f"[Reddit] r/{self.subreddit} → {len(items)} posts above score {self.min_score}")
        return items

    # ── RSS fallback (datacenter IPs) ────────────────────────────────────────

    async def _collect_rss(self) -> list[RawContent]:
        """Fetch via RSS. No scores available — take top N hot posts."""
        url = _RSS_URL.format(subreddit=self.subreddit)

        try:
            async with httpx.AsyncClient(headers=_HEADERS, timeout=15, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                feed_text = resp.text
        except Exception as exc:
            logger.error(f"[Reddit] r/{self.subreddit} RSS failed: {exc}")
            return []

        feed = feedparser.parse(feed_text)
        # RSS is hot-ordered; take only the top entries as a proxy for min_score
        max_entries = min(self.limit, 10)

        items: list[RawContent] = []
        for entry in feed.entries[:max_entries]:
            title = entry.get("title", "")
            link  = entry.get("link", "")

            # Extract post ID from URL
            m = _POST_ID_RE.search(link)
            post_id = m.group(1) if m else link

            author = entry.get("author", "").removeprefix("/u/")

            # Extract thumbnail from content HTML
            image_url = ""
            content_html = ""
            if entry.get("content"):
                content_html = entry["content"][0].get("value", "")
            img_match = re.search(r'<img[^>]+src="([^"]+)"', content_html)
            if img_match:
                image_url = img_match.group(1)

            # Extract body text from content (strip HTML)
            body = re.sub(r"<[^>]+>", "", content_html)[:500].strip()

            items.append(RawContent(
                source_id    = self.source_id,
                external_id  = post_id,
                niche        = self.niche,
                content_type = _detect_content_type_rss(title, self.niche),
                title        = title,
                url          = link,
                body         = body,
                image_url    = image_url,
                author       = author,
                score        = 0,
                metadata     = {
                    "subreddit": self.subreddit,
                    "flair":     "",
                    "source":    "rss_fallback",
                },
            ))

        logger.info(f"[Reddit] r/{self.subreddit} → {len(items)} posts via RSS fallback")
        return items


# ── Helpers (JSON path) ───────────────────────────────────────────────────────

def _detect_content_type_rss(title: str, niche: str) -> str:
    """Keyword-based content type detection for RSS entries (no flair/domain)."""
    return _detect_content_type_from_title(title.lower(), niche)


def _detect_content_type_from_title(title: str, niche: str) -> str:
    """Shared title-keyword detection used by both JSON and RSS paths."""
    # GD-specific
    if any(kw in title for kw in ("top 1", "new #1", "new top")):
        return "top1_verified"
    if any(kw in title for kw in ("verified", "verification")):
        return "level_verified"
    if any(kw in title for kw in ("beaten", "new victor", "first victor")):
        return "level_beaten"
    if any(kw in title for kw in ("demon list", "demonlist")):
        return "demon_list_update"

    # RL-specific
    if any(kw in title for kw in ("roster", "signs", "joins", "transfer",
                                   "leaves", "released", "benched", "sub",
                                   "free agent", "picked up", "dropped")):
        return "roster_change"
    if any(kw in title for kw in ("rlcs", "grand final", "championship",
                                   "major", "worlds")):
        return "esports_result"
    if any(kw in title for kw in ("update", "patch", "hotfix")):
        if niche == "rocketleague":
            return "patch_notes"
        return "game_update"
    if any(kw in title for kw in ("season", "new season")):
        return "season_start"
    if any(kw in title for kw in ("item shop",)):
        return "item_shop"

    return "reddit_highlight"


def _detect_content_type_json(post: dict) -> str:
    flair  = (post.get("link_flair_text") or "").lower()
    domain = post.get("domain") or ""
    title  = (post.get("title") or "").lower()

    # Title-keyword detection (shared with RSS path)
    from_title = _detect_content_type_from_title(title, "")
    if from_title != "reddit_highlight":
        return from_title

    # JSON-only: flair / domain / video detection for clips
    if flair in _CLIP_FLAIRS or any(d in domain for d in _CLIP_DOMAINS) or post.get("is_video"):
        return "community_clip"
    return "reddit_highlight"


def _extract_image_json(post: dict) -> str:
    """Return a direct image URL from the post if available."""
    url = post.get("url") or ""
    if any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp")):
        return url

    # Reddit-hosted preview image (highest resolution)
    try:
        previews = post["preview"]["images"][0]["resolutions"]
        if previews:
            return previews[-1]["url"]
    except (KeyError, IndexError, TypeError):
        pass

    return ""
