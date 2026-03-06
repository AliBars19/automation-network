"""
All tweet templates, keyed by niche → content_type.

Each content_type maps to a list of format strings.
Multiple variants exist so posts don't feel repetitive — one is chosen at random.
None means "handle as a retweet, not a text template".

Style references:
  - HYPEX (@HYPEX): ALL CAPS headlines, bullet • lists, direct + punchy, emoji at end of header
  - ShiinaBR (@ShiinaBR): "X DROPS @ TIME" format, dash-bullet lists, hype emoji in header
  - iFireMonkey (@iFireMonkey): version-tagged, timestamped, comprehensive bullet lists
  - kurrco (@kurrco): "[Subject] — [what happened]", quote detail, always media
  - General news pages: 🚨 for breaking, short factual relay, speed over polish
"""

# ──────────────────────────────────────────────────────────────────────────────
# ROCKET LEAGUE
# ──────────────────────────────────────────────────────────────────────────────

RL_TEMPLATES: dict[str, list[str | None]] = {

    # ── Patch notes / game updates ─────────────────────────────────────────────
    "patch_notes": [
        # kurrco style
        "Rocket League {version} — patch notes are out 🚗\n\n{summary}\n\n{url}",
        # ShiinaBR style: time + bullet list
        "ROCKET LEAGUE UPDATE {version} IS LIVE 🔄\n\n- {bullet1}\n- {bullet2}\n- {bullet3}\n\nFull notes: {url}",
        # HYPEX style: ALL CAPS header, • bullets
        "NEW ROCKET LEAGUE UPDATE ({version}) OUT NOW:\n\n• {bullet1}\n• {bullet2}\n• {bullet3}\n\n{url}",
        # Understated / factual
        "RL v{version} is now available.\n\n{summary}\n\n{url}",
        # Patch preview (before it drops)
        "Rocket League {version} patch notes are up 👀\n\nHere's what's changing:\n- {bullet1}\n- {bullet2}\n- {bullet3}\n\n{url}",
    ],

    # ── Esports results ────────────────────────────────────────────────────────
    "esports_result": [
        # kurrco score format
        "{event} — {stage}\n\n{team1} {score1}-{score2} {team2}\n\n{winner} take the series {emoji}",
        # HYPEX ALL CAPS result
        "{event} RESULTS 🏆\n\n{winner} {score1}-{score2} {loser}",
        # Bracket advance
        "{event}\n\n{winner} defeat {loser} {score} to advance {emoji}",
        # Grand finals specific
        "{event} Grand Finals\n\n{team1} {score1}-{score2} {team2}\n\n{winner} are your {event_short} Champions 🏆",
        # Sweep callout
        "{winner} sweep {loser} {score} at {event} {emoji}",
        # Comeback callout
        "{winner} come back from {deficit} down to beat {loser} at {event} 🔥",
    ],

    # ── Esports bracket / match preview ───────────────────────────────────────
    "esports_matchup": [
        "{event} — {stage} 🎮\n\n{team1} vs {team2}\n\nStarts {time} UTC",
        "MATCH ALERT 🚨\n\n{team1} vs {team2}\n{event} — {stage}\n\n{time} UTC",
        "{event} {stage} is set:\n\n{team1} vs {team2}\n\nWho wins? 👇",
    ],

    # ── Esports event / tournament start ──────────────────────────────────────
    "event_announcement": [
        "{event} kicks off today 🏟️\n\n{teams} teams competing\nPrize pool: {prize_pool}\n\nWatch: {url}",
        "RLCS {event} STARTS NOW 🚨\n\n{details}\n\n{url}",
        "{event} — Day {day} is underway 🎮\n\nSchedule: {url}",
    ],

    # ── Roster / transfer news ─────────────────────────────────────────────────
    "roster_change": [
        # kurrco style
        "{player} — joins {team} {emoji}",
        "{team} sign {player} for {season} 🔄",
        "ROSTER MOVE: {player} has joined {team}\n\nPreviously on {old_team}",
        "{player} is officially a free agent after parting ways with {old_team}",
        "{team} announce their {season} roster:\n\n{roster_list}",
        "{player} to {team} — {source} 🗞️",
    ],

    # ── Item shop ──────────────────────────────────────────────────────────────
    "item_shop": [
        "Rocket League Item Shop — {date} 🛒\n\n{items}",
        "TODAY'S ITEM SHOP IS LIVE 🛒\n\n{items}",
        "New items in the Rocket League Item Shop today:\n\n{items}\n\n{url}",
        "Item Shop Update — {date} 🎨\n\n{items}",
    ],

    # ── Season start ───────────────────────────────────────────────────────────
    "season_start": [
        "Rocket League Season {number} is now live! 🏎️\n\n{highlights}\n\n{url}",
        "SEASON {number} IS HERE 🚀\n\n• {highlight1}\n• {highlight2}\n• {highlight3}\n\n{url}",
        "RL Season {number} just dropped 🔥\n\nNew this season:\n- {highlight1}\n- {highlight2}\n- {highlight3}\n\n{url}",
    ],

    # ── Collab / crossover announcements ──────────────────────────────────────
    "collab_announcement": [
        "{brand} x Rocket League is CONFIRMED 👀\n\n{details}",
        "NEW COLLAB: {brand} is coming to Rocket League 🔥\n\n{details}\n\nAvailable {date}",
        "{brand} items are now in Rocket League 🎮\n\n{details}\n\n{url}",
    ],

    # ── Community clip / highlight ─────────────────────────────────────────────
    "community_clip": [
        "{title} 🔥\n\n📎 {url}",
        "{player} just pulled off this 👇\n\n{url}",
        "This {rank} player's {mechanic} is insane 🔥\n\n{url}",
    ],

    # ── Rank / competitive milestone (pro players & creators only) ───────────
    "rank_milestone": [
        "{player} has reached {rank} in Rocket League {emoji}",
        "{player} — {achievement} {emoji}",
    ],

    # ── Reddit highlight (catch-all for generic Reddit posts) ─────────────────
    "reddit_highlight": [
        "{title}\n\n{url}",
    ],

    # ── Flashback / on this day ────────────────────────────────────────────────
    "flashback": [
        "{headline}",
        "{years_ago} years ago today: {headline}",
    ],

    # ── Stat milestone ─────────────────────────────────────────────────────────
    "stat_milestone": [
        "{headline}",
    ],

    # ── Official account tweets (retweet signal) ──────────────────────────────
    "official_tweet": [None],
}


