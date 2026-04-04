"""
Tweepy client wrapper — one instance per niche account.

In DRY_RUN mode (DRY_RUN=true in .env) no tweets are sent; everything is
logged instead. This lets you test the full pipeline without spending API
quota or posting prematurely.
"""
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

    def post_tweet(
        self,
        text: str,
        media_path: str | None = None,
        reply_to: str | None = None,
    ) -> str | None:
        """
        Post a tweet. Returns the tweet ID string on success, None on failure.
        If reply_to is set, the tweet is posted as a reply to that tweet ID.
        In dry-run mode logs the tweet and returns a fake ID.
        """
        if self.dry_run:
            logger.info(
                f"[{self.niche}] DRY RUN tweet:\n"
                f"{'─' * 40}\n{text}\n{'─' * 40}\n"
                f"media: {media_path or 'none'}"
                f"{f' reply_to: {reply_to}' if reply_to else ''}"
            )
            return "dry_run_id"

        media_ids = None
        if media_path:
            media_ids = self._upload_media(media_path)

        try:
            kwargs: dict = {"text": text}
            if media_ids:
                kwargs["media_ids"] = media_ids
            if reply_to:
                kwargs["in_reply_to_tweet_id"] = reply_to

            response = self._client.create_tweet(**kwargs)
            tweet_id = str(response.data["id"])
            logger.success(f"[{self.niche}] posted tweet {tweet_id}")
            return tweet_id

        except tweepy.TweepyException as exc:
            logger.error(f"[{self.niche}] failed to post tweet: {exc}")
            return None

    def quote_tweet(self, tweet_id: str, text: str) -> str | None:
        """
        Post a quote tweet (text + embedded original tweet).
        Returns the new tweet ID on success, None on failure.
        """
        if self.dry_run:
            logger.info(
                f"[{self.niche}] DRY RUN quote tweet of {tweet_id}:\n"
                f"{'─' * 40}\n{text}\n{'─' * 40}"
            )
            return "dry_run_qt_id"
        try:
            response = self._client.create_tweet(
                text=text,
                quote_tweet_id=tweet_id,
            )
            new_id = str(response.data["id"])
            logger.success(f"[{self.niche}] quote-tweeted {tweet_id} → {new_id}")
            return new_id
        except tweepy.TweepyException as exc:
            logger.error(f"[{self.niche}] failed to quote-tweet {tweet_id}: {exc}")
            return None

    # ── Media ──────────────────────────────────────────────────────────────────

    def _upload_media(self, media_path: str, retries: int = 2) -> list[str] | None:
        """Upload an image or video via v1.1 API and return a list with one media_id.
        Automatically detects video files (.mp4) and uses chunked upload.
        Retries on transient errors (429, 5xx) with exponential backoff."""
        import time

        path = Path(media_path)
        if not path.exists():
            logger.warning(f"[{self.niche}] media file not found: {media_path}")
            return None

        # Use chunked upload for video files
        is_video = path.suffix.lower() in (".mp4", ".mov", ".webm")
        media_category = "tweet_video" if is_video else "tweet_image"

        for attempt in range(retries + 1):
            try:
                if is_video:
                    media = self._api.chunked_upload(
                        filename=str(path),
                        media_category=media_category,
                        wait_for_async_finalize=True,
                    )
                else:
                    media = self._api.media_upload(
                        filename=str(path),
                        media_category=media_category,
                    )
                logger.debug(f"[{self.niche}] uploaded {'video' if is_video else 'image'} {media.media_id_string}")
                return [media.media_id_string]
            except tweepy.TweepyException as exc:
                exc_str = str(exc).lower()
                is_transient = any(k in exc_str for k in ("429", "500", "502", "503", "timeout"))
                if is_transient and attempt < retries:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"[{self.niche}] media upload attempt {attempt + 1} failed: {exc} — retrying in {wait}s")
                    time.sleep(wait)
                    continue
                logger.error(f"[{self.niche}] media upload failed: {exc} — posting without image")
                return None

