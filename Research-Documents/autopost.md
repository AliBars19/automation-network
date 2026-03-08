# AutoPost Network — Claude Code Project Brief

> **Give this entire document to Claude Code as context before starting.**
> Two automated X (Twitter) news bots: Rocket League + Geometry Dash.
> No AI generation. Template-based posting. Hosted on existing DigitalOcean droplet.

---

## 1. What We're Building

Two automated X news pages modelled after [@kurrco](https://x.com/kurrco) (300K+ follower hip-hop news page). Kurrco's style is:

- **Short, factual, news-relay tweets** — no fluff, no opinion, just the news
- **Format:** `[Artist/Subject] — [what happened]. [optional quote or detail]`
- **Always attaches media** — images, video thumbnails, album covers, screenshots
- **Posts frequently** — 5-15+ tweets per day covering everything happening in the niche
- **Quotes primary sources** — artist tweets, official announcements, patch notes
- **Uses emojis sparingly** — 🔥 🚨 📸 🎮 but not every tweet
- **Engages with trending topics fast** — speed is everything for news pages

We replicate this exact style for:

1. **Rocket League News** — game updates, esports, item shop, patch notes, community highlights
2. **Geometry Dash News** — demon list updates, level verifications, RobTop updates, mod news, creator content

### Example Tweet Formats (Kurrco-style adapted)

**Rocket League:**
```
Rocket League v2.44 patch notes are out 🚗

- New arena: Starbase Redux
- Ranked season reset
- Bug fixes for demo mechanics

Full notes: [link]
```
```
RLCS Boston Major — Grand Finals

Team BDS 4-2 G2 Stride

BDS are your RLCS Major Champions 🏆
```
```
New items in the Rocket League Item Shop today 🛒

[image of item shop]
```

**Geometry Dash:**
```
"Acheron" has been moved to #3 on the Demon List after re-evaluation 📊

New Top 5:
1. Tidal Wave
2. Slaughterhouse  
3. Acheron
4. Abyss of Darkness
5. Sakupen Circles
```
```
RobTop has updated Geometry Dash to version 2.208 🔺

- Click Between Frames added
- New leaderboards
- New Gauntlets
- Bug fixes

Available now on Steam, iOS, and Android.
```
```
Zoink has become the first victor of Flamewall — currently #3 on the Demon List 🏆
```

---

## 2. Architecture

```
┌─────────────────────────────────────────────┐
│              CONTENT SOURCES                │
│  RSS · Reddit · APIs · Web Scrapers         │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│           INGESTION SERVICE                 │
│  Python collectors run on cron intervals    │
│  Each source has its own collector class    │
│  Raw content → SQLite database              │
│  Deduplication by external_id               │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│          TEMPLATE FORMATTER                 │
│  Maps content type → tweet template         │
│  Attaches media (images/thumbnails)         │
│  Enforces 280 char limit                    │
│  Adds hashtags based on content type        │
│  Queues formatted tweet in DB               │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│            POSTING SERVICE                  │
│  Tweepy v4 — posts from queue               │
│  One X account per niche                    │
│  Rate limiting + jitter (no exact intervals)│
│  Media upload before posting                │
│  Logs tweet ID + timestamp                  │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│              MONITORING                     │
│  Logs to file + optional Discord webhook    │
│  Alerts on: failures, rate limits, dry      │
│  spells (no content for 6+ hours)           │
└─────────────────────────────────────────────┘
```

**No AI layer. No LLM calls. Pure template-based formatting.**

---

## 3. Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| Language | **Python 3.12+** | Best library ecosystem for APIs + scraping |
| X API | **tweepy >= 4.14** | Most maintained Python X library |
| Reddit | **asyncpraw** | Async Reddit API wrapper |
| RSS | **feedparser** | Standard RSS/Atom parser |
| HTTP | **httpx** | Async HTTP client for API calls + scraping |
| Scraping | **beautifulsoup4** + **httpx** | For sites without APIs/RSS |
| Database | **SQLite** (via stdlib `sqlite3`) | Already on droplet, zero setup, good enough for this scale |
| Scheduling | **cron** (system) + **APScheduler** (in-app) | cron for the main process, APScheduler for internal intervals |
| Image handling | **Pillow** | Download, resize, format images for tweet media |
| Config | **pyyaml** + **python-dotenv** | YAML for niche configs, .env for secrets |
| Logging | **loguru** | Better than stdlib logging, simple setup |
| Process management | **systemd** | Keep the bot running on the droplet |
| Alerts (optional) | **Discord webhook** via httpx | Push alerts to a personal Discord channel |

### What NOT to use
- ❌ No Claude/OpenAI API — no AI text generation
- ❌ No Celery/Redis — overkill for this scale
- ❌ No Docker — runs directly on the droplet with a venv
- ❌ No PostgreSQL — SQLite handles this volume easily
- ❌ No web framework — no dashboard needed initially

---

## 4. Content Sources

### Rocket League

| Source | Type | Collector | What It Provides | Poll Interval |
|--------|------|-----------|-----------------|---------------|
| r/RocketLeague | Reddit API | `reddit_collector.py` | Game news, clips, community posts | 5 min |
| r/RocketLeagueEsports | Reddit API | `reddit_collector.py` | RLCS results, roster moves, match threads | 5 min |
| Rocket League Blog | RSS / Scraper | `rss_collector.py` or `scraper.py` | Official patch notes, events, new seasons | 15 min |
| Rocket League Twitter (@RocketLeague) | X API (read) | `twitter_monitor.py` | Official announcements | 5 min |
| RL Esports Twitter (@RLEsports) | X API (read) | `twitter_monitor.py` | Tournament results, schedules | 5 min |
| Liquipedia RL | Scraper | `scraper.py` | Esports results, rosters, transfers | 30 min |
| Octane.gg | REST API | `api_collector.py` | Match stats, player stats, event data | 15 min |
| Steam News for RL | RSS | `rss_collector.py` | Update/patch announcements | 30 min |

**Octane.gg API base:** `https://zsr.octane.gg/`
- `/events` — list events
- `/matches` — match results
- `/players` — player info

### Geometry Dash

| Source | Type | Collector | What It Provides | Poll Interval |
|--------|------|-----------|-----------------|---------------|
| r/geometrydash | Reddit API | `reddit_collector.py` | Community news, completions, drama, creator content | 5 min |
| Pointercrate Demon List API | REST API | `api_collector.py` | Demon list changes, new records, verifications | 10 min |
| GDBrowser API | REST API | `api_collector.py` | Featured/daily/weekly levels, level data | 15 min |
| Dashword.net | RSS / Scraper | `scraper.py` | News articles, major updates | 30 min |
| RobTop Twitter (@RobTopGames) | X API (read) | `twitter_monitor.py` | Dev updates (rare but critical) | 5 min |
| GD YouTube channels | YouTube Data API v3 | `youtube_collector.py` | New videos from EVW, Wulzy, GD Colon, etc. | 30 min |
| GitHub (Geode/mods) | GitHub API | `api_collector.py` | Mod updates, new releases | 60 min |

**Pointercrate API base:** `https://pointercrate.com/api/v2/`
- `/demons/listed/` — full demon list
- `/records/` — new records/verifications
- `/players/ranking/` — player rankings

**GDBrowser API base:** `https://gdbrowser.com/api/`
- `/level/{id}` — level info
- `/search/{query}` — search levels
- `/profile/{username}` — player profiles
- `/dailyLevel` — current daily level
- `/weeklyDemon` — current weekly demon

---

## 5. Database Schema

```sql
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    niche TEXT NOT NULL CHECK(niche IN ('rocketleague', 'geometrydash')),
    source_type TEXT NOT NULL CHECK(source_type IN ('rss', 'reddit', 'api', 'scraper', 'twitter', 'youtube')),
    url TEXT,
    config TEXT,  -- JSON string for source-specific config
    enabled INTEGER DEFAULT 1,
    last_fetched_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS raw_content (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    niche TEXT NOT NULL,
    external_id TEXT NOT NULL,       -- dedup key: reddit post id, rss guid, api record id
    content_type TEXT NOT NULL,      -- 'patch_notes', 'esports_result', 'demon_list', 'level_rated', etc.
    title TEXT,
    body TEXT,
    url TEXT,
    image_url TEXT,
    metadata TEXT,                   -- JSON: extra structured data (scores, player names, etc.)
    published_at TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    processed INTEGER DEFAULT 0,
    UNIQUE(source_id, external_id)
);

CREATE TABLE IF NOT EXISTS tweet_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id INTEGER REFERENCES raw_content(id),
    niche TEXT NOT NULL,
    tweet_text TEXT NOT NULL,
    media_paths TEXT,                -- JSON array of local file paths
    priority INTEGER DEFAULT 5,     -- 1=highest (breaking), 10=lowest (filler)
    status TEXT DEFAULT 'queued' CHECK(status IN ('queued', 'posted', 'failed', 'skipped')),
    scheduled_for TEXT,
    posted_at TEXT,
    tweet_id TEXT,                   -- X tweet ID after posting
    error TEXT,                      -- error message if failed
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS post_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    niche TEXT NOT NULL,
    tweet_id TEXT NOT NULL,
    tweet_text TEXT,
    posted_at TEXT DEFAULT (datetime('now')),
    impressions INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    retweets INTEGER DEFAULT 0,
    replies INTEGER DEFAULT 0
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_raw_content_niche ON raw_content(niche, processed);
CREATE INDEX IF NOT EXISTS idx_raw_content_dedup ON raw_content(source_id, external_id);
CREATE INDEX IF NOT EXISTS idx_tweet_queue_status ON tweet_queue(niche, status, priority);
```

---

## 6. Tweet Templates

Templates are Python format strings. Each `content_type` maps to a template.

### Rocket League Templates

```python
RL_TEMPLATES = {
    "patch_notes": [
        "Rocket League {version} patch notes are out 🚗\n\n{summary}\n\nFull notes: {url}",
        "New Rocket League update ({version}) is live 🔄\n\n{summary}\n\n{url}",
    ],
    "esports_result": [
        "{event} — {stage}\n\n{team1} {score1}-{score2} {team2}\n\n{winner} take the series {emoji}",
        "{event}\n\n{winner} defeat {loser} {score} to advance {emoji}",
    ],
    "item_shop": [
        "Rocket League Item Shop — {date} 🛒\n\n{items}",
    ],
    "roster_change": [
        "{player} has joined {team} for {event} 🔄",
        "{team} announce {player} as their new {role} for {season}",
    ],
    "season_start": [
        "Rocket League Season {number} is now live! 🏎️\n\n{highlights}\n\n{url}",
    ],
    "reddit_highlight": [
        "{title}\n\n📎 {url}",
    ],
    "official_tweet": [
        # Quote tweet / repost format — just RT or quote the official account
        None,  # Handle as retweet, not template
    ],
}
```

### Geometry Dash Templates

```python
GD_TEMPLATES = {
    "demon_list_update": [
        "Demon List Update 📊\n\n{changes}",
        "{level} has been placed at #{position} on the Demon List {emoji}",
    ],
    "level_verified": [
        "{player} has verified {level} — {description} {emoji}\n\n{url}",
        "NEW: {level} has been verified by {player} 🏆\n\n{details}",
    ],
    "level_beaten": [
        "{player} has beaten {level} (#{position} on Demon List) {emoji}",
        "New victor on {level}: {player} 🎮\n\n{context}",
    ],
    "game_update": [
        "Geometry Dash {version} is out now 🔺\n\n{changes}\n\nAvailable on Steam, iOS, and Android.",
        "RobTop has released Geometry Dash {version} 🔄\n\n{summary}\n\n{url}",
    ],
    "robtop_tweet": [
        None,  # Retweet the original
    ],
    "level_rated": [
        "New rated level: \"{level_name}\" by {creator} ⭐\n\nDifficulty: {difficulty}\nStars: {stars}",
    ],
    "daily_level": [
        "Today's Daily Level: \"{level_name}\" by {creator} 📅\n\nDifficulty: {difficulty}",
    ],
    "weekly_demon": [
        "This week's Weekly Demon: \"{level_name}\" by {creator} 👹\n\nDifficulty: {difficulty}",
    ],
    "mod_update": [
        "Geode {version} has been released 🔧\n\n{summary}\n\nDownload: {url}",
    ],
    "youtube_video": [
        "New video from {creator}: \"{title}\" 🎬\n\n{url}",
    ],
    "reddit_highlight": [
        "{title}\n\n📎 {url}",
    ],
}
```

### Template Selection Logic

```python
import random

def format_tweet(content: dict, niche: str) -> str:
    templates = RL_TEMPLATES if niche == "rocketleague" else GD_TEMPLATES
    content_type = content["content_type"]
    
    template_list = templates.get(content_type, templates["reddit_highlight"])
    
    # Filter out None (retweet types)
    valid = [t for t in template_list if t is not None]
    if not valid:
        return None  # Signal to retweet instead
    
    template = random.choice(valid)
    
    # Format with content data, truncate to 280 chars
    tweet = template.format(**content["metadata"])
    if len(tweet) > 280:
        tweet = tweet[:277] + "..."
    
    return tweet
```

---

## 7. Project Structure

```
autopost/
├── config/
│   ├── settings.py                # Global settings, loads .env
│   ├── rocketleague.yaml          # RL sources, templates, schedule
│   ├── geometrydash.yaml          # GD sources, templates, schedule
│   └── .env                       # API keys (gitignored)
│
├── src/
│   ├── __init__.py
│   ├── main.py                    # Entry point — starts scheduler
│   │
│   ├── collectors/                # One file per source type
│   │   ├── __init__.py
│   │   ├── base.py               # Abstract base: fetch() -> list[RawContent]
│   │   ├── rss.py                # feedparser-based RSS collector
│   │   ├── reddit.py             # asyncpraw Reddit collector
│   │   ├── twitter_monitor.py    # Monitor official accounts via X API
│   │   ├── youtube.py            # YouTube Data API v3 collector
│   │   ├── scraper.py            # BeautifulSoup generic scraper
│   │   └── apis/                 # Niche-specific API collectors
│   │       ├── __init__.py
│   │       ├── pointercrate.py   # GD demon list API
│   │       ├── gdbrowser.py      # GD level data API
│   │       └── octane.py         # RL esports data API
│   │
│   ├── formatter/                 # Template-based tweet formatting
│   │   ├── __init__.py
│   │   ├── templates.py          # All templates defined here
│   │   ├── formatter.py          # Maps content → tweet text
│   │   └── media.py             # Download + resize images for upload
│   │
│   ├── poster/                    # X API posting
│   │   ├── __init__.py
│   │   ├── client.py            # Tweepy client wrapper (one per niche)
│   │   ├── queue.py             # Reads from tweet_queue, posts in order
│   │   └── rate_limiter.py      # Tracks rate limits, adds jitter
│   │
│   ├── database/
│   │   ├── __init__.py
│   │   ├── db.py                # SQLite connection + helpers
│   │   └── schema.sql           # Schema from section 5
│   │
│   └── monitoring/
│       ├── __init__.py
│       └── alerts.py            # Discord webhook alerts
│
├── scripts/
│   ├── setup_db.py              # Initialise database + seed sources
│   ├── test_collector.py        # Test a single collector
│   ├── test_post.py             # Dry-run post to X
│   └── backfill.py              # Backfill from sources
│
├── data/
│   ├── autopost.db              # SQLite database (gitignored)
│   └── media/                   # Temp downloaded images (gitignored)
│
├── logs/                         # Log files (gitignored)
│
├── requirements.txt
├── .env.example
├── .gitignore
├── autopost.service             # systemd unit file
└── README.md
```

---

## 8. Key Implementation Details

### 8.1 Collector Base Class

```python
# src/collectors/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class RawContent:
    source_id: int
    niche: str
    external_id: str
    content_type: str
    title: str
    body: str | None = None
    url: str | None = None
    image_url: str | None = None
    metadata: dict | None = None  # Structured data for template formatting
    published_at: str | None = None

class BaseCollector(ABC):
    def __init__(self, source_config: dict):
        self.config = source_config
    
    @abstractmethod
    async def fetch(self) -> list[RawContent]:
        """Fetch new content from source. Must handle dedup externally."""
        pass
```

### 8.2 Reddit Collector (Example)

```python
# src/collectors/reddit.py
import asyncpraw
from .base import BaseCollector, RawContent

class RedditCollector(BaseCollector):
    async def fetch(self) -> list[RawContent]:
        reddit = asyncpraw.Reddit(
            client_id=self.config["client_id"],
            client_secret=self.config["client_secret"],
            user_agent="autopost:v1.0 (by u/yourusername)"
        )
        
        subreddit = await reddit.subreddit(self.config["subreddit"])
        items = []
        
        async for post in subreddit.hot(limit=25):
            # Skip low-effort posts
            if post.score < self.config.get("min_score", 50):
                continue
            
            content_type = self._classify_post(post)
            
            items.append(RawContent(
                source_id=self.config["source_id"],
                niche=self.config["niche"],
                external_id=post.id,
                content_type=content_type,
                title=post.title,
                body=post.selftext[:500] if post.selftext else None,
                url=f"https://reddit.com{post.permalink}",
                image_url=post.url if post.url.endswith(('.jpg', '.png', '.gif')) else None,
                metadata={
                    "score": post.score,
                    "num_comments": post.num_comments,
                    "flair": str(post.link_flair_text),
                    "author": str(post.author),
                },
                published_at=str(post.created_utc),
            ))
        
        await reddit.close()
        return items
    
    def _classify_post(self, post) -> str:
        """Classify Reddit post into a content_type based on flair/title."""
        title_lower = post.title.lower()
        flair = str(post.link_flair_text).lower() if post.link_flair_text else ""
        
        if any(kw in title_lower for kw in ["patch", "update", "hotfix"]):
            return "patch_notes"
        if any(kw in title_lower for kw in ["rlcs", "major", "regional", "tournament"]):
            return "esports_result"
        if any(kw in flair for kw in ["news", "announcement"]):
            return "patch_notes"
        return "reddit_highlight"
```

### 8.3 Pointercrate Collector (GD Demon List)

```python
# src/collectors/apis/pointercrate.py
import httpx
from ..base import BaseCollector, RawContent

class PointercrateCollector(BaseCollector):
    BASE_URL = "https://pointercrate.com/api/v2"
    
    async def fetch(self) -> list[RawContent]:
        async with httpx.AsyncClient() as client:
            items = []
            
            # Fetch latest records (verifications + completions)
            resp = await client.get(
                f"{self.BASE_URL}/records/",
                params={"limit": 25, "status": "approved"},
                headers={"Accept": "application/json"}
            )
            
            if resp.status_code == 200:
                records = resp.json()
                for record in records:
                    demon = record.get("demon", {})
                    player = record.get("player", {})
                    progress = record.get("progress", 0)
                    
                    if progress == 100:
                        content_type = "level_beaten"
                        emoji = "🏆" if demon.get("position", 999) <= 10 else "✅"
                    else:
                        continue  # Only post completions, not progress records
                    
                    items.append(RawContent(
                        source_id=self.config["source_id"],
                        niche="geometrydash",
                        external_id=f"pointercrate_{record['id']}",
                        content_type=content_type,
                        title=f"{player.get('name')} beat {demon.get('name')}",
                        url=f"https://pointercrate.com/demonlist/{demon.get('position', '')}",
                        metadata={
                            "player": player.get("name", "Unknown"),
                            "level": demon.get("name", "Unknown"),
                            "position": demon.get("position", "?"),
                            "emoji": emoji,
                            "context": f"Top {demon.get('position', '?')} on the Demon List",
                        },
                    ))
            
            return items
```

### 8.4 Posting Queue

```python
# src/poster/queue.py
import tweepy
import time
import random
import json
from pathlib import Path

class PostQueue:
    def __init__(self, niche: str, credentials: dict):
        self.niche = niche
        self.client = tweepy.Client(
            consumer_key=credentials["api_key"],
            consumer_secret=credentials["api_secret"],
            access_token=credentials["access_token"],
            access_token_secret=credentials["access_token_secret"],
        )
        # v1.1 API needed for media uploads
        auth = tweepy.OAuth1UserHandler(
            credentials["api_key"],
            credentials["api_secret"],
            credentials["access_token"],
            credentials["access_token_secret"],
        )
        self.api_v1 = tweepy.API(auth)
    
    def post_next(self, db) -> bool:
        """Post the highest priority queued tweet. Returns True if posted."""
        row = db.execute(
            """SELECT id, tweet_text, media_paths FROM tweet_queue
               WHERE niche = ? AND status = 'queued'
               ORDER BY priority ASC, scheduled_for ASC
               LIMIT 1""",
            (self.niche,)
        ).fetchone()
        
        if not row:
            return False
        
        tweet_id_db, text, media_paths_json = row
        media_ids = []
        
        # Upload media if present
        if media_paths_json:
            paths = json.loads(media_paths_json)
            for path in paths:
                if Path(path).exists():
                    media = self.api_v1.media_upload(filename=path)
                    media_ids.append(media.media_id)
        
        try:
            response = self.client.create_tweet(
                text=text,
                media_ids=media_ids if media_ids else None,
            )
            tweet_id = response.data["id"]
            
            db.execute(
                """UPDATE tweet_queue SET status='posted', posted_at=datetime('now'), tweet_id=?
                   WHERE id=?""",
                (tweet_id, tweet_id_db)
            )
            db.commit()
            return True
            
        except tweepy.TweepyException as e:
            db.execute(
                "UPDATE tweet_queue SET status='failed', error=? WHERE id=?",
                (str(e), tweet_id_db)
            )
            db.commit()
            return False
```

### 8.5 Rate Limiting + Jitter

```python
# src/poster/rate_limiter.py
import time
import random

class RateLimiter:
    """
    X Free tier: 1,500 tweets/month per account = ~50/day = ~1 every 30 min.
    We target 8-15 tweets/day per account to stay well within limits.
    """
    
    MIN_INTERVAL = 1200   # 20 minutes minimum between posts
    MAX_INTERVAL = 3600   # 60 minutes maximum between posts
    
    def __init__(self):
        self.last_post_time = 0
    
    def wait_if_needed(self):
        """Block until it's safe to post again with random jitter."""
        elapsed = time.time() - self.last_post_time
        interval = random.randint(self.MIN_INTERVAL, self.MAX_INTERVAL)
        
        if elapsed < interval:
            wait = interval - elapsed
            # Add extra jitter of 0-120 seconds so timing isn't predictable
            wait += random.randint(0, 120)
            time.sleep(wait)
        
        self.last_post_time = time.time()
```

---

## 9. Posting Schedule

Target: **8-15 tweets per day per account** (well within free tier limits).

### Rocket League
| Time (UTC) | Type | Notes |
|-----------|------|-------|
| 08:00 | Morning news roundup | EU morning |
| 10:00 | Reddit highlight | Best community post overnight |
| 12:00 | Esports news / roster moves | If available |
| 14:00 | Item shop (if rotated) | Daily item shop reset |
| 16:00 | Patch notes / game updates | When available |
| 18:00 | Esports results | Live events coverage |
| 20:00 | Community highlight | Clips, fan art, discussions |
| 22:00 | Evening update | US evening |

### Geometry Dash
| Time (UTC) | Type | Notes |
|-----------|------|-------|
| 08:00 | Daily level post | New daily level |
| 10:00 | Demon list update | If changes overnight |
| 12:00 | Reddit highlight | Best community post |
| 14:00 | Level verified / beaten | New completions |
| 16:00 | Creator content / YouTube | New videos from GD YouTubers |
| 18:00 | RobTop / game updates | When available |
| 20:00 | Community highlight | Levels, creators, mods |
| 22:00 | Weekly demon post (if Monday) | Weekly demon reset |

**Breaking news override:** If a high-priority item comes in (new game update, top 1 demon verified, RLCS finals result), post immediately regardless of schedule.

---

## 10. Configuration

### .env.example

```bash
# Rocket League X Account
RL_API_KEY=
RL_API_SECRET=
RL_ACCESS_TOKEN=
RL_ACCESS_TOKEN_SECRET=

# Geometry Dash X Account  
GD_API_KEY=
GD_API_SECRET=
GD_ACCESS_TOKEN=
GD_ACCESS_TOKEN_SECRET=

# Reddit API
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=autopost:v1.0

# YouTube Data API
YOUTUBE_API_KEY=

# Discord Webhook (for alerts)
DISCORD_WEBHOOK_URL=

# Database
DB_PATH=./data/autopost.db

# General
LOG_LEVEL=INFO
DRY_RUN=false
```

### rocketleague.yaml (example)

```yaml
niche: rocketleague
account:
  env_prefix: RL  # loads RL_API_KEY, RL_API_SECRET, etc.

sources:
  - name: r/RocketLeague
    type: reddit
    subreddit: RocketLeague
    min_score: 100
    poll_interval: 300  # 5 min

  - name: r/RocketLeagueEsports
    type: reddit
    subreddit: RocketLeagueEsports
    min_score: 50
    poll_interval: 300

  - name: Rocket League Blog
    type: rss
    url: https://www.rocketleague.com/news/rss
    poll_interval: 900  # 15 min

  - name: "@RocketLeague"
    type: twitter
    account_id: "RocketLeague"
    poll_interval: 300

  - name: "@RLEsports"
    type: twitter
    account_id: "RLEsports"
    poll_interval: 300

  - name: Steam News
    type: rss
    url: https://store.steampowered.com/feeds/news/app/252950
    poll_interval: 1800

  - name: Octane.gg
    type: api
    collector: octane
    poll_interval: 900

posting:
  min_interval_seconds: 1200
  max_interval_seconds: 3600
  max_daily_posts: 15
  breaking_news_override: true

hashtags:
  default: ["#RocketLeague"]
  esports: ["#RocketLeague", "#RLCS"]
  update: ["#RocketLeague", "#RLUpdate"]
```

---

## 11. systemd Service

### autopost.service

```ini
[Unit]
Description=AutoPost X News Bot
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/home/your-user/autopost
ExecStart=/home/your-user/autopost/venv/bin/python -m src.main
Restart=always
RestartSec=30
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
# Deploy
sudo cp autopost.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable autopost
sudo systemctl start autopost

# Logs
sudo journalctl -u autopost -f
```

---

## 12. requirements.txt

```
tweepy>=4.14
asyncpraw>=7.7
feedparser>=6.0
httpx>=0.27
beautifulsoup4>=4.12
Pillow>=10.0
pyyaml>=6.0
python-dotenv>=1.0
loguru>=0.7
apscheduler>=3.10
```

---

## 13. Implementation Order

Build in this exact order. Each step should be testable independently.

1. **Project setup** — create structure, venv, install deps, .env, settings.py
2. **Database** — schema.sql, db.py with helpers (insert, dedup check, get queue)
3. **RSS collector** — simplest collector, test with RL Steam News feed
4. **Template formatter** — take a hardcoded RawContent, output tweet text
5. **Posting client** — post a single test tweet to one account (dry run first)
6. **Wire RSS → formatter → poster** — end-to-end for one source
7. **Reddit collector** — add Reddit, test with r/RocketLeague
8. **Pointercrate collector** — GD demon list API
9. **GDBrowser collector** — daily level, weekly demon, rated levels
10. **Twitter monitor** — watch official accounts, retweet/quote
11. **Media handling** — download images, resize, attach to tweets
12. **Scheduling** — APScheduler or cron to run the full pipeline
13. **Second niche** — duplicate config for GD, point to GD sources + account
14. **systemd deployment** — deploy on droplet, enable service
15. **Monitoring** — Discord webhook alerts for failures
16. **YouTube collector** — optional, add last

---

## 14. Important Notes

- **X Free tier limits:** 1,500 tweets/month per app. That's ~50/day across all endpoints. With 2 accounts posting ~10-15/day each, you'll use ~600-900/month. Comfortable margin.
- **No automated label:** Don't enable the automated account label in X settings. Just post via the API like a normal account.
- **Vary timing:** The jitter in the rate limiter is critical. Never post at exact intervals — it looks like a bot.
- **Image priority:** Always try to attach an image. Tweets with images get significantly more engagement. Download from source, resize to 1200x675 (16:9) for consistency.
- **Dedup is critical:** The UNIQUE constraint on `(source_id, external_id)` prevents double-posting. Always INSERT OR IGNORE.
- **Don't over-post:** Quality > quantity. 10 solid news tweets beat 30 low-effort ones. Set `min_score` thresholds on Reddit collectors.
- **Breaking news fast:** For high-priority items, bypass the queue and post immediately. Speed is the #1 differentiator for news pages.
