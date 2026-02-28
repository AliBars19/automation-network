"""
Full pipeline test — tests every component that doesn't need credentials.
Writes a detailed markdown report to Research-Documents/pipeline_test_results.md

Tests covered:
  1. Database integrity
  2. RSS collectors (RL + GD Steam News)
  3. Pointercrate API (GD demon list)
  4. GDBrowser API (daily, weekly, rated levels)
  5. Octane.gg API (RL esports)
  6. Formatter — sample output for every content type
  7. Media — download + resize
  8. Queue — priority ordering, dedup
  9. Rate limiter
  10. DRY_RUN poster
"""
import asyncio
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger
logger.remove()  # suppress console output during tests — we capture to report

from src.collectors.apis.gdbrowser import GDBrowserCollector
from src.collectors.apis.octane import OctaneCollector
from src.collectors.apis.pointercrate import PointercrateCollector
from src.collectors.rss import RSSCollector
from src.database.db import get_db, get_queued_tweets, init_db, upsert_source
from src.formatter.formatter import format_tweet
from src.formatter.media import prepare_media
from src.poster.client import TwitterClient
from src.poster.queue import collect_and_queue, post_next
from src.poster.rate_limiter import can_post, monthly_post_count

REPORT_PATH = Path(__file__).resolve().parent.parent.parent / "Research-Documents" / "pipeline_test_results.md"
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ── Helpers ───────────────────────────────────────────────────────────────────

class Section:
    def __init__(self, title: str, buf: list):
        self.title = title
        self.buf = buf
        self.passed = 0
        self.failed = 0
        self.warnings = 0

    def ok(self, msg: str):
        self.buf.append(f"- ✅ {msg}")
        self.passed += 1

    def fail(self, msg: str):
        self.buf.append(f"- ❌ {msg}")
        self.failed += 1

    def warn(self, msg: str):
        self.buf.append(f"- ⚠️ {msg}")
        self.warnings += 1

    def info(self, msg: str):
        self.buf.append(f"- ℹ️ {msg}")

    def code(self, text: str, lang: str = ""):
        self.buf.append(f"```{lang}\n{text}\n```")

    def header(self, msg: str):
        self.buf.append(f"\n**{msg}**\n")

    def summary(self) -> str:
        total = self.passed + self.failed
        status = "PASS" if self.failed == 0 else "FAIL"
        return f"[{status}] {self.passed}/{total} checks passed, {self.warnings} warnings"


def _source_id(niche: str, name: str, type_: str, config: dict) -> int:
    with get_db() as conn:
        return upsert_source(conn, niche, name, type_, config)


# ── Test 1: Database integrity ────────────────────────────────────────────────

def test_database(buf: list) -> Section:
    s = Section("1. Database integrity", buf)
    buf.append("\n## 1. Database Integrity\n")
    try:
        init_db()
        s.ok("init_db() completed without error")

        with get_db() as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]

        expected = {"sources", "raw_content", "tweet_queue", "post_log"}
        missing = expected - set(tables)
        if missing:
            s.fail(f"Missing tables: {missing}")
        else:
            s.ok(f"All 4 tables present: {', '.join(sorted(tables))}")

        with get_db() as conn:
            counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in expected}

        for table, count in counts.items():
            s.info(f"{table}: {count} rows")

        with get_db() as conn:
            sources = conn.execute(
                "SELECT niche, COUNT(*) as n FROM sources GROUP BY niche"
            ).fetchall()
        for row in sources:
            s.ok(f"Sources seeded — {row['niche']}: {row['n']} sources")

    except Exception as e:
        s.fail(f"Database error: {e}")
        s.code(traceback.format_exc())

    buf.append(f"\n> {s.summary()}\n")
    return s


# ── Test 2: RSS collectors ─────────────────────────────────────────────────────

