import csv
import json
import math
import os
import random
from collections import defaultdict
from datetime import datetime, timezone

import v3_forward_test_scorecard as forward


GATE_HISTORY_FILE = "shadow_value_gate_history.csv"
OUTCOMES_FILE = "shadow_value_gate_outcomes.csv"
FORWARD_SUMMARY_FILE = "v3_forward_test_summary.json"
OUTPUT_FILE = "v3_shadow_risk_report.csv"
SUMMARY_FILE = "v3_shadow_risk_summary.json"

FORWARD_UNLOCK_STATUS = "REVIEW_FOR_CONTROLLED_PILOT"
MIN_RISK_SAMPLE = 100
MONTE_CARLO_PATHS = 5000
MONTE_CARLO_SEED = 2608

# Proposed controlled-pilot policy. This engine never places a bet.
PILOT_STAKE_FRACTION = 0.0025
MAX_DAILY_BETS = 4
MAX_DAILY_EXPOSURE_FRACTION = PILOT_STAKE_FRACTION * MAX_DAILY_BETS
ACTUAL_DRAWDOWN_LIMIT_FRACTION = 0.03
STRESS_DRAWDOWN_LIMIT_FRACTION = 0.05
MAX_LOSS_STREAK = 8

FIELDS = [
    "evaluated_utc", "status", "next_requirement", "forward_status",
    "eligible_settled", "policy_bets", "daily_cap_skipped", "bet_days",
    "profit_units", "roi", "max_drawdown_units", "max_drawdown_fraction",
    "longest_loss_streak", "worst_day_units", "max_bets_in_day",
    "mc_paths", "mc_p95_drawdown_units", "mc_p95_drawdown_fraction",
    "mc_probability_5pct_drawdown", "mc_probability_ending_negative",
    "pilot_stake_fraction", "pilot_max_daily_bets",
    "pilot_max_daily_exposure_fraction", "pilot_pause_drawdown_fraction",
    "risk_review_ready", "activation_locked", "manual_review_required", "api_requests_used",
    "shadow_only",
]


def utc_now():
    return datetime.now(timezone.utc)


def read_csv_rows(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader) if reader.fieldnames else []


def read_json(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def write_csv_atomic(path, row):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerow(row)
    os.replace(temporary, path)


def write_json_atomic(path, value):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def qualifying_settled(gate_history, outcomes):
    entries = forward.first_shadow_bets(gate_history)
    cancelled = forward.cancelled_fixtures(gate_history, entries)
    eligible = {
        fixture_id: row for fixture_id, row in entries.items()
        if forward.eligible_forward_bet(row) and fixture_id not in cancelled
    }
    outcome_by_fixture = {
        forward.clean(row.get("fixture_id")): row for row in outcomes
        if forward.clean(row.get("fixture_id"))
    }
    records = []
    for fixture_id, entry in eligible.items():
        outcome = outcome_by_fixture.get(fixture_id)
        if not outcome:
            continue
        profit = forward.safe_float(outcome.get("profit"))
        if profit is None:
            continue
        kickoff = (
            forward.parse_dt(outcome.get("kickoff_utc"))
            or forward.parse_dt(entry.get("kickoff_utc"))
        )
        if kickoff is None:
            continue
        records.append({
            "fixture_id": fixture_id,
            "gate_time": forward.parse_dt(entry.get("gate_time_utc")) or kickoff,
            "kickoff": kickoff,
            "profit": profit,
            "signal": forward.clean(entry.get("signal")).upper(),
        })
    return sorted(records, key=lambda row: (row["gate_time"], row["fixture_id"]))


def apply_daily_cap(records, cap=MAX_DAILY_BETS):
    accepted = []
    skipped = []
    counts = defaultdict(int)
    for row in records:
        day = row["kickoff"].date().isoformat()
        if counts[day] < cap:
            accepted.append(row)
            counts[day] += 1
        else:
            skipped.append(row)
    return accepted, skipped


def path_metrics(profits):
    equity = 0.0
    peak = 0.0
    maximum_drawdown = 0.0
    current_losses = 0
    longest_losses = 0
    for profit in profits:
        equity += profit
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, peak - equity)
        if profit < 0:
            current_losses += 1
            longest_losses = max(longest_losses, current_losses)
        else:
            current_losses = 0
    return {
        "profit_units": equity,
        "roi": equity / len(profits) if profits else None,
        "max_drawdown_units": maximum_drawdown,
        "longest_loss_streak": longest_losses,
    }


def percentile(values, probability):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(probability * len(ordered)) - 1))
    return ordered[index]


