"""
YouTube Data API v3 collector — surfaces new videos from subscribed channels.
Requires YOUTUBE_API_KEY in .env (free quota: 10,000 units/day, well within limits).

Each channel is polled for its latest uploads. New video IDs are deduplicated
via insert_raw_content so each video is only ever tweeted once.
"""
import re

import httpx
from loguru import logger

from config.settings import YOUTUBE_API_KEY
from src.collectors.base import BaseCollector, RawContent

_BASE_URL  = "https://www.googleapis.com/youtube/v3"
_TIMEOUT   = 15
_MAX_RESULTS = 5   # videos to check per poll (keeps quota usage low)

# Patterns that indicate a YouTube Short or low-effort upload
_SHORTS_RE = re.compile(r"#shorts?\b", re.I)
_LOW_QUALITY_TITLE_LEN = 15  # titles shorter than this are likely Shorts captions

# Off-topic title signals for GD niche — merch drops, vlogs, unrelated games
_GD_OFF_TOPIC_RE = re.compile(
    r"\bmerch(?:andise)?\b"   # "new merch", "merch drop", "merch store"
    r"|\bnew\s+drop\b"        # product drop (not level drop)
    r"|\broom\s+tour\b"       # personal vlog
    r"|\bstudio\s+tour\b"     # personal vlog
    r"|\bvlog\b"              # explicit vlog tag
    r"|\birl\b"               # in-real-life event
    r"|\bminecraft\b"         # other game
    r"|\bfortnite\b"          # other game
    r"|\bvalorant\b",         # other game
    re.I,
)

# Titles that indicate an ongoing series episode — not newsworthy as standalone posts
_SERIES_RE = re.compile(
    r"\bday\s+\d+\s+of\b"    # "Day 14 of Racing @MizuRL"
    r"|\bepisode\s+\d+\b"     # "Episode 23"
    r"|\bep\.?\s*\d+\b"       # "Ep. 7" / "Ep 7"
    r"|#\d+\s*[:\|]",         # "#14 | doing stuff" / "#3:"
    re.I,
)


def _is_short_or_low_quality(title: str, description: str) -> bool:
    """Return True if the video looks like a YouTube Short or low-effort content."""
    combined = f"{title} {description}".lower()
    # Explicit Shorts tag
    if _SHORTS_RE.search(combined):
        return True
    # Very short titles are usually Shorts captions ("insane clip 😱")
    if len(title.strip()) < _LOW_QUALITY_TITLE_LEN:
        return True
    # Common Short-style patterns
    if title.strip().endswith("...") and len(title.strip()) < 30:
        return True
    return False


class YouTubeCollector(BaseCollector):
    """
    Fetches recent uploads from one YouTube channel.
    config keys (from YAML):
        channel_id    (str)  YouTube channel ID (UCxxxxxxx)
        poll_interval (int)  seconds between polls
    """

    def __init__(self, source_id: int, config: dict, niche: str):
        super().__init__(source_id, config)
        self.niche      = niche
        self.channel_id = config["channel_id"]
        self._uploads_playlist: str | None = None   # cached after first lookup

    async def collect(self) -> list[RawContent]:
        if not YOUTUBE_API_KEY:
            logger.warning("[YouTube] YOUTUBE_API_KEY not set — skipping")
            return []

        async with httpx.AsyncClient(base_url=_BASE_URL, timeout=_TIMEOUT) as client:
            playlist_id = await self._resolve_uploads_playlist(client)
            if not playlist_id:
                return []
            return await self._fetch_videos(client, playlist_id)

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _resolve_uploads_playlist(self, client: httpx.AsyncClient) -> str | None:
        """Get the uploads playlist ID for the channel (cached after first call)."""
        if self._uploads_playlist:
            return self._uploads_playlist
        try:
            resp = await client.get(
                "/channels",
                params={
                    "part":  "contentDetails",
                    "id":    self.channel_id,
                    "key":   YOUTUBE_API_KEY,
                },
            )
            if resp.status_code == 403:
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                reason = body.get("error", {}).get("errors", [{}])[0].get("reason", "")
                if reason == "quotaExceeded":
                    logger.error("[YouTube] API QUOTA EXHAUSTED — all YouTube sources will skip until quota resets at midnight PT")
                    return None
            resp.raise_for_status()
            items = resp.json().get("items", [])
            if not items:
                logger.warning(f"[YouTube] channel {self.channel_id} not found")
                return None
            playlist_id = (
                items[0]
                .get("contentDetails", {})
                .get("relatedPlaylists", {})
                .get("uploads", "")
            )
            self._uploads_playlist = playlist_id
            return playlist_id
        except Exception as exc:
            logger.error(f"[YouTube] playlist lookup failed for {self.channel_id}: {exc}")
            return None

    async def _fetch_videos(
        self, client: httpx.AsyncClient, playlist_id: str
    ) -> list[RawContent]:
        try:
            resp = await client.get(
                "/playlistItems",
                params={
                    "part":       "snippet",
                    "playlistId": playlist_id,
                    "maxResults": _MAX_RESULTS,
                    "key":        YOUTUBE_API_KEY,
                },
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except Exception as exc:
            logger.error(f"[YouTube] video fetch failed for {self.channel_id}: {exc}")
            return []

        results: list[RawContent] = []
        for item in items:
            snippet  = item.get("snippet", {})
            video_id = snippet.get("resourceId", {}).get("videoId", "")
            if not video_id:
                continue

            title       = snippet.get("title", "")
            description = snippet.get("description", "")[:300]
            channel     = snippet.get("channelTitle", "")
            thumbnail   = (
                snippet.get("thumbnails", {})
                .get("maxres", snippet.get("thumbnails", {}).get("high", {}))
                .get("url", "")
            )
            video_url = f"https://youtu.be/{video_id}"

            # Skip YouTube Shorts and low-effort uploads.
            # Shorts have vertical thumbnails (no maxres), short titles,
            # or "#Shorts" / "#Short" in title/description.
            if _is_short_or_low_quality(title, description):
                logger.debug(f"[YouTube] skipping Short/low-quality: {title[:60]}")
                continue

            # Skip ongoing series episodes — not newsworthy as standalone posts
            if _SERIES_RE.search(title):
                logger.debug(f"[YouTube] skipping series episode: {title[:60]}")
                continue

            # For GD niche: skip videos about merch, vlogs, or other games
            if self.niche == "geometrydash" and _GD_OFF_TOPIC_RE.search(title):
                logger.debug(f"[YouTube] skipping off-topic GD video: {title[:60]}")
                continue

            content_type = "youtube_video"

            # Try to download a 30s clip if cookies are available.
            # Falls back to thumbnail image if clip download fails.
            video_clip_path = ""
            try:
                from src.collectors.video_clipper import clip_youtube_video, cookies_available
                if cookies_available():
                    import asyncio
                    clip = await asyncio.to_thread(clip_youtube_video, video_url, video_id)
                    if clip:
                        video_clip_path = clip
            except Exception:
                pass  # clip is optional — fall back to thumbnail

            results.append(RawContent(
                source_id    = self.source_id,
                external_id  = video_id,
                niche        = self.niche,
                content_type = content_type,
                title        = title,
                url          = video_url,
                body         = description,
                image_url    = thumbnail if not video_clip_path else "",
                author       = channel,
                score        = 0,
                metadata     = {
                    "creator":     channel,
                    "video_title": title,
                    "title":       title,
                    "url":         video_url,
                    "media_path":  video_clip_path,
                },
            ))

        logger.info(f"[YouTube] {self.channel_id} → {len(results)} videos")
        return results
