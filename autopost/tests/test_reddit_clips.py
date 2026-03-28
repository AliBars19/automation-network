"""
Unit tests for src/collectors/reddit_clips.py — Reddit clip collector.
All HTTP calls are mocked.
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

import pytest

from src.collectors.reddit_clips import (
    RedditClipCollector,
    _fetch_hot_posts,
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
