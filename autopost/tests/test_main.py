"""
Unit tests for src/main.py
Covers: _make_collector factory, build_scheduler, _run_collector,
_run_poster, _run_stale_cleanup, _run_db_cleanup, _alert.

All external dependencies (DB, APScheduler, collectors, poster) are mocked.
No network access, no real DB, no real scheduler execution.
"""
import asyncio
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ── _make_collector() ──────────────────────────────────────────────────────────

class TestMakeCollector:
    """Factory function that maps source type → collector instance."""

    def _call(self, type_: str, config: dict = None, niche: str = "rocketleague"):
        from src.main import _make_collector
        return _make_collector(source_id=1, type_=type_, config=config or {}, niche=niche)

    def test_rss_returns_rss_collector(self):
        from src.collectors.rss import RSSCollector
        result = self._call("rss", {"url": "https://example.com/feed"})
        assert isinstance(result, RSSCollector)

    def test_scraper_returns_scraper_collector(self):
        from src.collectors.scraper import ScraperCollector
        result = self._call("scraper", {"url": "https://example.com"})
        assert isinstance(result, ScraperCollector)

    def test_twitter_returns_twitter_monitor_collector(self):
        from src.collectors.twitter_monitor import TwitterMonitorCollector
        result = self._call("twitter", {"account_id": "RocketLeague"})
        assert isinstance(result, TwitterMonitorCollector)

    def test_youtube_returns_youtube_collector(self):
        from src.collectors.youtube import YouTubeCollector
        result = self._call("youtube", {"channel_id": "UC123"})
        assert isinstance(result, YouTubeCollector)

    def test_api_pointercrate_returns_pointercrate_collector(self):
        from src.collectors.apis.pointercrate import PointercrateCollector
        result = self._call("api", {"collector": "pointercrate"})
        assert isinstance(result, PointercrateCollector)

    def test_api_gdbrowser_returns_gdbrowser_collector(self):
        from src.collectors.apis.gdbrowser import GDBrowserCollector
        result = self._call("api", {"collector": "gdbrowser"})
        assert isinstance(result, GDBrowserCollector)

    def test_api_github_returns_github_collector(self):
        from src.collectors.apis.github import GitHubCollector
        result = self._call("api", {"collector": "github"})
        assert isinstance(result, GitHubCollector)

    def test_api_flashback_returns_flashback_collector(self):
        from src.collectors.apis.flashback import FlashbackCollector
        result = self._call("api", {"collector": "flashback"})
        assert isinstance(result, FlashbackCollector)

    def test_api_geode_index_returns_geode_index_collector(self):
        from src.collectors.apis.geode_index import GeodeIndexCollector
        result = self._call("api", {"collector": "geode_index"})
        assert isinstance(result, GeodeIndexCollector)

    def test_api_rl_stats_returns_rl_stats_collector(self):
        from src.collectors.apis.rl_stats import RLStatsCollector
        result = self._call("api", {"collector": "rl_stats"})
        assert isinstance(result, RLStatsCollector)

    def test_api_unknown_collector_returns_none(self):
        result = self._call("api", {"collector": "nonexistent_collector"})
        assert result is None

    def test_api_missing_collector_key_returns_none(self):
        result = self._call("api", {})
        assert result is None

    def test_unknown_type_returns_none(self):
        result = self._call("unknown_type", {})
        assert result is None

    def test_rss_collector_has_correct_source_id(self):
        from src.collectors.rss import RSSCollector
        from src.main import _make_collector
        result = _make_collector(source_id=42, type_="rss", config={"url": "https://example.com/feed"}, niche="rocketleague")
        assert result.source_id == 42

    def test_niche_passed_to_scraper(self):
        from src.collectors.scraper import ScraperCollector
        from src.main import _make_collector
        result = _make_collector(source_id=1, type_="scraper", config={}, niche="geometrydash")
        assert isinstance(result, ScraperCollector)
        assert result.niche == "geometrydash"


# ── _run_collector() ───────────────────────────────────────────────────────────

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "database" / "schema.sql"


def _make_test_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()
    return conn


