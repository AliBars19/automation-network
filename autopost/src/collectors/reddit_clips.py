"""
Reddit clip collector — monitors gaming subreddits for high-quality video posts.

Uses Reddit's public JSON endpoints (no OAuth needed, no API key required).
Only surfaces video posts that pass engagement thresholds (upvotes).

Video pipeline:
  1. Fetch /r/{subreddit}/hot.json for video posts
  2. Filter by score threshold (500+ upvotes for RL, 400+ for GD)
  3. Download video from v.redd.it (separate audio + video streams)
  4. Merge with ffmpeg into a single mp4
  5. Return as RawContent with media_path set for native Twitter upload

The quality gate in queue.py provides additional filtering on top of this.
"""
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from loguru import logger

from config.settings import MEDIA_DIR
from src.collectors.base import BaseCollector, RawContent
from src.collectors.url_utils import is_safe_url

_HEADERS = {
    "User-Agent": "AutoPost/1.0 (gaming news bot; +https://github.com/AliBars19/automation-network)",
}

# Upvote thresholds per niche — only posts above this get through
_SCORE_THRESHOLDS: dict[str, int] = {
    "rocketleague": 500,
    "geometrydash": 400,
}

# Max age of a Reddit post to be considered (hours)
_MAX_AGE_HOURS = 12

# Max video length in seconds (Twitter limit is 140s, we cap at 60s for quality)
_MAX_VIDEO_SECONDS = 60

# v.redd.it audio URL pattern
_VREDDIT_AUDIO_RE = re.compile(r"(https://v\.redd\.it/[\w\-]+)/")


class RedditClipCollector(BaseCollector):
    """
    Monitors one subreddit for high-engagement video posts.

    config keys (from YAML):
        subreddit     (str)   Subreddit name without r/ e.g. "RocketLeague"
        min_score     (int)   Override default upvote threshold
        poll_interval (int)   Seconds between polls
    """

    def __init__(self, source_id: int, config: dict, niche: str):
        super().__init__(source_id, config)
        self.niche = niche
        self.subreddit = config["subreddit"]
        self.min_score = config.get("min_score", _SCORE_THRESHOLDS.get(niche, 500))

    async def collect(self) -> list[RawContent]:
        posts = await _fetch_hot_posts(self.subreddit)
        if not posts:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=_MAX_AGE_HOURS)
        items: list[RawContent] = []

        for post in posts:
            data = post.get("data", {})

            # Must be a video post
            if not data.get("is_video"):
                continue

            # Score check
            score = data.get("score", 0)
            if score < self.min_score:
                continue

            # Age check
            created_utc = data.get("created_utc", 0)
            created_dt = datetime.fromtimestamp(created_utc, tz=timezone.utc)
            if created_dt < cutoff:
                continue

            # Must have a Reddit video URL
            media = data.get("media", {}) or {}
            reddit_video = media.get("reddit_video", {}) or {}
            video_url = reddit_video.get("fallback_url", "")
            if not video_url:
                continue

            # Check video duration
            duration = reddit_video.get("duration", 0)
            if duration > _MAX_VIDEO_SECONDS:
                continue

            title = data.get("title", "").strip()
            author = data.get("author", "unknown")
            permalink = data.get("permalink", "")
            post_id = data.get("id", "")
            thumbnail = data.get("thumbnail", "")

            # Download and merge video + audio
            media_path = await _download_reddit_video(video_url, post_id)

            age_hours = (datetime.now(timezone.utc) - created_dt).total_seconds() / 3600

            items.append(RawContent(
                source_id    = self.source_id,
                external_id  = post_id,
                niche        = self.niche,
                content_type = "reddit_clip",
                title        = title,
                url          = f"https://reddit.com{permalink}" if permalink else "",
                body         = title,
                image_url    = "",  # video, not image
                author       = author,
                score        = score,
                metadata     = {
                    "author":        author,
                    "score":         str(score),
                    "media_path":    media_path or "",
                    "video_url":     video_url,
                    "created_at":    created_dt.isoformat(),
                    "age_hours":     str(round(age_hours, 1)),
                },
            ))

        logger.info(
            f"[RedditClips] r/{self.subreddit} → {len(items)} clips "
            f"(threshold: {self.min_score}+ upvotes)"
        )
        return items


# ── Reddit JSON fetch ────────────────────────────────────────────────────────

async def _fetch_hot_posts(subreddit: str, limit: int = 25) -> list[dict]:
    """Fetch hot posts from a subreddit.

    Tries the JSON endpoint first, falls back to RSS + per-post JSON
    if the server blocks JSON (DigitalOcean IPs get 403 on .json but
    RSS still works).
    """
    # Try JSON first (has all the data we need in one call)
    posts = await _fetch_json(subreddit, limit)
    if posts:
        return posts

    # Fallback: RSS gives us post IDs, then we fetch each post's JSON
    return await _fetch_via_rss(subreddit, limit)


async def _fetch_json(subreddit: str, limit: int) -> list[dict]:
    """Primary: Reddit JSON endpoint."""
    url = f"https://www.reddit.com/r/{subreddit}/hot.json"
    if not is_safe_url(url):
        return []
    try:
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
            resp = await client.get(url, params={"limit": limit, "raw_json": 1})
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("children", [])
    except httpx.HTTPError as exc:
        logger.debug(f"[RedditClips] r/{subreddit} JSON blocked ({exc}), trying RSS")
        return []


