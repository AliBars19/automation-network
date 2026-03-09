"""
Unit tests for src/formatter/media.py — image dimension checks,
hash-based filename generation, and resize logic.
"""
import hashlib
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from src.formatter.media import (
    MIN_SOURCE_H,
    MIN_SOURCE_W,
    TARGET_H,
    TARGET_W,
    _dest_path,
    _resize,
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
