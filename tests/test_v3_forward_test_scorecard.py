import unittest
from datetime import datetime, timedelta, timezone

import v3_forward_test_scorecard as mod


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def gate_row(fixture_id="1", day=0, **updates):
    row = {
        "fixture_id": fixture_id,
        "gate_time_utc": (NOW + timedelta(days=day)).isoformat(),
        "kickoff_utc": (NOW + timedelta(days=day, hours=1)).isoformat(),
        "gate_decision": "SHADOW BET",
        "signal": "HOME",
        "data_quality": "HIGH",
        "market_freshness": "FRESH",
        "health_gate_status": "HEALTHY",
        "entry_handicap": "-0.5",
        "entry_best_odds": "2.0",
        "shadow_only": "1",
    }
    row.update(updates)
    return row


def outcome_row(fixture_id="1", clv=0.1, profit=0.1):
    return {
        "fixture_id": fixture_id,
        "line_clv": str(clv),
        "profit": str(profit),
    }


class V3ForwardTestScorecardTests(unittest.TestCase):
    def test_empty_forward_test_has_not_started(self):
        row, summary = mod.build_scorecard([], [], now=NOW)
        self.assertEqual(row["status"], "NOT_STARTED")
        self.assertEqual(summary["api_requests_used"], 0)

    def test_legacy_or_unsafe_candidate_is_excluded(self):
        row, _ = mod.build_scorecard(
            [gate_row(health_gate_status="")], [outcome_row()], now=NOW
        )
        self.assertEqual(row["shadow_candidates"], 1)
        self.assertEqual(row["eligible_forward_bets"], 0)
        self.assertEqual(row["excluded_candidates"], 1)

    def test_eligible_candidate_collects_clv(self):
        row, _ = mod.build_scorecard([gate_row()], [outcome_row()], now=NOW)
        self.assertEqual(row["status"], "COLLECTING_CLV")
        self.assertEqual(row["with_clv"], 1)
        self.assertEqual(row["settled"], 1)

    def test_clv_failure_keeps_system_in_shadow(self):
        gates = [gate_row(str(i), day=i % 10) for i in range(50)]
        outcomes = [outcome_row(str(i), clv=-0.1, profit=0.2) for i in range(50)]
        row, _ = mod.build_scorecard(gates, outcomes, now=NOW)
        self.assertEqual(row["status"], "KEEP_SHADOW_CLV_FAILED")

    def test_entry_cancellation_is_audited(self):
        first = gate_row()
        later = gate_row(
            gate_time_utc=(NOW + timedelta(minutes=5)).isoformat(),
            gate_decision="WATCH",
        )
        row, _ = mod.build_scorecard([first, later], [outcome_row()], now=NOW)
        self.assertEqual(row["cancelled_after_entry"], 1)
        self.assertEqual(row["eligible_forward_bets"], 0)

    def test_post_kickoff_pass_does_not_cancel_entry(self):
        first = gate_row()
        later = gate_row(
            gate_time_utc=(NOW + timedelta(hours=2)).isoformat(),
            gate_decision="PASS",
        )
        row, _ = mod.build_scorecard([first, later], [outcome_row()], now=NOW)
        self.assertEqual(row["cancelled_after_entry"], 0)
        self.assertEqual(row["eligible_forward_bets"], 1)

    def test_strong_diverse_sample_reaches_manual_pilot_review(self):
        gates = [gate_row(str(i), day=i % 20) for i in range(100)]
        outcomes = [outcome_row(str(i), clv=0.1, profit=0.1) for i in range(100)]
        row, summary = mod.build_scorecard(gates, outcomes, now=NOW)
        self.assertEqual(row["status"], "REVIEW_FOR_CONTROLLED_PILOT")
        self.assertEqual(row["forward_days"], 20)
        self.assertTrue(summary["manual_promotion_required"])


if __name__ == "__main__":
    unittest.main()
