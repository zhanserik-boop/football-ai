import unittest
import pandas as pd

from new_manager_effect_research import (
    build_event_study,
    build_placebo_candidates,
    match_placebo_controls,
    summarize_events,
    summarize_by_season,
    summarize_matched,
)


class NewManagerEffectResearchTests(unittest.TestCase):
    def _rows(self):
        rows = []
        for i in range(1, 11):
            change = 1 if i == 6 else 0
            coach = "Old" if i <= 5 else "New"
            spell = 1 if i <= 5 else 2
            rows.append({
                "team": "Alpha",
                "season": 2025,
                "fixture_id": i,
                "date": f"2025-08-{i:02d}",
                "coach": coach,
                "previous_coach": "Old" if change else "",
                "coach_change_flag": change,
                "coach_spell_id": spell,
                "actual_xg_diff": 0.0 if i <= 5 else 1.0,
                "actual_goal_diff": 0.0 if i <= 5 else 1.0,
                "actual_ah_cover_score": 0.5 if i <= 5 else 1.0,
            })
        return pd.DataFrame(rows)

    def _rows_with_placebo(self):
        rows = self._rows().to_dict("records")
        for i in range(1, 13):
            rows.append({
                "team": "Alpha",
                "season": 2024,
                "fixture_id": 100 + i,
                "date": f"2024-08-{i:02d}",
                "coach": "Stable",
                "previous_coach": "",
                "coach_change_flag": 0,
                "coach_spell_id": 10,
                "actual_xg_diff": 0.0 if i <= 6 else 0.2,
                "actual_goal_diff": 0.0 if i <= 6 else 0.2,
                "actual_ah_cover_score": 0.5 if i <= 6 else 0.6,
            })
        return pd.DataFrame(rows)

    def test_pre_post_windows_and_deltas(self):
        events = build_event_study(self._rows())
        self.assertEqual(events["post_window"].tolist(), [1, 3, 5])
        row5 = events[events["post_window"] == 5].iloc[0]
        self.assertAlmostEqual(row5["delta_actual_xg_diff"], 1.0)
        self.assertAlmostEqual(row5["delta_actual_goal_diff"], 1.0)
        self.assertAlmostEqual(row5["delta_actual_ah_cover_score"], 0.5)

    def test_requires_full_pre_window(self):
        df = self._rows().copy()
        df.loc[:, "coach_change_flag"] = 0
        df.loc[df["fixture_id"] == 3, "coach_change_flag"] = 1
        events = build_event_study(df)
        self.assertTrue(events.empty)

    def test_does_not_cross_next_coach_spell(self):
        df = self._rows().copy()
        df.loc[df["fixture_id"] >= 8, "coach_spell_id"] = 3
        events = build_event_study(df)
        self.assertEqual(events["post_window"].tolist(), [1])

    def test_summary_aggregates_by_window(self):
        events = build_event_study(self._rows())
        summary = summarize_events(events)
        row3 = summary[summary["post_window"] == 3].iloc[0]
        self.assertEqual(int(row3["events"]), 1)
        self.assertAlmostEqual(row3["mean_delta_actual_xg_diff"], 1.0)
        self.assertAlmostEqual(row3["ci95_low_actual_xg_diff"], 1.0)
        self.assertAlmostEqual(row3["ci95_high_actual_xg_diff"], 1.0)
        self.assertGreaterEqual(row3["signflip_p_actual_xg_diff"], 0.0)
        self.assertLessEqual(row3["signflip_p_actual_xg_diff"], 1.0)

    def test_season_summary_preserves_window(self):
        events = build_event_study(self._rows())
        by_season = summarize_by_season(events)
        row5 = by_season[(by_season["season"] == 2025) & (by_season["post_window"] == 5)].iloc[0]
        self.assertEqual(int(row5["events"]), 1)
        self.assertAlmostEqual(row5["mean_delta_actual_ah_cover_score"], 0.5)

    def test_matched_placebo_uses_same_team_stable_coach_window(self):
        df = self._rows_with_placebo()
        events = build_event_study(df)
        candidates = build_placebo_candidates(df)
        matched = match_placebo_controls(events, candidates)
        self.assertEqual(len(matched), 3)
        self.assertTrue((matched["team"] == "Alpha").all())
        self.assertTrue((matched["match_scope"] == "SAME_TEAM_OTHER_SEASON").all())
        self.assertTrue((matched["control_coach"] == "Stable").all())
        row1 = matched[matched["post_window"] == 1].iloc[0]
        self.assertGreater(row1["adjusted_delta_actual_xg_diff"], 0.0)
        self.assertGreater(row1["adjusted_delta_actual_ah_cover_score"], 0.0)

    def test_matched_summary_reports_uncertainty(self):
        df = self._rows_with_placebo()
        matched = match_placebo_controls(
            build_event_study(df), build_placebo_candidates(df)
        )
        summary = summarize_matched(matched)
        row3 = summary[summary["post_window"] == 3].iloc[0]
        self.assertEqual(int(row3["pairs"]), 1)
        self.assertGreater(row3["mean_adjusted_delta_actual_ah_cover_score"], 0.0)
        self.assertGreaterEqual(row3["signflip_p_adjusted_actual_ah_cover_score"], 0.0)
        self.assertLessEqual(row3["signflip_p_adjusted_actual_ah_cover_score"], 1.0)


if __name__ == "__main__":
    unittest.main()