# ──────────────────────────────────────────────────────────────────────────────
# GEOMETRY DASH
# ──────────────────────────────────────────────────────────────────────────────

GD_TEMPLATES: dict[str, list[str | None]] = {

    # ── Demon list updates ─────────────────────────────────────────────────────
    "demon_list_update": [
        "Demon List Update:\n\n{changes}",
        "\"{level}\" has been placed at #{position} on the Demon List.",
        "\"{level}\" moves from #{old_position} to #{position} on the Demon List.",
        "Demon List Top 5:\n\n1. {top1}\n2. {top2}\n3. {top3}\n4. {top4}\n5. {top5}",
        "\"{level}\" by {creator} enters the Demon List at #{position}.",
    ],

    # ── Top 1 verified (special — biggest news in GD) ─────────────────────────
    "top1_verified": [
        "BREAKING: \"{level}\" has been verified by {player}, the new Top 1 on the Demon List.\n\n{details}",
        "\"{level}\" has been verified by {player} — the new #1 on the Demon List.\n\n{url}",
        "{player} has verified \"{level}\", now the hardest rated level in Geometry Dash.\n\n{details}",
    ],

    # ── Level verified ────────────────────────────────────────────────────────
    "level_verified": [
        "\"{level}\" has been verified by {player}. #{position} on the Demon List.\n\n{url}",
        "{player} has verified \"{level}\" (#{position} on the Demon List).\n\n{details}",
        "BREAKING: \"{level}\" has been verified by {player}, placed at #{position} on the Demon List.",
        "\"{level}\" is officially verified by {player}. #{position} on the Demon List.\n\n{url}",
    ],

    # ── Level beaten (new victor) ──────────────────────────────────────────────
    "level_beaten": [
        "{player} has beaten \"{level}\" (#{position} on the Demon List).",
        "New victor on \"{level}\": {player}. #{position} on the Demon List.",
        "{player} beats \"{level}\", currently #{position} on the Demon List.\n\n{context}",
        "{player} becomes the {victor_number} person to beat \"{level}\".",
    ],

    # ── Game update ────────────────────────────────────────────────────────────
    "game_update": [
        "Geometry Dash {version} is out now.\n\n{summary}\n\nAvailable on Steam, iOS, and Android.",
        "RobTop has updated Geometry Dash to {version}.\n\n{summary}\n\n{url}",
        "Geometry Dash {version} is now live.\n\n- {bullet1}\n- {bullet2}\n- {bullet3}\n\n{url}",
    ],

    # ── RobTop tweet (retweet signal) ─────────────────────────────────────────
    "robtop_tweet": [None],

    # ── Level rated ───────────────────────────────────────────────────────────
    "level_rated": [
        "\"{level_name}\" by {creator} has been rated. {difficulty}, {stars} stars.",
        "New rated level: \"{level_name}\" by {creator}. {difficulty} | {stars} stars.",
        "\"{level_name}\" by {creator} just got rated. {difficulty} — {stars} stars.",
    ],

    # ── Daily level ────────────────────────────────────────────────────────────
    "daily_level": [
        "Today's Daily Level: \"{level_name}\" by {creator}. {difficulty}.",
        "\"{level_name}\" by {creator} is today's Daily Level. {difficulty}.",
    ],

    # ── Weekly demon ───────────────────────────────────────────────────────────
    "weekly_demon": [
        "This week's Weekly Demon: \"{level_name}\" by {creator}. {difficulty}.",
        "\"{level_name}\" by {creator} is this week's Weekly Demon. {difficulty}.",
    ],

    # ── Mod / Geode update ────────────────────────────────────────────────────
    "mod_update": [
        "Geode {version} has been released.\n\n{summary}\n\n{url}",
        "Geode mod loader updated to {version}.\n\n{summary}\n\n{url}",
        "New GD mod: \"{mod_name}\"\n\n{description}\n\n{url}",
    ],

    # ── YouTube video ─────────────────────────────────────────────────────────
    "youtube_video": [
        "New video from {creator}: \"{title}\"\n\n{url}",
        "{creator} just uploaded: \"{title}\"\n\n{url}",
    ],

    # ── Creator spotlight ─────────────────────────────────────────────────────
    "creator_spotlight": [
        "{creator} has released \"{level_name}\".\n\n{description}\n\n{url}",
        "New level from {creator}: \"{level_name}\".\n\n{details}",
    ],

    # ── Speedrun world record ─────────────────────────────────────────────────
    "speedrun_wr": [
        "{player} has set a new {category} world record: {time}.\n\nPrevious: {prev_time}\n\n{url}",
        "{player} breaks the {category} world record with {time}.\n\n{url}",
    ],

    # ── Reddit highlight (catch-all for generic Reddit posts) ─────────────────
    "reddit_highlight": [
        "{title}\n\n{url}",
    ],

    # ── Breaking / miscellaneous news ─────────────────────────────────────────
    "breaking_news": [
        "BREAKING: {headline}\n\n{details}",
        "{headline}\n\n{details}\n\n{url}",
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# Template lookup map
# ──────────────────────────────────────────────────────────────────────────────

TEMPLATES: dict[str, dict[str, list[str | None]]] = {
    "rocketleague": RL_TEMPLATES,
    "geometrydash":  GD_TEMPLATES,
}
