"""
Tests for Fix 3 in src/collectors/youtube.py: the _SERIES_RE pattern and its
application inside collect() for both niches.

Fix 3 added:
  - Module-level _SERIES_RE pattern that matches ongoing series episode titles
  - A filter inside _fetch_videos() that skips any title matched by _SERIES_RE,
    placed AFTER the _is_short_or_low_quality check and BEFORE the _GD_OFF_TOPIC_RE check.

Test classes:
  1. TestSeriesRePattern              — regex correctness (should-match / should-not-match)
  2. TestYouTubeCollectorSeriesFilter — collect() filters series titles via _fetch_videos()
  3. TestSeriesFilterOrdering         — filter fires in correct position relative to others
  4. TestSeriesFilterBothNiches       — filter applies to geometrydash AND rocketleague
  5. TestFalsePositiveGuards          — titles that resemble series but must NOT be filtered
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.collectors.base import RawContent
from src.collectors.youtube import (
    YouTubeCollector,
    _SERIES_RE,
    _is_short_or_low_quality,
)

# ---------------------------------------------------------------------------
# Shared test helpers (mirrors test_youtube.py conventions)
# ---------------------------------------------------------------------------

_FAKE_API_KEY = "FAKE_API_KEY_FOR_SERIES_TESTS"
_FAKE_CHANNEL_ID = "UCseries0001"
_FAKE_PLAYLIST_ID = "UUseries0001"


def _make_collector(
    source_id: int = 1,
    channel_id: str = _FAKE_CHANNEL_ID,
    niche: str = "rocketleague",
) -> YouTubeCollector:
    return YouTubeCollector(
        source_id=source_id,
        config={"channel_id": channel_id},
        niche=niche,
    )


def _make_video_snippet(
    video_id: str = "vid_series_001",
    title: str = "Normal Title That Passes All Filters",
    description: str = "A regular video description.",
    channel_title: str = "Test Channel",
    thumbnail_url: str = "https://img.youtube.com/vi/vid/maxresdefault.jpg",
) -> dict:
    return {
        "snippet": {
            "resourceId": {"videoId": video_id},
            "title": title,
            "description": description,
            "channelTitle": channel_title,
            "thumbnails": {
                "maxres": {"url": thumbnail_url},
                "high": {"url": thumbnail_url},
            },
        }
    }


def _make_httpx_response(status: int, payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.headers = MagicMock()
    resp.headers.get = (
        lambda key, default="": "application/json" if key == "content-type" else default
    )
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _make_channel_response(playlist_id: str = _FAKE_PLAYLIST_ID) -> dict:
    return {
        "items": [
            {
                "contentDetails": {
                    "relatedPlaylists": {"uploads": playlist_id}
                }
            }
        ]
    }


def _make_client_with_responses(*responses) -> AsyncMock:
    client = AsyncMock()
    client.get = AsyncMock(side_effect=list(responses))
    return client


def _make_async_ctx(client: AsyncMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


async def _collect_with_title(
    title: str,
    niche: str = "rocketleague",
    video_id: str = "vid_test_001",
) -> list[RawContent]:
    """Run a full collect() with a single video of the given title."""
    collector = _make_collector(niche=niche)
    video = _make_video_snippet(video_id=video_id, title=title)
    channel_resp = _make_httpx_response(200, _make_channel_response())
    playlist_resp = _make_httpx_response(200, {"items": [video]})
    client = _make_client_with_responses(channel_resp, playlist_resp)
    ctx = _make_async_ctx(client)

    with (
        patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY),
        patch("src.collectors.youtube.httpx.AsyncClient", return_value=ctx),
        patch("src.collectors.youtube.cookies_available", return_value=False, create=True),
    ):
        return await collector.collect()


# ===========================================================================
# Class 1: TestSeriesRePattern — test _SERIES_RE directly
# ===========================================================================

class TestSeriesRePattern:
    """Direct regex tests — no mocking needed, just _SERIES_RE.search(title)."""

    # -----------------------------------------------------------------------
    # Titles that SHOULD match (series episodes must be filtered out)
    # -----------------------------------------------------------------------

    def test_matches_day_N_of_with_name(self):
        assert _SERIES_RE.search("Day 1 of Racing @MizuRL") is not None

    def test_matches_day_large_number_of(self):
        assert _SERIES_RE.search("Day 14 of playing ranked") is not None

    def test_matches_day_lowercase(self):
        assert _SERIES_RE.search("day 3 of grinding GD") is not None

    def test_matches_day_uppercase(self):
        assert _SERIES_RE.search("DAY 5 OF trying something") is not None

    def test_matches_day_mixed_case(self):
        assert _SERIES_RE.search("DaY 7 Of the grind") is not None

    def test_matches_episode_with_number(self):
        assert _SERIES_RE.search("Episode 5 of the series") is not None

    def test_matches_episode_large_number(self):
        assert _SERIES_RE.search("episode 23 highlights") is not None

    def test_matches_episode_uppercase(self):
        assert _SERIES_RE.search("EPISODE 1 – starting out") is not None

    def test_matches_ep_dot_space_number(self):
        assert _SERIES_RE.search("Ep. 7 of the journey") is not None

    def test_matches_ep_space_number(self):
        assert _SERIES_RE.search("Ep 12 ranked games") is not None

    def test_matches_ep_dot_no_space_number(self):
        assert _SERIES_RE.search("ep.3 speedrun") is not None

    def test_matches_ep_dot_uppercase(self):
        assert _SERIES_RE.search("EP. 5 highlights") is not None

    def test_matches_hash_number_pipe(self):
        assert _SERIES_RE.search("#14 | Rocket League ranked") is not None

    def test_matches_hash_number_colon(self):
        assert _SERIES_RE.search("#3: best plays") is not None

    def test_matches_hash_large_number_pipe(self):
        assert _SERIES_RE.search("#99 | GD demons") is not None

    def test_matches_hash_number_pipe_preceded_by_channel_name(self):
        assert _SERIES_RE.search("My Channel #14 | Best Clips") is not None

    def test_matches_hash_number_colon_space(self):
        assert _SERIES_RE.search("Road to GC #7: Diamond ranked") is not None

    def test_matches_ep_two_digit_number(self):
        assert _SERIES_RE.search("Ep 42 — the comeback") is not None

    def test_matches_episode_mid_sentence(self):
        assert _SERIES_RE.search("This is episode 100 of my series") is not None

    def test_matches_hash_number_space_pipe_space(self):
        assert _SERIES_RE.search("Season Grind #5 | making progress") is not None

    # -----------------------------------------------------------------------
    # Titles that SHOULD NOT match (legitimate videos, false positives)
    # -----------------------------------------------------------------------

    def test_no_match_daybreak(self):
        """'Day' not followed by space+digit — 'daybreak' must not fire."""
        assert _SERIES_RE.search("Daybreak in Rocket League") is None

    def test_no_match_today(self):
        """'Today' contains 'day' but is a different word."""
        assert _SERIES_RE.search("Today's best plays") is None

    def test_no_match_episode_without_number(self):
        assert _SERIES_RE.search("Episode of my life") is None

    def test_no_match_ep_dot_without_number(self):
        assert _SERIES_RE.search("Ep. of ranked games") is None

    def test_no_match_hash_shorts_tag(self):
        """'#Shorts' has # followed by letters, not digits."""
        assert _SERIES_RE.search("#Shorts | Geometry Dash") is None

    def test_no_match_day_word_not_digit(self):
        """'Day one' uses a word, not a digit."""
        assert _SERIES_RE.search("Day one of ranked") is None

    def test_no_match_generic_best_plays(self):
        assert _SERIES_RE.search("Best plays of 2024") is None

    def test_no_match_top_1_demon(self):
        assert _SERIES_RE.search("I beat a top 1 demon today") is None

    def test_no_match_episode_drops_soon(self):
        assert _SERIES_RE.search("New episode drops soon") is None

    def test_no_match_episode_is_broken(self):
        """'Episode' is present but no number immediately after."""
        assert _SERIES_RE.search("Episode is broken (bug)") is None

    def test_no_match_episodic(self):
        """'episodic' starts with 'episode' but is a different word."""
        assert _SERIES_RE.search("My episodic adventure") is None

    def test_no_match_ep_dot_is_bugged(self):
        """'Ep. is' — no digit immediately after ep."""
        assert _SERIES_RE.search("Ep. is bugged") is None

    def test_no_match_hash_number_no_separator(self):
        """'#1' not followed by ':' or '|' — standalone ordinal."""
        assert _SERIES_RE.search("#1 Demon of the Week") is None

    def test_no_match_day_N_without_of(self):
        """'Day 1 Recap' — digit present but no 'of' after it."""
        assert _SERIES_RE.search("Day 1 Recap") is None

    def test_no_match_day_N_highlights_no_of(self):
        """'Day 1 Highlights' — tournament day recap, not an episode."""
        assert _SERIES_RE.search("RLCS Spring Major Day 1 Highlights") is None


