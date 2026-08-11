import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import v4_multileague_shadow as v4


NOW = datetime(2026, 8, 11, 14, 30, tzinfo=timezone.utc)


def completed_fixture(fixture_id, date, team_id, opponent_id, gf, ga, team_home=True):
    if team_home:
        home_id, away_id = team_id, opponent_id
        home_goals, away_goals = gf, ga
    else:
        home_id, away_id = opponent_id, team_id
        home_goals, away_goals = ga, gf
    return {
        "fixture": {"id": fixture_id, "date": date.isoformat(), "status": {"short": "FT"}},
        "teams": {"home": {"id": home_id}, "away": {"id": away_id}},
        "goals": {"home": home_goals, "away": away_goals},
    }


def form_payload(team_id, strong):
    rows = []
    for index in range(10):
        if strong:
            gf, ga = (2, 0) if index % 2 == 0 else (2, 1)
        else:
            gf, ga = (0, 2) if index % 2 == 0 else (1, 2)
        rows.append(completed_fixture(
            2000 + team_id * 100 + index,
            NOW - timedelta(days=index + 4),
            team_id,
            9000 + index,
            gf,
            ga,
            team_home=index % 2 == 0,
        ))
    return {"response": rows}


class FakeClient:
    def __init__(self):
        self.api_requests = 0
        self.remaining = "50"
        self.errors = []

    def get(self, endpoint, params=None, ttl_minutes=0, allow_stale=False):
        self.api_requests += 1
        params = params or {}
        if endpoint == "/fixtures" and "date" in params:
            payload = {"response": [{
                "fixture": {
                    "id": 1001,
                    "date": (NOW + timedelta(minutes=30)).isoformat(),
                    "status": {"short": "NS"},
                    "venue": {"name": "Central Stadium"},
                },
                "league": {
                    "id": 2,
                    "name": "UEFA Champions League",
                    "round": "3rd Qualifying Round",
                },
                "teams": {
                    "home": {"id": 1, "name": "Kairat Almaty"},
                    "away": {"id": 2, "name": "Levski Sofia"},
                },
            }]}
        elif endpoint == "/fixtures" and params.get("team") == 1:
            payload = form_payload(1, strong=True)
        elif endpoint == "/fixtures" and params.get("team") == 2:
            payload = form_payload(2, strong=False)
        elif endpoint == "/injuries":
            payload = {"response": []}
        elif endpoint == "/odds":
            bookmakers = []
            for book_id in (10, 20, 30):
                bookmakers.append({
                    "id": book_id,
                    "name": f"Book {book_id}",
                    "bets": [{
                        "id": 4,
                        "values": [
                            {"value": "Home -0.25", "odd": "1.95"},
                            {"value": "Away +0.25", "odd": "1.95"},
                        ],
                    }],
                })
            payload = {"response": [{"update": NOW.isoformat(), "bookmakers": bookmakers}]}
        elif endpoint == "/fixtures/lineups":
            payload = {"response": [
                {
                    "team": {"id": 1, "name": "Kairat Almaty"},
                    "formation": "4-3-3",
                    "startXI": [{"player": {"id": index}} for index in range(11)],
                },
                {
                    "team": {"id": 2, "name": "Levski Sofia"},
                    "formation": "4-2-3-1",
                    "startXI": [{"player": {"id": 100 + index}} for index in range(11)],
                },
            ]}
        else:
            payload = {"response": []}
        return payload, {"source": "TEST", "fetched_utc": NOW.isoformat(), "age_minutes": 0.0}


