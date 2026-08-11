import unittest

import historical_coach_builder as hcb


class HistoricalCoachBuilderTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            "100": {
                "season": "2024",
                "fixture_id": "100",
                "date": "2024-08-10",
                "match_home": "Chelsea",
                "match_away": "Liverpool",
                "team_ids": {"49", "40"},
            },
            "101": {
                "season": "2024",
                "fixture_id": "101",
                "date": "2024-08-17",
                "match_home": "Arsenal",
                "match_away": "Chelsea",
                "team_ids": {"42", "49"},
            },
        }

    def test_fixture_payload_extracts_two_coaches(self):
        payload = {
            "fixture": {"id": 100},
            "teams": {
                "home": {"id": 49, "name": "Chelsea"},
                "away": {"id": 40, "name": "Liverpool"},
            },
            "lineups": [
                {
                    "team": {"id": 49, "name": "Chelsea"},
                    "formation": "4-2-3-1",
                    "coach": {"id": 1, "name": "Coach A"},
                },
                {
                    "team": {"id": 40, "name": "Liverpool"},
                    "formation": "4-3-3",
                    "coach": {"id": 2, "name": "Coach B"},
                },
            ],
        }
        rows = hcb.parse_fixture_item(payload, self.catalog["100"], "2026-08-11T00:00:00+00:00")
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["coach"] for r in rows}, {"Coach A", "Coach B"})
        self.assertEqual({r["coach_status"] for r in rows}, {"OK"})

    def test_missing_coach_is_explicitly_unavailable_not_invented(self):
        payload = {
            "fixture": {"id": 100},
            "teams": {
                "home": {"id": 49, "name": "Chelsea"},
                "away": {"id": 40, "name": "Liverpool"},
            },
            "lineups": [
                {"team": {"id": 49}, "formation": "4-3-3", "coach": {}},
                {"team": {"id": 40}, "formation": "4-3-3", "coach": {}},
            ],
        }
        rows = hcb.parse_fixture_item(payload, self.catalog["100"], "x")
        self.assertEqual({r["coach_status"] for r in rows}, {"UNAVAILABLE"})
        self.assertTrue(all(r["coach"] == "" for r in rows))

    def test_completed_fixture_is_skipped_on_resume(self):
        existing = [
            {"fixture_id": "100", "coach_status": "OK"},
            {"fixture_id": "100", "coach_status": "OK"},
        ]
        missing, requests_count = hcb.build_plan(self.catalog, existing, batch_size=5)
        self.assertEqual(missing, ["101"])
        self.assertEqual(requests_count, 1)

    def test_unavailable_is_only_retried_when_requested(self):
        existing = [
            {"fixture_id": "100", "coach_status": "UNAVAILABLE"},
            {"fixture_id": "100", "coach_status": "UNAVAILABLE"},
        ]
        missing_default, _ = hcb.build_plan(self.catalog, existing, retry_unavailable=False)
        missing_retry, _ = hcb.build_plan(self.catalog, existing, retry_unavailable=True)
        self.assertNotIn("100", missing_default)
        self.assertIn("100", missing_retry)

    def test_batch_request_estimate_is_ceiling(self):
        catalog = {
            str(i): {
                "season": "2024",
                "fixture_id": str(i),
                "date": "",
                "match_home": "",
                "match_away": "",
                "team_ids": set(),
            }
            for i in range(1, 13)
        }
        missing, requests_count = hcb.build_plan(catalog, [], batch_size=5)
        self.assertEqual(len(missing), 12)
        self.assertEqual(requests_count, 3)


if __name__ == "__main__":
    unittest.main()
