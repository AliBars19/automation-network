"""
YouTube video clipper — downloads the first 30 seconds of YouTube videos
as native mp4 clips for Twitter upload.

Requires:
  - yt-dlp installed on the system (pip install yt-dlp)
  - cookies.txt file at data/cookies.txt (exported from browser)

Without cookies.txt, YouTube blocks downloads from datacenter IPs.
The cookies file needs refreshing every 3-6 months.
"""
import os
import subprocess
from pathlib import Path

from loguru import logger

from config.settings import MEDIA_DIR

_CLIP_DURATION = 30  # seconds to clip from the start
_MAX_HEIGHT = 720    # max video resolution (720p keeps file size reasonable)
_MAX_FILE_MB = 50    # abort if file exceeds this
_COOKIES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "cookies.txt"


def clip_youtube_video(video_url: str, video_id: str) -> str | None:
    """
    Download the first 30 seconds of a YouTube video as an mp4 clip.

    Returns the path to the clip file, or None on failure.
    Requires cookies.txt to exist — without it, YouTube blocks datacenter IPs.
    """
    if not _COOKIES_PATH.exists():
        logger.debug("[VideoClipper] No cookies.txt found — YouTube clips disabled")
        return None

    output_path = str(MEDIA_DIR / f"yt_clip_{video_id}.mp4")

    # Skip if already downloaded
    if os.path.exists(output_path):
        return output_path

    try:
        env = os.environ.copy()
        env["PATH"] = f"/root/.deno/bin:{env.get('PATH', '')}"

        cmd = [
            "yt-dlp",
            "--download-sections", f"*0-{_CLIP_DURATION}",
            "-f", f"bv*[height<={_MAX_HEIGHT}]+ba/b[height<={_MAX_HEIGHT}]",
            "--merge-output-format", "mp4",
            "--remux-video", "mp4",
            "--postprocessor-args", "ffmpeg:-c:v libx264 -c:a aac",
            "--remote-components", "ejs:github",
            "--no-playlist",
            "--no-warnings",
            "--quiet",
            "--cookies", str(_COOKIES_PATH),
            "-o", output_path,
            video_url,
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=120, text=True, env=env)

        if result.returncode != 0:
            stderr = result.stderr[:200] if result.stderr else "unknown error"
            logger.warning(f"[VideoClipper] yt-dlp failed for {video_id}: {stderr}")
            return None

        # Check file size
        if os.path.exists(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            if size_mb > _MAX_FILE_MB:
                logger.warning(f"[VideoClipper] clip too large ({size_mb:.1f}MB) — removing")
                os.remove(output_path)
                return None
            logger.info(f"[VideoClipper] clipped {video_id} ({size_mb:.1f}MB, {_CLIP_DURATION}s)")
            return output_path

        return None

    except subprocess.TimeoutExpired:
        logger.warning(f"[VideoClipper] yt-dlp timed out for {video_id}")
        return None
    except Exception as exc:
        logger.error(f"[VideoClipper] unexpected error for {video_id}: {exc}")
        return None


def clip_reddit_video(reddit_url: str, post_id: str) -> str | None:
    """
    Download a Reddit video clip using yt-dlp (handles v.redd.it automatically).

    yt-dlp natively supports Reddit video URLs and merges audio+video.
    Requires cookies.txt for Reddit if the IP is blocked.
    """
    output_path = str(MEDIA_DIR / f"reddit_{post_id}.mp4")

    if os.path.exists(output_path):
        return output_path

    try:
        cmd = [
            "yt-dlp",
            "-f", f"bv*[height<={_MAX_HEIGHT}]+ba/b[height<={_MAX_HEIGHT}]",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--no-warnings",
            "--quiet",
            "-o", output_path,
            reddit_url,
        ]

        # Add cookies if available (Reddit may also block datacenter IPs)
        reddit_cookies = Path(__file__).resolve().parent.parent.parent / "data" / "reddit_cookies.txt"
        if reddit_cookies.exists():
            cmd.extend(["--cookies", str(reddit_cookies)])

        result = subprocess.run(cmd, capture_output=True, timeout=60, text=True)

        if result.returncode != 0:
            return None

        if os.path.exists(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            if size_mb > _MAX_FILE_MB:
                os.remove(output_path)
                return None
            logger.info(f"[VideoClipper] downloaded reddit_{post_id} ({size_mb:.1f}MB)")
            return output_path

        return None

    except (subprocess.TimeoutExpired, Exception):
        return None


def cookies_available() -> bool:
    """Check if YouTube cookies.txt exists and is non-empty."""
    return _COOKIES_PATH.exists() and _COOKIES_PATH.stat().st_size > 100
