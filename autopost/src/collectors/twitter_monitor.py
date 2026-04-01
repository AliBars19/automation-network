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

# ── Relevance keywords per niche ─────────────────────────────────────────────
# Tweets from retweet sources must contain at least one keyword to be retweeted.
# Official accounts (e.g. @RocketLeague) almost always post on-topic, but this
# filter catches the occasional off-topic tweet, personal reply, or promo.

_RL_RELEVANCE: set[str] = {
    # Game identity
    "rocket league", "rlcs", "psyonix",
    # Vehicles / items
    "octane", "fennec", "dominus", "decal", "goal explosion",
    "item shop", "battle pass", "rocket pass",
    # Mechanics
    "aerial", "flip reset", "musty flick", "ceiling shot", "air dribble",
    # Modes
    "hoops", "dropshot", "rumble", "snowday",
    # Scoped updates / esports
    "rl patch", "rl update", "rl hotfix", "rl season",
    "rlcs major", "rlcs worlds", "rlcs regional",
    "rl roster", "rl free agent", "rl transfer",
    "grand champ", "supersonic legend",
    # Hashtags
    "#rlcs", "#rocketleague",
}

_GD_RELEVANCE: set[str] = {
    # Game identity
    "geometry dash", "geometrydash", "robtop", "rubrub",
    # Demon list
    "demon list", "demonlist", "extreme demon", "insane demon",
    "pointercrate", "aredl", "challenge list",
    # Scoped actions (require GD context words)
    "gd verified", "gd verification", "gd beaten", "gd rated",
    "gd featured", "star rate",
    # Game features
    "daily level", "weekly demon", "gauntlet", "map pack",
    "geode", "texture pack", "megahack",
    "gd level", "gd update", "gd mod", "gd creator",
    "gdbrowser", "newgrounds",
    # Known community figures (scoped names)
    "dashword", "gd colon", "wulzy", "npesta",
    "aeonair", "viprin", "doggie",
    # Scoped records / stats
    "gd world record", "demon list top",
    # Hashtags
    "#geometrydash", "#gd", "#demonlist",
}

_NICHE_RELEVANCE: dict[str, set[str]] = {
    "rocketleague": _RL_RELEVANCE,
    "geometrydash": _GD_RELEVANCE,
}


