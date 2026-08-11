import unittest
import pandas as pd

import formation_context_builder as fcb


class FormationContextTests(unittest.TestCase):
    def test_common_formation_is_not_shock(self):
        rows = []
        for i in range(1, 8):
            rows.append({
                "season": 2026,
                "fixture_id": i,
                "date": pd.Timestamp(2026, 8, i),
                "team_id": 1,
                "team": "Test FC",
                "formation": "4-3-3",
            })
        out = fcb.add_formation_history(pd.DataFrame(rows), window=10, min_history=5)
        self.assertEqual(int(out.iloc[-1]["formation_shock_flag"]), 0)
        self.assertGreater(float(out.iloc[-1]["formation_prior_share"]), 0.8)

    def test_unusual_formation_after_stable_history_is_shock(self):
        rows = []
        for i in range(1, 7):
            rows.append({
                "season": 2026,
                "fixture_id": i,
                "date": pd.Timestamp(2026, 8, i),
                "team_id": 1,
                "team": "Test FC",
                "formation": "4-3-3",
            })
        rows.append({
            "season": 2026,
            "fixture_id": 7,
            "date": pd.Timestamp(2026, 8, 7),
            "team_id": 1,
            "team": "Test FC",
            "formation": "3-4-2-1",
        })
        out = fcb.add_formation_history(pd.DataFrame(rows), window=10, min_history=5)
        last = out.iloc[-1]
        self.assertEqual(int(last["formation_shock_flag"]), 1)
        self.assertEqual(float(last["formation_prior_share"]), 0.0)
        self.assertEqual(last["dominant_formation_prior"], "4-3-3")

    def test_no_shock_before_minimum_history(self):
        rows = [
            {"season": 2026, "fixture_id": 1, "date": pd.Timestamp(2026, 8, 1), "team_id": 1, "team": "Test FC", "formation": "4-3-3"},
            {"season": 2026, "fixture_id": 2, "date": pd.Timestamp(2026, 8, 2), "team_id": 1, "team": "Test FC", "formation": "3-5-2"},
        ]
        out = fcb.add_formation_history(pd.DataFrame(rows), window=10, min_history=5)
        self.assertEqual(int(out.iloc[-1]["formation_shock_flag"]), 0)
        self.assertEqual(float(out.iloc[-1]["formation_shock_score"]), 0.0)


if __name__ == "__main__":
    unittest.main()
