"""
Tweepy client wrapper — one instance per niche account.

In DRY_RUN mode (DRY_RUN=true in .env) no tweets are sent; everything is
logged instead. This lets you test the full pipeline without spending API
quota or posting prematurely.
"""
import mimetypes
from pathlib import Path

import tweepy
from loguru import logger

from config.settings import DRY_RUN, NICHE_CREDENTIALS


class TwitterClient:
    """
    Thin wrapper around tweepy.Client (v2 API) + tweepy.API (v1.1 for media).
    One instance per niche — instantiate with niche='rocketleague' or 'geometrydash'.
    """

    def __init__(self, niche: str):
        self.niche   = niche
        self.dry_run = DRY_RUN
        creds        = NICHE_CREDENTIALS[niche]

        if self.dry_run:
            logger.info(f"[{niche}] DRY_RUN mode — no tweets will be sent")
            self._client = None
            self._api    = None
            return

        # v2 client — used for creating tweets
        self._client = tweepy.Client(
            consumer_key        = creds["api_key"],
            consumer_secret     = creds["api_secret"],
            access_token        = creds["access_token"],
            access_token_secret = creds["access_token_secret"],
        )

        # v1.1 API — used for media uploads (v2 doesn't support media upload yet)
        auth = tweepy.OAuth1UserHandler(
            creds["api_key"],
            creds["api_secret"],
            creds["access_token"],
            creds["access_token_secret"],
        )
        self._api = tweepy.API(auth)

    # ── Posting ────────────────────────────────────────────────────────────────

    def post_tweet(self, text: str, media_path: str | None = None) -> str | None:
        """
        Post a tweet. Returns the tweet ID string on success, None on failure.
        In dry-run mode logs the tweet and returns a fake ID.
        """
        if self.dry_run:
            logger.info(
                f"[{self.niche}] DRY RUN tweet:\n"
                f"{'─' * 40}\n{text}\n{'─' * 40}\n"
                f"media: {media_path or 'none'}"
            )
            return "dry_run_id"

        media_ids = None
        if media_path:
            media_ids = self._upload_media(media_path)

        try:
            kwargs: dict = {"text": text}
            if media_ids:
                kwargs["media_ids"] = media_ids

            response = self._client.create_tweet(**kwargs)
            tweet_id = str(response.data["id"])
            logger.success(f"[{self.niche}] posted tweet {tweet_id}")
            return tweet_id

        except tweepy.TweepyException as exc:
            logger.error(f"[{self.niche}] failed to post tweet: {exc}")
            return None

    def retweet(self, tweet_id: str) -> bool:
        """Retweet by ID. Returns True on success."""
        if self.dry_run:
            logger.info(f"[{self.niche}] DRY RUN retweet: {tweet_id}")
            return True
        try:
            me = self._client.get_me()
            self._client.retweet(tweet_id=tweet_id, user_auth=True)
            logger.success(f"[{self.niche}] retweeted {tweet_id}")
            return True
        except tweepy.TweepyException as exc:
            logger.error(f"[{self.niche}] failed to retweet {tweet_id}: {exc}")
            return False

    # ── Media ──────────────────────────────────────────────────────────────────

    def _upload_media(self, media_path: str) -> list[str] | None:
        """Upload an image via v1.1 API and return a list with one media_id."""
        path = Path(media_path)
        if not path.exists():
            logger.warning(f"[{self.niche}] media file not found: {media_path}")
            return None

        mime, _ = mimetypes.guess_type(str(path))
        try:
            media = self._api.media_upload(
                filename=str(path),
                media_category="tweet_image",
            )
            logger.debug(f"[{self.niche}] uploaded media {media.media_id_string}")
            return [media.media_id_string]
        except tweepy.TweepyException as exc:
            logger.error(f"[{self.niche}] media upload failed: {exc} — posting without image")
            return None

    # ── Rate limit info ────────────────────────────────────────────────────────

    def get_rate_limit_status(self) -> dict:
        """Return remaining requests for statuses/update endpoint (v1.1)."""
        if self.dry_run or self._api is None:
            return {}
        try:
            return self._api.rate_limit_status(resources="statuses")
        except tweepy.TweepyException:
            return {}