# ===========================================================================
# Class 2: TestYouTubeCollectorSeriesFilter — integration via collect()
# ===========================================================================

class TestYouTubeCollectorSeriesFilter:
    """
    Tests that _fetch_videos() correctly filters series titles in the full
    collect() pipeline. Uses mocked HTTP responses to avoid network calls.
    """

    @pytest.mark.asyncio
    async def test_series_day_N_of_filtered_out(self):
        result = await _collect_with_title("Day 14 of Racing @MizuRL")
        assert result == []

    @pytest.mark.asyncio
    async def test_series_episode_N_filtered_out(self):
        result = await _collect_with_title("Episode 23 ranked games")
        assert result == []

    @pytest.mark.asyncio
    async def test_series_ep_dot_N_filtered_out(self):
        result = await _collect_with_title("Ep. 7 of the journey")
        assert result == []

    @pytest.mark.asyncio
    async def test_series_ep_N_no_dot_filtered_out(self):
        result = await _collect_with_title("Ep 12 ranked games")
        assert result == []

    @pytest.mark.asyncio
    async def test_series_hash_N_pipe_filtered_out(self):
        result = await _collect_with_title("#14 | Rocket League ranked")
        assert result == []

    @pytest.mark.asyncio
    async def test_series_hash_N_colon_filtered_out(self):
        result = await _collect_with_title("#3: best plays")
        assert result == []

    @pytest.mark.asyncio
    async def test_non_series_title_passes_through(self):
        result = await _collect_with_title("RLCS Major Grand Finals Highlights")
        assert len(result) == 1
        assert result[0].title == "RLCS Major Grand Finals Highlights"

    @pytest.mark.asyncio
    async def test_non_series_gd_title_passes_through(self):
        result = await _collect_with_title(
            "New Extreme Demon Verified — Abyss of Darkness",
            niche="geometrydash",
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_series_title_in_mixed_batch_only_filters_series(self):
        """One series, one normal video in same playlist → only the normal one returned."""
        collector = _make_collector(niche="rocketleague")
        series_video = _make_video_snippet(
            video_id="series_vid",
            title="Road to SSL #22 | Diamond 3 games",
        )
        normal_video = _make_video_snippet(
            video_id="normal_vid",
            title="RLCS Season 15 Grand Finals Recap",
        )
        channel_resp = _make_httpx_response(200, _make_channel_response())
        playlist_resp = _make_httpx_response(
            200, {"items": [series_video, normal_video]}
        )
        client = _make_client_with_responses(channel_resp, playlist_resp)
        ctx = _make_async_ctx(client)

        with (
            patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY),
            patch("src.collectors.youtube.httpx.AsyncClient", return_value=ctx),
        ):
            result = await collector.collect()

        assert len(result) == 1
        assert result[0].external_id == "normal_vid"

    @pytest.mark.asyncio
    async def test_all_series_in_batch_returns_empty(self):
        """All five videos are series episodes → empty list returned."""
        collector = _make_collector(niche="rocketleague")
        series_titles = [
            "Day 1 of grinding ranked",
            "Episode 2 — the journey begins",
            "Ep. 3 more ranked games",
            "#4 | road to grand champ",
            "Day 5 of playing ranked",
        ]
        items = [
            _make_video_snippet(video_id=f"s{i}", title=t)
            for i, t in enumerate(series_titles)
        ]
        channel_resp = _make_httpx_response(200, _make_channel_response())
        playlist_resp = _make_httpx_response(200, {"items": items})
        client = _make_client_with_responses(channel_resp, playlist_resp)
        ctx = _make_async_ctx(client)

        with (
            patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY),
            patch("src.collectors.youtube.httpx.AsyncClient", return_value=ctx),
        ):
            result = await collector.collect()

        assert result == []


