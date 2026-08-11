import csv
import math
import os
from datetime import datetime, timezone


AH_FILE = "ah_agent_v2_latest.csv"
TIMELINE_FILE = "market_timeline_live.csv"
COACH_FILE = "coach_context_live.csv"
MATCHUP_FILE = "epl_matchup_context.csv"

OUTPUT_FILE = "shadow_value_gate_live.csv"
HISTORY_FILE = "shadow_value_gate_history.csv"

MAX_MARKET_AGE_MINUTES = 12.0
MIN_BOOKMAKERS = 2

# Step 19 out-of-sample robustness result. This is a population prior, not a
# per-fixture probability: HIGH-quality Lineup Shock rows produced positive
# signed close movement. MEDIUM quality is deliberately excluded from BET.
HIGH_PRIOR_N = 125
HIGH_PRIOR_SIGNED_MOVE = 0.0980
HIGH_PRIOR_LARGE_HIT_RATE = 0.836

OUTPUT_FIELDS = [
    "gate_time_utc", "fixture_id", "kickoff_utc", "minutes_to_kickoff",
    "home_team", "away_team", "signal", "abs_shock", "shock_band",
    "data_quality", "ah_decision", "directional_clv_prior",
    "directional_clv_prior_n", "directional_clv_prior_signed_move",
    "directional_clv_prior_large_hit_rate", "market_snapshot_utc",
    "market_age_minutes", "freshness_status", "market_freshness",
    "market_bookmakers", "new_manager_context", "new_manager_score",
    "matchup_context", "matchup_score", "context_score",
    "gate_decision", "reason", "shadow_only",
]


def utc_now():
    return datetime.now(timezone.utc)


