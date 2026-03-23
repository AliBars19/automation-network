"""
Unit tests for src/formatter/media.py — image dimension checks,
hash-based filename generation, resize logic, prepare_media, cleanup_old_media.
"""
import hashlib
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from src.formatter.media import (
    MIN_SOURCE_H,
    MIN_SOURCE_W,
    TARGET_H,
    TARGET_W,
    _dest_path,
    _download,
    _resize,
    cleanup_old_media,
    prepare_media,
)


# ── _dest_path() ──────────────────────────────────────────────────────────────

class TestDestPath:
    def test_deterministic_hash(self):
        """Same URL always produces the same file path."""
        p1 = _dest_path("https://example.com/image.png")
        p2 = _dest_path("https://example.com/image.png")
        assert p1 == p2

    def test_different_urls_different_paths(self):
        p1 = _dest_path("https://example.com/a.png")
        p2 = _dest_path("https://example.com/b.png")
        assert p1 != p2

    def test_returns_jpg_extension(self):
        p = _dest_path("https://example.com/image.png")
        assert p.suffix == ".jpg"

    def test_hash_matches_sha256(self):
        url = "https://example.com/test.png"
        expected_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        assert _dest_path(url).stem == expected_hash


# ── _resize() — dimension check ──────────────────────────────────────────────

class TestResizeDimensionCheck:
    @staticmethod
    def _make_image(width: int, height: int) -> bytes:
        """Create a minimal JPEG image of the given dimensions."""
        img = Image.new("RGB", (width, height), color=(128, 128, 128))
        buf = BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()

    def test_large_image_accepted(self):
        """Image larger than minimum in both dimensions should be resized."""
        raw = self._make_image(1920, 1080)
        result = _resize(raw)
        assert result is not None

    def test_tiny_image_rejected(self):
        """Image smaller than minimum in BOTH dimensions should be rejected."""
        raw = self._make_image(100, 100)
        result = _resize(raw)
        assert result is None

    def test_narrow_image_rejected(self):
        """Regression: image with only width too small should be rejected (OR logic)."""
        raw = self._make_image(200, 1000)  # width < 400, height OK
        result = _resize(raw)
        assert result is None

    def test_short_image_rejected(self):
        """Image with only height too small should be rejected (OR logic)."""
        raw = self._make_image(1000, 100)  # width OK, height < 300
        result = _resize(raw)
        assert result is None

    def test_exactly_minimum_accepted(self):
        """Image at exact minimum dimensions should be accepted."""
        raw = self._make_image(MIN_SOURCE_W, MIN_SOURCE_H)
        result = _resize(raw)
        assert result is not None

    def test_one_below_minimum_rejected(self):
        """Image one pixel below minimum width should be rejected."""
        raw = self._make_image(MIN_SOURCE_W - 1, MIN_SOURCE_H)
        result = _resize(raw)
        assert result is None


# ── _resize() — output format ─────────────────────────────────────────────────

class TestResizeOutput:
    @staticmethod
    def _make_image(width: int, height: int) -> bytes:
        img = Image.new("RGB", (width, height), color=(128, 128, 128))
        buf = BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()

    def test_output_is_jpeg(self):
        raw = self._make_image(1920, 1080)
        result = _resize(raw)
        assert result is not None
        # JPEG magic bytes: FF D8
        assert result[:2] == b"\xff\xd8"

    def test_large_image_resized_to_target(self):
        """Image larger than target should be scaled to exact target dimensions."""
        raw = self._make_image(2400, 1350)
        result = _resize(raw)
        assert result is not None
        img = Image.open(BytesIO(result))
        assert img.width == TARGET_W
        assert img.height == TARGET_H

    def test_medium_image_not_upscaled(self):
        """Image between min and target should not be upscaled."""
        raw = self._make_image(800, 600)
        result = _resize(raw)
        assert result is not None
        img = Image.open(BytesIO(result))
        # Should not exceed original dimensions
        assert img.width <= 800
        assert img.height <= 600

    def test_corrupt_data_returns_none(self):
        result = _resize(b"not an image at all")
        assert result is None


# ── prepare_media() ───────────────────────────────────────────────────────────

