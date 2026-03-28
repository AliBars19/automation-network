"""
Unit tests for src/collectors/reddit_clips.py — Reddit clip collector.
All HTTP calls are mocked.
"""
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
