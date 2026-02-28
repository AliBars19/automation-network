"""
Reddit collector — fetches hot posts from a subreddit via asyncpraw.
Filters by min_score to avoid low-quality content.
Maps posts to reddit_highlight or community_clip content types.
"""
import asyncpraw
from loguru import logger

from config.settings import REDDIT_CONFIG
from src.collectors.base import BaseCollector, RawContent

# Post types that suggest a video/clip rather than a discussion/image
_CLIP_FLAIRS  = {"clip", "highlight", "montage", "insane", "sick"}
_CLIP_DOMAINS = {"youtube.com", "youtu.be", "streamable.com", "medal.tv", "clips.twitch.tv"}


class RedditCollector(BaseCollector):
    """
    Fetches hot posts from one subreddit.
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

        reddit = asyncpraw.Reddit(
            client_id     = REDDIT_CONFIG["client_id"],
            client_secret = REDDIT_CONFIG["client_secret"],
            user_agent    = REDDIT_CONFIG["user_agent"],
        )

        items: list[RawContent] = []
        try:
            subreddit = await reddit.subreddit(self.subreddit)
            async for post in subreddit.hot(limit=self.limit):
                if post.score < self.min_score:
                    continue
                if post.stickied:
                    continue

                content_type = _detect_content_type(post)
                image_url    = _extract_image(post)

                items.append(RawContent(
                    source_id    = self.source_id,
                    external_id  = post.id,
                    niche        = self.niche,
                    content_type = content_type,
                    title        = post.title,
                    url          = f"https://reddit.com{post.permalink}",
                    body         = post.selftext[:500] if post.selftext else "",
                    image_url    = image_url,
                    author       = str(post.author) if post.author else "",
                    score        = post.score,
                    metadata     = {
                        "subreddit":  self.subreddit,
                        "flair":      post.link_flair_text or "",
                        "post_url":   post.url,
                        "is_video":   post.is_video,
                        "upvote_ratio": post.upvote_ratio,
                    },
                ))
        except Exception as exc:
            logger.error(f"[Reddit] r/{self.subreddit} failed: {exc}")
        finally:
            await reddit.close()

        logger.info(f"[Reddit] r/{self.subreddit} → {len(items)} posts above score {self.min_score}")
        return items


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_content_type(post) -> str:
    flair  = (post.link_flair_text or "").lower()
    domain = getattr(post, "domain", "") or ""

    if flair in _CLIP_FLAIRS or any(d in domain for d in _CLIP_DOMAINS) or post.is_video:
        return "community_clip"
    return "reddit_highlight"


def _extract_image(post) -> str:
    """Return a direct image URL from the post if available."""
    # Direct image link
    url = post.url or ""
    if any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp")):
        return url

    # Reddit-hosted preview image (highest resolution)
    try:
        previews = post.preview["images"][0]["resolutions"]
        if previews:
            return previews[-1]["url"].replace("&amp;", "&")
    except (AttributeError, KeyError, IndexError):
        pass

    return ""
