import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from coach_context_builder import (
    attach_match_metrics,
    build_coach_context,
    load_coach_history,
    load_context,
)


class CoachContextTests(unittest.TestCase):
    def _base_context(self, dates):
        rows = []
        for i, date in enumerate(dates, start=1):
            rows.append({
                "season": 2025,
                "date": pd.Timestamp(date),
                "home_team": "Alpha",
                "away_team": "Beta",
                "home_goals": 1,
                "away_goals": 0,
                "home_xg": 1.6,
                "away_xg": 0.8,
                "close_ah_home_line": -0.5,
            })
        return pd.DataFrame(rows)

    def _coach_rows(self, coaches, formations=None):
        formations = formations or ["4-3-3"] * len(coaches)
        rows = []
        for i, (coach, formation) in enumerate(zip(coaches, formations), start=1):
            rows.extend([
                {
                    "season": 2025,
                    "fixture_id": i,
                    "date": pd.Timestamp(f"2025-08-{i:02d}"),
                    "match_home": "Alpha",
                    "match_away": "Beta",
                    "team": "Alpha",
                    "coach": coach,
                    "formation": formation,
                    "coach_status": "OK",
                },
                {
                    "season": 2025,
                    "fixture_id": i,
                    "date": pd.Timestamp(f"2025-08-{i:02d}"),
                    "match_home": "Alpha",
                    "match_away": "Beta",
                    "team": "Beta",
                    "coach": "Coach B",
                    "formation": "4-4-2",
                    "coach_status": "OK",
                },
            ])
        return pd.DataFrame(rows)

    def test_new_coach_first_match_is_flagged(self):
        coaches = self._coach_rows(["Coach A", "Coach A", "Coach C"])
        ctx = self._base_context(["2025-08-01", "2025-08-02", "2025-08-03"])
        result = build_coach_context(attach_match_metrics(coaches, ctx))
        alpha = result[result["team"] == "Alpha"].reset_index(drop=True)
        self.assertEqual(alpha.loc[2, "coach_change_flag"], 1)
        self.assertEqual(alpha.loc[2, "new_manager_first_match"], 1)
        self.assertEqual(alpha.loc[2, "coach_spell_match_number"], 1)
        self.assertEqual(alpha.loc[2, "previous_coach"], "Coach A")

    def test_initial_observed_coach_is_not_counted_as_a_change(self):
        coaches = self._coach_rows(["Coach A"] * 3)
        ctx = self._base_context(["2025-08-01", "2025-08-02", "2025-08-03"])
        result = build_coach_context(attach_match_metrics(coaches, ctx))
        alpha = result[result["team"] == "Alpha"].reset_index(drop=True)
        self.assertEqual(alpha.loc[0, "coach_change_flag"], 0)
        self.assertEqual(alpha.loc[0, "new_manager_first_match"], 0)
        self.assertEqual(alpha["new_manager_window"].tolist(), [0, 0, 0])

    def test_new_manager_window_is_first_five_matches_of_changed_spell(self):
        coaches = self._coach_rows(["Coach A", "Coach C", "Coach C", "Coach C", "Coach C", "Coach C", "Coach C"])
        ctx = self._base_context([f"2025-08-{i:02d}" for i in range(1, 8)])
        result = build_coach_context(attach_match_metrics(coaches, ctx))
        alpha = result[result["team"] == "Alpha"].reset_index(drop=True)
        self.assertEqual(alpha["coach_spell_match_number"].tolist(), [1, 1, 2, 3, 4, 5, 6])
        self.assertEqual(alpha["new_manager_window"].tolist(), [0, 1, 1, 1, 1, 1, 0])

    def test_reappointment_starts_new_spell(self):
        coaches = self._coach_rows(["Coach A", "Coach A", "Coach C", "Coach C", "Coach A"])
        ctx = self._base_context([f"2025-08-{i:02d}" for i in range(1, 6)])
        result = build_coach_context(attach_match_metrics(coaches, ctx))
        row5 = result[(result["team"] == "Alpha") & (result["fixture_id"] == 5)].iloc[0]
        self.assertEqual(row5["coach_change_flag"], 1)
        self.assertEqual(row5["previous_coach"], "Coach C")
        self.assertEqual(row5["coach_spell_match_number"], 1)
        self.assertEqual(row5["new_manager_first_match"], 1)

    def test_profile_uses_only_prior_matches(self):
        coaches = self._coach_rows(["Coach A"] * 4)
        ctx = self._base_context([f"2025-08-{i:02d}" for i in range(1, 5)])
        attached = attach_match_metrics(coaches, ctx)
        alpha_attached = attached[attached["team"] == "Alpha"].copy()
        alpha_attached.loc[alpha_attached["fixture_id"] == 4, "xg_for"] = 9.0
        beta_attached = attached[attached["team"] == "Beta"]
        result = build_coach_context(pd.concat([alpha_attached, beta_attached], ignore_index=True))
        row4 = result[(result["team"] == "Alpha") & (result["fixture_id"] == 4)].iloc[0]
        self.assertAlmostEqual(row4["coach_prior_avg_xg_for"], 1.6, places=6)
        self.assertEqual(row4["coach_prior_matches"], 3)
        self.assertEqual(row4["coach_profile_ready"], 1)

    def test_formation_change_is_against_prior_coach_norm(self):
        coaches = self._coach_rows(["Coach A"] * 4, ["4-3-3", "4-3-3", "4-3-3", "3-4-2-1"])
        ctx = self._base_context([f"2025-08-{i:02d}" for i in range(1, 5)])
        result = build_coach_context(attach_match_metrics(coaches, ctx))
        row4 = result[(result["team"] == "Alpha") & (result["fixture_id"] == 4)].iloc[0]
        self.assertEqual(row4["coach_dominant_formation"], "4-3-3")
        self.assertEqual(row4["formation_vs_coach_norm"], "CHANGE")

    def test_away_ah_line_is_inverted(self):
        coaches = self._coach_rows(["Coach A"])
        ctx = self._base_context(["2025-08-01"])
        attached = attach_match_metrics(coaches, ctx)
        beta = attached[attached["team"] == "Beta"].iloc[0]
        self.assertAlmostEqual(beta["close_ah_team_line"], 0.5, places=6)
        self.assertAlmostEqual(beta["ah_margin"], -0.5, places=6)
        self.assertAlmostEqual(beta["ah_cover_score"], 0.0, places=6)

    def test_file_loaders_normalize_timezone_aware_and_naive_dates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            coach_path = root / "coaches.csv"
            context_path = root / "context.csv"

            pd.DataFrame([
                {
                    "season": 2025,
                    "fixture_id": 1,
                    "date": "2025-08-01T19:00:00+00:00",
                    "match_home": "Alpha",
                    "match_away": "Beta",
                    "team": "Alpha",
                    "coach": "Coach A",
                    "formation": "4-3-3",
                    "coach_status": "OK",
                }
            ]).to_csv(coach_path, index=False, encoding="utf-8-sig")

            pd.DataFrame([
                {
                    "season": 2025,
                    "date": "2025-08-01",
                    "home_team": "Alpha",
                    "away_team": "Beta",
                    "home_goals": 1,
                    "away_goals": 0,
                    "home_xg": 1.4,
                    "away_xg": 0.7,
                    "close_ah_home_line": -0.5,
                }
            ]).to_csv(context_path, index=False, encoding="utf-8-sig")

            coaches = load_coach_history(coach_path)
            context = load_context(context_path)
            self.assertIsNone(coaches["date"].dt.tz)
            self.assertIsNone(context["date"].dt.tz)
            merged = attach_match_metrics(coaches, context)
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged.iloc[0]["side"], "HOME")
            self.assertAlmostEqual(merged.iloc[0]["xg_for"], 1.4, places=6)


if __name__ == "__main__":
    unittest.main()