def is_relevant(text: str, niche: str) -> bool:
    """Return True if tweet text contains at least one niche keyword."""
    keywords = _NICHE_RELEVANCE.get(niche)
    if not keywords:
        return True  # unknown niche — let everything through
    lowered = text.lower()
    return any(kw in lowered for kw in keywords)

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

            # Skip native retweets that weren't caught by retweeted_status_result
            # (text-form RTs start with "RT @")
            if text.startswith("RT @"):
                continue

            # Skip low-quality tweets: emoji-only, too short, or no substance.
            # Retweet sources (official accounts) get a lower bar — they're
            # curated and their short hype tweets are part of the brand.
            import re as _tweet_re
            text_no_urls = _tweet_re.sub(r"https?://\S+", "", text).strip()
            text_no_emoji = _tweet_re.sub(
                r"[\U0001F600-\U0001FAFF\U00002600-\U000027BF\u200d\ufe0f]+", "", text_no_urls
            ).strip()
            _min_len = 15 if self.is_retweet_source else 30
            if len(text_no_emoji) < _min_len:
                logger.debug(
                    f"[TwitterMonitor] @{self.username} tweet {tweet_id} "
                    f"too short/emoji-only ({len(text_no_emoji)} chars) — skipping"
                )
                continue

            # Skip filler/personality tweets that have no news value
            _FILLER_RE = [
                _tweet_re.compile(r"^(hmm+|ah+|oh+|wow+|lol+|lmao|bruh|haha+)[.!?…\s]*$", _tweet_re.I),
                _tweet_re.compile(r"^\d+-\d+\.?\s*$"),  # bare scores "3-0."
                _tweet_re.compile(r"^(a{3,}h|o{3,}h|e{3,})", _tweet_re.I),  # "aaaaaaaah"
            ]
            if any(pat.match(text_no_emoji.strip()) for pat in _FILLER_RE):
                logger.debug(
                    f"[TwitterMonitor] @{self.username} tweet {tweet_id} "
                    f"filler pattern — skipping"
                )
                continue

            # Substance check for non-retweet (monitored/news) accounts only.
            # Retweet sources skip this — their content is already curated
            # and short hype tweets like "PLAYOFF SATURDAY IS LIVE" are legit.
            if not self.is_retweet_source:
                has_substance = (
                    _tweet_re.search(r"(?<!\A)\b[A-Z][a-z]{2,}", text_no_urls)  # proper noun
                    or _tweet_re.search(r"\d", text_no_urls)                     # number
                    or "#" in text_no_urls                                        # hashtag
                    or "@" in text_no_urls                                        # mention
                )
                if not has_substance and len(text_no_emoji) < 60:
                    logger.debug(
                        f"[TwitterMonitor] @{self.username} tweet {tweet_id} "
                        f"lacks news substance — skipping"
                    )
                    continue

            # Skip non-English tweets — both accounts target English audiences.
            # Check Twitter's lang field first, then text-based French detection.
            tweet_lang = legacy.get("lang", "")
            if tweet_lang and tweet_lang not in ("en", "und", "qme", "qht", "zxx"):
                logger.debug(
                    f"[TwitterMonitor] @{self.username} tweet {tweet_id} "
                    f"lang={tweet_lang} — skipping non-English"
                )
                continue

            # Text-based French detection — scoring approach.
            # Strip emojis first so they don't break word boundary checks.
            import unicodedata
            _fr_clean = "".join(
                c for c in text.lower()
                if unicodedata.category(c) not in ("So", "Sk", "Cf")
            )
            _FR_WORDS = re.compile(
                r"\b(?:les|des|est|pour|dans|cette|avec|nous|mais|sont"
                r"|le|la|une|du|au|ce|se|ne|pas|qui|que|sur|aussi"
                r"|tout|fait|comme|très|plus|mdr|mdrrr|ptdr|allez"
                r"|commence|furieux|victoire|équipe|incroyable"
                r"|magnifique|parcours|défaite|soirée|début"
                r"|rendez|retrouve)\b", re.I
            )
            _FR_PREFIXES = ("c'est", "l'open", "l'", "d'", "n'", "j'", "qu'")
            fr_score = len(_FR_WORDS.findall(_fr_clean))
            fr_score += sum(1 for p in _FR_PREFIXES if p in _fr_clean)
            if fr_score >= 2:
                logger.debug(
                    f"[TwitterMonitor] @{self.username} tweet {tweet_id} "
                    f"detected French text (score={fr_score}) — skipping"
                )
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

            # Only accept tweets authored by the account we're monitoring.
            # Embedded tweets from other accounts (e.g. @RocketBaguette
            # inside @RLEsports timeline) must be rejected.
            if screen_name.lower() != self.username.lower():
                logger.debug(
                    f"[TwitterMonitor] @{self.username} timeline contained "
                    f"tweet by @{screen_name} ({tweet_id}) — skipping"
                )
                continue

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

            # Strip "RT @username: " prefix from monitored tweets that are
            # text-form retweets — prevents the prefix leaking into our posts
            if clean_text.startswith("RT @"):
                rt_match = re.match(r"RT @\w+:\s*", clean_text)
                if rt_match:
                    clean_text = clean_text[rt_match.end():]

            # Relevance gate: retweet sources must be on-topic
            if self.is_retweet_source and not is_relevant(clean_text, self.niche):
                logger.debug(
                    f"[TwitterMonitor] @{self.username} tweet {tweet_id} "
                    f"failed relevance filter — skipping"
                )
                continue

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
    """Walk the GraphQL response tree to find tweet result objects.

    Skips sub-trees inside retweeted_status_result and quoted_status_result
    so that embedded tweets from *other* accounts aren't treated as
    standalone tweets from the timeline owner.  This prevents content from
    non-monitored accounts (e.g. @RocketBaguette inside an @RLEsports RT)
    from leaking into the pipeline.
    """
    _EMBEDDED_KEYS = {"retweeted_status_result", "quoted_status_result"}

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
            for k, v in obj.items():
                if k not in _EMBEDDED_KEYS:
                    stack.append(v)
        elif isinstance(obj, list):
            stack.extend(obj)
    return tweets
