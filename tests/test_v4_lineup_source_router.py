import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import v4_lineup_source_router as router


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def team(team_id, name):
    return {
        "id": str(team_id),
        "internationalName": name,
        "translations": {"displayName": {"EN": name}},
    }


def uefa_match(home="FC Kairat Almaty", away="PFC Levski Sofia"):
    return {
        "id": "2049001",
        "status": "UPCOMING",
        "kickOffTime": {"dateTime": (NOW + timedelta(minutes=40)).isoformat()},
        "homeTeam": team(1, home),
        "awayTeam": team(2, away),
    }


def field(prefix):
    return [
        {
            "player": {
                "id": f"{prefix}{index}",
                "internationalName": f"{prefix} Player {index}",
            },
            "jerseyNumber": index,
            "isLateUpdate": False,
        }
        for index in range(1, 12)
    ]


def prediction(api_status="NOT_PUBLISHED"):
    lineup = {
        "status": api_status,
        "home_starter_names": [f"H Player {index}" for index in range(1, 12)],
        "away_starter_names": [f"A Player {index}" for index in range(1, 12)],
    }
    return {
        "target": {"home_team": "Kairat Almaty", "away_team": "Levski Sofia"},
        "fixture": {
            "fixture_id": "1001",
            "kickoff_utc": (NOW + timedelta(minutes=40)).isoformat(),
            "home_team": "Kairat Almaty",
            "away_team": "Levski Sofia",
        },
        "analysis_status": "ANALYZED_PREMATCH",
        "agents": {"lineup": lineup},
    }


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def lineup(self, match_id):
        self.calls.append(match_id)
        return self.payload, {"source": "UEFA"}


class LineupSourceRouterTests(unittest.TestCase):
    def test_matches_uefa_fixture_with_prefix_variants(self):
        matched, score, delta = router.match_uefa_fixture(prediction(), [uefa_match()])
        self.assertEqual(matched["id"], "2049001")
        self.assertGreaterEqual(score, 0.9)
        self.assertEqual(delta, 0.0)

    def test_uefa_only_is_research_and_never_value_gate_approved(self):
        payload = {
            "matchId": "2049001",
            "lineupStatus": "TACTICAL_AVAILABLE",
            "homeTeam": {"team": team(1, "Kairat"), "field": field("H")},
            "awayTeam": {"team": team(2, "Levski"), "field": field("A")},
        }
        row = router.audit_result(
            prediction("NOT_PUBLISHED"), [uefa_match()], FakeClient(payload), now=NOW
        )
        self.assertEqual(row["status"], "UEFA_ONLY_RESEARCH")
        self.assertFalse(row["approved_for_value_gate"])
        self.assertEqual(len(row["uefa_home_starters"]), 11)

    def test_two_sources_are_verified_by_name_overlap(self):
        payload = {
            "lineupStatus": "AVAILABLE",
            "homeTeam": {"field": field("H")},
            "awayTeam": {"field": field("A")},
        }
        row = router.audit_result(
            prediction("CONFIRMED"), [uefa_match()], FakeClient(payload), now=NOW
        )
        self.assertEqual(row["status"], "VERIFIED_TWO_SOURCES")
        self.assertEqual(row["home_name_overlap"], 11)
        self.assertEqual(row["away_name_overlap"], 11)

    def test_conflict_fails_closed(self):
        payload = {
            "lineupStatus": "AVAILABLE",
            "homeTeam": {"field": field("Different H")},
            "awayTeam": {"field": field("Different A")},
        }
        row = router.audit_result(
            prediction("CONFIRMED"), [uefa_match()], FakeClient(payload), now=NOW
        )
        self.assertEqual(row["status"], "SOURCE_CONFLICT")
        self.assertFalse(row["approved_for_value_gate"])

    def test_outside_active_window_does_not_query_lineup(self):
        row_input = prediction("NOT_QUERIED")
        row_input["fixture"]["kickoff_utc"] = (NOW + timedelta(minutes=120)).isoformat()
        candidate = uefa_match()
        candidate["kickOffTime"]["dateTime"] = row_input["fixture"]["kickoff_utc"]
        client = FakeClient({})
        row = router.audit_result(row_input, [candidate], client, now=NOW)
        self.assertEqual(client.calls, [])
        self.assertEqual(row["uefa_status"], "NOT_QUERIED")

    def test_csv_contains_no_value_gate_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/audit.csv"
            row = {
                "home_team": "A", "away_team": "B", "status": "WAITING",
                "approved_for_value_gate": "NO",
            }
            router.write_csv(path, [row])
            with open(path, encoding="utf-8-sig") as handle:
                content = handle.read()
            self.assertIn("approved_for_value_gate", content)
            self.assertIn("NO", content)


if __name__ == "__main__":
    unittest.main()
