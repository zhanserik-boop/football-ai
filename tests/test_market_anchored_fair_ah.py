import unittest
import numpy as np
import pandas as pd

import market_anchored_fair_ah as mod


class MarketAnchoredFairAHTests(unittest.TestCase):
    def _frame(self):
        rows = []
        for season in (2022, 2023, 2024):
            for i in range(320):
                open_line = [-1.0, -0.5, 0.0, 0.5, 1.0][i % 5]
                xg_edge = ((i % 9) - 4) / 5.0
                ah_edge = ((i % 7) - 3) / 10.0
                drawp = 0.2 + (i % 10) / 50.0
                close_move = 0.08 * xg_edge + 0.05 * ah_edge + (0.02 if season >= 2023 else 0.0)
                rows.append({
                    "season": season,
                    "date": pd.Timestamp(f"{season}-08-01") + pd.Timedelta(days=i),
                    "home_team": f"H{i%20}", "away_team": f"A{i%20}",
                    "open_ah_home_line": open_line,
                    "close_ah_home_line": open_line + close_move,
                    "market_home_prob": 0.42, "market_draw_prob": drawp, "market_away_prob": 0.33,
                    "matchup_xg_balance_edge_home": xg_edge,
                    "ah_cover_edge_home": ah_edge,
                    "attack_volume_edge_home": ((i % 5) - 2) / 2.0,
                    "shot_quality_edge_home": ((i % 3) - 1) / 20.0,
                    "tempo_mean": 22.0 + (i % 6),
                    "h2h_relevant_xg_diff_home": ((i % 4) - 2) / 5.0,
                    "h2h_relevant_ah_cover_home": 0.45 + (i % 4) / 20.0,
                    "h2h_relevant_count": i % 4,
                    "underdog_resistance_score": 0.45 + (i % 5) / 20.0,
                    "draw_pressure_score": 0.40 + (i % 4) / 20.0,
                })
        return pd.DataFrame(rows)

    def test_walk_forward_never_trains_on_same_or_future_season(self):
        out = mod.walk_forward(self._frame())
        s2023 = out[out["season"] == 2023]
        self.assertTrue((s2023["model_train_through_season"] == 2022).all())
        s2024 = out[out["season"] == 2024]
        self.assertTrue((s2024["model_train_through_season"] == 2023).all())

    def test_future_season_change_does_not_change_prior_predictions(self):
        base = self._frame()
        a = mod.walk_forward(base.copy())
        changed = base.copy()
        changed.loc[changed["season"] == 2024, "close_ah_home_line"] += 5.0
        b = mod.walk_forward(changed)
        pa = a.loc[a["season"] == 2023, "predicted_close_move_home"].to_numpy()
        pb = b.loc[b["season"] == 2023, "predicted_close_move_home"].to_numpy()
        np.testing.assert_allclose(pa, pb, equal_nan=True)

    def test_opening_ah_identifies_underdog_side(self):
        d = pd.DataFrame([
            {"open_home_odds": 3.0, "open_draw_odds": 3.2, "open_away_odds": 2.2,
             "open_ah_home_line": 0.5, "close_ah_home_line": 0.5, "home_goals": 1, "away_goals": 1},
            {"open_home_odds": 1.8, "open_draw_odds": 3.5, "open_away_odds": 4.5,
             "open_ah_home_line": -0.75, "close_ah_home_line": -0.75, "home_goals": 2, "away_goals": 0},
        ])
        out = mod.add_market_features(d)
        self.assertEqual(out.iloc[0]["underdog_side"], "HOME")
        self.assertEqual(out.iloc[1]["underdog_side"], "AWAY")

    def test_fair_ah_is_explicitly_market_anchored(self):
        out = mod.walk_forward(self._frame())
        test = out[out["predicted_close_move_home"].notna()].iloc[0]
        self.assertAlmostEqual(
            test["fair_ah_proxy_home"],
            test["open_ah_home_line"] + test["predicted_close_move_home"],
            places=10,
        )


if __name__ == "__main__":
    unittest.main()
