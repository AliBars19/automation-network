"""
Unit tests for src/collectors/twitter_monitor.py

All twscrape API calls are mocked — no network access.
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.base import RawContent
from src.collectors.twitter_monitor import TwitterMonitorCollector


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_tweet(
    tweet_id: int = 100,
    text: str = "Rocket League Season 14 is here!",
    date: datetime | None = None,
    retweeted: bool = False,
    reply_to_user=None,
    user_username: str = "RocketLeague",
    has_media: bool = False,
    links: list | None = None,
):
    """Build a mock twscrape Tweet object."""
    tweet = MagicMock()
    tweet.id = tweet_id
    tweet.rawContent = text
    tweet.date = date or datetime.now(timezone.utc) - timedelta(hours=1)
    tweet.retweetedTweet = MagicMock() if retweeted else None
    tweet.inReplyToUser = MagicMock() if reply_to_user else None
    tweet.url = f"https://x.com/{user_username}/status/{tweet_id}"

    user = MagicMock()
    user.username = user_username
    tweet.user = user

    if has_media:
        photo = MagicMock()
        photo.url = "https://pbs.twimg.com/media/example.jpg"
        tweet.media = MagicMock()
        tweet.media.photos = [photo]
        tweet.media.videos = []
    else:
        tweet.media = MagicMock()
        tweet.media.photos = []
        tweet.media.videos = []

    tweet.links = links or []
    return tweet


def _make_collector(niche: str = "rocketleague", username: str = "RocketLeague"):
    return TwitterMonitorCollector(
        source_id=1,
        config={"account_id": username},
        niche=niche,
    )


# ── collect() — API failures ──────────────────────────────────────────────────

class TestCollectApiFailures:

    @pytest.mark.asyncio
    async def test_returns_empty_when_api_is_none(self):
        """If get_api() returns None, collect() returns an empty list."""
        collector = _make_collector()
        with patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=None):
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_user_id_unresolvable(self):
        """If resolve_user_id() returns None, collect() returns an empty list."""
        collector = _make_collector()
        mock_api = MagicMock()
        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=None),
        ):
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_gather_raises(self):
        """If gather() raises, collect() catches and returns empty list."""
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=12345),
            patch("src.collectors.twitter_monitor.gather", side_effect=Exception("network error")),
        ):
            result = await collector.collect()
        assert result == []


# ── collect() — tweet filtering ───────────────────────────────────────────────

class TestCollectFiltering:

    @pytest.mark.asyncio
    async def test_normal_tweet_included(self):
        tweet = _make_tweet(text="New content drop!")
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert len(result) == 1
        assert isinstance(result[0], RawContent)

    @pytest.mark.asyncio
    async def test_retweet_excluded(self):
        tweet = _make_tweet(retweeted=True, text="RT @someone: Cool update")
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert result == []

    @pytest.mark.asyncio
    async def test_reply_excluded_by_field(self):
        tweet = _make_tweet(reply_to_user=True, text="Thanks for letting me know!")
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert result == []

    @pytest.mark.asyncio
    async def test_at_reply_excluded_by_text_prefix(self):
        tweet = _make_tweet(text="@SomeUser Thanks for the question!")
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert result == []

    @pytest.mark.asyncio
    async def test_empty_text_excluded(self):
        tweet = _make_tweet(text="")
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert result == []

    @pytest.mark.asyncio
    async def test_zero_id_excluded(self):
        tweet = _make_tweet(tweet_id=0)
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert result == []

    @pytest.mark.asyncio
    async def test_old_tweet_excluded(self):
        """Tweet from 10 days ago should be filtered out."""
        old_date = datetime.now(timezone.utc) - timedelta(days=10)
        tweet = _make_tweet(date=old_date)
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert result == []

    @pytest.mark.asyncio
    async def test_tweet_no_date_passes_through(self):
        """Tweet with date=None should be allowed through (defensive)."""
        tweet = _make_tweet(text="Undated announcement")
        tweet.date = None
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert len(result) == 1


# ── collect() — RawContent fields ────────────────────────────────────────────

class TestCollectRawContentFields:

    @pytest.mark.asyncio
    async def test_raw_content_fields_populated(self):
        """Verify the RawContent produced has the expected field values."""
        tweet = _make_tweet(
            tweet_id=42,
            text="Season 14 is officially live!",
            user_username="RocketLeague",
        )
        collector = _make_collector(niche="rocketleague", username="RocketLeague")
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert len(result) == 1
        item = result[0]
        assert item.source_id == 1
        assert item.external_id == "42"
        assert item.niche == "rocketleague"
        assert item.content_type == "official_tweet"
        assert item.author == "RocketLeague"
        assert "retweet_id" in item.metadata
        assert item.metadata["retweet_id"] == "42"

    @pytest.mark.asyncio
    async def test_gd_content_type_is_robtop_tweet(self):
        """GD niche uses robtop_tweet content_type."""
        tweet = _make_tweet(text="GD 2.3 is coming!")
        collector = _make_collector(niche="geometrydash", username="RobTopGames")
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert len(result) == 1
        assert result[0].content_type == "robtop_tweet"

    @pytest.mark.asyncio
    async def test_image_url_extracted_from_photo(self):
        """Image URL should be populated from tweet.media.photos."""
        tweet = _make_tweet(has_media=True)
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert len(result) == 1
        assert result[0].image_url == "https://pbs.twimg.com/media/example.jpg"

    @pytest.mark.asyncio
    async def test_tco_link_expanded_in_text(self):
        """t.co links in text should be replaced with expanded URLs."""
        link = MagicMock()
        link.tcourl = "https://t.co/abc123"
        link.url = "https://www.rocketleague.com/news/season-14"
        tweet = _make_tweet(
            text="New season! https://t.co/abc123",
            links=[link],
        )
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert len(result) == 1
        assert "https://www.rocketleague.com/news/season-14" in result[0].body

    @pytest.mark.asyncio
    async def test_trailing_tco_stripped(self):
        """Trailing t.co media links should be stripped from cleaned text."""
        tweet = _make_tweet(text="Check this out https://t.co/xyz999")
        tweet.links = []
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert len(result) == 1
        assert "t.co" not in result[0].body

    @pytest.mark.asyncio
    async def test_unknown_niche_defaults_to_official_tweet(self):
        """A niche not in _CONTENT_TYPE should default to 'official_tweet'."""
        tweet = _make_tweet()
        collector = TwitterMonitorCollector(
            source_id=1,
            config={"account_id": "someaccount"},
            niche="unknown_niche",
        )
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert len(result) == 1
        assert result[0].content_type == "official_tweet"

    @pytest.mark.asyncio
    async def test_multiple_tweets_all_returned(self):
        """Multiple valid tweets should all be collected."""
        tweets = [_make_tweet(tweet_id=i, text=f"Update {i}") for i in range(1, 6)]
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=tweets),
        ):
            result = await collector.collect()

        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_video_thumbnail_used_when_no_photo(self):
        """If only video media, use thumbnailUrl as image_url."""
        tweet = _make_tweet(text="Watch this!")
        video = MagicMock()
        video.thumbnailUrl = "https://pbs.twimg.com/ext_tw_video_thumb/123/pu/img/thumb.jpg"
        tweet.media.photos = []
        tweet.media.videos = [video]
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert len(result) == 1
        assert "thumb.jpg" in result[0].image_url

    @pytest.mark.asyncio
    async def test_fallback_url_constructed_when_tweet_url_missing(self):
        """If tweet.url is None or empty, URL is built from username + id."""
        tweet = _make_tweet(tweet_id=55, user_username="gdrobtop")
        tweet.url = None
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert len(result) == 1
        assert "55" in result[0].url

    @pytest.mark.asyncio
    async def test_created_at_formatted_from_date(self):
        """metadata['created_at'] should be a non-empty string when tweet.date is set."""
        tweet = _make_tweet(date=datetime(2026, 3, 17, 12, 0, 0, tzinfo=timezone.utc))
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert result[0].metadata["created_at"] != ""


# ── collect() — edge-case branches ───────────────────────────────────────────

class TestCollectEdgeCases:
    """Cover defensive branches: naive datetime, strftime failure, media AttributeError,
    link expansion TypeError, and tweet.user being None."""

    @pytest.mark.asyncio
    async def test_naive_datetime_gets_utc_timezone(self):
        """A naive datetime (no tzinfo) should be treated as UTC and not raise."""
        naive_date = datetime(2026, 3, 17, 12, 0, 0)  # no tzinfo
        assert naive_date.tzinfo is None
        tweet = _make_tweet(text="Timezone-naive tweet", date=naive_date)
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        # Naive datetime is treated as UTC; tweet is recent so it should be included
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_date_parse_exception_lets_tweet_through(self):
        """If date arithmetic raises, the except clause lets the tweet pass through."""
        # Give tweet.date a value whose subtraction will raise
        bad_date = MagicMock()
        bad_date.tzinfo = None
        bad_date.replace.side_effect = Exception("cannot replace tzinfo")
        tweet = _make_tweet(text="Tweet with broken date")
        tweet.date = bad_date
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        # Exception in date block -> pass -> tweet included
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_strftime_exception_falls_back_to_str(self):
        """If tweet.date.strftime raises, created_at falls back to str(tweet.date)."""
        bad_date = MagicMock()
        bad_date.tzinfo = MagicMock()  # truthy tzinfo so the replace branch is skipped
        bad_date.__sub__ = MagicMock(return_value=timedelta(hours=1))  # age < 7 days
        bad_date.strftime.side_effect = Exception("bad format")
        bad_date.__str__ = MagicMock(return_value="2026-03-17T12:00:00")

        tweet = _make_tweet(text="Tweet with bad strftime")
        tweet.date = bad_date
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert len(result) == 1
        # created_at should fall back to str representation
        assert result[0].metadata["created_at"] == "2026-03-17T12:00:00"

    @pytest.mark.asyncio
    async def test_media_attribute_error_yields_empty_image_url(self):
        """If accessing media internals raises AttributeError, image_url stays empty string."""
        tweet = _make_tweet(text="Tweet with broken media")
        # Make .media.photos truthy (so we enter the if branch) but .photos[0].url raise AttributeError
        broken_photo = MagicMock()
        del broken_photo.url  # accessing .url raises AttributeError
        tweet.media = MagicMock()
        tweet.media.photos = [broken_photo]
        tweet.media.videos = []
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert len(result) == 1
        assert result[0].image_url == ""

    @pytest.mark.asyncio
    async def test_link_expansion_type_error_does_not_crash(self):
        """If tweet.links is not iterable (raises TypeError), link expansion is skipped."""
        tweet = _make_tweet(text="Tweet with broken links")
        # Make .links raise TypeError when iterated
        bad_links = MagicMock()
        bad_links.__iter__ = MagicMock(side_effect=TypeError("not iterable"))
        # Override the `or []` fallback by making links truthy but broken
        tweet.links = bad_links
        collector = _make_collector()
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_tweet_user_none_falls_back_to_username(self):
        """If tweet.user is None, screen_name falls back to self.username."""
        tweet = _make_tweet(text="Anonymous tweet")
        tweet.user = None
        collector = _make_collector(username="RocketLeague")
        mock_api = MagicMock()

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_api),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor.gather", return_value=[tweet]),
        ):
            result = await collector.collect()

        assert len(result) == 1
        assert result[0].author == "RocketLeague"
