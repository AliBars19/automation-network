"""
Quick X API credential tester.
Run from the autopost/ directory:
    python scripts/test_credentials.py geometrydash
    python scripts/test_credentials.py rocketleague

Prints the authenticated account name and user ID on success.
Prints the full error body on failure so you can see exactly what's wrong.
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

niche = sys.argv[1] if len(sys.argv) > 1 else "geometrydash"

prefix = {"geometrydash": "GD", "rocketleague": "RL"}.get(niche)
if not prefix:
    print(f"Unknown niche: {niche}")
    sys.exit(1)

api_key    = os.getenv(f"{prefix}_API_KEY", "")
api_secret = os.getenv(f"{prefix}_API_SECRET", "")
token      = os.getenv(f"{prefix}_ACCESS_TOKEN", "")
secret     = os.getenv(f"{prefix}_ACCESS_TOKEN_SECRET", "")

print(f"Niche        : {niche}")
print(f"API Key      : {api_key[:6]}…  (len={len(api_key)})")
print(f"API Secret   : {api_secret[:6]}…  (len={len(api_secret)})")
print(f"Access Token : {token[:20]}…  (len={len(token)})")
print(f"Token Secret : {secret[:6]}…  (len={len(secret)})")
print()

user_id_from_token = token.split("-")[0] if token else "N/A"
print(f"User ID in token: {user_id_from_token}")
print(f"Verify at: https://tweeterid.com/  (paste the ID above)")
print()

if not all([api_key, api_secret, token, secret]):
    print("ERROR: one or more credentials are empty — check your .env")
    sys.exit(1)

try:
    import tweepy
except ImportError:
    print("ERROR: tweepy not installed (pip install tweepy)")
    sys.exit(1)

print("Testing v2 client (GET /2/users/me — only needs OAuth 1.0a)…")
client = tweepy.Client(
    consumer_key=api_key,
    consumer_secret=api_secret,
    access_token=token,
    access_token_secret=secret,
)
try:
    me = client.get_me()
    if me.data:
        print(f"  SUCCESS — authenticated as @{me.data.username} (id={me.data.id})")
    else:
        print(f"  Unexpected response: {me}")
except tweepy.errors.TweepyException as e:
    print(f"  FAILED: {e}")
    if hasattr(e, "response") and e.response is not None:
        print(f"  HTTP {e.response.status_code}")
        print(f"  Body: {e.response.text}")
