"""
SQLite helpers — connection, init, insert, queue reads, status updates.
All public functions accept an open sqlite3.Connection so callers control
the transaction boundary via the get_db() context manager.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

from config.settings import DB_PATH


# ── Connection ─────────────────────────────────────────────────────────────────

@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Yield an open connection; commit on clean exit, rollback on exception."""
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema init ────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables and indexes from schema.sql. Safe to call repeatedly."""
    schema_path = Path(__file__).parent / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    with get_db() as conn:
        conn.executescript(sql)


# ── Sources ────────────────────────────────────────────────────────────────────

def upsert_source(conn: sqlite3.Connection, niche: str, name: str, type_: str, config: dict) -> int:
    """Insert source if it doesn't exist; return its id either way."""
    conn.execute(
        """INSERT OR IGNORE INTO sources (niche, name, type, config)
           VALUES (?, ?, ?, ?)""",
        (niche, name, type_, json.dumps(config)),
    )
    row = conn.execute(
        "SELECT id FROM sources WHERE niche = ? AND name = ?", (niche, name)
    ).fetchone()
    return row["id"]


def get_sources(conn: sqlite3.Connection, niche: str) -> list[sqlite3.Row]:
    """Return all enabled sources for a niche."""
    return conn.execute(
        "SELECT * FROM sources WHERE niche = ? AND enabled = 1", (niche,)
    ).fetchall()


# ── Raw content ────────────────────────────────────────────────────────────────

def insert_raw_content(conn: sqlite3.Connection, content) -> tuple[int, bool]:
    """
    INSERT OR IGNORE a RawContent item.
    Returns (id, was_new): was_new=True if just inserted, False if duplicate.
    """
    cur = conn.execute(
        """INSERT OR IGNORE INTO raw_content
               (source_id, external_id, niche, content_type,
                title, url, body, image_url, author, score, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            content.source_id,
            content.external_id,
            content.niche,
            content.content_type,
            content.title,
            content.url,
            content.body,
            content.image_url,
            content.author,
            content.score,
            json.dumps(content.metadata),
        ),
    )
    if cur.rowcount == 1:
        return cur.lastrowid, True

    # Duplicate — return the existing row's id
    row = conn.execute(
        "SELECT id FROM raw_content WHERE source_id = ? AND external_id = ?",
        (content.source_id, content.external_id),
    ).fetchone()
    return row["id"], False


# ── Tweet queue ────────────────────────────────────────────────────────────────

def add_to_queue(
    conn: sqlite3.Connection,
    niche: str,
    tweet_text: str,
    raw_content_id: int | None = None,
    media_path: str | None = None,
    priority: int = 5,
    scheduled_at: str | None = None,
) -> int:
    """Enqueue a formatted tweet. Returns the new queue row id."""
    cur = conn.execute(
        """INSERT INTO tweet_queue
               (niche, raw_content_id, tweet_text, media_path, priority, scheduled_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (niche, raw_content_id, tweet_text, media_path, priority, scheduled_at),
    )
    return cur.lastrowid


def get_queued_tweets(
    conn: sqlite3.Connection, niche: str, limit: int = 10
) -> list[sqlite3.Row]:
    """
    Return up to `limit` queued tweets for a niche, ordered by priority then age.
    Only returns rows whose scheduled_at is in the past (or NULL).
    """
    return conn.execute(
        """SELECT * FROM tweet_queue
           WHERE niche = ? AND status = 'queued'
             AND (scheduled_at IS NULL
                  OR scheduled_at <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
           ORDER BY priority ASC, created_at ASC
           LIMIT ?""",
        (niche, limit),
    ).fetchall()


def mark_posted(conn: sqlite3.Connection, queue_id: int, tweet_id: str) -> None:
    """Mark a queue row as posted and write a success entry to post_log."""
    now = _utcnow()
    conn.execute(
        "UPDATE tweet_queue SET status = 'posted', posted_at = ? WHERE id = ?",
        (now, queue_id),
    )
    row = conn.execute("SELECT * FROM tweet_queue WHERE id = ?", (queue_id,)).fetchone()
    conn.execute(
        """INSERT INTO post_log (tweet_queue_id, niche, tweet_id, tweet_text, posted_at)
           VALUES (?, ?, ?, ?, ?)""",
        (queue_id, row["niche"], tweet_id, row["tweet_text"], now),
    )


def mark_failed(conn: sqlite3.Connection, queue_id: int, error: str) -> None:
    """Mark a queue row as failed and write a failure entry to post_log."""
    now = _utcnow()
    conn.execute(
        "UPDATE tweet_queue SET status = 'failed', posted_at = ? WHERE id = ?",
        (now, queue_id),
    )
    row = conn.execute("SELECT * FROM tweet_queue WHERE id = ?", (queue_id,)).fetchone()
    conn.execute(
        """INSERT INTO post_log (tweet_queue_id, niche, tweet_id, tweet_text, posted_at, error)
           VALUES (?, ?, NULL, ?, ?, ?)""",
        (queue_id, row["niche"], row["tweet_text"], now, error),
    )


def mark_skipped(conn: sqlite3.Connection, queue_id: int) -> None:
    """Mark a queue row as skipped (e.g. duplicate detected late, rate limit)."""
    conn.execute(
        "UPDATE tweet_queue SET status = 'skipped' WHERE id = ?", (queue_id,)
    )


# ── Similarity & dedup helpers ─────────────────────────────────────────────────

def is_similar_story(
    conn: sqlite3.Connection,
    tweet_text: str,
    niche: str,
    threshold: float = 0.65,
    hours: int = 24,
) -> bool:
    """
    Return True if tweet_text is very similar to any tweet queued in the
    past `hours` hours for this niche.  Protects against cross-source story
    duplication where different sources cover the same news with slightly
    different wording.  Uses difflib.SequenceMatcher (no extra dependencies).
    """
    from difflib import SequenceMatcher

    cutoff = _hours_ago(hours)
    rows = conn.execute(
        """SELECT tweet_text FROM tweet_queue
           WHERE niche = ? AND created_at >= ? AND status = 'queued'""",
        (niche, cutoff),
    ).fetchall()
    needle = tweet_text.lower()
    for row in rows:
        ratio = SequenceMatcher(None, needle, row["tweet_text"].lower()).ratio()
        if ratio >= threshold:
            return True
    return False


def url_already_queued(conn: sqlite3.Connection, url: str, content_id: int) -> bool:
    """
    Return True if a raw_content row with the same URL has already been
    enqueued from a different source (id != content_id).
    Prevents the same article from two sources filling the queue twice.
    """
    if not url:
        return False
    row = conn.execute(
        """SELECT 1 FROM raw_content rc
           JOIN tweet_queue tq ON tq.raw_content_id = rc.id
           WHERE rc.url = ? AND rc.id != ?
           LIMIT 1""",
        (url, content_id),
    ).fetchone()
    return row is not None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hours_ago(hours: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