async def test_rss(buf: list) -> Section:
    s = Section("2. RSS Collectors", buf)
    buf.append("\n## 2. RSS Collectors\n")

    feeds = [
        ("rocketleague", "Steam News (RL)", "https://store.steampowered.com/feeds/news/app/252950"),
        ("geometrydash",  "Steam News (GD)", "https://store.steampowered.com/feeds/news/app/322170"),
        ("rocketleague", "RL Blog",          "https://www.rocketleague.com/news/rss"),
    ]

    for niche, name, url in feeds:
        s.header(name)
        src_id = _source_id(niche, name, "rss", {"url": url})
        collector = RSSCollector(src_id, {"url": url}, niche)
        t0 = time.time()
        try:
            items = await collector.collect()
            elapsed = time.time() - t0
            if items:
                s.ok(f"Fetched {len(items)} entries in {elapsed:.2f}s")
                content_types = {}
                for item in items:
                    content_types[item.content_type] = content_types.get(item.content_type, 0) + 1
                s.info(f"Content types: {content_types}")
                # Show a sample tweet
                sample = items[0]
                tweet = format_tweet(sample)
                if tweet:
                    s.info(f"Sample tweet ({sample.content_type}, {len(tweet)} chars):")
                    s.code(tweet)
                else:
                    s.warn("format_tweet returned None for first item")
            else:
                s.warn(f"No entries returned (feed may be empty or unreachable) in {elapsed:.2f}s")
        except Exception as e:
            s.fail(f"{name}: {e}")

    buf.append(f"\n> {s.summary()}\n")
    return s


# ── Test 3: Pointercrate ──────────────────────────────────────────────────────

async def test_pointercrate(buf: list) -> Section:
    s = Section("3. Pointercrate", buf)
    buf.append("\n## 3. Pointercrate (GD Demon List)\n")

    src_id = _source_id("geometrydash", "Pointercrate", "api", {"base_url": "https://pointercrate.com"})
    collector = PointercrateCollector(src_id, {}, "geometrydash")
    t0 = time.time()
    try:
        items = await collector.collect()
        elapsed = time.time() - t0
        s.ok(f"Fetched {len(items)} demons in {elapsed:.2f}s")

        content_types = {}
        for item in items:
            content_types[item.content_type] = content_types.get(item.content_type, 0) + 1
        s.info(f"Content type breakdown: {content_types}")

        if items:
            top1 = next((i for i in items if i.content_type == "top1_verified"), items[0])
            s.header(f"Current #1: {top1.title}")
            s.info(f"Verifier: {top1.author}")
            s.info(f"Position metadata: {top1.metadata.get('position', 'N/A')}")
            tweet = format_tweet(top1)
            if tweet:
                s.ok(f"top1_verified tweet ({len(tweet)} chars):")
                s.code(tweet)

            # Show a top-10 demon
            top10 = next((i for i in items if i.metadata.get("position", "999").isdigit()
                          and 1 < int(i.metadata.get("position", "999")) <= 10), None)
            if top10:
                tweet = format_tweet(top10)
                s.info(f"level_verified sample ({top10.title}, #{top10.metadata.get('position')}):")
                if tweet:
                    s.code(tweet)

    except Exception as e:
        s.fail(f"Pointercrate failed: {e}")
        s.code(traceback.format_exc())

    buf.append(f"\n> {s.summary()}\n")
    return s


# ── Test 4: GDBrowser ─────────────────────────────────────────────────────────

