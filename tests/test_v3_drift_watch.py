import csv
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import v3_drift_watch as mod


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def historical_rows(count=120):
    rows = []
    shocks = (1.7, 1.8, 2.2, 2.7)
    for index in range(count):
        rows.append({
            "fixture_id": str(index),
            "data_quality": "HIGH",
            "signal": "HOME" if index % 2 == 0 else "AWAY",
            "abs_shock": str(shocks[index % len(shocks)]),
            "signed_close_move_for_lineup": "0.1",
        })
    return rows


def gate_row(index, signal=None, shock=None):
    return {
        "fixture_id": str(index),
        "gate_time_utc": (NOW + timedelta(minutes=index)).isoformat(),
        "kickoff_utc": (NOW + timedelta(days=1, minutes=index)).isoformat(),
        "gate_decision": "SHADOW BET",
        "signal": signal or ("HOME" if index % 2 == 0 else "AWAY"),
        "abs_shock": str(shock if shock is not None else (1.7, 1.8, 2.2, 2.7)[index % 4]),
        "data_quality": "HIGH",
        "market_freshness": "FRESH",
        "health_gate_status": "HEALTHY",
        "entry_handicap": "-0.5",
        "entry_best_odds": "2.0",
        "shadow_only": "1",
    }


def write_csv(path, rows):
    fields = sorted({key for row in rows for key in row})
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class V3DriftWatchTests(unittest.TestCase):
    def test_baseline_uses_only_compatible_high_rows(self):
        rows = historical_rows()
        rows.extend([
            {**rows[0], "fixture_id": "low", "data_quality": "LOW"},
            {**rows[0], "fixture_id": "missing", "signed_close_move_for_lineup": ""},
        ])
        baseline = mod.create_baseline(rows, NOW, "abc")
        self.assertEqual(baseline["metrics"]["sample_size"], 120)
        self.assertEqual(baseline["source_sha256"], "abc")
        self.assertTrue(baseline["shadow_only"])

    def test_missing_research_output_waits_without_crashing(self):
        with TemporaryDirectory() as directory:
            row, summary, _, _ = mod.run_once(
                directory, now=NOW, notify=False
            )
            self.assertEqual(row["status"], "WAITING_FOR_BASELINE")
            self.assertFalse(summary["value_gate_blocked"])
            self.assertTrue((Path(directory) / mod.SUMMARY_FILE).exists())

    def test_baseline_is_frozen_after_first_build(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_csv(root / mod.HISTORICAL_FILE, historical_rows())
            mod.run_once(root, now=NOW, notify=False)
            first = json.loads((root / mod.BASELINE_FILE).read_text(encoding="utf-8"))
            write_csv(root / mod.HISTORICAL_FILE, historical_rows(140))
            _, summary, _, _ = mod.run_once(
                root, now=NOW + timedelta(days=1), notify=False
            )
            second = json.loads((root / mod.BASELINE_FILE).read_text(encoding="utf-8"))
            self.assertEqual(first, second)
            self.assertTrue(summary["baseline"]["source_changed_since_freeze"])

    def test_less_than_30_live_entries_is_collecting(self):
        baseline = mod.create_baseline(historical_rows(), NOW)
        row, summary = mod.evaluate_drift(
            baseline, [gate_row(i) for i in range(29)], [], NOW
        )
        self.assertEqual(row["status"], "COLLECTING")
        self.assertEqual(summary["live"]["eligible_total"], 29)

    def test_matching_live_mix_is_stable(self):
        baseline = mod.create_baseline(historical_rows(), NOW)
        row, summary = mod.evaluate_drift(
            baseline, [gate_row(i) for i in range(40)], [], NOW
        )
        self.assertEqual(row["status"], "STABLE")
        self.assertLess(summary["drift"]["side_psi"], mod.PSI_WARNING)
        self.assertFalse(summary["manual_review_required"])

    def test_one_sided_live_mix_triggers_drift_alert(self):
        baseline = mod.create_baseline(historical_rows(), NOW)
        row, summary = mod.evaluate_drift(
            baseline,
            [gate_row(i, signal="HOME") for i in range(40)],
            [], NOW,
        )
        self.assertEqual(row["status"], "DRIFT_ALERT")
        self.assertIn("SIDE_MIX_DRIFT", row["issue_codes"])
        self.assertFalse(summary["value_gate_blocked"])

    def test_confident_negative_recent_clv_triggers_alert(self):
        baseline = mod.create_baseline(historical_rows(), NOW)
        gates = [gate_row(i) for i in range(40)]
        outcomes = [
            {"fixture_id": str(i), "line_clv": "-0.1"} for i in range(20, 40)
        ]
        row, _ = mod.evaluate_drift(baseline, gates, outcomes, NOW)
        self.assertEqual(row["status"], "DRIFT_ALERT")
        self.assertIn("RECENT_CLV_NEGATIVE_CONFIRMED", row["issue_codes"])

    def test_notification_is_deduplicated_and_recovers(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_csv(root / mod.HISTORICAL_FILE, historical_rows())
            write_csv(
                root / mod.GATE_HISTORY_FILE,
                [gate_row(i, signal="HOME") for i in range(40)],
            )
            messages = []
            first = mod.run_once(
                root, sender=lambda message: messages.append(message) or True,
                now=NOW,
            )
            second = mod.run_once(
                root, sender=lambda message: messages.append(message) or True,
                now=NOW + timedelta(minutes=5),
            )
            write_csv(root / mod.GATE_HISTORY_FILE, [gate_row(i) for i in range(40)])
            recovered = mod.run_once(
                root, sender=lambda message: messages.append(message) or True,
                now=NOW + timedelta(minutes=10),
            )
            self.assertTrue(first[3])
            self.assertFalse(second[3])
            self.assertEqual(recovered[2][0], "RECOVERED")
            self.assertEqual(len(messages), 2)


if __name__ == "__main__":
    unittest.main()
