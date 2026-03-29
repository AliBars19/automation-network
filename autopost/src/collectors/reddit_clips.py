"""
Reddit clip collector — monitors gaming subreddits for high-quality video posts.

Uses Reddit's OAuth API (oauth.reddit.com) via asyncpraw. This bypasses
the IP-based blocking that Reddit applies to unauthenticated .json endpoints
on datacenter IPs (like DigitalOcean).

Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env.
Register a "script" app at https://www.reddit.com/prefs/apps

Video pipeline:
  1. Fetch /r/{subreddit}/hot via asyncpraw (OAuth2 authenticated)
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

            # Download video — use permalink URL (yt-dlp handles v.redd.it natively)
            reddit_url = f"https://www.reddit.com{permalink}" if permalink else video_url
            media_path = await _download_reddit_video(reddit_url, post_id)

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


# ── Reddit fetch via cookies ─────────────────────────────────────────────────

_REDDIT_COOKIES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "reddit_cookies.txt"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


async def _fetch_hot_posts(subreddit: str, limit: int = 25) -> list[dict]:
    """Fetch hot posts from a subreddit using cookie-authenticated JSON.

    Reddit blocks unauthenticated requests from datacenter IPs.
    Browser cookies (exported via cookies.txt extension) bypass this.
    """
    if not _REDDIT_COOKIES_PATH.exists():
        logger.warning(
            "[RedditClips] No reddit_cookies.txt found — Reddit clips disabled. "
            "Export cookies from your browser to data/reddit_cookies.txt"
        )
        return []

    url = f"https://www.reddit.com/r/{subreddit}/hot.json"
    if not is_safe_url(url):
        return []

    # Load cookies from Netscape cookies.txt into a dict
    cookies = _load_cookies_txt(_REDDIT_COOKIES_PATH)

    try:
        async with httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": _BROWSER_UA},
            cookies=cookies,
        ) as client:
            resp = await client.get(url, params={"limit": limit, "raw_json": 1})
            resp.raise_for_status()
            data = resp.json()
            posts = data.get("data", {}).get("children", [])
            logger.info(f"[RedditClips] r/{subreddit} → {len(posts)} posts (cookie auth)")
            return posts
    except httpx.HTTPError as exc:
        logger.warning(f"[RedditClips] r/{subreddit} fetch failed: {exc}")
        return []


def _load_cookies_txt(path: Path) -> dict[str, str]:
    """Parse a Netscape cookies.txt file into a {name: value} dict."""
    cookies: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
    except Exception:
        pass
    return cookies


# ── Video download + merge ───────────────────────────────────────────────────

async def _download_reddit_video(video_url: str, post_id: str) -> str | None:
    """
    Download a Reddit video using yt-dlp (handles v.redd.it natively).
    Falls back to manual download+merge if yt-dlp is not available.
    Returns the path to the mp4, or None on failure.
    """
    import asyncio

    # Prefer yt-dlp — handles audio merge, cookies, and format selection
    try:
        from src.collectors.video_clipper import clip_reddit_video
        clip = await asyncio.to_thread(clip_reddit_video, video_url, post_id)
        if clip:
            return clip
    except Exception:
        pass

    # Fallback: manual download + ffmpeg merge
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
