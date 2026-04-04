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
import threading
from pathlib import Path

from loguru import logger

from config.settings import MEDIA_DIR

_CLIP_DURATION = 30  # seconds to clip from the start
_MAX_HEIGHT = 720    # max video resolution (720p keeps file size reasonable)
_MAX_FILE_MB = 50    # abort if file exceeds this
_COOKIES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "cookies.txt"

# Serialize ffmpeg re-encodes — concurrent encodes saturate the droplet CPU
# and cause 180s timeout failures.  One encode at a time is fast enough
# since clips with wrong codecs will be rare after the format selector fix.
_ENCODE_SEM = threading.Semaphore(1)


def clip_youtube_video(video_url: str, video_id: str) -> str | None:
    """
    Download the first 30 seconds of a YouTube video as an mp4 clip.

    Returns the path to the clip file, or None on failure.
    Requires cookies.txt to exist — without it, YouTube blocks datacenter IPs.

    The output is always H.264/AAC — Twitter's only accepted video codec.
    If yt-dlp downloads AV1 or VP9 (common on YouTube), ffmpeg re-encodes it.
    """
    if not _COOKIES_PATH.exists():
        logger.debug("[VideoClipper] No cookies.txt found — YouTube clips disabled")
        return None

    # Sanitize video_id to prevent path traversal (defence-in-depth)
    import re
    video_id = re.sub(r"[^a-zA-Z0-9_\-]", "", video_id)
    if not video_id:
        return None

    output_path = str(MEDIA_DIR / f"yt_clip_{video_id}.mp4")
    skip_path   = str(MEDIA_DIR / f"yt_clip_{video_id}.skip")

    # Skip live-stream videos permanently (marked on first failed attempt)
    if os.path.exists(skip_path):
        logger.debug(f"[VideoClipper] {video_id} skipped (live-stream sentinel)")
        return None

    # Skip if already downloaded
    if os.path.exists(output_path):
        return output_path

    try:
        env = os.environ.copy()
        env["PATH"] = f"/root/.deno/bin:{env.get('PATH', '')}"

        # Prefer H.264 streams to avoid re-encoding overhead; fall back to any.
        cmd = [
            "yt-dlp",
            "--download-sections", f"*0-{_CLIP_DURATION}",
            "-f", (
                f"bv[vcodec^=avc][height<={_MAX_HEIGHT}]+ba[acodec=aac]"
                f"/bv[vcodec^=avc][height<={_MAX_HEIGHT}]+ba"
                f"/bv*[height<={_MAX_HEIGHT}]+ba"
                f"/b[height<={_MAX_HEIGHT}]"
            ),
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--no-warnings",
            "--quiet",
            "--cookies", str(_COOKIES_PATH),
            "-o", output_path,
            video_url,
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=120, text=True, env=env)

        if result.returncode != 0:
            stderr = result.stderr[:300] if result.stderr else "unknown error"
            logger.warning(f"[VideoClipper] yt-dlp failed for {video_id}: {stderr}")
            # Mark live-stream videos permanently so we don't retry every poll
            if "live event" in stderr.lower() or "live stream" in stderr.lower():
                Path(skip_path).touch()
                logger.info(f"[VideoClipper] {video_id} is a live stream — marked as skip")
            return None

        if not os.path.exists(output_path):
            return None

        # Validate video codec — Twitter requires H.264.
        # Re-encode if yt-dlp downloaded AV1, VP9, or anything else.
        _ensure_h264(output_path, video_id)

        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        if size_mb > _MAX_FILE_MB:
            logger.warning(f"[VideoClipper] clip too large ({size_mb:.1f}MB) — removing")
            os.remove(output_path)
            return None

        logger.info(f"[VideoClipper] clipped {video_id} ({size_mb:.1f}MB, {_CLIP_DURATION}s)")
        return output_path

    except subprocess.TimeoutExpired:
        logger.warning(f"[VideoClipper] yt-dlp timed out for {video_id}")
        return None
    except Exception as exc:
        logger.error(f"[VideoClipper] unexpected error for {video_id}: {exc}")
        return None


def _ensure_h264(mp4_path: str, video_id: str) -> None:
    """Re-encode mp4_path in-place to H.264+AAC when needed.

    Twitter requires H.264 video AND AAC audio.
    AV1/VP9 video and Opus audio (common on YouTube) both cause
    '400 Your media IDs are invalid — Incompatible audio/video' at tweet time.
    """
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "stream=codec_name",
                "-of", "csv=p=0",
                mp4_path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        codecs = [c.strip() for c in probe.stdout.strip().splitlines() if c.strip()]
        video_codec = codecs[0] if codecs else ""
        audio_codec = codecs[1] if len(codecs) > 1 else ""

        needs_reencode = (
            (video_codec and video_codec != "h264")
            or (audio_codec and audio_codec != "aac")
        )
        if not needs_reencode:
            return

        logger.info(
            f"[VideoClipper] re-encoding {video_id}: "
            f"video={video_codec or '?'} audio={audio_codec or '?'} → H.264+AAC"
        )
        tmp_path = mp4_path + ".tmp.mp4"
        with _ENCODE_SEM:  # serialize encodes — concurrent ffmpegs saturate the CPU
            encode = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", mp4_path,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k",
                    tmp_path,
                ],
                capture_output=True, timeout=300,  # 5 min — allows for queue wait
            )
        if encode.returncode == 0:
            os.replace(tmp_path, mp4_path)
        else:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            logger.warning(f"[VideoClipper] re-encode failed for {video_id}")
    except Exception as exc:
        logger.warning(f"[VideoClipper] codec-check failed for {video_id}: {exc}")


def clip_reddit_video(reddit_url: str, post_id: str) -> str | None:
    """
    Download a Reddit video clip using yt-dlp (handles v.redd.it automatically).

    yt-dlp natively supports Reddit video URLs and merges audio+video.
    Requires cookies.txt for Reddit if the IP is blocked.
    """
    # Sanitize post_id to prevent path traversal
    import re
    post_id = re.sub(r"[^a-zA-Z0-9_\-]", "", post_id)
    if not post_id:
        return None

    output_path = str(MEDIA_DIR / f"reddit_{post_id}.mp4")

    if os.path.exists(output_path):
        return output_path

    # SSRF guard: validate the URL before passing to yt-dlp subprocess
    from src.collectors.url_utils import is_safe_url
    if not is_safe_url(reddit_url):
        logger.warning(f"[VideoClipper] blocked unsafe reddit URL: {reddit_url[:80]}")
        return None

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
