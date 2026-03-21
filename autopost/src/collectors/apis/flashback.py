"""
RL Flashback collector — generates "on this day" and stat milestone tweets.

Two content sources:
  1. Static YAML file (data/rl_history.yaml) with dated RLCS events.
  2. Octane ZSR API — queries historical S-tier matches for today's date
     across all past years.

Produces content_type = "flashback" (new type, priority 6 — filler territory).
Runs once per day (poll_interval = 86400 in the YAML config).
"""
from datetime import datetime, timezone

import httpx
import yaml
from loguru import logger
from pathlib import Path

from src.collectors.base import BaseCollector, RawContent

_HISTORY_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "rl_history.yaml"
_OCTANE_BASE  = "https://zsr.octane.gg"
_TIMEOUT      = 15


class FlashbackCollector(BaseCollector):
    """Produces 0-2 flashback items per day for the RL niche."""

    def __init__(self, source_id: int, config: dict, niche: str = "rocketleague"):
        super().__init__(source_id, config)
        self.niche = niche

    async def collect(self) -> list[RawContent]:
        today = datetime.now(timezone.utc)
        month_day = (today.month, today.day)
        items: list[RawContent] = []

        # Source 1: static history file
        static = _load_static_events(month_day)
        for evt in static:
            items.append(RawContent(
                source_id    = self.source_id,
                external_id  = f"flashback_{evt['date']}_{evt['event'][:20]}",
                niche        = self.niche,
                content_type = "flashback",
                title        = evt["headline"],
                url          = evt.get("url", ""),
                body         = evt.get("details", ""),
                image_url    = evt.get("image_url", ""),
                author       = "",
                score        = 0,
                metadata     = {
                    "headline":    evt["headline"],
                    "details":     evt.get("details", ""),
                    "years_ago":   str(today.year - evt["year"]),
                    "year":        str(evt["year"]),
                    "event":       evt.get("event", ""),
                    "winner":      evt.get("winner", ""),
                    "loser":       evt.get("loser", ""),
                    "score":       evt.get("score", ""),
                    "url":         evt.get("url", ""),
                },
            ))

        # Source 2: Octane ZSR API — S-tier matches on this calendar date
        api_items = await _fetch_octane_flashbacks(today, self.source_id, self.niche)
        items.extend(api_items)

        if items:
            logger.info(f"[Flashback] {len(items)} flashback items for {today.strftime('%m-%d')}")
        return items


def _load_static_events(month_day: tuple[int, int]) -> list[dict]:
    """Load events from rl_history.yaml that match today's month/day."""
    if not _HISTORY_PATH.exists():
        return []
    try:
        data = yaml.safe_load(_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"[Flashback] failed to load {_HISTORY_PATH}: {exc}")
        return []

    events = []
    for evt in (data or {}).get("events", []):
        date_str = evt.get("date", "")
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            if (dt.month, dt.day) == month_day:
                evt["year"] = dt.year
                events.append(evt)
        except ValueError:
            continue
    return events


async def _fetch_octane_flashbacks(
    today: datetime, source_id: int, niche: str
) -> list[RawContent]:
    """Query Octane for S-tier matches that happened on this date in past years."""
    items: list[RawContent] = []
    month_day = today.strftime("%m-%d")

    # Check each year from 2016 to last year
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for year in range(2016, today.year):
            date_str = f"{year}-{month_day}"
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue  # skip Feb 29 on non-leap years

            try:
                resp = await client.get(
                    f"{_OCTANE_BASE}/matches",
                    params={
                        "tier": "S",
                        "after": f"{date_str}T00:00:00Z",
                        "before": f"{date_str}T23:59:59Z",
                        "perPage": 5,
                        "sort": "date:desc",
                    },
                )
                if resp.status_code != 200:
                    continue
                if "application/json" not in resp.headers.get("content-type", ""):
                    continue
                data = resp.json()
            except Exception:
                continue

            for match in data.get("matches", []):
                if not match.get("score"):
                    continue

                match_id = match.get("_id", "")
                blue = match.get("blue", {})
                orange = match.get("orange", {})
                blue_name = (blue.get("team") or {}).get("team", {}).get("name", "")
                org_name = (orange.get("team") or {}).get("team", {}).get("name", "")
                blue_score = blue.get("score") or 0
                orange_score = orange.get("score") or 0

                if not blue_name or not org_name:
                    continue

                winner = blue_name if blue_score > orange_score else org_name
                loser = org_name if blue_score > orange_score else blue_name
                score_str = f"{max(blue_score, orange_score)}-{min(blue_score, orange_score)}"
                event = (match.get("event") or {}).get("name", "RLCS")
                stage = (match.get("stage") or {}).get("name", "")
                years_ago = today.year - year

                headline = (
                    f"On this day in {year}, {winner} defeated {loser} {score_str} "
                    f"at {event}" + (f" ({stage})" if stage else "") + "."
                )

                items.append(RawContent(
                    source_id    = source_id,
                    external_id  = f"flashback_octane_{match_id}",
                    niche        = niche,
                    content_type = "flashback",
                    title        = headline,
                    url          = f"https://octane.gg/matches/{match_id}",
                    body         = "",
                    image_url    = "",
                    author       = "",
                    score        = 0,
                    metadata     = {
                        "headline":  headline,
                        "details":   f"{event} — {stage}" if stage else event,
                        "years_ago": str(years_ago),
                        "year":      str(year),
                        "event":     event,
                        "winner":    winner,
                        "loser":     loser,
                        "score":     score_str,
                    },
                ))

                # Only take the most notable match per year
                break

    return items
