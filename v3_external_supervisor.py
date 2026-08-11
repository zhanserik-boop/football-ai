import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


MONITOR_STATE_FILE = "market_monitor_v2_state.json"
EXPECTATION_FILE = "v3_supervisor_expectation.json"
REPORT_FILE = "v3_external_supervisor_status.json"
NOTIFY_STATE_FILE = "v3_external_supervisor_notify_state.json"

HEARTBEAT_WARN_MINUTES = 8.0
HEARTBEAT_CRITICAL_MINUTES = 12.0
STARTUP_GRACE_MINUTES = 15.0

SEVERITY = {"HEALTHY": 0, "DEGRADED": 1, "CRITICAL": 2}


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


def minutes_since(value, now):
    parsed = parse_dt(value)
    return None if parsed is None else (now - parsed).total_seconds() / 60.0


def read_json(path):
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def write_json_atomic(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def set_expected_running(expected, root=".", now=None, reason=""):
    now = now or utc_now()
    value = {
        "expected_running": bool(expected),
        "updated_utc": now.isoformat(),
        "reason": clean(reason) or ("V3_START" if expected else "CLEAN_STOP"),
    }
    write_json_atomic(Path(root) / EXPECTATION_FILE, value)
    return value


def heartbeat_time(state, state_path):
    health = state.get("health", {}) if state else {}
    value = (
        health.get("last_cycle_completed_utc")
        or health.get("last_cycle_started_utc")
        or (state or {}).get("last_fixture_refresh")
    )
    if value:
        return value
    if state_path.exists():
        return datetime.fromtimestamp(
            state_path.stat().st_mtime, tz=timezone.utc
        ).isoformat()
    return ""


def issue(severity, code, message):
    return {"severity": severity, "code": code, "message": message}


def build_status(root=".", now=None):
    root = Path(root)
    now = now or utc_now()
    expectation = read_json(root / EXPECTATION_FILE)
    expected = expectation.get("expected_running") is True
    expected_since = minutes_since(expectation.get("updated_utc"), now)

    if not expectation:
        return {
            "checked_utc": now.isoformat(),
            "overall_status": "UNARMED",
            "expected_running": False,
            "heartbeat_age_minutes": "",
            "issues": [],
            "message": "Supervisor has not been armed by V3 yet",
            "football_data_api_requests_used": 0,
        }
    if not expected:
        return {
            "checked_utc": now.isoformat(),
            "overall_status": "STANDBY",
            "expected_running": False,
            "heartbeat_age_minutes": "",
            "issues": [],
            "message": "V3 was stopped cleanly; no alert required",
            "football_data_api_requests_used": 0,
        }

    state_path = root / MONITOR_STATE_FILE
    state = read_json(state_path)
    heartbeat = heartbeat_time(state, state_path)
    heartbeat_age = minutes_since(heartbeat, now)
    issues = []
    inside_grace = expected_since is not None and expected_since <= STARTUP_GRACE_MINUTES

    if not state or heartbeat_age is None:
        if inside_grace:
            overall = "STARTING"
        else:
            issues.append(issue(
                "CRITICAL", "MONITOR_HEARTBEAT_MISSING",
                "V3 expects Market Monitor to run, but no heartbeat is available",
            ))
            overall = "CRITICAL"
    elif heartbeat_age > HEARTBEAT_CRITICAL_MINUTES:
        if inside_grace:
            overall = "STARTING"
        else:
            issues.append(issue(
                "CRITICAL", "MONITOR_HEARTBEAT_STALE",
                f"Market Monitor heartbeat is {heartbeat_age:.1f} minutes old",
            ))
            overall = "CRITICAL"
    else:
        if heartbeat_age > HEARTBEAT_WARN_MINUTES:
            issues.append(issue(
                "DEGRADED", "MONITOR_HEARTBEAT_DELAYED",
                f"Market Monitor heartbeat is delayed by {heartbeat_age:.1f} minutes",
            ))
        health = state.get("health", {}) or {}
        cycle_status = clean(health.get("last_cycle_status")).upper()
        errors = int(health.get("consecutive_errors") or 0)
        if cycle_status == "ERROR":
            severity = "CRITICAL" if errors >= 3 else "DEGRADED"
            issues.append(issue(
                severity, "MONITOR_CYCLE_ERROR",
                clean(health.get("last_error")) or f"Monitor cycle errors: {errors}",
            ))
        overall = "HEALTHY"
        if issues:
            overall = max(issues, key=lambda row: SEVERITY[row["severity"]])["severity"]

    return {
        "checked_utc": now.isoformat(),
        "overall_status": overall,
        "expected_running": True,
        "expected_since_minutes": "" if expected_since is None else round(expected_since, 2),
        "heartbeat_utc": heartbeat,
        "heartbeat_age_minutes": (
            "" if heartbeat_age is None else round(heartbeat_age, 2)
        ),
        "issues": issues,
        "message": (
            "V3 startup grace period is active"
            if overall == "STARTING"
            else "External V3 supervision is healthy"
            if overall == "HEALTHY"
            else f"{len(issues)} external supervision issue(s)"
        ),
        "football_data_api_requests_used": 0,
    }


def issue_fingerprint(status):
    return "|".join(sorted(
        f"{row['severity']}:{row['code']}" for row in status.get("issues", [])
    ))


def notification_message(status, recovered=False):
    if recovered:
        return "\n".join([
            "V3 EXTERNAL SUPERVISOR — RECOVERED",
            "",
            "Market Monitor heartbeat is healthy again.",
            "The live system is responding.",
        ])
    lines = [
        f"V3 EXTERNAL SUPERVISOR — {status['overall_status']}",
        "",
    ]
    for row in status.get("issues", []):
        lines.append(f"{row['code']}: {row['message']}")
    lines.extend([
        "",
        "Check the Football AI computer and restart run_live_system_v3.py if needed.",
    ])
    return "\n".join(lines)


def notification_event(status, state):
    overall = status.get("overall_status", "")
    previous_overall = state.get("overall_status", "")
    fingerprint = issue_fingerprint(status)
    previous_fingerprint = state.get("fingerprint", "")
    if overall in {"CRITICAL", "DEGRADED"}:
        if fingerprint != previous_fingerprint:
            return "ISSUE", notification_message(status)
        return None
    if overall == "HEALTHY" and previous_overall in {"CRITICAL", "DEGRADED"}:
        return "RECOVERED", notification_message(status, recovered=True)
    return None


def run_once(root=".", sender=None, notify=True, now=None):
    root = Path(root)
    status = build_status(root, now=now)
    write_json_atomic(root / REPORT_FILE, status)
    state = read_json(root / NOTIFY_STATE_FILE)
    event = notification_event(status, state)
    sent = False
    if notify and event:
        if sender is None:
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                from telegram_notifier import send_telegram
                sent = bool(send_telegram(event[1]))
            finally:
                os.chdir(old_cwd)
        else:
            sent = bool(sender(event[1]))
        if sent:
            write_json_atomic(root / NOTIFY_STATE_FILE, {
                "fingerprint": issue_fingerprint(status),
                "overall_status": status["overall_status"],
                "updated_utc": status["checked_utc"],
            })
    elif not state.get("overall_status"):
        write_json_atomic(root / NOTIFY_STATE_FILE, {
            "fingerprint": issue_fingerprint(status),
            "overall_status": status["overall_status"],
            "updated_utc": status["checked_utc"],
        })

    print("FOOTBALL AI V3 — EXTERNAL SUPERVISOR")
    print("STATUS:", status["overall_status"])
    print("EXPECTED RUNNING:", "YES" if status["expected_running"] else "NO")
    print("HEARTBEAT AGE:", status.get("heartbeat_age_minutes", "-"), "minutes")
    print("TELEGRAM SENT:", "YES" if sent else "NO")
    print("FOOTBALL DATA API REQUESTS USED: 0")
    return status, event, sent


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Independent Football AI V3 supervisor")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check-only", action="store_true", help="Check without Telegram")
    actions.add_argument("--arm", action="store_true", help="Expect V3 to be running")
    actions.add_argument("--disarm", action="store_true", help="Mark a clean V3 stop")
    args = parser.parse_args()
    if args.arm:
        set_expected_running(True, root=root, reason="MANUAL_ARM")
        print("Supervisor armed.")
        return
    if args.disarm:
        set_expected_running(False, root=root, reason="MANUAL_DISARM")
        print("Supervisor in standby.")
        return
    run_once(root=root, notify=not args.check_only)


if __name__ == "__main__":
    main()
