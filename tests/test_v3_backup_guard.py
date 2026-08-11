import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import v3_backup_guard as mod
import v3_runtime_checkpoint as checkpoint


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def prepare_checkpoint(root):
    (root / "data.csv").write_text("id,value\n1,test\n", encoding="utf-8")
    return checkpoint.checkpoint_once(
        root, now=NOW, filenames=("data.csv",), min_interval_hours=0
    )


class V3BackupGuardTests(unittest.TestCase):
    def test_local_only_is_degraded_and_requests_external_path(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_checkpoint(root)
            status = mod.build_guard(root, environ={}, now=NOW)
            self.assertEqual(status["overall_status"], "DEGRADED")
            self.assertEqual(status["local"]["status"], "VALID")
            self.assertIn(
                "MIRROR_NOT_CONFIGURED",
                {row["code"] for row in status["issues"]},
            )

    def test_external_mirror_is_copied_and_verified(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "project"
            root.mkdir()
            mirror = base / "external-backup"
            prepare_checkpoint(root)
            status = mod.build_guard(
                root, environ={mod.MIRROR_ENV: str(mirror)}, now=NOW
            )
            self.assertEqual(status["overall_status"], "HEALTHY")
            self.assertEqual(status["mirror"]["status"], "COPIED")
            copied = mirror / checkpoint.latest_checkpoint(root).name
            self.assertTrue(checkpoint.verify_checkpoint(copied)["valid"])

    def test_mirror_inside_project_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_checkpoint(root)
            status = mod.build_guard(
                root,
                environ={mod.MIRROR_ENV: str(root / "not-external")},
                now=NOW,
            )
            self.assertIn(
                "MIRROR_INSIDE_PROJECT",
                {row["code"] for row in status["issues"]},
            )

    def test_corrupt_local_checkpoint_is_critical(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_checkpoint(root)
            latest = checkpoint.latest_checkpoint(root)
            latest.write_bytes(b"corrupt")
            status = mod.build_guard(root, environ={}, now=NOW)
            self.assertEqual(status["overall_status"], "CRITICAL")
            self.assertIn(
                "LOCAL_CHECKPOINT_CORRUPT",
                {row["code"] for row in status["issues"]},
            )

    def test_stale_checkpoint_cycle_is_reported(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_checkpoint(root)
            status = mod.build_guard(
                root, environ={}, now=NOW + timedelta(hours=1)
            )
            self.assertIn(
                "CHECKPOINT_RUN_STALE",
                {row["code"] for row in status["issues"]},
            )

    def test_notifications_are_deduplicated_and_recovery_is_sent(self):
        degraded = {
            "overall_status": "DEGRADED",
            "issues": [{
                "severity": "DEGRADED",
                "code": "MIRROR_NOT_CONFIGURED",
                "message": "configure",
            }],
            "local": {"status": "VALID"},
            "mirror": {"status": "NOT_CONFIGURED"},
        }
        state = {"fingerprint": "", "overall_status": ""}
        first = mod.notification_event(degraded, state)
        self.assertEqual(first[0], "ISSUE")
        state = {
            "fingerprint": mod.issue_fingerprint(degraded),
            "overall_status": "DEGRADED",
        }
        self.assertIsNone(mod.notification_event(degraded, state))
        healthy = {
            "overall_status": "HEALTHY",
            "issues": [],
            "local": {"status": "VALID"},
            "mirror": {"status": "SYNCED"},
        }
        recovered = mod.notification_event(healthy, state)
        self.assertEqual(recovered[0], "RECOVERED")


if __name__ == "__main__":
    unittest.main()
