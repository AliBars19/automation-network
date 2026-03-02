"""
Reddit collector — fetches hot posts from a subreddit via Reddit's public JSON API.
No API credentials required.  Filters by min_score to avoid low-quality content.
Maps posts to reddit_highlight or community_clip content types.
"""
import httpx
from loguru import logger

from src.collectors.base import BaseCollector, RawContent

# Browser-like UA — Reddit blocks bot-style user agents from server IPs
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Post types that suggest a video/clip rather than a discussion/image
_CLIP_FLAIRS  = {"clip", "highlight", "montage", "insane", "sick"}
_CLIP_DOMAINS = {"youtube.com", "youtu.be", "streamable.com", "medal.tv", "clips.twitch.tv"}

# old.reddit.com is less aggressive about blocking non-authenticated requests
_BASE_URL = "https://old.reddit.com/r/{subreddit}/hot.json"


class RedditCollector(BaseCollector):
    """
    Fetches hot posts from one subreddit using Reddit's public JSON endpoint.
    config keys (from YAML):
        subreddit     (str)  e.g. "RocketLeague"
        min_score     (int)  minimum upvotes required
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
        logger.debug(f"[Reddit] r/{self.subreddit} — fetching top {self.limit} hot posts")

        url = _BASE_URL.format(subreddit=self.subreddit)
        params = {"limit": self.limit, "raw_json": 1}
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        }

        items: list[RawContent] = []
        try:
            async with httpx.AsyncClient(headers=headers, timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(f"[Reddit] r/{self.subreddit} failed: HTTP {exc.response.status_code}")
            return items
        except Exception as exc:
            logger.error(f"[Reddit] r/{self.subreddit} failed: {exc}")
            return items

        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})

            score = int(post.get("score", 0))
            if score < self.min_score:
                continue
            if post.get("stickied", False):
                continue

            content_type = _detect_content_type(post)
            image_url    = _extract_image(post)

            items.append(RawContent(
                source_id    = self.source_id,
                external_id  = post["id"],
                niche        = self.niche,
                content_type = content_type,
                title        = post.get("title", ""),
                url          = f"https://reddit.com{post['permalink']}",
                body         = (post.get("selftext") or "")[:500],
                image_url    = image_url,
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_content_type(post: dict) -> str:
    flair  = (post.get("link_flair_text") or "").lower()
    domain = post.get("domain") or ""

    if flair in _CLIP_FLAIRS or any(d in domain for d in _CLIP_DOMAINS) or post.get("is_video"):
        return "community_clip"
    return "reddit_highlight"


def _extract_image(post: dict) -> str:
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
