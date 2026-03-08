"""
Twitter/X monitor — watches official accounts by scraping the public
syndication endpoint.  No API credentials required.

Uses https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}
which returns server-rendered HTML with a __NEXT_DATA__ JSON blob containing
full tweet data (text, media, timestamps, etc.).

Retweet signals are stored with metadata["retweet_id"] so the poster can
call client.retweet() instead of client.post_tweet().
"""
import json
import re

import httpx
from loguru import logger

from src.collectors.base import BaseCollector, RawContent

_SYNDICATION_URL = (
    "https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_NEXT_DATA_RE = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)

# content_type per niche for official account tweets
_CONTENT_TYPE: dict[str, str] = {
    "rocketleague": "official_tweet",
    "geometrydash": "robtop_tweet",
}


class TwitterMonitorCollector(BaseCollector):
    """
    Fetches recent original tweets from one monitored X account via the
    public syndication embed endpoint.

    config keys (from YAML):
        account_id    (str)  Twitter username without @  e.g. "RocketLeague"
        poll_interval (int)  seconds between polls (used by scheduler)
    """

    def __init__(self, source_id: int, config: dict, niche: str):
        super().__init__(source_id, config)
        self.niche = niche
        self.username = config["account_id"]

    async def collect(self) -> list[RawContent]:
        url = _SYNDICATION_URL.format(username=self.username)

        try:
            async with httpx.AsyncClient(
                headers=_HEADERS, timeout=15, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 404:
                    logger.warning(
                        f"[TwitterMonitor] @{self.username} returned 404 — "
                        f"account may not exist"
                    )
                    return []
                if resp.status_code == 429:
                    logger.debug(
                        f"[TwitterMonitor] @{self.username} rate-limited (429)"
                    )
                    return []
                resp.raise_for_status()
                html = resp.text
        except Exception as exc:
            logger.error(f"[TwitterMonitor] @{self.username} fetch failed: {exc}")
            return []

        # Extract __NEXT_DATA__ JSON blob
        m = _NEXT_DATA_RE.search(html)
        if not m:
            logger.warning(
                f"[TwitterMonitor] @{self.username} — no __NEXT_DATA__ in response"
            )
            return []

        try:
            data = json.loads(m.group(1))
            entries = (
                data.get("props", {})
                .get("pageProps", {})
                .get("timeline", {})
                .get("entries", [])
            )
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error(
                f"[TwitterMonitor] @{self.username} JSON parse failed: {exc}"
            )
            return []

        content_type = _CONTENT_TYPE.get(self.niche, "official_tweet")
        items: list[RawContent] = []

        for entry in entries:
            if entry.get("type") != "tweet":
                continue

            tweet = entry.get("content", {}).get("tweet", {})
            if not tweet:
                continue

            # Skip retweets
            if tweet.get("retweeted_tweet"):
                continue

            # Use conversation_id_str as the tweet ID (id field is always 0)
            tweet_id = tweet.get("conversation_id_str", "")
            if not tweet_id:
                continue

            text = tweet.get("text", "")
            if not text:
                continue

            # Skip replies (tweet directed at another user)
            if text.startswith("@"):
                continue

            created_at = tweet.get("created_at", "")
            user = tweet.get("user", {})
            screen_name = user.get("screen_name", self.username)

            tweet_url = f"https://x.com/{screen_name}/status/{tweet_id}"

            # Extract first image/video thumbnail
            image_url = ""
            media_list = tweet.get("entities", {}).get("media", [])
            if media_list:
                media = media_list[0]
                image_url = media.get("media_url_https", "")

            # Extract expanded URLs to replace t.co links
            clean_text = text
            for url_entity in tweet.get("entities", {}).get("urls", []):
                short = url_entity.get("url", "")
                expanded = url_entity.get("expanded_url", "")
                if short and expanded:
                    clean_text = clean_text.replace(short, expanded)

            # Remove trailing t.co media links
            clean_text = re.sub(r"\s*https://t\.co/\w+\s*$", "", clean_text).strip()

            items.append(
                RawContent(
                    source_id=self.source_id,
                    external_id=tweet_id,
                    niche=self.niche,
                    content_type=content_type,
                    title=clean_text[:100],
                    url=tweet_url,
                    body=clean_text,
                    image_url=image_url,
                    author=screen_name,
                    score=0,
                    metadata={
                        "retweet_id": tweet_id,
                        "account": screen_name,
                        "tweet_url": tweet_url,
                        "created_at": created_at,
                    },
                )
            )

        logger.info(
            f"[TwitterMonitor] @{self.username} → {len(items)} tweets to consider"
        )
        return items
