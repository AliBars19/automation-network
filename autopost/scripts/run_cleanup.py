"""Delete all identified problematic tweets. Handles already-deleted gracefully."""
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tweepy
from config.settings import RL_CREDENTIALS, GD_CREDENTIALS

# ── Tweets to delete ─────────────────────────────────────────────────────────
# Includes: auto-detected (36) + manually identified French tweets the
# detection missed due to low word count

RL_DELETE = [
    # Broken templates (empty season/day numbers, "vlatest", broken update)
    "2030694637833175521",  # Day  is underway
    "2030744469029458019",  # Season  is now live
    "2030750004571234615",  # RL Season  just dropped
    "2030755538800119840",  # Day  is underway
    "2030916600119926896",  # vlatest
    "2030921633226002837",  # ROCKET LEAGUE UPDATE latest
    "2030926668122951837",  # Season  is now live
    "2030934723078279677",  # Season  is now live
    # Raw RT @ prefix
    "2036352477478437154",  # RT @RL_Status
    "2036714865880932657",  # RT @reidthesloth
    "2037219191036461278",  # RT @RLEsports
    "2037445177711718616",  # RT @T3Bates
    "2037918798133530918",  # RT @RLEsports schedule
    # Filler tweets
    "2036406334061502781",  # aaaaaaaah
    "2037596677670904066",  # 3-0.
    "2037646507747238133",  # Hmm...
    # French content (auto-detected, score >= 3)
    "2036425458368086073",  # Les GROUPES de l'OPEN
    "2036430994769678846",  # NOUVELLE VIDÉO
    "2036436531334856858",  # Les INSCRIPTIONS RBRS
    "2036442070718517412",  # LES RBRS REVIENNENT
    "2036777779694346293",  # Le championnat de France
    "2036792881202012568",  # INSCRIPTIONS OUVERTES
    "2036838182050795544",  # EN ROUTE POUR PARIS
    "2036880962827505980",  # BAGUETTE FLASH
    "2037203085244641617",  # LE BON PRONO
    "2037472356755497133",  # LE BON PRONO (RT)
    "2037543325654233598",  # QUI VA REMPORTER L'OPEN
    "2037563459219415164",  # L'OPEN 4 COMMENCE
    "2037585605912121481",  # Entrée douloureuse
    "2037813104642285768",  # Fin de parcours
    "2037824176992448563",  # Fin de cette première soirée
    "2037844810992635984",  # VOICI LE BRACKET
    "2037913264856719792",  # Début des playoffs
    # French content (manually identified, below auto threshold)
    "2037574531955544178",  # c'est un beau but hein
    "2037580068545798341",  # MOZZAAA LA TRIPLEEEE TAAAP
    "2037602214546477311",  # ALPHA54 EST BEL ET BIEN DE RETOUR
    "2037607750998348066",  # MTZZZZR, LA DOUBLE TAP À VITESSE
    "2037613287324409951",  # Ils ne vont pas la chercher
    "2037624359859155356",  # La KarmineCorp se fait surprendre
    "2037629898366316836",  # La TeamVitality confirme
    "2037635434591768920",  # AU BOUT DE 4 MINUTES D'OVERTIME
    "2037640970775286175",  # GeekayRL TERMINE SON OPEN
    "2037807567896723703",  # MDRRR LE BUZZER BEATER DE FOU FURIEUX
    "2037850349449478254",  # MDR MAIS LA FOLIE DE CE BUT
    "2037880044702376204",  # Première historique
    # Off-topic (Dexerto scraper)
    "2039704783842316733",  # The Boys / Homelander
]

GD_DELETE = [
    # Markdown in tweets
    "2039500948133847350",  # Geode ## v5.5.2
    "2039506484346646648",  # Geode ## v5.5.3
]


def delete_batch(client, tweet_ids, handle):
    deleted = 0
    already_gone = 0
    errors = 0

    for tid in tweet_ids:
        try:
            client.delete_tweet(id=tid)
            deleted += 1
            print(f"  DELETED {tid}")
            time.sleep(1.5)
        except tweepy.errors.NotFound:
            already_gone += 1
            print(f"  SKIP    {tid} (already deleted)")
        except tweepy.errors.Forbidden as e:
            err = str(e).lower()
            if "not found" in err or "does not belong" in err or "no status found" in err:
                already_gone += 1
                print(f"  SKIP    {tid} (not found)")
            elif "429" in str(e) or "too many" in err:
                print(f"  RATELIMIT — waiting 90s...")
                time.sleep(90)
                try:
                    client.delete_tweet(id=tid)
                    deleted += 1
                    print(f"  DELETED {tid} (retry)")
                    time.sleep(1.5)
                except Exception:
                    errors += 1
                    print(f"  FAILED  {tid}")
            else:
                errors += 1
                print(f"  ERROR   {tid}: {e}")
        except tweepy.errors.TooManyRequests:
            print(f"  RATELIMIT — waiting 90s...")
            time.sleep(90)
            try:
                client.delete_tweet(id=tid)
                deleted += 1
                print(f"  DELETED {tid} (retry)")
                time.sleep(1.5)
            except Exception:
                errors += 1
                print(f"  FAILED  {tid}")
        except Exception as e:
            errors += 1
            print(f"  ERROR   {tid}: {e}")

    print(f"\n{handle}: {deleted} deleted, {already_gone} already gone, {errors} errors")
    return deleted


def main():
    print(f"=== Cleanup: {len(RL_DELETE)} RL + {len(GD_DELETE)} GD = {len(RL_DELETE) + len(GD_DELETE)} total ===\n")

    # RL account
    print(f"--- @rl_wire1 ({len(RL_DELETE)} tweets) ---")
    rl_client = tweepy.Client(
        consumer_key=RL_CREDENTIALS["api_key"],
        consumer_secret=RL_CREDENTIALS["api_secret"],
        access_token=RL_CREDENTIALS["access_token"],
        access_token_secret=RL_CREDENTIALS["access_token_secret"],
    )
    rl_deleted = delete_batch(rl_client, RL_DELETE, "@rl_wire1")

    # GD account
    print(f"\n--- @gd_wire ({len(GD_DELETE)} tweets) ---")
    gd_client = tweepy.Client(
        consumer_key=GD_CREDENTIALS["api_key"],
        consumer_secret=GD_CREDENTIALS["api_secret"],
        access_token=GD_CREDENTIALS["access_token"],
        access_token_secret=GD_CREDENTIALS["access_token_secret"],
    )
    gd_deleted = delete_batch(gd_client, GD_DELETE, "@gd_wire")

    print(f"\n=== Done: {rl_deleted + gd_deleted} total deletions ===")


if __name__ == "__main__":
    main()
