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

    result = None

    # Pass 1 — find a variant that fills cleanly and fits in 280
    for tmpl in shuffled:
        if tmpl is None:
            continue
        candidate = _try_format(tmpl, ctx)
        if candidate and len(candidate) <= MAX_CHARS:
            result = candidate
            break

    # Pass 2 — find a fillable variant and truncate
    if result is None:
        for tmpl in shuffled:
            if tmpl is None:
                continue
            candidate = _try_format(tmpl, ctx)
            if candidate:
                result = _truncate(candidate, MAX_CHARS)
                break

    if result is None:
        result = _fallback(content)

    # Append niche hashtag if it fits within 280 chars
    return _append_hashtag(result, content.niche)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _try_format(template: str, ctx: dict) -> str | None:
    """
    Format template with ctx.
    Rejects if any placeholder is left unfilled or if the result has
    signs of empty-filled fields (double spaces, degenerate separators).
    """
    try:
        result = template.format_map(_SafeFormatDict(ctx))
    except Exception:
        return None
    if _PLACEHOLDER_RE.search(result):
        return None  # required fields missing for this variant
    result = result.strip()
    if not result:
        return None
    # Reject results showing signs of empty-filled placeholders
    if "  " in result:
        return None
    return result


_NICHE_HASHTAG: dict[str, str] = {
    "rocketleague": "#RocketLeague",
    "geometrydash": "#GeometryDash",
}


def _append_hashtag(text: str, niche: str) -> str:
    """Append the niche hashtag if it fits within 280 chars and isn't already present."""
    hashtag = _NICHE_HASHTAG.get(niche, "")
    if not hashtag:
        return text
    if hashtag.lower() in text.lower():
        return text  # already has it (e.g. template included #RLCS)
    candidate = f"{text}\n\n{hashtag}"
    if len(candidate) <= MAX_CHARS:
        return candidate
    return text  # doesn't fit — post without it


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
    """Build a context dict from RawContent.

    Only fields derivable from the content itself get defaults here.
    Structured fields (scores, positions, teams, version numbers, etc.) are
    intentionally omitted so that templates requiring them fail the
    placeholder check and get skipped in favour of simpler variants.
    Collector metadata overrides everything."""

    title   = content.title.strip()
    url     = content.url.strip()
    body    = content.body.strip()
    author  = content.author.strip() or "Unknown"

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

    # Version extraction — only set if actually found in the title
    version_match = re.search(r"v?\d+\.\d+[\w.]*", title)

    base: dict = {
        # ── Universal (always derivable from content fields) ──────────────────
        "title":       title,
        "url":         url,
        "headline":    title,
        "summary":     summary,
        "details":     short_summary,
        "description": short_summary,
        "author":      author,
        "emoji":       emoji,

        # ── Bullet points (derived from body) ─────────────────────────────────
        "bullet1":    bullet1,
        "bullet2":    bullet2,
        "bullet3":    bullet3,

        # ── Esports (title-derived only) ──────────────────────────────────────
        "event":       title,
        "event_short": title[:30],

        # ── Season / highlights (body-derived) ────────────────────────────────
        "highlights":  summary,
        "highlight1":  bullet1,
        "highlight2":  bullet2,
        "highlight3":  bullet3,

        # ── Player / creator (author-derived) ─────────────────────────────────
        "player":      author,
        "creator":     author,

        # ── Collab / item shop (title/summary-derived) ────────────────────────
        "brand":       title,
        "items":       summary,

        # ── Community ──────────────────────────────────────────────────────────
        "achievement": title,
        "context":     short_summary,

        # ── GD (title/summary-derived) ────────────────────────────────────────
        "level":        title,
        "level_name":   title,
        "changes":      summary,
        "mod_name":     title,

        # NOTE: All structured fields (version, stage, team1, team2, winner,
        # loser, score*, number, teams, prize_pool, position, difficulty, stars,
        # rank, category, years_ago, etc.) are intentionally NOT defaulted.
        # They must come from collector metadata — if absent, templates using
        # them will be skipped via the placeholder check in _try_format().
    }

    # Only add version if we actually found one in the title
    if version_match:
        base["version"] = version_match.group(0)

    # Collector metadata overrides base defaults — skip empty/whitespace values
    base.update({
        k: str(v)
        for k, v in content.metadata.items()
        if v is not None and str(v).strip()
    })

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
