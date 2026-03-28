"""
Unit tests for the relevance filter in src/collectors/twitter_monitor.py.

Tests cover keyword matching, niche isolation, and integration with collect().
"""
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.twitter_monitor import is_relevant, TwitterMonitorCollector


# ── is_relevant() unit tests ─────────────────────────────────────────────────

class TestIsRelevant:

    # ── Rocket League ─────────────────────────────────────────────────────────

    def test_rl_keyword_match(self):
        assert is_relevant("Rocket League Season 14 is here!", "rocketleague") is True

    def test_rl_hashtag_match(self):
        assert is_relevant("NRG take the series! #RLCS", "rocketleague") is True

    def test_rl_game_term_match(self):
        assert is_relevant("Insane flip reset into goal!", "rocketleague") is True

    def test_rl_esports_match(self):
        assert is_relevant("rl roster change: player joins team", "rocketleague") is True

    def test_rl_patch_match(self):
        assert is_relevant("RL patch v2.67 is live", "rocketleague") is True

    def test_rl_off_topic_rejected(self):
        assert is_relevant("Just had the best pizza ever", "rocketleague") is False

    def test_rl_meme_rejected(self):
        assert is_relevant("POV: when you realize it's Friday", "rocketleague") is False

    def test_rl_politics_rejected(self):
        assert is_relevant("The White House announced new policy", "rocketleague") is False

    def test_rl_case_insensitive(self):
        assert is_relevant("ROCKET LEAGUE UPDATE v2.67", "rocketleague") is True

    # ── Geometry Dash ─────────────────────────────────────────────────────────

    def test_gd_keyword_match(self):
        assert is_relevant("Geometry Dash 2.3 release date!", "geometrydash") is True

    def test_gd_demon_match(self):
        assert is_relevant("New extreme demon on the list!", "geometrydash") is True

    def test_gd_demonlist_match(self):
        assert is_relevant("Moved up on the demonlist", "geometrydash") is True

    def test_gd_creator_match(self):
        assert is_relevant("Wulzy uploaded a new video", "geometrydash") is True

    def test_gd_geode_match(self):
        assert is_relevant("Geode mod loader update released", "geometrydash") is True

    def test_gd_level_match(self):
        assert is_relevant("This GD level is insane!", "geometrydash") is True

    def test_gd_robtop_match(self):
        assert is_relevant("RobTop just tweeted something!", "geometrydash") is True

    def test_gd_hashtag_match(self):
        assert is_relevant("New creation! #GeometryDash", "geometrydash") is True

    def test_gd_off_topic_rejected(self):
        assert is_relevant("Just saw someone saying u shouldn't do that", "geometrydash") is False

    def test_gd_random_meme_rejected(self):
        assert is_relevant("why do incels fantasize about their future wife", "geometrydash") is False

    def test_gd_unrelated_game_rejected(self):
        assert is_relevant("Counter-Strike 2 is amazing this year", "geometrydash") is False

    def test_gd_case_insensitive(self):
        assert is_relevant("GEOMETRY DASH UPDATE", "geometrydash") is True

    # ── Unknown niche ─────────────────────────────────────────────────────────

    def test_unknown_niche_allows_everything(self):
        assert is_relevant("Random text about anything", "unknown_niche") is True

    def test_empty_text_rejected(self):
        assert is_relevant("", "rocketleague") is False

    # ── False positive rejection ─────────────────────────────────────────────

    def test_rl_competitive_not_false_positive(self):
        assert is_relevant("The competitive cooking scene is growing", "rocketleague") is False

    def test_rl_worlds_not_false_positive(self):
        assert is_relevant("The world's largest pizza was made today", "rocketleague") is False

    def test_rl_transfer_not_false_positive(self):
        assert is_relevant("Bank transfer failed this morning", "rocketleague") is False

    def test_rl_roster_not_false_positive(self):
        assert is_relevant("The roster of faculty at our school", "rocketleague") is False

    def test_gd_verified_not_false_positive(self):
        assert is_relevant("Get verified on X for free", "geometrydash") is False

    def test_gd_beaten_not_false_positive(self):
        assert is_relevant("I just got beaten by my brother at chess", "geometrydash") is False

    def test_gd_rated_not_false_positive(self):
        assert is_relevant("This restaurant is rated 5 stars", "geometrydash") is False

    def test_gd_featured_not_false_positive(self):
        assert is_relevant("Featured on BBC news today", "geometrydash") is False

    def test_gd_top10_not_false_positive(self):
        assert is_relevant("Top 10 foods to eat this summer", "geometrydash") is False

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_keyword_at_start(self):
        assert is_relevant("rlcs major starts tomorrow!", "rocketleague") is True

    def test_keyword_at_end(self):
        assert is_relevant("Tomorrow starts the rlcs", "rocketleague") is True

    def test_rl_partial_word_no_false_positive(self):
        # Tighter keywords avoid matching substrings of unrelated words
        assert is_relevant("This girl is funny", "rocketleague") is False

    def test_rl_explicit_keyword_match(self):
        assert is_relevant("rlcs is so exciting this season", "rocketleague") is True


