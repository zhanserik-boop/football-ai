import unittest

import asian_handicap_v3_r2 as ah


def ladder(layout="same"):
    rows = []
    for book in ("A", "B", "C"):
        for line, home_odd, away_odd in (
            (-1.25, 2.80, 1.42),
            (-1.00, 2.25, 1.67),
            (-0.75, 1.93, 1.97),
            (-0.50, 1.67, 2.25),
        ):
            away_label = line if layout == "same" else -line
            rows.extend([
                {"bookmaker": book, "side": "HOME", "handicap": line, "odd": home_odd},
                {"bookmaker": book, "side": "AWAY", "handicap": away_label, "odd": away_odd},
            ])
    return rows


class AsianHandicapV3R2Tests(unittest.TestCase):
    def test_duplicate_provider_rows_choose_balanced_pair(self):
        rows = [
            {"bookmaker": "A", "side": "HOME", "handicap": -0.5, "odd": 2.30},
            {"bookmaker": "A", "side": "HOME", "handicap": -0.5, "odd": 1.95},
            {"bookmaker": "A", "side": "AWAY", "handicap": -0.5, "odd": 1.95},
        ]
        market = ah.market_consensus(rows)
        self.assertEqual(market["home_average_odds"], 1.95)
        self.assertEqual(market["away_average_odds"], 1.95)

    def test_split_line_is_averaged(self):
        self.assertEqual(
            ah.parse_provider_value("Home -0.5, -1.0"),
            {"side": "HOME", "provider_handicap": -0.75},
        )

    def test_same_label_api_football_layout(self):
        market = ah.market_consensus(ladder("same"))
        self.assertEqual(market["home_handicap"], -0.75)
        self.assertEqual(market["provider_layouts"], ["SAME_LABEL_HOME_AH"])

    def test_conventional_opposite_layout(self):
        market = ah.market_consensus(ladder("opposite"))
        self.assertEqual(market["home_handicap"], -0.75)
        self.assertEqual(market["provider_layouts"], ["OPPOSITE_TEAM_AH"])

    def test_away_market_is_inverted_to_signal_perspective(self):
        market = ah.signal_market(ladder("same"), "AWAY")
        self.assertEqual(market["home_handicap"], -0.75)
        self.assertEqual(market["handicap"], 0.75)
        self.assertAlmostEqual(market["average_odds"], 1.97)

    def test_home_and_away_move_directions_are_opposite(self):
        self.assertEqual(ah.line_move_toward_signal(-0.50, -0.75, "HOME"), 0.25)
        self.assertEqual(ah.line_move_toward_signal(-0.50, -0.75, "AWAY"), -0.25)
        self.assertEqual(ah.line_move_toward_signal(-0.50, -0.25, "HOME"), -0.25)
        self.assertEqual(ah.line_move_toward_signal(-0.50, -0.25, "AWAY"), 0.25)

    def test_unpaired_price_fails_closed(self):
        rows = [{"bookmaker": "A", "side": "HOME", "handicap": -0.5, "odd": 1.95}]
        self.assertIsNone(ah.market_consensus(rows))


if __name__ == "__main__":
    unittest.main()
