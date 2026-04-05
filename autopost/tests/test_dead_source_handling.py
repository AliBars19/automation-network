"""
Tests for Fix 6: three GD Twitter sources disabled in geometrydash.yaml.

Disabled sources:
  - @_GeometryDash  (retweet source — 0 tweets collected all-time)
  - @geode_sdk      (retweet source — SDK account rarely posts)
  - @today_gd       (monitor source — likely blocked from DO IP)

Tests cover:
- YAML parsing: disabled sources have enabled: false; active sources do not
- Exactly 3 sources carry enabled: false in geometrydash.yaml
- Source scheduling logic: sources with enabled: false are excluded from results
- Sources with enabled: true or no enabled key are included
- Re-enable test: setting enabled: true restores the source
- Niche separation: rocketleague.yaml has 0 disabled sources
- Zero-source guard: niche with ALL sources disabled triggers error condition
- False-negative guards: true / absent / false behave correctly
"""
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
import json
import sqlite3

import pytest
import yaml

GD_YAML_PATH = Path(__file__).parent.parent / "config" / "geometrydash.yaml"
RL_YAML_PATH = Path(__file__).parent.parent / "config" / "rocketleague.yaml"
SCHEMA_PATH   = Path(__file__).parent.parent / "src" / "database" / "schema.sql"

# The three sources that must be disabled in the current config
DISABLED_SOURCES = {"@_GeometryDash", "@geode_sdk", "@today_gd"}

