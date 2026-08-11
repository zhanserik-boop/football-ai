import unittest
import numpy as np
import pandas as pd

import market_move_direction_research as mod


class MarketMoveDirectionResearchTests(unittest.TestCase):
    def _frame(self):
        rows = []
        for season in (2022, 2023, 2024):
            for i in range(320):
                rec = {"season": season, "close_move_home": [0.25, -0.25, 0.0, 0.0][i % 4]}
                for j, f in enumerate(mod.FEATURES):
                    rec[f] = ((i + j * 3) % 17 - 8) / 10.0
                rows.append(rec)
        return pd.DataFrame(rows)

    def test_home_handicap_move_maps_to_correct_strengthening_side(self):
        self.assertEqual(mod.strengthening_side_from_home_handicap_move(-0.25), "HOME_STRENGTHEN")
        self.assertEqual(mod.strengthening_side_from_home_handicap_move(0.25), "AWAY_STRENGTHEN")
        self.assertEqual(mod.strengthening_side_from_home_handicap_move(0.0), "FLAT")

    def test_walk_forward_uses_prior_seasons_only(self):
        out = mod.walk_forward(self._frame())
        s23 = out[out["season"] == 2023]
        s24 = out[out["season"] == 2024]
        self.assertTrue((s23["model_train_through_season_direction"] == 2022).all())
        self.assertTrue((s24["model_train_through_season_direction"] == 2023).all())

    def test_future_season_change_does_not_change_prior_prediction(self):
        base = self._frame()
        a = mod.walk_forward(base.copy())
        changed = base.copy()
        changed.loc[changed["season"] == 2024, "close_move_home"] = 5.0
        b = mod.walk_forward(changed)
        pa = a.loc[a["season"] == 2023, "direction_score"].to_numpy()
        pb = b.loc[b["season"] == 2023, "direction_score"].to_numpy()
        np.testing.assert_allclose(pa, pb, equal_nan=True)

    def test_high_confidence_cutoff_is_derived_from_training_only(self):
        out = mod.walk_forward(self._frame())
        cutoff_23 = out.loc[out["season"] == 2023, "confidence_cutoff"].dropna().unique()
        self.assertEqual(len(cutoff_23), 1)
        self.assertGreaterEqual(float(cutoff_23[0]), 0.0)


if __name__ == "__main__":
    unittest.main()