def bootstrap_risk(
    profits,
    paths=MONTE_CARLO_PATHS,
    seed=MONTE_CARLO_SEED,
):
    if len(profits) < 30 or paths <= 0:
        return {
            "paths": 0,
            "p95_drawdown_units": None,
            "probability_5pct_drawdown": None,
            "probability_ending_negative": None,
        }
    rng = random.Random(seed)
    drawdowns = []
    negative_endings = 0
    five_percent_drawdowns = 0
    threshold_units = STRESS_DRAWDOWN_LIMIT_FRACTION / PILOT_STAKE_FRACTION
    for _ in range(paths):
        sample = [rng.choice(profits) for _ in profits]
        metrics = path_metrics(sample)
        drawdown = metrics["max_drawdown_units"]
        drawdowns.append(drawdown)
        if drawdown >= threshold_units:
            five_percent_drawdowns += 1
        if metrics["profit_units"] < 0:
            negative_endings += 1
    return {
        "paths": paths,
        "p95_drawdown_units": percentile(drawdowns, 0.95),
        "probability_5pct_drawdown": five_percent_drawdowns / paths,
        "probability_ending_negative": negative_endings / paths,
    }


def daily_metrics(records):
    profits = defaultdict(float)
    counts = defaultdict(int)
    for row in records:
        day = row["kickoff"].date().isoformat()
        profits[day] += row["profit"]
        counts[day] += 1
    return {
        "bet_days": len(counts),
        "worst_day_units": min(profits.values()) if profits else None,
        "max_bets_in_day": max(counts.values()) if counts else 0,
    }


def risk_status(forward_status, sample, actual, stress):
    if forward_status != FORWARD_UNLOCK_STATUS:
        return "LOCKED_BY_FORWARD_TEST", "Wait until the forward scorecard passes all gates"
    if sample < MIN_RISK_SAMPLE:
        return "COLLECTING_RISK_SAMPLE", f"Need {MIN_RISK_SAMPLE - sample} more policy-qualified results"
    if stress["paths"] == 0:
        return "RISK_SAMPLE_UNAVAILABLE", "Monte Carlo stress sample is unavailable"
    actual_fraction = actual["max_drawdown_units"] * PILOT_STAKE_FRACTION
    stress_fraction = stress["p95_drawdown_units"] * PILOT_STAKE_FRACTION
    if actual_fraction >= ACTUAL_DRAWDOWN_LIMIT_FRACTION:
        return "KEEP_SHADOW_ACTUAL_DRAWDOWN", "Realized drawdown exceeds the 3% pilot limit"
    if stress_fraction >= STRESS_DRAWDOWN_LIMIT_FRACTION:
        return "KEEP_SHADOW_STRESS_DRAWDOWN", "95th-percentile drawdown reaches the 5% pause limit"
    if actual["longest_loss_streak"] >= MAX_LOSS_STREAK:
        return "KEEP_SHADOW_LOSS_STREAK", f"Observed loss streak reached {MAX_LOSS_STREAK} bets"
    return (
        "REVIEW_FOR_CONTROLLED_PILOT_RISK",
        "Risk gates passed; bankroll amount and manual approval are still required",
    )


