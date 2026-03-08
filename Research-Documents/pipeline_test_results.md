# AutoPost Pipeline Test Results

**Run at:** 2026-03-01 21:33 UTC

**Platform:** Python 3.11.9 on Windows

**Note:** Tests requiring credentials (Reddit, X API, YouTube) are skipped — marked as ⏭️


---


## 1. Database Integrity

- ✅ init_db() completed without error
- ✅ All 4 tables present: post_log, raw_content, source_errors, sources, sqlite_sequence, tweet_queue
- ℹ️ post_log: 2 rows
- ℹ️ raw_content: 179 rows
- ℹ️ tweet_queue: 179 rows
- ℹ️ sources: 67 rows
- ✅ Sources seeded — geometrydash: 34 sources
- ✅ Sources seeded — rocketleague: 33 sources

> [PASS] 4/4 checks passed, 0 warnings


## 2. RSS Collectors


**Steam News (RL)**

- ✅ Fetched 10 entries in 0.34s
- ℹ️ Content types: {'event_announcement': 1, 'season_start': 3, 'patch_notes': 4, 'collab_announcement': 2}
- ℹ️ Sample tweet (event_announcement, 174 chars):
```
Get Drops While Cheering On Your Favorite Team in the RLCS Boston Major — Day  is underway 🎮

Schedule: https://store.steampowered.com/news/app/252950/view/738163097677595118
```

**Steam News (GD)**

- ✅ Fetched 10 entries in 0.31s
- ℹ️ Content types: {'game_update': 10}
- ℹ️ Sample tweet (game_update, 243 chars):
```
Geometry Dash latest is now live.

The winners of The Geometry Dash Awards 2025 have been decided! Watch the video to see the best that Geometry Dash had to offer in 2025.

https://store.steampowered.com/news/app/322170/view/498347586726396669
```

**RL Blog**

- ⚠️ No entries returned (feed may be empty or unreachable) in 0.12s

> [PASS] 2/2 checks passed, 1 warnings


## 3. Pointercrate (GD Demon List)

- ✅ Fetched 50 demons in 0.64s
- ℹ️ Content type breakdown: {'top1_verified': 1, 'level_verified': 49}

**Current #1: Thinking Space II**

