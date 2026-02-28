"""
Entry point — initialises APScheduler and wires all collector + poster jobs.

Each enabled source in the DB gets a scheduled collect_and_queue job running
at its configured poll_interval. The poster runs every 2 minutes per niche;
rate_limiter.can_post() enforces the 20-min minimum gap internally.
skip_stale() cleans up the queue every 6 hours.

Usage:
    cd autopost
    python src/main.py
    # or via systemd: see autopost.service
"""
import asyncio
import json
import signal
import sys
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import LOG_LEVEL, LOGS_DIR, DRY_RUN
from src.collectors.apis.gdbrowser import GDBrowserCollector
from src.collectors.apis.octane import OctaneCollector
from src.collectors.apis.pointercrate import PointercrateCollector
from src.collectors.rss import RSSCollector
from src.collectors.reddit import RedditCollector
from src.collectors.twitter_monitor import TwitterMonitorCollector
from src.collectors.youtube import YouTubeCollector
from src.database.db import cleanup_old_records, get_db, get_sources, init_db
from src.poster.client import TwitterClient
from src.poster.queue import collect_and_queue, post_next, skip_stale

# ── Logging ────────────────────────────────────────────────────────────────────
LOGS_DIR.mkdir(parents=True, exist_ok=True)
logger.remove()
logger.add(sys.stderr, level=LOG_LEVEL)
logger.add(
    LOGS_DIR / "autopost_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="14 days",
    level=LOG_LEVEL,
    encoding="utf-8",
)

# ── Collector factory ──────────────────────────────────────────────────────────
_API_MAP = {
    "pointercrate": PointercrateCollector,
    "gdbrowser":    GDBrowserCollector,
    "octane":       OctaneCollector,
}


def _make_collector(source_id: int, type_: str, config: dict, niche: str):
    if type_ == "rss":
        return RSSCollector(source_id, config, niche)
    if type_ == "reddit":
        return RedditCollector(source_id, config, niche)
    if type_ == "twitter":
        return TwitterMonitorCollector(source_id, config, niche)
    if type_ == "youtube":
        return YouTubeCollector(source_id, config, niche)
    if type_ == "api":
        cls = _API_MAP.get(config.get("collector", ""))
        if cls:
            return cls(source_id, config, niche)
    return None  # scraper or unknown — skipped (site-specific logic not yet implemented)


# ── Job runners ────────────────────────────────────────────────────────────────

async def _run_collector(collector, niche: str) -> None:
    try:
        n = await collect_and_queue(collector, niche)
        if n:
            logger.info(
                f"[Scheduler] {type(collector).__name__} → {n} new items queued"
            )
    except Exception as exc:
        logger.error(f"[Scheduler] {type(collector).__name__} failed: {exc}")
        await _alert(f"Collector {type(collector).__name__} ({niche}) failed: {exc}")


def _run_poster(niche: str, client: TwitterClient) -> None:
    try:
        post_next(niche, client)
    except Exception as exc:
        logger.error(f"[Scheduler] poster [{niche}] failed: {exc}")
        asyncio.get_event_loop().create_task(
            _alert(f"Poster [{niche}] failed: {exc}")
        )


def _run_stale_cleanup(niche: str) -> None:
    skipped = skip_stale(niche, max_age_hours=6)
    if skipped:
        logger.info(f"[Scheduler] stale cleanup [{niche}] → {skipped} items skipped")


def _run_db_cleanup() -> None:
    """Daily job: trim old posted/skipped/failed rows from tweet_queue and raw_content."""
    with get_db() as conn:
        stats = cleanup_old_records(conn, days=30)
    total = stats["tweet_queue"] + stats["raw_content"]
    if total:
        logger.info(
            f"[Scheduler] DB cleanup → removed {stats['tweet_queue']} queue rows,"
            f" {stats['raw_content']} raw_content rows (>30 days old)"
        )
    else:
        logger.debug("[Scheduler] DB cleanup → nothing to remove")


async def _alert(msg: str, level: str = "error") -> None:
    try:
        from src.monitoring.alerts import send_alert
        await send_alert(msg, level=level)
    except Exception:
        pass


# ── Scheduler setup ────────────────────────────────────────────────────────────

def build_scheduler(niches: list[str] = ("rocketleague", "geometrydash")) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    for niche in niches:
        client = TwitterClient(niche)

        with get_db() as conn:
            sources = get_sources(conn, niche)

        # ── Collector jobs ────────────────────────────────────────────────────
        for row in sources:
            source_id = row["id"]
            type_     = row["type"]
            config    = json.loads(row["config"])
            name      = row["name"]

            poll_interval = int(config.get("poll_interval", 900))
            collector     = _make_collector(source_id, type_, config, niche)

            if collector is None:
                logger.debug(f"[Scheduler] no collector for [{niche}] {name} ({type_}) — skipping")
                continue

            scheduler.add_job(
                _run_collector,
                "interval",
                seconds      = poll_interval,
                args         = [collector, niche],
                id           = f"collect_{niche}_{source_id}",
                name         = f"Collect {name} ({niche})",
                max_instances= 1,
                coalesce     = True,
                next_run_time= __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            )
            logger.debug(f"[Scheduler] scheduled [{niche}] {name} every {poll_interval}s")

        # ── Poster job (every 2 min — rate_limiter enforces actual gap) ───────
        scheduler.add_job(
            _run_poster,
            "interval",
            minutes      = 2,
            args         = [niche, client],
            id           = f"poster_{niche}",
            name         = f"Post next ({niche})",
            max_instances= 1,
            coalesce     = True,
        )

        # ── Stale queue cleanup (every 6 hours) ───────────────────────────────
        scheduler.add_job(
            _run_stale_cleanup,
            "interval",
            hours  = 6,
            args   = [niche],
            id     = f"stale_{niche}",
            name   = f"Stale cleanup ({niche})",
        )

        logger.info(f"[Scheduler] {niche}: {len(sources)} sources scheduled")

    # ── Daily DB cleanup (03:00 UTC) — runs once regardless of niche count ─────
    if not scheduler.get_job("db_cleanup"):
        scheduler.add_job(
            _run_db_cleanup,
            "cron",
            hour   = 3,
            minute = 0,
            id     = "db_cleanup",
            name   = "Daily DB cleanup (30-day rolling window)",
        )

    return scheduler


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    logger.info("AutoPost starting up" + (" [DRY RUN]" if DRY_RUN else ""))

    init_db()
    scheduler = build_scheduler()
    await _alert(f"AutoPost started {'[DRY RUN]' if DRY_RUN else '[LIVE]'}", level="success")
    scheduler.start()

    logger.info(f"Scheduler running — {len(scheduler.get_jobs())} jobs active")

    # Graceful shutdown on SIGINT / SIGTERM
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown(scheduler)))

    # Keep running until cancelled
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass


async def _shutdown(scheduler: AsyncIOScheduler) -> None:
    logger.info("Shutting down scheduler…")
    scheduler.shutdown(wait=False)
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in tasks:
        t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
