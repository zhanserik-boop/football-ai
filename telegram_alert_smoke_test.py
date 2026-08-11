import csv
import os

import run_live_system as live


TEST_AH = "_test_ah.csv"
TEST_MASTER = "_test_master.csv"
TEST_BTTS = "_test_btts.csv"
TEST_STATE = "_test_telegram_state.json"


def write_csv(filename, rows):
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)

    with open(filename, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def cleanup():
    for filename in [
        TEST_AH,
        TEST_MASTER,
        TEST_BTTS,
        TEST_STATE,
        TEST_STATE + ".tmp",
    ]:
        if os.path.exists(filename):
            os.remove(filename)


cleanup()

write_csv(
    TEST_AH,
    [
        {
            "fixture_id": "900001",
            "kickoff_utc": "2026-08-21T19:00:00+00:00",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "decision": "LATE",
            "current_handicap": "-0.50",
            "current_avg_odds": "1.91",
            "line_move_toward_signal": "0.25",
            "reason": "TEST: market already moved",
        }
    ],
)

write_csv(
    TEST_MASTER,
    [
        {
            "fixture_id": "900002",
            "kickoff_utc": "2026-08-21T19:00:00+00:00",
            "home_team": "Liverpool",
            "away_team": "Tottenham",
            "master_decision": "BET",
            "confidence": "0.78",
            "ah_decision": "BET",
            "ah_handicap": "-0.75",
            "ah_best_odds": "1.95",
            "line_move_toward_signal": "0.00",
        },
        {
            "fixture_id": "900003",
            "kickoff_utc": "2026-08-22T14:00:00+00:00",
            "home_team": "Newcastle",
            "away_team": "Aston Villa",
            "master_decision": "WATCH",
            "confidence": "0.64",
            "ah_decision": "WATCH",
            "ah_handicap": "-0.25",
            "ah_best_odds": "1.90",
            "line_move_toward_signal": "0.10",
        },
    ],
)

write_csv(
    TEST_BTTS,
    [
        {
            "fixture_id": "900004",
            "kickoff_utc": "2026-08-22T16:30:00+00:00",
            "home_team": "Manchester United",
            "away_team": "Brighton",
            "decision": "SHADOW BET",
            "model_yes": "0.625",
            "market_yes": "0.580",
            "edge_yes": "0.045",
            "best_yes_odds": "1.82",
            "best_yes_bookmaker": "TEST BOOK",
        }
    ],
)

live.AH_LATEST_FILE = TEST_AH
live.MASTER_LIVE_FILE = TEST_MASTER
live.BTTS_WATCH_FILE = TEST_BTTS
live.TELEGRAM_STATE_FILE = TEST_STATE

state = live.load_telegram_state()

print()
print("=== FIRST PASS: 4 Telegram messages expected ===")
live.notify_ah_late(state)
live.notify_master(state)
live.notify_btts(state)

print()
print("=== SECOND PASS: 0 Telegram messages expected ===")
live.notify_ah_late(state)
live.notify_master(state)
live.notify_btts(state)

print()
print("TEST COMPLETE")
print("Temporary files removed.")

cleanup()