def clean(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def safe_float(value):
    try:
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    except Exception:
        return None


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


def read_csv_rows(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader) if reader.fieldnames else []


def write_csv_atomic(path, fields, rows):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def append_csv(path, fields, rows):
    if not rows:
        return
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    with open(path, "a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def shock_band(abs_shock):
    if abs_shock is None or abs_shock < 1.5:
        return "BELOW_THRESHOLD"
    if abs_shock < 2.0:
        return "ROBUST_1.5_2.0"
    if abs_shock < 2.5:
        return "UNSTABLE_2.0_2.5"
    return "EXTREME_2.5_PLUS_SMALL_SAMPLE"


def latest_timeline_by_fixture(rows):
    latest = {}
    for row in rows:
        fixture_id = clean(row.get("fixture_id"))
        snapshot = parse_dt(row.get("snapshot_utc"))
        if not fixture_id or snapshot is None:
            continue
        previous = latest.get(fixture_id)
        if previous is None or snapshot > previous[0]:
            latest[fixture_id] = (snapshot, row)
    return {fixture_id: item[1] for fixture_id, item in latest.items()}


def market_freshness(timeline_row, signal, now):
    if not timeline_row:
        return "MISSING", "", None, 0

    snapshot = parse_dt(timeline_row.get("snapshot_utc"))
    age = (now - snapshot).total_seconds() / 60.0 if snapshot else None
    status = clean(timeline_row.get("freshness_status")).upper()
    phase = clean(timeline_row.get("phase")).upper()
    side = clean(signal).lower()
    bookmakers = int(safe_float(timeline_row.get(f"{side}_bookmakers")) or 0)

    if phase != "POST_XI":
        verdict = "PRE_XI"
    elif age is None or age < -1 or age > MAX_MARKET_AGE_MINUTES:
        verdict = "STALE"
    elif bookmakers < MIN_BOOKMAKERS:
        verdict = "THIN"
    elif status not in {"POST_XI_CHANGED", "POST_XI_PROVIDER_TIMESTAMP"}:
        verdict = "UNPROVEN"
    else:
        verdict = "FRESH"

    return verdict, status, age, bookmakers


def new_manager_context(rows, fixture_id, signal, home_team, away_team):
    fixture_rows = [r for r in rows if clean(r.get("fixture_id")) == fixture_id]
    if not fixture_rows:
        return "UNAVAILABLE", 0

    signal_team = home_team if signal == "HOME" else away_team
    opponent = away_team if signal == "HOME" else home_team
    score = 0
    notes = []
    for row in fixture_rows:
        strength = clean(row.get("shadow_strength")).upper()
        if strength not in {"STRONG", "MODERATE"}:
            continue
        team = clean(row.get("team"))
        number = clean(row.get("new_manager_match_number"))
        if team == signal_team:
            score += 1
            notes.append(f"supports signal: {team} new manager match {number}")
        elif team == opponent:
            score -= 1
            notes.append(f"opposes signal: {team} new manager match {number}")

    return ("; ".join(notes) if notes else "NEUTRAL"), max(-1, min(1, score))


def latest_prior_matchup(rows, home_team, away_team, kickoff):
    candidates = []
    target_pair = {home_team, away_team}
    for row in rows:
        if {clean(row.get("home_team")), clean(row.get("away_team"))} != target_pair:
            continue
        row_date = parse_dt(row.get("date"))
        if kickoff and row_date and row_date >= kickoff:
            continue
        candidates.append((row_date or datetime.min.replace(tzinfo=timezone.utc), row))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def matchup_context(rows, home_team, away_team, kickoff, signal):
    row = latest_prior_matchup(rows, home_team, away_team, kickoff)
    if not row:
        return "UNAVAILABLE", 0

    historical_home = clean(row.get("home_team"))
    edge = safe_float(row.get("matchup_xg_balance_edge_home"))
    if edge is None:
        return "PRIOR_PAIR_NO_STYLE_EDGE", 0

    # Re-orient the historical home edge to today's home team.
    today_home_edge = edge if historical_home == home_team else -edge
    signal_edge = today_home_edge if signal == "HOME" else -today_home_edge
    if signal_edge >= 0.20:
        return f"SUPPORT xG-balance edge {signal_edge:+.2f}", 1
    if signal_edge <= -0.20:
        return f"CONFLICT xG-balance edge {signal_edge:+.2f}", -1
    return f"NEUTRAL xG-balance edge {signal_edge:+.2f}", 0


def decide_gate(ah_row, freshness, band, manager_score, matchup_score):
    ah_decision = clean(ah_row.get("decision")).upper()
    quality = clean(ah_row.get("data_quality")).upper()
    minutes = safe_float(ah_row.get("minutes_to_kickoff"))

    if minutes is not None and minutes <= 0:
        return "PASS", "Kickoff reached or passed"
    if ah_decision in {"NO SIGNAL", "NO BET", "LATE", ""}:
        return "PASS", f"AH Agent={ah_decision or 'MISSING'}"
    if ah_decision != "BET":
        return "WATCH", f"AH Agent={ah_decision}"
    if quality != "HIGH":
        return "WATCH", "Only HIGH lineup quality is eligible after Step 19"
    if band == "UNSTABLE_2.0_2.5":
        return "WATCH", "Shock 2.0-2.5 band was unstable in robustness research"
    if freshness != "FRESH":
        return "WATCH", f"Market freshness={freshness}"
    if manager_score + matchup_score <= -2:
        return "WATCH", "New-manager and matchup contexts both conflict"
    return "SHADOW BET", "HIGH Lineup Shock + robust directional prior + fresh tradeable AH"


def build_gate_rows(ah_rows, timeline_rows, coach_rows, matchup_rows, now=None):
    now = now or utc_now()
    timelines = latest_timeline_by_fixture(timeline_rows)
    output = []

    for ah in ah_rows:
        fixture_id = clean(ah.get("fixture_id"))
        signal = clean(ah.get("signal")).upper()
        home = clean(ah.get("home_team"))
        away = clean(ah.get("away_team"))
        kickoff = parse_dt(ah.get("kickoff_utc"))
        abs_value = safe_float(ah.get("abs_shock"))
        band = shock_band(abs_value)
        fresh, status, age, books = market_freshness(
            timelines.get(fixture_id), signal, now
        )
        manager_text, manager_score = new_manager_context(
            coach_rows, fixture_id, signal, home, away
        )
        matchup_text, matchup_score = matchup_context(
            matchup_rows, home, away, kickoff, signal
        )
        decision, reason = decide_gate(
            ah, fresh, band, manager_score, matchup_score
        )

        output.append({
            "gate_time_utc": now.isoformat(),
            "fixture_id": fixture_id,
            "kickoff_utc": clean(ah.get("kickoff_utc")),
            "minutes_to_kickoff": clean(ah.get("minutes_to_kickoff")),
            "home_team": home,
            "away_team": away,
            "signal": signal,
            "abs_shock": "" if abs_value is None else abs_value,
            "shock_band": band,
            "data_quality": clean(ah.get("data_quality")).upper(),
            "ah_decision": clean(ah.get("decision")).upper(),
            "directional_clv_prior": "ROBUST_HIGH_ONLY",
            "directional_clv_prior_n": HIGH_PRIOR_N,
            "directional_clv_prior_signed_move": HIGH_PRIOR_SIGNED_MOVE,
            "directional_clv_prior_large_hit_rate": HIGH_PRIOR_LARGE_HIT_RATE,
            "market_snapshot_utc": clean((timelines.get(fixture_id) or {}).get("snapshot_utc")),
            "market_age_minutes": "" if age is None else round(age, 3),
            "freshness_status": status,
            "market_freshness": fresh,
            "market_bookmakers": books,
            "new_manager_context": manager_text,
            "new_manager_score": manager_score,
            "matchup_context": matchup_text,
            "matchup_score": matchup_score,
            "context_score": manager_score + matchup_score,
            "gate_decision": decision,
            "reason": reason,
            "shadow_only": 1,
        })

    return sorted(output, key=lambda row: (row["kickoff_utc"], row["fixture_id"]))


def history_changes(current, previous):
    keys = {"gate_decision", "market_freshness", "ah_decision", "context_score"}
    by_fixture = {}
    for row in previous:
        by_fixture[clean(row.get("fixture_id"))] = row
    changed = []
    for row in current:
        old = by_fixture.get(row["fixture_id"])
        if old is None or any(clean(old.get(key)) != clean(row.get(key)) for key in keys):
            changed.append(row)
    return changed


def run_once():
    now = utc_now()
    current = build_gate_rows(
        read_csv_rows(AH_FILE),
        read_csv_rows(TIMELINE_FILE),
        read_csv_rows(COACH_FILE),
        read_csv_rows(MATCHUP_FILE),
        now=now,
    )
    write_csv_atomic(OUTPUT_FILE, OUTPUT_FIELDS, current)
    append_csv(
        HISTORY_FILE,
        OUTPUT_FIELDS,
        history_changes(current, read_csv_rows(HISTORY_FILE)),
    )

    counts = {name: 0 for name in ("SHADOW BET", "WATCH", "PASS")}
    for row in current:
        counts[row["gate_decision"]] = counts.get(row["gate_decision"], 0) + 1
    print("Shadow Value Gate V1 — SHADOW ONLY")
    print("Fixtures:", len(current))
    print("SHADOW BET:", counts["SHADOW BET"])
    print("WATCH:", counts["WATCH"])
    print("PASS:", counts["PASS"])
    print("Output:", os.path.abspath(OUTPUT_FILE))
    print("History:", os.path.abspath(HISTORY_FILE))
    print("API REQUESTS USED: 0")
    return current


if __name__ == "__main__":
    run_once()