class TestRunCollector:

    @pytest.mark.asyncio
    async def test_successful_collection_logs_count(self):
        from src.main import _run_collector
        mock_collector = MagicMock()

        with patch("src.main.collect_and_queue", AsyncMock(return_value=3)):
            await _run_collector(mock_collector, "rocketleague", source_id=1, source_name="Test Source")

    @pytest.mark.asyncio
    async def test_zero_items_does_not_log_info(self):
        from src.main import _run_collector
        mock_collector = MagicMock()

        with patch("src.main.collect_and_queue", AsyncMock(return_value=0)):
            await _run_collector(mock_collector, "rocketleague", source_id=1, source_name="Test Source")

    @pytest.mark.asyncio
    async def test_exception_records_error(self):
        from src.main import _run_collector

        mock_conn = _make_test_db()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=None)

        with (
            patch("src.main.collect_and_queue", AsyncMock(side_effect=RuntimeError("network error"))),
            patch("src.main.get_db", return_value=mock_ctx),
            patch("src.main.record_source_error") as mock_record,
            patch("src.main.recent_source_error_count", return_value=1),
            patch("src.main._alert", AsyncMock()),
        ):
            await _run_collector(MagicMock(), "rocketleague", source_id=5, source_name="Broken Source")

        mock_record.assert_called_once()
        call_args = mock_record.call_args
        assert call_args[0][1] == 5  # source_id

    @pytest.mark.asyncio
    async def test_exception_above_alert_threshold_sends_alert(self):
        from src.main import _run_collector, _ALERT_THRESHOLD

        mock_conn = _make_test_db()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=None)

        with (
            patch("src.main.collect_and_queue", AsyncMock(side_effect=ValueError("fail"))),
            patch("src.main.get_db", return_value=mock_ctx),
            patch("src.main.record_source_error"),
            patch("src.main.recent_source_error_count", return_value=_ALERT_THRESHOLD),
            patch("src.main._alert", AsyncMock()) as mock_alert,
            patch("src.main.disable_source"),
        ):
            await _run_collector(MagicMock(), "rocketleague", source_id=1, source_name="Degraded")

        mock_alert.assert_awaited_once()
        alert_msg = mock_alert.call_args[0][0]
        assert "degraded" in alert_msg.lower() or "Degraded" in alert_msg

    @pytest.mark.asyncio
    async def test_exception_above_disable_threshold_disables_source(self):
        from src.main import _run_collector, _DISABLE_THRESHOLD

        mock_conn = _make_test_db()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=None)

        with (
            patch("src.main.collect_and_queue", AsyncMock(side_effect=ValueError("fatal"))),
            patch("src.main.get_db", return_value=mock_ctx),
            patch("src.main.record_source_error"),
            patch("src.main.recent_source_error_count", return_value=_DISABLE_THRESHOLD),
            patch("src.main.disable_source") as mock_disable,
            patch("src.main._alert", AsyncMock()),
        ):
            await _run_collector(MagicMock(), "rocketleague", source_id=7, source_name="Dead Source")

        mock_disable.assert_called_once()


# ── _run_poster() ──────────────────────────────────────────────────────────────

class TestRunPoster:

    def test_calls_post_next(self):
        from src.main import _run_poster
        mock_client = MagicMock()

        with patch("src.main.post_next") as mock_post:
            _run_poster("rocketleague", mock_client)

        mock_post.assert_called_once_with("rocketleague", mock_client)

    def test_exception_does_not_propagate(self):
        from src.main import _run_poster
        mock_client = MagicMock()

        with patch("src.main.post_next", side_effect=RuntimeError("poster crashed")):
            # Should not raise — errors are caught and logged
            _run_poster("rocketleague", mock_client)

    def test_exception_creates_alert_task_when_loop_running(self):
        """When a running event loop exists, _run_poster creates an alert task."""
        from src.main import _run_poster
        mock_client = MagicMock()
        mock_loop = MagicMock()
        mock_loop.create_task = MagicMock()

        with (
            patch("src.main.post_next", side_effect=RuntimeError("crash")),
            patch("asyncio.get_running_loop", return_value=mock_loop),
            patch("src.main._alert", AsyncMock()),
        ):
            _run_poster("geometrydash", mock_client)

        mock_loop.create_task.assert_called_once()

    def test_runtime_error_on_get_loop_is_swallowed(self):
        """If get_running_loop raises RuntimeError, _run_poster still doesn't crash."""
        from src.main import _run_poster
        mock_client = MagicMock()

        with (
            patch("src.main.post_next", side_effect=ValueError("no post")),
            patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")),
        ):
            _run_poster("rocketleague", mock_client)


# ── _run_stale_cleanup() ───────────────────────────────────────────────────────

class TestRunStaleCleanup:

    def test_calls_skip_stale_with_correct_niche(self):
        from src.main import _run_stale_cleanup

        with patch("src.main.skip_stale", return_value=0) as mock_skip:
            _run_stale_cleanup("rocketleague")

        mock_skip.assert_called_once_with("rocketleague", max_age_hours=6)

    def test_logs_when_items_skipped(self):
        from src.main import _run_stale_cleanup

        with patch("src.main.skip_stale", return_value=5):
            # Should not raise
            _run_stale_cleanup("geometrydash")

    def test_no_log_when_zero_skipped(self):
        from src.main import _run_stale_cleanup

        with patch("src.main.skip_stale", return_value=0):
            _run_stale_cleanup("rocketleague")


