import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import v3_external_supervisor as mod


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def monitor_state(age_minutes=1, status="OK", errors=0):
    heartbeat = NOW - timedelta(minutes=age_minutes)
    return {
        "health": {
            "last_cycle_completed_utc": heartbeat.isoformat(),
            "last_cycle_status": status,
            "consecutive_errors": errors,
            "last_error": "test error" if status == "ERROR" else "",
        }
    }


class V3ExternalSupervisorTests(unittest.TestCase):
    def test_missing_expectation_is_unarmed(self):
        with TemporaryDirectory() as directory:
            status = mod.build_status(directory, now=NOW)
            self.assertEqual(status["overall_status"], "UNARMED")

    def test_clean_stop_is_standby_without_alert(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            mod.set_expected_running(False, root=root, now=NOW)
            status = mod.build_status(root, now=NOW)
            self.assertEqual(status["overall_status"], "STANDBY")
            self.assertIsNone(mod.notification_event(status, {}))

    def test_startup_grace_prevents_false_alarm(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            mod.set_expected_running(
                True, root=root, now=NOW - timedelta(minutes=5)
            )
            status = mod.build_status(root, now=NOW)
            self.assertEqual(status["overall_status"], "STARTING")

    def test_stale_heartbeat_after_grace_is_critical(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            mod.set_expected_running(
                True, root=root, now=NOW - timedelta(minutes=30)
            )
            write_json(root / mod.MONITOR_STATE_FILE, monitor_state(age_minutes=20))
            status = mod.build_status(root, now=NOW)
            self.assertEqual(status["overall_status"], "CRITICAL")
            self.assertIn(
                "MONITOR_HEARTBEAT_STALE",
                {row["code"] for row in status["issues"]},
            )

    def test_fresh_heartbeat_is_healthy(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            mod.set_expected_running(
                True, root=root, now=NOW - timedelta(minutes=30)
            )
            write_json(root / mod.MONITOR_STATE_FILE, monitor_state())
            status = mod.build_status(root, now=NOW)
            self.assertEqual(status["overall_status"], "HEALTHY")

    def test_notifications_deduplicate_and_recover(self):
        critical = {
            "overall_status": "CRITICAL",
            "issues": [{
                "severity": "CRITICAL",
                "code": "MONITOR_HEARTBEAT_STALE",
                "message": "stale",
            }],
        }
        first = mod.notification_event(critical, {})
        self.assertEqual(first[0], "ISSUE")
        state = {
            "overall_status": "CRITICAL",
            "fingerprint": mod.issue_fingerprint(critical),
        }
        self.assertIsNone(mod.notification_event(critical, state))
        healthy = {"overall_status": "HEALTHY", "issues": []}
        recovered = mod.notification_event(healthy, state)
        self.assertEqual(recovered[0], "RECOVERED")


if __name__ == "__main__":
    unittest.main()
