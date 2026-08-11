import argparse
import csv
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import v3_forward_test_scorecard as forward


HISTORICAL_FILE = "epl_lineup_shock_robustness.csv"
GATE_HISTORY_FILE = "shadow_value_gate_history.csv"
OUTCOMES_FILE = "shadow_value_gate_outcomes.csv"

BASELINE_FILE = "v3_drift_baseline.json"
OUTPUT_FILE = "v3_drift_watch.csv"
SUMMARY_FILE = "v3_drift_watch_summary.json"
NOTIFY_STATE_FILE = "v3_drift_watch_notify_state.json"

MIN_BASELINE_SAMPLE = 100
MIN_LIVE_SAMPLE = 30
LIVE_WINDOW = 50
MIN_CLV_SAMPLE = 20
CLV_WINDOW = 30

PSI_WARNING = 0.10
PSI_CRITICAL = 0.25
SHIFT_WARNING = 0.50
SHIFT_CRITICAL = 1.00

BANDS = (
    "ROBUST_1.5_2.0",
    "UNSTABLE_2.0_2.5",
    "EXTREME_2.5_PLUS_SMALL_SAMPLE",
)

FIELDS = (
    "checked_utc", "status", "reason", "baseline_n", "eligible_live_total",
    "live_window_n", "live_with_clv", "baseline_home_rate", "live_home_rate",
    "side_psi", "shock_band_psi", "baseline_abs_shock_mean",
    "live_abs_shock_mean", "abs_shock_standardized_shift", "recent_clv_n",
    "recent_avg_line_clv", "recent_clv_ci_low", "recent_clv_ci_high",
    "issue_codes", "baseline_frozen_utc", "baseline_source_changed",
    "api_requests_used", "shadow_only",
)


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


def write_csv_atomic(path, row):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerow(row)
    os.replace(temporary, path)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def shock_band(value):
    number = safe_float(value)
    if number is None or abs(number) < 1.5:
        return "BELOW_THRESHOLD"
    number = abs(number)
    if number < 2.0:
        return BANDS[0]
    if number < 2.5:
        return BANDS[1]
    return BANDS[2]


def distribution(values, categories):
    total = len(values)
    if total == 0:
        return {name: 0.0 for name in categories}
    return {name: values.count(name) / total for name in categories}


def sample_metrics(rows):
    signals = [clean(row.get("signal")).upper() for row in rows]
    shocks = [safe_float(row.get("abs_shock")) for row in rows]
    shocks = [abs(value) for value in shocks if value is not None]
    bands = [shock_band(value) for value in shocks]
    mean = sum(shocks) / len(shocks) if shocks else None
    if len(shocks) > 1:
        variance = sum((value - mean) ** 2 for value in shocks) / (len(shocks) - 1)
        standard_deviation = math.sqrt(variance)
    else:
        standard_deviation = 0.0 if shocks else None
    return {
        "sample_size": len(rows),
        "home_rate": signals.count("HOME") / len(signals) if signals else None,
        "side_rates": distribution(signals, ("HOME", "AWAY")),
        "shock_band_rates": distribution(bands, BANDS),
        "abs_shock_mean": mean,
        "abs_shock_standard_deviation": standard_deviation,
    }


def historical_population(rows):
    return [
        row for row in rows
        if clean(row.get("data_quality")).upper() == "HIGH"
        and clean(row.get("signal")).upper() in {"HOME", "AWAY"}
        and safe_float(row.get("abs_shock")) is not None
        and safe_float(row.get("signed_close_move_for_lineup")) is not None
    ]


def create_baseline(rows, now, source_sha256=""):
    population = historical_population(rows)
    if len(population) < MIN_BASELINE_SAMPLE:
        raise ValueError(
            f"Need at least {MIN_BASELINE_SAMPLE} compatible HIGH historical rows; "
            f"found {len(population)}"
        )
    return {
        "schema_version": 1,
        "frozen_utc": now.isoformat(),
        "source_file": HISTORICAL_FILE,
        "source_sha256": source_sha256,
        "filters": {
            "data_quality": "HIGH",
            "signal": ["HOME", "AWAY"],
            "requires_signed_close_move": True,
        },
        "metrics": sample_metrics(population),
        "api_requests_used": 0,
        "shadow_only": True,
    }


def valid_baseline(value):
    metrics = value.get("metrics", {}) if isinstance(value, dict) else {}
    sample_size = safe_float(metrics.get("sample_size"))
    return all([
        value.get("schema_version") == 1,
        sample_size is not None,
        sample_size is not None and sample_size >= MIN_BASELINE_SAMPLE,
        safe_float(metrics.get("home_rate")) is not None,
        safe_float(metrics.get("abs_shock_mean")) is not None,
        safe_float(metrics.get("abs_shock_standard_deviation")) is not None,
        isinstance(metrics.get("side_rates"), dict),
        isinstance(metrics.get("shock_band_rates"), dict),
    ])


