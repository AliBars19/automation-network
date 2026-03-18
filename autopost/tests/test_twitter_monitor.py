"""
Unit tests for src/collectors/twitter_monitor.py (TwitterAPI.io).

All HTTP calls are mocked — no network access.
"""
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.base import RawContent
from src.collectors.twitter_monitor import TwitterMonitorCollector


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_tweet(
    tweet_id: str = "100",
    text: str = "Rocket League Season 14 is here!",
    created_at: str | None = None,
    is_reply: bool = False,
    retweeted_tweet=None,
    user_name: str = "RocketLeague",
    media: list | None = None,
    urls: list | None = None,
):
    """Build a TwitterAPI.io tweet dict."""
    if created_at is None:
        dt = datetime.now(timezone.utc) - timedelta(hours=1)
        created_at = format_datetime(dt)

    tweet = {
        "id": tweet_id,
        "text": text,
        "createdAt": created_at,
        "isReply": is_reply,
        "url": f"https://x.com/{user_name}/status/{tweet_id}",
        "author": {"userName": user_name, "id": "12345"},
        "entities": {"urls": urls or [], "media": media or []},
    }
    if retweeted_tweet is not None:
        tweet["retweeted_tweet"] = retweeted_tweet
    return tweet


def _make_collector(niche: str = "rocketleague", username: str = "RocketLeague"):
    return TwitterMonitorCollector(
        source_id=1,
        config={"account_id": username},
        niche=niche,
    )


def _mock_response(tweets: list, status_code: int = 200):
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"tweets": tweets, "has_next_page": False}
    resp.raise_for_status = MagicMock()
    return resp


def _patch_httpx(resp):
    """Return a patch context for httpx.AsyncClient that returns resp."""
    mock_client = AsyncMock()
    mock_client.get.return_value = resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return patch("src.collectors.twitter_monitor.httpx.AsyncClient", return_value=mock_client)


# ── collect() — API failures ──────────────────────────────────────────────────

class TestCollectApiFailures:

    @pytest.mark.asyncio
    async def test_returns_empty_when_api_key_not_set(self):
        collector = _make_collector()
        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", None):
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        collector = _make_collector()
        resp = _mock_response([], status_code=500)
        resp.raise_for_status.side_effect = Exception("500 Server Error")

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with _patch_httpx(resp):
                result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_429(self):
        collector = _make_collector()
        resp = MagicMock()
        resp.status_code = 429

        mock_client = AsyncMock()
        mock_client.get.return_value = resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with patch("src.collectors.twitter_monitor.httpx.AsyncClient", return_value=mock_client):
                result = await collector.collect()
        assert result == []


# ── collect() — tweet filtering ───────────────────────────────────────────────

class TestCollectFiltering:

    @pytest.mark.asyncio
    async def test_normal_tweet_included(self):
        tweet = _make_tweet(text="New content drop!")
        resp = _mock_response([tweet])
        collector = _make_collector()

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with _patch_httpx(resp):
                result = await collector.collect()
        assert len(result) == 1
        assert isinstance(result[0], RawContent)

    @pytest.mark.asyncio
    async def test_retweet_excluded(self):
        tweet = _make_tweet(retweeted_tweet={"id": "456"}, text="RT stuff")
        resp = _mock_response([tweet])
        collector = _make_collector()

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with _patch_httpx(resp):
                result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_reply_excluded(self):
        tweet = _make_tweet(is_reply=True, text="Thanks!")
        resp = _mock_response([tweet])
        collector = _make_collector()

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with _patch_httpx(resp):
                result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_at_reply_excluded_by_text(self):
        tweet = _make_tweet(text="@SomeUser Thanks for the question!")
        resp = _mock_response([tweet])
        collector = _make_collector()

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with _patch_httpx(resp):
                result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_text_excluded(self):
        tweet = _make_tweet(text="")
        resp = _mock_response([tweet])
        collector = _make_collector()

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with _patch_httpx(resp):
                result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_no_tweet_id_excluded(self):
        tweet = _make_tweet(tweet_id="")
        resp = _mock_response([tweet])
        collector = _make_collector()

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with _patch_httpx(resp):
                result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_old_tweet_excluded(self):
        old_date = datetime.now(timezone.utc) - timedelta(days=10)
        tweet = _make_tweet(created_at=format_datetime(old_date))
        resp = _mock_response([tweet])
        collector = _make_collector()

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with _patch_httpx(resp):
                result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_tweet_no_date_passes_through(self):
        tweet = _make_tweet(text="No date tweet", created_at="")
        resp = _mock_response([tweet])
        collector = _make_collector()

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with _patch_httpx(resp):
                result = await collector.collect()
        assert len(result) == 1


# ── collect() — RawContent fields ────────────────────────────────────────────

class TestCollectRawContentFields:

    @pytest.mark.asyncio
    async def test_fields_populated(self):
        tweet = _make_tweet(tweet_id="42", text="Season 14 live!", user_name="RocketLeague")
        resp = _mock_response([tweet])
        collector = _make_collector()

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with _patch_httpx(resp):
                result = await collector.collect()

        item = result[0]
        assert item.source_id == 1
        assert item.external_id == "42"
        assert item.niche == "rocketleague"
        assert item.content_type == "official_tweet"
        assert item.author == "RocketLeague"
        assert item.metadata["retweet_id"] == "42"

    @pytest.mark.asyncio
    async def test_gd_content_type(self):
        tweet = _make_tweet(text="GD 2.3 coming!")
        resp = _mock_response([tweet])
        collector = _make_collector(niche="geometrydash")

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with _patch_httpx(resp):
                result = await collector.collect()
        assert result[0].content_type == "robtop_tweet"

    @pytest.mark.asyncio
    async def test_url_expansion(self):
        tweet = _make_tweet(
            text="Check this https://t.co/abc123",
            urls=[{"url": "https://t.co/abc123", "expanded_url": "https://rocketleague.com/news"}],
        )
        resp = _mock_response([tweet])
        collector = _make_collector()

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with _patch_httpx(resp):
                result = await collector.collect()
        assert "rocketleague.com/news" in result[0].body

    @pytest.mark.asyncio
    async def test_trailing_tco_stripped(self):
        tweet = _make_tweet(text="Look at this https://t.co/xyz999")
        resp = _mock_response([tweet])
        collector = _make_collector()

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with _patch_httpx(resp):
                result = await collector.collect()
        assert "t.co" not in result[0].body

    @pytest.mark.asyncio
    async def test_image_from_extended_entities(self):
        tweet = _make_tweet(text="Media tweet")
        tweet["extendedEntities"] = {
            "media": [{"media_url_https": "https://pbs.twimg.com/media/img.jpg", "type": "photo"}]
        }
        resp = _mock_response([tweet])
        collector = _make_collector()

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with _patch_httpx(resp):
                result = await collector.collect()
        assert result[0].image_url == "https://pbs.twimg.com/media/img.jpg"

    @pytest.mark.asyncio
    async def test_multiple_tweets(self):
        tweets = [_make_tweet(tweet_id=str(i), text=f"Update {i}") for i in range(1, 6)]
        resp = _mock_response(tweets)
        collector = _make_collector()

        with patch("src.collectors.twitter_monitor.TWITTERAPI_IO_KEY", "key"):
            with _patch_httpx(resp):
                result = await collector.collect()
        assert len(result) == 5
