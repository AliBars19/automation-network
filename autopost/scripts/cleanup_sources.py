"""
One-time script: disable zombie Reddit sources and dead scrapers in the DB.

Reddit was permanently removed from the codebase (2026-03-08) but 10 sources
remain enabled=1 in the database.  Several scrapers are also dead (returning
403/404/500) and should be disabled.

Also disables @RocketBaguette (French-only, removed from YAML).

Run: python -m scripts.cleanup_sources
"""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "autopost.db"


# Sources to disable — (niche, name) tuples
DISABLE_SOURCES: list[tuple[str, str]] = [
    # ── Reddit (server IP blocked, code deleted) ─────────────────────────────
    ("rocketleague", "r/RocketLeague"),
    ("rocketleague", "r/RocketLeagueEsports"),
    ("rocketleague", "r/RLSideSwipe"),
    ("rocketleague", "r/RocketLeagueSchool"),
    ("rocketleague", "r/RLFanArt"),
    ("geometrydash", "r/geometrydash"),
    ("geometrydash", "r/gdlevels"),
    ("geometrydash", "r/geometrydashcringe"),
    ("geometrydash", "r/challengelist"),
    ("geometrydash", "r/gdmods"),
    # ── Dead RL scrapers (403/404/500 consistently) ──────────────────────────
    ("rocketleague", "Esports.gg RL"),
    ("rocketleague", "GGRecon RL"),
    ("rocketleague", "RL Tracker Network News"),
    ("rocketleague", "RLshop.gg"),
    ("rocketleague", "Esports Insider RL"),
    ("rocketleague", "start.gg RLCS"),
    ("rocketleague", "Liquipedia RL"),
    # ── Duplicate RL RSS feeds ───────────────────────────────────────────────
    ("rocketleague", "RL Blog"),          # duplicate of "Rocket League Blog"
    ("rocketleague", "Steam News (RL)"),  # duplicate of "Steam News"
    ("rocketleague", "Dot Esports RL"),   # was returning 403
    # ── French-only source (removed from YAML) ───────────────────────────────
    ("rocketleague", "@RocketBaguette"),
    # ── Dead GD scrapers ─────────────────────────────────────────────────────
    ("geometrydash", "Geometry Dash Forum (News board)"),  # forum is dead
    ("geometrydash", "Speedrun.com \u2014 GD"),  # rarely updates
    # ── Duplicate GD API ─────────────────────────────────────────────────────
    ("geometrydash", "GDBrowser_test"),   # test source, not real
    ("geometrydash", "Pointercrate"),     # duplicate of "Pointercrate Demon List"
]


def main() -> None:
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    disabled = 0

    for niche, name in DISABLE_SOURCES:
        row = conn.execute(
            "SELECT id, enabled FROM sources WHERE niche = ? AND name = ?",
            (niche, name),
        ).fetchone()
        if row is None:
            print(f"  SKIP (not found): [{niche}] {name}")
            continue
        if row["enabled"] == 0:
            print(f"  SKIP (already disabled): [{niche}] {name} (id={row['id']})")
            continue
        conn.execute("UPDATE sources SET enabled = 0 WHERE id = ?", (row["id"],))
        print(f"  DISABLED: [{niche}] {name} (id={row['id']})")
        disabled += 1

    conn.commit()
    conn.close()
    print(f"\nDone — disabled {disabled} sources.")


if __name__ == "__main__":
    main()