def smoothed_distribution(values, categories, epsilon=1e-6):
    numbers = [max(epsilon, safe_float(values.get(name)) or 0.0) for name in categories]
    total = sum(numbers)
    return [number / total for number in numbers]


def population_stability_index(baseline, live, categories):
    expected = smoothed_distribution(baseline, categories)
    actual = smoothed_distribution(live, categories)
    return sum(
        (observed - reference) * math.log(observed / reference)
        for reference, observed in zip(expected, actual)
    )


def live_population(gate_history):
    entries = forward.first_shadow_bets(gate_history)
    cancelled = forward.cancelled_fixtures(gate_history, entries)
    rows = [
        row for fixture_id, row in entries.items()
        if fixture_id not in cancelled and forward.eligible_forward_bet(row)
    ]
    return sorted(
        rows,
        key=lambda row: forward.parse_dt(row.get("gate_time_utc"))
        or datetime.min.replace(tzinfo=timezone.utc),
    )


def recent_clv(entries, outcomes):
    by_fixture = {
        clean(row.get("fixture_id")): row for row in outcomes
        if clean(row.get("fixture_id"))
    }
    values = []
    for entry in reversed(entries):
        outcome = by_fixture.get(clean(entry.get("fixture_id")))
        value = safe_float((outcome or {}).get("line_clv"))
        if value is not None:
            values.append(value)
        if len(values) >= CLV_WINDOW:
            break
    values.reverse()
    mean, low, high = forward.mean_interval(values)
    return values, mean, low, high


def drift_issue(severity, code, message):
    return {"severity": severity, "code": code, "message": message}


