import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import v3_emergency_drill as mod


NOW = datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)


class V3EmergencyDrillTests(unittest.TestCase):
    def test_full_drill_passes_in_isolation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            report = mod.build_drill(root, now=NOW)
            self.assertEqual(report["status"], "PASSED")
            self.assertEqual(report["scenarios_passed"], 4)
            self.assertEqual(report["scenarios_total"], 4)
            self.assertEqual(report["real_telegram_messages_sent"], 0)
            self.assertEqual(report["football_data_api_requests_used"], 0)
            self.assertEqual(
                sorted(path.name for path in root.iterdir()),
                [mod.OUTPUT_FILE],
            )

    def test_report_is_persisted_as_json(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            report = mod.build_drill(root, now=NOW)
            stored = json.loads(
                (root / mod.OUTPUT_FILE).read_text(encoding="utf-8")
            )
            self.assertEqual(stored, report)
            self.assertTrue(stored["isolated_temporary_workspace"])
            self.assertFalse(stored["live_runtime_files_modified"])

    def test_failed_scenario_is_reported_without_stopping_other_checks(self):
        result = mod.run_scenario(
            "EXPECTED_FAILURE",
            lambda: mod.require(False, "synthetic failure"),
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("synthetic failure", result["detail"])


if __name__ == "__main__":
    unittest.main()
