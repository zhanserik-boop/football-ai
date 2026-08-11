import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import v3_daily_digest as mod


NOW = datetime(2026, 8, 11, 5, 30, tzinfo=timezone.utc)  # 10:30 Almaty


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


class V3DailyDigestTests(unittest.TestCase):
    def test_before_schedule_is_not_due(self):
        hour, _, tz = mod.schedule_config({})
        early = datetime(2026, 8, 11, 4, 0, tzinfo=timezone.utc)
        due, _, reason = mod.is_due(early, {}, hour, tz)
        self.assertFalse(due)
        self.assertEqual(reason, "BEFORE_SCHEDULE")

    def test_successful_send_happens_only_once_per_local_day(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            messages = []
            first, _ = mod.run_once(
                root, sender=lambda message: messages.append(message) or True,
                now=NOW, environ={},
            )
            second, _ = mod.run_once(
                root, sender=lambda message: messages.append(message) or True,
                now=NOW, environ={},
            )
            self.assertEqual(first["status"], "SENT")
            self.assertEqual(second["status"], "NOT_DUE")
            self.assertEqual(len(messages), 1)

    def test_failed_send_is_retried(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            failed, _ = mod.run_once(
                root, sender=lambda message: False, now=NOW, environ={}
            )
            self.assertEqual(failed["status"], "SEND_FAILED")
            self.assertFalse((root / mod.STATE_FILE).exists())
            retried, _ = mod.run_once(
                root, sender=lambda message: True, now=NOW, environ={}
            )
            self.assertEqual(retried["status"], "SENT")

    def test_preview_never_sends_or_advances_state(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            status, message = mod.run_once(
                root, sender=lambda text: self.fail("sender called"),
                preview=True, now=NOW, environ={},
            )
            self.assertEqual(status["status"], "PREVIEW")
            self.assertIn("ЕЖЕДНЕВНЫЙ ОТЧЁТ", message)
            self.assertFalse((root / mod.STATE_FILE).exists())

    def test_digest_aggregates_current_statuses(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / mod.READINESS_FILE, {"overall_status": "READY"})
            write_json(root / mod.BACKUP_FILE, {
                "overall_status": "HEALTHY",
                "local": {"status": "VALID"},
                "mirror": {"status": "SYNCED"},
            })
            write_json(root / mod.SUPERVISOR_FILE, {"overall_status": "HEALTHY"})
            write_json(root / mod.FORWARD_FILE, {
                "status": "COLLECTING_CLV",
                "funnel": {"with_clv": 3, "settled": 2},
                "evidence": {"avg_line_clv": 0.1, "roi": 0.05},
            })
            write_json(root / mod.DRIFT_FILE, {
                "status": "STABLE",
                "live": {"eligible_total": 31},
                "drift": {"side_psi": 0.02, "shock_band_psi": 0.03},
            })
            _, _, tz = mod.schedule_config({})
            metrics = mod.build_metrics(root, NOW, tz)
            self.assertEqual(metrics["readiness"], "READY")
            self.assertEqual(metrics["backup_mirror"], "SYNCED")
            self.assertEqual(metrics["with_clv"], 3)
            self.assertEqual(metrics["drift_status"], "STABLE")
            self.assertEqual(metrics["drift_live_n"], 31)

    def test_nearest_fixture_is_shown(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / mod.MONITOR_STATE_FILE, {
                "fixtures": {
                    "10": {
                        "kickoff": "2026-08-11T07:00:00+00:00",
                        "home_team": "Arsenal",
                        "away_team": "Chelsea",
                    }
                }
            })
            _, offset, tz = mod.schedule_config({})
            metrics = mod.build_metrics(root, NOW, tz)
            message = mod.build_message(metrics, NOW.astimezone(tz), offset)
            self.assertEqual(metrics["fixtures_48h"], 1)
            self.assertIn("Arsenal — Chelsea", message)

    def test_invalid_schedule_config_uses_almaty_defaults(self):
        hour, offset, _ = mod.schedule_config({
            mod.REPORT_HOUR_ENV: "not-an-hour",
            mod.UTC_OFFSET_ENV: "99",
        })
        self.assertEqual(hour, 10)
        self.assertEqual(offset, 5.0)


if __name__ == "__main__":
    unittest.main()
