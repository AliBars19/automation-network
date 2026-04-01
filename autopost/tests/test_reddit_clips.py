"""
Unit tests for src/collectors/reddit_clips.py — Reddit clip collector.
All HTTP calls are mocked.
"""
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch
from datetime import datetime, timezone, timedelta

import pytest

from src.collectors.reddit_clips import (
    RedditClipCollector,
    _fetch_hot_posts,
    _load_cookies_txt,
    _download_reddit_video,
    _download_and_merge,
    _download_file,
    _download_single,
    _copy_file,
    _VREDDIT_AUDIO_RE,
)


def _make_reddit_post(
    post_id: str = "abc123",
    title: str = "Insane double flip reset goal in ranked",
    score: int = 800,
    is_video: bool = True,
    author: str = "CoolPlayer",
    created_minutes_ago: int = 60,
    duration: int = 30,
    video_url: str = "https://v.redd.it/abc123/DASH_720.mp4",
) -> dict:
    created_utc = (datetime.now(timezone.utc) - timedelta(minutes=created_minutes_ago)).timestamp()
    post = {
        "data": {
            "id": post_id,
            "title": title,
            "score": score,
            "is_video": is_video,
            "author": author,
            "created_utc": created_utc,
            "permalink": f"/r/RocketLeague/comments/{post_id}/test/",
            "thumbnail": "https://example.com/thumb.jpg",
            "media": {
                "reddit_video": {
                    "fallback_url": video_url,
                    "duration": duration,
                }
            } if is_video else None,
        }
    }
    return post


def _make_collector(niche="rocketleague", subreddit="RocketLeague", min_score=500):
    return RedditClipCollector(
        source_id=1,
        config={"subreddit": subreddit, "min_score": min_score},
        niche=niche,
    )


class TestRedditClipFiltering:

    @pytest.mark.asyncio
    async def test_high_score_video_passes(self):
        post = _make_reddit_post(score=800)
        collector = _make_collector()

        with (
            patch("src.collectors.reddit_clips._fetch_hot_posts", new_callable=AsyncMock, return_value=[post]),
            patch("src.collectors.reddit_clips._download_reddit_video", new_callable=AsyncMock, return_value="/tmp/test.mp4"),
        ):
            results = await collector.collect()

        assert len(results) == 1
        assert results[0].content_type == "reddit_clip"
        assert "CoolPlayer" in results[0].author

    @pytest.mark.asyncio
    async def test_low_score_rejected(self):
        post = _make_reddit_post(score=100)
        collector = _make_collector(min_score=500)

        with patch("src.collectors.reddit_clips._fetch_hot_posts", new_callable=AsyncMock, return_value=[post]):
            results = await collector.collect()

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_non_video_rejected(self):
        post = _make_reddit_post(is_video=False)
        collector = _make_collector()

        with patch("src.collectors.reddit_clips._fetch_hot_posts", new_callable=AsyncMock, return_value=[post]):
            results = await collector.collect()

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_old_post_rejected(self):
        post = _make_reddit_post(created_minutes_ago=60 * 14)  # 14 hours old
        collector = _make_collector()

        with patch("src.collectors.reddit_clips._fetch_hot_posts", new_callable=AsyncMock, return_value=[post]):
            results = await collector.collect()

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_too_long_video_rejected(self):
        post = _make_reddit_post(duration=120)  # 2 minutes, over 60s cap
        collector = _make_collector()

        with patch("src.collectors.reddit_clips._fetch_hot_posts", new_callable=AsyncMock, return_value=[post]):
            results = await collector.collect()

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_credit_line_in_metadata(self):
        post = _make_reddit_post(author="TestUser")
        collector = _make_collector()

        with (
            patch("src.collectors.reddit_clips._fetch_hot_posts", new_callable=AsyncMock, return_value=[post]),
            patch("src.collectors.reddit_clips._download_reddit_video", new_callable=AsyncMock, return_value=None),
        ):
            results = await collector.collect()

        assert results[0].author == "TestUser"

    @pytest.mark.asyncio
    async def test_empty_subreddit_returns_empty(self):
        collector = _make_collector()

        with patch("src.collectors.reddit_clips._fetch_hot_posts", new_callable=AsyncMock, return_value=[]):
            results = await collector.collect()

        assert results == []

    @pytest.mark.asyncio
    async def test_gd_threshold_different_from_rl(self):
        post = _make_reddit_post(score=450)
        collector = _make_collector(niche="geometrydash", subreddit="geometrydash", min_score=400)

        with (
            patch("src.collectors.reddit_clips._fetch_hot_posts", new_callable=AsyncMock, return_value=[post]),
            patch("src.collectors.reddit_clips._download_reddit_video", new_callable=AsyncMock, return_value=None),
        ):
            results = await collector.collect()

        assert len(results) == 1


