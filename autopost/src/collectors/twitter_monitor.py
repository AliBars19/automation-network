"""
Twitter/X monitor — watches official accounts and surfaces their tweets
as retweet signals in the queue.

Uses the same niche credentials as the posting client (read + write on one app).
Requires X API Basic tier or higher for timeline reads.

Retweet signals are stored with tweet_text="RETWEET:{tweet_id}" so
queue.post_next() can call client.retweet() instead of client.post_tweet().
"""
import tweepy
from loguru import logger

from config.settings import NICHE_CREDENTIALS
from src.collectors.base import BaseCollector, RawContent

# content_type per niche for official account tweets
_CONTENT_TYPE: dict[str, str] = {
    "rocketleague": "official_tweet",
    "geometrydash":  "robtop_tweet",
}


class TwitterMonitorCollector(BaseCollector):
    """
    Fetches recent original tweets from one monitored X account.
    config keys (from YAML):
        account_id    (str)  Twitter username without @  e.g. "RocketLeague"
        poll_interval (int)  seconds between polls (used by scheduler)
    """

    def __init__(self, source_id: int, config: dict, niche: str):
        super().__init__(source_id, config)
        self.niche      = niche
        self.username   = config["account_id"]
        self._user_id:  str | None = None   # cached after first lookup

        creds = NICHE_CREDENTIALS[niche]
        self._client = tweepy.Client(
            consumer_key        = creds["api_key"],
            consumer_secret     = creds["api_secret"],
            access_token        = creds["access_token"],
            access_token_secret = creds["access_token_secret"],
        )

    async def collect(self) -> list[RawContent]:
        user_id = self._resolve_user_id()
        if not user_id:
            return []

        try:
            response = self._client.get_users_tweets(
                id             = user_id,
                max_results    = 10,
                exclude        = ["retweets", "replies"],
                tweet_fields   = ["created_at", "entities", "attachments"],
                expansions     = ["attachments.media_keys"],
                media_fields   = ["url", "preview_image_url"],
            )
        except tweepy.TweepyException as exc:
            logger.error(f"[TwitterMonitor] @{self.username} fetch failed: {exc}")
            return []

        if not response.data:
            logger.debug(f"[TwitterMonitor] @{self.username} — no recent tweets")
            return []

        # Build a media_key → url lookup from the includes
        media_map: dict[str, str] = {}
        if response.includes and "media" in response.includes:
            for m in response.includes["media"]:
                key = m.get("media_key", "")
                url = m.get("url") or m.get("preview_image_url") or ""
                if key and url:
                    media_map[key] = url

        content_type = _CONTENT_TYPE.get(self.niche, "official_tweet")
        items: list[RawContent] = []

        for tweet in response.data:
            tweet_id   = str(tweet.id)
            tweet_text = tweet.text
            tweet_url  = f"https://x.com/{self.username}/status/{tweet_id}"

            # Grab first attached image if any
            image_url = ""
            attachments = tweet.attachments or {}
            for mk in attachments.get("media_keys", []):
                if mk in media_map:
                    image_url = media_map[mk]
                    break

            items.append(RawContent(
                source_id    = self.source_id,
                external_id  = tweet_id,
                niche        = self.niche,
                content_type = content_type,
                title        = tweet_text[:100],
                url          = tweet_url,
                body         = tweet_text,
                image_url    = image_url,
                author       = self.username,
                score        = 0,
                metadata     = {
                    "retweet_id":   tweet_id,
                    "account":      self.username,
                    "tweet_url":    tweet_url,
                },
            ))

        logger.info(
            f"[TwitterMonitor] @{self.username} → {len(items)} tweets to consider"
        )
        return items

    def _resolve_user_id(self) -> str | None:
        """Look up and cache the numeric user ID for self.username."""
        if self._user_id:
            return self._user_id
        try:
            resp = self._client.get_user(username=self.username)
            if resp.data:
                self._user_id = str(resp.data.id)
                logger.debug(
                    f"[TwitterMonitor] @{self.username} → user_id {self._user_id}"
                )
                return self._user_id
        except tweepy.TweepyException as exc:
            logger.error(
                f"[TwitterMonitor] could not resolve @{self.username}: {exc}"
            )
        return None
