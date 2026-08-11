import csv
import json
import os


GATE_FILE = "shadow_value_gate_live.csv"
SUMMARY_FILE = "shadow_value_gate_outcome_summary.csv"
STATE_FILE = "shadow_value_gate_notify_state.json"

MILESTONES = (10, 25, 50, 75, 100, 150, 200, 300, 500)


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


def read_csv_rows(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader) if reader.fieldnames else []


def default_state():
    return {"fixtures": {}, "summary": {}}


def load_state(path=STATE_FILE):
    if not os.path.exists(path):
        return default_state()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        state = default_state()
        if isinstance(loaded, dict):
            if isinstance(loaded.get("fixtures"), dict):
                state["fixtures"] = loaded["fixtures"]
            if isinstance(loaded.get("summary"), dict):
                state["summary"] = loaded["summary"]
        return state
    except Exception:
        return default_state()


def save_state(state, path=STATE_FILE):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def signed(value, decimals=2):
    number = safe_float(value)
    return "-" if number is None else f"{number:+.{decimals}f}"


def decimal(value, decimals=3):
    number = safe_float(value)
    return "-" if number is None else f"{number:.{decimals}f}"


def percent(value):
    number = safe_float(value)
    return "-" if number is None else f"{number:+.1%}"


def signal_team(row):
    signal = clean(row.get("signal")).upper()
    if signal == "HOME":
        return clean(row.get("home_team"))
    if signal == "AWAY":
        return clean(row.get("away_team"))
    return signal or "-"


def gate_message(row, reactivated=False):
    title = "V3 SHADOW BET REACTIVATED" if reactivated else "V3 SHADOW BET"
    return "\n".join([
        title,
        "",
        f"{clean(row.get('home_team'))} — {clean(row.get('away_team'))}",
        f"Side: {signal_team(row)} ({clean(row.get('signal')).upper()})",
        f"AH: {signed(row.get('entry_handicap'))} @ {decimal(row.get('entry_best_odds'))}",
        f"Bookmaker: {clean(row.get('entry_best_bookmaker')) or '-'}",
        f"Shock: {decimal(row.get('abs_shock'), 2)} | {clean(row.get('data_quality'))}",
        f"Freshness: {clean(row.get('market_freshness'))} | books {clean(row.get('market_bookmakers')) or '0'}",
        f"Manager: {clean(row.get('new_manager_context')) or 'UNAVAILABLE'}",
        f"Matchup: {clean(row.get('matchup_context')) or 'UNAVAILABLE'}",
        f"T-minus: {decimal(row.get('minutes_to_kickoff'), 1)} min",
        "",
        "SHADOW ONLY — not a production bet",
    ])


def cancellation_message(row):
    return "\n".join([
        "V3 SHADOW BET CANCELLED",
        "",
        f"{clean(row.get('home_team'))} — {clean(row.get('away_team'))}",
        f"Previous side: {signal_team(row)}",
        f"New gate status: {clean(row.get('gate_decision')).upper()}",
        f"Freshness: {clean(row.get('market_freshness'))}",
        f"Reason: {clean(row.get('reason'))}",
        "",
        "Do not treat the earlier shadow signal as active.",
    ])


def gate_events(rows, state):
    events = []
    for row in rows:
        fixture_id = clean(row.get("fixture_id"))
        if not fixture_id:
            continue
        decision = clean(row.get("gate_decision")).upper()
        previous = state["fixtures"].get(fixture_id, {})
        previous_decision = clean(previous.get("decision")).upper()

        if decision == "SHADOW BET" and previous_decision != "SHADOW BET":
            events.append({
                "kind": "GATE",
                "message": gate_message(
                    row, reactivated=bool(previous.get("ever_alerted"))
                ),
                "fixture_id": fixture_id,
                "new_decision": decision,
            })
        elif decision != "SHADOW BET" and previous_decision == "SHADOW BET":
            events.append({
                "kind": "GATE",
                "message": cancellation_message(row),
                "fixture_id": fixture_id,
                "new_decision": decision,
            })
        elif not previous:
            state["fixtures"][fixture_id] = {
                "decision": decision,
                "ever_alerted": False,
            }
    return events


def reached_milestone(value):
    reached = [milestone for milestone in MILESTONES if value >= milestone]
    return max(reached) if reached else 0


def overall_summary(rows):
    for row in rows:
        if clean(row.get("scope")).upper() == "ALL":
            return row
    return None


def summary_message(row):
    return "\n".join([
        "V3 VALUE GATE — VALIDATION UPDATE",
        "",
        f"Candidates: {safe_int(row.get('candidates'))}",
        f"With closing CLV: {safe_int(row.get('with_clv'))}",
        f"Average line CLV: {signed(row.get('avg_line_clv'), 3)}",
        (
            "CLV 95% CI: "
            f"[{signed(row.get('line_clv_ci_low'), 3)}, "
            f"{signed(row.get('line_clv_ci_high'), 3)}]"
        ),
        f"Settled: {safe_int(row.get('settled'))}",
        f"Profit: {signed(row.get('profit_units'), 2)}u",
        f"ROI: {percent(row.get('roi'))}",
        f"Status: {clean(row.get('promotion_status'))}",
        "",
        "SHADOW ONLY",
    ])


def summary_events(rows, state):
    row = overall_summary(rows)
    if not row:
        return []

    previous = state.get("summary", {})
    status = clean(row.get("promotion_status")).upper()
    clv_milestone = reached_milestone(safe_int(row.get("with_clv")))
    settled_milestone = reached_milestone(safe_int(row.get("settled")))
    previous_status = clean(previous.get("status")).upper()
    important_status_change = (
        bool(previous_status)
        and status != previous_status
    )
    new_milestone = (
        clv_milestone > safe_int(previous.get("clv_milestone"))
        or settled_milestone > safe_int(previous.get("settled_milestone"))
    )

    if not important_status_change and not new_milestone:
        if not previous:
            state["summary"] = {
                "status": status,
                "clv_milestone": clv_milestone,
                "settled_milestone": settled_milestone,
            }
        return []

    return [{
        "kind": "SUMMARY",
        "message": summary_message(row),
        "status": status,
        "clv_milestone": clv_milestone,
        "settled_milestone": settled_milestone,
    }]


def apply_success(state, event):
    if event["kind"] == "GATE":
        previous = state["fixtures"].get(event["fixture_id"], {})
        state["fixtures"][event["fixture_id"]] = {
            "decision": event["new_decision"],
            "ever_alerted": (
                bool(previous.get("ever_alerted"))
                or event["new_decision"] == "SHADOW BET"
            ),
        }
    elif event["kind"] == "SUMMARY":
        state["summary"] = {
            "status": event["status"],
            "clv_milestone": event["clv_milestone"],
            "settled_milestone": event["settled_milestone"],
        }


def run_once(sender=None):
    if sender is None:
        from telegram_notifier import send_telegram
        sender = send_telegram
    state = load_state()
    events = gate_events(read_csv_rows(GATE_FILE), state)
    events.extend(summary_events(read_csv_rows(SUMMARY_FILE), state))

    sent = 0
    for event in events:
        if sender(event["message"]):
            apply_success(state, event)
            save_state(state)
            sent += 1

    # Persist silent initial WATCH/PASS and empty summary baselines.
    save_state(state)
    print("Shadow Value Gate Telegram Monitor")
    print("Events found:", len(events))
    print("Messages sent:", sent)
    print("API REQUESTS USED: 0 football-data calls")
    return events, sent


if __name__ == "__main__":
    run_once()
