import unittest
from datetime import date
from unittest.mock import patch

import download_old_odds as updater


class DownloadOldOddsTests(unittest.TestCase):
    def test_source_is_not_due_before_season_start(self):
        self.assertFalse(updater.source_check_due(date(2026, 8, 20)))

    def test_source_becomes_due_on_season_start(self):
        self.assertTrue(updater.source_check_due(date(2026, 8, 21)))

    def test_main_makes_no_http_request_before_season(self):
        with (
            patch.object(updater, "source_check_due", return_value=False),
            patch.object(updater, "quarantine_existing_invalid_file"),
            patch.object(updater.requests, "get") as request,
        ):
            updater.main()
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
