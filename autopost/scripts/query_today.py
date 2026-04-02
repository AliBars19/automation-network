"""Quick diagnostic: dump today's post_log and queue stats."""
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "autopost.db"
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

cutoff = "2026-04-02T00:00:00Z"
apr_cutoff = "2026-04-01T00:00:00Z"

# Totals
total = conn.execute("SELECT COUNT(*) FROM post_log WHERE posted_at >= ?", (cutoff,)).fetchone()[0]
success = conn.execute("SELECT COUNT(*) FROM post_log WHERE posted_at >= ? AND tweet_id IS NOT NULL", (cutoff,)).fetchone()[0]
failed = conn.execute("SELECT COUNT(*) FROM post_log WHERE posted_at >= ? AND tweet_id IS NULL", (cutoff,)).fetchone()[0]
monthly = conn.execute("SELECT COUNT(*) FROM post_log WHERE posted_at >= ? AND tweet_id IS NOT NULL", (apr_cutoff,)).fetchone()[0]

print(f"=== April 2 Post Summary ===")
print(f"Total post_log entries: {total}")
print(f"  Successful: {success}")
print(f"  Failed: {failed}")
print(f"Monthly (Apr): {monthly}")

# By niche
print("\nBy niche:")
for row in conn.execute("SELECT niche, COUNT(*) as c, SUM(CASE WHEN tweet_id IS NOT NULL THEN 1 ELSE 0 END) as ok FROM post_log WHERE posted_at >= ? GROUP BY niche", (cutoff,)).fetchall():
    print(f"  {row['niche']}: {row['c']} total, {row['ok']} success")

# All posts with details
print("\n=== All posts today ===")
for row in conn.execute("SELECT posted_at, niche, content_type, tweet_id, error, substr(tweet_text, 1, 80) as preview FROM post_log WHERE posted_at >= ? ORDER BY posted_at", (cutoff,)).fetchall():
    status = "OK" if row["tweet_id"] else "FAIL"
    err = f" ERR={row['error'][:50]}" if row["error"] else ""
    print(f"{row['posted_at']}  [{row['niche'][:2]}] {status}  {row['content_type'] or 'unknown':22s}  {row['preview']}{err}")

# Queue
print("\n=== Queue status ===")
for row in conn.execute("SELECT niche, status, COUNT(*) as c FROM tweet_queue GROUP BY niche, status ORDER BY niche, status").fetchall():
    print(f"  {row['niche']}: {row['status']}={row['c']}")
