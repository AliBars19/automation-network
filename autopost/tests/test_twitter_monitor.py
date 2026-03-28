"""
Unit tests for src/collectors/twitter_monitor.py (GraphQL API).

All HTTP calls are mocked — no network access.
"""
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.base import RawContent
from src.collectors.twitter_monitor import TwitterMonitorCollector, _extract_tweets


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_tweet_dict(
    tweet_id: str = "100",
    text: str = "Rocket League Season 14 is here!",
    created_at: str | None = None,
    is_reply_to: str | None = None,
    is_retweet: bool = False,
    screen_name: str = "RocketLeague",
    media: list | None = None,
    urls: list | None = None,
):
    """Build a GraphQL tweet result dict."""
    if created_at is None:
        dt = datetime.now(timezone.utc) - timedelta(hours=1)
        created_at = format_datetime(dt)

    legacy = {
        "id_str": tweet_id,
        "full_text": text,
        "created_at": created_at,
        "entities": {"urls": urls or [], "media": media or []},
    }
    if is_reply_to:
        legacy["in_reply_to_user_id_str"] = is_reply_to

    tweet = {
        "legacy": legacy,
        "core": {
            "user_results": {
                "result": {
                    "legacy": {"screen_name": screen_name},
                }
            }
        },
    }
    if is_retweet:
        tweet["retweeted_status_result"] = {"result": {"legacy": {"id_str": "999"}}}

    return tweet


def _wrap_in_timeline(tweets: list[dict]) -> dict:
    """Wrap tweet dicts in the GraphQL timeline response structure."""
    entries = []
    for t in tweets:
        entries.append({
            "content": {
                "itemContent": {
                    "tweet_results": {
                        "result": t
                    }
                }
            }
        })
    return {
        "data": {
            "user": {
                "result": {
                    "timeline_v2": {
                        "timeline": {
                            "instructions": [{"entries": entries}]
                        }
                    }
                }
            }
        }
    }


def _make_collector(niche: str = "rocketleague", username: str = "RocketLeague", retweet: bool = False):
    return TwitterMonitorCollector(
        source_id=1,
        config={"account_id": username, "retweet": retweet},
        niche=niche,
    )


def _patches(gql_response: dict):
    """Return context managers that mock get_api, resolve_user_id, and gql_get."""
    mock_client = MagicMock()
    mock_client.gql_get = AsyncMock(return_value=gql_response)
    return (
        patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_client),
        patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
    )


# ── collect() — API failures ──────────────────────────────────────────────────

class TestCollectApiFailures:

    @pytest.mark.asyncio
    async def test_returns_empty_when_client_is_none(self):
        collector = _make_collector()
        with patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=None):
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_user_id_unresolvable(self):
        collector = _make_collector()
        mock_client = MagicMock()
        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_client),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=None),
        ):
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_gql_raises(self):
        collector = _make_collector()
        mock_client = MagicMock()
        mock_client.gql_get = AsyncMock(side_effect=Exception("network error"))
        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_client),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
        ):
            result = await collector.collect()
        assert result == []


# ── collect() — tweet filtering ───────────────────────────────────────────────

class TestCollectFiltering:

    @pytest.mark.asyncio
    async def test_normal_tweet_included(self):
        tweet = _make_tweet_dict(text="New content drop!")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1
        assert isinstance(result[0], RawContent)

    @pytest.mark.asyncio
    async def test_retweet_excluded(self):
        tweet = _make_tweet_dict(is_retweet=True, text="RT stuff")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_reply_excluded(self):
        tweet = _make_tweet_dict(is_reply_to="12345", text="Thanks!")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_at_reply_excluded_by_text(self):
        tweet = _make_tweet_dict(text="@SomeUser Thanks!")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_text_excluded(self):
        tweet = _make_tweet_dict(text="")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_no_tweet_id_excluded(self):
        tweet = _make_tweet_dict(tweet_id="")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_old_tweet_excluded(self):
        old_date = datetime.now(timezone.utc) - timedelta(days=10)
        tweet = _make_tweet_dict(created_at=format_datetime(old_date))
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_tweet_no_date_passes_through(self):
        tweet = _make_tweet_dict(text="No date", created_at="")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1


# ── collect() — RawContent fields ────────────────────────────────────────────

