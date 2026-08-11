import unittest

import numpy as np
import pandas as pd

import historical_lineup_shock_builder as mod


class HistoricalLineupShockBuilderTests(unittest.TestCase):
    def _frames(self):
        lineup_rows = []
        stat_rows = []
        fixture_id = 100
        for match_no in range(6):
            fixture_id += 1
            for team, opponent, is_home in (("Home", "Away", True), ("Away", "Home", False)):
                for player_no in range(1, 15):
                    player_id = f"{team}-{player_no}"
                    starter = int(player_no <= 11)
                    lineup_rows.append({
                        "season": 2022,
                        "fixture_id": fixture_id,
                        "date": f"2022-08-{match_no + 1:02d}",
                        "match_home": "Home",
                        "match_away": "Away",
                        "team": team,
                        "player_id": player_id,
                        "starter": starter,
                    })
                    stat_rows.append({
                        "fixture_id": fixture_id,
                        "team": team,
                        "player_id": player_id,
                        "minutes": 90 if starter else 0,
                        "rating": 7.0 if starter else np.nan,
                    })
        return pd.DataFrame(lineup_rows), pd.DataFrame(stat_rows)

    def test_future_lineup_change_does_not_change_prior_shock(self):
        lineups, stats = self._frames()
        a = mod.build_historical_lineup_shocks(lineups, stats)
        changed = lineups.copy()
        last_fixture = changed["fixture_id"].max()
        changed.loc[
            (changed["fixture_id"] == last_fixture) & (changed["team"] == "Home"),
            "starter",
        ] = 0
        b = mod.build_historical_lineup_shocks(changed, stats)
        prior = a["fixture_id"] < last_fixture
        np.testing.assert_allclose(
            a.loc[prior, "shock_diff"].to_numpy(dtype=float),
            b.loc[prior, "shock_diff"].to_numpy(dtype=float),
            equal_nan=True,
        )

    def test_history_is_prior_only(self):
        lineups, stats = self._frames()
        out = mod.build_historical_lineup_shocks(lineups, stats)
        self.assertTrue((out["prior_only"] == 1).all())
        self.assertEqual(out.iloc[0]["data_quality"], "LOW")
        self.assertIn(out.iloc[-1]["data_quality"], ("MEDIUM", "HIGH"))

    def test_signal_direction(self):
        self.assertEqual(mod.classify_signal(1.5), "HOME")
        self.assertEqual(mod.classify_signal(-1.5), "AWAY")
        self.assertEqual(mod.classify_signal(1.49), "NO SIGNAL")


if __name__ == "__main__":
    unittest.main()