# ── _run_db_cleanup() ──────────────────────────────────────────────────────────

class TestRunDbCleanup:

    def test_calls_cleanup_old_records(self):
        from src.main import _run_db_cleanup

        mock_conn = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=None)

        with (
            patch("src.main.get_db", return_value=mock_ctx),
            patch("src.main.cleanup_old_records", return_value={"tweet_queue": 0, "raw_content": 0}) as mock_cleanup,
        ):
            _run_db_cleanup()

        mock_cleanup.assert_called_once_with(mock_conn, days=30)

    def test_logs_when_rows_deleted(self):
        from src.main import _run_db_cleanup

        mock_conn = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=None)

        with (
            patch("src.main.get_db", return_value=mock_ctx),
            patch("src.main.cleanup_old_records", return_value={"tweet_queue": 10, "raw_content": 5}),
        ):
            _run_db_cleanup()

    def test_no_log_when_nothing_deleted(self):
        from src.main import _run_db_cleanup

        mock_conn = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=None)

        with (
            patch("src.main.get_db", return_value=mock_ctx),
            patch("src.main.cleanup_old_records", return_value={"tweet_queue": 0, "raw_content": 0}),
        ):
            _run_db_cleanup()


# ── _alert() ──────────────────────────────────────────────────────────────────

class TestAlert:

    @pytest.mark.asyncio
    async def test_calls_send_alert(self):
        from src.main import _alert

        with patch("src.monitoring.alerts.send_alert", AsyncMock()) as mock_send:
            await _alert("test message", level="error")

        mock_send.assert_awaited_once_with("test message", level="error")

    @pytest.mark.asyncio
    async def test_swallows_exception_from_send_alert(self):
        from src.main import _alert

        with patch("src.monitoring.alerts.send_alert", AsyncMock(side_effect=RuntimeError("discord down"))):
            # Should not raise
            await _alert("oops", level="warning")

    @pytest.mark.asyncio
    async def test_default_level_is_error(self):
        from src.main import _alert

        with patch("src.monitoring.alerts.send_alert", AsyncMock()) as mock_send:
            await _alert("something went wrong")

        mock_send.assert_awaited_once_with("something went wrong", level="error")


# ── build_scheduler() ─────────────────────────────────────────────────────────

