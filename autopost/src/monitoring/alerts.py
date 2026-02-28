"""
Discord webhook alerts — fires on collector failures, poster failures,
and posting dry spells (queue empty for too long).

Set DISCORD_WEBHOOK_URL in .env to enable. If unset, all calls are no-ops.
"""
from datetime import datetime, timezone

import httpx
from loguru import logger

from config.settings import DISCORD_WEBHOOK_URL

# Colour codes for Discord embeds
_COLOUR = {
    "error":   0xE74C3C,   # red
    "warning": 0xF39C12,   # amber
    "success": 0x2ECC71,   # green
    "info":    0x3498DB,   # blue
}


async def send_alert(message: str, level: str = "error") -> None:
    """
    Send a plain-text alert to Discord.
    level: "error" | "warning" | "info" | "success"
    """
    if not DISCORD_WEBHOOK_URL:
        return

    payload = {
        "embeds": [{
            "description": message,
            "color":       _COLOUR.get(level, _COLOUR["error"]),
            "footer":      {"text": f"AutoPost • {_utcnow()}"},
        }]
    }
    await _post(payload)


async def alert_collector_failure(collector_name: str, niche: str, error: str) -> None:
    await send_alert(
        f"**Collector failed** `{collector_name}` [{niche}]\n```{error[:500]}```",
        level="error",
    )


async def alert_poster_failure(niche: str, error: str) -> None:
    await send_alert(
        f"**Poster failed** [{niche}]\n```{error[:500]}```",
        level="error",
    )


async def alert_dry_spell(niche: str, hours: int) -> None:
    """Call this when a niche hasn't posted anything for `hours` hours."""
    await send_alert(
        f"**Dry spell warning** [{niche}] — no posts in the last {hours}h. "
        f"Queue may be empty or rate-limited.",
        level="warning",
    )


async def alert_startup(dry_run: bool) -> None:
    mode = "DRY RUN" if dry_run else "LIVE"
    await send_alert(f"AutoPost started [{mode}]", level="success")


# ── Internal ──────────────────────────────────────────────────────────────────

async def _post(payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(DISCORD_WEBHOOK_URL, json=payload)
            resp.raise_for_status()
    except Exception as exc:
        # Never let an alert failure crash the main app
        logger.warning(f"[Alerts] Discord webhook failed: {exc}")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
