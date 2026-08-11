import unittest

from live_coach_context import (
    band_for_match_number,
    build_live_context,
    collect_missing_observations,
    extract_coach_rows,
)


class LiveCoachContextTests(unittest.TestCase):
    def test_band_is_strong_only_for_matches_two_and_three(self):
        self.assertEqual(band_for_match_number(1), ("MATCH_1", "MODERATE"))
        self.assertEqual(band_for_match_number(2), ("MATCH_2_3", "STRONG"))
        self.assertEqual(band_for_match_number(3), ("MATCH_2_3", "STRONG"))
        self.assertEqual(band_for_match_number(4), ("MATCH_4_5", "WEAK"))
        self.assertEqual(band_for_match_number(6), ("NEUTRAL", "NEUTRAL"))

    def test_offseason_change_starts_new_manager_spell(self):
        observations = [
            {
                "observed_utc": "2026-08-20T15:00:00+00:00",
                "fixture_id": "1",
                "kickoff_utc": "2026-08-20T16:00:00+00:00",
                "home_team": "Alpha",
                "away_team": "Beta",
                "team": "Alpha",
                "coach_id": "2",
                "coach": "New Coach",
                "formation": "4-3-3",
            },
            {
                "observed_utc": "2026-08-27T15:00:00+00:00",
                "fixture_id": "2",
                "kickoff_utc": "2026-08-27T16:00:00+00:00",
                "home_team": "Gamma",
                "away_team": "Alpha",
                "team": "Alpha",
                "coach_id": "2",
                "coach": "New Coach",
                "formation": "4-3-3",
            },
            {
                "observed_utc": "2026-09-03T15:00:00+00:00",
                "fixture_id": "3",
                "kickoff_utc": "2026-09-03T16:00:00+00:00",
                "home_team": "Alpha",
                "away_team": "Delta",
                "team": "Alpha",
                "coach_id": "2",
                "coach": "New Coach",
                "formation": "4-2-3-1",
            },
        ]
        result = build_live_context(observations, {"Alpha": "Old Coach"})
        self.assertEqual([r["new_manager_match_number"] for r in result], [1, 2, 3])
        self.assertEqual([r["shadow_strength"] for r in result], ["MODERATE", "STRONG", "STRONG"])
        self.assertEqual(sum(r["coach_change_flag"] for r in result), 1)
        self.assertTrue(all(r["shadow_only"] == 1 for r in result))

    def test_same_historical_coach_is_neutral(self):
        observations = [{
            "observed_utc": "2026-08-20T15:00:00+00:00",
            "fixture_id": "1",
            "kickoff_utc": "2026-08-20T16:00:00+00:00",
            "home_team": "Alpha",
            "away_team": "Beta",
            "team": "Alpha",
            "coach_id": "1",
            "coach": "Old Coach",
            "formation": "4-3-3",
        }]
        result = build_live_context(observations, {"Alpha": "Old Coach"})
        self.assertEqual(result[0]["new_manager_match_number"], 0)
        self.assertEqual(result[0]["new_manager_band"], "NEUTRAL")
        self.assertEqual(result[0]["coach_change_flag"], 0)

    def test_lineup_payload_extracts_embedded_coach(self):
        fixture = {
            "fixture_id": "9",
            "kickoff": "2026-08-20T16:00:00+00:00",
            "home_team": "Alpha",
            "away_team": "Beta",
        }
        payload = [
            {"team": {"name": "Alpha"}, "coach": {"id": 1, "name": "A"}, "formation": "4-3-3"},
            {"team": {"name": "Beta"}, "coach": {"id": 2, "name": "B"}, "formation": "4-4-2"},
        ]
        rows = extract_coach_rows(fixture, payload, observed_utc="now")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["coach"], "A")
        self.assertEqual(rows[1]["coach"], "B")

    def test_completed_fixture_is_not_refetched(self):
        state = {
            "lineup_results": {"9": {}},
            "fixtures": {"9": {"fixture_id": "9"}},
        }
        existing = [
            {"fixture_id": "9", "team": "Alpha"},
            {"fixture_id": "9", "team": "Beta"},
        ]
        calls = []
        rows, requests = collect_missing_observations(
            state, existing, fetcher=lambda fixture_id: calls.append(fixture_id) or []
        )
        self.assertEqual(rows, [])
        self.assertEqual(requests, 0)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