def evaluate_drift(baseline, gate_history, outcomes, now):
    baseline_metrics = baseline["metrics"]
    entries = live_population(gate_history)
    window = entries[-LIVE_WINDOW:]
    live_metrics = sample_metrics(window)
    clv_values, clv_mean, clv_low, clv_high = recent_clv(entries, outcomes)
    issues = []

    side_psi = None
    band_psi = None
    standardized_shift = None
    if len(entries) < MIN_LIVE_SAMPLE:
        status = "COLLECTING"
        reason = f"Need {MIN_LIVE_SAMPLE - len(entries)} more eligible shadow entries"
    else:
        side_psi = population_stability_index(
            baseline_metrics["side_rates"], live_metrics["side_rates"],
            ("HOME", "AWAY"),
        )
        band_psi = population_stability_index(
            baseline_metrics["shock_band_rates"],
            live_metrics["shock_band_rates"], BANDS,
        )
        baseline_sd = safe_float(baseline_metrics["abs_shock_standard_deviation"])
        difference = abs(
            live_metrics["abs_shock_mean"] - baseline_metrics["abs_shock_mean"]
        )
        standardized_shift = difference / baseline_sd if baseline_sd else (
            0.0 if difference == 0 else math.inf
        )

        for value, warning, critical, warning_code, critical_code, label in (
            (
                side_psi, PSI_WARNING, PSI_CRITICAL,
                "SIDE_MIX_SHIFT", "SIDE_MIX_DRIFT", "HOME/AWAY mix PSI",
            ),
            (
                band_psi, PSI_WARNING, PSI_CRITICAL,
                "SHOCK_BAND_SHIFT", "SHOCK_BAND_DRIFT", "shock-band PSI",
            ),
            (
                standardized_shift, SHIFT_WARNING, SHIFT_CRITICAL,
                "SHOCK_LEVEL_SHIFT", "SHOCK_LEVEL_DRIFT", "shock-level shift",
            ),
        ):
            if value >= critical:
                issues.append(drift_issue(
                    "CRITICAL", critical_code,
                    f"{label} {value:.3f} reached critical threshold {critical:.2f}",
                ))
            elif value >= warning:
                issues.append(drift_issue(
                    "DEGRADED", warning_code,
                    f"{label} {value:.3f} reached warning threshold {warning:.2f}",
                ))

        if len(clv_values) >= MIN_CLV_SAMPLE:
            if clv_high is not None and clv_high < 0:
                issues.append(drift_issue(
                    "CRITICAL", "RECENT_CLV_NEGATIVE_CONFIRMED",
                    "Recent qualifying CLV is negative at 95% confidence",
                ))
            elif clv_mean is not None and clv_mean <= 0:
                issues.append(drift_issue(
                    "DEGRADED", "RECENT_CLV_NON_POSITIVE",
                    "Recent qualifying average CLV is not positive",
                ))

        if any(row["severity"] == "CRITICAL" for row in issues):
            status = "DRIFT_ALERT"
            reason = "Critical live distribution or performance drift detected"
        elif issues:
            status = "DEGRADED"
            reason = "Live distribution or performance shift needs review"
        else:
            status = "STABLE"
            reason = "No material live drift detected"

    row = {
        "checked_utc": now.isoformat(),
        "status": status,
        "reason": reason,
        "baseline_n": baseline_metrics["sample_size"],
        "eligible_live_total": len(entries),
        "live_window_n": len(window),
        "live_with_clv": len(clv_values),
        "baseline_home_rate": baseline_metrics["home_rate"],
        "live_home_rate": "" if live_metrics["home_rate"] is None else live_metrics["home_rate"],
        "side_psi": "" if side_psi is None else side_psi,
        "shock_band_psi": "" if band_psi is None else band_psi,
        "baseline_abs_shock_mean": baseline_metrics["abs_shock_mean"],
        "live_abs_shock_mean": (
            "" if live_metrics["abs_shock_mean"] is None
            else live_metrics["abs_shock_mean"]
        ),
        "abs_shock_standardized_shift": (
            "" if standardized_shift is None else standardized_shift
        ),
        "recent_clv_n": len(clv_values),
        "recent_avg_line_clv": "" if clv_mean is None else clv_mean,
        "recent_clv_ci_low": "" if clv_low is None else clv_low,
        "recent_clv_ci_high": "" if clv_high is None else clv_high,
        "issue_codes": "|".join(sorted(row["code"] for row in issues)),
        "baseline_frozen_utc": baseline.get("frozen_utc", ""),
        "baseline_source_changed": False,
        "api_requests_used": 0,
        "shadow_only": 1,
    }
    summary = {
        "checked_utc": row["checked_utc"],
        "status": status,
        "reason": reason,
        "baseline": {
            "sample_size": baseline_metrics["sample_size"],
            "frozen_utc": baseline.get("frozen_utc", ""),
            "source_file": baseline.get("source_file", ""),
            "source_sha256": baseline.get("source_sha256", ""),
            "home_rate": baseline_metrics["home_rate"],
            "shock_band_rates": baseline_metrics["shock_band_rates"],
            "abs_shock_mean": baseline_metrics["abs_shock_mean"],
            "abs_shock_standard_deviation": baseline_metrics[
                "abs_shock_standard_deviation"
            ],
        },
        "live": {
            "eligible_total": len(entries),
            "window_size": len(window),
            "home_rate": live_metrics["home_rate"],
            "shock_band_rates": live_metrics["shock_band_rates"],
            "abs_shock_mean": live_metrics["abs_shock_mean"],
            "recent_clv_n": len(clv_values),
            "recent_avg_line_clv": clv_mean,
            "recent_clv_ci_low": clv_low,
            "recent_clv_ci_high": clv_high,
        },
        "drift": {
            "side_psi": side_psi,
            "shock_band_psi": band_psi,
            "abs_shock_standardized_shift": standardized_shift,
        },
        "issues": issues,
        "value_gate_blocked": False,
        "manual_review_required": status in {"DEGRADED", "DRIFT_ALERT"},
        "api_requests_used": 0,
        "shadow_only": True,
    }
    return row, summary


def pending_summary(now, status, reason, baseline=None):
    baseline = baseline or {}
    metrics = baseline.get("metrics", {}) if isinstance(baseline, dict) else {}
    row = {name: "" for name in FIELDS}
    row.update({
        "checked_utc": now.isoformat(),
        "status": status,
        "reason": reason,
        "baseline_n": metrics.get("sample_size", ""),
        "baseline_frozen_utc": baseline.get("frozen_utc", ""),
        "api_requests_used": 0,
        "shadow_only": 1,
    })
    summary = {
        "checked_utc": now.isoformat(),
        "status": status,
        "reason": reason,
        "baseline": baseline,
        "live": {},
        "drift": {},
        "issues": [],
        "value_gate_blocked": False,
        "manual_review_required": status == "BASELINE_INVALID",
        "api_requests_used": 0,
        "shadow_only": True,
    }
    return row, summary


def issue_fingerprint(summary):
    return "|".join(sorted(
        f"{row.get('severity', '')}:{row.get('code', '')}"
        for row in summary.get("issues", [])
    ))


def notification_event(summary, state):
    status = summary.get("status", "UNKNOWN")
    fingerprint = issue_fingerprint(summary)
    previous_status = state.get("status", "")
    previous_fingerprint = state.get("fingerprint", "")
    if status in {"DEGRADED", "DRIFT_ALERT"}:
        if fingerprint != previous_fingerprint or status != previous_status:
            return "ISSUE", drift_message(summary)
    elif status == "STABLE" and previous_status in {"DEGRADED", "DRIFT_ALERT"}:
        return "RECOVERED", drift_message(summary, recovered=True)
    return None


