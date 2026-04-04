"""
GDBrowser collector — daily level, weekly demon, and recently rated levels.

Primary: GDBrowser API (https://gdbrowser.com/api/)
Fallback: Official GD servers (boomlings.com) when GDBrowser is unavailable.

The official GD server API uses POST with form-encoded data and returns
colon-delimited key-value responses. We parse these as a fallback so the
bot can still post daily/weekly levels even when GDBrowser is down.
"""
import base64
from datetime import datetime, timezone

import httpx
from loguru import logger

from src.collectors.base import BaseCollector, RawContent

_GDBROWSER_URL = "https://gdbrowser.com/api"
_OFFICIAL_URL = "http://www.boomlings.com/database"
# Public GD game-client protocol value — not a private credential.
# Required by the official GD server API; same value used by the GD game itself.
_GD_PROTOCOL_SECRET = "Wmfd2893gb7"
_TIMEOUT = 15

# ── Notable creators ────────────────────────────────────────────────────────
_NOTABLE_CREATORS: set[str] = {s.lower() for s in (
    "Viprin", "Serponge", "Michigun", "KrMaL", "Zobros", "Knobbelboy",
    "Manix648", "Dorami", "Cyclic", "Xender Game", "AeonAir", "SpKale",
    "Colon", "Wulzy", "Riot", "Culuc", "TriAxis", "Juniper",
    "SrGuillester", "RobTop",
    "OniLink", "CairoX", "MindCap", "Narwall", "iMist", "Kiba",
    "Renn241", "Akunakunn", "Insxne97", "Cursed", "Sailent",
    "nikroplays", "PockeWindfish", "ryamu", "xander556", "Arraegen",
    "Exen", "GXQ", "SyQual", "icedcave", "Lavatrex", "McCoco",
    "Diamond", "ItsHybrid", "hawkyre", "Trick", "Amplitron",
    "stellar", "APTeamOfficial", "Linear", "Cersia", "LordVadercraft",
    "Nexel", "Dolabill", "HushLC", "Enfur", "Wahffle", "Dolphy",
    "Zeniux", "Drakosa", "DeniPol",
    "Bo", "Bianox", "Ggb0y", "Muffy450", "Rustam", "ILRELL",
    "RedUniverse", "Komp", "Sohn0924", "Mulpan", "Pennutoh",
    "Stormfly", "FunnyGame", "npesta", "paqoe", "nSwish", "Zoink",
    "Doggie", "Technical", "Xanii", "Sunix", "Giron",
)}

# Numeric difficulty → human-readable label
_DIFFICULTY: dict[int, str] = {
    0: "N/A", 1: "Easy", 2: "Normal", 3: "Hard",
    4: "Harder", 5: "Insane", 6: "Easy Demon", 7: "Medium Demon",
    8: "Hard Demon", 9: "Insane Demon", 10: "Extreme Demon",
}
_DIFFICULTY_STR = {v: v for v in _DIFFICULTY.values()}

# Official API demon difficulty → label (key 43)
_DEMON_DIFF: dict[int, str] = {
    3: "Easy Demon", 4: "Medium Demon", 0: "Hard Demon",
    5: "Insane Demon", 6: "Extreme Demon",
}

# Official API face difficulty → label (key 9, when not demon)
_FACE_DIFF: dict[int, str] = {
    0: "N/A", 10: "Easy", 20: "Normal", 30: "Hard",
    40: "Harder", 50: "Insane",
}


def _parse_difficulty(val) -> str:
    if isinstance(val, str):
        return _DIFFICULTY_STR.get(val, val)
    try:
        return _DIFFICULTY.get(int(val), "Unknown")
    except (ValueError, TypeError):
        return "Unknown"


def _parse_official_response(raw: str) -> dict[str, str]:
    """Parse colon-delimited key-value response from official GD servers."""
    parts = raw.split(":")
    result = {}
    for i in range(0, len(parts) - 1, 2):
        result[parts[i]] = parts[i + 1]
    return result


def _official_difficulty(data: dict[str, str]) -> str:
    """Derive human-readable difficulty from official API response keys."""
    is_auto = data.get("25", "0") == "1"
    if is_auto:
        return "Auto"
    is_demon = data.get("17", "0") == "1"
    if is_demon:
        demon_val = int(data.get("43", "0"))
        return _DEMON_DIFF.get(demon_val, "Hard Demon")
    face_val = int(data.get("9", "0"))
    return _FACE_DIFF.get(face_val, "N/A")


def _decode_b64(s: str) -> str:
    """Decode base64 description, ignoring errors."""
    try:
        return base64.urlsafe_b64decode(s + "==").decode("utf-8", errors="replace")
    except Exception:
        return ""


