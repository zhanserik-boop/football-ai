import csv
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import v3_r2_ah_audit as audit


class V3R2AHAuditTests(unittest.TestCase):
    def test_runtime_audit_reparses_raw_value_and_exposes_away_reversal(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "snapshots.csv"
            fields = [
                "fixture_id", "snapshot_utc", "bookmaker", "value",
                "parsed_side", "parsed_handicap", "odd",
            ]
            rows = []
            for book in ("A", "B", "C"):
                rows.extend([
                    ["1", "2026-08-12T10:00:00+00:00", book, "Home -0.5", "HOME", "-0.5", "1.95"],
                    ["1", "2026-08-12T10:00:00+00:00", book, "Away -0.5", "AWAY", "-0.5", "1.95"],
                ])
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(fields)
                writer.writerows(rows)
            report = audit.build_report(
                path, now=datetime(2026, 8, 12, tzinfo=timezone.utc)
            )
            self.assertEqual(report["status"], "PASSED")
            self.assertEqual(report["runtime_snapshot_rows"], 1)
            self.assertEqual(report["runtime_home_lines_changed"], 0)
            self.assertEqual(report["runtime_away_lines_changed"], 1)
            self.assertEqual(report["runtime_results"][0]["r2_away_handicap"], 0.5)


if __name__ == "__main__":
    unittest.main()
