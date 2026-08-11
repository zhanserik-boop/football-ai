import csv
import json
import math
import os
from datetime import datetime, timezone


GATE_HISTORY_FILE = "shadow_value_gate_history.csv"
OUTCOMES_FILE = "shadow_value_gate_outcomes.csv"
OUTPUT_FILE = "v3_forward_test_scorecard.csv"
SUMMARY_FILE = "v3_forward_test_summary.json"

MIN_CLV_SAMPLE = 50
MIN_SETTLED_SAMPLE = 100
MIN_FORWARD_DAYS = 20
ALLOWED_HEALTH_STATUSES = {"HEALTHY", "HEALTHY_FOR_FIXTURE", "DEGRADED"}

FIELDS = [
    "evaluated_utc", "status", "next_requirement", "unique_fixtures",
    "shadow_candidates", "eligible_forward_bets", "excluded_candidates",
    "cancelled_after_entry", "with_clv", "clv_target", "clv_progress",
    "avg_line_clv", "clv_ci_low", "clv_ci_high",
    "positive_line_clv_rate", "settled", "settled_target",
    "settled_progress", "profit_units", "roi", "roi_ci_low",
    "roi_ci_high", "forward_days", "forward_days_target",
    "forward_days_progress", "home_bets", "away_bets",
    "api_requests_used", "shadow_only",
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


def write_csv_atomic(path, rows):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def write_json_atomic(path, value):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def first_shadow_bets(rows):
    ordered = sorted(
        rows,
        key=lambda row: parse_dt(row.get("gate_time_utc"))
        or datetime.max.replace(tzinfo=timezone.utc),
    )
    first = {}
    for row in ordered:
        fixture_id = clean(row.get("fixture_id"))
        if (
            fixture_id
            and clean(row.get("gate_decision")).upper() == "SHADOW BET"
            and fixture_id not in first
        ):
            first[fixture_id] = row
    return first


def eligible_forward_bet(row):
    return all([
        clean(row.get("shadow_only")) == "1",
        clean(row.get("data_quality")).upper() == "HIGH",
        clean(row.get("market_freshness")).upper() == "FRESH",
        clean(row.get("health_gate_status")).upper() in ALLOWED_HEALTH_STATUSES,
        parse_dt(row.get("kickoff_utc")) is not None,
        safe_float(row.get("entry_handicap")) is not None,
        safe_float(row.get("entry_best_odds")) is not None,
    ])


def cancelled_fixtures(history, entries):
    cancelled = set()
    entry_times = {
        fixture_id: parse_dt(row.get("gate_time_utc"))
        for fixture_id, row in entries.items()
    }
    kickoffs = {
        fixture_id: parse_dt(row.get("kickoff_utc"))
        for fixture_id, row in entries.items()
    }
    for row in history:
        fixture_id = clean(row.get("fixture_id"))
        if fixture_id not in entry_times:
            continue
        row_time = parse_dt(row.get("gate_time_utc"))
        if row_time is None or entry_times[fixture_id] is None:
            continue
        if kickoffs[fixture_id] is None:
            continue
        if (
            row_time > entry_times[fixture_id]
            and row_time < kickoffs[fixture_id]
            and clean(row.get("gate_decision")).upper() != "SHADOW BET"
        ):
            cancelled.add(fixture_id)
    return cancelled


def mean_interval(values, z=1.96):
    values = [float(value) for value in values if value is not None]
    if not values:
        return None, None, None
    mean = sum(values) / len(values)
    if len(values) == 1:
        return mean, mean, mean
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    half = z * math.sqrt(variance) / math.sqrt(len(values))
    return mean, mean - half, mean + half


def capped_progress(value, target):
    return min(1.0, value / target) if target else 1.0


def qualification_status(eligible, with_clv, clv_low, settled, roi_low, days):
    if eligible == 0:
        return "NOT_STARTED", "Wait for the first fully eligible V3 shadow entry"
    if with_clv < MIN_CLV_SAMPLE:
        return "COLLECTING_CLV", f"Need {MIN_CLV_SAMPLE - with_clv} more closing-line observations"
    if clv_low is None or clv_low <= 0:
        return "KEEP_SHADOW_CLV_FAILED", "Positive CLV is not proven at 95% confidence"
    if settled < MIN_SETTLED_SAMPLE:
        return (
            "CLV_PASSED_COLLECTING_ROI",
            f"Need {MIN_SETTLED_SAMPLE - settled} more settled bets",
        )
    if days < MIN_FORWARD_DAYS:
        return (
            "CLV_PASSED_COLLECTING_DAYS",
            f"Need {MIN_FORWARD_DAYS - days} more distinct forward-test days",
        )
    if roi_low is None or roi_low <= 0:
        return "KEEP_SHADOW_ROI_UNPROVEN", "Positive ROI is not proven at 95% confidence"
    return (
        "REVIEW_FOR_CONTROLLED_PILOT",
        "Statistical and operational gates passed; manual review is still required",
    )


def build_scorecard(gate_history, outcomes, now=None):
    now = now or utc_now()
    entries = first_shadow_bets(gate_history)
    cancelled = cancelled_fixtures(gate_history, entries)
    eligible_entries = {
        fixture_id: row for fixture_id, row in entries.items()
        if eligible_forward_bet(row) and fixture_id not in cancelled
    }
    outcome_by_fixture = {
        clean(row.get("fixture_id")): row for row in outcomes
        if clean(row.get("fixture_id"))
    }
    qualifying_outcomes = [
        outcome_by_fixture[fixture_id]
        for fixture_id in eligible_entries
        if fixture_id in outcome_by_fixture
    ]

    clv_values = [safe_float(row.get("line_clv")) for row in qualifying_outcomes]
    clv_values = [value for value in clv_values if value is not None]
    profits = [safe_float(row.get("profit")) for row in qualifying_outcomes]
    profits = [value for value in profits if value is not None]
    avg_clv, clv_low, clv_high = mean_interval(clv_values)
    roi, roi_low, roi_high = mean_interval(profits)
    forward_days = len({
        parsed.date().isoformat()
        for parsed in (
            parse_dt(row.get("gate_time_utc")) for row in eligible_entries.values()
        )
        if parsed is not None
    })
    status, next_requirement = qualification_status(
        len(eligible_entries), len(clv_values), clv_low,
        len(profits), roi_low, forward_days,
    )
    unique_fixtures = len({
        clean(row.get("fixture_id")) for row in gate_history
        if clean(row.get("fixture_id"))
    })
    signals = [clean(row.get("signal")).upper() for row in eligible_entries.values()]

    row = {
        "evaluated_utc": now.isoformat(),
        "status": status,
        "next_requirement": next_requirement,
        "unique_fixtures": unique_fixtures,
        "shadow_candidates": len(entries),
        "eligible_forward_bets": len(eligible_entries),
        "excluded_candidates": len(entries) - len(eligible_entries),
        "cancelled_after_entry": len(cancelled),
        "with_clv": len(clv_values),
        "clv_target": MIN_CLV_SAMPLE,
        "clv_progress": capped_progress(len(clv_values), MIN_CLV_SAMPLE),
        "avg_line_clv": "" if avg_clv is None else avg_clv,
        "clv_ci_low": "" if clv_low is None else clv_low,
        "clv_ci_high": "" if clv_high is None else clv_high,
        "positive_line_clv_rate": (
            "" if not clv_values
            else sum(value > 0 for value in clv_values) / len(clv_values)
        ),
        "settled": len(profits),
        "settled_target": MIN_SETTLED_SAMPLE,
        "settled_progress": capped_progress(len(profits), MIN_SETTLED_SAMPLE),
        "profit_units": sum(profits),
        "roi": "" if roi is None else roi,
        "roi_ci_low": "" if roi_low is None else roi_low,
        "roi_ci_high": "" if roi_high is None else roi_high,
        "forward_days": forward_days,
        "forward_days_target": MIN_FORWARD_DAYS,
        "forward_days_progress": capped_progress(forward_days, MIN_FORWARD_DAYS),
        "home_bets": signals.count("HOME"),
        "away_bets": signals.count("AWAY"),
        "api_requests_used": 0,
        "shadow_only": 1,
    }
    summary = {
        "evaluated_utc": row["evaluated_utc"],
        "status": status,
        "next_requirement": next_requirement,
        "funnel": {
            "unique_fixtures": unique_fixtures,
            "shadow_candidates": len(entries),
            "eligible_forward_bets": len(eligible_entries),
            "excluded_candidates": row["excluded_candidates"],
            "with_clv": len(clv_values),
            "settled": len(profits),
        },
        "evidence": {
            "avg_line_clv": row["avg_line_clv"],
            "clv_ci_low": row["clv_ci_low"],
            "profit_units": row["profit_units"],
            "roi": row["roi"],
            "roi_ci_low": row["roi_ci_low"],
            "forward_days": forward_days,
        },
        "targets": {
            "with_clv": MIN_CLV_SAMPLE,
            "settled": MIN_SETTLED_SAMPLE,
            "forward_days": MIN_FORWARD_DAYS,
        },
        "manual_promotion_required": True,
        "api_requests_used": 0,
        "shadow_only": True,
    }
    return row, summary


def format_signed(value, decimals=3):
    number = safe_float(value)
    return "-" if number is None else f"{number:+.{decimals}f}"


def format_percent(value):
    number = safe_float(value)
    return "-" if number is None else f"{number:+.1%}"


def print_scorecard(row):
    print("\n" + "=" * 72)
    print("FOOTBALL AI V3 — SHADOW FORWARD-TEST SCORECARD")
    print("=" * 72)
    print("STATUS:", row["status"])
    print("NEXT:", row["next_requirement"])
    print(
        "FUNNEL:",
        f"fixtures={row['unique_fixtures']} | candidates={row['shadow_candidates']} | "
        f"eligible={row['eligible_forward_bets']} | excluded={row['excluded_candidates']}",
    )
    print(
        "CLV:",
        f"{row['with_clv']}/{row['clv_target']} | avg {format_signed(row['avg_line_clv'])} | "
        f"CI low {format_signed(row['clv_ci_low'])}",
    )
    print(
        "ROI:",
        f"{row['settled']}/{row['settled_target']} | {format_percent(row['roi'])} | "
        f"CI low {format_percent(row['roi_ci_low'])}",
    )
    print("FORWARD DAYS:", f"{row['forward_days']}/{row['forward_days_target']}")
    print("API REQUESTS USED: 0")
    print("SHADOW ONLY — promotion always requires manual review")
    print("=" * 72)
    print("CSV:", OUTPUT_FILE)
    print("JSON:", SUMMARY_FILE)


def run_once():
    row, summary = build_scorecard(
        read_csv_rows(GATE_HISTORY_FILE),
        read_csv_rows(OUTCOMES_FILE),
    )
    write_csv_atomic(OUTPUT_FILE, [row])
    write_json_atomic(SUMMARY_FILE, summary)
    print_scorecard(row)
    return row, summary


if __name__ == "__main__":
    run_once()
