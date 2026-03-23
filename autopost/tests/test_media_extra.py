"""
Additional tests for src/formatter/media.py covering missing lines:
  - Lines 61-63: prepare_media() OSError when writing file
  - Lines 96, 99, 102-103: _is_safe_url() edge cases (non-http scheme, known
    blocked hosts, private/loopback IP addresses)
  - Lines 107-108: _is_safe_url() exception path
  - Lines 114-115: _download() blocked by _is_safe_url
"""
import ipaddress
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from src.formatter.media import (
    _is_safe_url,
    _download,
    prepare_media,
    cleanup_old_media,
    _dest_path,
)


# ── _is_safe_url() edge cases ─────────────────────────────────────────────────

class TestIsSafeUrlEdgeCases:

    def test_http_public_domain_is_safe(self):
        assert _is_safe_url("https://example.com/image.jpg") is True

    def test_http_scheme_is_safe(self):
        assert _is_safe_url("http://example.com/image.jpg") is True

    def test_ftp_scheme_is_not_safe(self):
        # Line 96: scheme not in ("http", "https") → return False
        assert _is_safe_url("ftp://example.com/file.jpg") is False

    def test_file_scheme_is_not_safe(self):
        assert _is_safe_url("file:///etc/passwd") is False

    def test_data_url_is_not_safe(self):
        assert _is_safe_url("data:image/png;base64,abc") is False

    def test_aws_metadata_ip_is_blocked(self):
        # Line 99: host == "169.254.169.254" → return False
        assert _is_safe_url("http://169.254.169.254/latest/meta-data/") is False

    def test_gcp_metadata_host_is_blocked(self):
        # Line 99: host == "metadata.google.internal" → return False
        assert _is_safe_url("http://metadata.google.internal/computeMetadata/v1/") is False

    def test_private_ip_192_168_is_blocked(self):
        # Line 102-103: addr.is_private → return False
        assert _is_safe_url("http://192.168.1.1/image.jpg") is False

    def test_private_ip_10_x_is_blocked(self):
        assert _is_safe_url("http://10.0.0.1/image.jpg") is False

    def test_private_ip_172_16_is_blocked(self):
        assert _is_safe_url("http://172.16.0.1/image.jpg") is False

    def test_loopback_ip_is_blocked(self):
        # Line 102-103: addr.is_loopback → return False
        assert _is_safe_url("http://127.0.0.1/image.jpg") is False

    def test_loopback_127_0_0_1_is_blocked(self):
        # 127.0.0.1 parses as IP and is_loopback → blocked
        assert _is_safe_url("http://127.0.0.1/image.jpg") is False

    def test_ipv6_loopback_is_blocked(self):
        assert _is_safe_url("http://[::1]/image.jpg") is False

    def test_ipv6_private_is_blocked(self):
        # fc00::/7 is private
        assert _is_safe_url("http://[fc00::1]/image.jpg") is False

    def test_link_local_ip_is_blocked(self):
        # 169.254.x.x range (link-local)
        assert _is_safe_url("http://169.254.0.1/image.jpg") is False

    def test_hostname_passes_ip_parse_value_error(self):
        # Line 104: ValueError from ip_address() → pass (hostname is not an IP, safe)
        assert _is_safe_url("https://cdn.example.com/image.jpg") is True

    def test_exception_in_ip_check_returns_safe_true(self):
        # The inner try/except (ValueError) is for ip_address() parse failures on hostnames.
        # Public hostname that is not an IP → ValueError raised and caught → True returned.
        assert _is_safe_url("https://images.contentful.com/photo.jpg") is True

    def test_outer_exception_returns_false(self):
        # Line 107-108: outer except Exception → return False
        # We can trigger this by patching urlparse (imported locally inside _is_safe_url)
        # to raise a non-ValueError exception.
        with patch("urllib.parse.urlparse", side_effect=RuntimeError("forced error")):
            result = _is_safe_url("https://example.com/image.jpg")
        assert result is False


# ── _download() blocked path (lines 114-115) ──────────────────────────────────

class TestDownloadBlockedUrl:

    def test_private_ip_url_returns_none(self):
        # Line 114-115: _is_safe_url returns False → log warning and return None
        result = _download("http://192.168.1.1/image.jpg")
        assert result is None

    def test_loopback_url_returns_none(self):
        result = _download("http://127.0.0.1/image.jpg")
        assert result is None

    def test_metadata_endpoint_returns_none(self):
        result = _download("http://169.254.169.254/latest/meta-data/")
        assert result is None

    def test_ftp_url_returns_none(self):
        result = _download("ftp://example.com/image.jpg")
        assert result is None


