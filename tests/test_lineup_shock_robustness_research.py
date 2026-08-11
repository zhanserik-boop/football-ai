import unittest

import pandas as pd

import lineup_shock_robustness_research as mod


class LineupShockRobustnessResearchTests(unittest.TestCase):
    def test_home_handicap_sign_is_side_aware(self):
        lineup = pd.DataFrame([
            {
                "season": 2024, "date": "2024-08-01", "home_team": "A", "away_team": "B",
                "signal": "HOME", "abs_shock": 1.8, "data_quality": "HIGH",
            },
            {
                "season": 2024, "date": "2024-08-02", "home_team": "C", "away_team": "D",
                "signal": "AWAY", "abs_shock": 2.2, "data_quality": "HIGH",
            },
        ])
        market = pd.DataFrame([
            {
                "season": 2024, "date": "2024-08-01", "home_team": "A", "away_team": "B",
                "open_ah_home_line": -0.5, "close_ah_home_line": -0.75, "close_move_home": -0.25,
            },
            {
                "season": 2024, "date": "2024-08-02", "home_team": "C", "away_team": "D",
                "open_ah_home_line": -0.5, "close_ah_home_line": -0.25, "close_move_home": 0.25,
            },
        ])
        out = mod.attach_market(lineup, market)
        self.assertAlmostEqual(out.loc[0, "signed_close_move_for_lineup"], 0.25)
        self.assertAlmostEqual(out.loc[1, "signed_close_move_for_lineup"], 0.25)

    def test_shock_buckets(self):
        self.assertEqual(mod.shock_bucket(1.5), "1.5_TO_2.0")
        self.assertEqual(mod.shock_bucket(2.0), "2.0_TO_2.5")
        self.assertEqual(mod.shock_bucket(2.5), "GE_2.5")

    def test_wilson_interval_contains_observed_rate(self):
        low, high = mod.wilson_interval(8, 10)
        self.assertLess(low, 0.8)
        self.assertGreater(high, 0.8)

    def _passing_summary(self):
        rows = [
            {"scope": "ALL", "rows": 120, "avg_signed_close_move": 0.10,
             "avg_signed_move_ci_low": 0.04, "large_move_hit_ci_low": 0.60,
             "nonflat_direction_hit": 0.68},
            {"scope": "SEASON_2023", "rows": 40, "avg_signed_close_move": 0.08,
             "avg_signed_move_ci_low": 0.01, "large_move_hit_ci_low": 0.50,
             "nonflat_direction_hit": 0.62},
            {"scope": "SEASON_2024", "rows": 40, "avg_signed_close_move": 0.12,
             "avg_signed_move_ci_low": 0.02, "large_move_hit_ci_low": 0.50,
             "nonflat_direction_hit": 0.70},
            {"scope": "SEASON_2025", "rows": 40, "avg_signed_close_move": 0.10,
             "avg_signed_move_ci_low": 0.01, "large_move_hit_ci_low": 0.50,
             "nonflat_direction_hit": 0.66},
            {"scope": "SIDE_HOME", "rows": 60, "avg_signed_close_move": 0.09,
             "avg_signed_move_ci_low": 0.01, "large_move_hit_ci_low": 0.50,
             "nonflat_direction_hit": 0.64},
            {"scope": "SIDE_AWAY", "rows": 60, "avg_signed_close_move": 0.11,
             "avg_signed_move_ci_low": 0.01, "large_move_hit_ci_low": 0.50,
             "nonflat_direction_hit": 0.69},
            {"scope": "QUALITY_HIGH", "rows": 110, "avg_signed_close_move": 0.11,
             "avg_signed_move_ci_low": 0.04, "large_move_hit_ci_low": 0.61,
             "nonflat_direction_hit": 0.70},
            {"scope": "QUALITY_MEDIUM", "rows": 10, "avg_signed_close_move": -0.01,
             "avg_signed_move_ci_low": -0.10, "large_move_hit_ci_low": 0.20,
             "nonflat_direction_hit": 0.40},
        ]
        return pd.DataFrame(rows)

    def test_stability_gate_passes_balanced_history(self):
        passed, _ = mod.stability_gate(self._passing_summary())
        self.assertTrue(passed)

    def test_negative_season_blocks_candidate(self):
        summary = self._passing_summary()
        summary.loc[summary["scope"] == "SEASON_2024", "avg_signed_close_move"] = -0.01
        passed, reason = mod.stability_gate(summary)
        self.assertFalse(passed)
        self.assertIn("Season", reason)


if __name__ == "__main__":
    unittest.main()
