"""
Daily source health check — probes every enabled source to verify it's
reachable and returning data.  Sends a summary report to Discord.

Runs at 03:00 UTC via APScheduler cron job (see main.py).
"""
import json
import re
from dataclasses import dataclass

import feedparser
import httpx
from loguru import logger

from config.settings import YOUTUBE_API_KEY
from src.database.db import get_db
from src.monitoring.alerts import send_alert

_TIMEOUT = 12
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}

_SYNDICATION_URL = (
    "https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}"
)
_NEXT_DATA_RE = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)


@dataclass
class ProbeResult:
    source_name: str
    niche: str
    source_type: str
    status: str          # "healthy", "degraded", "dead"
    detail: str = ""     # short reason if not healthy


# ── Probes per source type ─────────────────────────────────────────────────────

async def _probe_twitter(config: dict, client: httpx.AsyncClient) -> tuple[str, str]:
    username = config.get("account_id", "")
    url = _SYNDICATION_URL.format(username=username)
    resp = await client.get(url)
    if resp.status_code == 429:
        return "healthy", "rate-limited (normal)"
    resp.raise_for_status()
    m = _NEXT_DATA_RE.search(resp.text)
    if not m:
        return "degraded", "no __NEXT_DATA__ in response"
    data = json.loads(m.group(1))
    entries = (
        data.get("props", {})
        .get("pageProps", {})
        .get("timeline", {})
        .get("entries", [])
    )
    if not entries:
        return "degraded", "0 timeline entries"
    return "healthy", f"{len(entries)} entries"


async def _probe_youtube(config: dict, client: httpx.AsyncClient) -> tuple[str, str]:
    channel_id = config.get("channel_id", "")
    if not YOUTUBE_API_KEY:
        return "degraded", "no API key"
    resp = await client.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "id", "id": channel_id, "key": YOUTUBE_API_KEY},
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        return "dead", f"channel {channel_id} not found"
    return "healthy", ""


async def _probe_rss(config: dict, client: httpx.AsyncClient) -> tuple[str, str]:
    url = config.get("url", "")
    resp = await client.get(url)
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)
    if feed.bozo and not feed.entries:
        return "degraded", "malformed feed, 0 entries"
    if not feed.entries:
        return "degraded", "0 entries"
    return "healthy", f"{len(feed.entries)} entries"


async def _probe_scraper(config: dict, client: httpx.AsyncClient) -> tuple[str, str]:
    url = config.get("url", "")
    resp = await client.get(url)
    resp.raise_for_status()
    length = len(resp.text)
    if length < 500:
        return "degraded", f"tiny response ({length} bytes)"
    return "healthy", ""


async def _probe_api(config: dict, client: httpx.AsyncClient) -> tuple[str, str]:
    collector = config.get("collector", "")
    if collector == "pointercrate":
        resp = await client.get(
            "https://pointercrate.com/api/v2/demons/listed",
            params={"limit": 1},
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return "degraded", "empty response"
        return "healthy", ""

    if collector == "gdbrowser":
        resp = await client.get("https://gdbrowser.com/api/search/*", params={"count": 1})
        if resp.status_code >= 500:
            return "degraded", f"HTTP {resp.status_code}"
        resp.raise_for_status()
        return "healthy", ""

    if collector == "github":
        repo = config.get("repo", "")
        resp = await client.get(
            f"https://api.github.com/repos/{repo}/releases",
            params={"per_page": 1},
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        return "healthy", ""

    if collector in ("flashback", "rl_stats"):
        # These are internal generators, no external dependency
        return "healthy", "internal"

    return "degraded", f"unknown collector: {collector}"


_PROBE_MAP = {
    "twitter": _probe_twitter,
    "youtube": _probe_youtube,
    "rss":     _probe_rss,
    "scraper": _probe_scraper,
    "api":     _probe_api,
}


# ── Main health check ─────────────────────────────────────────────────────────

async def run_health_check() -> None:
    """Probe all enabled sources and send a Discord report."""
    logger.info("[HealthCheck] Starting daily source health check")

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, niche, name, type, config, enabled FROM sources ORDER BY niche, type, name"
        ).fetchall()

    results: list[ProbeResult] = []

    async with httpx.AsyncClient(
        headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True
    ) as client:
        for row in rows:
            name = row["name"]
            niche = row["niche"]
            stype = row["type"]
            enabled = row["enabled"]
            config = json.loads(row["config"])

            if not enabled:
                results.append(ProbeResult(name, niche, stype, "dead", "disabled in DB"))
                continue

            probe = _PROBE_MAP.get(stype)
            if not probe:
                results.append(ProbeResult(name, niche, stype, "degraded", f"no probe for type '{stype}'"))
                continue

            try:
                status, detail = await probe(config, client)
                results.append(ProbeResult(name, niche, stype, status, detail))
            except httpx.HTTPStatusError as exc:
                results.append(ProbeResult(name, niche, stype, "dead", f"HTTP {exc.response.status_code}"))
            except Exception as exc:
                err = str(exc)[:80]
                results.append(ProbeResult(name, niche, stype, "dead", err))

    # Build report
    healthy  = [r for r in results if r.status == "healthy"]
    degraded = [r for r in results if r.status == "degraded"]
    dead     = [r for r in results if r.status == "dead"]

    report_lines = [
        f"**Daily Source Health Check** — {len(results)} sources probed\n",
    ]

    if healthy:
        names = ", ".join(f"`{r.source_name}`" for r in healthy)
        report_lines.append(f"**Healthy ({len(healthy)}):** {names}\n")

    if degraded:
        items = "\n".join(f"- `{r.source_name}` [{r.niche}] — {r.detail}" for r in degraded)
        report_lines.append(f"**Degraded ({len(degraded)}):**\n{items}\n")

    if dead:
        items = "\n".join(f"- `{r.source_name}` [{r.niche}] — {r.detail}" for r in dead)
        report_lines.append(f"**Dead ({len(dead)}):**\n{items}\n")

    if dead:
        report_lines.append(f"**Action needed:** {len(dead)} dead source(s)")

    report = "\n".join(report_lines)

    # Log it
    logger.info(f"[HealthCheck] Results: {len(healthy)} healthy, {len(degraded)} degraded, {len(dead)} dead")

    # Choose alert level based on worst status
    if dead:
        level = "error"
    elif degraded:
        level = "warning"
    else:
        level = "success"

    await send_alert(report, level=level)
    logger.info("[HealthCheck] Report sent to Discord")
