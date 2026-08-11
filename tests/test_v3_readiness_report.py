import csv
import json
from datetime import datetime, timedelta, timezone

import v3_readiness_report as readiness


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def write_csv(path, fieldnames, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def base_env():
    return {
        "API_FOOTBALL_KEY": "top-secret-api-key",
        "TELEGRAM_BOT_TOKEN": "top-secret-token",
        "TELEGRAM_CHAT_ID": "top-secret-chat",
    }


def healthy_state(age_minutes=1):
    heartbeat = NOW - timedelta(minutes=age_minutes)
    kickoff = NOW + timedelta(hours=2)
    return {
        "health": {
            "last_cycle_completed_utc": heartbeat.isoformat(),
            "last_cycle_status": "OK",
            "consecutive_errors": 0,
        },
        "fixtures": {
            "123": {
                "fixture_id": "123",
                "kickoff": kickoff.isoformat(),
                "home_team": "Arsenal",
                "away_team": "Chelsea",
            }
        },
    }


def test_missing_api_is_hard_blocker(tmp_path):
    rows, summary = readiness.build_readiness(
        tmp_path, environ={}, now=NOW, required_files=[]
    )

    assert summary["overall_status"] == "BLOCKED"
    assert "API_FOOTBALL" in summary["blockers"]
    assert summary["api_requests_used"] == 0


def test_clean_machine_is_ready_to_start_without_existing_squad(tmp_path):
    rows, summary = readiness.build_readiness(
        tmp_path, environ=base_env(), now=NOW, required_files=[]
    )

    assert summary["overall_status"] == "READY_TO_START"
    assert summary["runtime_status"] == "NOT_STARTED"
    assert "CURRENT_SQUADS" in summary["warnings"]


def test_fresh_monitor_and_healthy_watchdog_are_ready(tmp_path):
    write_json(tmp_path / readiness.STATE_FILE, healthy_state())
    write_csv(
        tmp_path / readiness.HEALTH_FILE,
        ["overall_status", "severity", "code", "fixture_id", "message"],
        [{
            "overall_status": "HEALTHY",
            "severity": "HEALTHY",
            "code": "SYSTEM_OK",
            "fixture_id": "",
            "message": "Healthy",
        }],
    )

    rows, summary = readiness.build_readiness(
        tmp_path, environ=base_env(), now=NOW, required_files=[]
    )

    assert summary["overall_status"] == "READY"
    assert summary["runtime_status"] == "RUNNING"
    assert summary["fixtures_48h"] == 1
    assert summary["nearest_fixture"]["fixture_id"] == "123"


def test_stale_monitor_is_ready_to_restart(tmp_path):
    write_json(tmp_path / readiness.STATE_FILE, healthy_state(age_minutes=20))

    _, summary = readiness.build_readiness(
        tmp_path, environ=base_env(), now=NOW, required_files=[]
    )

    assert summary["overall_status"] == "READY_TO_RESTART"
    assert summary["runtime_status"] == "STALE"


def test_invalid_squad_snapshot_blocks_start(tmp_path):
    write_csv(
        tmp_path / readiness.SQUADS_FILE,
        ["team_name", "player_id", "player_name", "snapshot_utc"],
        [{
            "team_name": "Arsenal",
            "player_id": "1",
            "player_name": "Player",
            "snapshot_utc": NOW.isoformat(),
        }],
    )

    _, summary = readiness.build_readiness(
        tmp_path, environ=base_env(), now=NOW, required_files=[]
    )

    assert summary["overall_status"] == "BLOCKED"
    assert "CURRENT_SQUADS" in summary["blockers"]


def test_secrets_are_never_returned(tmp_path):
    rows, summary = readiness.build_readiness(
        tmp_path, environ=base_env(), now=NOW, required_files=[]
    )
    serialized = json.dumps({"rows": rows, "summary": summary})

    assert "top-secret-api-key" not in serialized
    assert "top-secret-token" not in serialized
    assert "top-secret-chat" not in serialized
