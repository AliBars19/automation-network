"""
Formatter — maps RawContent → tweet text.

Strategy:
  1. Build a context dict from RawContent fields, with safe fallbacks for every
     template variable used across all templates.
  2. Shuffle the template variants for variety, try each one:
       - Fill with context via _SafeFormatDict (missing keys stay as {key})
       - Skip if any {placeholder} is still unfilled (means the data isn't there)
       - Skip if result exceeds 280 chars
  3. If no variant fits cleanly, use the first fillable variant and truncate.
  4. Absolute fallback: "{title}\n\n{url}" truncated to 280.

Retweet signals (templates == [None]) return None — the poster handles those.
"""
import random
import re

from loguru import logger

from src.collectors.base import RawContent
from src.formatter.templates import TEMPLATES

MAX_CHARS = 280

# Regex to detect any remaining {placeholder} in a formatted string
_PLACEHOLDER_RE = re.compile(r"\{[^}]+\}")


# ── Public API ─────────────────────────────────────────────────────────────────

def format_tweet(content: RawContent) -> str | None:
    """
    Format a RawContent item into a tweet string.
    Returns None for retweet-signal content types (e.g. official_tweet).
    """
    variants = TEMPLATES.get(content.niche, {}).get(content.content_type)
    if not variants:
        logger.debug(f"No template for {content.niche}/{content.content_type} — skipping")
        return None  # no template = don't post this content type

    if variants == [None]:
        return None  # retweet signal — poster will RT/QT directly

    ctx = _build_context(content)
    shuffled = random.sample(variants, len(variants))

    # Pass 1 — find a variant that fills cleanly and fits in 280
    for tmpl in shuffled:
        if tmpl is None:
            continue
        result = _try_format(tmpl, ctx)
        if result and len(result) <= MAX_CHARS:
            return result

    # Pass 2 — find a fillable variant and truncate
    for tmpl in shuffled:
        if tmpl is None:
            continue
        result = _try_format(tmpl, ctx)
        if result:
            return _truncate(result, MAX_CHARS)

    return _fallback(content)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _try_format(template: str, ctx: dict) -> str | None:
    """
    Format template with ctx. Returns None if any placeholder is left unfilled.
    """
    try:
        result = template.format_map(_SafeFormatDict(ctx))
    except Exception:
        return None
    if _PLACEHOLDER_RE.search(result):
        return None  # required fields missing for this variant
    return result.strip()


def _fallback(content: RawContent) -> str:
    text = f"{content.title}\n\n{content.url}" if content.url else content.title
    return _truncate(text, MAX_CHARS)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    # Try to cut at a word boundary, leaving room for "…"
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return cut + "…"


