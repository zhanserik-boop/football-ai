import os
import tempfile
import unittest

import market_timeline_engine as mte


class MarketTimelineTests(unittest.TestCase):
    def row(self, ts, lineup, side, line, odd, bookmaker, changed=0):
        return {
            "snapshot_utc": ts,
            "fixture_id": "1001",
            "kickoff_utc": "2026-08-21T19:00:00+00:00",
            "minutes_to_kickoff": "60",
            "home_team": "Alpha",
            "away_team": "Beta",
            "lineup_seen": str(lineup),
            "shock_diff": "1.80" if lineup else "",
            "signal": "HOME" if lineup else "",
            "data_quality": "HIGH" if lineup else "",
            "provider_update_utc": "",
            "odds_fingerprint": "fp-" + ts,
            "odds_changed_this_poll": str(changed),
            "odds_last_change_utc": ts,
            "parsed_side": side,
            "parsed_handicap": str(line),
            "odd": str(odd),
            "bookmaker": bookmaker,
        }

    def test_timeline_marks_opening_pre_and_first_post_xi(self):
        rows = []
        for side, line in (("HOME", -0.5), ("AWAY", 0.5)):
            rows.append(self.row("2026-08-21T17:00:00+00:00", 0, side, line, 1.95, "A"))
            rows.append(self.row("2026-08-21T17:00:00+00:00", 0, side, line, 1.97, "B"))
            rows.append(self.row("2026-08-21T18:00:00+00:00", 0, side, line, 1.94, "A"))
            rows.append(self.row("2026-08-21T18:00:00+00:00", 0, side, line, 1.96, "B"))
            rows.append(self.row("2026-08-21T18:10:00+00:00", 1, side, line, 1.91, "A", changed=1))
            rows.append(self.row("2026-08-21T18:10:00+00:00", 1, side, line, 1.93, "B", changed=1))

        timeline = mte.build_market_timeline(rows)
        self.assertEqual(len(timeline), 3)
        self.assertEqual(timeline[0]["is_opening"], 1)
        self.assertEqual(timeline[1]["is_last_pre_xi"], 1)
        self.assertEqual(timeline[2]["is_first_post_xi"], 1)
        self.assertEqual(timeline[2]["freshness_status"], "POST_XI_CHANGED")
        self.assertEqual(timeline[2]["home_bookmakers"], 2)
        self.assertAlmostEqual(float(timeline[2]["home_avg_odds"]), 1.92, places=6)

    def test_post_xi_unchanged_is_not_claimed_fresh(self):
        row = self.row(
            "2026-08-21T18:10:00+00:00",
            1,
            "HOME",
            -0.5,
            1.95,
            "A",
            changed=0,
        )
        self.assertEqual(
            mte.freshness_status([row]),
            "POST_XI_UNCHANGED_OR_UNPROVEN",
        )

    def test_api_football_same_label_is_inverted_for_away(self):
        rows = []
        for book in ("A", "B"):
            rows.append(self.row("2026-08-21T18:10:00+00:00", 1, "HOME", -0.75, 1.95, book, 1))
            rows.append(self.row("2026-08-21T18:10:00+00:00", 1, "AWAY", -0.75, 1.95, book, 1))
        timeline = mte.build_market_timeline(rows)
        self.assertEqual(float(timeline[0]["home_handicap"]), -0.75)
        self.assertEqual(float(timeline[0]["away_handicap"]), 0.75)

    def test_audit_event_hash_is_stable_and_deduplicated(self):
        source = {
            "decision_time_utc": "2026-08-21T18:11:00+00:00",
            "fixture_id": "1001",
            "kickoff_utc": "2026-08-21T19:00:00+00:00",
            "home_team": "Alpha",
            "away_team": "Beta",
            "signal": "HOME",
            "shock_diff": "1.80",
            "data_quality": "HIGH",
            "decision": "BET",
            "current_handicap": "-0.5",
            "current_best_odds": "1.95",
            "current_best_bookmaker": "A",
            "reason": "test",
        }
        first = mte.make_event("AH_DECISION", source, "ah.csv")
        second = mte.make_event("AH_DECISION", source, "ah.csv")
        self.assertEqual(first["event_hash"], second["event_hash"])

        with tempfile.TemporaryDirectory() as tmp:
            old = mte.AUDIT_LEDGER_FILE
            try:
                mte.AUDIT_LEDGER_FILE = os.path.join(tmp, "ledger.csv")
                self.assertEqual(mte.append_new_audit_events([first]), 1)
                self.assertEqual(mte.append_new_audit_events([second]), 0)
            finally:
                mte.AUDIT_LEDGER_FILE = old


if __name__ == "__main__":
    unittest.main()