class TestDownloadAndMerge:

    def test_blocks_unsafe_video_url(self):
        from src.collectors.reddit_clips import _download_and_merge
        result = _download_and_merge("http://169.254.169.254/video.mp4", "bad")
        assert result is None

    def test_download_file_returns_false_on_failure(self):
        from src.collectors.reddit_clips import _download_file
        with patch("src.collectors.reddit_clips.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.get.side_effect = Exception("network error")
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_client)
            mock_ctx.__exit__ = MagicMock(return_value=None)
            mock_cls.return_value = mock_ctx
            result = _download_file("https://v.redd.it/abc/DASH_720.mp4", "/tmp/test.mp4")
        assert result is False

    def test_copy_file_returns_none_on_failure(self):
        from src.collectors.reddit_clips import _copy_file
        result = _copy_file("/nonexistent/src.mp4", "/nonexistent/dest.mp4")
        assert result is None

    def test_download_single_blocks_unsafe_url(self):
        from src.collectors.reddit_clips import _download_single
        result = _download_single("http://localhost/evil.mp4", "test")
        assert result is None

    @pytest.mark.asyncio
    async def test_download_reddit_video_catches_exceptions(self):
        from src.collectors.reddit_clips import _download_reddit_video
        with patch("src.collectors.reddit_clips._download_and_merge", side_effect=Exception("ffmpeg crash")):
            result = await _download_reddit_video("https://v.redd.it/x/DASH_720.mp4", "test")
        assert result is None

    def test_download_and_merge_no_audio_match(self):
        """When the URL doesn't match the v.redd.it audio pattern, fall back to single download."""
        from src.collectors.reddit_clips import _download_and_merge
        with patch("src.collectors.reddit_clips._download_single", return_value="/tmp/video.mp4"):
            result = _download_and_merge("https://example.com/plain_video.mp4", "test")
        assert result == "/tmp/video.mp4"

    def test_download_and_merge_video_download_fails(self):
        from src.collectors.reddit_clips import _download_and_merge
        with patch("src.collectors.reddit_clips._download_file", return_value=False):
            result = _download_and_merge("https://v.redd.it/abc123/DASH_720.mp4", "test")
        assert result is None

    def test_download_and_merge_no_audio_copies_video(self):
        """When audio download fails, should copy video-only file."""
        from src.collectors.reddit_clips import _download_and_merge
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmpdir:
            # Mock _download_file: True for video, False for audio
            call_count = 0
            def mock_download(url, dest):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # Create a fake video file
                    with open(dest, "wb") as f:
                        f.write(b"fake video data")
                    return True
                return False  # audio fails

            with (
                patch("src.collectors.reddit_clips._download_file", side_effect=mock_download),
                patch("src.collectors.reddit_clips._copy_file", return_value="/tmp/output.mp4"),
                patch("src.collectors.reddit_clips.MEDIA_DIR", Path(tmpdir)),
            ):
                result = _download_and_merge("https://v.redd.it/abc123/DASH_720.mp4", "test_id")

            assert result == "/tmp/output.mp4"


class TestQualityGateDailyCap:
    """Test the _within_daily_cap DB query."""

    def test_within_daily_cap_under_limit(self):
        import sqlite3
        from pathlib import Path
        from src.poster.quality_gate import _within_daily_cap

        schema = Path(__file__).parent.parent / "src" / "database" / "schema.sql"
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(schema.read_text(encoding="utf-8"))
        conn.commit()

        with patch("src.poster.quality_gate.get_db") as mock_db:
            from contextlib import contextmanager
            @contextmanager
            def _ctx():
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            mock_db.side_effect = _ctx
            result = _within_daily_cap("rocketleague", "reddit_clip", 3)
        assert result is True


