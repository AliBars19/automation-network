# AutoPost Pipeline Test Results

**Run at:** 2026-03-18 11:06 UTC

**Platform:** Python 3.11.9 on Windows

**Note:** Tests requiring credentials (X API, YouTube) are skipped — marked as ⏭️


---


## 1. Database Integrity

- ✅ init_db() completed without error
- ✅ All 4 tables present: post_log, raw_content, source_errors, sources, sqlite_sequence, tweet_queue
- ℹ️ post_log: 0 rows
- ℹ️ tweet_queue: 182 rows
- ℹ️ raw_content: 182 rows
- ℹ️ sources: 68 rows
- ✅ Sources seeded — geometrydash: 35 sources
- ✅ Sources seeded — rocketleague: 33 sources

> [PASS] 4/4 checks passed, 0 warnings


## 2. RSS Collectors


**Steam News (RL)**

- ✅ Fetched 10 entries in 0.39s
- ℹ️ Content types: {'patch_notes': 6, 'season_start': 2, 'event_announcement': 1, 'collab_announcement': 1}
- ℹ️ Sample tweet (patch_notes, 222 chars):
```
RL vv2.66 is now available.

Version : Rocket League v2.66 Season 22 Live ⁠ Platforms : Epic Games Store, Steam, PlayStation, Xbox, Nintendo ⁠ Scheduled Release : March 11, 2026 9 AM PT / 4 PM UTC THE HEADLINES New Rocket…
```

**Steam News (GD)**

- ✅ Fetched 10 entries in 0.30s
- ℹ️ Content types: {'game_update': 10}
- ℹ️ Sample tweet (game_update, 110 chars):
```
The Geometry Dash Awards 2025: Winners

https://store.steampowered.com/news/app/322170/view/498347586726396669
```

**RL Blog**

- ⚠️ No entries returned (feed may be empty or unreachable) in 0.10s

> [PASS] 2/2 checks passed, 1 warnings


## 3. Pointercrate (GD Demon List)

- ✅ Fetched 50 demons in 0.83s
- ℹ️ Content type breakdown: {'top1_verified': 1, 'level_verified': 49}

**Current #1: Thinking Space II**

