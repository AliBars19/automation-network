# AutoPost — RLWire & GDWire

Automated X (Twitter) news bots for **Rocket League** ([@RLWire](https://x.com/RLWire)) and **Geometry Dash** ([@GDWire](https://x.com/GDWire)).

No AI generation. Pure template-based posting. Runs 24/7 on a DigitalOcean droplet via systemd.

---

## How it works

```
Sources (RSS / Reddit / APIs / YouTube / X)
        ↓  collectors/
SQLite raw_content  ←  dedup (UNIQUE source_id + external_id)
        ↓  formatter/
tweet_queue  ←  priority 1 (breaking) → 8 (filler)
        ↓  poster/
X API  ←  rate limiter (20-min min gap, 1,500/month cap)
        ↓
post_log  →  Discord alerts on failure
```

---

## Project structure

```
autopost/
├── config/
│   ├── settings.py          # loads .env, typed config
│   ├── rocketleague.yaml    # RL sources, schedule, hashtags
│   └── geometrydash.yaml    # GD sources, schedule, hashtags
├── src/
│   ├── main.py              # entry point — APScheduler
│   ├── collectors/
│   │   ├── base.py          # RawContent dataclass + BaseCollector ABC
│   │   ├── rss.py           # feedparser RSS/Atom
│   │   ├── reddit.py        # asyncpraw subreddit hot posts
│   │   ├── twitter_monitor.py  # watch official X accounts
│   │   ├── youtube.py       # YouTube Data API v3
│   │   └── apis/
│   │       ├── pointercrate.py  # GD demon list
│   │       ├── gdbrowser.py     # GD daily/weekly/rated levels
│   │       └── octane.py        # RL esports results (Octane.gg)
│   ├── formatter/
│   │   ├── templates.py     # all tweet templates (both niches)
│   │   ├── formatter.py     # RawContent → tweet text, 280-char enforcement
│   │   └── media.py         # download + resize images to 1200×675
│   ├── poster/
│   │   ├── client.py        # Tweepy v2 wrapper + DRY_RUN mode
│   │   ├── queue.py         # collect_and_queue, post_next, skip_stale
│   │   └── rate_limiter.py  # 20-min gap, 1500/month cap, jitter
│   ├── database/
│   │   ├── schema.sql       # sources, raw_content, tweet_queue, post_log
│   │   └── db.py            # SQLite helpers
│   └── monitoring/
│       └── alerts.py        # Discord webhook alerts
├── scripts/
│   ├── setup_db.py          # init DB + seed sources from YAML
│   └── test_collector.py    # pipeline smoke test (no credentials needed)
├── data/                    # gitignored — autopost.db + media/
├── logs/                    # gitignored — rotating daily logs
├── requirements.txt
├── .env.example
└── autopost.service         # systemd unit file
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

Required credentials:

| Key | Where to get it |
|-----|----------------|
| `RL_API_KEY` + 3 others | [developer.twitter.com](https://developer.twitter.com/en/portal/dashboard) — create app for @RLWire |
| `GD_API_KEY` + 3 others | Same portal — create app for @GDWire |
| `REDDIT_CLIENT_ID/SECRET` | [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) |
| `YOUTUBE_API_KEY` | [console.cloud.google.com](https://console.cloud.google.com/apis/library/youtube.googleapis.com) |
| `DISCORD_WEBHOOK_URL` | Discord server → Integrations → Webhooks (optional) |

### 3. Initialise the database

```bash
python scripts/setup_db.py
# → rocketleague: 22 sources seeded
# → geometrydash: 26 sources seeded
```

### 4. Smoke test (no credentials needed)

```bash
DRY_RUN=true python scripts/test_collector.py
```

### 5. Run

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

```bash
# On the droplet
sudo useradd -m -s /bin/bash autopost
sudo -u autopost git clone https://github.com/AliBars19/automation-network.git
# ... follow Setup steps above as the autopost user
sudo cp autopost/autopost.service /etc/systemd/system/
sudo systemctl enable --now autopost
```

---

## Posting schedule

- **Target:** 8–15 tweets/day per account
- **Window:** UTC 08:00–22:00
- **Gap:** minimum 20 min between posts (rate limiter enforced)
- **Cap:** 1,500 tweets/month (X Free tier limit)
- **Breaking news** (priority 1): bypasses queue delay, posts immediately

---

## Tech stack

Python 3.12 · Tweepy · asyncpraw · feedparser · httpx · SQLite · APScheduler · Pillow · loguru · systemd
