"""
Twitter/X monitor — watches accounts via twscrape (Twitter GraphQL API).

Uses cookie-based authentication with account pooling for rate-limit rotation.
Set TWSCRAPE_COOKIES in .env with pipe-separated cookie strings:
    TWSCRAPE_COOKIES=auth_token=abc; ct0=def|auth_token=ghi; ct0=jkl

Extract cookies from browser DevTools → Application → Cookies → x.com
(copy auth_token and ct0 values). Cookies expire periodically — refresh
by updating the env var and restarting the service.

Retweet signals are stored with metadata["retweet_id"] so the poster can
call client.retweet() instead of client.post_tweet().
"""
import re
from datetime import datetime, timezone, timedelta

from loguru import logger
from twscrape import gather

from src.collectors.base import BaseCollector, RawContent
from src.collectors.twscrape_pool import get_api, resolve_user_id

# content_type per niche for monitored account tweets
_CONTENT_TYPE: dict[str, str] = {
    "rocketleague": "official_tweet",
    "geometrydash": "robtop_tweet",
}


class TwitterMonitorCollector(BaseCollector):
    """
    Fetches recent original tweets from one monitored X account via twscrape.

    config keys (from YAML):
        account_id    (str)  Twitter username without @  e.g. "RocketLeague"
        poll_interval (int)  seconds between polls (used by scheduler)
    """

    def __init__(self, source_id: int, config: dict, niche: str):
        super().__init__(source_id, config)
        self.niche = niche
        self.username = config["account_id"]

    async def collect(self) -> list[RawContent]:
        api = await get_api()
        if api is None:
            return []

        user_id = await resolve_user_id(api, self.username)
        if user_id is None:
            return []

        try:
            tweets = await gather(api.user_tweets(user_id, limit=20))
        except Exception as exc:
            logger.error(f"[TwitterMonitor] @{self.username} fetch failed: {exc}")
            return []

        content_type = _CONTENT_TYPE.get(self.niche, "official_tweet")
        items: list[RawContent] = []

        for tweet in tweets:
            # Skip retweets
            if tweet.retweetedTweet is not None:
                continue

            # Skip replies (detected by twscrape field)
            if tweet.inReplyToUser is not None:
                continue

            tweet_id = str(tweet.id)
            if not tweet_id or tweet_id == "0":
                continue

            text = tweet.rawContent or ""
            if not text:
                continue

            # Skip replies (text directed at another user)
            if text.startswith("@"):
                continue

            # Only accept tweets from the last 7 days
            if tweet.date:
                try:
                    tweet_time = tweet.date
                    if tweet_time.tzinfo is None:
                        tweet_time = tweet_time.replace(tzinfo=timezone.utc)
                    age = datetime.now(timezone.utc) - tweet_time
                    if age > timedelta(days=7):
                        continue
                except Exception:
                    pass  # unparseable date — let it through

            screen_name = tweet.user.username if tweet.user else self.username
            tweet_url = tweet.url or f"https://x.com/{screen_name}/status/{tweet_id}"

            # Format created_at for metadata
            created_at = ""
            if tweet.date:
                try:
                    created_at = tweet.date.strftime("%a %b %d %H:%M:%S %z %Y")
                except Exception:
                    created_at = str(tweet.date)

            # Extract first image/video thumbnail
            image_url = ""
            try:
                if tweet.media.photos:
                    image_url = tweet.media.photos[0].url
                elif tweet.media.videos:
                    image_url = tweet.media.videos[0].thumbnailUrl
            except (AttributeError, IndexError):
                pass

            # Expand t.co links using tweet entity data
            clean_text = text
            try:
                for link in (tweet.links or []):
                    if link.tcourl and link.url:
                        clean_text = clean_text.replace(link.tcourl, link.url)
            except (AttributeError, TypeError):
                pass

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