- ℹ️ Verifier: [67] Zoink
- ℹ️ Position metadata: 1
- ✅ top1_verified tweet (114 chars):
```
THE NEW TOP 1 IS HERE 🚨

"Thinking Space II" — verified by [67] Zoink

https://www.youtube.com/watch?v=CELNmHwln_c
```
- ℹ️ level_verified sample (Flamewall, #2):
```
[400] CuatrocientosYT — verified "Flamewall" (#2 on Demon List) 🏆
```

> [PASS] 2/2 checks passed, 0 warnings


## 4. GDBrowser API

- ⚠️ Daily level returned no data (GDBrowser server-side issue on sentinel IDs)
- ⚠️ Weekly demon returned no data (same GDBrowser issue)
- ✅ Rated levels: 10 fetched in 0.85s
- ℹ️ Sample rated level tweet (63 chars):
```
"Autoplay 2" by moneyking23 just got rated ⭐

Unrated — 0 stars
```

> [PASS] 1/1 checks passed, 2 warnings


## 5. Octane.gg (RL Esports)

- ⚠️ No match results returned (may be off-season)
- ⚠️ No upcoming matches returned

> [PASS] 0/0 checks passed, 2 warnings


## 6. Formatter — All Content Types


### Rocket League

- ✅ `patch_notes` (123 chars)
```
NEW ROCKET LEAGUE UPDATE (v2.64) OUT NOW:

• New car body added
• Bug fixes
• Performance improvements

https://example.com
```
- ✅ `esports_result` (51 chars)
```
RLCS World Championship RESULTS 🏆

Vitality 4-2 NRG
```
- ✅ `esports_matchup` (64 chars)
```
RLCS Spring Major Quarterfinals is set:

G2 vs Faze

Who wins? 👇
```
- ✅ `roster_change` (18 chars)
```
jstn — joins NRG 🔄
```
- ✅ `item_shop` (111 chars)
```
TODAY'S ITEM SHOP IS LIVE 🛒

• Titanium White Octane
• Black Market Decal: Heatwave
• Goal Explosion: Fireworks
```
- ✅ `season_start` (130 chars)
```
RL Season 14 just dropped 🔥

New this season:
- New ranked rewards
- Updated item shop
- Season pass launched

https://example.com
```
- ✅ `collab_announcement` (71 chars)
```
Spongebob x Rocket League is CONFIRMED 👀

Spongebob themed car + decals
```
- ✅ `community_clip` (52 chars)
```
GarrettG just pulled off this 👇

https://example.com
```
- ✅ `reddit_highlight` (49 chars)
```
r/RocketLeague 🔥

Test title

https://example.com
```

### Geometry Dash

- ✅ `top1_verified` (106 chars)
```
Zoink just verified "Abyss of Darkness" — the NEW #1 on the Demon List 🏆

First ever sub-4% verified level
```
- ✅ `demon_list_update` (98 chars)
```
DEMON LIST UPDATE 📊

Abyss of Darkness enters at #1
Slaughterhouse moves to #2
Gelatin drops to #3
```
- ✅ `level_verified` (49 chars)
```
Dolphy — verified "Tartarus" (#3 on Demon List) 🏆
```
- ✅ `level_beaten` (55 chars)
```
Manix648 becomes the 214th person to beat "Bloodbath" 🎮
```
- ✅ `game_update` (111 chars)
```
RobTop has updated Geometry Dash to 2.3 🔺

Test body text for this item.

Available on Steam, iOS, and Android.
```
- ✅ `level_rated` (49 chars)
```
New Extreme Demon rated: "Sonic Wave" by Cyclic ⭐
```
- ✅ `daily_level` (80 chars)
```
Today's Daily Level: "Theory of Everything 2" by Partition 📅

Difficulty: Insane
```
- ✅ `weekly_demon` (64 chars)
```
"Bloodbath" is this week's Weekly Demon 👹

Extreme Demon by Riot
```
- ✅ `mod_update` (87 chars)
```
Geode mod loader updated to 2.1.0 🔧

Test body text for this item.

https://example.com
```
- ✅ `speedrun_wr` (36 chars)
```
WR | All Icons%: 1:24:37 by Doggie 🏆
```

> [PASS] 19/19 checks passed, 0 warnings


## 7. Media Handling (Download + Resize)

- ✅ RL Steam header: 1200×675px, 141KB (0.00s)
- ✅ GD Steam header: 1200×675px, 98KB (0.00s)
- ✅ Pointercrate thumb: 1200×675px, 64KB (0.00s)

> [PASS] 3/3 checks passed, 0 warnings


## 8. Queue & Deduplication

- ✅ Dedup working — re-running RSS collector added 0 new items
- ✅ RL queue: 9 queued, 1 posted
- ✅ GD queue: 168 queued, 1 posted

**RL top-5 queued (by priority)**

- ℹ️ [p2] RL Season  just dropped 🔥  New this season: - You’ve been loving Season 21, with…
- ℹ️ [p2] Rocket League v2.64 patch notes are up 👀  Here's what's changing: - Version : Ro…
- ℹ️ [p2] RL vlatest is now available.  The gang’s all here. Cartman, Stan, Kyle, Kenny, a…
- ℹ️ [p2] NEW ROCKET LEAGUE UPDATE (v2.63) OUT NOW:  • Version : Rocket League v2.63 Seaso…
- ℹ️ [p2] Rocket League v2.63 — patch notes are out 🚗  Version : Rocket League v2.63 ⁠ Pla…

**GD top-5 queued (by priority)**

- ℹ️ [p2] RobTop has updated Geometry Dash to 2.2081 🔺  This update includes some tweaks t…
- ℹ️ [p2] GEOMETRY DASH 2.208 IS OUT NOW 🔺  - • More precise gameplay options: Click Betwe…
- ℹ️ [p2] RobTop has released the latest update notes 👀  Here's what's new: - It's time fo…
- ℹ️ [p2] RobTop has released the latest update notes 👀  Here's what's new: - 100+ songs b…
- ℹ️ [p2] NEW GEOMETRY DASH UPDATE (latest):  • The Random Gauntlet Contest is here! • Wat…

> [PASS] 3/3 checks passed, 0 warnings


## 9. Rate Limiter

- ℹ️ [rocketleague] can_post=True, monthly_posts=0/1500
- ✅ [rocketleague] within monthly limit (0/1500)
- ℹ️ [geometrydash] can_post=True, monthly_posts=0/1500
- ✅ [geometrydash] within monthly limit (0/1500)

> [PASS] 2/2 checks passed, 0 warnings


## 10. DRY_RUN Poster

- ✅ [rocketleague] would post [p2]: RL Season  just dropped 🔥  New this season: - You’ve been loving Season 21, with 1 million of you taking the field at th…
- ℹ️ [rocketleague] media_path: none
- ✅ [geometrydash] would post [p2]: RobTop has updated Geometry Dash to 2.2081 🔺  This update includes some tweaks to Click Between / On Steps, performance …
- ℹ️ [geometrydash] media_path: none

> [PASS] 2/2 checks passed, 0 warnings


## ⏭️ Skipped (credentials required)

- **Reddit collector** — needs `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET`
- **Twitter monitor** — needs X API keys (`RL_API_KEY` etc.)
- **YouTube collector** — needs `YOUTUBE_API_KEY`
- **Live posting** — needs X API keys + `DRY_RUN=false`


---

## Overall Summary

| # | Test | Result |
|---|------|--------|
| 1 | 1. Database integrity | ✅ PASS — [PASS] 4/4 checks passed, 0 warnings |
| 2 | 2. RSS Collectors | ⚠️ WARN — [PASS] 2/2 checks passed, 1 warnings |
| 3 | 3. Pointercrate | ✅ PASS — [PASS] 2/2 checks passed, 0 warnings |
| 4 | 4. GDBrowser | ⚠️ WARN — [PASS] 1/1 checks passed, 2 warnings |
| 5 | 5. Octane.gg | ⚠️ WARN — [PASS] 0/0 checks passed, 2 warnings |
| 6 | 6. Formatter | ✅ PASS — [PASS] 19/19 checks passed, 0 warnings |
| 7 | 7. Media | ✅ PASS — [PASS] 3/3 checks passed, 0 warnings |
| 8 | 8. Queue + Dedup | ✅ PASS — [PASS] 3/3 checks passed, 0 warnings |
| 9 | 9. Rate Limiter | ✅ PASS — [PASS] 2/2 checks passed, 0 warnings |
| 10 | 10. DRY_RUN Poster | ✅ PASS — [PASS] 2/2 checks passed, 0 warnings |

**Total: 38 passed, 0 failed, 5 warnings**