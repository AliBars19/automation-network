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
    text: str = "Rocket League Season 14 is here with big new features and changes!",
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
        tweet = _make_tweet_dict(text="Rocket League Season 14 is here with new Arena and Ranked changes!")
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
        tweet = _make_tweet_dict(text="Rocket League v2.68 update coming with no confirmed date yet", created_at="")
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
        tweet = _make_tweet_dict(tweet_id="42", text="Rocket League Season 14 is now live!", screen_name="RocketLeague")
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
        tweet = _make_tweet_dict(tweet_id="42", text="Rocket League Season 14 is now live with new features!", screen_name="SomePlayer")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(username="SomePlayer", retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        item = result[0]
        assert item.content_type == "monitored_tweet"
        assert "retweet_id" not in item.metadata

    @pytest.mark.asyncio
    async def test_gd_retweet_content_type(self):
        tweet = _make_tweet_dict(text="Geometry Dash 2.3 update coming with new features!")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(niche="geometrydash", retweet=True)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result[0].content_type == "robtop_tweet"

    @pytest.mark.asyncio
    async def test_gd_non_retweet_content_type(self):
        tweet = _make_tweet_dict(text="Geometry Dash 2.3 is officially coming soon with new levels!")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(niche="geometrydash", retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result[0].content_type == "monitored_tweet"

    @pytest.mark.asyncio
    async def test_url_expansion(self):
        tweet = _make_tweet_dict(
            text="Rocket League Season 15 content update details https://t.co/abc123",
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
        tweet = _make_tweet_dict(text="Look at this incredible Rocket League play https://t.co/xyz999")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert "t.co" not in result[0].body

    @pytest.mark.asyncio
    async def test_image_from_extended_entities(self):
        tweet = _make_tweet_dict(text="Check out this amazing Rocket League gameplay screenshot and clip")
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
        tweets = [_make_tweet_dict(tweet_id=str(i), text=f"Major Rocket League update number {i} with lots of new content") for i in range(1, 6)]
        resp = _wrap_in_timeline(tweets)
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_unknown_niche_retweet_defaults_to_official_tweet(self):
        tweet = _make_tweet_dict(screen_name="TestAccount")
        resp = _wrap_in_timeline([tweet])
        collector = TwitterMonitorCollector(source_id=1, config={"account_id": "TestAccount", "retweet": True}, niche="unknown")
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result[0].content_type == "official_tweet"

    @pytest.mark.asyncio
    async def test_unknown_niche_no_retweet_gets_monitored_tweet(self):
        tweet = _make_tweet_dict(screen_name="TestAccount")
        resp = _wrap_in_timeline([tweet])
        collector = TwitterMonitorCollector(source_id=1, config={"account_id": "TestAccount"}, niche="unknown")
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result[0].content_type == "monitored_tweet"


# ── collect() — edge cases for new branching logic ───────────────────────────

class TestCollectEdgeCases:

    @pytest.mark.asyncio
    async def test_unparseable_date_lets_tweet_through(self):
        """A completely invalid created_at string should not drop the tweet."""
        tweet = _make_tweet_dict(text="Rocket League v2.68 Patch Notes released today — full changelog inside", created_at="not-a-real-date")
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
        tweet = _make_tweet_dict(text="Rocket League Season 14 trailer has broken media entities attached")
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
        tweet = _make_tweet_dict(tweet_id="77", text="Geode v3.0 mod loader update brings new API for Geometry Dash modding", screen_name="SomeGDPlayer")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(niche="geometrydash", username="SomeGDPlayer", retweet=False)
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
        tweet = _make_tweet_dict(tweet_id="55", text="Rocket League Season 14 has no core block in the response data")
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

    def test_retweeted_status_result_not_traversed(self):
        """Tweets nested under retweeted_status_result must not be extracted."""
        inner_tweet = _make_tweet_dict(tweet_id="999", text="Inner embedded tweet")
        outer_tweet = _make_tweet_dict(tweet_id="1", text="Outer tweet by timeline owner")
        # Attach inner tweet under the blocked key
        outer_tweet["retweeted_status_result"] = {"result": inner_tweet}
        resp = _wrap_in_timeline([outer_tweet])
        tweets = _extract_tweets(resp)
        # Only the outer tweet should be extracted; inner must be invisible
        ids = {t["legacy"]["id_str"] for t in tweets}
        assert "1" in ids
        assert "999" not in ids

    def test_quoted_status_result_not_traversed(self):
        """Tweets nested under quoted_status_result must not be extracted."""
        quoted_tweet = _make_tweet_dict(tweet_id="888", text="Quoted tweet from another account")
        outer_tweet = _make_tweet_dict(tweet_id="2", text="Outer tweet quoting someone")
        outer_tweet["quoted_status_result"] = {"result": quoted_tweet}
        resp = _wrap_in_timeline([outer_tweet])
        tweets = _extract_tweets(resp)
        ids = {t["legacy"]["id_str"] for t in tweets}
        assert "2" in ids
        assert "888" not in ids

    def test_both_embedded_keys_blocked_simultaneously(self):
        """Both retweeted_status_result and quoted_status_result are blocked."""
        retweet_inner = _make_tweet_dict(tweet_id="777", text="Retweeted inner")
        quote_inner = _make_tweet_dict(tweet_id="666", text="Quoted inner")
        outer_tweet = _make_tweet_dict(tweet_id="3", text="Outer with both embedded")
        outer_tweet["retweeted_status_result"] = {"result": retweet_inner}
        outer_tweet["quoted_status_result"] = {"result": quote_inner}
        resp = _wrap_in_timeline([outer_tweet])
        tweets = _extract_tweets(resp)
        ids = {t["legacy"]["id_str"] for t in tweets}
        assert "3" in ids
        assert "777" not in ids
        assert "666" not in ids

    def test_list_children_are_traversed(self):
        """Lists inside the response are still walked."""
        tweet = _make_tweet_dict(tweet_id="10", text="Inside a list")
        resp = {"items": [tweet]}
        tweets = _extract_tweets(resp)
        assert len(tweets) == 1
        assert tweets[0]["legacy"]["id_str"] == "10"

    def test_tweet_without_id_str_not_added(self):
        """A legacy dict without id_str is not included in results."""
        obj = {"legacy": {"full_text": "No id_str here"}}
        tweets = _extract_tweets(obj)
        assert tweets == []


# ── collect() — missing id_str guard (line 164) ──────────────────────────────
# _extract_tweets already filters empty id_str, so line 164 is only reachable
# if the function is mocked to return such an entry.  We mock _extract_tweets
# directly to simulate that scenario and confirm collect() skips the tweet.

class TestMissingTweetIdGuard:

    @pytest.mark.asyncio
    async def test_tweet_with_empty_id_str_skipped(self):
        """collect() must skip a tweet dict whose id_str is empty, even if
        _extract_tweets incorrectly returns it (guards line 164)."""
        tweet_with_no_id = {
            "legacy": {
                "id_str": "",
                "full_text": "Rocket League Season 14 is live with new ranked content!",
                "created_at": "",
                "entities": {"urls": []},
            },
            "core": {
                "user_results": {
                    "result": {"legacy": {"screen_name": "RocketLeague"}}
                }
            },
        }
        mock_client = MagicMock()
        mock_client.gql_get = AsyncMock(return_value={})
        collector = _make_collector(username="RocketLeague")
        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_client),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
            patch("src.collectors.twitter_monitor._extract_tweets", return_value=[tweet_with_no_id]),
        ):
            result = await collector.collect()
        assert result == []


# ── collect() — text-form RT skipping (line 177) ─────────────────────────────

class TestTextFormRTSkipping:

    @pytest.mark.asyncio
    async def test_text_form_rt_excluded(self):
        """Tweets starting with 'RT @' that were not caught by retweeted_status_result
        must still be excluded (line 177 branch)."""
        tweet = _make_tweet_dict(
            tweet_id="200",
            text="RT @SomeAccount: Rocket League Season 14 drops next week with new content",
        )
        # No retweeted_status_result key so it reaches the text check
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_text_not_starting_with_rt_at_passes(self):
        """A tweet whose text happens to contain 'RT @' in the middle is not blocked."""
        tweet = _make_tweet_dict(
            tweet_id="201",
            text="Big news: Rocket League Season 14 broke RT @record for concurrent players!",
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector()
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1


# ── collect() — minimum length filter (lines 188-193) ────────────────────────

class TestMinimumLengthFilter:

    @pytest.mark.asyncio
    async def test_monitored_source_short_tweet_excluded(self):
        """Monitored (non-retweet) sources require >= 30 chars after stripping emoji/URL."""
        # 29 chars of real text — just under the 30-char limit
        tweet = _make_tweet_dict(tweet_id="300", text="Short text under thirty chars.")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_monitored_source_emoji_only_excluded(self):
        """A tweet that is all emoji collapses to zero chars and must be excluded."""
        tweet = _make_tweet_dict(tweet_id="301", text="\U0001F680\U0001F525\U0001F3AE")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_retweet_source_allows_short_tweet_above_15_chars(self):
        """Retweet sources use a 15-char minimum, so a 16-char tweet must pass."""
        # Exactly 16 printable chars — passes the 15-char retweet bar but would
        # fail the 30-char monitored bar
        tweet = _make_tweet_dict(
            tweet_id="302",
            text="SEASON 14 LIVE!",  # 15 chars exactly — borderline, use 16
        )
        # "SEASON 14 LIVE!!" is 16 chars
        tweet["legacy"]["full_text"] = "SEASON 14 LIVE!!"
        resp = _wrap_in_timeline([tweet])
        # Retweet source in rocketleague niche; text must pass relevance too
        collector = _make_collector(retweet=True)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        # Must pass the length gate (16 >= 15) even though it's under 30
        # It may still fail relevance; we just check it is NOT dropped for length
        # by verifying "too short" is not the reason — test with a relevant keyword
        tweet["legacy"]["full_text"] = "RLCS Major live!"  # 16 chars, has "rlcs"
        resp2 = _wrap_in_timeline([tweet])
        p3, p4 = _patches(resp2)
        with p3, p4:
            result2 = await collector.collect()
        assert len(result2) == 1

    @pytest.mark.asyncio
    async def test_retweet_source_excludes_tweet_under_15_chars(self):
        """Retweet sources still drop tweets under 15 chars (lines 188-193)."""
        tweet = _make_tweet_dict(tweet_id="303", text="\U0001F525\U0001F525\U0001F525")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=True)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_url_stripped_before_length_check(self):
        """A tweet that is only a URL should collapse to empty and be excluded."""
        tweet = _make_tweet_dict(tweet_id="304", text="https://t.co/abc123")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []


# ── collect() — filler tweet patterns (lines 195-206) ────────────────────────

class TestFillerTweetPatterns:

    @pytest.mark.asyncio
    async def test_hmm_filler_excluded(self):
        """'hmm...' is a filler tweet and must be dropped."""
        tweet = _make_tweet_dict(tweet_id="400", text="hmm...")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_hmm_variant_excluded(self):
        """'hmmmmm' (repeated m's) also matches and must be dropped."""
        tweet = _make_tweet_dict(tweet_id="401", text="hmmmmm")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_bare_score_filler_excluded(self):
        """'3-0.' matches the bare score pattern and must be dropped."""
        tweet = _make_tweet_dict(tweet_id="402", text="3-0.")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_bare_score_without_period_excluded(self):
        """'7-0' (no period) also matches the bare score pattern."""
        tweet = _make_tweet_dict(tweet_id="403", text="7-0")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_aaaaah_filler_excluded(self):
        """'aaaaaaaah' matches the repeated-vowel filler pattern and must be dropped."""
        tweet = _make_tweet_dict(tweet_id="404", text="aaaaaaaah")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_oooh_filler_excluded(self):
        """'ooooh' matches the repeated-o filler pattern."""
        tweet = _make_tweet_dict(tweet_id="405", text="ooooh")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_lmao_filler_excluded(self):
        """'lmao' alone is a filler tweet."""
        tweet = _make_tweet_dict(tweet_id="406", text="lmao")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_filler_case_insensitive(self):
        """Filler patterns are case-insensitive — 'HMM' must also be caught."""
        tweet = _make_tweet_dict(tweet_id="407", text="HMM...")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_score_in_context_not_excluded(self):
        """A score inside a real sentence is not a bare score and must pass through."""
        tweet = _make_tweet_dict(
            tweet_id="408",
            text="Vitality beats Karmine Corp 3-0 to claim the RLCS Grand Finals!",
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_retweet_source_filler_also_excluded(self):
        """Filler tweets are dropped from retweet sources too (filter runs before
        the substance-check guard that only applies to monitored sources)."""
        tweet = _make_tweet_dict(tweet_id="409", text="3-0.")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=True)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    # -- Tests below use texts long enough to pass the length gate (>= 30 chars)
    # so that the filler regex is the actual reason for exclusion (lines 202-206).

    @pytest.mark.asyncio
    async def test_long_hmm_filler_reaches_filler_logger_lines(self):
        """A 32-char string of repeated 'hmm' passes the length gate but hits
        the filler regex, exercising the logger.debug block at lines 202-206."""
        # "hmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmm" = 32 chars, matches ^(hmm+)[.!?…\s]*$
        long_hmm = "h" + "m" * 31  # 32 chars total, starts with hmm...
        tweet = _make_tweet_dict(tweet_id="410", text=long_hmm)
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_long_bare_score_reaches_filler_logger_lines(self):
        """A bare score padded with spaces to 32 chars passes the length gate
        but is caught by the bare score filler pattern (lines 202-206)."""
        # "3-0." + 28 spaces = 32 chars, matches ^\d+-\d+\.?\s*$
        padded_score = "3-0." + " " * 28
        tweet = _make_tweet_dict(tweet_id="411", text=padded_score)
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_long_aaaaah_reaches_filler_logger_lines(self):
        """A 32-char 'aaaa...ah' passes the length gate but matches the
        repeated-vowel filler pattern (lines 202-206)."""
        # "a" * 31 + "h" = 32 chars, matches ^(a{3,}h)
        long_aaaaah = "a" * 31 + "h"
        tweet = _make_tweet_dict(tweet_id="412", text=long_aaaaah)
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []


# ── collect() — substance check (lines 218-223) ──────────────────────────────

class TestSubstanceCheck:

    @pytest.mark.asyncio
    async def test_tweet_with_proper_noun_passes(self):
        """A tweet with a proper noun satisfies the substance check."""
        tweet = _make_tweet_dict(
            tweet_id="500",
            text="Jstn just hit Grand Champion on the new ranked ladder",
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_tweet_with_number_passes(self):
        """A tweet containing a digit satisfies the substance check."""
        tweet = _make_tweet_dict(
            tweet_id="501",
            text="Rank 1 on the demon list achieved after 3 months grinding",
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_tweet_with_hashtag_passes(self):
        """A tweet containing a hashtag satisfies the substance check."""
        tweet = _make_tweet_dict(
            tweet_id="502",
            text="huge play happened today watch this #RLCS",
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_tweet_with_mention_passes(self):
        """A tweet containing an @mention satisfies the substance check."""
        tweet = _make_tweet_dict(
            tweet_id="503",
            text="crazy clip from @SomePlayer worth watching right now",
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_short_no_substance_tweet_excluded(self):
        """A tweet under 60 chars with no proper noun, number, hashtag, or mention
        is considered low-substance and must be dropped (lines 218-223)."""
        tweet = _make_tweet_dict(tweet_id="504", text="huge play happened today watch this")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_long_no_substance_tweet_passes(self):
        """A tweet >= 60 chars passes the substance check even without markers,
        because it is long enough to be considered substantive prose."""
        # 60+ chars, all lowercase, no numbers/hashtags/mentions/proper nouns
        long_text = "this is just a long tweet without any obvious substance markers in it whatsoever"
        tweet = _make_tweet_dict(tweet_id="505", text=long_text)
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_retweet_source_skips_substance_check(self):
        """Retweet sources bypass the substance check entirely — their short hype
        tweets must not be dropped by it."""
        # Short, no proper noun/number/hashtag/mention — would fail for monitored
        tweet = _make_tweet_dict(tweet_id="506", text="RLCS live now watch!")
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=True)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        # Retweet sources skip substance check so this reaches relevance gate
        # "rlcs" is in _RL_RELEVANCE so it passes relevance too
        assert len(result) == 1


# ── collect() — non-English lang field (lines 228-233) ───────────────────────

class TestNonEnglishLangField:

    @pytest.mark.asyncio
    async def test_french_lang_field_excluded(self):
        """A tweet with lang='fr' must be dropped (lines 229-233)."""
        tweet = _make_tweet_dict(
            tweet_id="600",
            text="Rocket League Season 14 est maintenant disponible avec de nouveaux contenus",
        )
        tweet["legacy"]["lang"] = "fr"
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_spanish_lang_field_excluded(self):
        """A tweet with lang='es' is non-English and must be dropped."""
        tweet = _make_tweet_dict(
            tweet_id="601",
            text="Rocket League Season 14 ya esta disponible con nuevas funciones increibles",
        )
        tweet["legacy"]["lang"] = "es"
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_english_lang_field_passes(self):
        """A tweet with lang='en' must pass the lang check."""
        tweet = _make_tweet_dict(
            tweet_id="602",
            text="Rocket League Season 14 is live now with major new ranked changes!",
        )
        tweet["legacy"]["lang"] = "en"
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_und_lang_field_passes(self):
        """lang='und' (undetermined) is whitelisted and must pass through."""
        tweet = _make_tweet_dict(
            tweet_id="603",
            text="Rocket League Season 14 is live now with major new ranked changes!",
        )
        tweet["legacy"]["lang"] = "und"
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_lang_field_passes(self):
        """An absent/empty lang field skips the lang check entirely."""
        tweet = _make_tweet_dict(
            tweet_id="604",
            text="Rocket League Season 14 is live now with major new ranked changes!",
        )
        # No lang key in legacy — falls through to French text detection
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_qme_lang_field_passes(self):
        """lang='qme' (Twitter's emoji-only) is whitelisted."""
        tweet = _make_tweet_dict(
            tweet_id="605",
            text="Rocket League Season 14 is here with new ranked season content!",
        )
        tweet["legacy"]["lang"] = "qme"
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1


# ── collect() — French text detection (lines 253-258) ────────────────────────

class TestFrenchTextDetection:

    @pytest.mark.asyncio
    async def test_french_words_score_reaches_threshold(self):
        """Two or more French word matches triggers the French filter (lines 253-258)."""
        tweet = _make_tweet_dict(
            tweet_id="700",
            text="Rocket League est dans les meilleures conditions pour cette saison",
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_mdr_abbreviation_scores_as_french(self):
        """'mdr' is a French internet abbreviation and must count toward the score."""
        tweet = _make_tweet_dict(
            tweet_id="701",
            # 'mdr' + 'les' = score 2 — should be caught
            text="c'est pas possible mdr les gens sont trop forts",
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_french_prefix_contributes_to_score(self):
        """French prefixes like 'c'est' and 'd'' contribute to the French score."""
        tweet = _make_tweet_dict(
            tweet_id="702",
            # c'est (prefix) + 'est' (word) — score >= 2
            text="c'est vraiment incroyable cette performance",
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_emoji_stripped_before_french_check(self):
        """Emojis in the tweet text do not prevent French word boundary detection."""
        tweet = _make_tweet_dict(
            tweet_id="703",
            # Emojis between French words — must still be detected
            text="\U0001F525 c'est magnifique \U0001F3AE les mecs font du beau travail",
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_one_french_word_below_threshold_passes(self):
        """A single French word (score=1) does not reach the threshold of 2
        and must not be dropped."""
        tweet = _make_tweet_dict(
            tweet_id="704",
            # 'les' is a French word but score stays at 1
            text="The Les Mills BODYCOMBAT pack is now in Rocket League Season 14!",
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_english_text_with_no_french_words_passes(self):
        """Purely English text with no French markers must pass the French check."""
        tweet = _make_tweet_dict(
            tweet_id="705",
            text="Geometry Dash 2.3 has officially been verified by the top players worldwide!",
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(niche="geometrydash", retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_french_tweet_with_english_lang_field_still_caught_by_text(self):
        """Even if Twitter reports lang='en', French text detection must still catch
        a tweet that scores >= 2 French markers."""
        tweet = _make_tweet_dict(
            tweet_id="706",
            text="c'est une victoire pour nous dans cette competition incroyable",
        )
        # Twitter incorrectly labelled this 'en' — text check must still catch it
        tweet["legacy"]["lang"] = "en"
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_ptdr_abbreviation_counts_as_french(self):
        """'ptdr' (French slang for 'lol') scores as a French word."""
        tweet = _make_tweet_dict(
            tweet_id="707",
            # ptdr (word) + c'est (prefix) = score >= 2
            text="c'est ptdr comment cette équipe joue",
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []


# ── collect() — author / screen_name validation (lines 278-283) ──────────────

class TestAuthorValidation:

    @pytest.mark.asyncio
    async def test_tweet_from_different_account_excluded(self):
        """A tweet in the timeline whose screen_name differs from the monitored
        account must be dropped (lines 278-283)."""
        tweet = _make_tweet_dict(
            tweet_id="800",
            text="Rocket League Season 14 is live now with new ranked content and maps!",
            screen_name="RocketBaguette",  # different account in the timeline
        )
        resp = _wrap_in_timeline([tweet])
        # Monitoring 'RocketLeague' but tweet is from 'RocketBaguette'
        collector = _make_collector(username="RocketLeague")
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_tweet_from_correct_account_passes(self):
        """A tweet whose screen_name matches the monitored account must pass."""
        tweet = _make_tweet_dict(
            tweet_id="801",
            text="Rocket League Season 14 is live now with new ranked content and maps!",
            screen_name="RocketLeague",
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(username="RocketLeague")
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_author_check_is_case_insensitive(self):
        """The screen_name comparison is case-insensitive — 'rocketleague' must
        match a collector configured for 'RocketLeague'."""
        tweet = _make_tweet_dict(
            tweet_id="802",
            text="Rocket League Season 14 is live now with new ranked content and maps!",
            screen_name="rocketleague",  # lowercase variant
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(username="RocketLeague")
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_multiple_tweets_only_matching_author_kept(self):
        """When the timeline mixes tweets from two accounts, only the monitored
        account's tweet must survive."""
        good_tweet = _make_tweet_dict(
            tweet_id="803",
            text="Rocket League Season 14 is live now with new ranked content and maps!",
            screen_name="RocketLeague",
        )
        bad_tweet = _make_tweet_dict(
            tweet_id="804",
            text="Geometry Dash 2.3 coming soon with a huge update for the community!",
            screen_name="RobtopGames",
        )
        resp = _wrap_in_timeline([good_tweet, bad_tweet])
        collector = _make_collector(username="RocketLeague")
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1
        assert result[0].external_id == "803"


# ── collect() — RT prefix stripping (lines 311-314) ──────────────────────────

class TestRTPrefixStripping:

    @pytest.mark.asyncio
    async def test_rt_prefix_stripped_from_clean_text(self):
        """If clean_text still starts with 'RT @username: ' after URL expansion,
        the prefix must be stripped before the item is emitted (lines 311-314)."""
        # Build a tweet where the expanded URL replaces the t.co but leaves an RT prefix.
        # We simulate this by putting "RT @SomeUser: " directly in the text with no
        # leading "RT @" (so it does not get dropped by the earlier RT filter) —
        # this requires placing the RT prefix after a leading character.
        # The simpler path: the text does NOT start with "RT @" (so the early filter
        # does not drop it), but after URL-expansion the clean_text ends up starting
        # with "RT @" — we model this by using a URL entity whose expanded_url causes
        # the swap to produce a clean_text that starts with "RT @".
        # In practice the code does: clean_text = text with URLs replaced.
        # So if text = "...\nRT @Someone: content", clean_text would not start with RT.
        # The only real way to hit line 311 is when clean_text.startswith("RT @") after
        # URL replacement — we construct that scenario by having text itself be
        # "placeholder" then using url entities to replace placeholder with "RT @Foo: "
        # That isn't how the code works; the code does string replace of short→expanded.
        # Realistic scenario: text was passed through already starting with "RT @" only
        # if the earlier check was bypassed (e.g. text starts with whitespace).
        # Easiest reliable approach: give the tweet a text that starts with " RT @"
        # (space prefix) so the earlier .startswith("RT @") check does NOT fire,
        # then after strip-then-replace in the url expansion block, clean_text ends
        # up as "RT @Someone: actual content".

        # Actually the cleanest approach — text itself starts with "RT @" but wrapped
        # in a URL so: text = "https://t.co/x RT @RL: some content" where after URL
        # replacement the result is "https://expanded RT @RL: some content" which does
        # NOT start with RT. Let's just test directly: set text to not start with RT @
        # at all, but construct clean_text to start with it by having expanded_url be
        # "RT @SomeUser: " replacing the t.co link at the start.
        tweet = _make_tweet_dict(
            tweet_id="900",
            text="https://t.co/fakelink Rocket League Season 14 major update is here",
            urls=[{
                "url": "https://t.co/fakelink",
                "expanded_url": "RT @SomeUser:",
            }],
        )
        # After expansion: "RT @SomeUser: Rocket League Season 14 major update is here"
        # This WILL be caught by the earlier startswith("RT @") filter, so we need
        # a different approach.  The RT-prefix strip at line 311 is only reachable when
        # clean_text starts with "RT @" AFTER URL expansion but the original `text`
        # did NOT start with "RT @".
        # Simplest: text has a non-RT prefix that expands to one.
        # Let's just accept that and confirm the branch fires on a direct input.
        # We patch _TRAILING_TCO_RE.sub to be identity and feed a tweet where the
        # text starts with a non-RT string that after url entity replacement becomes RT.

        # The most direct test: provide text that starts with "RT @" preceded only by
        # characters that get replaced by the URL expansion step.
        # text = "PLACEHOLDER more content about Rocket League Season 14 here now"
        # url entity: url="PLACEHOLDER", expanded_url="RT @SomeUser: "
        tweet2 = _make_tweet_dict(
            tweet_id="901",
            text="PLACEHOLDER more content about Rocket League Season 14 here now today!",
            urls=[{
                "url": "PLACEHOLDER",
                "expanded_url": "RT @SomeUser: ",
            }],
        )
        resp = _wrap_in_timeline([tweet2])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        # The prefix must be stripped — body should start with "more content"
        assert len(result) == 1
        assert result[0].body.startswith("more content")
        assert "RT @SomeUser" not in result[0].body

    @pytest.mark.asyncio
    async def test_clean_text_without_rt_prefix_unchanged(self):
        """When clean_text does not start with 'RT @', the stripping branch is
        not entered and the text is left intact."""
        tweet = _make_tweet_dict(
            tweet_id="902",
            text="Rocket League Season 14 has a brand new ranked ladder and content!",
        )
        resp = _wrap_in_timeline([tweet])
        collector = _make_collector(retweet=False)
        p1, p2 = _patches(resp)
        with p1, p2:
            result = await collector.collect()
        assert len(result) == 1
        assert result[0].body.startswith("Rocket League")
