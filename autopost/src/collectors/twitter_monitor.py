"""
Twitter/X monitor — reads user timelines via TwitterAPI.io REST API.

Uses a simple API key for authentication. No cookies, no scraping,
no account pool needed. Set TWITTERAPI_IO_KEY in .env.

Pricing: ~$0.15 per 1,000 API calls (pay-as-you-go).
Sign up at https://twitterapi.io (free credits on signup, no CC required).

Retweet signals are stored with metadata["retweet_id"] so the poster can
call client.retweet() instead of client.post_tweet().
"""
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import httpx
from loguru import logger

from src.collectors.base import BaseCollector, RawContent
from config.settings import TWITTERAPI_IO_KEY

_ENDPOINT = "https://api.twitterapi.io/twitter/user/last_tweets"

# content_type per niche for monitored account tweets
_CONTENT_TYPE: dict[str, str] = {
    "rocketleague": "official_tweet",
    "geometrydash": "robtop_tweet",
}


class TwitterMonitorCollector(BaseCollector):
    """
    Fetches recent original tweets from one monitored X account via
    TwitterAPI.io REST API.

    config keys (from YAML):
        account_id    (str)  Twitter username without @  e.g. "RocketLeague"
        poll_interval (int)  seconds between polls (used by scheduler)
    """

    def __init__(self, source_id: int, config: dict, niche: str):
        super().__init__(source_id, config)
        self.niche = niche
        self.username = config["account_id"]

    async def collect(self) -> list[RawContent]:
        if not TWITTERAPI_IO_KEY:
            logger.error(
                "[TwitterMonitor] TWITTERAPI_IO_KEY not set — Twitter monitoring disabled"
            )
            return []

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    _ENDPOINT,
                    headers={"X-API-Key": TWITTERAPI_IO_KEY},
                    params={"userName": self.username},
                )
                if resp.status_code == 429:
                    logger.debug(
                        f"[TwitterMonitor] @{self.username} rate-limited (429)"
                    )
                    return []
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error(f"[TwitterMonitor] @{self.username} fetch failed: {exc}")
            return []

        tweets = data.get("tweets", [])
        content_type = _CONTENT_TYPE.get(self.niche, "official_tweet")
        items: list[RawContent] = []

        for tweet in tweets:
            # Skip retweets
            if tweet.get("retweeted_tweet"):
                continue

            # Skip replies
            if tweet.get("isReply"):
                continue

            tweet_id = tweet.get("id", "")
            if not tweet_id:
                continue

            text = tweet.get("text", "")
            if not text:
                continue

            # Skip replies (text directed at another user)
            if text.startswith("@"):
                continue

            # Only accept tweets from the last 7 days
            created_at = tweet.get("createdAt", "")
            if created_at:
                try:
                    tweet_time = parsedate_to_datetime(created_at)
                    if datetime.now(timezone.utc) - tweet_time > timedelta(days=7):
                        continue
                except Exception:
                    pass  # unparseable date — let it through

            author = tweet.get("author", {})
            screen_name = author.get("userName", self.username)
            tweet_url = tweet.get("url", f"https://x.com/{screen_name}/status/{tweet_id}")

            # Extract first image URL from extended entities or entities
            image_url = ""
            try:
                media_list = (
                    tweet.get("extendedEntities", {}).get("media", [])
                    or tweet.get("entities", {}).get("media", [])
                )
                if media_list:
                    image_url = media_list[0].get("media_url_https", "")
            except (AttributeError, IndexError, TypeError):
                pass

            # Expand t.co links using URL entities
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
