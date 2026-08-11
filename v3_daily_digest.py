import argparse
import csv
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import v3_forward_test_scorecard as forward


STATE_FILE = "v3_daily_digest_state.json"
STATUS_FILE = "v3_daily_digest_status.json"

READINESS_FILE = "v3_readiness_summary.json"
MONITOR_STATE_FILE = "market_monitor_v2_state.json"
HEALTH_FILE = "system_health_live.csv"
GATE_HISTORY_FILE = "shadow_value_gate_history.csv"
FORWARD_FILE = "v3_forward_test_summary.json"
RISK_FILE = "v3_shadow_risk_summary.json"
BACKUP_FILE = "v3_backup_guard_status.json"
SUPERVISOR_FILE = "v3_external_supervisor_status.json"

DEFAULT_REPORT_HOUR = 10
DEFAULT_UTC_OFFSET = 5.0
REPORT_HOUR_ENV = "FOOTBALL_AI_DAILY_REPORT_HOUR"
UTC_OFFSET_ENV = "FOOTBALL_AI_REPORT_UTC_OFFSET"


def utc_now():
    return datetime.now(timezone.utc)


def clean(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


def safe_int(value):
    try:
        return int(float(value))
    except Exception:
        return 0


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


def read_csv_rows(path):
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            return list(reader) if reader.fieldnames else []
    except Exception:
        return []


def read_env_file(path):
    values = {}
    path = Path(path)
    if not path.exists():
        return values
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except Exception:
        return {}
    return values


def effective_env(root, environ=None):
    values = read_env_file(Path(root) / ".env")
    values.update(dict(os.environ if environ is None else environ))
    return values


def schedule_config(env):
    raw_hour = safe_float(env.get(REPORT_HOUR_ENV, DEFAULT_REPORT_HOUR))
    if (
        raw_hour is None
        or not raw_hour.is_integer()
        or not 0 <= raw_hour <= 23
    ):
        hour = DEFAULT_REPORT_HOUR
    else:
        hour = int(raw_hour)
    offset = safe_float(env.get(UTC_OFFSET_ENV, DEFAULT_UTC_OFFSET))
    if offset is None or not -12 <= offset <= 14:
        offset = DEFAULT_UTC_OFFSET
    tz = timezone(timedelta(hours=offset))
    return hour, offset, tz


def is_due(now, state, hour, tz, force=False):
    local = now.astimezone(tz)
    local_date = local.date().isoformat()
    if force:
        return True, local, "FORCED"
    if state.get("last_sent_local_date") == local_date:
        return False, local, "ALREADY_SENT"
    if local.hour < hour:
        return False, local, "BEFORE_SCHEDULE"
    return True, local, "DUE"


def latest_gate_rows(rows):
    latest = {}
    for row in rows:
        fixture_id = clean(row.get("fixture_id"))
        when = forward.parse_dt(row.get("gate_time_utc"))
        if not fixture_id or when is None:
            continue
        previous = latest.get(fixture_id)
        if previous is None or when > previous[0]:
            latest[fixture_id] = (when, row)
    return latest


def fixture_metrics(state, now):
    upcoming = []
    for fixture_id, raw in (state.get("fixtures", {}) or {}).items():
        fixture = dict(raw or {})
        kickoff = forward.parse_dt(fixture.get("kickoff"))
        if kickoff is None:
            continue
        minutes = (kickoff - now).total_seconds() / 60.0
        if 0 <= minutes <= 48 * 60:
            upcoming.append((minutes, str(fixture_id), fixture, kickoff))
    upcoming.sort(key=lambda item: item[0])
    if not upcoming:
        return 0, None
    minutes, fixture_id, fixture, kickoff = upcoming[0]
    return len(upcoming), {
        "fixture_id": clean(fixture.get("fixture_id")) or fixture_id,
        "home_team": clean(fixture.get("home_team")),
        "away_team": clean(fixture.get("away_team")),
        "kickoff_utc": kickoff.isoformat(),
        "minutes_to_kickoff": round(minutes, 1),
    }


def build_metrics(root, now, tz):
    root = Path(root)
    readiness = read_json(root / READINESS_FILE)
    monitor = read_json(root / MONITOR_STATE_FILE)
    health_rows = read_csv_rows(root / HEALTH_FILE)
    gate_history = read_csv_rows(root / GATE_HISTORY_FILE)
    forward_summary = read_json(root / FORWARD_FILE)
    risk = read_json(root / RISK_FILE)
    backup = read_json(root / BACKUP_FILE)
    supervisor = read_json(root / SUPERVISOR_FILE)
    local_date = now.astimezone(tz).date()

    first_bets = forward.first_shadow_bets(gate_history)
    shadow_today = 0
    for row in first_bets.values():
        when = forward.parse_dt(row.get("gate_time_utc"))
        if when is not None and when.astimezone(tz).date() == local_date:
            shadow_today += 1

    latest = latest_gate_rows(gate_history)
    decision_today = {"WATCH": 0, "PASS": 0}
    for when, row in latest.values():
        if when.astimezone(tz).date() != local_date:
            continue
        decision = clean(row.get("gate_decision")).upper()
        if decision in decision_today:
            decision_today[decision] += 1

    fixtures_48h, nearest = fixture_metrics(monitor, now)
    health = "UNKNOWN"
    if health_rows:
        health = clean(
            health_rows[0].get("overall_status")
            or health_rows[0].get("severity")
        ).upper() or "UNKNOWN"

    funnel = forward_summary.get("funnel", {}) or {}
    evidence = forward_summary.get("evidence", {}) or {}
    realized = risk.get("realized", {}) or {}
    return {
        "readiness": clean(readiness.get("overall_status")).upper() or "UNKNOWN",
        "health": health,
        "supervisor": clean(supervisor.get("overall_status")).upper() or "UNKNOWN",
        "backup": clean(backup.get("overall_status")).upper() or "UNKNOWN",
        "backup_local": clean((backup.get("local", {}) or {}).get("status")) or "UNKNOWN",
        "backup_mirror": clean((backup.get("mirror", {}) or {}).get("status")) or "UNKNOWN",
        "fixtures_48h": fixtures_48h,
        "nearest_fixture": nearest,
        "shadow_bets_today": shadow_today,
        "watch_today": decision_today["WATCH"],
        "pass_today": decision_today["PASS"],
        "forward_status": clean(forward_summary.get("status")).upper() or "NOT_STARTED",
        "with_clv": safe_int(funnel.get("with_clv")),
        "settled": safe_int(funnel.get("settled")),
        "avg_line_clv": evidence.get("avg_line_clv", ""),
        "roi": evidence.get("roi", ""),
        "risk_status": clean(risk.get("status")).upper() or "LOCKED_BY_FORWARD_TEST",
        "risk_roi": realized.get("roi", ""),
        "risk_drawdown": realized.get("max_drawdown_fraction", ""),
    }


def signed(value, decimals=3):
    number = safe_float(value)
    return "-" if number is None else f"{number:+.{decimals}f}"


def percent(value):
    number = safe_float(value)
    return "-" if number is None else f"{number:+.1%}"


def build_message(metrics, local, offset):
    nearest = metrics.get("nearest_fixture")
    if nearest:
        match = (
            f"{nearest['home_team']} — {nearest['away_team']} "
            f"через {nearest['minutes_to_kickoff']:.0f} мин"
        )
    else:
        match = "нет матчей в ближайшие 48 часов"
    sign = "+" if offset >= 0 else ""
    return "\n".join([
        "FOOTBALL AI V3 — ЕЖЕДНЕВНЫЙ ОТЧЁТ",
        f"{local:%d.%m.%Y %H:%M} (UTC{sign}{offset:g})",
        "",
        f"Система: {metrics['readiness']} | Health: {metrics['health']}",
        f"Supervisor: {metrics['supervisor']}",
        (
            f"Backup: {metrics['backup']} | local {metrics['backup_local']} | "
            f"mirror {metrics['backup_mirror']}"
        ),
        "",
        f"Матчи ≤48ч: {metrics['fixtures_48h']} | {match}",
        (
            f"Сегодня: Shadow Bet {metrics['shadow_bets_today']} | "
            f"Watch {metrics['watch_today']} | Pass {metrics['pass_today']}"
        ),
        "",
        (
            f"Forward: {metrics['forward_status']} | CLV {metrics['with_clv']}/50 "
            f"({signed(metrics['avg_line_clv'])}) | settled {metrics['settled']}/100 "
            f"| ROI {percent(metrics['roi'])}"
        ),
        (
            f"Risk: {metrics['risk_status']} | ROI {percent(metrics['risk_roi'])} "
            f"| max DD {percent(metrics['risk_drawdown'])}"
        ),
        "",
        "SHADOW ONLY — реальных ставок V3 не размещает",
        "Football-data API requests used by digest: 0",
    ])


def run_once(
    root=".", sender=None, force=False, preview=False, now=None, environ=None
):
    root = Path(root)
    now = now or utc_now()
    env = effective_env(root, environ)
    hour, offset, tz = schedule_config(env)
    state = read_json(root / STATE_FILE)
    due, local, due_reason = is_due(now, state, hour, tz, force=force)
    metrics = build_metrics(root, now, tz)
    message = build_message(metrics, local, offset)
    sent = False

    if preview:
        status_name = "PREVIEW"
    elif not due:
        status_name = "NOT_DUE"
    else:
        if sender is None:
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                from telegram_notifier import send_telegram
                sent = bool(send_telegram(message))
            finally:
                os.chdir(old_cwd)
        else:
            sent = bool(sender(message))
        status_name = "SENT" if sent else "SEND_FAILED"
        if sent:
            write_json_atomic(root / STATE_FILE, {
                "last_sent_local_date": local.date().isoformat(),
                "last_sent_utc": now.isoformat(),
                "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
            })

    status = {
        "checked_utc": now.isoformat(),
        "status": status_name,
        "due_reason": due_reason,
        "scheduled_local_hour": hour,
        "utc_offset": offset,
        "local_time": local.isoformat(),
        "sent": sent,
        "metrics": metrics,
        "football_data_api_requests_used": 0,
    }
    write_json_atomic(root / STATUS_FILE, status)
    print("\n" + "=" * 72)
    print("FOOTBALL AI V3 — DAILY DIGEST")
    print("=" * 72)
    print("STATUS:", status_name)
    print("REASON:", due_reason)
    print("LOCAL TIME:", local.isoformat())
    print("SCHEDULED HOUR:", hour)
    print("TELEGRAM SENT:", "YES" if sent else "NO")
    print("FOOTBALL DATA API REQUESTS USED: 0")
    if preview:
        print("\n" + message)
    print("=" * 72)
    return status, message


def main():
    parser = argparse.ArgumentParser(description="Football AI V3 daily Telegram digest")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--force", action="store_true", help="Send now and mark today sent")
    actions.add_argument("--preview", action="store_true", help="Print without sending")
    args = parser.parse_args()
    run_once(force=args.force, preview=args.preview)


if __name__ == "__main__":
    main()