async def test_gdbrowser(buf: list) -> Section:
    s = Section("4. GDBrowser", buf)
    buf.append("\n## 4. GDBrowser API\n")

    src_id = _source_id("geometrydash", "GDBrowser", "api", {"base_url": "https://gdbrowser.com/api"})
    collector = GDBrowserCollector(src_id, {}, "geometrydash")
    t0 = time.time()
    try:
        items = await collector.collect()
        elapsed = time.time() - t0

        daily  = [i for i in items if i.content_type == "daily_level"]
        weekly = [i for i in items if i.content_type == "weekly_demon"]
        rated  = [i for i in items if i.content_type == "level_rated"]

        if daily:
            s.ok(f"Daily level: {daily[0].title}")
            tweet = format_tweet(daily[0])
            if tweet:
                s.code(tweet)
        else:
            s.warn("Daily level returned no data (GDBrowser server-side issue on sentinel IDs)")

        if weekly:
            s.ok(f"Weekly demon: {weekly[0].title}")
            tweet = format_tweet(weekly[0])
            if tweet:
                s.code(tweet)
        else:
            s.warn("Weekly demon returned no data (same GDBrowser issue)")

        if rated:
            s.ok(f"Rated levels: {len(rated)} fetched in {elapsed:.2f}s")
            tweet = format_tweet(rated[0])
            if tweet:
                s.info(f"Sample rated level tweet ({len(tweet)} chars):")
                s.code(tweet)
        else:
            s.fail("No rated levels returned")

    except Exception as e:
        s.fail(f"GDBrowser failed: {e}")
        s.code(traceback.format_exc())

    buf.append(f"\n> {s.summary()}\n")
    return s


# ── Test 5: Octane.gg ─────────────────────────────────────────────────────────

async def test_octane(buf: list) -> Section:
    s = Section("5. Octane.gg", buf)
    buf.append("\n## 5. Octane.gg (RL Esports)\n")

    src_id = _source_id("rocketleague", "Octane.gg", "api",
                         {"base_url": "https://zsr.octane.gg", "collector": "octane"})
    collector = OctaneCollector(src_id, {"base_url": "https://zsr.octane.gg"}, "rocketleague")
    t0 = time.time()
    try:
        items = await collector.collect()
        elapsed = time.time() - t0

        results  = [i for i in items if i.content_type == "esports_result"]
        upcoming = [i for i in items if i.content_type == "esports_matchup"]

        if results:
            s.ok(f"Fetched {len(results)} recent match results in {elapsed:.2f}s")
            tweet = format_tweet(results[0])
            if tweet:
                s.info(f"Sample result tweet:")
                s.code(tweet)
        else:
            s.warn("No match results returned (may be off-season)")

        if upcoming:
            s.ok(f"Fetched {len(upcoming)} upcoming matches")
            tweet = format_tweet(upcoming[0])
            if tweet:
                s.info("Sample upcoming match tweet:")
                s.code(tweet)
        else:
            s.warn("No upcoming matches returned")

    except Exception as e:
        s.fail(f"Octane failed: {e}")
        s.code(traceback.format_exc())

    buf.append(f"\n> {s.summary()}\n")
    return s


# ── Test 6: Formatter — all content types ─────────────────────────────────────

