"""
Twitter/X monitor — watches accounts via Twitter's internal GraphQL API.

Uses cookie-based authentication. Set TWSCRAPE_COOKIES in .env:
    TWSCRAPE_COOKIES=auth_token=abc; ct0=def

Retweet signals are stored with metadata["retweet_id"] so the poster can
call client.retweet() instead of client.post_tweet().
"""
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

from loguru import logger

from src.collectors.base import BaseCollector, RawContent
from src.collectors.twscrape_pool import OP_USER_TWEETS, get_api, resolve_user_id

# content_type per niche for accounts explicitly marked retweet: true
_RETWEET_CONTENT_TYPE: dict[str, str] = {
    "rocketleague": "official_tweet",
    "geometrydash": "robtop_tweet",
}

_TRAILING_TCO_RE = re.compile(r"\s*https://t\.co/\w+\s*$")

_USER_TWEETS_FEATURES = {
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
}


class TwitterMonitorCollector(BaseCollector):
    """
    Fetches recent original tweets from one monitored X account via
    Twitter's internal GraphQL API.

    config keys (from YAML):
        account_id    (str)  Twitter username without @  e.g. "RocketLeague"
        poll_interval (int)  seconds between polls (used by scheduler)
    """

    def __init__(self, source_id: int, config: dict, niche: str):
        super().__init__(source_id, config)
        self.niche = niche
        self.username = config["account_id"]
        self.is_retweet_source = config.get("retweet", False)

    async def collect(self) -> list[RawContent]:
        client = await get_api()
        if client is None:
            return []

        user_id = await resolve_user_id(client, self.username)
        if user_id is None:
            return []

        try:
            data = await client.gql_get(
                OP_USER_TWEETS,
                {
                    "userId": str(user_id),
                    "count": 20,
                    "includePromotedContent": False,
                    "withQuickPromoteEligibilityTweetFields": True,
                    "withVoice": True,
                    "withV2Timeline": True,
                },
                _USER_TWEETS_FEATURES,
            )
        except Exception as exc:
            logger.error(f"[TwitterMonitor] @{self.username} fetch failed: {exc}")
            return []

        tweets = _extract_tweets(data)
        if self.is_retweet_source:
            content_type = _RETWEET_CONTENT_TYPE.get(self.niche, "official_tweet")
        else:
            content_type = "monitored_tweet"
        items: list[RawContent] = []

        for tweet in tweets:
            legacy = tweet.get("legacy", {})
            core = tweet.get("core", {}).get("user_results", {}).get("result", {})

            # Skip retweets
            if "retweeted_status_result" in tweet:
                continue

            # Skip replies
            if legacy.get("in_reply_to_user_id_str"):
                continue

            tweet_id = legacy.get("id_str", "")
            if not tweet_id:
                continue

            text = legacy.get("full_text", "")
            if not text:
                continue

            # Skip replies (text directed at another user)
            if text.startswith("@"):
                continue

            # Only accept tweets from the last 7 days
            created_at = legacy.get("created_at", "")
            if created_at:
                try:
                    tweet_time = parsedate_to_datetime(created_at)
                    if datetime.now(timezone.utc) - tweet_time > timedelta(days=7):
                        continue
                except Exception:
                    pass  # unparseable date — let it through

            screen_name = (
                core.get("legacy", {}).get("screen_name")
                or self.username
            )
            tweet_url = f"https://x.com/{screen_name}/status/{tweet_id}"

            # Extract first image URL from media entities
            image_url = ""
            try:
                media_list = (
                    legacy.get("extended_entities", {}).get("media", [])
                    or legacy.get("entities", {}).get("media", [])
                )
                if media_list:
                    image_url = media_list[0].get("media_url_https", "")
            except (AttributeError, IndexError, TypeError):
                pass

            # Expand t.co links
            clean_text = text
            for url_entity in legacy.get("entities", {}).get("urls", []):
                short = url_entity.get("url", "")
                expanded = url_entity.get("expanded_url", "")
                if short and expanded:
                    clean_text = clean_text.replace(short, expanded)

            clean_text = _TRAILING_TCO_RE.sub("", clean_text).strip()

            meta = {
                "account": screen_name,
                "tweet_url": tweet_url,
                "created_at": created_at,
            }
            if self.is_retweet_source:
                meta["retweet_id"] = tweet_id

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
                    metadata=meta,
                )
            )

        logger.info(
            f"[TwitterMonitor] @{self.username} → {len(items)} tweets to consider"
        )
        return items


def _extract_tweets(data: dict) -> list[dict]:
    """Walk the GraphQL response tree to find tweet result objects."""
    tweets: list[dict] = []
    seen: set[str] = set()
    stack: list = [data]
    while stack:
        obj = stack.pop()
        if isinstance(obj, dict):
            legacy = obj.get("legacy")
            if isinstance(legacy, dict) and "full_text" in legacy:
                tid = legacy.get("id_str", "")
                if tid and tid not in seen:
                    seen.add(tid)
                    tweets.append(obj)
            stack.extend(obj.values())
        elif isinstance(obj, list):
            stack.extend(obj)
    return tweets
