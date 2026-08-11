import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import v3_freeze_guard as mod


NOW = datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc)


def write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding="utf-8")


def passed_drill():
    return {
        "status": "PASSED",
        "scenarios_passed": 3,
        "scenarios_total": 3,
        "real_telegram_messages_sent": 0,
        "live_runtime_files_modified": False,
        "secrets_included": False,
        "football_data_api_requests_used": 0,
    }


def prepare(root, with_drill=True):
    frozen = Path(root) / "engine.py"
    frozen.write_text("print('frozen')\n", encoding="utf-8")
    write_json(Path(root) / "manifest.json", {
        "schema_version": 1,
        "release": "V3_SHADOW_FROZEN",
        "frozen_at_utc": NOW.isoformat(),
        "source_commit": "test",
        "files": {"engine.py": mod.git_blob_sha1(frozen)},
    })
    if with_drill:
        write_json(Path(root) / "drill.json", passed_drill())


class V3FreezeGuardTests(unittest.TestCase):
    def test_approved_files_and_drill_allow_startup(self):
        with TemporaryDirectory() as directory:
            prepare(directory)
            report = mod.build_guard(
                directory, now=NOW,
                manifest_file="manifest.json", drill_file="drill.json",
            )
            self.assertEqual(report["status"], "FROZEN")
            self.assertTrue(report["startup_allowed"])
            self.assertEqual(report["files_verified"], 1)
            self.assertFalse(report["automatic_real_betting_enabled"])

    def test_changed_frozen_file_blocks_startup(self):
        with TemporaryDirectory() as directory:
            prepare(directory)
            (Path(directory) / "engine.py").write_text("changed\n", encoding="utf-8")
            report = mod.build_guard(
                directory, now=NOW,
                manifest_file="manifest.json", drill_file="drill.json",
            )
            self.assertEqual(report["status"], "BLOCKED")
            self.assertIn("FROZEN_FILE_CHANGED", {row["code"] for row in report["issues"]})

    def test_missing_frozen_file_blocks_startup(self):
        with TemporaryDirectory() as directory:
            prepare(directory)
            (Path(directory) / "engine.py").unlink()
            report = mod.build_guard(
                directory, now=NOW,
                manifest_file="manifest.json", drill_file="drill.json",
            )
            self.assertIn("FROZEN_FILE_MISSING", {row["code"] for row in report["issues"]})

    def test_local_emergency_drill_is_required(self):
        with TemporaryDirectory() as directory:
            prepare(directory, with_drill=False)
            report = mod.build_guard(
                directory, now=NOW,
                manifest_file="manifest.json", drill_file="drill.json",
            )
            self.assertFalse(report["startup_allowed"])
            self.assertIn("EMERGENCY_DRILL_REQUIRED", {row["code"] for row in report["issues"]})

    def test_unsafe_drill_report_blocks_startup(self):
        with TemporaryDirectory() as directory:
            prepare(directory)
            unsafe = passed_drill()
            unsafe["real_telegram_messages_sent"] = 1
            write_json(Path(directory) / "drill.json", unsafe)
            report = mod.build_guard(
                directory, now=NOW,
                manifest_file="manifest.json", drill_file="drill.json",
            )
            self.assertIn("EMERGENCY_DRILL_UNSAFE", {row["code"] for row in report["issues"]})


if __name__ == "__main__":
    unittest.main()
