"""
Unit tests for quote tweet support in client.py and queue.py.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import tweepy

from src.poster.client import TwitterClient
from src.poster.queue import post_next


# ── Fixtures ────────────────────────────────────────────────────────────────

_FAKE_CREDS = {
    "api_key": "key", "api_secret": "secret",
    "access_token": "token", "access_token_secret": "tsecret",
}
_NICHE_CREDS = {"rocketleague": _FAKE_CREDS, "geometrydash": _FAKE_CREDS}

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "database" / "schema.sql"


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


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def _insert_queue(conn, text, priority=5):
    conn.execute(
        "INSERT INTO tweet_queue (niche, tweet_text, priority, status) VALUES (?, ?, ?, 'queued')",
        ("rocketleague", text, priority),
    )
    conn.commit()


@contextmanager
def _ctx(conn):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── TwitterClient.quote_tweet() ─────────────────────────────────────────────

class TestQuoteTweetClient:

    def test_dry_run_returns_fake_id(self):
        client = _dry_run_client()
        result = client.quote_tweet("12345", "Great play!")
        assert result == "dry_run_qt_id"

    def test_live_success_returns_tweet_id(self):
        client = _live_client()
        mock_response = MagicMock()
        mock_response.data = {"id": "67890"}
        client._client = MagicMock()
        client._client.create_tweet.return_value = mock_response

        result = client.quote_tweet("12345", "What a shot!")

        assert result == "67890"
        client._client.create_tweet.assert_called_once_with(
            text="What a shot!",
            quote_tweet_id="12345",
        )

    def test_live_failure_returns_none(self):
        client = _live_client()
        client._client = MagicMock()
        client._client.create_tweet.side_effect = tweepy.TweepyException("403")

        result = client.quote_tweet("12345", "This will fail")

        assert result is None


# ── post_next() with QUOTE: signal ──────────────────────────────────────────

class TestQuoteTweetQueue:

    def test_dispatches_quote_signal(self):
        """QUOTE:{id}:{text} should call client.quote_tweet()."""
        conn = _make_db()
        _insert_queue(conn, "QUOTE:11111:Amazing goal!", priority=3)
        client = MagicMock()
        client.quote_tweet.return_value = "qt_999"

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("rocketleague", client)

        assert result is True
        client.quote_tweet.assert_called_once_with("11111", "Amazing goal!")
        client.post_tweet.assert_not_called()
        client.retweet.assert_not_called()

    def test_marks_failed_on_malformed_quote(self):
        """QUOTE: without a colon separator should fail."""
        conn = _make_db()
        _insert_queue(conn, "QUOTE:no-separator-here", priority=3)
        client = MagicMock()

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("rocketleague", client)

        assert result is False
        row = conn.execute("SELECT status FROM tweet_queue").fetchone()
        assert row["status"] == "failed"

    def test_marks_failed_on_non_numeric_quote_id(self):
        """QUOTE: with non-numeric tweet ID should fail."""
        conn = _make_db()
        _insert_queue(conn, "QUOTE:abc:Some text", priority=3)
        client = MagicMock()

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("rocketleague", client)

        assert result is False

    def test_marks_failed_on_empty_commentary(self):
        """QUOTE: with empty text after the ID should fail."""
        conn = _make_db()
        _insert_queue(conn, "QUOTE:11111:", priority=3)
        client = MagicMock()

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
        ):
            result = post_next("rocketleague", client)

        assert result is False

    def test_marks_failed_when_quote_tweet_returns_none(self):
        """If quote_tweet returns None, row should be marked failed."""
        conn = _make_db()
        _insert_queue(conn, "QUOTE:22222:Good stuff", priority=3)
        client = MagicMock()
        client.quote_tweet.return_value = None

        with (
            patch("src.poster.queue.get_db", return_value=_ctx(conn)),
            patch("src.poster.queue.within_monthly_limit", return_value=True),
            patch("src.poster.queue.failure_backoff_ok", return_value=True),
            patch("src.poster.queue.within_posting_window", return_value=True),
            patch("src.poster.queue.can_post", return_value=True),
            patch("src.poster.queue._posts_in_last_30min", return_value=0),
            patch("src.poster.queue._check_failure_alert"),
        ):
            result = post_next("rocketleague", client)

        assert result is False
        row = conn.execute("SELECT status FROM tweet_queue").fetchone()
        assert row["status"] == "failed"
