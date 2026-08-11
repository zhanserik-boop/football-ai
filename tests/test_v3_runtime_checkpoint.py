import json
import unittest
import zipfile
from datetime import datetime, timedelta, timezone

import v3_runtime_checkpoint as mod


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


class V3RuntimeCheckpointTests(unittest.TestCase):
    def test_no_runtime_data_does_not_create_empty_archive(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            status = mod.checkpoint_once(directory, now=NOW, filenames=("data.csv",))
            self.assertEqual(status["status"], "NO_DATA")
            self.assertIsNone(mod.latest_checkpoint(directory))

    def test_checkpoint_is_verified_and_never_contains_env(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data.csv").write_text("id,value\n1,test\n", encoding="utf-8")
            (root / ".env").write_text("SECRET=never-copy", encoding="utf-8")
            status = mod.checkpoint_once(
                root, now=NOW, filenames=("data.csv",), min_interval_hours=0
            )
            self.assertEqual(status["status"], "CREATED")
            checkpoint = mod.latest_checkpoint(root)
            result = mod.verify_checkpoint(checkpoint)
            self.assertTrue(result["valid"])
            with zipfile.ZipFile(checkpoint) as archive:
                self.assertNotIn(".env", archive.namelist())
                self.assertEqual(archive.read("data.csv"), b"id,value\n1,test\n")
            self.assertFalse(result["manifest"]["secrets_included"])

    def test_unchanged_source_does_not_create_duplicate(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data.csv").write_text("id\n1\n", encoding="utf-8")
            mod.checkpoint_once(root, now=NOW, filenames=("data.csv",))
            status = mod.checkpoint_once(
                root, now=NOW + timedelta(hours=1), filenames=("data.csv",)
            )
            self.assertEqual(status["status"], "UNCHANGED")
            self.assertEqual(len(mod.archive_paths(root)), 1)

    def test_changed_source_is_throttled_then_checkpointed(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "data.csv"
            path.write_text("id\n1\n", encoding="utf-8")
            mod.checkpoint_once(root, now=NOW, filenames=("data.csv",))
            path.write_text("id\n1\n2\n", encoding="utf-8")
            deferred = mod.checkpoint_once(
                root, now=NOW + timedelta(hours=1), filenames=("data.csv",)
            )
            self.assertEqual(deferred["status"], "DEFERRED")
            created = mod.checkpoint_once(
                root, now=NOW + timedelta(hours=7), filenames=("data.csv",)
            )
            self.assertEqual(created["status"], "CREATED")
            self.assertEqual(len(mod.archive_paths(root)), 2)

    def test_corruption_is_detected(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as directory:
            path = Path(directory) / "broken.zip"
            path.write_bytes(b"not-a-zip")
            result = mod.verify_checkpoint(path)
            self.assertFalse(result["valid"])
            self.assertTrue(result["errors"])

    def test_restore_uses_new_directory_and_refuses_overwrite(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data.csv").write_text("id\n1\n", encoding="utf-8")
            mod.checkpoint_once(root, now=NOW, filenames=("data.csv",))
            checkpoint = mod.latest_checkpoint(root)
            target = root / "restored"
            result = mod.restore_checkpoint(checkpoint, target)
            self.assertTrue(result["verified"])
            self.assertEqual((target / "data.csv").read_text(encoding="utf-8"), "id\n1\n")
            with self.assertRaises(FileExistsError):
                mod.restore_checkpoint(checkpoint, target)

    def test_retention_removes_only_old_matching_archives(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "data.csv"
            for index in range(3):
                path.write_text(f"id\n{index}\n", encoding="utf-8")
                mod.checkpoint_once(
                    root,
                    now=NOW + timedelta(hours=index),
                    filenames=("data.csv",),
                    min_interval_hours=0,
                    retention=2,
                )
            self.assertEqual(len(mod.archive_paths(root)), 2)
            self.assertTrue((root / "data.csv").exists())


if __name__ == "__main__":
    unittest.main()