async def _fetch_via_rss(subreddit: str, limit: int) -> list[dict]:
    """Fallback: RSS feed for post URLs, then individual .json per post.

    RSS bypasses Reddit's IP-based JSON blocking on DigitalOcean.
    """
    rss_url = f"https://www.reddit.com/r/{subreddit}/.rss"
    if not is_safe_url(rss_url):
        return []
    try:
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
            resp = await client.get(rss_url)
            resp.raise_for_status()
            rss_text = resp.text
    except httpx.HTTPError as exc:
        logger.warning(f"[RedditClips] r/{subreddit} RSS failed: {exc}")
        return []

    # Parse RSS to extract post URLs
    import re as _re
    links = _re.findall(
        rf"https://www\.reddit\.com/r/{_re.escape(subreddit)}/comments/(\w+)/",
        rss_text,
    )
    if not links:
        return []

    # Fetch each post's JSON (limit to avoid hammering)
    posts: list[dict] = []
    async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
        for post_id in links[:limit]:
            try:
                url = f"https://www.reddit.com/comments/{post_id}.json"
                resp = await client.get(url, params={"raw_json": 1})
                if resp.status_code == 200:
                    data = resp.json()
                    # Reddit returns [listing, comments] — first listing has the post
                    if isinstance(data, list) and len(data) > 0:
                        children = data[0].get("data", {}).get("children", [])
                        if children:
                            posts.append(children[0])
            except Exception:
                continue

    logger.info(f"[RedditClips] r/{subreddit} RSS fallback → {len(posts)} posts")
    return posts


# ── Video download + merge ───────────────────────────────────────────────────

async def _download_reddit_video(video_url: str, post_id: str) -> str | None:
    """
    Download a v.redd.it video and merge with its audio track using ffmpeg.
    Returns the path to the merged mp4, or None on failure.
    """
    import asyncio

    try:
        return await asyncio.to_thread(_download_and_merge, video_url, post_id)
    except Exception as exc:
        logger.error(f"[RedditClips] video download failed: {exc}")
        return None


def _download_and_merge(video_url: str, post_id: str) -> str | None:
    """Synchronous video download + ffmpeg merge. Runs in a thread."""
    if not is_safe_url(video_url):
        return None

    # Sanitize post_id to prevent path traversal (defence-in-depth)
    post_id = re.sub(r"[^a-z0-9]", "", post_id.lower())

    # Derive the audio URL from the video URL
    # v.redd.it format: https://v.redd.it/{id}/DASH_720.mp4
    # Audio is at: https://v.redd.it/{id}/DASH_audio.mp4
    match = _VREDDIT_AUDIO_RE.search(video_url)
    if not match:
        # No audio — download video only
        return _download_single(video_url, post_id)

    base_url = match.group(1)
    audio_url = f"{base_url}/DASH_audio.mp4"

    output_path = str(MEDIA_DIR / f"reddit_{post_id}.mp4")

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "video.mp4")
        audio_path = os.path.join(tmpdir, "audio.mp4")

        # Download video
        if not _download_file(video_url, video_path):
            return None

        # Try to download audio (may not exist for GIFs)
        has_audio = _download_file(audio_url, audio_path)

        if has_audio:
            # Merge with ffmpeg
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", video_path,
                    "-i", audio_path,
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-shortest",
                    output_path,
                ],
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.warning(f"[RedditClips] ffmpeg merge failed: {result.stderr[:200]}")
                # Fall back to video without audio
                return _copy_file(video_path, output_path)
        else:
            # No audio track — just copy the video
            return _copy_file(video_path, output_path)

    if os.path.exists(output_path):
        size_kb = os.path.getsize(output_path) // 1024
        logger.info(f"[RedditClips] saved reddit_{post_id}.mp4 ({size_kb} KB)")
        return output_path
    return None


def _download_single(url: str, post_id: str) -> str | None:
    """Download a single file (no audio merge needed)."""
    output_path = str(MEDIA_DIR / f"reddit_{post_id}.mp4")
    if _download_file(url, output_path):
        return output_path
    return None


_MAX_VIDEO_BYTES = 50 * 1024 * 1024  # 50 MB hard cap per file


def _download_file(url: str, dest: str) -> bool:
    """Stream a URL to a local file with a hard size cap. Returns True on success."""
    if not is_safe_url(url):
        return False
    try:
        with httpx.Client(timeout=20, headers=_HEADERS) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return False
                size = 0
                with open(dest, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        size += len(chunk)
                        if size > _MAX_VIDEO_BYTES:
                            logger.warning(
                                f"[RedditClips] download aborted: exceeded {_MAX_VIDEO_BYTES // (1024*1024)} MB cap"
                            )
                            return False
                        f.write(chunk)
                return size > 0
    except Exception as exc:
        logger.debug(f"[RedditClips] download failed {url[:60]}: {exc}")
        return False


def _copy_file(src: str, dest: str) -> str | None:
    """Copy a file from src to dest."""
    import shutil
    try:
        shutil.copy2(src, dest)
        return dest
    except Exception:
        return None