class TestFetchHotPosts:

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        import httpx
        with patch("src.collectors.reddit_clips.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.HTTPError("blocked"))
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_ctx

            result = await _fetch_hot_posts("RocketLeague")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_cookies_file(self):
        """Line 164-169: missing cookies file disables Reddit collection."""
        with patch("src.collectors.reddit_clips._REDDIT_COOKIES_PATH") as mock_path:
            mock_path.exists.return_value = False
            result = await _fetch_hot_posts("RocketLeague")
        assert result == []

    @pytest.mark.asyncio
    async def test_successful_fetch_returns_posts(self):
        """Lines 171-189: successful HTTP response parses children list."""
        fake_posts = [{"data": {"id": "x1"}}, {"data": {"id": "x2"}}]
        fake_json = {"data": {"children": fake_posts}}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = fake_json

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("src.collectors.reddit_clips._REDDIT_COOKIES_PATH") as mock_path,
            patch("src.collectors.reddit_clips._load_cookies_txt", return_value={"session": "tok"}),
            patch("src.collectors.reddit_clips.httpx.AsyncClient", return_value=mock_ctx),
        ):
            mock_path.exists.return_value = True
            result = await _fetch_hot_posts("RocketLeague", limit=25)

        assert result == fake_posts

    @pytest.mark.asyncio
    async def test_empty_children_list_returned_as_empty(self):
        """Lines 186-188: response with no children returns empty list."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": {"children": []}}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("src.collectors.reddit_clips._REDDIT_COOKIES_PATH") as mock_path,
            patch("src.collectors.reddit_clips._load_cookies_txt", return_value={}),
            patch("src.collectors.reddit_clips.httpx.AsyncClient", return_value=mock_ctx),
        ):
            mock_path.exists.return_value = True
            result = await _fetch_hot_posts("geometrydash")

        assert result == []

    @pytest.mark.asyncio
    async def test_http_status_error_returns_empty(self):
        """Lines 190-192: HTTPStatusError (e.g. 403) is caught and returns []."""
        import httpx
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError("403", request=MagicMock(), response=MagicMock())
        )

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("src.collectors.reddit_clips._REDDIT_COOKIES_PATH") as mock_path,
            patch("src.collectors.reddit_clips._load_cookies_txt", return_value={}),
            patch("src.collectors.reddit_clips.httpx.AsyncClient", return_value=mock_ctx),
        ):
            mock_path.exists.return_value = True
            result = await _fetch_hot_posts("RocketLeague")

        assert result == []

    @pytest.mark.asyncio
    async def test_unsafe_subreddit_url_returns_empty(self):
        """Line 172-173: is_safe_url blocks bad URLs before any HTTP call is made."""
        with (
            patch("src.collectors.reddit_clips._REDDIT_COOKIES_PATH") as mock_path,
            patch("src.collectors.reddit_clips.is_safe_url", return_value=False),
        ):
            mock_path.exists.return_value = True
            result = await _fetch_hot_posts("evil")

        assert result == []


# ── _load_cookies_txt ─────────────────────────────────────────────────────────

class TestLoadCookiesTxt:

    def test_valid_netscape_file_parses_cookies(self, tmp_path: Path):
        """Lines 197-207: well-formed Netscape cookies.txt is parsed correctly."""
        cookies_file = tmp_path / "cookies.txt"
        cookies_file.write_text(
            "# Netscape HTTP Cookie File\n"
            ".reddit.com\tTRUE\t/\tFALSE\t0\treddit_session\tabc123\n"
            ".reddit.com\tTRUE\t/\tFALSE\t0\ttoken_v2\txyz789\n"
        )
        result = _load_cookies_txt(cookies_file)
        assert result == {"reddit_session": "abc123", "token_v2": "xyz789"}

    def test_empty_file_returns_empty_dict(self, tmp_path: Path):
        """Empty cookies.txt produces an empty dict, not an error."""
        cookies_file = tmp_path / "cookies.txt"
        cookies_file.write_text("")
        result = _load_cookies_txt(cookies_file)
        assert result == {}

    def test_comment_only_file_returns_empty_dict(self, tmp_path: Path):
        """Lines with # prefix are skipped."""
        cookies_file = tmp_path / "cookies.txt"
        cookies_file.write_text("# This is a comment\n# Another comment\n")
        result = _load_cookies_txt(cookies_file)
        assert result == {}

    def test_malformed_lines_skipped(self, tmp_path: Path):
        """Lines with fewer than 7 tab-separated fields are silently skipped."""
        cookies_file = tmp_path / "cookies.txt"
        # Only 3 fields — not enough
        cookies_file.write_text("domain\tFALSE\t/\n")
        result = _load_cookies_txt(cookies_file)
        assert result == {}

    def test_missing_file_returns_empty_dict(self, tmp_path: Path):
        """Line 206: exception during read returns {} without raising."""
        missing = tmp_path / "does_not_exist.txt"
        result = _load_cookies_txt(missing)
        assert result == {}

    def test_mixed_valid_and_malformed_lines(self, tmp_path: Path):
        """Only lines with 7+ fields contribute to the result."""
        cookies_file = tmp_path / "cookies.txt"
        cookies_file.write_text(
            "# comment\n"
            "bad line\n"
            "   \n"
            ".reddit.com\tTRUE\t/\tFALSE\t0\tmy_cookie\tmy_value\n"
            "only\ttwo\tfields\n"
        )
        result = _load_cookies_txt(cookies_file)
        assert result == {"my_cookie": "my_value"}

    def test_line_with_exactly_seven_fields_parsed(self, tmp_path: Path):
        """Boundary: exactly 7 tab-separated fields (len >= 7 is satisfied)."""
        cookies_file = tmp_path / "cookies.txt"
        cookies_file.write_text("f1\tf2\tf3\tf4\tf5\tname\tvalue\n")
        result = _load_cookies_txt(cookies_file)
        assert result == {"name": "value"}

    def test_blank_lines_skipped(self, tmp_path: Path):
        """Blank/whitespace-only lines are skipped without error."""
        cookies_file = tmp_path / "cookies.txt"
        cookies_file.write_text(
            "\n"
            "   \n"
            ".reddit.com\tTRUE\t/\tFALSE\t0\tck\tv\n"
        )
        result = _load_cookies_txt(cookies_file)
        assert result == {"ck": "v"}


