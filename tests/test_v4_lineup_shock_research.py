import unittest
from datetime import datetime, timedelta, timezone

import v4_lineup_shock_research as research


def profile(team_id, quality="HIGH"):
    players = []
    for player_id in range(team_id * 100, team_id * 100 + 14):
        players.append({
            "player_id": player_id,
            "player_name": f"P{player_id}",
            "minutes_weighted": 1000,
            "importance": 1.0 if player_id < team_id * 100 + 11 else 0.4,
        })
    return {
        "team_id": team_id, "data_quality": quality,
        "baseline_valid": True, "baseline_formation": "4-3-3",
        "baseline_player_ids": [row["player_id"] for row in players[:11]],
        "baseline_score": 11.0, "players": players,
    }


def confirmed_result(home_ids, away_ids):
    return {
        "fixture": {
            "fixture_id": "1", "home_team_id": 1, "away_team_id": 2,
            "home_team": "Home", "away_team": "Away",
        },
        "agents": {
            "quant": {"fair_home_ah": -0.5},
            "lineup": {
                "status": "CONFIRMED", "home_starter_ids": home_ids,
                "away_starter_ids": away_ids,
                "home_starter_names": [str(x) for x in home_ids],
                "away_starter_names": [str(x) for x in away_ids],
            },
        },
    }


class V4LineupShockResearchTests(unittest.TestCase):
    def test_weaker_home_xi_moves_fair_line_against_home(self):
        home_ids = list(range(100, 108)) + [111, 112, 113]
        away_ids = list(range(200, 211))
        row = research.evaluate_result(
            confirmed_result(home_ids, away_ids),
            {"1": profile(1), "2": profile(2)},
        )
        self.assertEqual(row["status"], "READY_RESEARCH")
        self.assertLess(row["home_goal_margin_adjustment_proxy"], 0)
        self.assertGreater(row["adjusted_fair_home_ah_proxy"], -0.5)
        self.assertFalse(row["approved_for_value_gate"])

    def test_medium_profile_is_blocked(self):
        row = research.evaluate_result(
            confirmed_result(list(range(100, 111)), list(range(200, 211))),
            {"1": profile(1, "MEDIUM"), "2": profile(2)},
        )
        self.assertEqual(row["status"], "BLOCKED")

    def test_missing_confirmed_xi_waits(self):
        row = research.evaluate_result(
            {
                "fixture": {"fixture_id": "1"},
                "agents": {"lineup": {"status": "NOT_PUBLISHED"}},
            },
            {},
        )
        self.assertEqual(row["status"], "WAITING_FOR_CONFIRMED_XI")

    def test_espn_research_lineup_maps_players_by_name(self):
        result = confirmed_result([], [])
        result["agents"]["lineup"]["status"] = "NOT_PUBLISHED"
        audit = {
            "status": "ESPN_ONLY_RESEARCH",
            "espn_status": "PUBLISHED_XI",
            "espn_home_starters": [
                {"source_player_id": f"e{value}", "player_name": f"P{value}"}
                for value in range(100, 111)
            ],
            "espn_away_starters": [
                {"source_player_id": f"e{value}", "player_name": f"P{value}"}
                for value in range(200, 211)
            ],
        }
        row = research.evaluate_result(
            result, {"1": profile(1), "2": profile(2)}, audit
        )
        self.assertEqual(row["status"], "READY_RESEARCH")
        self.assertEqual(row["lineup_source"], "ESPN_RESEARCH")
        self.assertEqual(row["home_lineup"]["valued_starters"], 11)

    def test_source_conflict_blocks_research(self):
        row = research.evaluate_result(
            confirmed_result(list(range(100, 111)), list(range(200, 211))),
            {"1": profile(1), "2": profile(2)},
            {"status": "SOURCE_CONFLICT"},
        )
        self.assertEqual(row["status"], "BLOCKED")
        self.assertEqual(row["lineup_source"], "SOURCE_CONFLICT")

    def test_stale_lineup_audit_is_ignored(self):
        now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
        document = {
            "generated_utc": (now - timedelta(minutes=121)).isoformat(),
            "results": [{"fixture_id": "1", "status": "ESPN_ONLY_RESEARCH"}],
        }
        self.assertEqual(research.fresh_audit_map(document, now=now), {})


if __name__ == "__main__":
    unittest.main()
