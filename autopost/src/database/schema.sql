PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Sources ────────────────────────────────────────────────────────────────────
-- One row per configured source (seeded from YAML by scripts/setup_db.py).
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    niche       TEXT    NOT NULL,                   -- 'rocketleague' | 'geometrydash'
    name        TEXT    NOT NULL,                   -- human-readable, matches YAML
    type        TEXT    NOT NULL,                   -- 'reddit' | 'rss' | 'scraper' | 'twitter' | 'youtube' | 'api'
    config      TEXT    NOT NULL DEFAULT '{}',      -- JSON: all type-specific fields
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(niche, name)
);

-- ── Raw content ────────────────────────────────────────────────────────────────
-- Every item a collector finds, before formatting.
-- Dedup enforced by UNIQUE(source_id, external_id) — always INSERT OR IGNORE.
CREATE TABLE IF NOT EXISTS raw_content (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    INTEGER NOT NULL REFERENCES sources(id),
    external_id  TEXT    NOT NULL,                  -- post id, tweet id, guid, etc.
    niche        TEXT    NOT NULL,
    content_type TEXT    NOT NULL,                  -- maps to template key
    title        TEXT    NOT NULL DEFAULT '',
    url          TEXT    NOT NULL DEFAULT '',
    body         TEXT    NOT NULL DEFAULT '',
    image_url    TEXT    NOT NULL DEFAULT '',
    author       TEXT    NOT NULL DEFAULT '',
    score        INTEGER NOT NULL DEFAULT 0,        -- reddit upvotes, yt views, etc.
    metadata     TEXT    NOT NULL DEFAULT '{}',     -- JSON: extra fields per type
    collected_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(source_id, external_id)
);

-- ── Tweet queue ────────────────────────────────────────────────────────────────
-- Formatted tweets waiting to be posted.
-- priority: 1 = breaking news (post immediately), 10 = low-priority filler
-- status:   queued → posted | failed | skipped
CREATE TABLE IF NOT EXISTS tweet_queue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    niche          TEXT    NOT NULL,
    raw_content_id INTEGER REFERENCES raw_content(id),
    tweet_text     TEXT    NOT NULL,
    media_path     TEXT,                            -- local path to downloaded image
    priority       INTEGER NOT NULL DEFAULT 5,
    status         TEXT    NOT NULL DEFAULT 'queued',
    scheduled_at   TEXT,                            -- NULL = post as soon as possible
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    posted_at      TEXT
);

-- ── Post log ───────────────────────────────────────────────────────────────────
-- Immutable record of every attempted post (success or failure).
CREATE TABLE IF NOT EXISTS post_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tweet_queue_id INTEGER REFERENCES tweet_queue(id),
    niche          TEXT    NOT NULL,
    tweet_id       TEXT,                            -- X tweet ID on success, NULL on failure
    tweet_text     TEXT    NOT NULL,
    posted_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    error          TEXT                             -- error message if failed
);

-- ── Indexes ────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_raw_niche        ON raw_content(niche);
CREATE INDEX IF NOT EXISTS idx_raw_collected    ON raw_content(collected_at);
CREATE INDEX IF NOT EXISTS idx_queue_status     ON tweet_queue(status, priority, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_queue_niche      ON tweet_queue(niche, status);
CREATE INDEX IF NOT EXISTS idx_log_niche        ON post_log(niche, posted_at);
