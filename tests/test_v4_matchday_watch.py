import unittest

import v4_matchday_watch as watch


class V4MatchdayWatchTests(unittest.TestCase):
    def test_pending_lineups_ignores_live_and_confirmed(self):
        document = {"results": [
            {
                "analysis_status": "ANALYZED_PREMATCH",
                "fixture": {
                    "fixture_id": "1", "home_team": "A", "away_team": "B",
                    "minutes_to_kickoff": 44.0,
                },
                "agents": {"lineup": {"status": "NOT_PUBLISHED"}},
            },
            {
                "analysis_status": "ANALYZED_PREMATCH",
                "fixture": {"fixture_id": "2", "minutes_to_kickoff": 35.0},
                "agents": {"lineup": {"status": "CONFIRMED"}},
            },
            {
                "analysis_status": "EXCLUDED_NOT_PREMATCH",
                "fixture": {"fixture_id": "3", "minutes_to_kickoff": -2.0},
                "agents": {"data_quality": {"grade": "LOW"}},
            },
        ]}
        pending = watch.pending_lineups(document)
        self.assertEqual([row["fixture_id"] for row in pending], ["1"])

    def test_next_checkpoint_is_bounded_not_continuous_polling(self):
        pending = [{"minutes_to_kickoff": 49.0}]
        self.assertEqual(watch.next_checkpoint_delay_minutes(pending), 4.0)
        pending = [{"minutes_to_kickoff": 39.0}]
        self.assertEqual(watch.next_checkpoint_delay_minutes(pending), 9.0)
        pending = [{"minutes_to_kickoff": 4.0}]
        self.assertIsNone(watch.next_checkpoint_delay_minutes(pending))


if __name__ == "__main__":
    unittest.main()
