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

    def test_ah_extraction_preserves_raw_provider_values_for_audit(self):
        payload = {"response": [{
            "update": NOW.isoformat(),
            "bookmakers": [{
                "id": 10,
                "name": "Audit Book",
                "bets": [{
                    "id": 4,
                    "name": "Asian Handicap",
                    "values": [
                        {"value": "Home -0.5, -1.0", "odd": "1.91"},
                        {"value": "Away +0.5, +1.0", "odd": "1.97"},
                    ],
                }],
            }],
        }]}
        rows, provider_update = v4.extract_ah_rows(payload)
        self.assertEqual(provider_update, NOW.isoformat())
        self.assertEqual(rows[0]["raw_value"], "Home -0.5, -1.0")
        self.assertEqual(rows[0]["bet_name"], "Asian Handicap")
        self.assertEqual(rows[0]["handicap"], -0.75)

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

    def test_consensus_selects_balanced_main_line_from_alternative_ladder(self):
        rows = []
        for book_id in ("1", "2", "3"):
            for home_line, home_odd, away_odd in (
                (-1.0, 5.00, 1.15),
                (0.0, 3.20, 1.35),
                (1.0, 1.95, 1.95),
                (2.0, 1.20, 4.20),
            ):
                rows.extend([
                    {
                        "bookmaker_id": book_id, "bookmaker": book_id,
                        "side": "HOME", "handicap": home_line, "odd": home_odd,
                    },
                    {
                        "bookmaker_id": book_id, "bookmaker": book_id,
                        "side": "AWAY", "handicap": -home_line, "odd": away_odd,
                    },
                ])
        market = v4.market_consensus(rows)
        self.assertEqual(market["home_handicap"], 1.0)
        self.assertEqual(market["bookmakers"], 3)
        self.assertEqual(market["bookmakers_with_main_line"], 3)
        self.assertEqual(market["main_line_agreement"], 1.0)
        self.assertEqual(market["main_line_spread"], 0.0)
        self.assertEqual(len(market["selected_bookmaker_lines"]), 3)
        self.assertEqual(
            market["line_vote_counts"],
            [{"home_line": 1.0, "bookmakers": 3}],
        )
        self.assertEqual(market["consensus_version"], 2)

    def test_new_consensus_version_resets_invalid_opening_baseline(self):
        consensus = {
            "home_handicap": 1.0, "home_avg_odds": 1.95,
            "away_avg_odds": 1.95, "fair_home_cover_probability": 0.5,
            "bookmakers": 3, "best_home_odds": 2.0, "best_away_odds": 2.0,
            "bookmakers_with_main_line": 3, "consensus_version": 2,
        }
        old_state = {
            "market_consensus_version": 1, "opening_home_handicap": 0.0,
            "last_odds_fingerprint": "old",
        }
        market = v4.market_agent(
            consensus, NOW.isoformat(), old_state, "new", NOW.isoformat(), NOW
        )
        self.assertEqual(market["opening_home_handicap"], 1.0)
        self.assertEqual(market["home_line_move"], 0.0)
        state = {"fixtures": {"10": dict(old_state)}}
        v4.snapshot_state_update(state, "10", market, "new", {"confirmed": False}, NOW)
        self.assertEqual(state["fixtures"]["10"]["opening_home_handicap"], 1.0)
        self.assertEqual(state["fixtures"]["10"]["market_consensus_version"], 2)

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

    def test_market_audit_document_contains_no_credentials(self):
        results = [{
            "market_audit": {
                "fixture_id": "1001",
                "raw_ah_rows": [{"raw_value": "Home -0.25", "odd": 1.95}],
            },
        }]
        document = v4.build_market_audit_document(results, NOW.isoformat())
        self.assertFalse(document["contains_secrets"])
        self.assertEqual(document["matches"][0]["fixture_id"], "1001")


if __name__ == "__main__":
    unittest.main()
