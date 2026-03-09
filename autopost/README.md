# AutoPost — RLWire & GDWire

Automated X (Twitter) news bots for **Rocket League** ([@rl_wire1](https://x.com/rl_wire1)) and **Geometry Dash** ([@gd_wire](https://x.com/gd_wire)).

No AI generation. Pure template-based posting. Runs 24/7 on a DigitalOcean droplet via systemd.

---

## How it works

```
Sources (RSS / APIs / YouTube / X syndication scraping)
        |  collectors/
SQLite raw_content  <-  dedup (UNIQUE source_id + external_id)
        |  formatter/
tweet_queue  <-  priority 1 (breaking) -> 8 (filler)
        |  poster/
X API  <-  rate limiter (20-min min gap, 1,500/month cap)
        |
post_log  ->  Discord alerts on failure
```

---

## Sources (51 active)

### Rocket League (22 sources)

| Type | Sources |
|------|---------|
| Twitter | @RocketLeague, @RLEsports, @PsyonixStudios, @RLCS, @ShiftRLE, @ApparentlyJxck, @Flakes_RL |
| YouTube | RLEsports Official, SunlessKhan, Lethamyr, Musty, Wayton Pilkin, ApparentlyJack, Flakes |
| Scrapers | BLAST.tv, ONE Esports, Dexerto, The Loadout |
| RSS | Steam News |
| APIs | Octane.gg (flashbacks + stats) |

### Geometry Dash (29 sources)

| Type | Sources |
|------|---------|
| Twitter | @RobTopGames, @_GeometryDash, @today_gd, @demonlistgd, @geode_sdk, @GDW_ORG, @vipringd, @zNpesta__ |
| YouTube | EVW, Wulzy, GD Colon, Nexus, ItzBran, Viprin, Juniper, Moldy, Knobbelboy, Doggie, AeonAir, npesta |
| Scrapers | RobTop Website, AREDL, GDDP, Geode Mods Catalog, Speedrun.com |
| RSS | Steam News (GD) |
| APIs | Pointercrate Demon List, GDBrowser, Geode SDK (GitHub) |

Twitter monitoring uses **syndication scraping** — no API read credentials needed.

---

## Project structure

```
autopost/
├── config/
│   ├── settings.py            # loads .env, typed config
│   ├── rocketleague.yaml      # RL sources, schedule, hashtags
│   └── geometrydash.yaml      # GD sources, schedule, hashtags
├── src/
│   ├── main.py                # entry point — APScheduler
│   ├── collectors/
│   │   ├── base.py            # RawContent dataclass + BaseCollector ABC
│   │   ├── rss.py             # feedparser RSS/Atom
│   │   ├── scraper.py         # BeautifulSoup headline scraper
│   │   ├── twitter_monitor.py # syndication scraping (no API creds needed)
│   │   ├── youtube.py         # YouTube Data API v3
│   │   └── apis/
│   │       ├── pointercrate.py  # GD demon list
│   │       ├── gdbrowser.py     # GD daily/weekly/rated levels
│   │       ├── octane.py        # RL esports results (Octane.gg)
│   │       ├── flashback.py     # RL "on this day" historical content
│   │       ├── rl_stats.py      # RL all-time stat milestones
│   │       └── github.py        # GitHub releases (Geode SDK)
│   ├── formatter/
│   │   ├── templates.py       # 77 tweet template variants (both niches)
│   │   ├── formatter.py       # RawContent -> tweet text, 280-char enforcement
│   │   └── media.py           # download + resize images to 1200x675
│   ├── poster/
│   │   ├── client.py          # Tweepy v2 wrapper + DRY_RUN mode
│   │   ├── queue.py           # collect_and_queue, post_next, skip_stale
│   │   └── rate_limiter.py    # 20-min gap, 1500/month cap, jitter
│   ├── database/
│   │   ├── schema.sql         # sources, raw_content, tweet_queue, post_log
│   │   └── db.py              # SQLite helpers + similarity dedup
│   └── monitoring/
│       ├── alerts.py          # Discord webhook alerts
│       └── health_check.py    # daily source integrity check (03:05 UTC)
├── tests/                     # 100 unit tests (pytest)
│   ├── test_formatter.py      # _cap, _truncate, emoji, templates
│   ├── test_collectors.py     # age filter, syndication parsing, classifier
│   ├── test_media.py          # dimension check, resize, hash paths
│   ├── test_rate_limiter.py   # posting window, jitter, constants
│   └── test_db.py             # timestamps, similarity dedup
├── scripts/
│   ├── setup_db.py            # init DB + seed sources from YAML
│   ├── full_pipeline_test.py  # integration test (10 sections)
│   └── test_collector.py      # quick smoke test
├── data/                      # gitignored — autopost.db + media/
├── logs/                      # gitignored — rotating daily logs
├── requirements.txt
├── pytest.ini
├── .env.example
└── autopost.service           # systemd unit file
```

---

## Setup

### 1. Clone & install

```bash
git clone https://github.com/AliBars19/automation-network.git
cd automation-network/autopost
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
nano .env   # fill in your API keys
```

| Key | Where to get it |
|-----|----------------|
| `RL_API_KEY` + 3 others | [developer.twitter.com](https://developer.twitter.com/en/portal/dashboard) — create app for @rl_wire1 |
| `GD_API_KEY` + 3 others | Same portal — create app for @gd_wire |
| `YOUTUBE_API_KEY` | [console.cloud.google.com](https://console.cloud.google.com/apis/library/youtube.googleapis.com) |
| `DISCORD_WEBHOOK_URL` | Discord server -> Integrations -> Webhooks (optional) |

### 3. Initialise the database

```bash
python scripts/setup_db.py
# -> rocketleague: 21 sources seeded
# -> geometrydash: 29 sources seeded
```

### 4. Run tests

```bash
python -m pytest tests/ -v
# 100 passed in <1s
```

### 5. Smoke test

```bash
DRY_RUN=true python scripts/test_collector.py
```

### 6. Run

```bash
# Development
DRY_RUN=true python src/main.py

# Production (systemd)
sudo cp autopost.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now autopost
sudo journalctl -u autopost -f
```

---

## Deployment (DigitalOcean)

### Requirements
- Ubuntu 22.04+ droplet (1 GB RAM / 1 vCPU is sufficient)
- Python 3.12+
- sqlite3 CLI for backups
- All API credentials from the table above

### Deploy flow

```bash
# On the droplet:
cd /root/automation-network
git pull
cd autopost
source venv/bin/activate
pip install -r requirements.txt
python scripts/setup_db.py
sudo systemctl restart autopost
```

### Re-enabling a disabled source

Sources are auto-disabled after 10 failures in 1 hour. To re-enable:

```bash
sqlite3 data/autopost.db "UPDATE sources SET enabled = 1 WHERE name = 'Source Name';"
sudo systemctl restart autopost
```

---

## Monitoring

- **Daily health check** runs at 03:05 UTC — probes every enabled source and sends a Discord report (healthy/degraded/dead)
- **Discord alerts** fire after 3 consecutive collector failures
- **Source auto-disable** after 10 errors in 1 hour
- **Log files** rotate daily with 14-day retention

```bash
# Live logs
sudo journalctl -u autopost -f

# Queue depth
sqlite3 data/autopost.db \
  "SELECT niche, status, COUNT(*) FROM tweet_queue GROUP BY niche, status;"

# Today's posts
sqlite3 data/autopost.db \
  "SELECT niche, COUNT(*) FROM post_log WHERE posted_at >= date('now') GROUP BY niche;"
```

---

## Posting schedule

- **Target:** 8-15 tweets/day per account
- **Window:** UTC 08:00-22:00
- **Gap:** minimum 20 min between posts (rate limiter enforced)
- **Cap:** 1,500 tweets/month (X Free tier limit)
- **Breaking news** (priority 1): bypasses queue delay, posts immediately
- **Content filtering:** only tweets from the last 7 days are collected

---

## Tech stack

Python 3.12 · Tweepy · feedparser · httpx · BeautifulSoup · SQLite · APScheduler · Pillow · loguru · pytest · systemd