- ℹ️ Verifier: [67] Zoink
- ℹ️ Position metadata: 1
- ✅ top1_verified tweet (128 chars):
```
"Thinking Space II" has been verified by [67] Zoink — the new #1 on the Demon List.

https://www.youtube.com/watch?v=CELNmHwln_c
```
- ℹ️ level_verified sample (Flamewall, #2):
```
BREAKING: "Flamewall" has been verified by [400] CuatrocientosYT, placed at #2 on the Demon List.
```

> [PASS] 2/2 checks passed, 0 warnings


## 4. GDBrowser API

- ⚠️ Daily level returned no data (GDBrowser server-side issue on sentinel IDs)
- ⚠️ Weekly demon returned no data (same GDBrowser issue)
- ❌ No rated levels returned

> [FAIL] 0/1 checks passed, 2 warnings


## 5. Octane.gg (RL Esports)

- ⚠️ No match results returned (may be off-season)
- ⚠️ No upcoming matches returned

> [PASS] 0/0 checks passed, 2 warnings


## 6. Formatter — All Content Types


### Rocket League

- ✅ `patch_notes` (146 chars)
```
Rocket League v2.64 patch notes are up 👀

Here's what's changing:
- New car body added
- Bug fixes
- Performance improvements

https://example.com
```
- ✅ `esports_result` (97 chars)
```
RLCS World Championship Grand Finals

Vitality 4-2 NRG

Vitality are your RLCS Worlds Champions 🏆
```
- ✅ `esports_matchup` (74 chars)
```
MATCH ALERT 🚨

G2 vs Faze
RLCS Spring Major — Quarterfinals

18:00 UTC UTC
```
- ✅ `roster_change` (29 chars)
```
NRG sign jstn for Season 14 🔄
```
- ✅ `item_shop` (111 chars)
```
TODAY'S ITEM SHOP IS LIVE 🛒

• Titanium White Octane
• Black Market Decal: Heatwave
• Goal Explosion: Fireworks
```
- ✅ `season_start` (105 chars)
```
SEASON 14 IS HERE 🚀

• New ranked rewards
• Updated item shop
• Season pass launched

https://example.com
```
- ✅ `collab_announcement` (71 chars)
```
Spongebob x Rocket League is CONFIRMED 👀

Spongebob themed car + decals
```
- ✅ `community_clip` (35 chars)
```
Test title 🔥

📎 https://example.com
```

### Geometry Dash

- ✅ `top1_verified` (119 chars)
```
Zoink has verified "Abyss of Darkness", now the hardest rated level in Geometry Dash.

First ever sub-4% verified level
```
- ✅ `demon_list_update` (97 chars)
```
Demon List Update:

Abyss of Darkness enters at #1
Slaughterhouse moves to #2
Gelatin drops to #3
```
- ✅ `level_verified` (85 chars)
```
Dolphy has verified "Tartarus" (#3 on the Demon List).

Test body text for this item.
```
- ✅ `level_beaten` (91 chars)
```
Manix648 beats "Bloodbath", currently #17 on the Demon List.

Test body text for this item.
```
- ✅ `game_update` (99 chars)
```
Geometry Dash 2.3 is out now.

Test body text for this item.

Available on Steam, iOS, and Android.
```
- ✅ `level_rated` (64 chars)
```
"Sonic Wave" by Cyclic just got rated. Extreme Demon — 10 stars.
```
- ✅ `daily_level` (69 chars)
```
"Theory of Everything 2" by Partition is today's Daily Level. Insane.
```
- ✅ `weekly_demon` (61 chars)
```
This week's Weekly Demon: "Bloodbath" by Riot. Extreme Demon.
```
- ✅ `mod_update` (82 chars)
```
Geode 2.1.0 has been released.

Test body text for this item.

https://example.com
```
- ✅ `speedrun_wr` (76 chars)
```
Doggie breaks the All Icons% world record with 1:24:37.

https://example.com
```

> [PASS] 18/18 checks passed, 0 warnings


## 7. Media Handling (Download + Resize)

- ✅ RL Steam header: 1200×675px, 141KB (0.00s)
- ✅ GD Steam header: 1200×675px, 98KB (0.00s)
- ✅ Pointercrate thumb: 1200×675px, 64KB (0.00s)

> [PASS] 3/3 checks passed, 0 warnings


## 8. Queue & Deduplication

- ✅ Dedup working — re-running RSS collector added 0 new items
- ✅ RL queue: 3 queued, 0 posted
- ✅ GD queue: 0 queued, 0 posted

**RL top-5 queued (by priority)**

- ℹ️ [p2] NEW ROCKET LEAGUE UPDATE (v2.66) OUT NOW:  • Version : Rocket League v2.66 Seaso…
- ℹ️ [p2] RL vv2.66 is now available.  Version : Rocket League v2.66 ⁠ Platforms : Epic Ga…
- ℹ️ [p2] Rocket League Season 22: Training, Rivalries & Rewards  https://store.steampower…

**GD top-5 queued (by priority)**


> [PASS] 3/3 checks passed, 0 warnings


## 9. Rate Limiter

- ℹ️ [rocketleague] can_post=True, monthly_posts=0/1500
- ✅ [rocketleague] within monthly limit (0/1500)
- ℹ️ [geometrydash] can_post=True, monthly_posts=0/1500
- ✅ [geometrydash] within monthly limit (0/1500)

> [PASS] 2/2 checks passed, 0 warnings


## 10. DRY_RUN Poster

- ✅ [rocketleague] would post [p2]: NEW ROCKET LEAGUE UPDATE (v2.66) OUT NOW:  • Version : Rocket League v2.66 Season 22 Live ⁠ Platforms : Epic Games Store…
- ℹ️ [rocketleague] media_path: C:\Users\aliba\Downloads\automation-network\autopost\data\media\63b34ce18c4bb4ff.jpg
- ⚠️ [geometrydash] queue empty, nothing to dry-post

> [PASS] 1/1 checks passed, 1 warnings


## ⏭️ Skipped (credentials required)

- **Twitter monitor** — uses twscrape (requires TWSCRAPE_COOKIES in .env)
- **YouTube collector** — needs `YOUTUBE_API_KEY`
- **Live posting** — needs X API keys + `DRY_RUN=false`


---

## Overall Summary

| # | Test | Result |
|---|------|--------|
| 1 | 1. Database integrity | ✅ PASS — [PASS] 4/4 checks passed, 0 warnings |
| 2 | 2. RSS Collectors | ⚠️ WARN — [PASS] 2/2 checks passed, 1 warnings |
| 3 | 3. Pointercrate | ✅ PASS — [PASS] 2/2 checks passed, 0 warnings |
| 4 | 4. GDBrowser | ❌ FAIL — [FAIL] 0/1 checks passed, 2 warnings |
| 5 | 5. Octane.gg | ⚠️ WARN — [PASS] 0/0 checks passed, 2 warnings |
| 6 | 6. Formatter | ✅ PASS — [PASS] 18/18 checks passed, 0 warnings |
| 7 | 7. Media | ✅ PASS — [PASS] 3/3 checks passed, 0 warnings |
| 8 | 8. Queue + Dedup | ✅ PASS — [PASS] 3/3 checks passed, 0 warnings |
| 9 | 9. Rate Limiter | ✅ PASS — [PASS] 2/2 checks passed, 0 warnings |
| 10 | 10. DRY_RUN Poster | ⚠️ WARN — [PASS] 1/1 checks passed, 1 warnings |

**Total: 35 passed, 1 failed, 6 warnings**