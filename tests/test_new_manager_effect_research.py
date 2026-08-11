import unittest
import pandas as pd

from new_manager_effect_research import build_event_study, summarize_events, summarize_by_season


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


if __name__ == "__main__":
    unittest.main()
