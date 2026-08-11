import csv
import hashlib
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

SNAPSHOT_FILE = "market_snapshots_v2.csv"
SIGNAL_FILE = "lineup_signals_live.csv"
AH_HISTORY_FILE = "ah_agent_v2_history.csv"
MASTER_HISTORY_FILE = "master_decisions_history.csv"

TIMELINE_FILE = "market_timeline_live.csv"
AUDIT_LEDGER_FILE = "signal_audit_ledger.csv"

TIMELINE_FIELDS = [
    "timeline_built_utc", "fixture_id", "kickoff_utc", "snapshot_utc",
    "minutes_to_kickoff", "home_team", "away_team", "phase",
    "is_opening", "is_last_pre_xi", "is_first_post_xi", "lineup_seen",
    "shock_diff", "signal", "data_quality", "provider_update_utc",
    "odds_fingerprint", "odds_changed_this_poll", "odds_last_change_utc",
    "freshness_status", "home_handicap", "home_avg_odds",
    "home_best_odds", "home_best_bookmaker", "home_bookmakers",
    "away_handicap", "away_avg_odds", "away_best_odds",
    "away_best_bookmaker", "away_bookmakers",
]

AUDIT_FIELDS = [
    "ledger_written_utc", "event_hash", "event_type", "event_time_utc",
    "fixture_id", "kickoff_utc", "home_team", "away_team", "signal",
    "shock_diff", "data_quality", "ah_decision", "master_decision",
    "handicap", "odds", "bookmaker", "reason", "source_file",
]


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def safe_float(value):
    try:
        x = float(value)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except Exception:
        return None


def truthy(value):
    return clean(value).lower() in {"1", "true", "yes", "y"}


def read_csv_rows(filename):
    if not os.path.exists(filename) or os.path.getsize(filename) == 0:
        return []
    with open(filename, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader) if reader.fieldnames else []


def write_csv_atomic(filename, fields, rows):
    temp = filename + ".tmp"
    with open(temp, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, filename)