class TestPrepareMedia:

    @staticmethod
    def _make_jpeg_bytes(w: int = 1200, h: int = 675) -> bytes:
        img = Image.new("RGB", (w, h), color=(100, 100, 200))
        buf = BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()

    def test_returns_none_for_empty_url(self):
        assert prepare_media("") is None

    def test_returns_cached_path_when_file_exists(self, tmp_path):
        url = "https://example.com/existing.jpg"
        dest = _dest_path(url)
        # Pre-create the file so it appears cached
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake cached image")
        with patch("src.formatter.media.MEDIA_DIR", dest.parent):
            with patch("src.formatter.media._dest_path", return_value=dest):
                result = prepare_media(url)
        assert result == str(dest)

    def test_returns_none_when_download_fails(self, tmp_path):
        url = "https://example.com/no-image.jpg"
        with (
            patch("src.formatter.media.MEDIA_DIR", tmp_path),
            patch("src.formatter.media._dest_path", return_value=tmp_path / "x.jpg"),
            patch("src.formatter.media._download", return_value=None),
        ):
            result = prepare_media(url)
        assert result is None

    def test_returns_none_when_resize_fails(self, tmp_path):
        url = "https://example.com/bad-image.jpg"
        with (
            patch("src.formatter.media.MEDIA_DIR", tmp_path),
            patch("src.formatter.media._dest_path", return_value=tmp_path / "x.jpg"),
            patch("src.formatter.media._download", return_value=b"raw bytes"),
            patch("src.formatter.media._resize", return_value=None),
        ):
            result = prepare_media(url)
        assert result is None

    def test_saves_and_returns_path_on_success(self, tmp_path):
        url = "https://example.com/good.jpg"
        jpeg_bytes = self._make_jpeg_bytes()
        dest = tmp_path / "abc123.jpg"
        with (
            patch("src.formatter.media.MEDIA_DIR", tmp_path),
            patch("src.formatter.media._dest_path", return_value=dest),
            patch("src.formatter.media._download", return_value=b"raw"),
            patch("src.formatter.media._resize", return_value=jpeg_bytes),
        ):
            result = prepare_media(url)
        assert result == str(dest)
        assert dest.exists()


# ── _download() ───────────────────────────────────────────────────────────────

class TestDownload:

    def test_returns_none_on_http_error(self):
        import httpx
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=None)
            mock_response = MagicMock()
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "404", request=MagicMock(), response=MagicMock()
            )
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = _download("https://example.com/missing.jpg")

        assert result is None

    def test_returns_none_when_response_too_large(self):
        from src.formatter.media import MAX_BYTES
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=None)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.content = b"x" * (MAX_BYTES + 1)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = _download("https://example.com/huge.jpg")

        assert result is None

    def test_returns_bytes_on_success(self):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=None)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.content = b"image data"
            mock_response.is_redirect = False
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = _download("https://example.com/img.jpg")

        assert result == b"image data"


# ── cleanup_old_media() ───────────────────────────────────────────────────────

class TestCleanupOldMedia:

    def test_deletes_oldest_files_when_over_limit(self, tmp_path):
        """When more than max_files exist, oldest should be deleted."""
        import time
        # Create 5 files with slightly different mtimes
        files = []
        for i in range(5):
            f = tmp_path / f"file_{i:03d}.jpg"
            f.write_bytes(b"data")
            files.append(f)

        with patch("src.formatter.media.MEDIA_DIR", tmp_path):
            deleted = cleanup_old_media(max_files=3)

        assert deleted == 2
        remaining = list(tmp_path.glob("*.jpg"))
        assert len(remaining) == 3

    def test_no_deletion_when_under_limit(self, tmp_path):
        for i in range(3):
            (tmp_path / f"img_{i}.jpg").write_bytes(b"x")

        with patch("src.formatter.media.MEDIA_DIR", tmp_path):
            deleted = cleanup_old_media(max_files=10)

        assert deleted == 0

    def test_returns_zero_when_no_files(self, tmp_path):
        with patch("src.formatter.media.MEDIA_DIR", tmp_path):
            deleted = cleanup_old_media(max_files=500)
        assert deleted == 0
