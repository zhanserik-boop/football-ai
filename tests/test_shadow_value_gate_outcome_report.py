import unittest
from datetime import datetime, timezone

import shadow_value_gate_outcome_report as mod


NOW = datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc)


def gate_row(time="2026-08-11T12:00:00+00:00", **updates):
    row = {
        "gate_time_utc": time, "fixture_id": "10",
        "home_team": "Arsenal", "away_team": "Chelsea",
        "kickoff_utc": "2026-08-11T13:00:00+00:00", "signal": "HOME",
        "shock_band": "ROBUST_1.5_2.0", "data_quality": "HIGH",
        "market_freshness": "FRESH", "context_score": "0",
        "entry_handicap": "-0.5", "entry_avg_odds": "1.95",
        "entry_best_odds": "2.0", "entry_best_bookmaker": "Book A",
        "gate_decision": "SHADOW BET",
    }
    row.update(updates)
    return row


def post_row(**updates):
    row = {
        "fixture_id": "10", "close_time": "2026-08-11T12:59:00+00:00",
        "close_handicap": "-0.75", "close_avg_odds": "1.90",
        "match_status": "FT", "home_goals": "2", "away_goals": "1",
    }
    row.update(updates)
    return row


class ShadowValueGateOutcomeReportTests(unittest.TestCase):
    def test_first_shadow_bet_freezes_entry(self):
        first = gate_row(entry_best_odds="2.05")
        later = gate_row(
            time="2026-08-11T12:05:00+00:00", entry_best_odds="1.85"
        )
        selected = mod.first_shadow_bets([later, first])
        self.assertEqual(selected["10"]["entry_best_odds"], "2.05")

    def test_gate_entry_gets_own_clv_and_settlement(self):
        outcomes = mod.build_outcomes([gate_row()], [post_row()], now=NOW)
        row = outcomes[0]
        self.assertAlmostEqual(row["line_clv"], 0.25)
        self.assertEqual(row["same_line"], 0)
        self.assertEqual(row["price_clv"], "")
        self.assertAlmostEqual(row["profit"], 1.0)

    def test_same_line_price_clv(self):
        outcomes = mod.build_outcomes(
            [gate_row()], [post_row(close_handicap="-0.5")], now=NOW
        )
        self.assertEqual(outcomes[0]["same_line"], 1)
        self.assertAlmostEqual(outcomes[0]["price_clv"], 0.05)

    def test_quarter_handicap_half_win(self):
        profit = mod.settle_ah("HOME", -0.75, 2.0, 2, 1)
        self.assertAlmostEqual(profit, 0.5)

    def test_pending_match_has_no_profit(self):
        outcomes = mod.build_outcomes(
            [gate_row()], [post_row(match_status="NS", home_goals="", away_goals="")],
            now=NOW,
        )
        self.assertEqual(outcomes[0]["profit"], "")

    def test_promotion_requires_clv_and_roi_confidence(self):
        self.assertEqual(
            mod.promotion_status(49, 0.01, 100, 0.01), "COLLECTING_CLV"
        )
        self.assertEqual(
            mod.promotion_status(50, 0.01, 99, 0.01),
            "CLV_PASSED_COLLECTING_ROI",
        )
        self.assertEqual(
            mod.promotion_status(50, 0.01, 100, 0.01),
            "REVIEW_FOR_PROMOTION",
        )


if __name__ == "__main__":
    unittest.main()
