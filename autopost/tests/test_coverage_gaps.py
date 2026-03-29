"""
Targeted tests for uncovered lines in client.py, queue.py, and scraper.py.
Brings all three modules above 95% coverage.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import tweepy

from src.poster.client import TwitterClient
from src.poster.queue import _split_url, _retweet_context, post_next
from src.collectors.scraper import _fetch

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "database" / "schema.sql"

_FAKE_CREDS = {
    "api_key": "k", "api_secret": "s",
    "access_token": "t", "access_token_secret": "ts",
}
_NICHE_CREDS = {"rocketleague": _FAKE_CREDS, "geometrydash": _FAKE_CREDS}


def _dry_client():
    with patch("src.poster.client.DRY_RUN", True), \
         patch("src.poster.client.NICHE_CREDENTIALS", _NICHE_CREDS):
        return TwitterClient("rocketleague")


def _live_client():
    with patch("src.poster.client.DRY_RUN", False), \
         patch("src.poster.client.NICHE_CREDENTIALS", _NICHE_CREDS), \
         patch("src.poster.client.tweepy.Client"), \
         patch("src.poster.client.tweepy.OAuth1UserHandler"), \
         patch("src.poster.client.tweepy.API"):
        return TwitterClient("rocketleague")


# ── client.py: post_tweet with reply_to (line 81) ───────────────────────────

class TestPostTweetReplyTo:
    def test_dry_run_with_reply_to(self):
        client = _dry_client()
        result = client.post_tweet("Reply text", reply_to="12345")
        assert result == "dry_run_id"

    def test_live_with_reply_to(self):
        client = _live_client()
        mock_resp = MagicMock()
        mock_resp.data = {"id": "99"}
        client._client = MagicMock()
        client._client.create_tweet.return_value = mock_resp

        result = client.post_tweet("Reply text", reply_to="12345")

        assert result == "99"
        client._client.create_tweet.assert_called_once_with(
            text="Reply text", in_reply_to_tweet_id="12345"
        )


# ── client.py: post_thread (lines 97-108) ───────────────────────────────────

class TestPostThread:
    def test_empty_list_returns_none(self):
        client = _dry_client()
        assert client.post_thread([]) is None

    def test_single_tweet_thread(self):
        client = _dry_client()
        result = client.post_thread(["Hello"])
        assert result == "dry_run_id"

    def test_multi_tweet_thread(self):
        client = _dry_client()
        result = client.post_thread(["Tweet 1", "Tweet 2", "Tweet 3"])
        assert result == "dry_run_id"

    def test_partial_thread_on_failure(self):
        """If second tweet fails, return the first tweet's ID."""
        client = _live_client()
        call_count = 0

        def _mock_post(text, reply_to=None, media_path=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "first_id"
            return None  # second tweet fails

        client.post_tweet = _mock_post
        result = client.post_thread(["A", "B"])
        assert result == "first_id"


# ── queue.py: _split_url (lines 288-295) ────────────────────────────────────

class TestSplitUrl:
    def test_no_url_returns_text_and_none(self):
        text, url = _split_url("Just plain text here")
        assert text == "Just plain text here"
        assert url is None

    def test_splits_url_from_long_text(self):
        text = "This is a long enough tweet about something interesting happening today https://example.com/news"
        main, url = _split_url(text)
        assert url == "https://example.com/news"
        assert "https://example.com" not in main
        assert "interesting" in main

    def test_keeps_url_inline_when_text_too_short(self):
        text = "Short https://example.com"
        main, url = _split_url(text)
        assert main == text  # not split — remaining text < 30 chars
        assert url is None

    def test_splits_last_url_when_multiple(self):
        text = "Check https://first.com and also this longer text about things https://second.com"
        main, url = _split_url(text)
        assert url == "https://second.com"
        assert "https://first.com" in main

    def test_strips_whitespace_after_url_removal(self):
        text = "A long enough description of the news event here\n\nhttps://example.com/article"
        main, url = _split_url(text)
        assert url == "https://example.com/article"
        assert not main.endswith("\n")


# ── queue.py: _retweet_context ───────────────────────────────────────────────

class TestRetweetContext:
    def test_returns_string_for_rl(self):
        ctx = _retweet_context("rocketleague")
        assert isinstance(ctx, str)
        assert len(ctx) > 0

    def test_returns_string_for_gd(self):
        ctx = _retweet_context("geometrydash")
        assert isinstance(ctx, str)
        assert len(ctx) > 0

    def test_unknown_niche_returns_default(self):
        ctx = _retweet_context("unknown")
        assert ctx == "News:"


# ── queue.py: URL self-reply in post_next (line 220) ────────────────────────

def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


@contextmanager
def _ctx(conn):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


class TestUrlInlineTweet:
    def test_url_stays_inline(self):
        """URLs are posted inline (self-reply pattern was removed)."""
        conn = _make_db()
        conn.execute(
            "INSERT INTO tweet_queue (niche, tweet_text, priority, status) VALUES (?, ?, ?, 'queued')",
            ("rocketleague", "This is a long enough tweet about RL news happening today\n\nhttps://example.com/article", 5),
        )
        conn.commit()

        client = MagicMock()
        client.post_tweet.return_value = "tweet_123"

        with (
            patch("src.poster.queue.get_db", side_effect=lambda: _ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("rocketleague", client)

        assert result is True
        # Single tweet posted with full text including URL
        assert client.post_tweet.call_count == 1
        call_text = client.post_tweet.call_args.kwargs.get("text", client.post_tweet.call_args[0][0] if client.post_tweet.call_args[0] else "")
        assert "https://example.com/article" in call_text


# ── scraper.py: SSRF guard and redirect handling (lines 63-64, 79-80, etc.) ─

class TestScraperFetch:

    @pytest.mark.asyncio
    async def test_blocks_private_ip_url(self):
        """SSRF guard should reject private IPs."""
        result = await _fetch("http://169.254.169.254/latest/meta-data/")
        assert result is None

    @pytest.mark.asyncio
    async def test_blocks_non_http_scheme(self):
        result = await _fetch("file:///etc/passwd")
        assert result is None

    @pytest.mark.asyncio
    async def test_handles_redirect_safely(self):
        """Redirects should be validated before following."""
        redirect_resp = MagicMock()
        redirect_resp.is_redirect = True
        redirect_resp.headers = {"location": "http://169.254.169.254/evil"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=redirect_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch("https://example.com/redirect")

        assert result is None

    @pytest.mark.asyncio
    async def test_successful_no_redirect(self):
        """Normal response without redirect returns text."""
        mock_resp = MagicMock()
        mock_resp.is_redirect = False
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = "<html>OK</html>"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            result = await _fetch("https://example.com/page")

        assert result == "<html>OK</html>"
