import unittest
from datetime import datetime, timedelta, timezone

import system_health_watchdog as mod


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def state(heartbeat=None, fixture=None, odds=None, lineup=False):
    heartbeat = heartbeat or NOW.isoformat()
    fixtures = {"10": fixture} if fixture else {}
    return {
        "health": {
            "last_cycle_completed_utc": heartbeat,
            "last_cycle_status": "OK",
            "consecutive_errors": 0,
        },
        "fixtures": fixtures,
        "odds_freshness": {"10": odds} if odds else {},
        "lineup_results": {"10": {"signal": "HOME"}} if lineup else {},
    }


def fixture(minutes=45):
    return {
        "fixture_id": "10",
        "kickoff": (NOW + timedelta(minutes=minutes)).isoformat(),
        "home_team": "Arsenal",
        "away_team": "Chelsea",
    }


class SystemHealthWatchdogTests(unittest.TestCase):
    def test_missing_state_is_critical(self):
        rows = mod.build_health(None, now=NOW)
        self.assertEqual(rows[0]["overall_status"], "CRITICAL")
        self.assertEqual(rows[1]["code"], "MONITOR_STATE_MISSING")

    def test_current_heartbeat_without_fixtures_is_healthy(self):
        rows = mod.build_health(state(), now=NOW)
        self.assertEqual(rows[0]["overall_status"], "HEALTHY")

    def test_stale_heartbeat_is_critical(self):
        heartbeat = (NOW - timedelta(minutes=13)).isoformat()
        rows = mod.build_health(state(heartbeat=heartbeat), now=NOW)
        self.assertEqual(rows[0]["overall_status"], "CRITICAL")
        self.assertIn("MONITOR_HEARTBEAT_STALE", [row["code"] for row in rows])

    def test_missing_lineup_close_to_kickoff_is_critical(self):
        recent_odds = {"last_seen_utc": NOW.isoformat()}
        rows = mod.build_health(
            state(fixture=fixture(15), odds=recent_odds), now=NOW
        )
        self.assertEqual(rows[0]["overall_status"], "CRITICAL")
        self.assertIn("CONFIRMED_XI_MISSING", [row["code"] for row in rows])

    def test_stale_post_xi_odds_are_critical(self):
        stale = {"last_seen_utc": (NOW - timedelta(minutes=13)).isoformat()}
        rows = mod.build_health(
            state(fixture=fixture(45), odds=stale, lineup=True), now=NOW
        )
        self.assertIn("POST_XI_ODDS_STALE", [row["code"] for row in rows])

    def test_same_problem_does_not_repeat_alert(self):
        rows = mod.build_health(None, now=NOW)
        event = mod.notification_event(rows, {})
        saved = {"status": event["status"], "fingerprint": event["fingerprint"]}
        self.assertIsNone(mod.notification_event(rows, saved))

    def test_recovery_sends_one_message(self):
        healthy = mod.build_health(state(), now=NOW)
        previous = {"status": "CRITICAL", "fingerprint": "old"}
        event = mod.notification_event(healthy, previous)
        self.assertIn("RECOVERED", event["message"])

    def test_history_compares_only_latest_snapshot(self):
        old_critical = mod.build_health(None, now=NOW - timedelta(minutes=10))
        old_healthy = mod.build_health(
            state(heartbeat=(NOW - timedelta(minutes=5)).isoformat()),
            now=NOW - timedelta(minutes=5),
        )
        current = mod.build_health(state(), now=NOW)
        self.assertEqual(
            mod.history_change(current, old_critical + old_healthy), []
        )


if __name__ == "__main__":
    unittest.main()
