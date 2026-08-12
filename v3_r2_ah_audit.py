"""Offline V3 R2 Asian Handicap correctness and compatibility audit."""

from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import asian_handicap_v3_r2 as ah


SNAPSHOT_FILE = "market_snapshots_v2.csv"
JSON_OUTPUT = "v3_r2_ah_audit.json"
CSV_OUTPUT = "v3_r2_ah_audit.csv"


def utc_now():
    return datetime.now(timezone.utc)


def read_rows(path):
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader) if reader.fieldnames else []


def write_json(path, value):
    temporary = str(path) + ".tmp"
    Path(temporary).write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def write_csv(path, rows):
    fields = [
        "fixture_id", "snapshot_utc", "status", "home_handicap",
        "bookmakers", "provider_layouts", "raw_rows", "legacy_home_handicap",
        "legacy_away_handicap", "r2_away_handicap", "home_line_changed",
        "away_line_changed", "raw_values_reparsed", "reason",
    ]
    temporary = str(path) + ".tmp"
    with open(temporary, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def canonical_snapshot_rows(rows):
    output = []
    reparsed = 0
    for row in rows:
        parsed = ah.parse_provider_value(row.get("value"))
        if parsed is not None:
            output.append({
                **row,
                "side": parsed["side"],
                "handicap": parsed["provider_handicap"],
            })
            reparsed += 1
        else:
            output.append(row)
    return output, reparsed


def legacy_side_line(rows, signal):
    candidates = []
    for row in rows:
        side = str(row.get("parsed_side") or row.get("side") or "").upper()
        line = ah.safe_float(row.get("parsed_handicap", row.get("handicap")))
        if side == signal and line is not None:
            candidates.append(line)
    if not candidates:
        return None
    counts = Counter(candidates)
    maximum = max(counts.values())
    lines = [line for line, count in counts.items() if count == maximum]
    ordered = sorted(candidates)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (
        ordered[middle - 1] + ordered[middle]
    ) / 2
    return min(lines, key=lambda line: abs(line - median))


def changed(left, right):
    return int(left is not None and right is not None and abs(left - right) > 0.001)


def runtime_snapshot_audit(rows):
    grouped = defaultdict(list)
    for row in rows:
        key = (str(row.get("fixture_id") or ""), str(row.get("snapshot_utc") or ""))
        if all(key):
            grouped[key].append(row)
    output = []
    for (fixture_id, snapshot), part in sorted(grouped.items()):
        canonical, reparsed = canonical_snapshot_rows(part)
        market = ah.market_consensus(canonical)
        legacy_home = legacy_side_line(part, "HOME")
        legacy_away = legacy_side_line(part, "AWAY")
        if market is None:
            output.append({
                "fixture_id": fixture_id,
                "snapshot_utc": snapshot,
                "status": "FAIL_CLOSED",
                "home_handicap": "",
                "bookmakers": 0,
                "provider_layouts": "",
                "raw_rows": len(part),
                "legacy_home_handicap": "" if legacy_home is None else legacy_home,
                "legacy_away_handicap": "" if legacy_away is None else legacy_away,
                "r2_away_handicap": "",
                "home_line_changed": 0,
                "away_line_changed": 0,
                "raw_values_reparsed": reparsed,
                "reason": "no paired balanced AH market",
            })
        else:
            output.append({
                "fixture_id": fixture_id,
                "snapshot_utc": snapshot,
                "status": "NORMALIZED",
                "home_handicap": market["home_handicap"],
                "bookmakers": market["bookmakers"],
                "provider_layouts": "|".join(market["provider_layouts"]),
                "raw_rows": len(part),
                "legacy_home_handicap": "" if legacy_home is None else legacy_home,
                "legacy_away_handicap": "" if legacy_away is None else legacy_away,
                "r2_away_handicap": -market["home_handicap"],
                "home_line_changed": changed(legacy_home, market["home_handicap"]),
                "away_line_changed": changed(legacy_away, -market["home_handicap"]),
                "raw_values_reparsed": reparsed,
                "reason": "per-book balanced main lines then cross-book consensus",
            })
    return output


def invariant_checks():
    checks = []

    def add(name, passed, detail):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    add(
        "HOME_DIRECTION",
        ah.line_move_toward_signal(-0.50, -0.75, "HOME") == 0.25,
        "home handicap -0.50 to -0.75 strengthens HOME",
    )
    add(
        "AWAY_DIRECTION",
        ah.line_move_toward_signal(-0.50, -0.25, "AWAY") == 0.25,
        "home handicap -0.50 to -0.25 strengthens AWAY",
    )
    add(
        "SPLIT_LINE",
        ah.parse_provider_value("Home -0.5, -1.0") == {
            "side": "HOME", "provider_handicap": -0.75,
        },
        "split AH is averaged rather than taking the last number",
    )
    add(
        "UNPAIRED_FAIL_CLOSED",
        ah.market_consensus([{
            "bookmaker": "A", "side": "HOME", "handicap": -0.5, "odd": 1.95,
        }]) is None,
        "one-sided quote cannot become a market",
    )
    ladder = []
    for bookmaker in ("A", "B", "C"):
        ladder.extend([
            {"bookmaker": bookmaker, "side": "HOME", "handicap": -1.0, "odd": 2.50},
            {"bookmaker": bookmaker, "side": "AWAY", "handicap": -1.0, "odd": 1.55},
            {"bookmaker": bookmaker, "side": "HOME", "handicap": -0.5, "odd": 1.94},
            {"bookmaker": bookmaker, "side": "AWAY", "handicap": -0.5, "odd": 1.96},
        ])
    market = ah.market_consensus(ladder)
    add(
        "BALANCED_MAIN_LINE",
        market is not None and market["home_handicap"] == -0.5,
        "balanced bookmaker line is selected before cross-book consensus",
    )
    away = ah.signal_market(ladder, "AWAY")
    add(
        "AWAY_SIGNAL_PERSPECTIVE",
        away is not None and away["handicap"] == 0.5,
        "home handicap -0.50 becomes AWAY +0.50 for CLV and settlement",
    )
    return checks


def build_report(snapshot_file=SNAPSHOT_FILE, now=None):
    now = now or utc_now()
    runtime_rows = runtime_snapshot_audit(read_rows(snapshot_file))
    checks = invariant_checks()
    counts = Counter(row["status"] for row in runtime_rows)
    passed = all(row["passed"] for row in checks)
    return {
        "schema_version": 1,
        "generated_utc": now.isoformat(),
        "release_candidate": "V3_SHADOW_FROZEN_R2",
        "status": "PASSED" if passed else "FAILED",
        "normalization_version": ah.NORMALIZATION_VERSION,
        "r1_forward_evidence_compatible": False,
        "r1_evidence_action": "ARCHIVE_AND_RESTART_FORWARD_TEST",
        "automatic_real_betting_enabled": False,
        "shadow_only": True,
        "api_requests_used": 0,
        "invariant_checks": checks,
        "runtime_snapshot_rows": len(runtime_rows),
        "runtime_snapshots_normalized": counts["NORMALIZED"],
        "runtime_snapshots_failed_closed": counts["FAIL_CLOSED"],
        "runtime_home_lines_changed": sum(
            row.get("home_line_changed", 0) for row in runtime_rows
        ),
        "runtime_away_lines_changed": sum(
            row.get("away_line_changed", 0) for row in runtime_rows
        ),
        "runtime_raw_values_reparsed": sum(
            row.get("raw_values_reparsed", 0) for row in runtime_rows
        ),
        "runtime_results": runtime_rows,
    }


def main():
    report = build_report()
    write_json(JSON_OUTPUT, report)
    write_csv(CSV_OUTPUT, report["runtime_results"])
    print("\n" + "=" * 72)
    print("FOOTBALL AI V3 R2 — ASIAN HANDICAP AUDIT")
    print("=" * 72)
    print("STATUS:", report["status"])
    for row in report["invariant_checks"]:
        print(f"[{'PASS' if row['passed'] else 'FAIL'}] {row['check']}: {row['detail']}")
    print("RUNTIME SNAPSHOTS:", report["runtime_snapshot_rows"])
    print("HOME LINES CHANGED BY R2:", report["runtime_home_lines_changed"])
    print("AWAY LINES CHANGED BY R2:", report["runtime_away_lines_changed"])
    print("R1 FORWARD EVIDENCE COMPATIBLE: NO")
    print("ACTION: ARCHIVE R1 DERIVED EVIDENCE AND RESTART FORWARD TEST")
    print("API REQUESTS USED: 0")
    print("SHADOW ONLY: YES")
    print("=" * 72)
    print("CSV:", CSV_OUTPUT)
    print("JSON:", JSON_OUTPUT)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
