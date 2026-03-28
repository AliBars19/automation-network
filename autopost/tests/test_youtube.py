"""
Unit tests for src/collectors/youtube.py — YouTubeCollector and helpers.

All HTTP calls are mocked — no network access.
YOUTUBE_API_KEY is patched to a fake value for tests that require it.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.collectors.base import RawContent
from src.collectors.youtube import YouTubeCollector, _MAX_RESULTS


# ===========================================================================
# Helpers
# ===========================================================================

_FAKE_API_KEY = "FAKE_API_KEY_FOR_TESTS"
_FAKE_CHANNEL_ID = "UCxyz1234567890"
_FAKE_PLAYLIST_ID = "UUxyz1234567890"


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


def _make_channel_response(playlist_id: str = _FAKE_PLAYLIST_ID) -> dict:
    return {
        "items": [
            {
                "contentDetails": {
                    "relatedPlaylists": {
                        "uploads": playlist_id,
                    }
                }
            }
        ]
    }


def _make_video_snippet(
    video_id: str = "vid_abc123",
    title: str = "Rocket League Season 15 Trailer",
    description: str = "Watch the new season trailer.",
    channel_title: str = "Rocket League",
    thumbnail_url: str = "https://img.youtube.com/vi/vid_abc123/maxresdefault.jpg",
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


def _make_playlist_response(video_items: list) -> dict:
    return {"items": video_items}


def _make_httpx_response(status: int, payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.headers = MagicMock()
    resp.headers.get = lambda key, default="": "application/json" if key == "content-type" else default
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _make_client_with_responses(*responses) -> AsyncMock:
    """Build a mock async HTTP client that returns responses in order."""
    client = AsyncMock()
    client.get = AsyncMock(side_effect=list(responses))
    return client


def _make_async_ctx(client: AsyncMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ===========================================================================
# YouTubeCollector.collect() — API key checks
# ===========================================================================

class TestCollectApiKeyCheck:

    @pytest.mark.asyncio
    async def test_returns_empty_when_api_key_not_set(self):
        collector = _make_collector()
        with patch("src.collectors.youtube.YOUTUBE_API_KEY", None):
            result = await collector.collect()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_api_key_is_empty_string(self):
        collector = _make_collector()
        with patch("src.collectors.youtube.YOUTUBE_API_KEY", ""):
            result = await collector.collect()
        assert result == []


# ===========================================================================
# _resolve_uploads_playlist()
# ===========================================================================

class TestResolveUploadsPlaylist:

    @pytest.mark.asyncio
    async def test_returns_playlist_id_on_success(self):
        collector = _make_collector()
        resp = _make_httpx_response(200, _make_channel_response(_FAKE_PLAYLIST_ID))
        client = _make_client_with_responses(resp)

        with patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY):
            playlist_id = await collector._resolve_uploads_playlist(client)

        assert playlist_id == _FAKE_PLAYLIST_ID

    @pytest.mark.asyncio
    async def test_caches_playlist_id_after_first_call(self):
        collector = _make_collector()
        resp = _make_httpx_response(200, _make_channel_response(_FAKE_PLAYLIST_ID))
        client = _make_client_with_responses(resp)

        with patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY):
            first = await collector._resolve_uploads_playlist(client)
            # Second call should return cached value, not call client.get again
            second = await collector._resolve_uploads_playlist(client)

        assert first == second == _FAKE_PLAYLIST_ID
        assert client.get.call_count == 1  # only one HTTP call made

    @pytest.mark.asyncio
    async def test_returns_none_when_channel_not_found(self):
        collector = _make_collector()
        resp = _make_httpx_response(200, {"items": []})
        client = _make_client_with_responses(resp)

        with patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY):
            result = await collector._resolve_uploads_playlist(client)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self):
        collector = _make_collector()
        resp = _make_httpx_response(500, {})
        client = _make_client_with_responses(resp)

        with patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY):
            result = await collector._resolve_uploads_playlist(client)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        collector = _make_collector()
        client = AsyncMock()
        client.get = AsyncMock(side_effect=Exception("network failure"))

        with patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY):
            result = await collector._resolve_uploads_playlist(client)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_quota_exceeded_403(self):
        collector = _make_collector()
        resp = MagicMock()
        resp.status_code = 403
        resp.headers.get = lambda k, d="": "application/json" if k == "content-type" else d
        resp.json.return_value = {
            "error": {"errors": [{"reason": "quotaExceeded"}]}
        }
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403", request=MagicMock(), response=resp
        )
        client = _make_client_with_responses(resp)

        with patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY):
            result = await collector._resolve_uploads_playlist(client)

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_uploads_key_returns_empty_string(self):
        """Channel found but relatedPlaylists has no 'uploads' key."""
        collector = _make_collector()
        resp = _make_httpx_response(200, {
            "items": [{"contentDetails": {"relatedPlaylists": {}}}]
        })
        client = _make_client_with_responses(resp)

        with patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY):
            result = await collector._resolve_uploads_playlist(client)

        # Empty string is falsy — collect() will return early
        assert not result


# ===========================================================================
# _fetch_videos()
# ===========================================================================

class TestFetchVideos:

    @pytest.mark.asyncio
    async def test_happy_path_returns_rawcontent_list(self):
        collector = _make_collector()
        video = _make_video_snippet()
        resp = _make_httpx_response(200, _make_playlist_response([video]))
        client = _make_client_with_responses(resp)

        with patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY):
            result = await collector._fetch_videos(client, _FAKE_PLAYLIST_ID)

        assert len(result) == 1
        assert isinstance(result[0], RawContent)

    @pytest.mark.asyncio
    async def test_skips_items_without_video_id(self):
        collector = _make_collector()
        # snippet with no videoId
        bad_item = {"snippet": {"resourceId": {}, "title": "No ID", "description": "", "channelTitle": "", "thumbnails": {}}}
        resp = _make_httpx_response(200, {"items": [bad_item]})
        client = _make_client_with_responses(resp)

        with patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY):
            result = await collector._fetch_videos(client, _FAKE_PLAYLIST_ID)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        collector = _make_collector()
        resp = _make_httpx_response(403, {})
        client = _make_client_with_responses(resp)

        with patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY):
            result = await collector._fetch_videos(client, _FAKE_PLAYLIST_ID)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        collector = _make_collector()
        client = AsyncMock()
        client.get = AsyncMock(side_effect=Exception("timeout"))

        with patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY):
            result = await collector._fetch_videos(client, _FAKE_PLAYLIST_ID)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_items(self):
        collector = _make_collector()
        resp = _make_httpx_response(200, {"items": []})
        client = _make_client_with_responses(resp)

        with patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY):
            result = await collector._fetch_videos(client, _FAKE_PLAYLIST_ID)

        assert result == []


# ===========================================================================
# RawContent field correctness
# ===========================================================================

class TestRawContentFields:

    @pytest.mark.asyncio
    async def _collect_one_video(self, video_snippet: dict, niche: str = "rocketleague") -> RawContent:
        collector = _make_collector(niche=niche)
        channel_resp = _make_httpx_response(200, _make_channel_response())
        playlist_resp = _make_httpx_response(200, _make_playlist_response([video_snippet]))
        client = _make_client_with_responses(channel_resp, playlist_resp)
        ctx = _make_async_ctx(client)

        with (
            patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY),
            patch("src.collectors.youtube.httpx.AsyncClient", return_value=ctx),
        ):
            result = await collector.collect()

        assert len(result) == 1
        return result[0]

    @pytest.mark.asyncio
    async def test_external_id_is_video_id(self):
        video = _make_video_snippet(video_id="dQw4w9WgXcQ")
        item = await self._collect_one_video(video)
        assert item.external_id == "dQw4w9WgXcQ"

    @pytest.mark.asyncio
    async def test_content_type_is_youtube_video(self):
        video = _make_video_snippet()
        item = await self._collect_one_video(video)
        assert item.content_type == "youtube_video"

    @pytest.mark.asyncio
    async def test_url_is_youtu_be_link(self):
        video = _make_video_snippet(video_id="abc123")
        item = await self._collect_one_video(video)
        assert item.url == "https://youtu.be/abc123"

    @pytest.mark.asyncio
    async def test_title_propagated(self):
        video = _make_video_snippet(title="My Awesome Video Title")
        item = await self._collect_one_video(video)
        assert item.title == "My Awesome Video Title"

    @pytest.mark.asyncio
    async def test_description_truncated_to_300_chars(self):
        long_desc = "A" * 500
        video = _make_video_snippet(description=long_desc)
        item = await self._collect_one_video(video)
        assert len(item.body) <= 300

    @pytest.mark.asyncio
    async def test_author_is_channel_title(self):
        video = _make_video_snippet(channel_title="Rocket League")
        item = await self._collect_one_video(video)
        assert item.author == "Rocket League"

    @pytest.mark.asyncio
    async def test_image_url_is_maxres_thumbnail(self):
        video = _make_video_snippet(thumbnail_url="https://img.youtube.com/maxres.jpg")
        item = await self._collect_one_video(video)
        assert item.image_url == "https://img.youtube.com/maxres.jpg"

    @pytest.mark.asyncio
    async def test_niche_propagated(self):
        video = _make_video_snippet()
        item = await self._collect_one_video(video, niche="geometrydash")
        assert item.niche == "geometrydash"

    @pytest.mark.asyncio
    async def test_score_is_zero(self):
        video = _make_video_snippet()
        item = await self._collect_one_video(video)
        assert item.score == 0

    @pytest.mark.asyncio
    async def test_metadata_contains_creator_title_url(self):
        video = _make_video_snippet(video_id="xyz", title="Best Plays of RLCS Season 15", channel_title="RLCS")
        item = await self._collect_one_video(video)
        assert item.metadata["creator"] == "RLCS"
        assert item.metadata["title"] == "Best Plays of RLCS Season 15"
        assert item.metadata["url"] == "https://youtu.be/xyz"

    @pytest.mark.asyncio
    async def test_source_id_propagated(self):
        collector = YouTubeCollector(
            source_id=55,
            config={"channel_id": _FAKE_CHANNEL_ID},
            niche="rocketleague",
        )
        video = _make_video_snippet()
        channel_resp = _make_httpx_response(200, _make_channel_response())
        playlist_resp = _make_httpx_response(200, _make_playlist_response([video]))
        client = _make_client_with_responses(channel_resp, playlist_resp)
        ctx = _make_async_ctx(client)

        with (
            patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY),
            patch("src.collectors.youtube.httpx.AsyncClient", return_value=ctx),
        ):
            result = await collector.collect()

        assert result[0].source_id == 55


# ===========================================================================
# Full collect() integration (playlist resolution + video fetch)
# ===========================================================================

class TestCollectIntegration:

    @pytest.mark.asyncio
    async def test_returns_empty_when_playlist_not_found(self):
        collector = _make_collector()
        channel_resp = _make_httpx_response(200, {"items": []})
        client = _make_client_with_responses(channel_resp)
        ctx = _make_async_ctx(client)

        with (
            patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY),
            patch("src.collectors.youtube.httpx.AsyncClient", return_value=ctx),
        ):
            result = await collector.collect()

        assert result == []

    @pytest.mark.asyncio
    async def test_multiple_videos_all_returned(self):
        collector = _make_collector()
        videos = [_make_video_snippet(video_id=f"vid_{i}", title=f"Full Length Video Number {i}") for i in range(3)]
        channel_resp = _make_httpx_response(200, _make_channel_response())
        playlist_resp = _make_httpx_response(200, _make_playlist_response(videos))
        client = _make_client_with_responses(channel_resp, playlist_resp)
        ctx = _make_async_ctx(client)

        with (
            patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY),
            patch("src.collectors.youtube.httpx.AsyncClient", return_value=ctx),
        ):
            result = await collector.collect()

        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_cached_playlist_avoids_second_channel_lookup(self):
        """On second collect(), playlist ID should be cached — only one channels call total."""
        collector = _make_collector()
        video = _make_video_snippet()

        channel_resp = _make_httpx_response(200, _make_channel_response())
        playlist_resp_1 = _make_httpx_response(200, _make_playlist_response([video]))
        playlist_resp_2 = _make_httpx_response(200, _make_playlist_response([video]))
        client = _make_client_with_responses(channel_resp, playlist_resp_1, playlist_resp_2)
        ctx = _make_async_ctx(client)

        with (
            patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY),
            patch("src.collectors.youtube.httpx.AsyncClient", return_value=ctx),
        ):
            await collector.collect()
            await collector.collect()

        # channels endpoint called only once (playlist is cached after first call)
        get_calls = client.get.call_args_list
        channels_calls = [c for c in get_calls if "/channels" in str(c)]
        assert len(channels_calls) == 1

    @pytest.mark.asyncio
    async def test_thumbnail_falls_back_to_high_when_maxres_missing(self):
        collector = _make_collector()
        video = {
            "snippet": {
                "resourceId": {"videoId": "fallback_thumb"},
                "title": "Fallback Thumbnail Test",
                "description": "",
                "channelTitle": "Test",
                "thumbnails": {
                    "high": {"url": "https://img.youtube.com/high.jpg"},
                    # maxres intentionally absent
                },
            }
        }
        channel_resp = _make_httpx_response(200, _make_channel_response())
        playlist_resp = _make_httpx_response(200, _make_playlist_response([video]))
        client = _make_client_with_responses(channel_resp, playlist_resp)
        ctx = _make_async_ctx(client)

        with (
            patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY),
            patch("src.collectors.youtube.httpx.AsyncClient", return_value=ctx),
        ):
            result = await collector.collect()

        assert result[0].image_url == "https://img.youtube.com/high.jpg"

    @pytest.mark.asyncio
    async def test_max_results_parameter_respected(self):
        """Verify the API call is made with maxResults = _MAX_RESULTS."""
        collector = _make_collector()
        video = _make_video_snippet()

        channel_resp = _make_httpx_response(200, _make_channel_response())
        playlist_resp = _make_httpx_response(200, _make_playlist_response([video]))
        client = _make_client_with_responses(channel_resp, playlist_resp)
        ctx = _make_async_ctx(client)

        with (
            patch("src.collectors.youtube.YOUTUBE_API_KEY", _FAKE_API_KEY),
            patch("src.collectors.youtube.httpx.AsyncClient", return_value=ctx),
        ):
            await collector.collect()

        # Find the playlistItems call and check maxResults param
        playlist_call = client.get.call_args_list[1]
        params = playlist_call[1].get("params", playlist_call[0][1] if len(playlist_call[0]) > 1 else {})
        assert params.get("maxResults") == _MAX_RESULTS