def build_risk_report(gate_history, outcomes, forward_status, now=None):
    now = now or utc_now()
    settled = qualifying_settled(gate_history, outcomes)
    policy_records, skipped = apply_daily_cap(settled)
    profits = [row["profit"] for row in policy_records]
    actual = path_metrics(profits)
    stress = bootstrap_risk(profits)
    daily = daily_metrics(policy_records)
    status, next_requirement = risk_status(
        forward.clean(forward_status).upper(), len(policy_records), actual, stress
    )
    max_drawdown_fraction = actual["max_drawdown_units"] * PILOT_STAKE_FRACTION
    p95_units = stress["p95_drawdown_units"]
    p95_fraction = None if p95_units is None else p95_units * PILOT_STAKE_FRACTION
    risk_review_ready = status == "REVIEW_FOR_CONTROLLED_PILOT_RISK"
    # Deliberately never unlocked by statistics. A future controlled pilot
    # requires a separate reviewed change and explicit bankroll configuration.
    activation_locked = True

    row = {
        "evaluated_utc": now.isoformat(),
        "status": status,
        "next_requirement": next_requirement,
        "forward_status": forward.clean(forward_status).upper() or "UNKNOWN",
        "eligible_settled": len(settled),
        "policy_bets": len(policy_records),
        "daily_cap_skipped": len(skipped),
        "bet_days": daily["bet_days"],
        "profit_units": actual["profit_units"],
        "roi": "" if actual["roi"] is None else actual["roi"],
        "max_drawdown_units": actual["max_drawdown_units"],
        "max_drawdown_fraction": max_drawdown_fraction,
        "longest_loss_streak": actual["longest_loss_streak"],
        "worst_day_units": "" if daily["worst_day_units"] is None else daily["worst_day_units"],
        "max_bets_in_day": daily["max_bets_in_day"],
        "mc_paths": stress["paths"],
        "mc_p95_drawdown_units": "" if p95_units is None else p95_units,
        "mc_p95_drawdown_fraction": "" if p95_fraction is None else p95_fraction,
        "mc_probability_5pct_drawdown": (
            "" if stress["probability_5pct_drawdown"] is None
            else stress["probability_5pct_drawdown"]
        ),
        "mc_probability_ending_negative": (
            "" if stress["probability_ending_negative"] is None
            else stress["probability_ending_negative"]
        ),
        "pilot_stake_fraction": PILOT_STAKE_FRACTION,
        "pilot_max_daily_bets": MAX_DAILY_BETS,
        "pilot_max_daily_exposure_fraction": MAX_DAILY_EXPOSURE_FRACTION,
        "pilot_pause_drawdown_fraction": STRESS_DRAWDOWN_LIMIT_FRACTION,
        "risk_review_ready": int(risk_review_ready),
        "activation_locked": int(activation_locked),
        "manual_review_required": 1,
        "api_requests_used": 0,
        "shadow_only": 1,
    }
    summary = {
        "evaluated_utc": row["evaluated_utc"],
        "status": status,
        "next_requirement": next_requirement,
        "forward_status": row["forward_status"],
        "sample": {
            "eligible_settled": len(settled),
            "policy_bets": len(policy_records),
            "daily_cap_skipped": len(skipped),
            "bet_days": daily["bet_days"],
        },
        "realized": {
            "profit_units": row["profit_units"],
            "roi": row["roi"],
            "max_drawdown_units": row["max_drawdown_units"],
            "max_drawdown_fraction": row["max_drawdown_fraction"],
            "longest_loss_streak": row["longest_loss_streak"],
            "worst_day_units": row["worst_day_units"],
        },
        "stress": {
            "paths": row["mc_paths"],
            "p95_drawdown_units": row["mc_p95_drawdown_units"],
            "p95_drawdown_fraction": row["mc_p95_drawdown_fraction"],
            "probability_5pct_drawdown": row["mc_probability_5pct_drawdown"],
            "probability_ending_negative": row["mc_probability_ending_negative"],
        },
        "proposed_pilot_policy": {
            "stake_fraction": PILOT_STAKE_FRACTION,
            "max_daily_bets": MAX_DAILY_BETS,
            "max_daily_exposure_fraction": MAX_DAILY_EXPOSURE_FRACTION,
            "pause_drawdown_fraction": STRESS_DRAWDOWN_LIMIT_FRACTION,
        },
        "risk_review_ready": risk_review_ready,
        "activation_locked": activation_locked,
        "manual_review_required": True,
        "api_requests_used": 0,
        "shadow_only": True,
    }
    return row, summary


def format_percent(value):
    number = forward.safe_float(value)
    return "-" if number is None else f"{number:.2%}"


def print_report(row):
    print("\n" + "=" * 72)
    print("FOOTBALL AI V3 — SHADOW RISK ENGINE")
    print("=" * 72)
    print("STATUS:", row["status"])
    print("NEXT:", row["next_requirement"])
    print(
        "SAMPLE:",
        f"eligible={row['eligible_settled']} | policy={row['policy_bets']} | "
        f"daily-cap skipped={row['daily_cap_skipped']}",
    )
    print(
        "REALIZED:",
        f"ROI {format_percent(row['roi'])} | max DD {format_percent(row['max_drawdown_fraction'])} | "
        f"loss streak {row['longest_loss_streak']}",
    )
    print(
        "STRESS:",
        f"paths={row['mc_paths']} | p95 DD {format_percent(row['mc_p95_drawdown_fraction'])} | "
        f"P(end<0) {format_percent(row['mc_probability_ending_negative'])}",
    )
    print(
        "PROPOSED PILOT LIMITS:",
        f"stake {format_percent(row['pilot_stake_fraction'])} | "
        f"daily exposure {format_percent(row['pilot_max_daily_exposure_fraction'])} | "
        f"pause DD {format_percent(row['pilot_pause_drawdown_fraction'])}",
    )
    print("RISK REVIEW READY:", "YES" if row["risk_review_ready"] else "NO")
    print("ACTIVATION LOCKED: YES — a separate reviewed change is required")
    print("API REQUESTS USED: 0")
    print("SHADOW ONLY — this engine never places bets")
    print("=" * 72)
    print("CSV:", OUTPUT_FILE)
    print("JSON:", SUMMARY_FILE)


def run_once():
    forward_summary = read_json(FORWARD_SUMMARY_FILE)
    row, summary = build_risk_report(
        read_csv_rows(GATE_HISTORY_FILE),
        read_csv_rows(OUTCOMES_FILE),
        forward_summary.get("status", "UNKNOWN"),
    )
    write_csv_atomic(OUTPUT_FILE, row)
    write_json_atomic(SUMMARY_FILE, summary)
    print_report(row)
    return row, summary


if __name__ == "__main__":
    run_once()
