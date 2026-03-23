"""
RL stat milestone collector — checks Octane ZSR for all-time stat leaders
and generates tweets when notable players hold impressive records.

Runs weekly (poll_interval = 604800 in the YAML config).
Produces content_type = "stat_milestone" (priority 7, filler).

Unlike the flashback collector, this doesn't look at historical dates —
it queries the current all-time leaderboard and picks interesting stats
that are worth tweeting about.
"""
from datetime import datetime, timezone

import httpx
from loguru import logger

from src.collectors.base import BaseCollector, RawContent

# Octane ZSR has broken TLS/SNI — HTTP only (public read-only API, no sensitive data)
_OCTANE_BASE = "http://zsr.octane.gg"
_TIMEOUT     = 15

# Stats to check with human-readable labels
_STAT_CATEGORIES = [
    ("goals",   "goals"),
    ("assists", "assists"),
    ("saves",   "saves"),
    ("shots",   "shots"),
    ("score",   "total score"),
]


class RLStatsCollector(BaseCollector):
    """Fetches all-time stat leaders from Octane ZSR and generates milestone tweets."""

    def __init__(self, source_id: int, config: dict, niche: str = "rocketleague"):
        super().__init__(source_id, config)
        self.niche = niche

    async def collect(self) -> list[RawContent]:
        items: list[RawContent] = []

        for stat_key, stat_label in _STAT_CATEGORIES:
            leaders = await _fetch_stat_leaders(stat_key)
            if not leaders:
                continue

            # Take top 3 and generate a leaderboard tweet
            top3 = leaders[:3]
            if len(top3) < 3:
                continue

            names = [_player_name(p) for p in top3]
            values = [_player_stat(p, stat_key) for p in top3]

            headline = (
                f"All-time RLCS {stat_label} leaders (S-tier events):\n\n"
                f"1. {names[0]} — {values[0]:,}\n"
                f"2. {names[1]} — {values[1]:,}\n"
                f"3. {names[2]} — {values[2]:,}"
            )

            # Use a date-based external_id so this only fires once per week
            week = datetime.now(timezone.utc).strftime("%Y-W%W")
            items.append(RawContent(
                source_id    = self.source_id,
                external_id  = f"rl_stat_{stat_key}_{week}",
                niche        = self.niche,
                content_type = "stat_milestone",
                title        = headline,
                url          = "",
                body         = "",
                image_url    = "",
                author       = names[0],
                score        = 0,
                metadata     = {
                    "headline": headline,
                    "details":  f"All-time {stat_label} leaders across S-tier RLCS events.",
                    "player":   names[0],
                    "stat":     stat_label,
                    "value":    str(values[0]),
                },
            ))

            # Only generate 1 stat tweet per cycle to avoid spam
            break

        if items:
            logger.info(f"[RLStats] generated {len(items)} stat milestone items")
        return items


async def _fetch_stat_leaders(stat: str) -> list[dict]:
    """Fetch top 10 all-time leaders for a given stat from Octane ZSR."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_OCTANE_BASE}/stats/players",
                params={
                    "tier": "S",
                    "stat": stat,
                    "sort": stat,
                    "order": "desc",
                    "perPage": 10,
                    "minGames": 50,
                },
            )
            if resp.status_code != 200:
                return []
            if "application/json" not in resp.headers.get("content-type", ""):
                return []
            data = resp.json()
            return data.get("stats", [])
    except Exception as exc:
        logger.error(f"[RLStats] failed to fetch {stat} leaders: {exc}")
        return []


def _player_name(entry: dict) -> str:
    player = entry.get("player", {})
    return player.get("tag", "Unknown")


def _player_stat(entry: dict, stat: str) -> int:
    stats = entry.get("stats", {})
    # Octane returns stats nested under "core"
    core = stats.get("core", {})
    return int(core.get(stat, 0))
