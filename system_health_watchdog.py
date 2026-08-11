import csv
import json
import os
from datetime import datetime, timezone


MONITOR_STATE_FILE = "market_monitor_v2_state.json"
OUTPUT_FILE = "system_health_live.csv"
HISTORY_FILE = "system_health_history.csv"
NOTIFY_STATE_FILE = "system_health_notify_state.json"

HEARTBEAT_WARN_MINUTES = 8.0
HEARTBEAT_CRITICAL_MINUTES = 12.0
FIXTURE_WINDOW_MINUTES = 180.0

FIELDS = [
    "checked_utc", "overall_status", "severity", "component", "code",
    "fixture_id", "kickoff_utc", "minutes_to_kickoff", "home_team",
    "away_team", "age_minutes", "message",
]

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
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def write_json_atomic(path, value):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def read_csv_rows(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader) if reader.fieldnames else []


def write_csv_atomic(path, rows):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def append_csv(path, rows):
    if not rows:
        return
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    with open(path, "a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def issue(severity, component, code, message, fixture=None, age=None):
    fixture = fixture or {}
    return {
        "severity": severity,
        "component": component,
        "code": code,
        "fixture_id": clean(fixture.get("fixture_id")),
        "kickoff_utc": clean(fixture.get("kickoff")),
        "minutes_to_kickoff": clean(fixture.get("minutes_to_kickoff")),
        "home_team": clean(fixture.get("home_team")),
        "away_team": clean(fixture.get("away_team")),
        "age_minutes": "" if age is None else round(age, 2),
        "message": message,
    }


def heartbeat_time(state, state_mtime=None):
    health = state.get("health", {}) if state else {}
    value = (
        health.get("last_cycle_completed_utc")
        or health.get("last_cycle_started_utc")
        or (state or {}).get("last_fixture_refresh")
    )
    if value:
        return value
    if state_mtime is not None:
        return datetime.fromtimestamp(state_mtime, tz=timezone.utc).isoformat()
    return ""


def build_health(state, now=None, state_mtime=None):
    now = now or utc_now()
    issues = []
    active_count = 0

    if not state:
        issues.append(issue(
            "CRITICAL", "MARKET_MONITOR", "MONITOR_STATE_MISSING",
            "Market Monitor state is missing or unreadable",
        ))
    else:
        heartbeat = heartbeat_time(state, state_mtime)
        heartbeat_age = minutes_since(heartbeat, now)
        if heartbeat_age is None or heartbeat_age > HEARTBEAT_CRITICAL_MINUTES:
            issues.append(issue(
                "CRITICAL", "MARKET_MONITOR", "MONITOR_HEARTBEAT_STALE",
                "Market Monitor heartbeat is stale", age=heartbeat_age,
            ))
        elif heartbeat_age > HEARTBEAT_WARN_MINUTES:
            issues.append(issue(
                "DEGRADED", "MARKET_MONITOR", "MONITOR_HEARTBEAT_DELAYED",
                "Market Monitor heartbeat is delayed", age=heartbeat_age,
            ))

        monitor_health = state.get("health", {}) or {}
        consecutive = int(monitor_health.get("consecutive_errors") or 0)
        if clean(monitor_health.get("last_cycle_status")).upper() == "ERROR":
            level = "CRITICAL" if consecutive >= 3 else "DEGRADED"
            issues.append(issue(
                level, "MARKET_MONITOR", "MONITOR_CYCLE_ERROR",
                clean(monitor_health.get("last_error")) or "Market cycle error",
            ))

        lineup_results = state.get("lineup_results", {}) or {}
        odds_freshness = state.get("odds_freshness", {}) or {}
        for fixture_id, raw_fixture in (state.get("fixtures", {}) or {}).items():
            fixture = dict(raw_fixture or {})
            fixture["fixture_id"] = clean(fixture.get("fixture_id")) or clean(fixture_id)
            kickoff = parse_dt(fixture.get("kickoff"))
            if kickoff is None:
                continue
            minutes_to_kickoff = (kickoff - now).total_seconds() / 60.0
            fixture["minutes_to_kickoff"] = round(minutes_to_kickoff, 2)
            if not (0 < minutes_to_kickoff <= FIXTURE_WINDOW_MINUTES):
                continue
            active_count += 1
            lineup_seen = fixture["fixture_id"] in lineup_results
            odds = odds_freshness.get(fixture["fixture_id"], {}) or {}
            odds_age = minutes_since(odds.get("last_seen_utc"), now)

            if odds_age is None:
                level = "CRITICAL" if minutes_to_kickoff <= 60 else "DEGRADED"
                issues.append(issue(
                    level, "MARKET_ODDS", "ODDS_MISSING",
                    "No AH odds observation for an approaching fixture",
                    fixture=fixture,
                ))
            else:
                if lineup_seen:
                    limit = 12.0
                elif minutes_to_kickoff <= 90:
                    limit = 15.0
                else:
                    limit = 40.0
                if odds_age > limit:
                    level = (
                        "CRITICAL"
                        if lineup_seen or minutes_to_kickoff <= 60
                        else "DEGRADED"
                    )
                    code = "POST_XI_ODDS_STALE" if lineup_seen else "ODDS_STALE"
                    issues.append(issue(
                        level, "MARKET_ODDS", code,
                        f"AH odds have not refreshed inside {limit:.0f} minutes",
                        fixture=fixture, age=odds_age,
                    ))

            if not lineup_seen and minutes_to_kickoff <= 50:
                level = "CRITICAL" if minutes_to_kickoff <= 20 else "DEGRADED"
                issues.append(issue(
                    level, "LINEUPS", "CONFIRMED_XI_MISSING",
                    "Both confirmed starting lineups are not available",
                    fixture=fixture,
                ))

    overall = "HEALTHY"
    if issues:
        overall = max(issues, key=lambda row: SEVERITY[row["severity"]])["severity"]
    checked = now.isoformat()
    overall_row = {
        "checked_utc": checked,
        "overall_status": overall,
        "severity": overall,
        "component": "SYSTEM",
        "code": "SYSTEM_OK" if overall == "HEALTHY" else "SYSTEM_ISSUES",
        "fixture_id": "",
        "kickoff_utc": "",
        "minutes_to_kickoff": "",
        "home_team": "",
        "away_team": "",
        "age_minutes": "",
        "message": (
            f"Healthy; active fixtures inside 3h: {active_count}"
            if overall == "HEALTHY"
            else f"{len(issues)} health issue(s); active fixtures inside 3h: {active_count}"
        ),
    }
    rows = [overall_row]
    for row in issues:
        rows.append({"checked_utc": checked, "overall_status": overall, **row})
    return rows


def issue_fingerprint(rows):
    values = sorted(
        f"{row['severity']}:{row['code']}:{row['fixture_id']}"
        for row in rows[1:]
    )
    return "|".join(values)


def health_message(rows, recovered=False):
    overall = rows[0]
    if recovered:
        return "\n".join([
            "V3 HEALTH RECOVERED",
            "",
            overall["message"],
            "Market Monitor and live inputs are healthy again.",
        ])

    lines = [
        f"V3 HEALTH {overall['overall_status']}",
        "",
        overall["message"],
    ]
    for row in rows[1:6]:
        match = ""
        if row["home_team"] or row["away_team"]:
            match = f" | {row['home_team']} — {row['away_team']}"
        lines.append(f"- {row['code']}{match}: {row['message']}")
    if len(rows) > 6:
        lines.append(f"- plus {len(rows) - 6} more issue(s)")
    return "\n".join(lines)


def notification_event(rows, notify_state):
    status = rows[0]["overall_status"]
    fingerprint = issue_fingerprint(rows)
    previous_status = clean(notify_state.get("status")).upper()
    previous_fingerprint = clean(notify_state.get("fingerprint"))

    if status == "HEALTHY":
        if previous_status and previous_status != "HEALTHY":
            return {"message": health_message(rows, recovered=True), "status": status,
                    "fingerprint": fingerprint}
        return None
    if status != previous_status or fingerprint != previous_fingerprint:
        return {"message": health_message(rows), "status": status,
                "fingerprint": fingerprint}
    return None


def history_change(rows, previous):
    if not previous:
        return rows
    last_checked = clean(previous[-1].get("checked_utc"))
    last_rows = [
        row for row in previous
        if clean(row.get("checked_utc")) == last_checked
    ]
    previous_overall = last_rows[0].get("overall_status") if last_rows else ""
    previous_codes = sorted(
        f"{row.get('severity')}:{row.get('code')}:{row.get('fixture_id')}"
        for row in last_rows if row.get("code") != "SYSTEM_OK"
    )
    current_codes = sorted(
        f"{row.get('severity')}:{row.get('code')}:{row.get('fixture_id')}"
        for row in rows if row.get("code") != "SYSTEM_OK"
    )
    if previous_overall != rows[0]["overall_status"] or previous_codes != current_codes:
        return rows
    return []


def run_once(sender=None):
    now = utc_now()
    state = read_json(MONITOR_STATE_FILE)
    state_mtime = os.path.getmtime(MONITOR_STATE_FILE) if os.path.exists(MONITOR_STATE_FILE) else None
    rows = build_health(state, now=now, state_mtime=state_mtime)
    write_csv_atomic(OUTPUT_FILE, rows)
    append_csv(HISTORY_FILE, history_change(rows, read_csv_rows(HISTORY_FILE)))

    notify_state = read_json(NOTIFY_STATE_FILE) or {}
    event = notification_event(rows, notify_state)
    sent = 0
    if event:
        if sender is None:
            from telegram_notifier import send_telegram
            sender = send_telegram
        if sender(event["message"]):
            write_json_atomic(NOTIFY_STATE_FILE, {
                "status": event["status"],
                "fingerprint": event["fingerprint"],
            })
            sent = 1
    elif not notify_state:
        write_json_atomic(NOTIFY_STATE_FILE, {
            "status": rows[0]["overall_status"],
            "fingerprint": issue_fingerprint(rows),
        })

    print("Football AI V3 — Health Watchdog")
    print("Status:", rows[0]["overall_status"])
    print("Issues:", len(rows) - 1)
    print("Telegram alerts sent:", sent)
    print("API REQUESTS USED: 0")
    return rows, sent


if __name__ == "__main__":
    run_once()
