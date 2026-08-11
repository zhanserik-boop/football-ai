import unittest
from datetime import datetime, timedelta, timezone

from shadow_value_gate_v1 import build_gate_rows, market_freshness, shock_band


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def ah_row(**updates):
    row = {
        "fixture_id": "10", "kickoff_utc": "2026-08-11T12:45:00+00:00",
        "minutes_to_kickoff": "45", "home_team": "Arsenal",
        "away_team": "Chelsea", "signal": "HOME", "abs_shock": "1.8",
        "data_quality": "HIGH", "decision": "BET",
    }
    row.update(updates)
    return row


def timeline(**updates):
    row = {
        "fixture_id": "10", "snapshot_utc": "2026-08-11T11:55:00+00:00",
        "phase": "POST_XI", "freshness_status": "POST_XI_CHANGED",
        "home_bookmakers": "3", "away_bookmakers": "3",
    }
    row.update(updates)
    return row


class ShadowValueGateV1Tests(unittest.TestCase):
    def test_clean_candidate_becomes_shadow_bet(self):
        rows = build_gate_rows([ah_row()], [timeline()], [], [], now=NOW)
        self.assertEqual(rows[0]["gate_decision"], "SHADOW BET")
        self.assertEqual(rows[0]["directional_clv_prior"], "ROBUST_HIGH_ONLY")

    def test_unproven_market_cannot_pass_gate(self):
        rows = build_gate_rows(
            [ah_row()],
            [timeline(freshness_status="POST_XI_UNCHANGED_OR_UNPROVEN")],
            [], [], now=NOW,
        )
        self.assertEqual(rows[0]["gate_decision"], "WATCH")
        self.assertEqual(rows[0]["market_freshness"], "UNPROVEN")

    def test_medium_quality_is_excluded_after_robustness_result(self):
        rows = build_gate_rows(
            [ah_row(data_quality="MEDIUM")], [timeline()], [], [], now=NOW
        )
        self.assertEqual(rows[0]["gate_decision"], "WATCH")

    def test_unstable_shock_band_is_watch(self):
        rows = build_gate_rows(
            [ah_row(abs_shock="2.2")], [timeline()], [], [], now=NOW
        )
        self.assertEqual(shock_band(2.2), "UNSTABLE_2.0_2.5")
        self.assertEqual(rows[0]["gate_decision"], "WATCH")

    def test_double_context_conflict_downgrades_to_watch(self):
        coaches = [{
            "fixture_id": "10", "team": "Chelsea", "shadow_strength": "STRONG",
            "new_manager_match_number": "2",
        }]
        matchups = [{
            "date": "2025-12-01", "home_team": "Arsenal", "away_team": "Chelsea",
            "matchup_xg_balance_edge_home": "-0.5",
        }]
        rows = build_gate_rows([ah_row()], [timeline()], coaches, matchups, now=NOW)
        self.assertEqual(rows[0]["context_score"], -2)
        self.assertEqual(rows[0]["gate_decision"], "WATCH")

    def test_ah_late_is_always_pass(self):
        rows = build_gate_rows(
            [ah_row(decision="LATE")], [timeline()], [], [], now=NOW
        )
        self.assertEqual(rows[0]["gate_decision"], "PASS")

    def test_market_age_and_bookmaker_depth_are_hard_freshness_checks(self):
        stale = timeline(snapshot_utc=(NOW - timedelta(minutes=13)).isoformat())
        self.assertEqual(market_freshness(stale, "HOME", NOW)[0], "STALE")
        self.assertEqual(
            market_freshness(timeline(home_bookmakers="1"), "HOME", NOW)[0],
            "THIN",
        )


if __name__ == "__main__":
    unittest.main()
