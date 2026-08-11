import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path


OUTPUT_FILE = "v3_readiness_report.csv"
SUMMARY_FILE = "v3_readiness_summary.json"
STATE_FILE = "market_monitor_v2_state.json"
HEALTH_FILE = "system_health_live.csv"
SQUADS_FILE = "current_squads_2026.csv"

HEARTBEAT_WARN_MINUTES = 8.0
HEARTBEAT_CRITICAL_MINUTES = 12.0
SQUAD_WARN_HOURS = 36.0
FIXTURE_WINDOW_HOURS = 48.0

API_KEY_NAMES = ("API_FOOTBALL_KEY", "API_KEY", "APISPORTS_KEY")
TELEGRAM_TOKEN_NAME = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_NAME = "TELEGRAM_CHAT_ID"

BASE_REQUIRED_FILES = (
    "run_live_system.py",
    "market_monitor_v2.py",
    "live_lineup_engine.py",
    "odds_provider.py",
    "ah_agent_v2.py",
    "master_agent_v1.py",
    "post_lineup_clv_report.py",
    "btts_fixture_feed.py",
    "btts_live_features.py",
    "btts_live_predict.py",
    "btts_live_agent.py",
    "download_xg.py",
    "download_old_odds.py",
    "update_squads_transfers.py",
    "telegram_notifier.py",
    "btts_core_model_2026.joblib",
    "btts_core_model_2026_meta.json",
    "epl_lineups_4seasons.csv",
    "player_match_history_2022.csv",
    "player_match_history_2023.csv",
    "player_match_history_2024.csv",
    "player_match_history_2025.csv",
)

V3_REQUIRED_FILES = (
    "run_live_system_v3.py",
    "live_coach_context.py",
    "market_timeline_engine.py",
    "shadow_value_gate_v1.py",
    "shadow_value_gate_outcome_report.py",
    "shadow_value_gate_notifier.py",
    "system_health_watchdog.py",
    "v3_readiness_report.py",
    "v3_forward_test_scorecard.py",
    "v3_drift_watch.py",
    "v3_shadow_risk_engine.py",
    "v3_runtime_checkpoint.py",
    "v3_backup_guard.py",
    "v3_external_supervisor.py",
    "install_v3_supervisor.ps1",
    "uninstall_v3_supervisor.ps1",
    "v3_daily_digest.py",
    "v3_emergency_drill.py",
)

FIELDS = (
    "checked_utc",
    "overall_status",
    "category",
    "check",
    "status",
    "severity",
    "detail",
)


def utc_now():
    return datetime.now(timezone.utc)


