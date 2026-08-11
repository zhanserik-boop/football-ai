import unittest
from collections import defaultdict, deque
from types import MethodType

from ah_agent_v2 import SHOCK_THRESHOLD, decide
from live_lineup_engine import LiveLineupEngine, MIN_HISTORY, WINDOW


class LineupEngineInvariantTests(unittest.TestCase):
    def make_engine(self):
        engine = LiveLineupEngine.__new__(LiveLineupEngine)
        engine.team_games = defaultdict(int)
        engine.known_players = defaultdict(set)
        engine.starter_history = defaultdict(lambda: deque(maxlen=WINDOW))
        engine.minutes_history = defaultdict(lambda: deque(maxlen=WINDOW))
        engine.rating_history = defaultdict(lambda: deque(maxlen=WINDOW))
        engine.current_squads = defaultdict(set)
        engine.current_squad_info = defaultdict(dict)
        return engine

    def add_history(self, engine, team, player_id, starts, minutes, ratings):
        key = (team, str(player_id))
        engine.known_players[team].add(str(player_id))
        engine.starter_history[key].extend(starts)
        engine.minutes_history[key].extend(minutes)
        engine.rating_history[key].extend(ratings)

    def test_transferred_out_player_is_not_expected_xi_eligible(self):
        engine = self.make_engine()
        team = "Example FC"

        for i in range(1, 13):
            pid = str(i)
            engine.current_squads[team].add(pid)
            engine.current_squad_info[team][pid] = {"player": f"Player {pid}", "position": ""}
            self.add_history(engine, team, pid, [1, 1, 1], [90, 90, 90], [7.0, 7.0, 7.0])

        transferred_out = "999"
        self.add_history(
            engine,
            team,
            transferred_out,
            [1] * 10,
            [90] * 10,
            [8.0] * 10,
        )

        expected_ids = {x["player_id"] for x in engine.get_expected_xi(team)}
        self.assertNotIn(transferred_out, expected_ids)
        self.assertEqual(len(expected_ids), 11)

    def test_epl_to_epl_transfer_reuses_persistent_player_history(self):
        engine = self.make_engine()
        old_team = "Old Club"
        new_team = "New Club"
        player_id = "77"
        engine.current_squads[new_team].add(player_id)
        engine.current_squad_info[new_team][player_id] = {"player": "Transfer Player", "position": "M"}

        self.add_history(
            engine,
            old_team,
            player_id,
            [1] * MIN_HISTORY,
            [90] * MIN_HISTORY,
            [7.2] * MIN_HISTORY,
        )

        info = engine.get_live_player_score(new_team, player_id)
        self.assertEqual(info["player_status"], "TRANSFERRED_EPL_HISTORY")
        self.assertEqual(info["history_source"], old_team)
        self.assertGreater(info["score"], 0)

    def test_new_player_stays_unknown_instead_of_receiving_invented_strength(self):
        engine = self.make_engine()
        team = "Example FC"
        player_id = "12345"
        engine.current_squads[team].add(player_id)
        engine.current_squad_info[team][player_id] = {"player": "New Player", "position": "F"}

        info = engine.get_live_player_score(team, player_id)
        self.assertEqual(info["player_status"], "NEW_UNKNOWN")
        self.assertIsNone(info["history_source"])
        self.assertEqual(info["history_games"], 0)
        self.assertEqual(info["score"], 0.0)

    def test_frozen_lineup_shock_threshold_is_exactly_1_5(self):
        self.assertEqual(SHOCK_THRESHOLD, 1.50)

        engine = self.make_engine()

        def fake_team(self, team, starters):
            shock = 1.5 if team == "Home" else 0.0
            return {
                "coverage": 1.0,
                "lineup_shock": shock,
                "non_current_starters": 0,
            }

        engine.calculate_live_team = MethodType(fake_team, engine)
        result = engine.calculate_match("Home", "Away", [], [], threshold=1.5)
        self.assertEqual(result["signal"], "HOME")
        self.assertEqual(result["shock_diff"], 1.5)


class AHAgentInvariantTests(unittest.TestCase):
    def base_row(self, **overrides):
        row = {
            "signal": "HOME",
            "shock_diff": 1.8,
            "abs_shock": 1.8,
            "data_quality": "HIGH",
        }
        row.update(overrides)
        return row

    def market(self, handicap, odds=1.95, bookmakers=3):
        return {
            "handicap": handicap,
            "average_odds": odds,
            "best_odds": odds,
            "best_bookmaker": "TestBook",
            "bookmakers": bookmakers,
            "snapshot_time": None,
        }

    def test_low_quality_can_never_be_bet(self):
        result = decide(
            self.base_row(data_quality="LOW"),
            self.market(-0.5),
            self.market(-0.5),
        )
        self.assertEqual(result["decision"], "NO BET")

    def test_below_threshold_is_no_signal(self):
        result = decide(
            self.base_row(abs_shock=1.49, shock_diff=1.49),
            self.market(-0.5),
            self.market(-0.5),
        )
        self.assertEqual(result["decision"], "NO SIGNAL")

    def test_quarter_line_move_is_late(self):
        result = decide(
            self.base_row(),
            self.market(-0.5),
            self.market(-0.75),
        )
        self.assertEqual(result["decision"], "LATE")

    def test_single_bookmaker_is_not_bet(self):
        result = decide(
            self.base_row(),
            self.market(-0.5),
            self.market(-0.5, bookmakers=1),
        )
        self.assertEqual(result["decision"], "WATCH")


if __name__ == "__main__":
    unittest.main(verbosity=2)
