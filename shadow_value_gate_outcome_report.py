import csv
import math
import os
from datetime import datetime, timezone


GATE_HISTORY_FILE = "shadow_value_gate_history.csv"
POST_LINEUP_REPORT_FILE = "post_lineup_clv_report.csv"
OUTPUT_FILE = "shadow_value_gate_outcomes.csv"
SUMMARY_FILE = "shadow_value_gate_outcome_summary.csv"

FINAL_STATUSES = {"FT", "AET", "PEN"}
MIN_CLV_SAMPLE = 50
MIN_SETTLED_SAMPLE = 100

OUTCOME_FIELDS = [
    "evaluated_utc", "fixture_id", "home_team", "away_team", "kickoff_utc",
    "signal", "shock_band", "data_quality", "market_freshness",
    "context_score", "gate_time_utc", "entry_handicap", "entry_avg_odds",
    "entry_best_odds", "entry_best_bookmaker", "close_time",
    "close_handicap", "close_avg_odds", "line_clv", "same_line",
    "price_clv", "match_status", "home_goals", "away_goals", "profit",
    "shadow_only",
]

SUMMARY_FIELDS = [
    "evaluated_utc", "scope", "candidates", "with_clv", "avg_line_clv",
    "line_clv_ci_low", "line_clv_ci_high", "positive_line_clv_rate",
    "settled", "profit_units", "roi", "roi_ci_low", "roi_ci_high",
    "promotion_status", "shadow_only",
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


def split_asian_line(handicap):
    normalized = round(float(handicap) * 4) / 4
    if abs(round(normalized * 4)) % 2 == 1:
        return [normalized - 0.25, normalized + 0.25]
    return [normalized]


def settle_ah(signal, handicap, odds, home_goals, away_goals):
    values = [handicap, odds, home_goals, away_goals]
    if any(value is None for value in values):
        return None
    if signal == "HOME":
        difference = home_goals - away_goals
    elif signal == "AWAY":
        difference = away_goals - home_goals
    else:
        return None

    returns = []
    for line in split_asian_line(handicap):
        adjusted = difference + line
        returns.append(odds - 1 if adjusted > 0 else -1.0 if adjusted < 0 else 0.0)
    return sum(returns) / len(returns)


def build_outcomes(gate_history, post_report, now=None):
    now = now or utc_now()
    bets = first_shadow_bets(gate_history)
    post_by_fixture = {
        clean(row.get("fixture_id")): row for row in post_report
        if clean(row.get("fixture_id"))
    }
    output = []

    for fixture_id, gate in bets.items():
        post = post_by_fixture.get(fixture_id, {})
        signal = clean(gate.get("signal")).upper()
        entry_line = safe_float(gate.get("entry_handicap"))
        entry_avg = safe_float(gate.get("entry_avg_odds"))
        entry_best = safe_float(gate.get("entry_best_odds"))
        close_line = safe_float(post.get("close_handicap"))
        close_avg = safe_float(post.get("close_avg_odds"))
        same_line = int(
            entry_line is not None
            and close_line is not None
            and abs(entry_line - close_line) < 0.001
        )
        line_clv = (
            entry_line - close_line
            if entry_line is not None and close_line is not None else None
        )
        price_clv = (
            entry_avg - close_avg
            if same_line and entry_avg is not None and close_avg is not None else None
        )
        status = clean(post.get("match_status")).upper()
        home_goals = safe_float(post.get("home_goals"))
        away_goals = safe_float(post.get("away_goals"))
        profit = None
        if status in FINAL_STATUSES:
            profit = settle_ah(
                signal, entry_line, entry_best, home_goals, away_goals
            )

        output.append({
            "evaluated_utc": now.isoformat(),
            "fixture_id": fixture_id,
            "home_team": clean(gate.get("home_team")),
            "away_team": clean(gate.get("away_team")),
            "kickoff_utc": clean(gate.get("kickoff_utc")),
            "signal": signal,
            "shock_band": clean(gate.get("shock_band")),
            "data_quality": clean(gate.get("data_quality")),
            "market_freshness": clean(gate.get("market_freshness")),
            "context_score": clean(gate.get("context_score")),
            "gate_time_utc": clean(gate.get("gate_time_utc")),
            "entry_handicap": "" if entry_line is None else entry_line,
            "entry_avg_odds": "" if entry_avg is None else entry_avg,
            "entry_best_odds": "" if entry_best is None else entry_best,
            "entry_best_bookmaker": clean(gate.get("entry_best_bookmaker")),
            "close_time": clean(post.get("close_time")),
            "close_handicap": "" if close_line is None else close_line,
            "close_avg_odds": "" if close_avg is None else close_avg,
            "line_clv": "" if line_clv is None else line_clv,
            "same_line": same_line,
            "price_clv": "" if price_clv is None else price_clv,
            "match_status": status,
            "home_goals": "" if home_goals is None else home_goals,
            "away_goals": "" if away_goals is None else away_goals,
            "profit": "" if profit is None else profit,
            "shadow_only": 1,
        })

    return sorted(output, key=lambda row: (row["kickoff_utc"], row["fixture_id"]))


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


def promotion_status(with_clv, clv_low, settled, roi_low):
    if with_clv < MIN_CLV_SAMPLE:
        return "COLLECTING_CLV"
    if clv_low is None or clv_low <= 0:
        return "KEEP_SHADOW_CLV_FAILED"
    if settled < MIN_SETTLED_SAMPLE:
        return "CLV_PASSED_COLLECTING_ROI"
    if roi_low is None or roi_low <= 0:
        return "KEEP_SHADOW_ROI_UNPROVEN"
    return "REVIEW_FOR_PROMOTION"


def summarize_scope(scope, rows, now):
    clv_values = [safe_float(row.get("line_clv")) for row in rows]
    clv_values = [value for value in clv_values if value is not None]
    profits = [safe_float(row.get("profit")) for row in rows]
    profits = [value for value in profits if value is not None]
    avg_clv, clv_low, clv_high = mean_interval(clv_values)
    roi, roi_low, roi_high = mean_interval(profits)
    return {
        "evaluated_utc": now.isoformat(),
        "scope": scope,
        "candidates": len(rows),
        "with_clv": len(clv_values),
        "avg_line_clv": "" if avg_clv is None else avg_clv,
        "line_clv_ci_low": "" if clv_low is None else clv_low,
        "line_clv_ci_high": "" if clv_high is None else clv_high,
        "positive_line_clv_rate": (
            "" if not clv_values
            else sum(value > 0 for value in clv_values) / len(clv_values)
        ),
        "settled": len(profits),
        "profit_units": sum(profits),
        "roi": "" if roi is None else roi,
        "roi_ci_low": "" if roi_low is None else roi_low,
        "roi_ci_high": "" if roi_high is None else roi_high,
        "promotion_status": promotion_status(
            len(clv_values), clv_low, len(profits), roi_low
        ),
        "shadow_only": 1,
    }


def build_summary(outcomes, now=None):
    now = now or utc_now()
    groups = [("ALL", outcomes)]
    for side in ("HOME", "AWAY"):
        group = [row for row in outcomes if row["signal"] == side]
        if group:
            groups.append((f"SIDE_{side}", group))
    for band in sorted({row["shock_band"] for row in outcomes if row["shock_band"]}):
        groups.append((
            f"SHOCK_{band}",
            [row for row in outcomes if row["shock_band"] == band],
        ))
    return [summarize_scope(scope, rows, now) for scope, rows in groups]


def run_once():
    now = utc_now()
    outcomes = build_outcomes(
        read_csv_rows(GATE_HISTORY_FILE),
        read_csv_rows(POST_LINEUP_REPORT_FILE),
        now=now,
    )
    summary = build_summary(outcomes, now=now)
    write_csv_atomic(OUTPUT_FILE, OUTCOME_FIELDS, outcomes)
    write_csv_atomic(SUMMARY_FILE, SUMMARY_FIELDS, summary)

    overall = summary[0]
    print("Shadow Value Gate Outcome Report — SHADOW ONLY")
    print("Candidates:", overall["candidates"])
    print("With closing CLV:", overall["with_clv"])
    print("Settled:", overall["settled"])
    print("Promotion status:", overall["promotion_status"])
    if overall["avg_line_clv"] != "":
        print("Average line CLV:", f"{overall['avg_line_clv']:+.3f}")
    if overall["roi"] != "":
        print("ROI:", f"{overall['roi']:+.2%}")
    print("Output:", os.path.abspath(OUTPUT_FILE))
    print("Summary:", os.path.abspath(SUMMARY_FILE))
    print("API REQUESTS USED: 0")
    return outcomes, summary


if __name__ == "__main__":
    run_once()