class GDBrowserCollector(BaseCollector):
    """
    Collects daily level, weekly demon, and recently rated levels.
    Tries GDBrowser first, falls back to official GD servers.
    """

    def __init__(self, source_id: int, config: dict, niche: str = "geometrydash"):
        super().__init__(source_id, config)
        self.niche = niche

    async def collect(self) -> list[RawContent]:
        items: list[RawContent] = []

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            daily = await _fetch_daily_gdbrowser(client, self.source_id, self.niche)
            if not daily:
                daily = await _fetch_daily_official(client, self.source_id, self.niche)

            weekly = await _fetch_weekly_gdbrowser(client, self.source_id, self.niche)
            if not weekly:
                weekly = await _fetch_weekly_official(client, self.source_id, self.niche)

            rated = await _fetch_rated(client, self.source_id, self.niche)

        if daily:
            items.append(daily)
        if weekly:
            items.append(weekly)
        items.extend(rated)

        logger.info(
            f"[GDBrowser] collected {len(items)} items "
            f"(daily={'yes' if daily else 'no'}, "
            f"weekly={'yes' if weekly else 'no'}, "
            f"rated={len(rated)})"
        )
        return items


# ── GDBrowser API fetchers ────────────────────────────────────────────────────

async def _fetch_daily_gdbrowser(
    client: httpx.AsyncClient, source_id: int, niche: str
) -> RawContent | None:
    try:
        resp = await client.get(f"{_GDBROWSER_URL}/level/-1")
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug(f"[GDBrowser] daily fetch failed (trying fallback): {exc}")
        return None

    return _make_daily_content(
        source_id, niche,
        level_id=str(data.get("id", "unknown")),
        name=data.get("name", "Unknown"),
        author=data.get("author", "Unknown"),
        difficulty=_parse_difficulty(data.get("difficulty", 0)),
        stars=data.get("stars", 0),
        likes=int(data.get("likes", 0)),
    )


async def _fetch_weekly_gdbrowser(
    client: httpx.AsyncClient, source_id: int, niche: str
) -> RawContent | None:
    try:
        resp = await client.get(f"{_GDBROWSER_URL}/level/-2")
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug(f"[GDBrowser] weekly fetch failed (trying fallback): {exc}")
        return None

    return _make_weekly_content(
        source_id, niche,
        level_id=str(data.get("id", "unknown")),
        name=data.get("name", "Unknown"),
        author=data.get("author", "Unknown"),
        difficulty=_parse_difficulty(data.get("difficulty", 0)),
        stars=data.get("stars", 0),
        likes=int(data.get("likes", 0)),
    )


# ── Official GD server fallbacks ─────────────────────────────────────────────

async def _fetch_daily_official(
    client: httpx.AsyncClient, source_id: int, niche: str
) -> RawContent | None:
    try:
        resp = await client.post(
            f"{_OFFICIAL_URL}/getGJDailyLevel.php",
            data={"secret": _GD_PROTOCOL_SECRET},
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "User-Agent": ""},
        )
        resp.raise_for_status()
        body = resp.text.strip()
        if body == "-1" or "|" not in body or "error" in body.lower():
            logger.debug("[GDBrowser/official] daily level unavailable")
            return None
        level_id = body.split("|")[0]

        return await _download_level_official(client, source_id, niche, level_id, "daily")
    except Exception as exc:
        logger.debug(f"[GDBrowser/official] daily fallback unavailable: {exc}")
        return None


async def _fetch_weekly_official(
    client: httpx.AsyncClient, source_id: int, niche: str
) -> RawContent | None:
    try:
        resp = await client.post(
            f"{_OFFICIAL_URL}/getGJDailyLevel.php",
            data={"secret": _GD_PROTOCOL_SECRET, "weekly": "1"},
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "User-Agent": ""},
        )
        resp.raise_for_status()
        body = resp.text.strip()
        if body == "-1" or "|" not in body or "error" in body.lower():
            logger.debug("[GDBrowser/official] weekly level unavailable")
            return None
        level_id = body.split("|")[0]

        return await _download_level_official(client, source_id, niche, level_id, "weekly")
    except Exception as exc:
        logger.debug(f"[GDBrowser/official] weekly fallback unavailable: {exc}")
        return None


