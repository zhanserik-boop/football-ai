import hashlib
import json
from datetime import datetime, timezone


class OddsProviderError(RuntimeError):
    pass


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def odds_fingerprint(rows):
    canonical = sorted(
        (
            str(row.get("bookmaker_id", "")),
            str(row.get("bookmaker", "")),
            str(row.get("value", "")),
            str(row.get("odd", "")),
        )
        for row in rows
    )
    raw = json.dumps(canonical, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class OddsProvider:
    """Normalized pre-match Asian Handicap provider contract."""

    name = "base"

    def fetch_ah(self, fixture_id):
        raise NotImplementedError


class ApiFootballOddsProvider(OddsProvider):
    """Adapter around the existing API-Football api_get function."""

    name = "api-football"

    def __init__(self, api_get, bet_id=4):
        if not callable(api_get):
            raise TypeError("api_get must be callable")
        self.api_get = api_get
        self.bet_id = int(bet_id)

    def fetch_ah(self, fixture_id):
        fetched_at = utc_now_iso()
        data = self.api_get(
            "/odds",
            {
                "fixture": fixture_id,
                "bet": self.bet_id,
            },
        )

        if not data:
            return [], {
                "provider": self.name,
                "fetched_at_utc": fetched_at,
                "provider_update_utc": None,
                "fingerprint": None,
                "rows": 0,
            }

        rows = []
        provider_updates = []

        for fixture_data in data.get("response", []):
            provider_update = fixture_data.get("update")
            if provider_update:
                provider_updates.append(str(provider_update))

            for bookmaker in fixture_data.get("bookmakers", []):
                bookmaker_id = bookmaker.get("id")
                bookmaker_name = bookmaker.get("name")

                for bet in bookmaker.get("bets", []):
                    try:
                        bet_id = int(bet.get("id", -1))
                    except Exception:
                        continue
                    if bet_id != self.bet_id:
                        continue

                    for value in bet.get("values", []):
                        rows.append(
                            {
                                "bookmaker_id": bookmaker_id,
                                "bookmaker": bookmaker_name,
                                "value": str(value.get("value", "")),
                                "odd": str(value.get("odd", "")),
                            }
                        )

        provider_update_utc = max(provider_updates) if provider_updates else None

        return rows, {
            "provider": self.name,
            "fetched_at_utc": fetched_at,
            "provider_update_utc": provider_update_utc,
            "fingerprint": odds_fingerprint(rows) if rows else None,
            "rows": len(rows),
        }


def build_odds_provider(provider_name, *, api_get=None, bet_id=4):
    """
    Build a provider using a strict normalized contract.

    Unknown providers fail closed. Football AI must never silently switch to
    a different market feed because that would invalidate freshness/CLV audit.
    """

    name = str(provider_name or "api-football").strip().lower()
    aliases = {
        "api-football": "api-football",
        "apifootball": "api-football",
        "api_football": "api-football",
    }
    normalized = aliases.get(name, name)

    if normalized == "api-football":
        return ApiFootballOddsProvider(api_get=api_get, bet_id=bet_id)

    raise OddsProviderError(
        f"Unsupported odds provider: {provider_name!r}. "
        "No silent fallback is allowed."
    )
