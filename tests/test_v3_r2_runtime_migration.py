import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import v3_r2_runtime_migration as migration


NOW = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)


class V3R2RuntimeMigrationTests(unittest.TestCase):
    def test_preview_does_not_move_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "market_snapshots_v2.csv"
            path.write_text("x\n1\n", encoding="utf-8")
            report = migration.migrate(root, now=NOW, apply=False)
            self.assertEqual(report["status"], "APPLY_REQUIRED")
            self.assertTrue(path.exists())

    def test_apply_archives_and_verifies_without_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "market_snapshots_v2.csv").write_text("x\n1\n", encoding="utf-8")
            (root / ".env").write_text("SECRET=hidden", encoding="utf-8")
            report = migration.migrate(root, now=NOW, apply=True)
            self.assertEqual(report["status"], "COMPLETED")
            self.assertEqual(report["files_archived"], 1)
            archive = root / report["archive_directory"]
            self.assertTrue((archive / "market_snapshots_v2.csv").exists())
            self.assertFalse((archive / ".env").exists())
            self.assertTrue((root / ".env").exists())
            self.assertTrue(migration.migration_complete(root))

    def test_second_apply_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = migration.migrate(root, now=NOW, apply=True)
            second = migration.migrate(root, now=NOW, apply=True)
            self.assertEqual(first["status"], "COMPLETED")
            self.assertEqual(second["status"], "ALREADY_COMPLETED")


if __name__ == "__main__":
    unittest.main()