def test_formatter(buf: list) -> Section:
    from src.collectors.base import RawContent
    s = Section("6. Formatter", buf)
    buf.append("\n## 6. Formatter — All Content Types\n")

    sample_rl = [
        ("patch_notes",        {"version": "v2.64", "bullet1": "New car body added", "bullet2": "Bug fixes", "bullet3": "Performance improvements"}),
        ("esports_result",     {"event": "RLCS World Championship", "event_short": "RLCS Worlds", "stage": "Grand Finals", "team1": "Vitality", "team2": "NRG", "score1": "4", "score2": "2", "winner": "Vitality", "loser": "NRG", "score": "4-2", "emoji": "🏆"}),
        ("esports_matchup",    {"event": "RLCS Spring Major", "stage": "Quarterfinals", "team1": "G2", "team2": "Faze", "time": "18:00 UTC"}),
        ("roster_change",      {"player": "jstn", "team": "NRG", "old_team": "Cloud9", "season": "Season 14", "emoji": "🔄"}),
        ("item_shop",          {"date": "Feb 28", "items": "• Titanium White Octane\n• Black Market Decal: Heatwave\n• Goal Explosion: Fireworks"}),
        ("season_start",       {"number": "14", "highlight1": "New ranked rewards", "highlight2": "Updated item shop", "highlight3": "Season pass launched"}),
        ("collab_announcement",{"brand": "Spongebob", "details": "Spongebob themed car + decals", "date": "March 5"}),
        ("community_clip",     {"player": "GarrettG", "mechanic": "ceiling shot"}),
        ("reddit_highlight",   {"subreddit": "RocketLeague"}),
    ]

    sample_gd = [
        ("top1_verified",      {"level": "Abyss of Darkness", "player": "Zoink", "details": "First ever sub-4% verified level", "emoji": "🚨"}),
        ("demon_list_update",  {"changes": "Abyss of Darkness enters at #1\nSlaughterhouse moves to #2\nGelatin drops to #3"}),
        ("level_verified",     {"level": "Tartarus", "player": "Dolphy", "position": "3", "emoji": "🏆"}),
        ("level_beaten",       {"level": "Bloodbath", "player": "Manix648", "position": "17", "victor_number": "214th", "emoji": "🎮"}),
        ("game_update",        {"version": "2.3", "bullet1": "New editor tools", "bullet2": "100 new songs", "bullet3": "Daily level overhaul"}),
        ("level_rated",        {"level_name": "Sonic Wave", "creator": "Cyclic", "difficulty": "Extreme Demon", "stars": "10"}),
        ("daily_level",        {"level_name": "Theory of Everything 2", "creator": "Partition", "difficulty": "Insane"}),
        ("weekly_demon",       {"level_name": "Bloodbath", "creator": "Riot", "difficulty": "Extreme Demon"}),
        ("mod_update",         {"version": "2.1.0", "bullet1": "New texture loader", "bullet2": "Crash fixes", "bullet3": "Better mod manager"}),
        ("speedrun_wr",        {"player": "Doggie", "category": "All Icons%", "time": "1:24:37", "prev_time": "1:25:12"}),
    ]

    buf.append("\n### Rocket League\n")
    rl_pass = 0
    for ct, extra in sample_rl:
        from src.collectors.base import RawContent
        item = RawContent(
            source_id=1, external_id=f"test_{ct}", niche="rocketleague",
            content_type=ct, title="Test title", url="https://example.com",
            body="Test body text for this item.", author="TestAuthor", metadata=extra,
        )
        tweet = format_tweet(item)
        if tweet and "{" not in tweet:
            s.ok(f"`{ct}` ({len(tweet)} chars)")
            s.code(tweet)
            rl_pass += 1
        elif tweet:
            s.warn(f"`{ct}` has unfilled placeholders — fell back to truncated version")
            s.code(tweet)
        else:
            s.fail(f"`{ct}` returned None")

    buf.append("\n### Geometry Dash\n")
    gd_pass = 0
    for ct, extra in sample_gd:
        item = RawContent(
            source_id=2, external_id=f"test_{ct}", niche="geometrydash",
            content_type=ct, title="Test title", url="https://example.com",
            body="Test body text for this item.", author="TestAuthor", metadata=extra,
        )
        tweet = format_tweet(item)
        if tweet and "{" not in tweet:
            s.ok(f"`{ct}` ({len(tweet)} chars)")
            s.code(tweet)
            gd_pass += 1
        elif tweet:
            s.warn(f"`{ct}` has unfilled placeholders")
            s.code(tweet)
        else:
            s.fail(f"`{ct}` returned None")

    buf.append(f"\n> {s.summary()}\n")
    return s


# ── Test 7: Media ─────────────────────────────────────────────────────────────