# ── Integration with collect() ───────────────────────────────────────────────

def _make_tweet_dict(tweet_id="100", text="Some tweet", screen_name="TestUser"):
    dt = datetime.now(timezone.utc) - timedelta(hours=1)
    created_at = format_datetime(dt)
    return {
        "legacy": {
            "id_str": tweet_id,
            "full_text": text,
            "created_at": created_at,
            "entities": {"urls": [], "media": []},
        },
        "core": {
            "user_results": {
                "result": {
                    "legacy": {"screen_name": screen_name},
                }
            }
        },
    }


def _wrap_in_timeline(tweets):
    entries = []
    for t in tweets:
        entries.append({"content": {"itemContent": {"tweet_results": {"result": t}}}})
    return {"data": {"user": {"result": {"timeline_v2": {"timeline": {"instructions": [{"entries": entries}]}}}}}}


class TestRelevanceInCollect:

    @pytest.mark.asyncio
    async def test_retweet_source_skips_off_topic(self):
        """A retweet source should skip tweets that fail the relevance filter."""
        tweet = _make_tweet_dict(text="Just had amazing pizza for lunch")
        resp = _wrap_in_timeline([tweet])

        collector = TwitterMonitorCollector(
            source_id=1,
            config={"account_id": "SomeAccount", "retweet": True},
            niche="rocketleague",
        )

        mock_client = MagicMock()
        mock_client.gql_get = AsyncMock(return_value=resp)

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_client),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
        ):
            result = await collector.collect()

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_retweet_source_keeps_on_topic(self):
        """A retweet source should keep tweets that pass the relevance filter."""
        tweet = _make_tweet_dict(text="Rocket League Season 22 is live!")
        resp = _wrap_in_timeline([tweet])

        collector = TwitterMonitorCollector(
            source_id=1,
            config={"account_id": "RocketLeague", "retweet": True},
            niche="rocketleague",
        )

        mock_client = MagicMock()
        mock_client.gql_get = AsyncMock(return_value=resp)

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_client),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
        ):
            result = await collector.collect()

        assert len(result) == 1
        assert result[0].metadata["retweet_id"] == "100"

    @pytest.mark.asyncio
    async def test_non_retweet_source_bypasses_filter(self):
        """Monitored (non-retweet) sources should NOT be filtered — all tweets pass."""
        tweet = _make_tweet_dict(text="Completely random personal tweet")
        resp = _wrap_in_timeline([tweet])

        collector = TwitterMonitorCollector(
            source_id=1,
            config={"account_id": "SomePlayer", "retweet": False},
            niche="rocketleague",
        )

        mock_client = MagicMock()
        mock_client.gql_get = AsyncMock(return_value=resp)

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_client),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
        ):
            result = await collector.collect()

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_gd_retweet_source_filters_correctly(self):
        """GD retweet source keeps GD content, skips off-topic."""
        on_topic = _make_tweet_dict(tweet_id="1", text="Geometry Dash 2.3 coming soon!")
        off_topic = _make_tweet_dict(tweet_id="2", text="My cat is so cute today")
        resp = _wrap_in_timeline([on_topic, off_topic])

        collector = TwitterMonitorCollector(
            source_id=1,
            config={"account_id": "RobTopGames", "retweet": True},
            niche="geometrydash",
        )

        mock_client = MagicMock()
        mock_client.gql_get = AsyncMock(return_value=resp)

        with (
            patch("src.collectors.twitter_monitor.get_api", new_callable=AsyncMock, return_value=mock_client),
            patch("src.collectors.twitter_monitor.resolve_user_id", new_callable=AsyncMock, return_value=99),
        ):
            result = await collector.collect()

        assert len(result) == 1
        assert result[0].external_id == "1"