class V4MultiLeagueTests(unittest.TestCase):
    def test_team_alias_matching(self):
        self.assertEqual(v4.name_similarity("Red Star Belgrade", "FK Crvena Zvezda"), 1.0)
        self.assertEqual(v4.name_similarity("Bodø/Glimt", "Bodo Glimt"), 1.0)

    def test_split_handicap_is_averaged(self):
        self.assertEqual(v4.parse_handicap_value("Home -0.5, -1.0"), ("HOME", -0.75))

    def test_consensus_requires_paired_sides(self):
        rows = [
            {"bookmaker_id": "1", "bookmaker": "A", "side": "HOME", "handicap": -0.5, "odd": 1.95},
            {"bookmaker_id": "1", "bookmaker": "A", "side": "AWAY", "handicap": 0.5, "odd": 1.95},
            {"bookmaker_id": "2", "bookmaker": "B", "side": "HOME", "handicap": -0.5, "odd": 2.00},
            {"bookmaker_id": "2", "bookmaker": "B", "side": "AWAY", "handicap": 0.5, "odd": 1.90},
        ]
        market = v4.market_consensus(rows)
        self.assertEqual(market["home_handicap"], -0.5)
        self.assertEqual(market["bookmakers"], 2)

    def test_data_quality_veto_blocks_low_sample(self):
        low = v4.completed_team_metrics({}, 1, NOW)
        market = {"status": "OK", "bookmakers": 3, "freshness": "FRESH"}
        lineup = {"status": "CONFIRMED", "value_quality": "VALIDATED"}
        quality = v4.data_quality_agent({}, low, low, market, lineup, [], True)
        self.assertEqual(quality["grade"], "LOW")
        decision = v4.moderator_agent(
            {"side": "HOME", "confidence": 0.7, "fair_home_ah": -1.0},
            {**market, "current_home_handicap": -0.25, "home_line_move": 0.0},
            lineup,
            {"side": "HOME"},
            {"level": "LOW", "underdog": "AWAY"},
            {"level": "LOW"},
            {"side": "NEUTRAL"},
            quality,
            True,
        )
        self.assertEqual(decision["decision"], "PASS")

    def test_non_prematch_fixture_is_hard_vetoed(self):
        metrics = v4.completed_team_metrics(form_payload(1, True), 1, NOW)
        market = {"status": "OK", "bookmakers": 3, "freshness": "FRESH"}
        lineup = {"status": "CONFIRMED", "value_quality": "VALIDATED"}
        quality = v4.data_quality_agent(
            {"fixture_id": "1"}, metrics, metrics, market, lineup, [], True,
            pre_match=False,
        )
        self.assertEqual(quality["grade"], "LOW")
        self.assertIn("NOT_PREMATCH", quality["codes"])

    def test_previous_meeting_is_oriented_to_current_home_team(self):
        first_leg = completed_fixture(
            8001, NOW - timedelta(days=6), 2, 1, 1, 0, team_home=True
        )
        context = v4.previous_meeting_context(
            {"response": [first_leg]}, 1, 2, NOW + timedelta(hours=1)
        )
        self.assertTrue(context["second_leg"])
        self.assertEqual(context["aggregate_margin_home"], -1.0)
        self.assertEqual(context["aggregate_state"], "HOME_TRAILING")

    def test_full_pipeline_stays_watch_until_lineup_values_are_validated(self):
        target = {
            "date_local": "2026-08-11",
            "kickoff_local": "20:00",
            "competition": "UEFA Champions League Qualification",
            "home_team": "Kairat Almaty",
            "away_team": "Levski Sofia",
        }
        state = {"schema_version": 1, "fixtures": {}}
        results = v4.collect_and_analyze(
            [target], FakeClient(), "Asia/Almaty", state, now=NOW
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["agents"]["data_quality"]["grade"], "MEDIUM")
        self.assertTrue(results[0]["post_lineup_market_evidence"])
        self.assertEqual(results[0]["moderator"]["decision"], "WATCH")
        self.assertEqual(results[0]["moderator"]["side"], "HOME")

    def test_report_files_are_atomic_and_flattened(self):
        row = {
            "target": {"competition": "Test", "home_team": "A", "away_team": "B"},
            "fixture": None,
            "agents": {"data_quality": {"grade": "LOW"}},
            "moderator": {"decision": "PASS", "side": "", "confidence": 0.0, "reason": "test"},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = f"{directory}/out.csv"
            v4.write_csv(output, [v4.flatten_result(row, NOW.isoformat())])
            with open(output, encoding="utf-8-sig") as handle:
                saved = list(__import__("csv").DictReader(handle))
            self.assertEqual(saved[0]["decision"], "PASS")
            self.assertEqual(saved[0]["shadow_only"], "YES")


if __name__ == "__main__":
    unittest.main()
