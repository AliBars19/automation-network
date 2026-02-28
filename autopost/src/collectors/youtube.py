"""
YouTube Data API v3 collector — surfaces new videos from subscribed channels.
Requires YOUTUBE_API_KEY in .env (free quota: 10,000 units/day, well within limits).

Each channel is polled for its latest uploads. New video IDs are deduplicated
via insert_raw_content so each video is only ever tweeted once.
"""
import httpx
from loguru import logger

from config.settings import YOUTUBE_API_KEY
from src.collectors.base import BaseCollector, RawContent

_BASE_URL  = "https://www.googleapis.com/youtube/v3"
_TIMEOUT   = 15
_MAX_RESULTS = 5   # videos to check per poll (keeps quota usage low)


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

            # Choose content_type based on niche
            content_type = "youtube_video" if self.niche == "geometrydash" else "pro_player_content"

            results.append(RawContent(
                source_id    = self.source_id,
                external_id  = video_id,
                niche        = self.niche,
                content_type = content_type,
                title        = title,
                url          = video_url,
                body         = description,
                image_url    = thumbnail,
                author       = channel,
                score        = 0,
                metadata     = {
                    "creator": channel,
                    "title":   title,
                    "url":     video_url,
                },
            ))

        logger.info(f"[YouTube] {self.channel_id} → {len(results)} videos")
        return results
