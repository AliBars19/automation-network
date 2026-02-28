# AutoPost Pipeline Test Results

**Run at:** 2026-02-28 15:29 UTC

**Platform:** Python 3.11.9 on Windows

**Note:** Tests requiring credentials (Reddit, X API, YouTube) are skipped — marked as ⏭️


---


## 1. Database Integrity

- ✅ init_db() completed without error
- ✅ All 4 tables present: post_log, raw_content, sources, sqlite_sequence, tweet_queue
- ℹ️ tweet_queue: 179 rows
- ℹ️ raw_content: 179 rows
- ℹ️ sources: 51 rows
- ℹ️ post_log: 2 rows
- ✅ Sources seeded — geometrydash: 27 sources
- ✅ Sources seeded — rocketleague: 24 sources

> [PASS] 4/4 checks passed, 0 warnings


## 2. RSS Collectors


**Steam News (RL)**

- ✅ Fetched 10 entries in 0.34s
- ℹ️ Content types: {'event_announcement': 1, 'season_start': 3, 'patch_notes': 4, 'collab_announcement': 2}
- ℹ️ Sample tweet (event_announcement, 278 chars):
```
RLCS Get Drops While Cheering On Your Favorite Team in the RLCS Boston Major STARTS NOW 🚨

This February, 16 of Rocket League’s top teams take the pitch at the Agganis Arena in Boston, Massachusetts, for a…

https://store.steampowered.com/news/app/252950/view/738163097677595118
```

**Steam News (GD)**

- ✅ Fetched 10 entries in 0.30s
- ℹ️ Content types: {'game_update': 10}
- ℹ️ Sample tweet (game_update, 243 chars):
```
RobTop has released the latest update notes 👀

Here's what's new:
- The nominees for The Geometry Dash Awards 2025 have been announced!
- Vote to decide the winners here .
- Before voting, you can watch the video below to see all the nominees.
```

**RL Blog**

- ⚠️ No entries returned (feed may be empty or unreachable) in 0.12s

> [PASS] 2/2 checks passed, 1 warnings


## 3. Pointercrate (GD Demon List)

- ✅ Fetched 50 demons in 0.61s
- ℹ️ Content type breakdown: {'top1_verified': 1, 'level_verified': 49}

**Current #1: Thinking Space II**

- ℹ️ Verifier: [67] Zoink
- ℹ️ Position metadata: 1
- ✅ top1_verified tweet (111 chars):
```
🚨 NEW TOP 1

"Thinking Space II" has been verified by [67] Zoink

#1 on the Demon List — verified by [67] Zoink
```
- ℹ️ level_verified sample (Flamewall, #2):
```
[400] CuatrocientosYT verifies "Flamewall" 🏆

#2 on the Demon List

https://www.youtube.com/watch?v=x4Io4zkWVRw
```

> [PASS] 2/2 checks passed, 0 warnings


## 4. GDBrowser API

- ⚠️ Daily level returned no data (GDBrowser server-side issue on sentinel IDs)
- ⚠️ Weekly demon returned no data (same GDBrowser issue)
- ✅ Rated levels: 10 fetched in 0.86s
- ℹ️ Sample rated level tweet (54 chars):
```
New Unrated rated: "pixel Madness" by MrPhotatoFries ⭐
```

> [PASS] 1/1 checks passed, 2 warnings


## 5. Octane.gg (RL Esports)

- ⚠️ No match results returned (may be off-season)
- ⚠️ No upcoming matches returned

> [PASS] 0/0 checks passed, 2 warnings


## 6. Formatter — All Content Types


### Rocket League

- ✅ `patch_notes` (130 chars)
```
ROCKET LEAGUE UPDATE v2.64 IS LIVE 🔄

- New car body added
- Bug fixes
- Performance improvements

Full notes: https://example.com
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
- ✅ `item_shop` (152 chars)
```
New items in the Rocket League Item Shop today:

• Titanium White Octane
• Black Market Decal: Heatwave
• Goal Explosion: Fireworks

https://example.com
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
- ✅ `community_clip` (35 chars)
```
Test title 🔥

📎 https://example.com
```
- ✅ `reddit_highlight` (33 chars)
```
Test title

📎 https://example.com
```

### Geometry Dash

- ✅ `top1_verified` (85 chars)
```
THE NEW TOP 1 IS HERE 🚨

"Abyss of Darkness" — verified by Zoink

https://example.com
```
- ✅ `demon_list_update` (38 chars)
```
Demon List Top 5 📊

1. 
2. 
3. 
4. 
5.
```
- ✅ `level_verified` (76 chars)
```
NEW: "Tartarus" has been verified by Dolphy 🏆

Test body text for this item.
```
- ✅ `level_beaten` (48 chars)
```
Manix648 beats "Bloodbath" (#17 on Demon List) 🎮
```
- ✅ `game_update` (111 chars)
```
RobTop has updated Geometry Dash to 2.3 🔺

Test body text for this item.

Available on Steam, iOS, and Android.
```
- ✅ `level_rated` (65 chars)
```
"Sonic Wave" by Cyclic just got rated ⭐

Extreme Demon — 10 stars
```
- ✅ `daily_level` (80 chars)
```
Today's Daily Level: "Theory of Everything 2" by Partition 📅

Difficulty: Insane
```
- ✅ `weekly_demon` (63 chars)
```
New Weekly Demon is here 👹

"Bloodbath" by Riot — Extreme Demon
```
- ✅ `mod_update` (93 chars)
```
Geode 2.1.0 has been released 🔧

Test body text for this item.

Download: https://example.com
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

- ℹ️ [rocketleague] can_post=True, monthly_posts=1/1500
- ✅ [rocketleague] within monthly limit (1/1500)
- ℹ️ [geometrydash] can_post=True, monthly_posts=1/1500
- ✅ [geometrydash] within monthly limit (1/1500)

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