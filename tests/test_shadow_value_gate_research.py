import unittest

import pandas as pd

import shadow_value_gate_research as mod


class ShadowValueGateResearchTests(unittest.TestCase):
    def _frames(self):
        lineup = pd.DataFrame([
            {
                "season": 2023, "date": "2023-08-01", "home_team": "A", "away_team": "B",
                "signal": "HOME", "data_quality": "HIGH", "shock_diff": 1.8,
            },
            {
                "season": 2023, "date": "2023-08-02", "home_team": "C", "away_team": "D",
                "signal": "AWAY", "data_quality": "HIGH", "shock_diff": -2.0,
            },
            {
                "season": 2023, "date": "2023-08-03", "home_team": "E", "away_team": "F",
                "signal": "HOME", "data_quality": "LOW", "shock_diff": 2.2,
            },
        ])
        direction = pd.DataFrame([
            {
                "season": 2023, "date": "2023-08-01", "home_team": "A", "away_team": "B",
                "close_move_home": 0.25, "direction_score": 0.4,
                "direction_signal": "HOME_STRENGTHEN", "confidence_cutoff": 0.3,
                "high_confidence": 1, "model_train_through_season_direction": 2022,
            },
            {
                "season": 2023, "date": "2023-08-02", "home_team": "C", "away_team": "D",
                "close_move_home": 0.25, "direction_score": 0.2,
                "direction_signal": "HOME_STRENGTHEN", "confidence_cutoff": 0.3,
                "high_confidence": 0, "model_train_through_season_direction": 2022,
            },
            {
                "season": 2023, "date": "2023-08-03", "home_team": "E", "away_team": "F",
                "close_move_home": 0.50, "direction_score": 0.5,
                "direction_signal": "HOME_STRENGTHEN", "confidence_cutoff": 0.3,
                "high_confidence": 1, "model_train_through_season_direction": 2022,
            },
        ])
        return lineup, direction

    def test_agreement_and_signed_move_are_side_aware(self):
        lineup, direction = self._frames()
        out = mod.combine(lineup, direction)
        self.assertEqual(out.loc[0, "direction_agrees"], 1)
        self.assertEqual(out.loc[1, "direction_agrees"], 0)
        self.assertAlmostEqual(out.loc[0, "signed_close_move_for_lineup"], 0.25)
        self.assertAlmostEqual(out.loc[1, "signed_close_move_for_lineup"], -0.25)

    def test_low_quality_lineup_is_excluded(self):
        lineup, direction = self._frames()
        summary = mod.summarize(mod.combine(lineup, direction))
        all_rows = summary.loc[summary["scope"] == "ALL_LINEUP_SIGNALS", "rows"].iloc[0]
        self.assertEqual(all_rows, 2)

    def test_small_sample_cannot_be_promoted(self):
        lineup, direction = self._frames()
        summary = mod.summarize(mod.combine(lineup, direction))
        self.assertEqual(int(summary["research_promotion_candidate"].max()), 0)
        self.assertTrue((summary["shadow_only"] == 1).all())


if __name__ == "__main__":
    unittest.main()
