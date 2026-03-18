"""
Global settings — loads from .env and exposes typed config values to the rest of the app.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
MEDIA_DIR = DATA_DIR / "media"
LOGS_DIR = ROOT_DIR / "logs"
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "autopost.db")))

# ── General ────────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# ── Rocket League X credentials ────────────────────────────────────────────────
RL_CREDENTIALS = {
    "api_key":              os.getenv("RL_API_KEY"),
    "api_secret":           os.getenv("RL_API_SECRET"),
    "access_token":         os.getenv("RL_ACCESS_TOKEN"),
    "access_token_secret":  os.getenv("RL_ACCESS_TOKEN_SECRET"),
}

# ── Geometry Dash X credentials ────────────────────────────────────────────────
GD_CREDENTIALS = {
    "api_key":              os.getenv("GD_API_KEY"),
    "api_secret":           os.getenv("GD_API_SECRET"),
    "access_token":         os.getenv("GD_ACCESS_TOKEN"),
    "access_token_secret":  os.getenv("GD_ACCESS_TOKEN_SECRET"),
}

# ── YouTube ────────────────────────────────────────────────────────────────────
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# ── twscrape (Twitter monitoring via GraphQL) ─────────────────────────────────
# Pipe-separated cookie strings: "auth_token=abc; ct0=def|auth_token=ghi; ct0=jkl"
TWSCRAPE_COOKIES = os.getenv("TWSCRAPE_COOKIES")

# ── Discord alerts ─────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# ── Credentials map (keyed by niche) ───────────────────────────────────────────
NICHE_CREDENTIALS = {
    "rocketleague": RL_CREDENTIALS,
    "geometrydash":  GD_CREDENTIALS,
}
