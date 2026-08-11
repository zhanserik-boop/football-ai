from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MONITOR = ROOT / "market_monitor_v2.py"


class MarketMonitorProviderIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MONITOR.read_text(encoding="utf-8-sig")

    def test_monitor_imports_provider_builder(self):
        self.assertIn(
            "from odds_provider import build_odds_provider",
            self.source,
        )

    def test_monitor_builds_provider_once(self):
        self.assertIn("odds_provider = build_odds_provider(", self.source)
        self.assertIn("FOOTBALL_AI_ODDS_PROVIDER", self.source)

    def test_ah_fetch_routes_through_provider(self):
        self.assertIn(
            "def get_ah_odds(fixture_id):\n    return odds_provider.fetch_ah(fixture_id)",
            self.source,
        )

    def test_provider_name_is_audited(self):
        self.assertIn('"provider": meta.get("provider") or odds_provider.name', self.source)
        self.assertIn('print("Odds provider:", odds_provider.name)', self.source)

    def test_no_silent_provider_fallback_in_monitor(self):
        self.assertNotIn("except OddsProviderError", self.source)


if __name__ == "__main__":
    unittest.main()
