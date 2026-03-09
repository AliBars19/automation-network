# AutoPost — Knowledge Base

Everything learned during development, credential setup, and deployment.

---

## Credentials Setup

### X API (Twitter) — OAuth 1.0a

Both bots use OAuth 1.0a (Consumer Key + Consumer Secret + Access Token + Access Token Secret).

**How to get credentials:**
1. Log into [developer.x.com](https://developer.x.com) as the bot account (e.g. @gd_wire)
2. Create a new app
3. Set up **User Authentication Settings** → select "Read and Write"
4. Go to **Keys and tokens**
5. Generate **Consumer Keys** first → save both values
6. Generate **Access Token and Secret** immediately after → save both values

**Critical rules:**
- The Access Token MUST be generated AFTER the Consumer Key. If you regenerate the Consumer Key later, the Access Token is invalidated.
- The "Generate" button in the developer portal creates tokens for the **currently logged-in account only**. Make sure you're logged in as the correct bot account.
- The number before the `-` in the Access Token (e.g. `2027706428111343618-xxxxx`) is the user ID of the token owner. Use this to verify which account the token belongs to.

**Error codes:**
- `401 error code 32` — OAuth signature mismatch. Usually means the Access Token was generated before the current Consumer Key. Regenerate the Access Token.
- `401 error code 89` — Invalid or expired token. Same root cause as above.
- `401 on v1.1 endpoints` — Normal on Free tier. Free tier is write-only (POST /2/tweets). Use v2 Client for posting.

**Current accounts:**
- GDWire: `@gd_wire` — user ID `2027706428111343618`
- RLWire: `@rl_wire1` — user ID `2028019551276019712`

### YouTube Data API v3

- Only needs an **API key** (not OAuth) — read-only access to public data
- Get it from [Google Cloud Console](https://console.cloud.google.com/) → create project → enable YouTube Data API v3 → create API key
- Free quota: 10,000 units/day (bot uses ~480/day for 10 channels at 30-min intervals)
- Two-step fetch: resolve uploads playlist via `/channels`, then get videos via `/playlistItems`

### Discord Webhook

- No API application needed
- Discord server → channel settings → Integrations → Webhooks → New Webhook → copy URL
- Used for alerts when sources fail or bots go down

---

## Deployment (DigitalOcean)

### Current setup
- **Droplet hostname:** macbookvisuals
- **Repo location:** `/root/automation-network/autopost/`
- **Running as:** root (service file updated from default `/home/autopost/` paths)
- **Service file:** `/etc/systemd/system/autopost.service`
- **Python venv:** `/root/automation-network/autopost/venv/`

### Service file path fix

The default `autopost.service` assumes a dedicated `autopost` user at `/home/autopost/`. If running as root:
```bash
sudo sed -i 's|User=autopost|User=root|' /etc/systemd/system/autopost.service
sudo sed -i 's|Group=autopost|Group=root|' /etc/systemd/system/autopost.service
sudo sed -i 's|/home/autopost|/root|g' /etc/systemd/system/autopost.service
sudo systemctl daemon-reload
```

### First-time setup on droplet
```bash
git clone https://github.com/AliBars19/automation-network.git
cd automation-network/autopost
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env                          # paste all API keys
python scripts/setup_db.py         # seed sources from YAML configs
DRY_RUN=true python src/main.py    # smoke test, Ctrl+C to stop
```

### DRY_RUN mode
- `DRY_RUN=true` — collects, formats, queues, but does NOT post to X. Logs what it would post.
- `DRY_RUN=false` — fully live, posts to X for real.
- Set in `.env` file, requires service restart to take effect.

---

## Troubleshooting

### "Run time of job was missed by 0:00:01"
Normal on startup. All 67 sources fire at once when the scheduler starts. APScheduler's `coalesce=True` ensures they still run. Not an error.

### Service fails with "unavailable resources"
The service file paths don't match the actual repo location. Check `WorkingDirectory`, `ExecStart`, and `EnvironmentFile` in the service file match where the repo actually is.

### DRY_RUN test fails in full_pipeline_test.py
`DRY_RUN` is evaluated at module import time. Setting `os.environ["DRY_RUN"]` after import has no effect. The test patches module-level variables directly:
```python
import src.poster.client as _client_mod
import config.settings as _settings_mod
_settings_mod.DRY_RUN = True
_client_mod.DRY_RUN = True
```

### "python: command not found" on Ubuntu
Use `python3` instead of `python`. Or activate the venv first (`source venv/bin/activate`) which aliases it.

### 401 on all X API endpoints
1. Check which account the Access Token belongs to (user ID before the `-`)
2. Regenerate Consumer Key, then immediately regenerate Access Token
3. Make sure you're logged into the correct account on developer.x.com

---

## Architecture Notes

### Posting schedule
- Poster job runs every 2 minutes per niche
- Rate limiter enforces 20-minute minimum gap between posts
- Monthly cap: 1,500 tweets per account (X Free tier limit)
- Breaking news (priority 1) bypasses queue delay
- Stale queue cleanup runs every 6 hours
- Daily DB cleanup at 03:00 UTC (30-day rolling window)

### Source health tracking
- Errors logged to `source_errors` table
- Discord alert after 3 errors in 1 hour
- Source auto-disabled after 10 errors in 1 hour
- Re-enable: `UPDATE sources SET enabled = 1 WHERE name = 'Source Name';`

### Cross-source deduplication
- `is_similar_story()` uses difflib.SequenceMatcher (0.65 threshold, 24h window)
- `url_already_queued()` prevents same URL from two different sources
- `INSERT OR IGNORE` on `(source_id, external_id)` for exact-match dedup

### Known limitations
- GDBrowser daily/weekly level endpoints sometimes return no data (server-side issue)
- Octane.gg returns nothing during RL esports off-season
- Twitter monitor uses syndication scraping (no API credentials needed for reads)
