"""
All tweet templates, keyed by niche → content_type.

Each content_type maps to a list of format strings.
Multiple variants exist so posts don't feel repetitive — one is chosen at random.
None means "handle as a retweet, not a text template".
"""

# ── Rocket League ──────────────────────────────────────────────────────────────

RL_TEMPLATES: dict[str, list[str | None]] = {

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

    # Signals to the poster to retweet the source account directly
    "official_tweet": [None],
}

# ── Geometry Dash ──────────────────────────────────────────────────────────────

GD_TEMPLATES: dict[str, list[str | None]] = {

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

    # Signals to the poster to retweet RobTop directly
    "robtop_tweet": [None],
}

# ── Template lookup map ────────────────────────────────────────────────────────

TEMPLATES: dict[str, dict[str, list[str | None]]] = {
    "rocketleague": RL_TEMPLATES,
    "geometrydash":  GD_TEMPLATES,
}
