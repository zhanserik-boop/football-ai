import unittest
from datetime import datetime, timezone

import schedule_priority_agent as spa


class SchedulePriorityAgentTests(unittest.TestCase):
    def test_competition_priority_orders_champions_league_above_epl(self):
        self.assertGreater(
            spa.competition_priority("UEFA Champions League", "League Stage - 1"),
            spa.competition_priority("Premier League", "Regular Season - 3"),
        )

    def test_knockout_round_increases_priority(self):
        group = spa.competition_priority("UEFA Champions League", "League Stage - 6")
        semi = spa.competition_priority("UEFA Champions League", "Semi-finals")
        self.assertGreater(semi, group)

    def test_three_days_to_high_priority_match_is_high_rotation_risk(self):
        pressure, risk, _ = spa.classify_rotation_risk(
            current_priority=60,
            next_priority=90,
            hours_to_next=72,
            matches_next_7d=2,
        )
        self.assertEqual(pressure, "HIGH")
        self.assertEqual(risk, "HIGH")

    def test_same_priority_match_in_four_days_is_not_high_rotation_risk(self):
        _, risk, _ = spa.classify_rotation_risk(
            current_priority=60,
            next_priority=60,
            hours_to_next=96,
            matches_next_7d=1,
        )
        self.assertEqual(risk, "LOW")

    def test_agent_is_shadow_context_not_betting_rule(self):
        fixture = {
            "fixture_id": 1,
            "kickoff": datetime(2026, 9, 12, 14, 0, tzinfo=timezone.utc),
            "competition": "Premier League",
            "round": "Regular Season - 4",
            "home_id": 49,
            "home_name": "Chelsea",
            "away_id": 40,
            "away_name": "Liverpool",
        }
        schedule_items = [
            {
                "fixture": {"id": 2, "date": "2026-09-15T19:00:00+00:00"},
                "league": {
                    "id": 2,
                    "name": "UEFA Champions League",
                    "round": "League Stage - 1",
                },
                "teams": {
                    "home": {"id": 49, "name": "Chelsea"},
                    "away": {"id": 541, "name": "Real Madrid"},
                },
            }
        ]
        ctx = spa.build_team_context(fixture, 49, "Chelsea", schedule_items)
        self.assertEqual(ctx["rotation_risk"], "HIGH")
        self.assertEqual(ctx["next_competition"], "UEFA Champions League")
        self.assertAlmostEqual(float(ctx["hours_to_next_match"]), 77.0, places=2)


if __name__ == "__main__":
    unittest.main()