def _build_context(content: RawContent) -> dict:
    """Build a rich context dict from RawContent, with safe defaults for every
    template variable. API collectors populate content.metadata with extra fields
    that override the defaults here."""

    title   = content.title.strip()
    url     = content.url.strip()
    body    = content.body.strip()
    author  = content.author.strip() or "Unknown"

    # Version extraction (used by patch_notes / game_update templates)
    version_match = re.search(r"v?\d+\.\d+[\w.]*", title)
    version = version_match.group(0) if version_match else "latest"

    # Bullet points from body lines (used by HYPEX/ShiinaBR-style templates)
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if len(lines) < 2:
        lines = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
    bullet1 = _cap(lines[0], 120) if len(lines) > 0 else title
    bullet2 = _cap(lines[1], 120) if len(lines) > 1 else "See full details"
    bullet3 = _cap(lines[2], 120) if len(lines) > 2 else url

    # Summaries of varying lengths
    summary       = _cap(body, 200) or title
    short_summary = _cap(body, 120) or title

    # Emoji that fits the niche + content type
    emoji = _pick_emoji(content.niche, content.content_type)

    base: dict = {
        # ── Universal ──────────────────────────────────────────────────────────
        "title":       title,
        "url":         url,
        "headline":    title,
        "summary":     summary,
        "details":     short_summary,
        "description": short_summary,
        "author":      author,
        "emoji":       emoji,

        # ── Patch / update ─────────────────────────────────────────────────────
        "version":    version,
        "bullet1":    bullet1,
        "bullet2":    bullet2,
        "bullet3":    bullet3,

        # ── Esports ────────────────────────────────────────────────────────────
        "event":       title,
        "event_short": title[:30],
        "stage":       "",
        "team1":       "",
        "team2":       "",
        "winner":      "",
        "loser":       "",
        "score":       "",
        "score1":      "",
        "score2":      "",
        "deficit":     "",
        "teams":       "",
        "prize_pool":  "",
        "time":        "",
        "day":         "",

        # ── Season / highlights ────────────────────────────────────────────────
        "number":      "",
        "highlights":  summary,
        "highlight1":  bullet1,
        "highlight2":  bullet2,
        "highlight3":  bullet3,

        # ── Roster / player ────────────────────────────────────────────────────
        "player":      author,
        "creator":     author,
        "old_team":    "",
        "team":        "",
        "season":      "",
        "roster_list": "",
        "source":      "",

        # ── Collab / item shop ─────────────────────────────────────────────────
        "brand":       title,
        "items":       summary,
        "date":        "",

        # ── Community ──────────────────────────────────────────────────────────
        "rank":        "",
        "achievement": title,
        "mechanic":    "",
        "context":     short_summary,

        # ── GD — demon list ────────────────────────────────────────────────────
        "level":        title,
        "level_name":   title,
        "position":     "",
        "old_position": "",
        "changes":      summary,
        "top1":         "",
        "top2":         "",
        "top3":         "",
        "top4":         "",
        "top5":         "",

        # ── GD — level metadata ────────────────────────────────────────────────
        "difficulty":     "",
        "stars":          "",
        "victor_number":  "",

        # ── GD — speedrun ──────────────────────────────────────────────────────
        "category":   "",
        "prev_time":  "",

        # ── GD — mod ──────────────────────────────────────────────────────────
        "mod_name":   title,

        # ── Flashback / on this day ──────────────────────────────────────────
        "years_ago": "",
        "year":      "",

        # ── Stat milestone ───────────────────────────────────────────────────
        "stat":      "",
        "value":     "",
    }

    # metadata from the collector overrides base defaults (enables rich API data)
    base.update({k: str(v) for k, v in content.metadata.items() if v is not None})

    return base


def _cap(text: str, limit: int) -> str:
    """Return text truncated at a word boundary to `limit` chars."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",.;:")
    return cut + "…"


def _pick_emoji(niche: str, content_type: str) -> str:
    _MAP = {
        "patch_notes":         "🔄",
        "season_start":        "🚀",
        "item_shop":           "🛒",
        "collab_announcement": "🔥",
        "event_announcement":  "🏟️",
        "esports_result":      "🏆",
        "esports_matchup":     "🎮",
        "roster_change":       "🔄",
        "community_clip":      "🔥",
        "rank_milestone":      "🏆",
        "pro_player_content":  "🎬",
        "top1_verified":       "🚨",
        "level_verified":      "🏆",
        "level_beaten":        "🎮",
        "demon_list_update":   "📊",
        "game_update":         "🔺",
        "mod_update":          "🔧",
        "level_rated":         "⭐",
        "daily_level":         "📅",
        "weekly_demon":        "👹",
        "youtube_video":       "🎬",
        "creator_spotlight":   "🎨",
        "speedrun_wr":         "🏆",
        "breaking_news":       "🚨",
        "flashback":           "📅",
        "stat_milestone":      "📊",
    }
    return _MAP.get(content_type, "📢")


class _SafeFormatDict(dict):
    """Returns the placeholder string itself for any missing key,
    so we can detect unfilled variables after formatting."""
    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"