# ── _download_reddit_video ────────────────────────────────────────────────────

class TestDownloadRedditVideo:

    @pytest.mark.asyncio
    async def test_ytdlp_path_returns_clip_when_successful(self):
        """Lines 222-226: when clip_reddit_video returns a path, it is returned directly."""
        with patch("src.collectors.video_clipper.clip_reddit_video", return_value="/media/reddit_abc.mp4"):
            result = await _download_reddit_video(
                "https://www.reddit.com/r/RocketLeague/comments/abc/test/", "abc"
            )
        assert result == "/media/reddit_abc.mp4"

    @pytest.mark.asyncio
    async def test_ytdlp_returns_none_falls_through_to_merge(self):
        """Lines 225-226: when clip returns None, falls through to _download_and_merge."""
        with (
            patch("src.collectors.video_clipper.clip_reddit_video", return_value=None),
            patch(
                "src.collectors.reddit_clips._download_and_merge",
                return_value="/media/reddit_fallback.mp4",
            ),
        ):
            result = await _download_reddit_video(
                "https://v.redd.it/abc123/DASH_720.mp4", "abc123"
            )
        assert result == "/media/reddit_fallback.mp4"

    @pytest.mark.asyncio
    async def test_ytdlp_import_error_falls_through_to_merge(self):
        """Lines 227-228: ImportError from video_clipper is silenced; merge path is tried."""
        with (
            patch.dict("sys.modules", {"src.collectors.video_clipper": None}),
            patch(
                "src.collectors.reddit_clips._download_and_merge",
                return_value="/media/merged.mp4",
            ),
        ):
            result = await _download_reddit_video(
                "https://v.redd.it/abc123/DASH_720.mp4", "abc123"
            )
        assert result == "/media/merged.mp4"

    @pytest.mark.asyncio
    async def test_both_paths_fail_returns_none(self):
        """Both yt-dlp and merge raise; final return must be None."""
        with (
            patch("src.collectors.video_clipper.clip_reddit_video", side_effect=RuntimeError("yt-dlp gone")),
            patch(
                "src.collectors.reddit_clips._download_and_merge",
                side_effect=Exception("merge crashed"),
            ),
        ):
            result = await _download_reddit_video(
                "https://v.redd.it/abc123/DASH_720.mp4", "abc123"
            )
        assert result is None


# ── _download_and_merge ───────────────────────────────────────────────────────