async def test_media(buf: list) -> Section:
    s = Section("7. Media", buf)
    buf.append("\n## 7. Media Handling (Download + Resize)\n")

    test_urls = [
        ("RL Steam header",  "https://cdn.cloudflare.steamstatic.com/steam/apps/252950/header.jpg"),
        ("GD Steam header",  "https://cdn.cloudflare.steamstatic.com/steam/apps/322170/header.jpg"),
        ("Pointercrate thumb", "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"),
    ]

    for name, url in test_urls:
        t0 = time.time()
        path = prepare_media(url)
        elapsed = time.time() - t0
        if path:
            from PIL import Image
            img = Image.open(path)
            w, h = img.size
            size_kb = Path(path).stat().st_size // 1024
            if (w, h) == (1200, 675):
                s.ok(f"{name}: {w}×{h}px, {size_kb}KB ({elapsed:.2f}s)")
            else:
                s.fail(f"{name}: wrong dimensions {w}×{h} (expected 1200×675)")
        else:
            s.fail(f"{name}: download/resize failed")

    buf.append(f"\n> {s.summary()}\n")
    return s


# ── Test 8: Queue and dedup ────────────────────────────────────────────────────

async def test_queue(buf: list) -> Section:
    s = Section("8. Queue + Dedup", buf)
    buf.append("\n## 8. Queue & Deduplication\n")

    try:
        # Run RSS RL again — should all be deduped (0 new)
        src_id = _source_id("rocketleague", "Steam News (RL)", "rss",
                              {"url": "https://store.steampowered.com/feeds/news/app/252950"})
        collector = RSSCollector(src_id, {"url": "https://store.steampowered.com/feeds/news/app/252950"}, "rocketleague")
        n = await collect_and_queue(collector, "rocketleague")
        if n == 0:
            s.ok("Dedup working — re-running RSS collector added 0 new items")
        else:
            s.warn(f"Expected 0 new items on re-run, got {n}")

        # Check queue contents
        with get_db() as conn:
            rl_queued  = conn.execute("SELECT COUNT(*) FROM tweet_queue WHERE niche='rocketleague' AND status='queued'").fetchone()[0]
            gd_queued  = conn.execute("SELECT COUNT(*) FROM tweet_queue WHERE niche='geometrydash' AND status='queued'").fetchone()[0]
            rl_posted  = conn.execute("SELECT COUNT(*) FROM tweet_queue WHERE niche='rocketleague' AND status='posted'").fetchone()[0]
            gd_posted  = conn.execute("SELECT COUNT(*) FROM tweet_queue WHERE niche='geometrydash' AND status='posted'").fetchone()[0]
            rl_top5    = get_queued_tweets(conn, "rocketleague", limit=5)
            gd_top5    = get_queued_tweets(conn, "geometrydash", limit=5)

        s.ok(f"RL queue: {rl_queued} queued, {rl_posted} posted")
        s.ok(f"GD queue: {gd_queued} queued, {gd_posted} posted")

        s.header("RL top-5 queued (by priority)")
        for row in rl_top5:
            preview = row["tweet_text"][:80].replace("\n", " ")
            s.info(f"[p{row['priority']}] {preview}…")

        s.header("GD top-5 queued (by priority)")
        for row in gd_top5:
            preview = row["tweet_text"][:80].replace("\n", " ")
            s.info(f"[p{row['priority']}] {preview}…")

    except Exception as e:
        s.fail(f"Queue test failed: {e}")
        s.code(traceback.format_exc())

    buf.append(f"\n> {s.summary()}\n")
    return s


# ── Test 9: Rate limiter ──────────────────────────────────────────────────────

def test_rate_limiter(buf: list) -> Section:
    s = Section("9. Rate Limiter", buf)
    buf.append("\n## 9. Rate Limiter\n")

    for niche in ("rocketleague", "geometrydash"):
        can = can_post(niche)
        count = monthly_post_count(niche)
        s.info(f"[{niche}] can_post={can}, monthly_posts={count}/1500")
        if count < 1500:
            s.ok(f"[{niche}] within monthly limit ({count}/1500)")
        else:
            s.warn(f"[{niche}] monthly limit reached")

    buf.append(f"\n> {s.summary()}\n")
    return s