def append_csv(filename, fields, rows):
    if not rows:
        return
    exists = os.path.exists(filename) and os.path.getsize(filename) > 0
    with open(filename, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def consensus(rows, side):
    candidates = []
    for row in rows:
        if clean(row.get("parsed_side")).upper() != side:
            continue
        handicap = safe_float(row.get("parsed_handicap"))
        odd = safe_float(row.get("odd"))
        if handicap is None or odd is None or odd <= 1.0:
            continue
        candidates.append((handicap, odd, clean(row.get("bookmaker"))))

    if not candidates:
        return None

    counts = defaultdict(int)
    for handicap, _, _ in candidates:
        counts[handicap] += 1
    max_count = max(counts.values())
    lines = [line for line, count in counts.items() if count == max_count]

    all_lines = sorted(x[0] for x in candidates)
    if len(all_lines) % 2:
        median = all_lines[len(all_lines) // 2]
    else:
        median = (
            all_lines[len(all_lines) // 2 - 1]
            + all_lines[len(all_lines) // 2]
        ) / 2.0

    line = min(lines, key=lambda x: abs(x - median))
    same = [(odd, book) for h, odd, book in candidates if h == line]
    avg_odds = sum(x[0] for x in same) / len(same)
    best_odds, best_book = max(same, key=lambda x: x[0])

    return {
        "handicap": line,
        "avg_odds": avg_odds,
        "best_odds": best_odds,
        "best_bookmaker": best_book,
        "bookmakers": len(same),
    }


def freshness_status(rows):
    row = rows[0]
    lineup_seen = truthy(row.get("lineup_seen"))
    changed = truthy(row.get("odds_changed_this_poll"))
    provider_update = clean(row.get("provider_update_utc"))

    if not lineup_seen:
        return "PRE_XI_BASELINE"
    if changed:
        return "POST_XI_CHANGED"
    if provider_update:
        return "POST_XI_PROVIDER_TIMESTAMP"
    return "POST_XI_UNCHANGED_OR_UNPROVEN"


def build_market_timeline(snapshot_rows):
    grouped = defaultdict(list)
    for row in snapshot_rows:
        fixture_id = clean(row.get("fixture_id"))
        snapshot_utc = clean(row.get("snapshot_utc"))
        if fixture_id and snapshot_utc:
            grouped[(fixture_id, snapshot_utc)].append(row)

    fixture_times = defaultdict(list)
    for fixture_id, snapshot_utc in grouped:
        fixture_times[fixture_id].append(snapshot_utc)
    for fixture_id in fixture_times:
        fixture_times[fixture_id] = sorted(set(fixture_times[fixture_id]))

    built = utc_now_iso()
    output = []

    for fixture_id, times in fixture_times.items():
        first_time = times[0]
        pre_times = []
        post_times = []

        for ts in times:
            rows = grouped[(fixture_id, ts)]
            if truthy(rows[0].get("lineup_seen")):
                post_times.append(ts)
            else:
                pre_times.append(ts)

        last_pre = pre_times[-1] if pre_times else None
        first_post = post_times[0] if post_times else None

        for ts in times:
            rows = grouped[(fixture_id, ts)]
            first = rows[0]
            home = consensus(rows, "HOME")
            away = consensus(rows, "AWAY")
            lineup_seen = truthy(first.get("lineup_seen"))

            out = {
                "timeline_built_utc": built,
                "fixture_id": fixture_id,
                "kickoff_utc": clean(first.get("kickoff_utc")),
                "snapshot_utc": ts,
                "minutes_to_kickoff": clean(first.get("minutes_to_kickoff")),
                "home_team": clean(first.get("home_team")),
                "away_team": clean(first.get("away_team")),
                "phase": "POST_XI" if lineup_seen else "PRE_XI",
                "is_opening": int(ts == first_time),
                "is_last_pre_xi": int(ts == last_pre),
                "is_first_post_xi": int(ts == first_post),
                "lineup_seen": int(lineup_seen),
                "shock_diff": clean(first.get("shock_diff")),
                "signal": clean(first.get("signal")),
                "data_quality": clean(first.get("data_quality")),
                "provider_update_utc": clean(first.get("provider_update_utc")),
                "odds_fingerprint": clean(first.get("odds_fingerprint")),
                "odds_changed_this_poll": int(
                    truthy(first.get("odds_changed_this_poll"))
                ),
                "odds_last_change_utc": clean(first.get("odds_last_change_utc")),
                "freshness_status": freshness_status(rows),
            }

            for prefix, market in (("home", home), ("away", away)):
                out[f"{prefix}_handicap"] = (
                    market["handicap"] if market else ""
                )
                out[f"{prefix}_avg_odds"] = (
                    round(market["avg_odds"], 6) if market else ""
                )
                out[f"{prefix}_best_odds"] = (
                    market["best_odds"] if market else ""
                )
                out[f"{prefix}_best_bookmaker"] = (
                    market["best_bookmaker"] if market else ""
                )
                out[f"{prefix}_bookmakers"] = (
                    market["bookmakers"] if market else 0
                )

            output.append(out)

    return output


def canonical_hash(event):
    payload = {
        key: clean(value)
        for key, value in event.items()
        if key not in {"ledger_written_utc", "event_hash"}
    }
    raw = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_event(event_type, row, source_file):
    event_time = (
        row.get("decision_time_utc")
        or row.get("master_time_utc")
        or row.get("signal_time_utc")
    )

    event = {
        "event_type": event_type,
        "event_time_utc": clean(event_time),
        "fixture_id": clean(row.get("fixture_id")),
        "kickoff_utc": clean(row.get("kickoff_utc")),
        "home_team": clean(row.get("home_team")),
        "away_team": clean(row.get("away_team")),
        "signal": clean(row.get("signal") or row.get("primary_side")),
        "shock_diff": clean(row.get("shock_diff")),
        "data_quality": clean(row.get("data_quality")),
        "ah_decision": clean(
            row.get("ah_decision")
            or (row.get("decision") if event_type == "AH_DECISION" else "")
        ),
        "master_decision": clean(
            row.get("master_decision")
            or (row.get("decision") if event_type == "MASTER_DECISION" else "")
        ),
        "handicap": clean(
            row.get("ah_handicap")
            or row.get("current_handicap")
            or row.get("entry_handicap")
        ),
        "odds": clean(
            row.get("ah_best_odds")
            or row.get("current_best_odds")
            or row.get("entry_best_odds")
        ),
        "bookmaker": clean(
            row.get("ah_best_bookmaker")
            or row.get("current_best_bookmaker")
            or row.get("entry_best_bookmaker")
        ),
        "reason": clean(row.get("reason") or row.get("master_reason")),
        "source_file": source_file,
    }

    event["event_hash"] = canonical_hash(event)
    event["ledger_written_utc"] = utc_now_iso()
    return event


def collect_audit_events():
    events = []

    for row in read_csv_rows(SIGNAL_FILE):
        events.append(make_event("LINEUP_SIGNAL", row, SIGNAL_FILE))

    for row in read_csv_rows(AH_HISTORY_FILE):
        events.append(make_event("AH_DECISION", row, AH_HISTORY_FILE))

    for row in read_csv_rows(MASTER_HISTORY_FILE):
        events.append(make_event("MASTER_DECISION", row, MASTER_HISTORY_FILE))

    return events


def append_new_audit_events(events):
    existing = {
        clean(row.get("event_hash"))
        for row in read_csv_rows(AUDIT_LEDGER_FILE)
    }
    new_rows = [
        event for event in events
        if event["event_hash"] not in existing
    ]
    append_csv(AUDIT_LEDGER_FILE, AUDIT_FIELDS, new_rows)
    return len(new_rows)


def run_once():
    snapshots = read_csv_rows(SNAPSHOT_FILE)
    timeline = build_market_timeline(snapshots)

    if timeline:
        write_csv_atomic(TIMELINE_FILE, TIMELINE_FIELDS, timeline)
    elif not os.path.exists(TIMELINE_FILE):
        write_csv_atomic(TIMELINE_FILE, TIMELINE_FIELDS, [])

    added = append_new_audit_events(collect_audit_events())

    print("Market timeline snapshots:", len(timeline))
    print("New immutable audit events:", added)
    print("Timeline:", TIMELINE_FILE)
    print("Audit ledger:", AUDIT_LEDGER_FILE)


if __name__ == "__main__":
    run_once()
