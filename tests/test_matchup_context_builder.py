import unittest
import pandas as pd

from matchup_context_builder import to_team_matches, add_style_profiles, build_matchup_context


class MatchupContextTests(unittest.TestCase):
    def _matches(self):
        rows = []
        for i in range(1, 8):
            rows.append({
                "season": 2025,
                "date": f"2025-08-{i:02d}",
                "home_team": "Alpha" if i % 2 else "Beta",
                "away_team": "Beta" if i % 2 else "Alpha",
                "home_goals": 2, "away_goals": 1,
                "home_xg": float(i), "away_xg": 1.0,
                "home_shots": 10 + i, "away_shots": 8,
                "home_sot": 5, "away_sot": 3,
                "home_corners": 6, "away_corners": 4,
                "home_fouls": 10, "away_fouls": 11,
                "open_ah_home_line": -0.5,
            })
        d = pd.DataFrame(rows)
        d["date"] = pd.to_datetime(d["date"])
        return d

    def test_style_profile_uses_only_prior_matches(self):
        tm = to_team_matches(self._matches())
        styles = add_style_profiles(tm, window=8, min_style=1)
        alpha = styles[styles["team"] == "Alpha"].sort_values("date").reset_index(drop=True)
        self.assertEqual(int(alpha.iloc[0]["style_prior_matches"]), 0)
        self.assertTrue(pd.isna(alpha.iloc[0]["prior_xg_for"]))
        first_actual = float(alpha.iloc[0]["xg_for"])
        self.assertAlmostEqual(float(alpha.iloc[1]["prior_xg_for"]), first_actual)

    def test_future_match_does_not_change_prior_profile(self):
        base = self._matches().iloc[:5].copy()
        tm1 = to_team_matches(base)
        s1 = add_style_profiles(tm1, min_style=1)
        before = s1[(s1["team"] == "Alpha")].sort_values("date").iloc[-1]["prior_xg_for"]
        extended = pd.concat([base, self._matches().iloc[[5]].assign(home_xg=99.0)], ignore_index=True)
        tm2 = to_team_matches(extended)
        s2 = add_style_profiles(tm2, min_style=1)
        same_date = s2[(s2["team"] == "Alpha") & (s2["date"] == s1[(s1["team"] == "Alpha")].sort_values("date").iloc[-1]["date"])].iloc[0]
        self.assertAlmostEqual(float(before), float(same_date["prior_xg_for"]))

    def test_relevant_h2h_requires_tactical_similarity(self):
        matches = self._matches().iloc[:3].copy()
        tm = add_style_profiles(to_team_matches(matches), min_style=1)
        tm["formation"] = "4-3-3"
        tm["coach"] = ""
        out = build_matchup_context(matches, tm)
        self.assertEqual(int(out.iloc[0]["h2h_relevant_count"]), 0)
        self.assertGreaterEqual(int(out.iloc[1]["h2h_relevant_count"]), 1)
        self.assertIn("formation", out.iloc[1]["h2h_relevance_basis"])

    def test_raw_h2h_is_not_called_relevant_without_tactical_metadata(self):
        matches = self._matches().iloc[:3].copy()
        tm = add_style_profiles(to_team_matches(matches), min_style=1)
        tm["formation"] = ""
        tm["coach"] = ""
        out = build_matchup_context(matches, tm)
        self.assertGreater(int(out.iloc[2]["h2h_prior_count"]), 0)
        self.assertEqual(int(out.iloc[2]["h2h_relevant_count"]), 0)


if __name__ == "__main__":
    unittest.main()
