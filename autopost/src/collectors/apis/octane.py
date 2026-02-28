"""
Octane.gg collector — RL esports results, upcoming matches, and events.
Public API (zsr.octane.gg), no authentication required.

API reference: https://zsr.octane.gg/
"""
from datetime import datetime, timezone

import httpx
from loguru import logger

from src.collectors.base import BaseCollector, RawContent

_BASE_URL = "https://zsr.octane.gg"
_TIMEOUT  = 15


class OctaneCollector(BaseCollector):
    """
    Fetches recent match results and upcoming matches from Octane.gg.
    config keys (from YAML): base_url, poll_interval
    """

    def __init__(self, source_id: int, config: dict, niche: str = "rocketleague"):
        super().__init__(source_id, config)
        self.niche    = niche
        self.base_url = config.get("base_url", _BASE_URL)

    async def collect(self) -> list[RawContent]:
        items: list[RawContent] = []

        async with httpx.AsyncClient(base_url=self.base_url, timeout=_TIMEOUT) as client:
            results  = await _fetch_results(client, self.source_id, self.niche)
            upcoming = await _fetch_upcoming(client, self.source_id, self.niche)

        items.extend(results)
        items.extend(upcoming)
        logger.info(
            f"[Octane] {len(results)} results + {len(upcoming)} upcoming matches"
        )
        return items


# ── Fetchers ───────────────────────────────────────────────────────────────────

async def _fetch_results(
    client: httpx.AsyncClient, source_id: int, niche: str
) -> list[RawContent]:
    try:
        resp = await client.get(
            "/matches",
            params={"tier": "S,A", "page": 1, "perPage": 10, "sort": "date:desc"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error(f"[Octane] results fetch failed: {exc}")
        return []

    items: list[RawContent] = []
    for match in data.get("matches", []):
        # Only process completed matches
        if not match.get("score"):
            continue

        match_id  = match.get("_id", "")
        blue      = match.get("blue", {})
        orange    = match.get("orange", {})
        blue_name = (blue.get("team") or {}).get("team", {}).get("name", "Blue")
        org_name  = (orange.get("team") or {}).get("team", {}).get("name", "Orange")
        blue_score   = (blue.get("score") or 0)
        orange_score = (orange.get("score") or 0)

        winner = blue_name if blue_score > orange_score else org_name
        loser  = org_name if blue_score > orange_score else blue_name
        score  = f"{max(blue_score, orange_score)}-{min(blue_score, orange_score)}"

        event      = (match.get("event") or {}).get("name", "RLCS")
        event_short = event[:20] if len(event) > 20 else event
        stage      = (match.get("stage") or {}).get("name", "")

        items.append(RawContent(
            source_id    = source_id,
            external_id  = f"octane_result_{match_id}",
            niche        = niche,
            content_type = "esports_result",
            title        = f"{winner} def. {loser} {score} at {event}",
            url          = f"https://octane.gg/matches/{match_id}",
            body         = f"{event} — {stage}",
            image_url    = "",
            author       = "",
            score        = 0,
            metadata     = {
                "event":       event,
                "event_short": event_short,
                "stage":       stage,
                "team1":       blue_name,
                "team2":       org_name,
                "score1":      str(blue_score),
                "score2":      str(orange_score),
                "winner":      winner,
                "loser":       loser,
                "score":       score,
                "emoji":       "🏆",
            },
        ))

    return items


async def _fetch_upcoming(
    client: httpx.AsyncClient, source_id: int, niche: str
) -> list[RawContent]:
    try:
        resp = await client.get(
            "/matches",
            params={"tier": "S,A", "page": 1, "perPage": 5, "sort": "date:asc", "after": _today()},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error(f"[Octane] upcoming fetch failed: {exc}")
        return []

    items: list[RawContent] = []
    for match in data.get("matches", []):
        match_id   = match.get("_id", "")
        blue       = match.get("blue", {})
        orange     = match.get("orange", {})
        blue_name  = (blue.get("team") or {}).get("team", {}).get("name", "TBD")
        org_name   = (orange.get("team") or {}).get("team", {}).get("name", "TBD")
        event      = (match.get("event") or {}).get("name", "RLCS")
        stage      = (match.get("stage") or {}).get("name", "")
        start_date = match.get("date", "")[:16].replace("T", " ") + " UTC" if match.get("date") else "TBD"

        items.append(RawContent(
            source_id    = source_id,
            external_id  = f"octane_upcoming_{match_id}",
            niche        = niche,
            content_type = "esports_matchup",
            title        = f"{blue_name} vs {org_name} — {event}",
            url          = f"https://octane.gg/matches/{match_id}",
            body         = f"{event} — {stage}",
            image_url    = "",
            author       = "",
            score        = 0,
            metadata     = {
                "event": event,
                "stage": stage,
                "team1": blue_name,
                "team2": org_name,
                "time":  start_date,
            },
        ))

    return items


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