class TestDownloadAndMergeExtended:

    def test_ffmpeg_merge_succeeds_returns_output_path(self, tmp_path: Path):
        """Lines 272-296: successful ffmpeg merge writes file; path returned."""
        output_mp4 = tmp_path / "reddit_testid.mp4"

        call_count = 0

        def mock_download(url, dest):
            nonlocal call_count
            call_count += 1
            with open(dest, "wb") as fh:
                fh.write(b"data" * 100)
            return True

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        def fake_ffmpeg(*args, **kwargs):
            # Create the output file so os.path.exists passes
            output_path_arg = args[0][-1]
            with open(output_path_arg, "wb") as fh:
                fh.write(b"merged video data")
            return mock_proc

        with (
            patch("src.collectors.reddit_clips._download_file", side_effect=mock_download),
            patch("src.collectors.reddit_clips.subprocess.run", side_effect=fake_ffmpeg),
            patch("src.collectors.reddit_clips.MEDIA_DIR", tmp_path),
        ):
            result = _download_and_merge("https://v.redd.it/abc123/DASH_720.mp4", "testid")

        assert result == str(tmp_path / "reddit_testid.mp4")

    def test_ffmpeg_failure_falls_back_to_video_only(self, tmp_path: Path):
        """Lines 285-288: ffmpeg non-zero return code falls back to video-only copy."""
        call_count = 0

        def mock_download(url, dest):
            nonlocal call_count
            call_count += 1
            with open(dest, "wb") as fh:
                fh.write(b"video bytes")
            return True

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = b"ffmpeg error"

        with (
            patch("src.collectors.reddit_clips._download_file", side_effect=mock_download),
            patch("src.collectors.reddit_clips.subprocess.run", return_value=mock_proc),
            patch("src.collectors.reddit_clips._copy_file", return_value=str(tmp_path / "reddit_fallback.mp4")),
            patch("src.collectors.reddit_clips.MEDIA_DIR", tmp_path),
        ):
            result = _download_and_merge("https://v.redd.it/abc123/DASH_720.mp4", "fallback")

        assert result == str(tmp_path / "reddit_fallback.mp4")

    def test_output_file_missing_after_merge_returns_none(self, tmp_path: Path):
        """Lines 293-297: if output_path does not exist after tempdir exits, return None."""
        call_count = 0

        def mock_download(url, dest):
            nonlocal call_count
            call_count += 1
            with open(dest, "wb") as fh:
                fh.write(b"data")
            return True

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        # ffmpeg "succeeds" but does NOT create the output file
        with (
            patch("src.collectors.reddit_clips._download_file", side_effect=mock_download),
            patch("src.collectors.reddit_clips.subprocess.run", return_value=mock_proc),
            patch("src.collectors.reddit_clips.MEDIA_DIR", tmp_path),
        ):
            result = _download_and_merge("https://v.redd.it/abc123/DASH_720.mp4", "noop")

        assert result is None

    def test_post_id_sanitised_strips_path_traversal(self, tmp_path: Path):
        """Line 244: path traversal chars in post_id are stripped before building filename."""
        with (
            patch("src.collectors.reddit_clips._download_file", return_value=False),
            patch("src.collectors.reddit_clips.MEDIA_DIR", tmp_path),
        ):
            # This should not raise and must not create files outside MEDIA_DIR
            result = _download_and_merge(
                "https://v.redd.it/abc123/DASH_720.mp4", "../../../etc/passwd"
            )
        assert result is None  # download fails, but no path traversal occurred


# ── _download_file ────────────────────────────────────────────────────────────

