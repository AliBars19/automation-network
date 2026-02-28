"""
Pipeline smoke test — runs collectors that don't need credentials,
formats results, queues them, then dry-runs the poster.

Usage:
    cd autopost
    DRY_RUN=true python scripts/test_collector.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger
from src.collectors.apis.gdbrowser import GDBrowserCollector
from src.collectors.apis.pointercrate import PointercrateCollector
from src.collectors.rss import RSSCollector
from src.database.db import get_db, get_queued_tweets, init_db, upsert_source
from src.formatter.formatter import format_tweet
from src.poster.client import TwitterClient
from src.poster.queue import collect_and_queue, post_next


# ── Test sources (inline — no YAML/DB lookup needed) ─────────────────────────

RSS_SOURCES = [
    {
        "niche": "rocketleague",
        "name":  "Steam News (RL)",
        "config": {"url": "https://store.steampowered.com/feeds/news/app/252950"},
    },
    {
        "niche": "geometrydash",
        "name":  "Steam News (GD)",
        "config": {"url": "https://store.steampowered.com/feeds/news/app/322170"},
    },
]


async def main() -> None:
    logger.info("=" * 60)
    logger.info("AutoPost pipeline smoke test")
    logger.info("=" * 60)

    # Ensure DB and sources exist
    init_db()
    with get_db() as conn:
        for src in RSS_SOURCES:
            src["id"] = upsert_source(
                conn, src["niche"], src["name"], "rss", src["config"]
            )

    # ── 1. RSS collectors ─────────────────────────────────────────────────────
    logger.info("\n[1] RSS collectors")
    for src in RSS_SOURCES:
        collector = RSSCollector(src["id"], src["config"], src["niche"])
        n = await collect_and_queue(collector, src["niche"])
        logger.info(f"    {src['name']} → {n} new items queued")

    # ── 2. Pointercrate (GD demon list) ──────────────────────────────────────
    logger.info("\n[2] Pointercrate (GD demon list)")
    with get_db() as conn:
        pc_id = upsert_source(
            conn, "geometrydash", "Pointercrate", "api",
            {"base_url": "https://pointercrate.com/api/v2"}
        )
    collector = PointercrateCollector(pc_id, {}, "geometrydash")
    n = await collect_and_queue(collector, "geometrydash")
    logger.info(f"    Pointercrate → {n} new items queued")

    # ── 3. GDBrowser (daily, weekly, rated) ───────────────────────────────────
    logger.info("\n[3] GDBrowser (daily, weekly, rated levels)")
    with get_db() as conn:
        gdb_id = upsert_source(
            conn, "geometrydash", "GDBrowser", "api",
            {"base_url": "https://gdbrowser.com/api"}
        )
    collector = GDBrowserCollector(gdb_id, {}, "geometrydash")
    n = await collect_and_queue(collector, "geometrydash")
    logger.info(f"    GDBrowser → {n} new items queued")

    # ── 4. Show queue contents ────────────────────────────────────────────────
    logger.info("\n[4] Current queue")
    for niche in ("rocketleague", "geometrydash"):
        with get_db() as conn:
            rows = get_queued_tweets(conn, niche, limit=5)
        logger.info(f"\n  {niche} — top {len(rows)} queued:")
        for row in rows:
            logger.info(
                f"    [p{row['priority']}] {row['tweet_text'][:80]}…"
                if len(row['tweet_text']) > 80
                else f"    [p{row['priority']}] {row['tweet_text']}"
            )

    # ── 5. Dry-run poster ─────────────────────────────────────────────────────
    logger.info("\n[5] Dry-run poster (1 tweet per niche)")
    for niche in ("rocketleague", "geometrydash"):
        client = TwitterClient(niche)
        posted = post_next(niche, client)
        if not posted:
            logger.info(f"    {niche}: nothing to post or rate limited")

    logger.info("\n" + "=" * 60)
    logger.info("Smoke test complete.")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
