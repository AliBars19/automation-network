"""
All tweet templates, keyed by niche -> content_type.

Each content_type maps to a list of format strings.
Multiple variants exist so posts don't feel repetitive -- one is chosen at random.
None means "handle as a retweet, not a text template".

Style references:
  - @ShiftRLE: "Sources:" for rumors, #RLCS on every post, ALL CAPS only for
    championship celebrations, zero emojis in standard news
  - @today_gd: Plain factual sentences, level names in quotes, zero emojis,
    zero hashtags, editorial color for significant events
  - @Dexerto: AP-newswire declarative sentences, zero emojis, zero hashtags
  - @DemonListNews: "{player} has verified '{level}'" construction
"""

# ──────────────────────────────────────────────────────────────────────────────
# ROCKET LEAGUE
# ──────────────────────────────────────────────────────────────────────────────

RL_TEMPLATES: dict[str, list[str | None]] = {

    # ── Patch notes / game updates ─────────────────────────────────────────────
    "patch_notes": [
        "Rocket League {version} is now live.\n\n{summary}\n\n{url}",
        "Rocket League {version} patch notes:\n\n- {bullet1}\n- {bullet2}\n- {bullet3}\n\n{url}",
        "Patch {version} has been deployed for Rocket League.\n\n{summary}\n\n{url}",
        "RL {version} is now available.\n\n- {bullet1}\n- {bullet2}\n- {bullet3}\n\n{url}",
    ],

    # ── Esports results ────────────────────────────────────────────────────────
    "esports_result": [
        "{winner} {score1}-{score2} {loser} at {event}. #RLCS",
        "{winner} take down {loser} {score1}-{score2}. #RLCS",
        "{event}: {winner} {score1}-{score2} {loser}. #RLCS",
        "{winner} sweep {loser} at {event}. #RLCS",
        "{winner} are your {event_short} Champions. #RLCS",
        "{winner} reverse sweep {loser} at {event}. #RLCS",
    ],

    # ── Esports bracket / match preview ───────────────────────────────────────
    "esports_matchup": [
        "{team1} vs {team2} coming up at {event} -- {stage}. #RLCS",
        "{event} {stage}: {team1} vs {team2}. #RLCS",
        "Next up at {event}: {team1} vs {team2}. #RLCS",
    ],

    # ── Esports event / tournament start ──────────────────────────────────────
    "event_announcement": [
        "{event} begins today. {details}\n\n{url} #RLCS",
        "{event} is underway.\n\n{details}\n\n{url} #RLCS",
        "{event} Day {day} is live.\n\nSchedule: {url} #RLCS",
    ],

    # ── Roster / transfer news ─────────────────────────────────────────────────
    "roster_change": [
        "Sources: {player} is expected to join {team}. #RLCS",
        "{player} to {team}, per sources. #RLCS",
        "{team} sign {player} for {season}. #RLCS",
        "{player} has been released from {old_team}. #RLCS",
        "{team} announce their {season} roster:\n\n{roster_list}\n\n#RLCS",
        "{player} is officially a free agent after parting ways with {old_team}. #RLCS",
    ],

    # ── Item shop ──────────────────────────────────────────────────────────────
    "item_shop": [
        "Rocket League Item Shop for {date}:\n\n{items}",
        "Today's Item Shop:\n\n{items}\n\n{url}",
        "New in the Item Shop:\n\n{items}",
    ],

    # ── Season start ───────────────────────────────────────────────────────────
    "season_start": [
        "Rocket League Season {number} is now live.\n\n{highlights}\n\n{url}",
        "Season {number} has arrived.\n\n- {highlight1}\n- {highlight2}\n- {highlight3}\n\n{url}",
        "Season {number} is here.\n\n- {highlight1}\n- {highlight2}\n- {highlight3}\n\n{url}",
    ],

    # ── Collab / crossover announcements ──────────────────────────────────────
    "collab_announcement": [
        "{brand} x Rocket League has been announced.\n\n{details}",
        "{brand} items are coming to Rocket League.\n\n{details}",
        "{brand} collaboration now live in Rocket League.\n\n{details}\n\n{url}",
    ],

    # ── Community clip / highlight ─────────────────────────────────────────────
    "community_clip": [
        "{title}",
        "{title}\n\nvia {author}",
    ],

    # ── Reddit clip (native video attached, no URL needed) ───────────────────
    "reddit_clip": [
        "{title}\n\nvia u/{author} on Reddit",
        "{title}\n\n(via u/{author})",
    ],

    # ── Viral moment ──────────────────────────────────────────────────────────
    "viral_moment": [
        "{title}",
        "{title}\n\nvia {author}",
    ],

    # ── Community event ───────────────────────────────────────────────────────
    "community_event": [
        "{title}",
        "{title}\n\n{details}",
    ],

    # ── Rank / competitive milestone ──────────────────────────────────────────
    "rank_milestone": [
        "{player} has reached {rank} in Rocket League.",
        "{player} -- {achievement}.",
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

    # ── YouTube video uploads ─────────────────────────────────────────────────
    "youtube_video": [
        "New video from {creator}: \"{video_title}\"\n\n{url}",
        "{creator} just uploaded: \"{video_title}\"\n\n{url}",
        "{creator} dropped a new video: \"{video_title}\"\n\n{url}",
    ],

    # ── Pro player content (disabled -- bot signal) ──────────────────────────
    "pro_player_content": [None],

    # ── Breaking / miscellaneous news ─────────────────────────────────────────
    "breaking_news": [
        "{title}\n\n{url}",
        "{title}\n\n{url}",
    ],

    # ── Official account tweets (retweet signal) ──────────────────────────────
    "official_tweet": [None],

    # ── Monitored account tweets (non-retweet — formatted as news) ──────────
    "monitored_tweet": [
        "{title}",
        "{title}\n\nvia @{author}",
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# GEOMETRY DASH
# ──────────────────────────────────────────────────────────────────────────────

GD_TEMPLATES: dict[str, list[str | None]] = {

    # ── Demon list updates ─────────────────────────────────────────────────────
    # NOTE: Use "No." instead of "#" for positions to avoid Twitter rendering
    # them as clickable hashtags (e.g. "#56" becomes a hashtag link).
    "demon_list_update": [
        "Demon List has been updated.\n\n{changes}",
        "\"{level}\" has been placed at No. {position} on the Demon List.",
        "\"{level}\" moved from No. {old_position} to No. {position} on the Demon List.",
        "Demon List Top 5:\n\n1. {top1}\n2. {top2}\n3. {top3}\n4. {top4}\n5. {top5}",
        "\"{level}\" by {creator} enters the Demon List at No. {position}.",
    ],

    # ── Top 1 verified (biggest news in GD -- only type that gets BREAKING) ──
    "top1_verified": [
        "BREAKING: \"{level}\" has been verified by {player}. New No. 1 on the Demon List.",
        "BREAKING: {player} has verified \"{level}\" after {attempts} attempts. New No. 1 on the Demon List.",
        "{player} has verified \"{level}\", now the hardest rated level in Geometry Dash.",
    ],

    # ── Level verified ────────────────────────────────────────────────────────
    "level_verified": [
        "{player} has verified \"{level}\". No. {position} on the Demon List.",
        "\"{level}\" has been verified by {player} after {attempts} attempts. No. {position} on the Demon List.",
        "\"{level}\" has been verified by {player}, placed at No. {position} on the Demon List.",
    ],

    # ── Level beaten (new victor) ──────────────────────────────────────────────
    "level_beaten": [
        "{player} has beaten \"{level}\" (No. {position} on the Demon List).",
        "New victor on \"{level}\": {player}. No. {position} on the Demon List.",
        "{player} beats \"{level}\" after {attempts} attempts, currently No. {position} on the Demon List.",
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

    # ── Monitored account tweets (non-retweet — formatted as news) ──────────
    "monitored_tweet": [
        "{title}",
        "{title}\n\nvia @{author}",
    ],

    # ── Level rated ───────────────────────────────────────────────────────────
    "level_rated": [
        "\"{level_name}\" by {creator} has been rated. {difficulty}, {stars} stars.",
        "New rated level: \"{level_name}\" by {creator}. {difficulty}, {stars} stars.",
        "\"{level_name}\" by {creator} just got rated ({difficulty}, {stars} stars).",
    ],

    # ── Daily level ────────────────────────────────────────────────────────────
    "daily_level": [
        "Today's Daily Level: \"{level_name}\" by {creator}. {difficulty}.",
        "\"{level_name}\" by {creator} is today's Daily Level. {difficulty}.",
        "Daily Level: \"{level_name}\" by {creator} ({difficulty}, {stars} stars).",
    ],

    # ── Weekly demon ───────────────────────────────────────────────────────────
    "weekly_demon": [
        "This week's Weekly Demon: \"{level_name}\" by {creator}. {difficulty}.",
        "\"{level_name}\" by {creator} is this week's Weekly Demon. {difficulty}.",
        "Weekly Demon: \"{level_name}\" by {creator} ({difficulty}, {stars} stars).",
    ],

    # ── Mod / Geode update ────────────────────────────────────────────────────
    "mod_update": [
        "Geode {version} has been released.\n\n{summary}\n\n{url}",
        "Geode mod loader updated to {version}.\n\n{summary}\n\n{url}",
        "New GD mod: \"{mod_name}\"\n\n{description}\n\n{url}",
    ],

    # ── YouTube video uploads ─────────────────────────────────────────────────
    "youtube_video": [
        "New video from {creator}: \"{video_title}\"\n\n{url}",
        "{creator} just uploaded: \"{video_title}\"\n\n{url}",
        "{creator} dropped a new video: \"{video_title}\"\n\n{url}",
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

    # ── First victor ──────────────────────────────────────────────────────────
    "first_victor": [
        "{player} becomes the first person to beat \"{level}\" (No. {position} on the Demon List).",
        "First victor on \"{level}\": {player}. No. {position} on the Demon List after {attempts} attempts.",
        "FIRST VICTOR: {player} has beaten \"{level}\" (No. {position}).",
    ],

    # ── Reddit clip (native video attached) ──────────────────────────────────
    "reddit_clip": [
        "{title}\n\nvia u/{author} on Reddit",
        "{title}\n\n(via u/{author})",
    ],

    # ── Community event ───────────────────────────────────────────────────────
    "community_event": [
        "{title}",
        "{title}\n\n{details}",
    ],

    # ── Viral moment ──────────────────────────────────────────────────────────
    "viral_moment": [
        "{title}",
        "{title}\n\nvia {author}",
    ],

    # ── Breaking / miscellaneous news ─────────────────────────────────────────
    "breaking_news": [
        "{headline}\n\n{details}",
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
