"""
Unit tests for src/poster/client.py — TwitterClient

All tweepy calls are mocked. Tests cover both dry-run and live modes,
post/retweet success and failure paths, media upload, and rate limit status.
"""
import tempfile
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
import tweepy

from src.poster.client import TwitterClient


# ── Fixtures / helpers ────────────────────────────────────────────────────────

_FAKE_CREDS = {
    "api_key":             "key",
    "api_secret":          "secret",
    "access_token":        "token",
    "access_token_secret": "tsecret",
}

_NICHE_CREDS = {
    "rocketleague": _FAKE_CREDS,
    "geometrydash":  _FAKE_CREDS,
}


def _dry_run_client(niche: str = "rocketleague") -> TwitterClient:
    with (
        patch("src.poster.client.DRY_RUN", True),
        patch("src.poster.client.NICHE_CREDENTIALS", _NICHE_CREDS),
    ):
        return TwitterClient(niche)


def _live_client(niche: str = "rocketleague") -> TwitterClient:
    with (
        patch("src.poster.client.DRY_RUN", False),
        patch("src.poster.client.NICHE_CREDENTIALS", _NICHE_CREDS),
        patch("src.poster.client.tweepy.Client"),
        patch("src.poster.client.tweepy.OAuth1UserHandler"),
        patch("src.poster.client.tweepy.API"),
    ):
        return TwitterClient(niche)


# ── Dry-run mode ──────────────────────────────────────────────────────────────

class TestDryRunMode:

    def test_post_tweet_returns_fake_id(self):
        client = _dry_run_client()
        result = client.post_tweet("Hello world")
        assert result == "dry_run_id"

    def test_post_tweet_with_media_returns_fake_id(self):
        client = _dry_run_client()
        result = client.post_tweet("Hello world", media_path="/tmp/img.jpg")
        assert result == "dry_run_id"

    def test_retweet_returns_true(self):
        client = _dry_run_client()
        result = client.retweet("12345678")
        assert result is True

    def test_get_rate_limit_returns_empty_dict(self):
        client = _dry_run_client()
        result = client.get_rate_limit_status()
        assert result == {}

    def test_client_and_api_are_none_in_dry_run(self):
        client = _dry_run_client()
        assert client._client is None
        assert client._api is None


# ── Live mode — post_tweet() ──────────────────────────────────────────────────

class TestPostTweetLive:

    def test_returns_tweet_id_on_success(self):
        client = _live_client()
        mock_response = MagicMock()
        mock_response.data = {"id": "9876543210"}
        client._client = MagicMock()
        client._client.create_tweet.return_value = mock_response

        result = client.post_tweet("Great tweet!")

        assert result == "9876543210"
        client._client.create_tweet.assert_called_once_with(text="Great tweet!")

    def test_returns_none_on_tweepy_exception(self):
        client = _live_client()
        client._client = MagicMock()
        client._client.create_tweet.side_effect = tweepy.TweepyException("403 Forbidden")

        result = client.post_tweet("Will fail")

        assert result is None

    def test_includes_media_ids_when_upload_succeeds(self):
        client = _live_client()
        mock_response = MagicMock()
        mock_response.data = {"id": "111"}
        client._client = MagicMock()
        client._client.create_tweet.return_value = mock_response

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"fake image bytes")
            tmp_path = f.name

        try:
            mock_media = MagicMock()
            mock_media.media_id_string = "media_123"
            client._api = MagicMock()
            client._api.media_upload.return_value = mock_media

            result = client.post_tweet("Tweet with image", media_path=tmp_path)

            assert result == "111"
            client._client.create_tweet.assert_called_once_with(
                text="Tweet with image", media_ids=["media_123"]
            )
        finally:
            os.unlink(tmp_path)

    def test_posts_without_media_when_upload_returns_none(self):
        """If media upload fails, post text-only rather than abandoning."""
        client = _live_client()
        mock_response = MagicMock()
        mock_response.data = {"id": "222"}
        client._client = MagicMock()
        client._client.create_tweet.return_value = mock_response
        client._api = MagicMock()
        client._api.media_upload.side_effect = tweepy.TweepyException("upload failed")

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"fake")
            tmp_path = f.name

        try:
            result = client.post_tweet("Text only fallback", media_path=tmp_path)
            assert result == "222"
            # Called without media_ids
            client._client.create_tweet.assert_called_once_with(text="Text only fallback")
        finally:
            os.unlink(tmp_path)


# ── Live mode — retweet() ─────────────────────────────────────────────────────

class TestRetweetLive:

    def test_returns_true_on_success(self):
        client = _live_client()
        client._client = MagicMock()
        client._client.retweet.return_value = MagicMock()

        result = client.retweet("98765")

        assert result is True
        client._client.retweet.assert_called_once_with(tweet_id="98765", user_auth=True)

    def test_returns_false_on_tweepy_exception(self):
        client = _live_client()
        client._client = MagicMock()
        client._client.retweet.side_effect = tweepy.TweepyException("already retweeted")

        result = client.retweet("98765")

        assert result is False


# ── _upload_media() ───────────────────────────────────────────────────────────

class TestUploadMedia:

    def test_returns_none_for_missing_file(self):
        client = _live_client()
        client._api = MagicMock()

        result = client._upload_media("/nonexistent/path/img.jpg")

        assert result is None
        client._api.media_upload.assert_not_called()

    def test_retries_on_transient_error(self):
        """Should retry on 429/5xx errors."""
        client = _live_client()

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"img")
            tmp_path = f.name

        try:
            call_count = 0

            def _upload(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise tweepy.TweepyException("429 rate limited")
                media = MagicMock()
                media.media_id_string = "success_id"
                return media

            client._api = MagicMock()
            client._api.media_upload.side_effect = _upload

            with patch("time.sleep"):  # don't actually sleep
                result = client._upload_media(tmp_path, retries=2)

            assert result == ["success_id"]
        finally:
            os.unlink(tmp_path)

    def test_returns_none_after_exhausting_retries(self):
        """Non-transient error on final retry → return None."""
        client = _live_client()
        client._api = MagicMock()
        client._api.media_upload.side_effect = tweepy.TweepyException("bad request")

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"img")
            tmp_path = f.name

        try:
            with patch("time.sleep"):
                result = client._upload_media(tmp_path, retries=1)
            assert result is None
        finally:
            os.unlink(tmp_path)


# ── get_rate_limit_status() ───────────────────────────────────────────────────

class TestRateLimitStatus:

    def test_returns_dict_on_success(self):
        client = _live_client()
        client._api = MagicMock()
        client._api.rate_limit_status.return_value = {"resources": {"statuses": {}}}

        result = client.get_rate_limit_status()

        assert isinstance(result, dict)

    def test_returns_empty_dict_on_exception(self):
        client = _live_client()
        client._api = MagicMock()
        client._api.rate_limit_status.side_effect = tweepy.TweepyException("forbidden")

        result = client.get_rate_limit_status()

        assert result == {}
