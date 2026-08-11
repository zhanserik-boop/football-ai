import unittest
from datetime import datetime, timedelta, timezone

import v3_shadow_risk_engine as mod


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def gate_row(fixture_id="1", day=0, hour=0, **updates):
    gate_time = NOW + timedelta(days=day, hours=hour)
    row = {
        "fixture_id": fixture_id,
        "gate_time_utc": gate_time.isoformat(),
        "kickoff_utc": (gate_time + timedelta(hours=1)).isoformat(),
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


def outcome_row(fixture_id="1", day=0, hour=0, profit=0.1):
    return {
        "fixture_id": fixture_id,
        "kickoff_utc": (NOW + timedelta(days=day, hours=hour + 1)).isoformat(),
        "profit": str(profit),
    }


def sample(profits, per_day=1):
    gates = []
    outcomes = []
    for index, profit in enumerate(profits):
        day = index // per_day
        hour = index % per_day
        fixture_id = str(index)
        gates.append(gate_row(fixture_id, day=day, hour=hour))
        outcomes.append(outcome_row(fixture_id, day=day, hour=hour, profit=profit))
    return gates, outcomes


class V3ShadowRiskEngineTests(unittest.TestCase):
    def test_forward_gate_keeps_risk_engine_locked(self):
        row, summary = mod.build_risk_report([], [], "COLLECTING_CLV", now=NOW)
        self.assertEqual(row["status"], "LOCKED_BY_FORWARD_TEST")
        self.assertEqual(row["activation_locked"], 1)
        self.assertTrue(summary["shadow_only"])

    def test_path_metrics_measure_drawdown_and_loss_streak(self):
        metrics = mod.path_metrics([1.0, -1.0, -1.0, 0.5, -1.0])
        self.assertAlmostEqual(metrics["profit_units"], -1.5)
        self.assertAlmostEqual(metrics["max_drawdown_units"], 2.5)
        self.assertEqual(metrics["longest_loss_streak"], 2)

    def test_daily_cap_accepts_only_four_entries(self):
        gates, outcomes = sample([0.1] * 6, per_day=6)
        row, _ = mod.build_risk_report(
            gates, outcomes, mod.FORWARD_UNLOCK_STATUS, now=NOW
        )
        self.assertEqual(row["eligible_settled"], 6)
        self.assertEqual(row["policy_bets"], 4)
        self.assertEqual(row["daily_cap_skipped"], 2)
        self.assertEqual(row["max_bets_in_day"], 4)

    def test_cancelled_entry_is_not_in_risk_sample(self):
        first = gate_row()
        cancelled = gate_row(
            gate_time_utc=(NOW + timedelta(minutes=5)).isoformat(),
            gate_decision="WATCH",
        )
        row, _ = mod.build_risk_report(
            [first, cancelled], [outcome_row()], mod.FORWARD_UNLOCK_STATUS, now=NOW
        )
        self.assertEqual(row["eligible_settled"], 0)

    def test_monte_carlo_is_deterministic(self):
        profits = [1.0, -1.0, 0.5, -0.5] * 10
        first = mod.bootstrap_risk(profits, paths=100, seed=7)
        second = mod.bootstrap_risk(profits, paths=100, seed=7)
        self.assertEqual(first, second)

    def test_strong_sample_reaches_manual_risk_review(self):
        gates, outcomes = sample([0.1] * 100)
        row, summary = mod.build_risk_report(
            gates, outcomes, mod.FORWARD_UNLOCK_STATUS, now=NOW
        )
        self.assertEqual(row["status"], "REVIEW_FOR_CONTROLLED_PILOT_RISK")
        self.assertEqual(row["risk_review_ready"], 1)
        self.assertEqual(row["activation_locked"], 1)
        self.assertTrue(summary["manual_review_required"])
        self.assertEqual(row["api_requests_used"], 0)

    def test_eight_loss_streak_keeps_strategy_in_shadow(self):
        gates, outcomes = sample([-1.0] * 8 + [1.0] * 92)
        row, _ = mod.build_risk_report(
            gates, outcomes, mod.FORWARD_UNLOCK_STATUS, now=NOW
        )
        self.assertEqual(row["status"], "KEEP_SHADOW_LOSS_STREAK")


if __name__ == "__main__":
    unittest.main()