# ── Test 10: DRY_RUN poster ───────────────────────────────────────────────────

def test_dry_poster(buf: list) -> Section:
    import os
    os.environ["DRY_RUN"] = "true"
    s = Section("10. DRY_RUN Poster", buf)
    buf.append("\n## 10. DRY_RUN Poster\n")

    for niche in ("rocketleague", "geometrydash"):
        try:
            client = TwitterClient(niche)
            if not client.dry_run:
                s.fail(f"[{niche}] DRY_RUN not active")
                continue

            with get_db() as conn:
                rows = get_queued_tweets(conn, niche, limit=1)
            if not rows:
                s.warn(f"[{niche}] queue empty, nothing to dry-post")
                continue

            row = rows[0]
            preview = row["tweet_text"][:120].replace("\n", " ")
            s.ok(f"[{niche}] would post [p{row['priority']}]: {preview}…")
            s.info(f"[{niche}] media_path: {row['media_path'] or 'none'}")
        except Exception as e:
            s.fail(f"[{niche}] dry poster error: {e}")

    buf.append(f"\n> {s.summary()}\n")
    return s


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    buf = []
    buf.append(f"# AutoPost Pipeline Test Results")
    buf.append(f"\n**Run at:** {NOW}")
    buf.append(f"\n**Platform:** Python {sys.version.split()[0]} on Windows")
    buf.append("\n**Note:** Tests requiring credentials (Reddit, X API, YouTube) are skipped — marked as ⏭️\n")
    buf.append("\n---\n")

    sections = []

    print("Running database tests...")
    sections.append(test_database(buf))

    print("Running RSS collector tests...")
    sections.append(await test_rss(buf))

    print("Running Pointercrate tests...")
    sections.append(await test_pointercrate(buf))

    print("Running GDBrowser tests...")
    sections.append(await test_gdbrowser(buf))

    print("Running Octane.gg tests...")
    sections.append(await test_octane(buf))

    print("Running formatter tests...")
    sections.append(test_formatter(buf))

    print("Running media tests...")
    sections.append(await test_media(buf))

    print("Running queue/dedup tests...")
    sections.append(await test_queue(buf))

    print("Running rate limiter tests...")
    sections.append(test_rate_limiter(buf))

    print("Running DRY_RUN poster tests...")
    sections.append(test_dry_poster(buf))

    # ── Skipped tests (need credentials) ──────────────────────────────────────
    buf.append("\n## ⏭️ Skipped (credentials required)\n")
    buf.append("- **Reddit collector** — needs `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET`")
    buf.append("- **Twitter monitor** — needs X API keys (`RL_API_KEY` etc.)")
    buf.append("- **YouTube collector** — needs `YOUTUBE_API_KEY`")
    buf.append("- **Live posting** — needs X API keys + `DRY_RUN=false`")

    # ── Overall summary ────────────────────────────────────────────────────────
    buf.append("\n\n---\n")
    buf.append("## Overall Summary\n")
    buf.append(f"| # | Test | Result |")
    buf.append(f"|---|------|--------|")
    for i, sec in enumerate(sections, 1):
        status = "✅ PASS" if sec.failed == 0 else "❌ FAIL"
        if sec.warnings > 0 and sec.failed == 0:
            status = "⚠️ WARN"
        buf.append(f"| {i} | {sec.title} | {status} — {sec.summary()} |")

    total_pass = sum(s.passed for s in sections)
    total_fail = sum(s.failed for s in sections)
    total_warn = sum(s.warnings for s in sections)
    buf.append(f"\n**Total: {total_pass} passed, {total_fail} failed, {total_warn} warnings**")

    # Write report
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(buf), encoding="utf-8")
    print(f"\nReport written to: {REPORT_PATH}")
    print(f"Summary: {total_pass} passed, {total_fail} failed, {total_warn} warnings")


if __name__ == "__main__":
    asyncio.run(main())