# ===========================================================================
# Class 3: TestSeriesFilterOrdering — relative order vs other filters
# ===========================================================================

class TestSeriesFilterOrdering:
    """
    The filter chain inside _fetch_videos() is:
      1. _is_short_or_low_quality  (returns early → RawContent never created)
      2. _SERIES_RE                (new Fix 3 check)
      3. _GD_OFF_TOPIC_RE          (only for geometrydash niche)

    Ordering tests verify that a title caught by an earlier filter is NOT
    accidentally allowed through, and that the right filter is responsible.
    """

    @pytest.mark.asyncio
    async def test_short_and_series_short_filter_fires_first(self):
        """
        A title with #Shorts (caught by filter 1) AND a series pattern (filter 2).
        Both filters would reject it — the result is still empty regardless of order,
        but we confirm the video is definitely not collected.
        """
        # "#3: #Shorts best ranked games" matches both _SHORTS_RE and _SERIES_RE
        result = await _collect_with_title("#3: #Shorts best ranked games")
        assert result == []

    @pytest.mark.asyncio
    async def test_series_fires_before_gd_off_topic(self):
        """
        A title that is BOTH a series episode AND contains a GD off-topic word.
        The series filter (check 2) runs before the GD off-topic filter (check 3),
        so the video is still rejected — the result must be empty.
        """
        result = await _collect_with_title(
            "Day 3 of making Geometry Dash merch",
            niche="geometrydash",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_off_topic_gd_without_series_still_filtered(self):
        """
        A GD off-topic title that is NOT a series → caught by filter 3 (GD off-topic).
        """
        result = await _collect_with_title(
            "My New Merch Drop is Here",
            niche="geometrydash",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_valid_gd_non_series_reaches_rawcontent(self):
        """
        A clean GD title passes all three filters and produces a RawContent.
        """
        result = await _collect_with_title(
            "Geometry Dash — I Verified the Hardest Extreme Demon",
            niche="geometrydash",
        )
        assert len(result) == 1
        assert isinstance(result[0], RawContent)

    @pytest.mark.asyncio
    async def test_valid_rl_non_series_reaches_rawcontent(self):
        """
        A clean RL title passes all filters and produces a RawContent.
        """
        result = await _collect_with_title(
            "RLCS World Championship Finals — Best Moments",
            niche="rocketleague",
        )
        assert len(result) == 1
        assert isinstance(result[0], RawContent)

    def test_is_short_or_low_quality_not_affected_by_series_re(self):
        """
        _is_short_or_low_quality is an independent helper and must not be changed
        by the series fix — its logic remains the same.
        """
        # Short title → low quality
        assert _is_short_or_low_quality("wow", "") is True
        # Normal title → not low quality
        assert _is_short_or_low_quality("RLCS Grand Finals Recap", "") is False
        # Shorts tag → low quality
        assert _is_short_or_low_quality("insane save #shorts", "") is True


# ===========================================================================
# Class 4: TestSeriesFilterBothNiches — filter covers both niches
# ===========================================================================

class TestSeriesFilterBothNiches:
    """
    The series filter runs unconditionally before the niche-specific GD check.
    It must reject series episodes for BOTH geometrydash and rocketleague.
    """

    @pytest.mark.asyncio
    async def test_day_N_of_filtered_for_geometrydash(self):
        result = await _collect_with_title(
            "Day 3 of grinding GD levels",
            niche="geometrydash",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_day_N_of_filtered_for_rocketleague(self):
        result = await _collect_with_title(
            "Day 3 of grinding Rocket League ranked",
            niche="rocketleague",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_episode_N_filtered_for_geometrydash(self):
        result = await _collect_with_title(
            "Episode 10 of my GD journey",
            niche="geometrydash",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_episode_N_filtered_for_rocketleague(self):
        result = await _collect_with_title(
            "Episode 10 of my Rocket League journey",
            niche="rocketleague",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_hash_N_pipe_filtered_for_geometrydash(self):
        result = await _collect_with_title(
            "#7 | road to extreme demon",
            niche="geometrydash",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_hash_N_pipe_filtered_for_rocketleague(self):
        result = await _collect_with_title(
            "#7 | road to grand champion",
            niche="rocketleague",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_non_series_geometrydash_not_filtered(self):
        result = await _collect_with_title(
            "Extreme Demon Verified After 50000 Attempts",
            niche="geometrydash",
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_non_series_rocketleague_not_filtered(self):
        result = await _collect_with_title(
            "Insane Flip Reset Musty That Won the Match",
            niche="rocketleague",
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_ep_dot_N_filtered_for_geometrydash(self):
        result = await _collect_with_title(
            "Ep. 2 practising GD mechanics",
            niche="geometrydash",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_ep_N_filtered_for_rocketleague(self):
        result = await _collect_with_title(
            "Ep 5 flying through platinum",
            niche="rocketleague",
        )
        assert result == []


# ===========================================================================
# Class 5: TestFalsePositiveGuards — near-miss titles that must NOT be filtered
# ===========================================================================

class TestFalsePositiveGuards:
    """
    Titles that superficially resemble series episodes but must pass through the
    filter unchanged. Regressions here would silently drop legitimate content.
    """

    def test_nine_circles_level_name_no_match(self):
        """Classic GD level name — no 'day N of', 'episode N', 'ep N' or '#N:'."""
        assert _SERIES_RE.search("Nine Circles — New Extreme Demon Verified") is None

    def test_cataclysm_level_name_no_match(self):
        assert _SERIES_RE.search("Cataclysm Verified by Trick") is None

    def test_rlcs_day_1_highlights_no_match(self):
        """
        Tournament recap format: 'Day 1 Highlights' — no 'of' after the digit,
        so the 'day N of' branch does NOT fire.
        """
        assert _SERIES_RE.search("RLCS Spring Major Day 1 Highlights") is None

    def test_rlcs_day_1_recap_no_match(self):
        assert _SERIES_RE.search("Day 1 Recap — RLCS Major") is None

    def test_hash_1_demon_of_week_no_match(self):
        """'#1' not followed immediately by ':' or '|'."""
        assert _SERIES_RE.search("#1 Demon of the Week") is None

    def test_top_1_verified_no_match(self):
        assert _SERIES_RE.search("Top 1 verified — Slaughterhouse") is None

    def test_episode_of_the_year_award_no_match(self):
        """'Episode' present but no digit immediately following it."""
        assert _SERIES_RE.search("Episode of the Year Award") is None

    def test_hash_number_no_separator_no_match(self):
        """'#10' at start of title with no ':' or '|' immediately after."""
        assert _SERIES_RE.search("#10 most insane plays ever") is None

    def test_level_number_in_parentheses_no_match(self):
        assert _SERIES_RE.search("I Verified Level #420 and It Was Hard") is None

    @pytest.mark.asyncio
    async def test_rlcs_day_1_highlights_passes_collect(self):
        """Full pipeline: tournament day recap must NOT be rejected by series filter."""
        result = await _collect_with_title("RLCS Spring Major Day 1 Highlights")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_nine_circles_passes_collect(self):
        result = await _collect_with_title(
            "Nine Circles Verified — World Record",
            niche="geometrydash",
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_top_1_demon_passes_collect(self):
        result = await _collect_with_title(
            "I beat the top 1 demon today",
            niche="geometrydash",
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_hash_shorts_tag_not_treated_as_series_number(self):
        """
        '#Shorts' contains '#' but not '#<digit>', so _SERIES_RE does not fire.
        However, _is_short_or_low_quality WILL fire — the video is still rejected,
        but for the right reason.
        """
        result = await _collect_with_title("Insane Save #Shorts")
        # Filtered by Shorts check, not by series check — still empty
        assert result == []

    def test_episode_preceded_by_adjective_no_match(self):
        """'episode' embedded in a different context without a digit."""
        assert _SERIES_RE.search("This episode blew my mind") is None

    def test_day_without_digit_no_match(self):
        assert _SERIES_RE.search("Day of the grind begins") is None