# ── prepare_media() — OSError on write (lines 61-63) ─────────────────────────

class TestPrepareMediaWriteError:

    @staticmethod
    def _make_jpeg_bytes(w: int = 1200, h: int = 675) -> bytes:
        img = Image.new("RGB", (w, h), color=(100, 100, 200))
        buf = BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()

    def test_returns_none_when_write_raises_oserror(self, tmp_path):
        url = "https://example.com/test.jpg"
        jpeg_bytes = self._make_jpeg_bytes()
        dest = tmp_path / "abc123.jpg"

        mock_dest = MagicMock(spec=Path)
        mock_dest.exists.return_value = False
        mock_dest.write_bytes.side_effect = OSError("disk full")
        mock_dest.name = "abc123.jpg"

        with (
            patch("src.formatter.media.MEDIA_DIR", tmp_path),
            patch("src.formatter.media._dest_path", return_value=mock_dest),
            patch("src.formatter.media._download", return_value=b"raw"),
            patch("src.formatter.media._resize", return_value=jpeg_bytes),
        ):
            result = prepare_media(url)

        assert result is None


# ── _download() — redirect handling (lines 135-148) ──────────────────────────

class TestDownloadRedirects:
    """Tests for the manual redirect loop in _download()."""

    @staticmethod
    def _make_client_with_responses(responses: list) -> MagicMock:
        """Build a mock httpx.Client context manager that returns responses in order."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.get = MagicMock(side_effect=responses)
        return mock_client

    def test_follows_safe_redirect(self):
        """A redirect to a safe URL should be followed and content returned."""
        redirect_resp = MagicMock()
        redirect_resp.is_redirect = True
        redirect_resp.headers = {"location": "https://cdn.example.com/final.jpg"}

        final_resp = MagicMock()
        final_resp.is_redirect = False
        final_resp.raise_for_status = MagicMock()
        final_resp.content = b"image bytes"

        mock_client = self._make_client_with_responses([redirect_resp, final_resp])

        with patch("httpx.Client", return_value=mock_client):
            result = _download("https://example.com/redirect")

        assert result == b"image bytes"

    def test_blocks_unsafe_redirect(self):
        """A redirect to a private IP should be blocked."""
        redirect_resp = MagicMock()
        redirect_resp.is_redirect = True
        redirect_resp.headers = {"location": "http://192.168.1.1/evil.jpg"}

        mock_client = self._make_client_with_responses([redirect_resp])

        with patch("httpx.Client", return_value=mock_client):
            result = _download("https://example.com/redirect")

        assert result is None

    def test_returns_none_on_http_error_after_redirect(self):
        """HTTPError after following a redirect should return None."""
        import httpx as httpx_module

        redirect_resp = MagicMock()
        redirect_resp.is_redirect = True
        redirect_resp.headers = {"location": "https://cdn.example.com/img.jpg"}

        final_resp = MagicMock()
        final_resp.is_redirect = False
        final_resp.raise_for_status.side_effect = httpx_module.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
        final_resp.content = b""

        mock_client = self._make_client_with_responses([redirect_resp, final_resp])

        with patch("httpx.Client", return_value=mock_client):
            result = _download("https://example.com/redirect")

        assert result is None

    def test_direct_response_without_redirect(self):
        """Non-redirect response should be returned directly."""
        resp = MagicMock()
        resp.is_redirect = False
        resp.raise_for_status = MagicMock()
        resp.content = b"direct content"

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.get = MagicMock(return_value=resp)

        with patch("httpx.Client", return_value=mock_client):
            result = _download("https://example.com/img.jpg")

        assert result == b"direct content"


# ── cleanup_old_media() — already tested in test_media.py, but cover log path ─

class TestCleanupOldMediaLogging:

    def test_deletes_files_and_returns_correct_count(self, tmp_path):
        """Ensure cleanup path with logging branch is exercised."""
        for i in range(4):
            (tmp_path / f"media_{i:03d}.jpg").write_bytes(b"data")

        with patch("src.formatter.media.MEDIA_DIR", tmp_path):
            count = cleanup_old_media(max_files=2)

        assert count == 2
        remaining = list(tmp_path.glob("*.jpg"))
        assert len(remaining) == 2

    def test_exactly_at_limit_deletes_nothing(self, tmp_path):
        for i in range(3):
            (tmp_path / f"img_{i}.jpg").write_bytes(b"x")

        with patch("src.formatter.media.MEDIA_DIR", tmp_path):
            count = cleanup_old_media(max_files=3)

        assert count == 0
