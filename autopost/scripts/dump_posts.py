"""Dump all posted tweets for both accounts."""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "autopost.db"
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

for niche in ("rocketleague", "geometrydash"):
    rows = conn.execute(
        "SELECT tweet_id, content_type, posted_at, tweet_text FROM post_log "
        "WHERE niche = ? AND tweet_id IS NOT NULL AND tweet_id != 'dry_run_id' "
        "ORDER BY posted_at",
        (niche,),
    ).fetchall()
    handle = "@rl_wire1" if niche == "rocketleague" else "@gd_wire"
    print(f"=== {handle} ({len(rows)} posts) ===")
    for r in rows:
        text = r["tweet_text"].replace("\n", " | ")[:130]
        ct = r["content_type"] or "?"
        print(f'{r["posted_at"]}  id={r["tweet_id"]}  [{ct}]  {text}')
    print()