def clean(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def parse_dt(value):
    text = clean(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def age_minutes(value, now):
    parsed = parse_dt(value)
    if parsed is None:
        return None
    return (now - parsed).total_seconds() / 60.0


def read_env_file(path):
    values = {}
    if not path.exists():
        return values
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    values[key] = value
    except Exception:
        return {}
    return values


def effective_env(root, environ=None):
    values = read_env_file(root / ".env")
    values.update(dict(os.environ if environ is None else environ))
    return values


def configured(env, names):
    return any(clean(env.get(name)) for name in names)


def read_json(path):
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def read_csv_rows(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            return list(reader) if reader.fieldnames else []
    except Exception:
        return []


def add_check(rows, category, check, status, severity, detail):
    rows.append({
        "category": category,
        "check": check,
        "status": status,
        "severity": severity,
        "detail": detail,
    })


def check_required_files(root, rows, required_files):
    missing = [name for name in required_files if not (root / name).exists()]
    if missing:
        add_check(
            rows,
            "FILES",
            "REQUIRED_FILES",
            "FAIL",
            "BLOCKER",
            f"Missing {len(missing)}: " + ", ".join(missing),
        )
    else:
        add_check(
            rows,
            "FILES",
            "REQUIRED_FILES",
            "PASS",
            "INFO",
            f"All {len(required_files)} required files are present",
        )


def check_configuration(env, rows):
    if configured(env, API_KEY_NAMES):
        add_check(
            rows, "CONFIG", "API_FOOTBALL", "PASS", "INFO",
            "API-Football credential is configured (value hidden)",
        )
        api_ready = True
    else:
        add_check(
            rows, "CONFIG", "API_FOOTBALL", "FAIL", "BLOCKER",
            "Set one of API_FOOTBALL_KEY, API_KEY or APISPORTS_KEY",
        )
        api_ready = False

    token_ready = configured(env, (TELEGRAM_TOKEN_NAME,))
    chat_ready = configured(env, (TELEGRAM_CHAT_NAME,))
    if token_ready and chat_ready:
        add_check(
            rows, "CONFIG", "TELEGRAM", "PASS", "INFO",
            "Telegram bot token and chat ID are configured (values hidden)",
        )
    else:
        missing = []
        if not token_ready:
            missing.append(TELEGRAM_TOKEN_NAME)
        if not chat_ready:
            missing.append(TELEGRAM_CHAT_NAME)
        add_check(
            rows, "CONFIG", "TELEGRAM", "WARN", "WARNING",
            "Alerts disabled; missing: " + ", ".join(missing),
        )

    provider = clean(env.get("FOOTBALL_AI_ODDS_PROVIDER")) or "api-football"
    add_check(
        rows, "CONFIG", "ODDS_PROVIDER", "PASS", "INFO",
        f"Provider selected: {provider}",
    )
    return api_ready


def squad_snapshot_time(rows):
    timestamps = [parse_dt(row.get("snapshot_utc")) for row in rows]
    timestamps = [value for value in timestamps if value is not None]
    return max(timestamps) if timestamps else None


def check_squads(root, rows, api_ready, now):
    path = root / SQUADS_FILE
    if not path.exists():
        severity = "WARNING" if api_ready else "BLOCKER"
        status = "WARN" if api_ready else "FAIL"
        detail = (
            "Snapshot is absent; startup can create it with the configured API"
            if api_ready
            else "Snapshot is absent and cannot be refreshed without API credentials"
        )
        add_check(rows, "DATA", "CURRENT_SQUADS", status, severity, detail)
        return

    squad_rows = read_csv_rows(path)
    required = {"team_name", "player_id", "player_name"}
    columns = set(squad_rows[0]) if squad_rows else set()
    if not squad_rows or not required.issubset(columns):
        missing = sorted(required - columns)
        add_check(
            rows, "DATA", "CURRENT_SQUADS", "FAIL", "BLOCKER",
            "Snapshot is empty or malformed; missing columns: "
            + (", ".join(missing) if missing else "valid rows"),
        )
        return

    team_players = {}
    for row in squad_rows:
        team = clean(row.get("team_name"))
        player = clean(row.get("player_id"))
        if team and player:
            team_players.setdefault(team, set()).add(player)

    thin = {team: len(players) for team, players in team_players.items() if len(players) < 15}
    if len(team_players) != 20 or thin:
        detail = f"Expected 20 EPL teams with >=15 players; teams={len(team_players)}"
        if thin:
            detail += "; thin squads: " + ", ".join(
                f"{team}={count}" for team, count in sorted(thin.items())
            )
        add_check(rows, "DATA", "CURRENT_SQUADS", "FAIL", "BLOCKER", detail)
        return

    snapshot = squad_snapshot_time(squad_rows)
    if snapshot is None:
        age_hours = (now.timestamp() - path.stat().st_mtime) / 3600.0
    else:
        age_hours = (now - snapshot).total_seconds() / 3600.0
    if age_hours > SQUAD_WARN_HOURS:
        add_check(
            rows, "DATA", "CURRENT_SQUADS", "WARN", "WARNING",
            f"Valid 20-team snapshot is {age_hours:.1f}h old; startup should refresh it",
        )
    else:
        add_check(
            rows, "DATA", "CURRENT_SQUADS", "PASS", "INFO",
            f"Valid 20-team snapshot; age {max(age_hours, 0):.1f}h",
        )


def heartbeat_value(state):
    health = state.get("health", {}) if state else {}
    return (
        health.get("last_cycle_completed_utc")
        or health.get("last_cycle_started_utc")
        or (state or {}).get("last_fixture_refresh")
    )


def fixture_summary(state, now):
    candidates = []
    for fixture_id, raw in ((state or {}).get("fixtures", {}) or {}).items():
        fixture = dict(raw or {})
        kickoff = parse_dt(fixture.get("kickoff"))
        if kickoff is None:
            continue
        minutes = (kickoff - now).total_seconds() / 60.0
        if 0 <= minutes <= FIXTURE_WINDOW_HOURS * 60:
            candidates.append((minutes, str(fixture_id), fixture, kickoff))
    candidates.sort(key=lambda value: value[0])
    if not candidates:
        return 0, None
    minutes, fixture_id, fixture, kickoff = candidates[0]
    return len(candidates), {
        "fixture_id": clean(fixture.get("fixture_id")) or fixture_id,
        "home_team": clean(fixture.get("home_team")),
        "away_team": clean(fixture.get("away_team")),
        "kickoff_utc": kickoff.isoformat(),
        "minutes_to_kickoff": round(minutes, 1),
    }


def check_runtime(root, rows, now):
    state_path = root / STATE_FILE
    state = read_json(state_path)
    fixtures_48h, nearest = fixture_summary(state, now)
    if state is None:
        add_check(
            rows, "RUNTIME", "MARKET_MONITOR", "STOPPED", "INFO",
            "No readable monitor state; system has not started yet",
        )
        runtime_status = "NOT_STARTED"
    else:
        heartbeat = heartbeat_value(state)
        age = age_minutes(heartbeat, now)
        if age is None:
            age = (now.timestamp() - state_path.stat().st_mtime) / 60.0
        cycle_status = clean((state.get("health", {}) or {}).get("last_cycle_status")).upper()
        errors = int((state.get("health", {}) or {}).get("consecutive_errors") or 0)
        if age > HEARTBEAT_CRITICAL_MINUTES:
            add_check(
                rows, "RUNTIME", "MARKET_MONITOR", "STOPPED", "WARNING",
                f"Heartbeat is stale: {age:.1f}m",
            )
            runtime_status = "STALE"
        elif age > HEARTBEAT_WARN_MINUTES or cycle_status == "ERROR":
            detail = f"Heartbeat age {age:.1f}m"
            if cycle_status == "ERROR":
                detail += f"; cycle errors={errors}"
            add_check(rows, "RUNTIME", "MARKET_MONITOR", "WARN", "WARNING", detail)
            runtime_status = "DEGRADED"
        else:
            add_check(
                rows, "RUNTIME", "MARKET_MONITOR", "RUNNING", "INFO",
                f"Heartbeat age {max(age, 0):.1f}m; fixtures inside 48h: {fixtures_48h}",
            )
            runtime_status = "RUNNING"

    if nearest:
        name = f"{nearest['home_team']} - {nearest['away_team']}".strip(" -")
        add_check(
            rows, "SCHEDULE", "NEXT_FIXTURE", "FOUND", "INFO",
            f"{name or nearest['fixture_id']} in {nearest['minutes_to_kickoff']:.1f}m; "
            f"{fixtures_48h} fixture(s) inside 48h",
        )
    else:
        add_check(
            rows, "SCHEDULE", "NEXT_FIXTURE", "NONE", "INFO",
            "No cached upcoming fixture inside 48h",
        )
    return state, runtime_status, fixtures_48h, nearest


def check_health(root, rows, runtime_status):
    health_rows = read_csv_rows(root / HEALTH_FILE)
    if runtime_status != "RUNNING":
        add_check(
            rows, "SAFETY", "HEALTH_WATCHDOG", "INACTIVE", "INFO",
            "Health gate will become active after the live cycle starts",
        )
        return "INACTIVE", []
    if not health_rows:
        add_check(
            rows, "SAFETY", "HEALTH_WATCHDOG", "WARN", "WARNING",
            "Live monitor is running but no watchdog report is available yet",
        )
        return "MISSING", []

    overall = clean(health_rows[0].get("overall_status") or health_rows[0].get("severity")).upper()
    active = [
        {
            "severity": clean(row.get("severity")).upper(),
            "code": clean(row.get("code")),
            "fixture_id": clean(row.get("fixture_id")),
            "message": clean(row.get("message")),
        }
        for row in health_rows[1:]
        if clean(row.get("severity")).upper() in {"DEGRADED", "CRITICAL"}
    ]
    if overall == "HEALTHY":
        add_check(
            rows, "SAFETY", "HEALTH_WATCHDOG", "PASS", "INFO",
            "Latest watchdog report is HEALTHY",
        )
    else:
        severity = "BLOCKER" if overall == "CRITICAL" else "WARNING"
        add_check(
            rows, "SAFETY", "HEALTH_WATCHDOG", "FAIL" if overall == "CRITICAL" else "WARN",
            severity, f"Latest watchdog status: {overall or 'UNKNOWN'}; active issues={len(active)}",
        )
    return overall or "UNKNOWN", active


def choose_overall(rows, runtime_status, health_status):
    if any(row["severity"] == "BLOCKER" and row["category"] != "SAFETY" for row in rows):
        return "BLOCKED"
    if runtime_status == "NOT_STARTED":
        return "READY_TO_START"
    if runtime_status == "STALE":
        return "READY_TO_RESTART"
    if runtime_status == "DEGRADED" or health_status in {"CRITICAL", "DEGRADED", "MISSING", "UNKNOWN"}:
        return "DEGRADED"
    return "READY"


def build_readiness(root=".", environ=None, now=None, required_files=None):
    root = Path(root)
    now = now or utc_now()
    env = effective_env(root, environ)
    rows = []
    required = tuple(BASE_REQUIRED_FILES + V3_REQUIRED_FILES) if required_files is None else tuple(required_files)
    check_required_files(root, rows, required)
    api_ready = check_configuration(env, rows)
    check_squads(root, rows, api_ready, now)
    _, runtime_status, fixtures_48h, nearest = check_runtime(root, rows, now)
    health_status, health_issues = check_health(root, rows, runtime_status)
    overall = choose_overall(rows, runtime_status, health_status)
    checked = now.isoformat()
    for row in rows:
        row["checked_utc"] = checked
        row["overall_status"] = overall

    blockers = [row["check"] for row in rows if row["severity"] == "BLOCKER"]
    warnings = [row["check"] for row in rows if row["severity"] == "WARNING"]
    summary = {
        "checked_utc": checked,
        "overall_status": overall,
        "runtime_status": runtime_status,
        "health_status": health_status,
        "blockers": blockers,
        "warnings": warnings,
        "fixtures_48h": fixtures_48h,
        "nearest_fixture": nearest,
        "active_health_issues": health_issues,
        "api_requests_used": 0,
    }
    return rows, summary


def write_csv_atomic(path, rows):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def write_json_atomic(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def print_report(rows, summary):
    print("\n" + "=" * 72)
    print("FOOTBALL AI V3 — READINESS REPORT")
    print("=" * 72)
    print("OVERALL:", summary["overall_status"])
    print("RUNTIME:", summary["runtime_status"])
    print("HEALTH:", summary["health_status"])
    print("FIXTURES <=48H:", summary["fixtures_48h"])
    print("API REQUESTS USED: 0")
    print()
    for row in rows:
        marker = {"PASS": "OK", "RUNNING": "OK", "FOUND": "OK"}.get(row["status"], row["status"])
        print(f"[{marker:8}] {row['category']}/{row['check']}: {row['detail']}")
    print("=" * 72)
    print("CSV:", OUTPUT_FILE)
    print("JSON:", SUMMARY_FILE)


def main():
    root = Path(".")
    rows, summary = build_readiness(root=root)
    write_csv_atomic(root / OUTPUT_FILE, rows)
    write_json_atomic(root / SUMMARY_FILE, summary)
    print_report(rows, summary)


if __name__ == "__main__":
    main()
