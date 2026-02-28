"""
Media handling — download images from URLs and resize to 1200×675 (16:9)
for optimal X/Twitter display. Returns the local file path for use in
add_to_queue(media_path=...) and TwitterClient.post_tweet(media_path=...).

Files are stored in data/media/ and named by a hash of the source URL
so the same image is never downloaded twice.
"""
import hashlib
import mimetypes
from pathlib import Path

import httpx
from loguru import logger
from PIL import Image

from config.settings import MEDIA_DIR

# Target dimensions for Twitter card image (16:9)
TARGET_W = 1200
TARGET_H = 675

# Max file size Twitter accepts for images (5 MB)
MAX_BYTES = 5 * 1024 * 1024

# Supported formats we'll try to convert to JPEG
_SUPPORTED = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def prepare_media(image_url: str) -> str | None:
    """
    Download `image_url`, resize to 1200×675, save to data/media/.
    Returns the absolute local file path, or None on failure.
    Uses a content-hash filename so the same URL is never re-downloaded.
    """
    if not image_url:
        return None

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    dest = _dest_path(image_url)
    if dest.exists():
        logger.debug(f"[Media] cache hit: {dest.name}")
        return str(dest)

    raw = _download(image_url)
    if raw is None:
        return None

    resized = _resize(raw, image_url)
    if resized is None:
        return None

    try:
        dest.write_bytes(resized)
        logger.info(f"[Media] saved {dest.name} ({len(resized) / 1024:.0f} KB)")
        return str(dest)
    except OSError as exc:
        logger.error(f"[Media] could not write {dest}: {exc}")
        return None


def cleanup_old_media(max_files: int = 500) -> int:
    """
    Delete oldest media files when the folder grows beyond max_files.
    Returns the number of files deleted.
    """
    files = sorted(MEDIA_DIR.glob("*.jpg"), key=lambda f: f.stat().st_mtime)
    to_delete = files[: max(0, len(files) - max_files)]
    for f in to_delete:
        f.unlink(missing_ok=True)
    if to_delete:
        logger.info(f"[Media] cleaned up {len(to_delete)} old files")
    return len(to_delete)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _dest_path(url: str) -> Path:
    """Deterministic filename from URL hash."""
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    return MEDIA_DIR / f"{url_hash}.jpg"


def _download(url: str) -> bytes | None:
    """Download raw bytes from URL. Returns None on failure."""
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            resp = client.get(
                url,
                headers={"User-Agent": "AutoPost/1.0 (image downloader)"},
            )
            resp.raise_for_status()

        if len(resp.content) > MAX_BYTES:
            logger.warning(f"[Media] {url} too large ({len(resp.content)} bytes), skipping")
            return None

        return resp.content

    except httpx.HTTPError as exc:
        logger.warning(f"[Media] download failed for {url}: {exc}")
        return None


def _resize(raw: bytes, source_url: str = "") -> bytes | None:
    """
    Open raw image bytes, resize/crop to 1200×675, return JPEG bytes.
    Strategy: scale to fit 1200×675, then centre-crop any excess.
    """
    try:
        from io import BytesIO

        img = Image.open(BytesIO(raw)).convert("RGB")

        # Scale so both dimensions are at least TARGET size (cover mode)
        scale = max(TARGET_W / img.width, TARGET_H / img.height)
        new_w = int(img.width * scale)
        new_h = int(img.height * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

        # Centre crop to exact target
        left = (new_w - TARGET_W) // 2
        top  = (new_h - TARGET_H) // 2
        img  = img.crop((left, top, left + TARGET_W, top + TARGET_H))

        out = BytesIO()
        img.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue()

    except Exception as exc:
        logger.warning(f"[Media] resize failed (src={source_url}): {exc}")
        return None
