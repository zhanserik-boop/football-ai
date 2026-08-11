import unittest

from odds_provider import (
    ApiFootballOddsProvider,
    OddsProviderError,
    build_odds_provider,
    odds_fingerprint,
)


class OddsProviderTests(unittest.TestCase):
    def test_api_football_normalizes_ah_rows(self):
        calls = []

        def fake_api_get(endpoint, params):
            calls.append((endpoint, params))
            return {
                "response": [
                    {
                        "update": "2026-08-21T18:01:00+00:00",
                        "bookmakers": [
                            {
                                "id": 1,
                                "name": "Book A",
                                "bets": [
                                    {
                                        "id": 4,
                                        "values": [
                                            {"value": "Home -0.5", "odd": "1.95"},
                                            {"value": "Away +0.5", "odd": "1.91"},
                                        ],
                                    },
                                    {
                                        "id": 1,
                                        "values": [
                                            {"value": "Home", "odd": "1.60"},
                                        ],
                                    },
                                ],
                            }
                        ],
                    }
                ]
            }

        provider = ApiFootballOddsProvider(fake_api_get, bet_id=4)
        rows, meta = provider.fetch_ah("123")

        self.assertEqual(calls, [("/odds", {"fixture": "123", "bet": 4})])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["bookmaker"], "Book A")
        self.assertEqual(rows[0]["value"], "Home -0.5")
        self.assertEqual(rows[0]["odd"], "1.95")
        self.assertEqual(meta["provider"], "api-football")
        self.assertEqual(meta["rows"], 2)
        self.assertEqual(meta["provider_update_utc"], "2026-08-21T18:01:00+00:00")
        self.assertEqual(meta["fingerprint"], odds_fingerprint(rows))

    def test_empty_provider_response_fails_safe(self):
        provider = ApiFootballOddsProvider(lambda *_args, **_kwargs: None)
        rows, meta = provider.fetch_ah("999")
        self.assertEqual(rows, [])
        self.assertIsNone(meta["fingerprint"])
        self.assertEqual(meta["rows"], 0)

    def test_fingerprint_is_order_independent(self):
        rows_a = [
            {"bookmaker_id": 1, "bookmaker": "A", "value": "Home -0.5", "odd": "1.95"},
            {"bookmaker_id": 2, "bookmaker": "B", "value": "Home -0.5", "odd": "1.93"},
        ]
        rows_b = list(reversed(rows_a))
        self.assertEqual(odds_fingerprint(rows_a), odds_fingerprint(rows_b))

    def test_unknown_provider_fails_closed(self):
        with self.assertRaises(OddsProviderError):
            build_odds_provider("mystery-feed", api_get=lambda *_args: None)

    def test_factory_aliases_api_football(self):
        provider = build_odds_provider(
            "api_football",
            api_get=lambda *_args, **_kwargs: {"response": []},
            bet_id=4,
        )
        self.assertIsInstance(provider, ApiFootballOddsProvider)


if __name__ == "__main__":
    unittest.main()
