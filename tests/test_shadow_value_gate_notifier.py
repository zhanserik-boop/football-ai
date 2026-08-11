import unittest

import shadow_value_gate_notifier as mod


def gate_row(decision="SHADOW BET", **updates):
    row = {
        "fixture_id": "10", "home_team": "Arsenal", "away_team": "Chelsea",
        "signal": "HOME", "gate_decision": decision, "entry_handicap": "-0.5",
        "entry_best_odds": "2.0", "entry_best_bookmaker": "Book A",
        "abs_shock": "1.8", "data_quality": "HIGH", "market_freshness": "FRESH",
        "market_bookmakers": "3", "new_manager_context": "NEUTRAL",
        "matchup_context": "SUPPORT", "minutes_to_kickoff": "45",
        "reason": "test",
    }
    row.update(updates)
    return row


def summary_row(**updates):
    row = {
        "scope": "ALL", "candidates": "10", "with_clv": "10",
        "avg_line_clv": "0.1", "line_clv_ci_low": "0.02",
        "line_clv_ci_high": "0.18", "settled": "10",
        "profit_units": "1.5", "roi": "0.15",
        "promotion_status": "COLLECTING_CLV",
    }
    row.update(updates)
    return row


class ShadowValueGateNotifierTests(unittest.TestCase):
    def test_new_shadow_bet_alerts_only_once(self):
        state = mod.default_state()
        events = mod.gate_events([gate_row()], state)
        self.assertEqual(len(events), 1)
        self.assertIn("SHADOW ONLY", events[0]["message"])
        mod.apply_success(state, events[0])
        self.assertEqual(mod.gate_events([gate_row()], state), [])

    def test_initial_watch_is_silent_then_upgrade_alerts(self):
        state = mod.default_state()
        self.assertEqual(mod.gate_events([gate_row("WATCH")], state), [])
        events = mod.gate_events([gate_row("SHADOW BET")], state)
        self.assertEqual(len(events), 1)
        self.assertNotIn("REACTIVATED", events[0]["message"])

    def test_active_shadow_bet_downgrade_alerts(self):
        state = {"fixtures": {"10": {"decision": "SHADOW BET"}}, "summary": {}}
        events = mod.gate_events([gate_row("WATCH")], state)
        self.assertEqual(len(events), 1)
        self.assertIn("CANCELLED", events[0]["message"])

    def test_cancelled_bet_reactivation_is_labeled(self):
        state = {
            "fixtures": {"10": {"decision": "WATCH", "ever_alerted": True}},
            "summary": {},
        }
        events = mod.gate_events([gate_row("SHADOW BET")], state)
        self.assertIn("REACTIVATED", events[0]["message"])

    def test_summary_initial_milestone_alerts(self):
        state = mod.default_state()
        events = mod.summary_events([summary_row()], state)
        self.assertEqual(len(events), 1)
        self.assertIn("VALIDATION UPDATE", events[0]["message"])

    def test_summary_does_not_repeat_same_milestone(self):
        state = mod.default_state()
        event = mod.summary_events([summary_row()], state)[0]
        mod.apply_success(state, event)
        self.assertEqual(mod.summary_events([summary_row(with_clv="14")], state), [])

    def test_promotion_status_change_alerts_without_new_milestone(self):
        state = {
            "fixtures": {},
            "summary": {
                "status": "COLLECTING_CLV", "clv_milestone": 50,
                "settled_milestone": 50,
            },
        }
        row = summary_row(
            with_clv="52", settled="52",
            promotion_status="CLV_PASSED_COLLECTING_ROI",
        )
        self.assertEqual(len(mod.summary_events([row], state)), 1)


if __name__ == "__main__":
    unittest.main()
