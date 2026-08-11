import unittest

import v4_player_value_builder as builder


def player_item(player_id, name, position, minutes, starts, rating, goals=0, assists=0):
    return {
        "player": {"id": player_id, "name": name, "age": 25},
        "statistics": [{
            "team": {"id": 10},
            "games": {
                "minutes": minutes, "appearences": max(starts, 1),
                "lineups": starts, "rating": str(rating), "position": position,
            },
            "goals": {"total": goals, "assists": assists},
        }],
    }


class FakeClient:
    def __init__(self, items):
        self.items = items
        self.api_requests = 0
        self.remaining = "100"
        self.errors = []
        self.calls = []

    def get(self, endpoint, params=None, ttl_minutes=0, allow_stale=False):
        self.api_requests += 1
        self.calls.append((endpoint, dict(params or {})))
        return {
            "response": self.items,
            "paging": {"current": 1, "total": 1},
        }, {"source": "TEST"}


class V4PlayerValueBuilderTests(unittest.TestCase):
    def test_team_ids_are_read_from_prediction_fixtures(self):
        document = {"results": [{"fixture": {
            "home_team_id": 10, "home_team": "Alpha",
            "away_team_id": 20, "away_team": "Beta",
        }}]}
        teams = builder.teams_from_predictions(document)
        self.assertEqual([row["team_id"] for row in teams], [10, 20])

    def test_player_statistics_are_aggregated_for_expected_team_only(self):
        item = player_item(1, "Player One", "Midfielder", 900, 10, 7.2, 3, 4)
        item["statistics"].append({
            "team": {"id": 999},
            "games": {"minutes": 500, "lineups": 5, "appearences": 6},
            "goals": {"total": 10, "assists": 10},
        })
        row = builder.aggregate_player_item(item, 10)
        self.assertEqual(row["minutes"], 900)
        self.assertEqual(row["starts"], 10)
        self.assertEqual(row["goals"] + row["assists"], 7)

    def test_builder_uses_team_season_pages_not_player_requests(self):
        items = [player_item(1, "Keeper", "Goalkeeper", 1000, 11, 7.0)]
        items += [
            player_item(index, f"Player {index}", "Defender", 900-index, 10, 6.8)
            for index in range(2, 17)
        ]
        client = FakeClient(items)
        profile = builder.fetch_team_profile(
            client, {"team_id": 10, "team_name": "Alpha"}, [2026]
        )
        self.assertEqual(client.api_requests, 1)
        self.assertEqual(client.calls[0][0], "/players")
        self.assertNotIn("player", client.calls[0][1])
        self.assertEqual(profile["data_quality"], "HIGH")
        self.assertEqual(len(profile["baseline_player_ids"]), 11)
        self.assertEqual(profile["baseline_player_ids"][0], 1)


if __name__ == "__main__":
    unittest.main()