def drift_message(summary, recovered=False):
    if recovered:
        return "\n".join([
            "V3 DRIFT WATCH — RECOVERED",
            "",
            "Live signal distribution is back inside monitoring thresholds.",
            "Value Gate remained shadow-only and was not changed.",
        ])
    lines = [f"V3 DRIFT WATCH — {summary.get('status', 'UNKNOWN')}", ""]
    for row in summary.get("issues", []):
        lines.append(f"{row.get('code')}: {row.get('message')}")
    lines.extend(["", "Manual review required. Value Gate was not blocked or changed."])
    return "\n".join(lines)


def update_notification(root, summary, sender=None):
    state_path = Path(root) / NOTIFY_STATE_FILE
    state = read_json(state_path)
    event = notification_event(summary, state)
    sent = False
    if event:
        if sender is None:
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                from telegram_notifier import send_telegram
                sender = send_telegram
            finally:
                os.chdir(old_cwd)
        sent = bool(sender(event[1]))
        if sent:
            write_json_atomic(state_path, {
                "status": summary["status"],
                "fingerprint": issue_fingerprint(summary),
                "updated_utc": summary["checked_utc"],
            })
    elif not state.get("status"):
        write_json_atomic(state_path, {
            "status": summary["status"],
            "fingerprint": issue_fingerprint(summary),
            "updated_utc": summary["checked_utc"],
        })
    return event, sent


def run_once(root=".", sender=None, now=None, rebuild_baseline=False, notify=True):
    root = Path(root)
    now = now or utc_now()
    baseline_path = root / BASELINE_FILE
    historical_path = root / HISTORICAL_FILE
    baseline = {} if rebuild_baseline else read_json(baseline_path)

    if not valid_baseline(baseline):
        if not historical_path.exists():
            row, summary = pending_summary(
                now, "WAITING_FOR_BASELINE",
                f"Run lineup_shock_robustness_research.py to create {HISTORICAL_FILE}",
            )
        else:
            try:
                baseline = create_baseline(
                    read_csv_rows(historical_path), now,
                    source_sha256=file_sha256(historical_path),
                )
                write_json_atomic(baseline_path, baseline)
                row, summary = evaluate_drift(
                    baseline,
                    read_csv_rows(root / GATE_HISTORY_FILE),
                    read_csv_rows(root / OUTCOMES_FILE),
                    now,
                )
            except ValueError as exc:
                row, summary = pending_summary(
                    now, "BASELINE_INVALID", str(exc), baseline=baseline
                )
    else:
        row, summary = evaluate_drift(
            baseline,
            read_csv_rows(root / GATE_HISTORY_FILE),
            read_csv_rows(root / OUTCOMES_FILE),
            now,
        )
        if historical_path.exists() and baseline.get("source_sha256"):
            changed = file_sha256(historical_path) != baseline["source_sha256"]
            row["baseline_source_changed"] = changed
            summary["baseline"]["source_changed_since_freeze"] = changed

    write_csv_atomic(root / OUTPUT_FILE, row)
    write_json_atomic(root / SUMMARY_FILE, summary)
    event, sent = update_notification(root, summary, sender=sender) if notify else (None, False)

    print("\n" + "=" * 72)
    print("FOOTBALL AI V3 — DRIFT WATCH")
    print("=" * 72)
    print("STATUS:", summary["status"])
    print("DETAIL:", summary["reason"])
    print("BASELINE N:", row.get("baseline_n") or "-")
    print("LIVE ELIGIBLE:", row.get("eligible_live_total") or 0)
    print("LIVE WINDOW:", row.get("live_window_n") or 0)
    print("TELEGRAM SENT:", "YES" if sent else "NO")
    print("VALUE GATE BLOCKED: NO")
    print("API REQUESTS USED: 0")
    print("SHADOW ONLY: YES")
    print("=" * 72)
    print("CSV:", OUTPUT_FILE)
    print("JSON:", SUMMARY_FILE)
    return row, summary, event, sent


def main():
    parser = argparse.ArgumentParser(description="Football AI V3 shadow drift monitor")
    parser.add_argument(
        "--rebuild-baseline", action="store_true",
        help="Explicitly replace the locally frozen historical baseline",
    )
    parser.add_argument(
        "--no-notify", action="store_true", help="Do not send Telegram notifications"
    )
    args = parser.parse_args()
    run_once(rebuild_baseline=args.rebuild_baseline, notify=not args.no_notify)


if __name__ == "__main__":
    main()
