"""
Identify and delete problematic tweets from both accounts.
Handles already-deleted tweets gracefully (Twitter returns 404).
"""
import re
import sqlite3
import time
from pathlib import Path

import tweepy

# Load credentials
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.settings import RL_CREDENTIALS, GD_CREDENTIALS

DB = Path(__file__).resolve().parent.parent / "data" / "autopost.db"

# ── Detection patterns ──────────────────────────────────────────────────────

def is_problematic(tweet_text: str, content_type: str, niche: str) -> str | None:
    """Return a reason string if the tweet should be deleted, None if OK."""
    t = tweet_text

    # Off-topic content (Dexerto/Loadout scraper garbage)
    off_topic_signals = [
        "homelander", "the boys", "invincible", "hatsune miku",
        "minecraft", "fortnite", "valorant", "call of duty",
        "andrew lloyd webber", "texas judge", "luigi mangione",
        "ps6", "ps5", "xbox", "fanta collab",
        "arc raiders", "husband fined", "netflix-style",
        "technoblade", "hello kitty",
    ]
    lower = t.lower()
    if niche == "rocketleague":
        for sig in off_topic_signals:
            if sig in lower and "rocket league" not in lower:
                return f"off-topic: contains '{sig}'"

    # Markdown headings leaked from GitHub
    if re.search(r"^#{1,6}\s", t, re.MULTILINE):
        return "markdown heading in tweet"

    # Raw commit hashes in parentheses
    if re.search(r"\([a-f0-9]{7,}\)", t):
        return "commit hash in tweet"

    # French content (should never be on English accounts)
    fr_words = re.compile(
        r"\b(?:les|des|est|pour|dans|cette|avec|nous|mais|sont"
        r"|une|qui|que|sur|aussi|tout|fait|comme|très|plus|mdr|mdrrr"
        r"|commence|furieux|victoire|équipe|incroyable"
        r"|magnifique|parcours|défaite|soirée|début"
        r"|rendez|retrouve|reviennent|inscriptions|ouvertes"
        r"|championnat|débutent|demain|accessible|plusieurs"
        r"|prédictions|remporter|faites|mettez"
        r"|nouvelle|vidéo|accompagnés|retour|parti)\b", re.I
    )
    fr_prefixes = ("c'est", "l'open", "l'", "d'", "n'", "j'", "qu'")
    fr_clean = lower
    fr_score = len(fr_words.findall(fr_clean))
    fr_score += sum(1 for p in fr_prefixes if p in fr_clean)
    if fr_score >= 3:
        return f"French content (score={fr_score})"

    # Raw RT @ prefix (old retweet format)
    if t.startswith("RT @"):
        return "raw RT @ prefix"

    # Filler tweets with no news value
    text_stripped = re.sub(r"https?://\S+", "", t).strip()
    text_no_emoji = re.sub(r"[\U0001F600-\U0001FAFF\U00002600-\U000027BF\u200d\ufe0f]+", "", text_stripped).strip()
    filler_patterns = [
        re.compile(r"^(hmm+|ah+|oh+|wow+|lol+|bruh)[.!?…\s]*$", re.I),
        re.compile(r"^\d+-\d+\.?\s*$"),
        re.compile(r"^(a{3,}h|o{3,}h|e{3,})", re.I),
    ]
    if any(p.match(text_no_emoji) for p in filler_patterns):
        return "filler tweet"

    # Broken template fills
    if "vlatest" in lower:
        return "broken version placeholder 'vlatest'"
    # "Season  IS HERE" — double space where season number should be
    if re.search(r"Season\s{2,}", t):
        return "empty season number"
    if "ROCKET LEAGUE UPDATE latest" in t:
        return "broken update template"
    if "Day  is underway" in t or " Day  " in t:
        return "empty day number"

    # Test/debug entries
    if "[new app credentials]" in t:
        return "test/debug entry"

    # Pure RETWEET: text in log (old format before quote tweet conversion)
    if t.startswith("RETWEET:"):
        return None  # These are retweet signals, the actual tweet is a retweet — skip

    return None


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    to_delete: dict[str, list[tuple[str, str]]] = {
        "rocketleague": [],
        "geometrydash": [],
    }

    for niche in ("rocketleague", "geometrydash"):
        rows = conn.execute(
            "SELECT tweet_id, content_type, tweet_text FROM post_log "
            "WHERE niche = ? AND tweet_id IS NOT NULL AND tweet_id != 'dry_run_id' "
            "AND tweet_id != 'cred_fix'",
            (niche,),
        ).fetchall()

        for r in rows:
            tid = r["tweet_id"]
            # Skip non-numeric IDs (dry runs, test entries)
            if not tid.isdigit():
                continue
            reason = is_problematic(r["tweet_text"], r["content_type"] or "", niche)
            if reason:
                preview = r["tweet_text"].replace("\n", " ")[:80]
                to_delete[niche].append((tid, reason))
                print(f"[{niche[:2]}] DELETE {tid}: {reason}")
                print(f"    {preview}")

    # Summary
    rl_count = len(to_delete["rocketleague"])
    gd_count = len(to_delete["geometrydash"])
    print(f"\n=== Summary: {rl_count} RL + {gd_count} GD = {rl_count + gd_count} tweets to delete ===")

    if not (rl_count + gd_count):
        print("Nothing to delete!")
        return

    # Confirm
    confirm = input("\nProceed with deletion? [y/N] ")
    if confirm.lower() != "y":
        print("Aborted.")
        return

    # Delete from each account
    for niche, creds in [("rocketleague", RL_CREDENTIALS), ("geometrydash", GD_CREDENTIALS)]:
        if not to_delete[niche]:
            continue

        client = tweepy.Client(
            consumer_key=creds["api_key"],
            consumer_secret=creds["api_secret"],
            access_token=creds["access_token"],
            access_token_secret=creds["access_token_secret"],
        )

        handle = "@rl_wire1" if niche == "rocketleague" else "@gd_wire"
        print(f"\nDeleting {len(to_delete[niche])} tweets from {handle}...")

        deleted = 0
        skipped = 0
        for tid, reason in to_delete[niche]:
            try:
                client.delete_tweet(id=tid)
                deleted += 1
                print(f"  DELETED {tid} ({reason})")
                time.sleep(1.5)  # rate limit buffer
            except tweepy.errors.NotFound:
                skipped += 1
                print(f"  SKIP {tid} (already deleted)")
            except tweepy.errors.Forbidden as e:
                if "not found" in str(e).lower() or "does not belong" in str(e).lower():
                    skipped += 1
                    print(f"  SKIP {tid} (not found / not owned)")
                else:
                    print(f"  ERROR {tid}: {e}")
                    # Rate limited — wait and retry
                    if "429" in str(e) or "Too Many" in str(e):
                        print("  Rate limited, waiting 60s...")
                        time.sleep(60)
                        try:
                            client.delete_tweet(id=tid)
                            deleted += 1
                            print(f"  DELETED {tid} on retry")
                        except Exception as e2:
                            print(f"  FAILED on retry: {e2}")
            except Exception as e:
                print(f"  ERROR {tid}: {e}")
                if "429" in str(e):
                    print("  Rate limited, waiting 60s...")
                    time.sleep(60)

        print(f"\n{handle}: {deleted} deleted, {skipped} already gone")


if __name__ == "__main__":
    main()
