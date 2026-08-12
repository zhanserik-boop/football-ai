import contextlib
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import shadow_value_gate_v1 as value_gate
import system_health_watchdog as health_watchdog
import v3_backup_guard as backup_guard
import v3_external_supervisor as supervisor
import v3_runtime_checkpoint as checkpoint
import asian_handicap_v3_r2 as asian_handicap


OUTPUT_FILE = "v3_emergency_drill_report.json"
FROZEN_RELEASE = "V3_SHADOW_FROZEN_R2"


def utc_now():
    return datetime.now(timezone.utc)


def write_json_atomic(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def health_to_value_gate_scenario(sandbox, now):
    stale = now - timedelta(minutes=30)
    monitor_state = {
        "health": {
            "last_cycle_completed_utc": stale.isoformat(),
            "last_cycle_status": "OK",
            "consecutive_errors": 0,
        },
        "fixtures": {},
    }
    health_rows = health_watchdog.build_health(monitor_state, now=now)
    require(health_rows[0]["overall_status"] == "CRITICAL", "stale heartbeat was not critical")
    require(
        any(row["code"] == "MONITOR_HEARTBEAT_STALE" for row in health_rows[1:]),
        "stale-heartbeat code is missing",
    )

    kickoff = now + timedelta(hours=1)
    ah_rows = [{
        "fixture_id": "drill-1",
        "kickoff_utc": kickoff.isoformat(),
        "minutes_to_kickoff": "60",
        "home_team": "Drill Home",
        "away_team": "Drill Away",
        "signal": "HOME",
        "abs_shock": "1.75",
        "data_quality": "HIGH",
        "decision": "BET",
        "current_handicap": "-0.5",
        "current_avg_odds": "1.95",
        "current_best_odds": "2.00",
        "current_best_bookmaker": "DRILL",
    }]
    timeline_rows = [{
        "fixture_id": "drill-1",
        "snapshot_utc": now.isoformat(),
        "phase": "POST_XI",
        "freshness_status": "POST_XI_CHANGED",
        "home_bookmakers": "3",
        "away_bookmakers": "3",
    }]
    gate_rows = value_gate.build_gate_rows(
        ah_rows, timeline_rows, [], [], now=now, health_rows=health_rows
    )
    require(len(gate_rows) == 1, "Value Gate did not return the drill fixture")
    gate = gate_rows[0]
    require(gate["market_freshness"] == "FRESH", "drill market setup was not fresh")
    require(gate["gate_decision"] == "WATCH", "critical health did not block SHADOW BET")
    require("Safety Kill Switch" in gate["reason"], "kill-switch reason is missing")
    require(gate["shadow_only"] == 1, "Value Gate left shadow-only mode")
    return (
        "stale heartbeat became CRITICAL and converted an otherwise eligible "
        "candidate to WATCH"
    )


def supervisor_alert_scenario(sandbox, now):
    root = Path(sandbox) / "supervisor"
    root.mkdir()
    stale = now - timedelta(minutes=30)
    supervisor.set_expected_running(True, root=root, now=stale, reason="DRILL")
    supervisor.write_json_atomic(root / supervisor.MONITOR_STATE_FILE, {
        "health": {
            "last_cycle_completed_utc": stale.isoformat(),
            "last_cycle_status": "OK",
            "consecutive_errors": 0,
        }
    })
    messages = []
    sender = lambda message: messages.append(message) or True
    with contextlib.redirect_stdout(io.StringIO()):
        first_status, first_event, first_sent = supervisor.run_once(
            root=root, sender=sender, now=now
        )
        _, repeated_event, repeated_sent = supervisor.run_once(
            root=root, sender=sender, now=now + timedelta(minutes=1)
        )
    require(first_status["overall_status"] == "CRITICAL", "supervisor missed stale heartbeat")
    require(first_event and first_event[0] == "ISSUE" and first_sent, "first alert was not captured")
    require(repeated_event is None and not repeated_sent, "duplicate alert was not suppressed")

    supervisor.write_json_atomic(root / supervisor.MONITOR_STATE_FILE, {
        "health": {
            "last_cycle_completed_utc": (now + timedelta(minutes=2)).isoformat(),
            "last_cycle_status": "OK",
            "consecutive_errors": 0,
        }
    })
    with contextlib.redirect_stdout(io.StringIO()):
        recovered_status, recovered_event, recovered_sent = supervisor.run_once(
            root=root, sender=sender, now=now + timedelta(minutes=2)
        )
    require(recovered_status["overall_status"] == "HEALTHY", "supervisor did not recover")
    require(
        recovered_event and recovered_event[0] == "RECOVERED" and recovered_sent,
        "recovery alert was not captured",
    )
    require(len(messages) == 2, "unexpected number of captured Telegram messages")
    return "one issue alert, no duplicate, and one recovery alert were captured locally"


def checkpoint_backup_scenario(sandbox, now):
    base = Path(sandbox)
    root = base / "project"
    mirror = base / "external_mirror"
    root.mkdir()
    evidence = root / "evidence.csv"
    evidence.write_text("id,value\n1,drill\n", encoding="utf-8")
    (root / ".env").write_text("DRILL_SECRET=must-not-copy", encoding="utf-8")

    status = checkpoint.checkpoint_once(
        root, now=now, filenames=("evidence.csv",), min_interval_hours=0
    )
    require(status["status"] == "CREATED", "verified checkpoint was not created")
    archive = checkpoint.latest_checkpoint(root)
    verification = checkpoint.verify_checkpoint(archive)
    require(verification["valid"], "fresh checkpoint failed verification")
    require(not verification["manifest"]["secrets_included"], "manifest included secrets")
    with zipfile.ZipFile(archive) as opened:
        require(".env" not in opened.namelist(), ".env entered the checkpoint")

    environment = {backup_guard.MIRROR_ENV: str(mirror)}
    healthy = backup_guard.build_guard(root, environ=environment, now=now)
    require(healthy["overall_status"] == "HEALTHY", "checkpoint mirror was not healthy")
    require(healthy["mirror"]["status"] in {"COPIED", "SYNCED"}, "mirror was not synchronized")
    mirrored = mirror / archive.name
    require(checkpoint.verify_checkpoint(mirrored)["valid"], "mirrored checkpoint is invalid")

    archive.write_bytes(b"deliberately corrupted by isolated emergency drill")
    broken = backup_guard.build_guard(
        root, environ=environment, now=now + timedelta(minutes=1)
    )
    codes = {row["code"] for row in broken["issues"]}
    require(broken["overall_status"] == "CRITICAL", "corrupt local checkpoint was not critical")
    require("LOCAL_CHECKPOINT_CORRUPT" in codes, "corruption code is missing")
    issue_event = backup_guard.notification_event(broken, {})
    require(issue_event and issue_event[0] == "ISSUE", "backup issue notification was not created")
    notification_state = {
        "overall_status": broken["overall_status"],
        "fingerprint": backup_guard.issue_fingerprint(broken),
    }
    require(
        backup_guard.notification_event(broken, notification_state) is None,
        "backup duplicate notification was not suppressed",
    )

    restore_target = base / "verified_restore"
    restored = checkpoint.restore_checkpoint(mirrored, restore_target)
    require(restored["verified"], "external checkpoint restore was not verified")
    require(
        (restore_target / "evidence.csv").read_text(encoding="utf-8")
        == "id,value\n1,drill\n",
        "restored evidence differs from the checkpoint",
    )
    require(not (restore_target / ".env").exists(), "secret appeared in restored data")
    try:
        checkpoint.restore_checkpoint(mirrored, restore_target)
    except FileExistsError:
        pass
    else:
        raise AssertionError("restore overwrote an existing directory")
    return (
        "checkpoint and mirror verified, corruption detected, and external restore "
        "completed without secrets or overwrite"
    )


def run_scenario(name, callback):
    try:
        detail = callback()
        return {"name": name, "status": "PASS", "detail": detail}
    except Exception as exc:
        return {"name": name, "status": "FAIL", "detail": f"{type(exc).__name__}: {exc}"}


def asian_handicap_r2_scenario():
    rows = []
    # Alternative line first on purpose: R2 must choose the balanced main line.
    for bookmaker in ("A", "B", "C"):
        rows.extend([
            {"bookmaker": bookmaker, "side": "HOME", "handicap": -1.0, "odd": 2.50},
            {"bookmaker": bookmaker, "side": "AWAY", "handicap": -1.0, "odd": 1.55},
            {"bookmaker": bookmaker, "side": "HOME", "handicap": -0.5, "odd": 1.94},
            {"bookmaker": bookmaker, "side": "AWAY", "handicap": -0.5, "odd": 1.96},
        ])
    market = asian_handicap.market_consensus(rows)
    require(market is not None, "paired AH market was not normalized")
    require(market["home_handicap"] == -0.5, "alternative AH line became the main line")
    away = asian_handicap.signal_market(rows, "AWAY")
    require(away["handicap"] == 0.5, "AWAY signal handicap was not inverted")
    require(
        asian_handicap.line_move_toward_signal(-0.5, -0.25, "AWAY") == 0.25,
        "AWAY market movement direction is reversed",
    )
    return "balanced main line, same-label provider layout, and AWAY direction verified"


def build_drill(root=".", now=None):
    root = Path(root)
    now = now or utc_now()
    with TemporaryDirectory(prefix="football_ai_v3_drill_") as directory:
        sandbox = Path(directory)
        results = [
            run_scenario(
                "HEALTH_TO_VALUE_GATE",
                lambda: health_to_value_gate_scenario(sandbox, now),
            ),
            run_scenario(
                "SUPERVISOR_ALERT_DEDUPE_RECOVERY",
                lambda: supervisor_alert_scenario(sandbox, now),
            ),
            run_scenario(
                "CHECKPOINT_MIRROR_CORRUPTION_RESTORE",
                lambda: checkpoint_backup_scenario(sandbox, now),
            ),
            run_scenario("AH_R2_NORMALIZATION", asian_handicap_r2_scenario),
        ]
    passed = sum(row["status"] == "PASS" for row in results)
    report = {
        "release": FROZEN_RELEASE,
        "checked_utc": now.isoformat(),
        "status": "PASSED" if passed == len(results) else "FAILED",
        "scenarios_passed": passed,
        "scenarios_total": len(results),
        "results": results,
        "isolated_temporary_workspace": True,
        "real_telegram_messages_sent": 0,
        "live_runtime_files_modified": False,
        "secrets_included": False,
        "football_data_api_requests_used": 0,
        "shadow_only": True,
    }
    write_json_atomic(root / OUTPUT_FILE, report)
    return report


def print_report(report):
    print("\n" + "=" * 72)
    print("FOOTBALL AI V3 — EMERGENCY E2E DRILL")
    print("=" * 72)
    print("STATUS:", report["status"])
    print("SCENARIOS:", f"{report['scenarios_passed']}/{report['scenarios_total']} PASS")
    for row in report["results"]:
        print(f"[{row['status']:<4}] {row['name']}: {row['detail']}")
    print("ISOLATED TEMP WORKSPACE: YES")
    print("REAL TELEGRAM SENT: NO")
    print("LIVE RUNTIME FILES MODIFIED: NO")
    print("SECRETS INCLUDED: NO")
    print("API REQUESTS USED: 0")
    print("SHADOW ONLY: YES")
    print("=" * 72)
    print("JSON:", OUTPUT_FILE)


def main():
    report = build_drill()
    print_report(report)
    raise SystemExit(0 if report["status"] == "PASSED" else 1)


if __name__ == "__main__":
    main()
