"""
Initialise the database and seed the sources table from YAML configs.
Safe to run multiple times — uses INSERT OR IGNORE on sources.

Usage:
    cd autopost
    python scripts/setup_db.py
"""
import json
import sys
from pathlib import Path

import yaml

# Allow imports from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import DATA_DIR, DB_PATH
from src.database.db import get_db, init_db, upsert_source


YAML_FILES = {
    "rocketleague": Path(__file__).parent.parent / "config" / "rocketleague.yaml",
    "geometrydash":  Path(__file__).parent.parent / "config" / "geometrydash.yaml",
}

# Fields that are stored at the top level of the sources table — everything
# else gets packed into the config JSON blob.
_TOP_LEVEL = {"name", "type"}


def seed_sources(niche: str, yaml_path: Path) -> int:
    """Parse one YAML file and upsert all sources. Returns count inserted."""
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    sources = data.get("sources", [])
    count = 0
    with get_db() as conn:
        for src in sources:
            name   = src["name"]
            type_  = src["type"]
            config = {k: v for k, v in src.items() if k not in _TOP_LEVEL}
            upsert_source(conn, niche, name, type_, config)
            count += 1
    return count


def main() -> None:
    # Ensure data/ directory exists
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Database path: {DB_PATH}")

    print("Initialising schema…")
    init_db()
    print("  Schema ready.")

    for niche, yaml_path in YAML_FILES.items():
        if not yaml_path.exists():
            print(f"  [WARN] {yaml_path} not found — skipping {niche}")
            continue
        n = seed_sources(niche, yaml_path)
        print(f"  {niche}: {n} sources seeded.")

    print("Done.")


if __name__ == "__main__":
    main()