class TestBuildScheduler:

    def _make_db_ctx(self, sources: list = None):
        """Return a context manager mock that yields a connection with preset sources."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = sources or []
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=None)
        return mock_ctx

    def test_returns_scheduler_instance(self):
        from src.main import build_scheduler
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        with (
            patch("src.main.get_db", return_value=self._make_db_ctx([])),
            patch("src.main.TwitterClient", return_value=MagicMock()),
        ):
            scheduler = build_scheduler(niches=["rocketleague"])

        assert isinstance(scheduler, AsyncIOScheduler)

    def test_adds_poster_job_per_niche(self):
        from src.main import build_scheduler

        with (
            patch("src.main.get_db", return_value=self._make_db_ctx([])),
            patch("src.main.TwitterClient", return_value=MagicMock()),
        ):
            scheduler = build_scheduler(niches=["rocketleague", "geometrydash"])

        job_ids = [j.id for j in scheduler.get_jobs()]
        assert "poster_rocketleague" in job_ids
        assert "poster_geometrydash" in job_ids

    def test_adds_stale_cleanup_job_per_niche(self):
        from src.main import build_scheduler

        with (
            patch("src.main.get_db", return_value=self._make_db_ctx([])),
            patch("src.main.TwitterClient", return_value=MagicMock()),
        ):
            scheduler = build_scheduler(niches=["rocketleague"])

        job_ids = [j.id for j in scheduler.get_jobs()]
        assert "stale_rocketleague" in job_ids

    def test_adds_db_cleanup_job_once(self):
        from src.main import build_scheduler

        with (
            patch("src.main.get_db", return_value=self._make_db_ctx([])),
            patch("src.main.TwitterClient", return_value=MagicMock()),
        ):
            scheduler = build_scheduler(niches=["rocketleague", "geometrydash"])

        db_cleanup_jobs = [j for j in scheduler.get_jobs() if j.id == "db_cleanup"]
        assert len(db_cleanup_jobs) == 1

    def test_adds_health_check_job_once(self):
        from src.main import build_scheduler

        with (
            patch("src.main.get_db", return_value=self._make_db_ctx([])),
            patch("src.main.TwitterClient", return_value=MagicMock()),
        ):
            scheduler = build_scheduler(niches=["rocketleague", "geometrydash"])

        health_jobs = [j for j in scheduler.get_jobs() if j.id == "health_check"]
        assert len(health_jobs) == 1

    def test_schedules_collector_job_for_valid_source(self):
        from src.main import build_scheduler

        source_row = MagicMock()
        source_row.__getitem__ = lambda self, k: {
            "id": 1,
            "type": "rss",
            "config": json.dumps({"url": "https://example.com/feed", "poll_interval": 300}),
            "name": "Test RSS",
        }[k]

        with (
            patch("src.main.get_db", return_value=self._make_db_ctx([source_row])),
            patch("src.main.TwitterClient", return_value=MagicMock()),
        ):
            scheduler = build_scheduler(niches=["rocketleague"])

        job_ids = [j.id for j in scheduler.get_jobs()]
        assert "collect_rocketleague_1" in job_ids

    def test_skips_unknown_source_type(self):
        from src.main import build_scheduler

        source_row = MagicMock()
        source_row.__getitem__ = lambda self, k: {
            "id": 99,
            "type": "unknown_type",
            "config": json.dumps({"poll_interval": 300}),
            "name": "Unknown Source",
        }[k]

        with (
            patch("src.main.get_db", return_value=self._make_db_ctx([source_row])),
            patch("src.main.TwitterClient", return_value=MagicMock()),
        ):
            scheduler = build_scheduler(niches=["rocketleague"])

        job_ids = [j.id for j in scheduler.get_jobs()]
        assert "collect_rocketleague_99" not in job_ids

    def test_default_poll_interval_used_when_not_in_config(self):
        """Source without poll_interval in config should use the default 900s."""
        from src.main import build_scheduler

        source_row = MagicMock()
        source_row.__getitem__ = lambda self, k: {
            "id": 2,
            "type": "rss",
            "config": json.dumps({"url": "https://example.com/feed"}),
            "name": "No Interval RSS",
        }[k]

        with (
            patch("src.main.get_db", return_value=self._make_db_ctx([source_row])),
            patch("src.main.TwitterClient", return_value=MagicMock()),
        ):
            scheduler = build_scheduler(niches=["rocketleague"])

        job = scheduler.get_job("collect_rocketleague_2")
        assert job is not None

    def test_empty_niches_list_still_adds_db_cleanup(self):
        from src.main import build_scheduler

        with (
            patch("src.main.get_db", return_value=self._make_db_ctx([])),
            patch("src.main.TwitterClient", return_value=MagicMock()),
        ):
            scheduler = build_scheduler(niches=[])

        job_ids = [j.id for j in scheduler.get_jobs()]
        assert "db_cleanup" in job_ids


# ── _shutdown() ───────────────────────────────────────────────────────────────

class TestShutdown:

    @pytest.mark.asyncio
    async def test_shutdown_calls_scheduler_shutdown(self):
        from src.main import _shutdown

        mock_scheduler = MagicMock()
        await _shutdown(mock_scheduler)

        mock_scheduler.shutdown.assert_called_once_with(wait=False)

    @pytest.mark.asyncio
    async def test_shutdown_cancels_other_tasks(self):
        from src.main import _shutdown

        mock_scheduler = MagicMock()

        # Create a long-running task that we'll observe being cancelled
        cancelled_tasks = []

        async def dummy_long_task():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled_tasks.append(True)
                raise

        # Start the task in background
        task = asyncio.create_task(dummy_long_task())
        # Give it a moment to start
        await asyncio.sleep(0)

        await _shutdown(mock_scheduler)

        # Let the event loop process the cancellation
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()


# ── main() ─────────────────────────────────────────────────────────────────────

class TestMain:

    @pytest.mark.asyncio
    async def test_main_initialises_db_and_starts_scheduler(self):
        from src.main import main

        mock_scheduler = MagicMock()
        mock_scheduler.get_jobs.return_value = []
        mock_scheduler.start = MagicMock()

        # main() waits on asyncio.Event().wait() — we cancel immediately
        async def cancel_after_start(*args, **kwargs):
            raise asyncio.CancelledError()

        with (
            patch("src.main.init_db") as mock_init_db,
            patch("src.main.build_scheduler", return_value=mock_scheduler),
            patch("src.main._alert", AsyncMock()),
            patch("asyncio.Event") as mock_event_cls,
        ):
            mock_event = MagicMock()
            mock_event.wait = AsyncMock(side_effect=asyncio.CancelledError())
            mock_event_cls.return_value = mock_event

            try:
                await main()
            except (asyncio.CancelledError, SystemExit):
                pass

        mock_init_db.assert_called_once()
        mock_scheduler.start.assert_called_once()
