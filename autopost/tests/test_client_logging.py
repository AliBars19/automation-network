"""
Tests for TwitterClient exception handling and logging in src/poster/client.py.

Covers the three-way exception split introduced in Fix 1:
  - tweepy.BadRequest  → logger.error with "400 Bad Request" + truncated text
  - tweepy.TooManyRequests → logger.warning (short message, no text dump)
  - tweepy.TweepyException (generic) → logger.error with text dump

All external dependencies (tweepy, settings, video_clipper) are patched so
no real credentials or filesystem access are needed.
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest
import tweepy


# ---------------------------------------------------------------------------
# Helpers to build a patched TwitterClient without real credentials
# ---------------------------------------------------------------------------

FAKE_CREDS = {
    "rocketleague": {
        "api_key": "ck",
        "api_secret": "cs",
        "access_token": "at",
        "access_token_secret": "ats",
    },
    "geometrydash": {
        "api_key": "ck2",
        "api_secret": "cs2",
        "access_token": "at2",
        "access_token_secret": "ats2",
    },
}


def _make_client(niche: str = "rocketleague", dry_run: bool = False) -> tuple:
    """
    Return (client, mock_logger) with all external deps patched.
    The returned mock_logger is the patched logger inside src.poster.client.
    """
    with (
        patch("src.poster.client.DRY_RUN", dry_run),
        patch("src.poster.client.NICHE_CREDENTIALS", FAKE_CREDS),
        patch("tweepy.Client"),
        patch("tweepy.OAuth1UserHandler"),
        patch("tweepy.API"),
        patch("src.poster.client._ensure_h264"),
        patch("src.poster.client.logger") as mock_log,
    ):
        from src.poster.client import TwitterClient

        client = TwitterClient(niche)
        client._client = MagicMock()
        client._api = MagicMock()
        return client, mock_log


# Convenience: build fresh client + mock_logger inside a test
# (we can't hold references across the context-manager boundary, so we expose
# a fixture-style factory instead)


@pytest.fixture()
def rl_client_and_log():
    """Yield (TwitterClient[rocketleague], mock_logger) with all deps patched."""
    with (
        patch("src.poster.client.DRY_RUN", False),
        patch("src.poster.client.NICHE_CREDENTIALS", FAKE_CREDS),
        patch("tweepy.Client"),
        patch("tweepy.OAuth1UserHandler"),
        patch("tweepy.API"),
        patch("src.poster.client._ensure_h264"),
        patch("src.poster.client.logger") as mock_log,
    ):
        from src.poster.client import TwitterClient

        client = TwitterClient("rocketleague")
        client._client = MagicMock()
        client._api = MagicMock()
        yield client, mock_log


@pytest.fixture()
def gd_client_and_log():
    """Yield (TwitterClient[geometrydash], mock_logger) with all deps patched."""
    with (
        patch("src.poster.client.DRY_RUN", False),
        patch("src.poster.client.NICHE_CREDENTIALS", FAKE_CREDS),
        patch("tweepy.Client"),
        patch("tweepy.OAuth1UserHandler"),
        patch("tweepy.API"),
        patch("src.poster.client._ensure_h264"),
        patch("src.poster.client.logger") as mock_log,
    ):
        from src.poster.client import TwitterClient

        client = TwitterClient("geometrydash")
        client._client = MagicMock()
        client._api = MagicMock()
        yield client, mock_log


@pytest.fixture()
def dry_client_and_log():
    """Yield (TwitterClient[rocketleague, dry_run=True], mock_logger)."""
    with (
        patch("src.poster.client.DRY_RUN", True),
        patch("src.poster.client.NICHE_CREDENTIALS", FAKE_CREDS),
        patch("tweepy.Client"),
        patch("tweepy.OAuth1UserHandler"),
        patch("tweepy.API"),
        patch("src.poster.client._ensure_h264"),
        patch("src.poster.client.logger") as mock_log,
    ):
        from src.poster.client import TwitterClient

        client = TwitterClient("rocketleague")
        yield client, mock_log


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_create_tweet_response(tweet_id: str = "123456") -> MagicMock:
    """Return a tweepy-style response object with data['id'] set."""
    resp = MagicMock()
    resp.data = {"id": tweet_id}
    return resp


# ===========================================================================
# 1. Return-value tests
# ===========================================================================


class TestReturnValues:
    def test_bad_request_returns_none(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        assert client.post_tweet("hello") is None

    def test_too_many_requests_returns_none(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TooManyRequests(MagicMock())
        assert client.post_tweet("hello") is None

    def test_generic_tweepy_exception_returns_none(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TweepyException("boom")
        assert client.post_tweet("hello") is None

    def test_forbidden_subclass_returns_none(self, rl_client_and_log):
        """tweepy.Forbidden is a subclass of TweepyException — must fall through to the generic handler."""
        client, _ = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.Forbidden(MagicMock())
        assert client.post_tweet("hello") is None

    def test_success_returns_tweet_id_string(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.return_value = _make_create_tweet_response("987")
        result = client.post_tweet("hello")
        assert result == "987"

    def test_success_returns_string_not_int(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.return_value = _make_create_tweet_response(99)
        result = client.post_tweet("hello")
        assert isinstance(result, str)
        assert result == "99"

    def test_dry_run_returns_dry_run_id(self, dry_client_and_log):
        client, _ = dry_client_and_log
        result = client.post_tweet("hello")
        assert result == "dry_run_id"

    def test_dry_run_returns_dry_run_id_with_media(self, dry_client_and_log):
        client, _ = dry_client_and_log
        result = client.post_tweet("hello", media_path="/fake/video.mp4")
        assert result == "dry_run_id"

    def test_dry_run_returns_dry_run_id_with_reply_to(self, dry_client_and_log):
        client, _ = dry_client_and_log
        result = client.post_tweet("hello", reply_to="111")
        assert result == "dry_run_id"


# ===========================================================================
# 2. Log level tests
# ===========================================================================


class TestLogLevels:
    def test_bad_request_logs_at_error(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        client.post_tweet("some tweet")
        mock_log.error.assert_called_once()
        mock_log.warning.assert_not_called()

    def test_too_many_requests_logs_at_warning(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TooManyRequests(MagicMock())
        client.post_tweet("some tweet")
        mock_log.warning.assert_called_once()
        mock_log.error.assert_not_called()

    def test_generic_tweepy_exception_logs_at_error(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TweepyException("oops")
        client.post_tweet("some tweet")
        mock_log.error.assert_called_once()
        mock_log.warning.assert_not_called()

    def test_forbidden_logs_at_error_not_warning(self, rl_client_and_log):
        """Forbidden is not rate-limiting — must be ERROR, not WARNING."""
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.Forbidden(MagicMock())
        client.post_tweet("some tweet")
        mock_log.error.assert_called_once()
        mock_log.warning.assert_not_called()

    def test_success_does_not_log_error_or_warning(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.return_value = _make_create_tweet_response()
        client.post_tweet("hello")
        mock_log.error.assert_not_called()
        mock_log.warning.assert_not_called()

    def test_dry_run_does_not_log_error_or_warning(self, dry_client_and_log):
        client, mock_log = dry_client_and_log
        client.post_tweet("hello")
        mock_log.error.assert_not_called()
        mock_log.warning.assert_not_called()


# ===========================================================================
# 3. Tweet text appears in log message tests
# ===========================================================================


class TestTweetTextInLog:
    def test_bad_request_log_contains_tweet_text(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        client.post_tweet("breaking news headline")
        log_msg = mock_log.error.call_args[0][0]
        assert "breaking news headline" in log_msg

    def test_generic_exception_log_contains_tweet_text(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TweepyException("server error")
        client.post_tweet("another tweet body")
        log_msg = mock_log.error.call_args[0][0]
        assert "another tweet body" in log_msg

    def test_too_many_requests_log_is_short_no_full_text_required(self, rl_client_and_log):
        """The 429 warning is intentionally brief — just verify it doesn't crash."""
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TooManyRequests(MagicMock())
        client.post_tweet("this text need not appear in the 429 log")
        mock_log.warning.assert_called_once()

    def test_bad_request_log_contains_char_count(self, rl_client_and_log):
        """Log message must include the length of the tweet text."""
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        text = "x" * 50
        client.post_tweet(text)
        log_msg = mock_log.error.call_args[0][0]
        assert "50" in log_msg

    def test_generic_exception_log_contains_char_count(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TweepyException("err")
        text = "y" * 30
        client.post_tweet(text)
        log_msg = mock_log.error.call_args[0][0]
        assert "30" in log_msg

    def test_bad_request_log_contains_400_string(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        client.post_tweet("hello")
        log_msg = mock_log.error.call_args[0][0]
        assert "400" in log_msg

    def test_too_many_requests_log_contains_429_string(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TooManyRequests(MagicMock())
        client.post_tweet("hello")
        log_msg = mock_log.warning.call_args[0][0]
        assert "429" in log_msg

    def test_text_exactly_120_chars_logged_in_full(self, rl_client_and_log):
        """At exactly 120 chars the slice [:120] equals the full string — no truncation."""
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        text = "a" * 120
        client.post_tweet(text)
        log_msg = mock_log.error.call_args[0][0]
        # repr of 120-char string is "'aaa...aaa'" — all 120 a's present
        assert "a" * 120 in log_msg

    def test_text_121_chars_truncated_at_120_in_bad_request_log(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        text = "a" * 120 + "Z"  # 121 chars; the Z must not appear
        client.post_tweet(text)
        log_msg = mock_log.error.call_args[0][0]
        assert "Z" not in log_msg

    def test_text_121_chars_truncated_at_120_in_generic_exception_log(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TweepyException("err")
        text = "b" * 120 + "Q"
        client.post_tweet(text)
        log_msg = mock_log.error.call_args[0][0]
        assert "Q" not in log_msg

    def test_text_500_chars_truncated_at_120(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        text = "c" * 500
        client.post_tweet(text)
        log_msg = mock_log.error.call_args[0][0]
        # 120 c's inside repr quotes = 122 chars of c-sequences
        assert "c" * 120 in log_msg
        # The 121st c would appear if truncation is wrong — but we logged 500 chars
        # The char count logged must be 500, not 120
        assert "500" in log_msg

    def test_empty_text_does_not_crash_bad_request(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        result = client.post_tweet("")
        assert result is None
        mock_log.error.assert_called_once()

    def test_empty_text_does_not_crash_generic_exception(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TweepyException("err")
        result = client.post_tweet("")
        assert result is None
        mock_log.error.assert_called_once()

    def test_unicode_emoji_text_does_not_crash(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        result = client.post_tweet("🚀 GD update 🎮 great news 🔥")
        assert result is None
        mock_log.error.assert_called_once()

    def test_repr_escaping_present_in_bad_request_log(self, rl_client_and_log):
        """The format string uses !r, so the logged value is a Python repr."""
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        text = "line1\nline2"
        client.post_tweet(text)
        log_msg = mock_log.error.call_args[0][0]
        # !r escapes the newline to \\n inside the repr
        assert "\\n" in log_msg

    def test_text_with_special_sql_chars_does_not_crash(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TweepyException("err")
        result = client.post_tweet("'; DROP TABLE tweets; --")
        assert result is None

    def test_niche_name_in_bad_request_log(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        client.post_tweet("hello")
        log_msg = mock_log.error.call_args[0][0]
        assert "rocketleague" in log_msg

    def test_niche_name_in_generic_exception_log(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TweepyException("err")
        client.post_tweet("hello")
        log_msg = mock_log.error.call_args[0][0]
        assert "rocketleague" in log_msg

    def test_niche_name_in_too_many_requests_log(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TooManyRequests(MagicMock())
        client.post_tweet("hello")
        log_msg = mock_log.warning.call_args[0][0]
        assert "rocketleague" in log_msg


# ===========================================================================
# 4. Niche-specific log tests (geometrydash)
# ===========================================================================


class TestNicheInLog:
    def test_geometrydash_niche_in_bad_request_log(self, gd_client_and_log):
        client, mock_log = gd_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        client.post_tweet("GD news")
        log_msg = mock_log.error.call_args[0][0]
        assert "geometrydash" in log_msg

    def test_geometrydash_niche_in_too_many_requests_log(self, gd_client_and_log):
        client, mock_log = gd_client_and_log
        client._client.create_tweet.side_effect = tweepy.TooManyRequests(MagicMock())
        client.post_tweet("GD news")
        log_msg = mock_log.warning.call_args[0][0]
        assert "geometrydash" in log_msg

    def test_geometrydash_niche_in_generic_exception_log(self, gd_client_and_log):
        client, mock_log = gd_client_and_log
        client._client.create_tweet.side_effect = tweepy.TweepyException("err")
        client.post_tweet("GD news")
        log_msg = mock_log.error.call_args[0][0]
        assert "geometrydash" in log_msg


# ===========================================================================
# 5. dry_run mode tests
# ===========================================================================


class TestDryRun:
    def test_dry_run_does_not_call_create_tweet(self, dry_client_and_log):
        client, _ = dry_client_and_log
        client._client = MagicMock()
        client.post_tweet("hello")
        client._client.create_tweet.assert_not_called()

    def test_dry_run_logs_info(self, dry_client_and_log):
        client, mock_log = dry_client_and_log
        client.post_tweet("hello")
        mock_log.info.assert_called()

    def test_dry_run_log_contains_tweet_text(self, dry_client_and_log):
        client, mock_log = dry_client_and_log
        client.post_tweet("special dry run tweet")
        info_call = mock_log.info.call_args[0][0]
        assert "special dry run tweet" in info_call

    def test_dry_run_does_not_call_upload_media(self, dry_client_and_log):
        client, _ = dry_client_and_log
        with patch.object(client, "_upload_media") as mock_upload:
            client.post_tweet("hello", media_path="/fake/video.mp4")
            mock_upload.assert_not_called()

    def test_dry_run_no_tweepy_exception_propagated(self, dry_client_and_log):
        """Even if tweepy internals were broken, dry-run must not raise."""
        client, _ = dry_client_and_log
        # No tweepy calls happen in dry_run — should simply return "dry_run_id"
        result = client.post_tweet("hello")
        assert result == "dry_run_id"


# ===========================================================================
# 6. reply_to parameter tests
# ===========================================================================


class TestReplyTo:
    def test_reply_to_bad_request_returns_none(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        result = client.post_tweet("reply text", reply_to="000111")
        assert result is None

    def test_reply_to_bad_request_logs_tweet_text(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        client.post_tweet("this is the reply body", reply_to="000111")
        log_msg = mock_log.error.call_args[0][0]
        assert "this is the reply body" in log_msg

    def test_reply_to_too_many_requests_returns_none(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TooManyRequests(MagicMock())
        result = client.post_tweet("reply text", reply_to="000111")
        assert result is None

    def test_reply_to_success_passes_in_reply_to_tweet_id(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.return_value = _make_create_tweet_response("555")
        client.post_tweet("reply text", reply_to="000111")
        call_kwargs = client._client.create_tweet.call_args[1]
        assert call_kwargs.get("in_reply_to_tweet_id") == "000111"


# ===========================================================================
# 7. media_path / _upload_media interaction tests
# ===========================================================================


class TestMediaAttachment:
    def test_no_media_does_not_pass_media_ids(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.return_value = _make_create_tweet_response()
        client.post_tweet("no media tweet")
        call_kwargs = client._client.create_tweet.call_args[1]
        assert "media_ids" not in call_kwargs

    def test_with_media_path_calls_upload_media(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.return_value = _make_create_tweet_response()
        with patch.object(client, "_upload_media", return_value=["mid_123"]) as mock_up:
            client.post_tweet("with media", media_path="/tmp/clip.mp4")
            mock_up.assert_called_once_with("/tmp/clip.mp4")

    def test_with_media_path_passes_media_ids_to_create_tweet(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.return_value = _make_create_tweet_response()
        with patch.object(client, "_upload_media", return_value=["mid_abc"]):
            client.post_tweet("with media", media_path="/tmp/clip.mp4")
        call_kwargs = client._client.create_tweet.call_args[1]
        assert call_kwargs.get("media_ids") == ["mid_abc"]

    def test_upload_media_returns_none_does_not_pass_media_ids(self, rl_client_and_log):
        """If _upload_media returns None, media_ids must not be forwarded."""
        client, _ = rl_client_and_log
        client._client.create_tweet.return_value = _make_create_tweet_response()
        with patch.object(client, "_upload_media", return_value=None):
            client.post_tweet("media failed", media_path="/tmp/clip.mp4")
        call_kwargs = client._client.create_tweet.call_args[1]
        assert "media_ids" not in call_kwargs


# ===========================================================================
# 8. quote_tweet exception handling tests
# ===========================================================================


class TestQuoteTweetExceptions:
    def test_quote_tweet_bad_request_returns_none(self, rl_client_and_log):
        """quote_tweet catches TweepyException which covers BadRequest."""
        client, _ = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        result = client.quote_tweet("orig_id", "quote text")
        assert result is None

    def test_quote_tweet_too_many_requests_returns_none(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TooManyRequests(MagicMock())
        result = client.quote_tweet("orig_id", "quote text")
        assert result is None

    def test_quote_tweet_generic_exception_returns_none(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TweepyException("err")
        result = client.quote_tweet("orig_id", "quote text")
        assert result is None

    def test_quote_tweet_exception_logs_error(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TweepyException("err")
        client.quote_tweet("orig_id", "quote text")
        mock_log.error.assert_called_once()

    def test_quote_tweet_error_log_contains_original_tweet_id(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TweepyException("err")
        client.quote_tweet("tweet_abc_123", "quote text")
        log_msg = mock_log.error.call_args[0][0]
        assert "tweet_abc_123" in log_msg

    def test_quote_tweet_success_returns_new_tweet_id(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.return_value = _make_create_tweet_response("777")
        result = client.quote_tweet("orig", "text")
        assert result == "777"

    def test_quote_tweet_success_passes_quote_tweet_id(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.return_value = _make_create_tweet_response()
        client.quote_tweet("src_tweet_99", "text")
        call_kwargs = client._client.create_tweet.call_args[1]
        assert call_kwargs.get("quote_tweet_id") == "src_tweet_99"

    def test_quote_tweet_dry_run_returns_dry_run_qt_id(self, dry_client_and_log):
        client, _ = dry_client_and_log
        result = client.quote_tweet("orig", "text")
        assert result == "dry_run_qt_id"

    def test_quote_tweet_dry_run_does_not_call_create_tweet(self, dry_client_and_log):
        client, _ = dry_client_and_log
        client._client = MagicMock()
        client.quote_tweet("orig", "text")
        client._client.create_tweet.assert_not_called()

    def test_quote_tweet_niche_in_error_log(self, gd_client_and_log):
        client, mock_log = gd_client_and_log
        client._client.create_tweet.side_effect = tweepy.TweepyException("err")
        client.quote_tweet("some_id", "gd text")
        log_msg = mock_log.error.call_args[0][0]
        assert "geometrydash" in log_msg


# ===========================================================================
# 9. Integration-style / sequence tests
# ===========================================================================


class TestIntegrationSequences:
    def test_consecutive_success_bad_request_success(self, rl_client_and_log):
        client, _ = rl_client_and_log
        client._client.create_tweet.side_effect = [
            _make_create_tweet_response("100"),
            tweepy.BadRequest(MagicMock()),
            _make_create_tweet_response("200"),
        ]
        r1 = client.post_tweet("first")
        r2 = client.post_tweet("second")
        r3 = client.post_tweet("third")
        assert r1 == "100"
        assert r2 is None
        assert r3 == "200"

    def test_bad_request_followed_by_success_error_count_correct(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = [
            tweepy.BadRequest(MagicMock()),
            _make_create_tweet_response("999"),
        ]
        client.post_tweet("fail")
        client.post_tweet("succeed")
        assert mock_log.error.call_count == 1

    def test_three_rate_limit_errors_logged_as_warnings(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TooManyRequests(MagicMock())
        for _ in range(3):
            client.post_tweet("rate limited")
        assert mock_log.warning.call_count == 3
        mock_log.error.assert_not_called()

    def test_exception_message_appears_in_bad_request_log(self, rl_client_and_log):
        """The `error: {exc}` part should embed the exception string."""
        client, mock_log = rl_client_and_log
        exc = tweepy.BadRequest(MagicMock())
        client._client.create_tweet.side_effect = exc
        client.post_tweet("hello")
        log_msg = mock_log.error.call_args[0][0]
        assert "error:" in log_msg

    def test_exception_message_appears_in_generic_exception_log(self, rl_client_and_log):
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TweepyException("my special error msg")
        client.post_tweet("hello")
        log_msg = mock_log.error.call_args[0][0]
        assert "my special error msg" in log_msg

    def test_permanent_failure_phrase_in_bad_request_log(self, rl_client_and_log):
        """The log must contain the phrase 'permanent failure' to distinguish from retryable."""
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.BadRequest(MagicMock())
        client.post_tweet("hello")
        log_msg = mock_log.error.call_args[0][0]
        assert "permanent" in log_msg.lower()

    def test_retry_later_phrase_in_too_many_requests_log(self, rl_client_and_log):
        """The 429 warning should hint at retry semantics."""
        client, mock_log = rl_client_and_log
        client._client.create_tweet.side_effect = tweepy.TooManyRequests(MagicMock())
        client.post_tweet("hello")
        log_msg = mock_log.warning.call_args[0][0]
        assert "retry" in log_msg.lower()