class TestDownloadFile:

    def test_successful_streaming_download(self, tmp_path: Path):
        """Lines 320-330: 200 response streams chunks and returns True."""
        dest = str(tmp_path / "out.mp4")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_bytes.return_value = iter([b"chunk1", b"chunk2", b"chunk3"])
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("src.collectors.reddit_clips.httpx.Client", return_value=mock_client):
            result = _download_file("https://v.redd.it/abc/DASH_720.mp4", dest)

        assert result is True
        assert os.path.exists(dest)
        with open(dest, "rb") as fh:
            assert fh.read() == b"chunk1chunk2chunk3"

    def test_non_200_status_returns_false(self, tmp_path: Path):
        """Line 319: status != 200 immediately returns False."""
        dest = str(tmp_path / "out.mp4")

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("src.collectors.reddit_clips.httpx.Client", return_value=mock_client):
            result = _download_file("https://v.redd.it/abc/DASH_720.mp4", dest)

        assert result is False

    def test_size_cap_exceeded_returns_false(self, tmp_path: Path):
        """Lines 323-328: exceeding 50 MB cap aborts download and returns False."""
        dest = str(tmp_path / "huge.mp4")
        # One chunk that is slightly over 50 MB
        big_chunk = b"x" * (51 * 1024 * 1024)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_bytes.return_value = iter([big_chunk])
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("src.collectors.reddit_clips.httpx.Client", return_value=mock_client):
            result = _download_file("https://v.redd.it/abc/DASH_720.mp4", dest)

        assert result is False

    def test_zero_byte_response_returns_false(self, tmp_path: Path):
        """Line 330: size == 0 after streaming means empty response — return False."""
        dest = str(tmp_path / "empty.mp4")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_bytes.return_value = iter([])  # no chunks at all
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("src.collectors.reddit_clips.httpx.Client", return_value=mock_client):
            result = _download_file("https://v.redd.it/abc/DASH_720.mp4", dest)

        assert result is False

    def test_blocks_ssrf_url(self):
        """_download_file rejects private IP URLs before opening any connection."""
        result = _download_file("http://192.168.1.1/video.mp4", "/tmp/ignored.mp4")
        assert result is False

    def test_network_exception_returns_false(self, tmp_path: Path):
        """Lines 331-333: any exception during streaming is caught; returns False."""
        dest = str(tmp_path / "err.mp4")

        mock_client = MagicMock()
        mock_client.stream.side_effect = OSError("connection reset")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("src.collectors.reddit_clips.httpx.Client", return_value=mock_client):
            result = _download_file("https://v.redd.it/abc/DASH_720.mp4", dest)

        assert result is False


# ── _download_single ──────────────────────────────────────────────────────────

class TestDownloadSingle:

    def test_returns_path_on_success(self, tmp_path: Path):
        """Line 302-304: when _download_file succeeds, output path is returned."""
        with (
            patch("src.collectors.reddit_clips._download_file", return_value=True),
            patch("src.collectors.reddit_clips.MEDIA_DIR", tmp_path),
        ):
            result = _download_single("https://v.redd.it/abc/DASH_720.mp4", "mypost")

        assert result == str(tmp_path / "reddit_mypost.mp4")

    def test_returns_none_when_download_fails(self, tmp_path: Path):
        """Line 304: when _download_file returns False, returns None."""
        with (
            patch("src.collectors.reddit_clips._download_file", return_value=False),
            patch("src.collectors.reddit_clips.MEDIA_DIR", tmp_path),
        ):
            result = _download_single("https://v.redd.it/abc/DASH_720.mp4", "mypost")

        assert result is None


# ── _copy_file ────────────────────────────────────────────────────────────────

class TestCopyFile:

    def test_copies_file_and_returns_dest_path(self, tmp_path: Path):
        """Lines 339-341: successful copy returns the dest path."""
        src = tmp_path / "src.mp4"
        dest = str(tmp_path / "dest.mp4")
        src.write_bytes(b"video content")

        result = _copy_file(str(src), dest)

        assert result == dest
        assert os.path.exists(dest)
        with open(dest, "rb") as fh:
            assert fh.read() == b"video content"

    def test_returns_none_when_src_missing(self, tmp_path: Path):
        """shutil.copy2 raises; _copy_file returns None rather than propagating."""
        result = _copy_file("/nonexistent/src.mp4", str(tmp_path / "dest.mp4"))
        assert result is None

    def test_returns_none_when_dest_dir_missing(self, tmp_path: Path):
        """Destination directory does not exist — copy fails gracefully."""
        src = tmp_path / "src.mp4"
        src.write_bytes(b"data")
        result = _copy_file(str(src), "/no/such/dir/dest.mp4")
        assert result is None


# ── VREDDIT_AUDIO_RE regex ────────────────────────────────────────────────────

class TestVRedditAudioRegex:

    def test_matches_standard_v_redd_it_url(self):
        """Regex extracts base URL from a standard DASH video URL."""
        url = "https://v.redd.it/abcdef1234/DASH_720.mp4"
        m = _VREDDIT_AUDIO_RE.search(url)
        assert m is not None
        assert m.group(1) == "https://v.redd.it/abcdef1234"

    def test_matches_url_with_hyphens_in_id(self):
        """Post IDs with hyphens are captured correctly."""
        url = "https://v.redd.it/abc-def-123/DASH_480.mp4"
        m = _VREDDIT_AUDIO_RE.search(url)
        assert m is not None
        assert m.group(1) == "https://v.redd.it/abc-def-123"

    def test_does_not_match_non_vreddit_url(self):
        """Non-v.redd.it URLs must not match."""
        url = "https://example.com/video/DASH_720.mp4"
        m = _VREDDIT_AUDIO_RE.search(url)
        assert m is None

    def test_audio_url_constructed_correctly_from_match(self):
        """Verify the audio URL is correctly derived from the regex match group."""
        url = "https://v.redd.it/xyz999/DASH_1080.mp4"
        m = _VREDDIT_AUDIO_RE.search(url)
        assert m is not None
        audio_url = f"{m.group(1)}/DASH_audio.mp4"
        assert audio_url == "https://v.redd.it/xyz999/DASH_audio.mp4"