# Sources that must remain active (sample — not exhaustive)
ACTIVE_SOURCES = {
    "@RobTopGames",
    "@demonlistgd",
    "@DashwordGD",
    "@DemonListNews",
    "@demonlistorg",
    "@StatsGd",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_gd_yaml() -> dict:
    return yaml.safe_load(GD_YAML_PATH.read_text(encoding="utf-8"))


def _load_rl_yaml() -> dict:
    return yaml.safe_load(RL_YAML_PATH.read_text(encoding="utf-8"))


def _get_sources(config: dict) -> list[dict]:
    """Return the sources list from a parsed YAML config."""
    return config.get("sources", [])


def _enabled_sources(config: dict) -> list[dict]:
    """Return sources where enabled is not explicitly False."""
    return [s for s in _get_sources(config) if s.get("enabled", True) is not False]


def _disabled_sources(config: dict) -> list[dict]:
    """Return sources where enabled is explicitly False."""
    return [s for s in _get_sources(config) if s.get("enabled", True) is False]


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()
    return conn


def _source_by_name(config: dict, name: str) -> dict | None:
    for s in _get_sources(config):
        if s.get("name") == name:
            return s
    return None


# ── 1. YAML parsing: disabled sources ─────────────────────────────────────────

class TestYAMLParsingDisabledSources:
    """Each disabled source must have enabled: false in the parsed YAML."""

    def test_geometry_dash_account_has_enabled_false(self):
        cfg = _load_gd_yaml()
        src = _source_by_name(cfg, "@_GeometryDash")
        assert src is not None, "@_GeometryDash not found in geometrydash.yaml"
        assert src.get("enabled") is False

    def test_geode_sdk_account_has_enabled_false(self):
        cfg = _load_gd_yaml()
        src = _source_by_name(cfg, "@geode_sdk")
        assert src is not None, "@geode_sdk not found in geometrydash.yaml"
        assert src.get("enabled") is False

    def test_today_gd_account_has_enabled_false(self):
        cfg = _load_gd_yaml()
        src = _source_by_name(cfg, "@today_gd")
        assert src is not None, "@today_gd not found in geometrydash.yaml"
        assert src.get("enabled") is False

    def test_exactly_three_sources_disabled(self):
        cfg = _load_gd_yaml()
        disabled = _disabled_sources(cfg)
        disabled_names = {s["name"] for s in disabled}
        assert len(disabled) == 3, (
            f"Expected exactly 3 disabled sources, found {len(disabled)}: {disabled_names}"
        )

    def test_disabled_source_names_match_expected(self):
        cfg = _load_gd_yaml()
        disabled = _disabled_sources(cfg)
        disabled_names = {s["name"] for s in disabled}
        assert disabled_names == DISABLED_SOURCES

    def test_geometry_dash_source_is_twitter_type(self):
        cfg = _load_gd_yaml()
        src = _source_by_name(cfg, "@_GeometryDash")
        assert src["type"] == "twitter"

    def test_geode_sdk_source_is_twitter_type(self):
        cfg = _load_gd_yaml()
        src = _source_by_name(cfg, "@geode_sdk")
        assert src["type"] == "twitter"

    def test_today_gd_source_is_twitter_type(self):
        cfg = _load_gd_yaml()
        src = _source_by_name(cfg, "@today_gd")
        assert src["type"] == "twitter"

    def test_disabled_flag_value_is_python_false_not_string(self):
        """yaml.safe_load must parse `false` as Python False, not the string 'false'."""
        cfg = _load_gd_yaml()
        for name in DISABLED_SOURCES:
            src = _source_by_name(cfg, name)
            assert src is not None
            assert src["enabled"] is False, (
                f"{name}: enabled should be Python False, got {type(src['enabled'])}"
            )


# ── 2. Active sources are NOT disabled ────────────────────────────────────────

class TestYAMLParsingActiveSources:
    """Sources that must remain active must not have enabled: false."""

    def test_robtop_games_is_active(self):
        cfg = _load_gd_yaml()
        src = _source_by_name(cfg, "@RobTopGames")
        assert src is not None
        assert src.get("enabled", True) is not False

    def test_demonlistgd_is_active(self):
        cfg = _load_gd_yaml()
        src = _source_by_name(cfg, "@demonlistgd")
        assert src is not None
        assert src.get("enabled", True) is not False

    def test_dashword_gd_is_active(self):
        cfg = _load_gd_yaml()
        src = _source_by_name(cfg, "@DashwordGD")
        assert src is not None
        assert src.get("enabled", True) is not False

    def test_demon_list_news_is_active(self):
        cfg = _load_gd_yaml()
        src = _source_by_name(cfg, "@DemonListNews")
        assert src is not None
        assert src.get("enabled", True) is not False

    def test_stats_gd_is_active(self):
        cfg = _load_gd_yaml()
        src = _source_by_name(cfg, "@StatsGd")
        assert src is not None
        assert src.get("enabled", True) is not False

    def test_demon_list_org_is_active(self):
        cfg = _load_gd_yaml()
        src = _source_by_name(cfg, "@demonlistorg")
        assert src is not None
        assert src.get("enabled", True) is not False

    def test_enabled_count_is_total_minus_three(self):
        cfg = _load_gd_yaml()
        total = len(_get_sources(cfg))
        enabled = len(_enabled_sources(cfg))
        assert enabled == total - 3, (
            f"Expected {total - 3} enabled sources, found {enabled}"
        )


# ── 3. Source scheduling simulation ──────────────────────────────────────────

class TestSourceScheduling:
    """
    Simulate how main.py reads enabled sources from the DB.
    get_sources() in db.py filters WHERE enabled = 1.
    We test the filtering logic directly against the YAML-derived data.
    """

    def test_disabled_sources_excluded_from_enabled_list(self):
        cfg = _load_gd_yaml()
        enabled = _enabled_sources(cfg)
        enabled_names = {s["name"] for s in enabled}
        for name in DISABLED_SOURCES:
            assert name not in enabled_names, f"{name} should not be in enabled sources"

    def test_active_sources_included_in_enabled_list(self):
        cfg = _load_gd_yaml()
        enabled = _enabled_sources(cfg)
        enabled_names = {s["name"] for s in enabled}
        for name in ACTIVE_SOURCES:
            assert name in enabled_names, f"{name} should be in enabled sources"

    def test_sources_with_no_enabled_key_default_to_enabled(self):
        """Sources without an 'enabled' key must be treated as enabled."""
        mock_config = {
            "sources": [
                {"name": "src-no-key", "type": "rss", "url": "https://example.com"},
                {"name": "src-true", "type": "rss", "url": "https://example.com", "enabled": True},
                {"name": "src-false", "type": "rss", "url": "https://example.com", "enabled": False},
            ]
        }
        enabled = _enabled_sources(mock_config)
        enabled_names = {s["name"] for s in enabled}
        assert "src-no-key" in enabled_names
        assert "src-true" in enabled_names
        assert "src-false" not in enabled_names

    def test_enabled_true_explicitly_is_scheduled(self):
        mock_config = {
            "sources": [
                {"name": "explicit-true", "type": "twitter", "enabled": True}
            ]
        }
        enabled = _enabled_sources(mock_config)
        assert len(enabled) == 1
        assert enabled[0]["name"] == "explicit-true"

    def test_enabled_false_explicitly_is_not_scheduled(self):
        mock_config = {
            "sources": [
                {"name": "explicit-false", "type": "twitter", "enabled": False}
            ]
        }
        enabled = _enabled_sources(mock_config)
        assert len(enabled) == 0

    def test_mixed_sources_only_non_false_scheduled(self):
        mock_config = {
            "sources": [
                {"name": "a", "type": "rss", "enabled": True},
                {"name": "b", "type": "rss"},  # no key → default enabled
                {"name": "c", "type": "rss", "enabled": False},
                {"name": "d", "type": "rss", "enabled": False},
            ]
        }
        enabled = _enabled_sources(mock_config)
        enabled_names = {s["name"] for s in enabled}
        assert enabled_names == {"a", "b"}

    def test_get_sources_db_filters_enabled_equals_1(self):
        """get_sources() in db.py uses WHERE enabled = 1. Verify via in-memory DB."""
        from src.database.db import get_sources, upsert_source

        conn = _make_db()
        upsert_source(conn, "geometrydash", "@_GeometryDash", "twitter",
                      {"account_id": "_GeometryDash", "poll_interval": 300, "retweet": True})
        upsert_source(conn, "geometrydash", "@RobTopGames", "twitter",
                      {"account_id": "RobTopGames", "poll_interval": 300, "retweet": True})

        # Disable _GeometryDash in DB (mirrors what YAML-to-DB sync would do)
        conn.execute(
            "UPDATE sources SET enabled = 0 WHERE name = ?", ("@_GeometryDash",)
        )
        conn.commit()

        enabled_rows = get_sources(conn, "geometrydash")
        enabled_names = {row["name"] for row in enabled_rows}
        assert "@_GeometryDash" not in enabled_names
        assert "@RobTopGames" in enabled_names


# ── 4. Re-enable test ──────────────────────────────────────────────────────────

class TestReEnable:
    """Setting enabled: true on a previously-disabled source restores it."""

    def test_re_enabling_geometry_dash_account_makes_it_appear(self):
        cfg = _load_gd_yaml()
        # Create a mutable deep copy and flip the flag
        sources = [dict(s) for s in _get_sources(cfg)]
        for s in sources:
            if s["name"] == "@_GeometryDash":
                s["enabled"] = True
        mock_cfg = {"sources": sources}

        enabled = _enabled_sources(mock_cfg)
        enabled_names = {s["name"] for s in enabled}
        assert "@_GeometryDash" in enabled_names

    def test_re_enabling_geode_sdk_makes_it_appear(self):
        cfg = _load_gd_yaml()
        sources = [dict(s) for s in _get_sources(cfg)]
        for s in sources:
            if s["name"] == "@geode_sdk":
                s["enabled"] = True
        mock_cfg = {"sources": sources}

        enabled = _enabled_sources(mock_cfg)
        enabled_names = {s["name"] for s in enabled}
        assert "@geode_sdk" in enabled_names

    def test_re_enabling_today_gd_makes_it_appear(self):
        cfg = _load_gd_yaml()
        sources = [dict(s) for s in _get_sources(cfg)]
        for s in sources:
            if s["name"] == "@today_gd":
                s["enabled"] = True
        mock_cfg = {"sources": sources}

        enabled = _enabled_sources(mock_cfg)
        enabled_names = {s["name"] for s in enabled}
        assert "@today_gd" in enabled_names

    def test_re_enabling_all_three_restores_total_count(self):
        cfg = _load_gd_yaml()
        sources = [dict(s) for s in _get_sources(cfg)]
        for s in sources:
            if s["name"] in DISABLED_SOURCES:
                s["enabled"] = True
        mock_cfg = {"sources": sources}

        total = len(sources)
        enabled = _enabled_sources(mock_cfg)
        assert len(enabled) == total

    def test_re_enabling_via_db_update(self):
        """Simulate a DB-level re-enable (mirrors production fix procedure)."""
        from src.database.db import get_sources, upsert_source

        conn = _make_db()
        upsert_source(conn, "geometrydash", "@today_gd", "twitter",
                      {"account_id": "today_gd", "poll_interval": 300})
        # Disable
        conn.execute("UPDATE sources SET enabled = 0 WHERE name = '@today_gd'")
        conn.commit()

        # Verify disabled
        rows = get_sources(conn, "geometrydash")
        assert not any(r["name"] == "@today_gd" for r in rows)

        # Re-enable
        conn.execute("UPDATE sources SET enabled = 1 WHERE name = '@today_gd'")
        conn.commit()

        rows = get_sources(conn, "geometrydash")
        assert any(r["name"] == "@today_gd" for r in rows)


# ── 5. Niche separation: rocketleague.yaml ────────────────────────────────────

class TestNicheSeparation:
    """Changes to GD config must not affect RL config."""

    def test_rocketleague_yaml_has_no_disabled_sources(self):
        cfg = _load_rl_yaml()
        disabled = _disabled_sources(cfg)
        assert len(disabled) == 0, (
            f"Expected 0 disabled RL sources, found {len(disabled)}: "
            f"{[s['name'] for s in disabled]}"
        )

    def test_rocketleague_all_sources_are_enabled(self):
        cfg = _load_rl_yaml()
        for src in _get_sources(cfg):
            assert src.get("enabled", True) is not False, (
                f"RL source {src['name']} unexpectedly disabled"
            )

    def test_gd_disabled_source_names_absent_from_rl_yaml(self):
        rl_cfg = _load_rl_yaml()
        rl_names = {s["name"] for s in _get_sources(rl_cfg)}
        for name in DISABLED_SOURCES:
            assert name not in rl_names, f"{name} appears in rocketleague.yaml"

    def test_rl_yaml_has_sources(self):
        cfg = _load_rl_yaml()
        assert len(_get_sources(cfg)) > 0

    def test_both_niches_have_enabled_sources(self):
        gd_cfg = _load_gd_yaml()
        rl_cfg = _load_rl_yaml()
        assert len(_enabled_sources(gd_cfg)) > 0
        assert len(_enabled_sources(rl_cfg)) > 0


# ── 6. Zero-source guard ──────────────────────────────────────────────────────

class TestZeroSourceGuard:
    """
    When a niche has 0 enabled sources, main.py logs an ERROR.
    This is the safeguard added after the RL muzzle incident.
    """

    def test_build_scheduler_logs_error_when_zero_sources(self):
        """build_scheduler() logs ERROR when get_sources returns empty list."""
        from src.main import build_scheduler

        with patch("src.main.get_db") as mock_get_db, \
             patch("src.main.TwitterClient"), \
             patch("src.main.logger") as mock_logger:

            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value.fetchall.return_value = []
            mock_get_db.return_value = mock_conn

            build_scheduler(niches=["geometrydash"])

            error_calls = [
                call for call in mock_logger.error.call_args_list
                if "0 sources" in str(call) or "0 enabled" in str(call)
                   or "sources enabled" in str(call)
            ]
            assert len(error_calls) > 0, (
                "Expected logger.error to be called when niche has 0 enabled sources"
            )

    def test_config_with_all_sources_disabled_yields_zero_enabled(self):
        """A config where every source has enabled: false produces 0 enabled."""
        mock_config = {
            "sources": [
                {"name": "src1", "type": "rss", "enabled": False},
                {"name": "src2", "type": "twitter", "enabled": False},
                {"name": "src3", "type": "youtube", "enabled": False},
            ]
        }
        enabled = _enabled_sources(mock_config)
        assert len(enabled) == 0

    def test_gd_yaml_does_not_result_in_zero_enabled(self):
        """After Fix 6 the GD niche still has multiple enabled sources."""
        cfg = _load_gd_yaml()
        enabled = _enabled_sources(cfg)
        assert len(enabled) > 0

    def test_rl_yaml_does_not_result_in_zero_enabled(self):
        cfg = _load_rl_yaml()
        enabled = _enabled_sources(cfg)
        assert len(enabled) > 0

    def test_get_sources_returns_empty_list_when_all_disabled_in_db(self):
        """db.get_sources() returns [] when all sources have enabled=0."""
        from src.database.db import get_sources, upsert_source

        conn = _make_db()
        upsert_source(conn, "geometrydash", "@_GeometryDash", "twitter", {})
        upsert_source(conn, "geometrydash", "@geode_sdk", "twitter", {})
        conn.execute("UPDATE sources SET enabled = 0 WHERE niche = 'geometrydash'")
        conn.commit()

        rows = get_sources(conn, "geometrydash")
        assert rows == []


# ── 7. False-negative guards ───────────────────────────────────────────────────

class TestFalseNegativeGuards:
    """
    Guard against false negatives: valid sources must never be accidentally excluded.
    """

    def test_source_with_enabled_true_is_included(self):
        mock_config = {
            "sources": [{"name": "active", "type": "rss", "enabled": True}]
        }
        enabled = _enabled_sources(mock_config)
        assert any(s["name"] == "active" for s in enabled)

    def test_source_without_enabled_key_is_included(self):
        mock_config = {
            "sources": [{"name": "no-flag", "type": "rss"}]
        }
        enabled = _enabled_sources(mock_config)
        assert any(s["name"] == "no-flag" for s in enabled)

    def test_source_with_enabled_false_is_excluded(self):
        mock_config = {
            "sources": [{"name": "off", "type": "rss", "enabled": False}]
        }
        enabled = _enabled_sources(mock_config)
        assert not any(s["name"] == "off" for s in enabled)

    def test_gd_youtube_channels_are_all_enabled(self):
        """All GD YouTube channels in the YAML must be enabled (none have enabled: false)."""
        cfg = _load_gd_yaml()
        youtube_sources = [s for s in _get_sources(cfg) if s.get("type") == "youtube"]
        for src in youtube_sources:
            assert src.get("enabled", True) is not False, (
                f"YouTube source {src['name']} unexpectedly disabled"
            )

    def test_gd_api_sources_are_all_enabled(self):
        """All GD API sources must be enabled."""
        cfg = _load_gd_yaml()
        api_sources = [s for s in _get_sources(cfg) if s.get("type") == "api"]
        for src in api_sources:
            assert src.get("enabled", True) is not False, (
                f"API source {src['name']} unexpectedly disabled"
            )

    def test_gd_rss_sources_are_all_enabled(self):
        cfg = _load_gd_yaml()
        rss_sources = [s for s in _get_sources(cfg) if s.get("type") == "rss"]
        for src in rss_sources:
            assert src.get("enabled", True) is not False, (
                f"RSS source {src['name']} unexpectedly disabled"
            )

    def test_only_twitter_type_sources_are_disabled_in_gd(self):
        """All three disabled GD sources are Twitter type — no other types affected."""
        cfg = _load_gd_yaml()
        disabled = _disabled_sources(cfg)
        for src in disabled:
            assert src.get("type") == "twitter", (
                f"Disabled source {src['name']} is type {src.get('type')}, expected twitter"
            )

    def test_enabled_count_is_stable_across_multiple_loads(self):
        """Loading the YAML twice must give the same enabled count (no side effects)."""
        cfg1 = _load_gd_yaml()
        cfg2 = _load_gd_yaml()
        assert len(_enabled_sources(cfg1)) == len(_enabled_sources(cfg2))

    def test_disabled_source_poll_interval_field_present(self):
        """Disabled sources must still have a valid poll_interval in YAML."""
        cfg = _load_gd_yaml()
        for name in DISABLED_SOURCES:
            src = _source_by_name(cfg, name)
            assert src is not None
            assert "poll_interval" in src
            assert isinstance(src["poll_interval"], int)
            assert src["poll_interval"] > 0

    def test_disabled_source_account_id_field_present(self):
        """Disabled Twitter sources must still have account_id configured."""
        cfg = _load_gd_yaml()
        for name in DISABLED_SOURCES:
            src = _source_by_name(cfg, name)
            assert src is not None
            assert "account_id" in src
            assert len(src["account_id"]) > 0

    def test_total_gd_sources_count_is_correct(self):
        """geometrydash.yaml total source count must be >= 10 (sanity check)."""
        cfg = _load_gd_yaml()
        assert len(_get_sources(cfg)) >= 10

    def test_total_rl_sources_count_is_correct(self):
        """rocketleague.yaml total source count must be >= 10 (sanity check)."""
        cfg = _load_rl_yaml()
        assert len(_get_sources(cfg)) >= 10

    def test_disabled_sources_have_name_field(self):
        """Every disabled source must have a non-empty name field."""
        cfg = _load_gd_yaml()
        for src in _disabled_sources(cfg):
            assert "name" in src
            assert len(src["name"]) > 0

    def test_geometry_dash_retweet_field_is_true(self):
        """@_GeometryDash is a retweet source — the retweet field must be true."""
        cfg = _load_gd_yaml()
        src = _source_by_name(cfg, "@_GeometryDash")
        assert src.get("retweet") is True

    def test_geode_sdk_retweet_field_is_true(self):
        """@geode_sdk is a retweet source — the retweet field must be true."""
        cfg = _load_gd_yaml()
        src = _source_by_name(cfg, "@geode_sdk")
        assert src.get("retweet") is True

    def test_today_gd_has_no_retweet_flag(self):
        """@today_gd is a monitor (not retweet) source — retweet key should be absent or False."""
        cfg = _load_gd_yaml()
        src = _source_by_name(cfg, "@today_gd")
        assert src.get("retweet", False) is not True