async def _download_level_official(
    client: httpx.AsyncClient, source_id: int, niche: str,
    level_id: str, kind: str,
) -> RawContent | None:
    """Download level details from official GD servers and convert to RawContent."""
    resp = await client.post(
        f"{_OFFICIAL_URL}/downloadGJLevel22.php",
        data={"secret": _GD_PROTOCOL_SECRET, "levelID": level_id},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    raw_body = resp.text.strip()
    if raw_body == "-1":
        return None

    # Response may contain hash segments after #; level data is before first #
    level_data_str = raw_body.split("#")[0]
    data = _parse_official_response(level_data_str)

    name = data.get("2", "Unknown")
    author = data.get("creator", "Unknown")
    # Author name isn't in the level download response — use player ID as fallback
    # The downloadGJLevel22 response includes creator info after the # separators
    # Try extracting from the full response
    parts = raw_body.split("#")
    if len(parts) >= 4 and parts[3]:
        # Creator name is in the 4th segment (index 3)
        creator_parts = parts[3].split(":")
        if len(creator_parts) >= 2:
            author = creator_parts[1] if creator_parts[1] else author

    difficulty = _official_difficulty(data)
    stars = int(data.get("18", "0"))
    likes = int(data.get("14", "0"))
    lid = data.get("1", level_id)

    logger.info(f"[GDBrowser/official] {kind} level: {name} (ID {lid}) via official API")

    if kind == "daily":
        return _make_daily_content(source_id, niche, lid, name, author, difficulty, stars, likes)
    return _make_weekly_content(source_id, niche, lid, name, author, difficulty, stars, likes)


# ── Content builders ──────────────────────────────────────────────────────────

def _make_daily_content(
    source_id: int, niche: str, level_id: str, name: str,
    author: str, difficulty: str, stars: int, likes: int,
) -> RawContent:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return RawContent(
        source_id=source_id,
        external_id=f"daily_{today}_{level_id}",
        niche=niche,
        content_type="daily_level",
        title=f"Daily Level: {name} by {author}",
        url=f"https://gdbrowser.com/{level_id}",
        body=f"{difficulty} — {stars} stars",
        image_url="",
        author=author,
        score=likes,
        metadata={
            "level_name": name, "creator": author,
            "difficulty": difficulty, "stars": str(stars),
            "level_id": str(level_id),
        },
    )


def _make_weekly_content(
    source_id: int, niche: str, level_id: str, name: str,
    author: str, difficulty: str, stars: int, likes: int,
) -> RawContent:
    week_num = datetime.now(timezone.utc).strftime("%Y-W%W")
    return RawContent(
        source_id=source_id,
        external_id=f"weekly_{week_num}_{level_id}",
        niche=niche,
        content_type="weekly_demon",
        title=f"Weekly Demon: {name} by {author}",
        url=f"https://gdbrowser.com/{level_id}",
        body=f"{difficulty} — {stars} stars",
        image_url="",
        author=author,
        score=likes,
        metadata={
            "level_name": name, "creator": author,
            "difficulty": difficulty, "stars": str(stars),
            "level_id": str(level_id),
        },
    )


# ── Rated levels (GDBrowser only — no official fallback needed) ───────────────

async def _fetch_rated(
    client: httpx.AsyncClient, source_id: int, niche: str
) -> list[RawContent]:
    """Fetch the 10 most recently rated levels via GDBrowser."""
    try:
        resp = await client.get(
            f"{_GDBROWSER_URL}/search/*",
            params={"type": 4, "count": 10},
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception as exc:
        logger.debug(f"[GDBrowser] rated fetch failed: {exc}")
        return []

    items: list[RawContent] = []
    for data in results:
        level_id = data.get("id", "")
        name = data.get("name", "Unknown")
        author = data.get("author", "Unknown")
        difficulty = _parse_difficulty(data.get("difficulty", 0))
        stars = data.get("stars", 0)

        if not level_id:
            continue

        # Skip levels that show as unrated — these are GDBrowser cache artifacts.
        # Catches both numeric 0 → "N/A" and string "Unrated" / "Unknown" from API.
        if difficulty not in _DIFFICULTY_STR or difficulty == "N/A" or stars == 0:
            continue

        is_notable = author.lower() in _NOTABLE_CREATORS
        is_extreme = difficulty == "Extreme Demon"
        if not (is_notable or is_extreme):
            continue

        items.append(RawContent(
            source_id=source_id,
            external_id=f"rated_{level_id}",
            niche=niche,
            content_type="level_rated",
            title=f"{name} by {author}",
            url=f"https://gdbrowser.com/{level_id}",
            body=f"{difficulty} — {stars} stars",
            image_url="",
            author=author,
            score=int(data.get("likes", 0)),
            metadata={
                "level_name": name, "creator": author,
                "difficulty": difficulty, "stars": str(stars),
                "level_id": str(level_id),
            },
        ))

    return items