class TestCollectRawContentFields:

    @pytest.mark.asyncio
    async def test_retweet_source_fields_populated(self):
        """Official accounts with retweet: true get retweet content type + retweet_id."""
        tweet = _make_tweet_dict(tweet_id="42", text="Rocket League Season 14 live!", screen_name="RocketLeague")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=True)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        item = result[0]
        assert item.source_id == 1
        assert item.external_id == "42"
        assert item.niche == "rocketleague"
        assert item.content_type == "official_tweet"
        assert item.author == "RocketLeague"
        assert item.metadata["retweet_id"] == "42"

    @pytest.mark.asyncio
    async def test_non_retweet_source_gets_monitored_tweet(self):
        """Non-official accounts (no retweet flag) get monitored_tweet type, no retweet_id."""
        tweet = _make_tweet_dict(tweet_id="42", text="Season 14 live!", screen_name="SomePlayer")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        item = result[0]
        assert item.content_type == "monitored_tweet"
        assert "retweet_id" not in item.metadata

    @pytest.mark.asyncio
    async def test_gd_retweet_content_type(self):
        tweet = _make_tweet_dict(text="Geometry Dash 2.3 coming!")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(niche="geometrydash", retweet=True)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result[0].content_type == "robtop_tweet"

    @pytest.mark.asyncio
    async def test_gd_non_retweet_content_type(self):
        tweet = _make_tweet_dict(text="GD 2.3 coming!")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(niche="geometrydash", retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result[0].content_type == "monitored_tweet"

    @pytest.mark.asyncio
    async def test_url_expansion(self):
        tweet = _make_tweet_dict(
            text="Check this https://t.co/abc123",
            urls=[{"url": "https://t.co/abc123", "expanded_url": "https://rocketleague.com/news"}],
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert "rocketleague.com/news" in result[0].body

    @pytest.mark.asyncio
    async def test_trailing_tco_stripped(self):
        tweet = _make_tweet_dict(text="Look at this https://t.co/xyz999")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert "t.co" not in result[0].body

    @pytest.mark.asyncio
    async def test_image_from_extended_entities(self):
        tweet = _make_tweet_dict(text="Media tweet")
        tweet["legacy"]["extended_entities"] = {
            "media": [{"media_url_https": "https://pbs.twimg.com/media/img.jpg", "type": "photo"}]
        }
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result[0].image_url == "https://pbs.twimg.com/media/img.jpg"

    @pytest.mark.asyncio
    async def test_multiple_tweets(self):
        tweets = [_make_tweet_dict(tweet_id=str(i), text=f"Update {i}") for i in range(1, 6)]
        resp = _wrap_in_timeline(tweets)
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_unknown_niche_retweet_defaults_to_official_tweet(self):
        tweet = _make_tweet_dict()
        resp = _wrap_in_timeline([tweet])
        collector = TwitterMonitorCollector(source_id=1, config={"account_id": "x", "retweet": True}, niche="unknown")
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result[0].content_type == "official_tweet"

    @pytest.mark.asyncio
    async def test_unknown_niche_no_retweet_gets_monitored_tweet(self):
        tweet = _make_tweet_dict()
        resp = _wrap_in_timeline([tweet])
        collector = TwitterMonitorCollector(source_id=1, config={"account_id": "x"}, niche="unknown")
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result[0].content_type == "monitored_tweet"


# ── collect() — edge cases for new branching logic ───────────────────────────

class TestCollectEdgeCases:

    @pytest.mark.asyncio
    async def test_unparseable_date_lets_tweet_through(self):
        """A completely invalid created_at string should not drop the tweet."""
        tweet = _make_tweet_dict(text="Survive bad date", created_at="not-a-real-date")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        # Must pass through — the except branch sets pass
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_broken_extended_entities_does_not_raise(self):
        """If extended_entities is a non-dict type, image_url should silently stay empty."""
        tweet = _make_tweet_dict(text="Broken media")
        # Replace extended_entities with a scalar, triggering AttributeError in .get()
        tweet["legacy"]["extended_entities"] = "not-a-dict"
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1
        assert result[0].image_url == ""

    @pytest.mark.asyncio
    async def test_monitored_tweet_metadata_has_account_and_tweet_url(self):
        """Non-retweet items must still carry account and tweet_url in metadata."""
        tweet = _make_tweet_dict(tweet_id="77", text="Community update", screen_name="SomeGDPlayer")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(niche="geometrydash", retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        item = result[0]
        assert item.metadata["account"] == "SomeGDPlayer"
        assert "x.com/SomeGDPlayer/status/77" in item.metadata["tweet_url"]
        assert "retweet_id" not in item.metadata

    @pytest.mark.asyncio
    async def test_retweet_source_metadata_has_retweet_id_equal_to_tweet_id(self):
        """Retweet sources must set retweet_id to the tweet's own id_str."""
        tweet = _make_tweet_dict(tweet_id="88", text="Rocket League Season 14 patch notes!")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(niche="rocketleague", retweet=True)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result[0].metadata["retweet_id"] == "88"

    @pytest.mark.asyncio
    async def test_screen_name_falls_back_to_username_when_core_absent(self):
        """When core user_results is empty, screen_name falls back to self.username."""
        tweet = _make_tweet_dict(tweet_id="55", text="No core block")
        # Remove the core entirely
        tweet["core"] = {}
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(username="FallbackUser")
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result[0].author == "FallbackUser"


# ── _extract_tweets() ────────────────────────────────────────────────────────

class TestExtractTweets:

    def test_finds_nested_tweets(self):
        tweet = _make_tweet_dict(tweet_id="1", text="Hello")
        resp = _wrap_in_timeline([tweet])
        tweets = _extract_tweets(resp)
        assert len(tweets) == 1
        assert tweets[0]["legacy"]["id_str"] == "1"

    def test_deduplicates_by_id(self):
        tweet = _make_tweet_dict(tweet_id="1", text="Hello")
        # Same tweet nested twice
        resp = {"a": tweet, "b": tweet}
        tweets = _extract_tweets(resp)
        assert len(tweets) == 1

    def test_empty_response(self):
        assert _extract_tweets({}) == []
