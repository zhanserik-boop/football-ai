import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import v4_lineup_source_router as router


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def competitor(team_id, name, side):
    return {
        "id": str(team_id),
        "homeAway": side,
        "team": {"id": str(team_id), "displayName": name},
    }


def espn_event(home="Kairat Almaty", away="Levski Sofia", state="pre"):
    return {
        "id": "401902671",
        "date": (NOW + timedelta(minutes=45)).isoformat(),
        "status": {"type": {"state": state}},
        "competitions": [{
            "competitors": [
                competitor(1, home, "home"),
                competitor(2, away, "away"),
            ],
        }],
    }


def roster(prefix):
    return [
        {
            "athlete": {"id": f"{prefix}{index}", "displayName": f"{prefix} Player {index}"},
            "starter": True,
            "jersey": str(index),
            "position": {"displayName": "Player"},
        }
        for index in range(1, 12)
    ]


def summary(home_prefix="H", away_prefix="A", state="pre"):
    return {
        "header": {"competitions": [{"status": {"type": {"state": state}}}]},
        "rosters": [
            {"homeAway": "home", "formation": "4-3-3", "roster": roster(home_prefix)},
            {"homeAway": "away", "formation": "4-2-3-1", "roster": roster(away_prefix)},
        ],
    }


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
            "kickoff_utc": (NOW + timedelta(minutes=45)).isoformat(),
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

    def lineup_summary(self, event_id):
        self.calls.append(event_id)
        return self.payload, {"source": "ESPN"}


class LineupSourceRouterTests(unittest.TestCase):
    def test_matches_espn_fixture_with_name_variants(self):
        row = prediction()
        row["target"]["home_team"] = "Sabah Baku"
        row["target"]["away_team"] = "Aarhus"
        row["fixture"]["home_team"] = "Sabah FA"
        row["fixture"]["away_team"] = "Aarhus"
        matched, score, delta = router.match_espn_fixture(
            row, [espn_event("Sabah FK", "AGF")]
        )
        self.assertEqual(matched["id"], "401902671")
        self.assertGreaterEqual(score, 0.9)
        self.assertEqual(delta, 0.0)

    def test_espn_only_is_research_and_never_value_gate_approved(self):
        row = router.audit_result(
            prediction("NOT_PUBLISHED"), [espn_event()], FakeClient(summary()), now=NOW
        )
        self.assertEqual(row["status"], "ESPN_ONLY_RESEARCH")
        self.assertFalse(row["approved_for_value_gate"])
        self.assertEqual(len(row["espn_home_starters"]), 11)
        self.assertEqual(row["espn_status"], "PUBLISHED_XI")

    def test_two_sources_are_verified_by_name_overlap(self):
        row = router.audit_result(
            prediction("CONFIRMED"), [espn_event()], FakeClient(summary()), now=NOW
        )
        self.assertEqual(row["status"], "VERIFIED_TWO_SOURCES")
        self.assertEqual(row["home_name_overlap"], 11)
        self.assertEqual(row["away_name_overlap"], 11)

    def test_conflict_fails_closed(self):
        row = router.audit_result(
            prediction("CONFIRMED"),
            [espn_event()],
            FakeClient(summary("Different H", "Different A")),
            now=NOW,
        )
        self.assertEqual(row["status"], "SOURCE_CONFLICT")
        self.assertFalse(row["approved_for_value_gate"])

    def test_live_event_skips_per_event_lineup_request(self):
        client = FakeClient(summary())
        row = router.audit_result(
            prediction("NOT_PUBLISHED"), [espn_event(state="in")], client, now=NOW
        )
        self.assertEqual(row["status"], "NOT_PREMATCH")
        self.assertEqual(client.calls, [])

    def test_outside_active_window_does_not_query_lineup(self):
        row_input = prediction("NOT_QUERIED")
        row_input["fixture"]["kickoff_utc"] = (NOW + timedelta(minutes=120)).isoformat()
        candidate = espn_event()
        candidate["date"] = row_input["fixture"]["kickoff_utc"]
        client = FakeClient({})
        row = router.audit_result(row_input, [candidate], client, now=NOW)
        self.assertEqual(client.calls, [])
        self.assertEqual(row["espn_status"], "NOT_QUERIED")

    def test_wrong_home_team_is_not_matched(self):
        row = prediction()
        row["fixture"]["home_team"] = "Celta Vigo"
        row["fixture"]["away_team"] = "Ararat-Armenia"
        matched, _, _ = router.match_espn_fixture(
            row, [espn_event("NK Celje", "Ararat-Armenia")]
        )
        self.assertIsNone(matched)

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