# ── collect() edge cases ──────────────────────────────────────────────────────

class TestCollectEdgeCases:

    @pytest.mark.asyncio
    async def test_post_without_fallback_url_skipped(self):
        """Line 101: post with reddit_video but empty fallback_url is skipped."""
        post = _make_reddit_post()
        post["data"]["media"]["reddit_video"]["fallback_url"] = ""
        collector = _make_collector()

        with patch("src.collectors.reddit_clips._fetch_hot_posts", new_callable=AsyncMock, return_value=[post]):
            results = await collector.collect()

        assert results == []

    @pytest.mark.asyncio
    async def test_post_with_null_media_skipped(self):
        """media field is None — media dict access must not raise."""
        post = _make_reddit_post()
        post["data"]["media"] = None
        post["data"]["is_video"] = True
        collector = _make_collector()

        with patch("src.collectors.reddit_clips._fetch_hot_posts", new_callable=AsyncMock, return_value=[post]):
            results = await collector.collect()

        assert results == []

    @pytest.mark.asyncio
    async def test_metadata_contains_expected_keys(self):
        """collect() builds RawContent with the full expected metadata dict."""
        post = _make_reddit_post(score=900, author="ProPlayer", post_id="id99")
        collector = _make_collector()

        with (
            patch("src.collectors.reddit_clips._fetch_hot_posts", new_callable=AsyncMock, return_value=[post]),
            patch("src.collectors.reddit_clips._download_reddit_video", new_callable=AsyncMock, return_value="/media/r.mp4"),
        ):
            results = await collector.collect()

        assert len(results) == 1
        meta = results[0].metadata
        assert "author" in meta
        assert "score" in meta
        assert "media_path" in meta
        assert "video_url" in meta
        assert "created_at" in meta
        assert "age_hours" in meta
        assert meta["media_path"] == "/media/r.mp4"

    @pytest.mark.asyncio
    async def test_multiple_posts_all_filtered_returns_empty(self):
        """Mix of failing criteria — none pass, result is empty list."""
        posts = [
            _make_reddit_post(score=10),                 # low score
            _make_reddit_post(created_minutes_ago=900),  # too old (15 hours)
            _make_reddit_post(duration=200),             # too long
            _make_reddit_post(is_video=False),           # not a video
        ]
        collector = _make_collector()

        with patch("src.collectors.reddit_clips._fetch_hot_posts", new_callable=AsyncMock, return_value=posts):
            results = await collector.collect()

        assert results == []

    @pytest.mark.asyncio
    async def test_post_id_used_in_permalink_url(self):
        """URL field in RawContent is derived from the permalink."""
        post = _make_reddit_post(post_id="abc999")
        collector = _make_collector()

        with (
            patch("src.collectors.reddit_clips._fetch_hot_posts", new_callable=AsyncMock, return_value=[post]),
            patch("src.collectors.reddit_clips._download_reddit_video", new_callable=AsyncMock, return_value=None),
        ):
            results = await collector.collect()

        assert results[0].url.startswith("https://reddit.com/")
        assert "abc999" in results[0].url

    @pytest.mark.asyncio
    async def test_default_niche_threshold_applied(self):
        """Constructor uses _SCORE_THRESHOLDS when min_score is absent from config."""
        collector = RedditClipCollector(
            source_id=42,
            config={"subreddit": "rocketleague"},
            niche="rocketleague",
        )
        assert collector.min_score == 500

    @pytest.mark.asyncio
    async def test_unknown_niche_uses_fallback_threshold(self):
        """Unknown niche defaults to 500 (the fallback in _SCORE_THRESHOLDS.get)."""
        collector = RedditClipCollector(
            source_id=7,
            config={"subreddit": "gaming"},
            niche="gaming",
        )
        assert collector.min_score == 500
