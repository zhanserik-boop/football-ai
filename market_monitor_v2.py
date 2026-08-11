import os
import csv
import json
import re
import time

from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from live_lineup_engine import LiveLineupEngine
from odds_provider import build_odds_provider


# =========================================================
# CONFIG
# =========================================================

load_dotenv()

API_KEY = (
    os.getenv("API_FOOTBALL_KEY")
    or os.getenv("API_KEY")
    or os.getenv("APISPORTS_KEY")
)

if not API_KEY:
    raise RuntimeError("API key not found in .env")

HEADERS = {"x-apisports-key": API_KEY}
BASE_URL = "https://v3.football.api-sports.io"

LEAGUE_ID = 39
SEASON = 2026
AH_BET_ID = 4
SHOCK_THRESHOLD = 1.5
LOOKAHEAD_HOURS = 48
MAIN_LOOP_SECONDS = 5 * 60
ODDS_PROVIDER_NAME = os.getenv("FOOTBALL_AI_ODDS_PROVIDER", "api-football")

SNAPSHOT_FILE = "market_snapshots_v2.csv"
LINEUP_FILE = "live_lineups_v2.csv"
SIGNAL_FILE = "lineup_signals_live.csv"
STATE_FILE = "market_monitor_v2_state.json"


# =========================================================
# ENGINE
# =========================================================

print("\nInitializing historical Lineup Shock engine...")
engine = LiveLineupEngine()


# =========================================================
# API
# =========================================================

def api_get(endpoint, params=None):
    try:
        response = requests.get(
            BASE_URL + endpoint,
            headers=HEADERS,
            params=params or {},
            timeout=30,
        )

        if response.status_code != 200:
            print("HTTP ERROR:", response.status_code, endpoint)
            return None

        data = response.json()

        if data.get("errors"):
            print("API ERROR:", endpoint, data["errors"])
            return None

        return data

    except Exception as e:
        print("REQUEST ERROR:", endpoint, repr(e))
        return None


# Odds source is now provider-agnostic. Fixture/lineup API remains unchanged.
odds_provider = build_odds_provider(
    ODDS_PROVIDER_NAME,
    api_get=api_get,
    bet_id=AH_BET_ID,
)


# =========================================================
# STATE
# =========================================================

def default_state():
    return {
        "last_fixture_refresh": None,
        "fixtures": {},
        "lineup_first_seen": {},
        "lineup_results": {},
        "signal_entries": {},
        "odds_freshness": {},
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        return default_state()

    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            loaded = json.load(f)
        state = default_state()
        state.update(loaded)
        return state
    except Exception:
        return default_state()


def save_state():
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


state = load_state()


# =========================================================
# CSV / TIME
# =========================================================

def append_csv(filename, fieldnames, row):
    exists = os.path.exists(filename)
    with open(filename, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def utc_now():
    return datetime.now(timezone.utc)


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


# =========================================================
# SMART REQUEST INTERVAL
# =========================================================

def odds_interval_minutes(minutes_to_kickoff, lineup_seen):
    if lineup_seen:
        return 5
    if minutes_to_kickoff <= 90:
        return 10
    if minutes_to_kickoff <= 180:
        return 30
    if minutes_to_kickoff <= 720:
        return 60
    return 180


def lineup_interval_minutes(minutes_to_kickoff):
    if minutes_to_kickoff <= 60:
        return 5
    if minutes_to_kickoff <= 120:
        return 10
    return None


def request_is_due(last_time, interval_minutes):
    if not last_time:
        return True
    dt = parse_dt(last_time)
    if dt is None:
        return True
    elapsed = (utc_now() - dt).total_seconds() / 60
    return elapsed >= interval_minutes


# =========================================================
# FIXTURES
# =========================================================

def refresh_fixtures():
    now = utc_now()
    end = now + timedelta(hours=LOOKAHEAD_HOURS)

    data = api_get(
        "/fixtures",
        {
            "league": LEAGUE_ID,
            "season": SEASON,
            "from": now.date().isoformat(),
            "to": end.date().isoformat(),
            "status": "NS",
        },
    )

    if not data:
        return

    found = {}

    for item in data.get("response", []):
        fixture = item.get("fixture", {})
        teams = item.get("teams", {})
        fixture_id = fixture.get("id")
        kickoff_raw = fixture.get("date")

        if fixture_id is None or not kickoff_raw:
            continue

        kickoff = parse_dt(kickoff_raw)
        if kickoff is None:
            continue

        hours_to_match = (kickoff - now).total_seconds() / 3600
        if not (-0.1 <= hours_to_match <= LOOKAHEAD_HOURS):
            continue

        fixture_id = str(fixture_id)
        previous = state["fixtures"].get(fixture_id, {})

        found[fixture_id] = {
            "fixture_id": fixture_id,
            "kickoff": kickoff.isoformat(),
            "home_team": teams.get("home", {}).get("name"),
            "away_team": teams.get("away", {}).get("name"),
            "last_odds_check": previous.get("last_odds_check"),
            "last_lineup_check": previous.get("last_lineup_check"),
        }

    previous_fixtures = state.get("fixtures", {})
    carried_forward = 0

    for old_id, old_fixture in previous_fixtures.items():
        if old_id in found:
            continue

        old_kickoff = parse_dt(old_fixture.get("kickoff"))
        if old_kickoff is None:
            continue

        old_hours_to_match = (old_kickoff - now).total_seconds() / 3600
        if 0 < old_hours_to_match <= LOOKAHEAD_HOURS:
            found[old_id] = old_fixture
            carried_forward += 1

    state["fixtures"] = found
    state["last_fixture_refresh"] = now.isoformat()
    save_state()

    print("Upcoming fixtures:", len(found))
    if carried_forward:
        print("Carried forward from previous state:", carried_forward)


# =========================================================
# LINEUPS
# =========================================================

def get_lineups(fixture_id):
    data = api_get("/fixtures/lineups", {"fixture": fixture_id})
    if not data:
        return []
    return data.get("response", [])


def confirmed_lineups_ready(fixture, lineups):
    if not isinstance(lineups, list):
        return False

    home_team = str(fixture.get("home_team", "")).strip()
    away_team = str(fixture.get("away_team", "")).strip()
    required = {home_team, away_team}
    complete = set()

    for team_data in lineups:
        team_name = str(team_data.get("team", {}).get("name", "")).strip()
        starters = team_data.get("startXI", [])
        if (
            team_name in required
            and isinstance(starters, list)
            and len(starters) >= 11
        ):
            complete.add(team_name)

    return complete == required


LINEUP_FIELDS = [
    "first_seen_utc",
    "fixture_id",
    "kickoff_utc",
    "minutes_to_kickoff",
    "home_team",
    "away_team",
    "team",
    "formation",
    "player_id",
    "player",
    "position",
    "starter",
]


def save_lineups(fixture, lineups, first_seen):
    kickoff = parse_dt(fixture["kickoff"])
    minutes_to_kickoff = (kickoff - utc_now()).total_seconds() / 60

    for team_data in lineups:
        team_name = team_data.get("team", {}).get("name")
        formation = team_data.get("formation")

        for starter_flag, collection in (
            (1, team_data.get("startXI", [])),
            (0, team_data.get("substitutes", [])),
        ):
            for item in collection:
                player = item.get("player", {})
                append_csv(
                    LINEUP_FILE,
                    LINEUP_FIELDS,
                    {
                        "first_seen_utc": first_seen,
                        "fixture_id": fixture["fixture_id"],
                        "kickoff_utc": fixture["kickoff"],
                        "minutes_to_kickoff": round(minutes_to_kickoff, 2),
                        "home_team": fixture["home_team"],
                        "away_team": fixture["away_team"],
                        "team": team_name,
                        "formation": formation,
                        "player_id": player.get("id"),
                        "player": player.get("name"),
                        "position": player.get("pos"),
                        "starter": starter_flag,
                    },
                )


# =========================================================
# AH ODDS + FRESHNESS
# =========================================================

def get_ah_odds(fixture_id):
    return odds_provider.fetch_ah(fixture_id)


def register_odds_observation(fixture_id, odds, meta):
    now = utc_now()
    now_iso = now.isoformat()
    fingerprint = meta.get("fingerprint")

    previous = state["odds_freshness"].get(fixture_id, {})
    previous_fingerprint = previous.get("fingerprint")

    changed = bool(
        fingerprint
        and previous_fingerprint
        and fingerprint != previous_fingerprint
    )

    first_seen_utc = previous.get("first_seen_utc") or now_iso
    last_change_utc = previous.get("last_change_utc")

    if previous_fingerprint is None and fingerprint:
        last_change_utc = now_iso
    elif changed:
        last_change_utc = now_iso

    record = {
        "provider": meta.get("provider") or odds_provider.name,
        "fingerprint": fingerprint,
        "first_seen_utc": first_seen_utc,
        "last_seen_utc": now_iso,
        "last_change_utc": last_change_utc,
        "provider_update_utc": meta.get("provider_update_utc"),
        "rows": len(odds),
    }

    state["odds_freshness"][fixture_id] = record
    save_state()

    return {
        **record,
        "changed_this_poll": changed,
    }


def freshness_after_signal(fixture_id, signal_time, observation):
    signal_dt = parse_dt(signal_time)
    if signal_dt is None or not observation:
        return False, "No valid signal/freshness timestamp"

    first_seen_dt = parse_dt(observation.get("first_seen_utc"))
    last_change_dt = parse_dt(observation.get("last_change_utc"))
    provider_update_dt = parse_dt(observation.get("provider_update_utc"))
    provider_name = observation.get("provider") or odds_provider.name

    if first_seen_dt is None or first_seen_dt >= signal_dt:
        return False, "No pre-lineup odds baseline observed"

    if provider_update_dt is not None and provider_update_dt > signal_dt:
        return True, f"{provider_name} update timestamp is after lineup signal"

    if last_change_dt is not None and last_change_dt > signal_dt:
        return True, f"{provider_name} odds payload changed after lineup signal"

    return False, f"{provider_name} odds payload has not changed since lineup signal"


# =========================================================
# PARSE AH VALUE
# =========================================================

def parse_ah_value(value):
    if not value:
        return None

    text = value.strip().replace("−", "-")
    lower = text.lower()

    if lower.startswith("home"):
        side = "HOME"
    elif lower.startswith("away"):
        side = "AWAY"
    else:
        return None

    numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
    if not numbers:
        return None

    try:
        handicap = float(numbers[-1])
    except Exception:
        return None

    return {"side": side, "handicap": handicap}


# =========================================================
# SNAPSHOTS
# =========================================================

SNAPSHOT_FIELDS = [
    "snapshot_utc",
    "fixture_id",
    "kickoff_utc",
    "minutes_to_kickoff",
    "home_team",
    "away_team",
    "lineup_seen",
    "shock_diff",
    "signal",
    "data_quality",
    "odds_provider",
    "provider_update_utc",
    "odds_fingerprint",
    "odds_changed_this_poll",
    "odds_last_change_utc",
    "bookmaker_id",
    "bookmaker",
    "value",
    "parsed_side",
    "parsed_handicap",
    "odd",
]


def save_snapshot(fixture, odds, observation):
    now = utc_now()
    kickoff = parse_dt(fixture["kickoff"])
    minutes_to_kickoff = (kickoff - now).total_seconds() / 60
    fixture_id = fixture["fixture_id"]

    result = state["lineup_results"].get(fixture_id)
    lineup_seen = result is not None
    shock_diff = result.get("shock_diff") if result else ""
    signal = result.get("signal") if result else ""
    data_quality = result.get("data_quality") if result else ""

    for item in odds:
        parsed = parse_ah_value(item["value"])

        append_csv(
            SNAPSHOT_FILE,
            SNAPSHOT_FIELDS,
            {
                "snapshot_utc": now.isoformat(),
                "fixture_id": fixture_id,
                "kickoff_utc": fixture["kickoff"],
                "minutes_to_kickoff": round(minutes_to_kickoff, 2),
                "home_team": fixture["home_team"],
                "away_team": fixture["away_team"],
                "lineup_seen": int(lineup_seen),
                "shock_diff": shock_diff,
                "signal": signal,
                "data_quality": data_quality,
                "odds_provider": observation.get("provider") or odds_provider.name,
                "provider_update_utc": observation.get("provider_update_utc") or "",
                "odds_fingerprint": observation.get("fingerprint") or "",
                "odds_changed_this_poll": int(
                    bool(observation.get("changed_this_poll"))
                ),
                "odds_last_change_utc": observation.get("last_change_utc") or "",
                "bookmaker_id": item["bookmaker_id"],
                "bookmaker": item["bookmaker"],
                "value": item["value"],
                "parsed_side": parsed["side"] if parsed else "",
                "parsed_handicap": parsed["handicap"] if parsed else "",
                "odd": item["odd"],
            },
        )


# =========================================================
# CONSENSUS ENTRY PRICE
# =========================================================

def get_signal_market(odds, signal):
    candidates = []

    for item in odds:
        parsed = parse_ah_value(item["value"])
        if not parsed or parsed["side"] != signal:
            continue

        try:
            odd = float(item["odd"])
        except Exception:
            continue

        candidates.append(
            {
                "bookmaker": item["bookmaker"],
                "bookmaker_id": item["bookmaker_id"],
                "handicap": parsed["handicap"],
                "odd": odd,
            }
        )

    if not candidates:
        return None

    counts = {}
    for x in candidates:
        line = x["handicap"]
        counts[line] = counts.get(line, 0) + 1

    max_count = max(counts.values())
    candidate_lines = [
        line for line, count in counts.items() if count == max_count
    ]

    ordered_lines = sorted(x["handicap"] for x in candidates)
    n_lines = len(ordered_lines)
    if n_lines % 2 == 1:
        median_line = ordered_lines[n_lines // 2]
    else:
        median_line = (
            ordered_lines[n_lines // 2 - 1]
            + ordered_lines[n_lines // 2]
        ) / 2.0

    consensus_line = min(
        candidate_lines,
        key=lambda line: abs(line - median_line),
    )

    same_line = [
        x for x in candidates if x["handicap"] == consensus_line
    ]

    average_odds = sum(x["odd"] for x in same_line) / len(same_line)
    best_odds = max(x["odd"] for x in same_line)
    best_book = max(same_line, key=lambda x: x["odd"])

    return {
        "handicap": consensus_line,
        "average_odds": average_odds,
        "best_odds": best_odds,
        "best_bookmaker": best_book["bookmaker"],
        "bookmakers_on_line": len(same_line),
    }


# =========================================================
# SIGNAL ENTRY FILE
# =========================================================

SIGNAL_FIELDS = [
    "signal_time_utc",
    "entry_time_utc",
    "fixture_id",
    "kickoff_utc",
    "minutes_to_kickoff_at_signal",
    "minutes_to_kickoff_at_entry",
    "home_team",
    "away_team",
    "home_shock",
    "away_shock",
    "shock_diff",
    "abs_shock",
    "signal",
    "data_quality",
    "home_coverage",
    "away_coverage",
    "entry_handicap",
    "entry_avg_odds",
    "entry_best_odds",
    "entry_best_bookmaker",
    "bookmakers_on_entry_line",
]


def store_signal_result(fixture, result):
    fixture_id = fixture["fixture_id"]

    clean = {
        "shock_diff": result["shock_diff"],
        "abs_shock": result["abs_shock"],
        "signal": result["signal"],
        "data_quality": result["data_quality"],
        "home_shock": result["home"]["lineup_shock"],
        "away_shock": result["away"]["lineup_shock"],
        "home_coverage": result["home"]["coverage"],
        "away_coverage": result["away"]["coverage"],
        "signal_time": utc_now().isoformat(),
    }

    state["lineup_results"][fixture_id] = clean
    save_state()


# =========================================================
# CAPTURE FIRST PROVABLY FRESH TRADEABLE PRICE
# =========================================================

def capture_entry_if_needed(fixture, odds, observation):
    fixture_id = fixture["fixture_id"]
    result = state["lineup_results"].get(fixture_id)

    if not result:
        return

    signal = result["signal"]
    if signal not in ("HOME", "AWAY"):
        return

    if fixture_id in state["signal_entries"]:
        return

    fresh, freshness_reason = freshness_after_signal(
        fixture_id,
        result["signal_time"],
        observation,
    )

    if not fresh:
        print(
            "Signal exists, but AH quote is NOT proven fresh:",
            freshness_reason,
        )
        print("Entry NOT captured; waiting for a changed post-lineup quote.")
        return

    market = get_signal_market(odds, signal)
    if not market:
        print("Signal exists but no parseable AH price available yet.")
        return

    now = utc_now()
    kickoff = parse_dt(fixture["kickoff"])
    minutes_entry = (kickoff - now).total_seconds() / 60

    if minutes_entry <= 0:
        print(
            "Signal exists but kickoff has already started. "
            "Entry NOT captured."
        )
        return

    signal_time = parse_dt(result["signal_time"])
    minutes_signal = (kickoff - signal_time).total_seconds() / 60

    entry = {
        "signal_time_utc": result["signal_time"],
        "entry_time_utc": now.isoformat(),
        "fixture_id": fixture_id,
        "kickoff_utc": fixture["kickoff"],
        "minutes_to_kickoff_at_signal": round(minutes_signal, 2),
        "minutes_to_kickoff_at_entry": round(minutes_entry, 2),
        "home_team": fixture["home_team"],
        "away_team": fixture["away_team"],
        "home_shock": result["home_shock"],
        "away_shock": result["away_shock"],
        "shock_diff": result["shock_diff"],
        "abs_shock": result["abs_shock"],
        "signal": signal,
        "data_quality": result["data_quality"],
        "home_coverage": result["home_coverage"],
        "away_coverage": result["away_coverage"],
        "entry_handicap": market["handicap"],
        "entry_avg_odds": round(market["average_odds"], 4),
        "entry_best_odds": round(market["best_odds"], 4),
        "entry_best_bookmaker": market["best_bookmaker"],
        "bookmakers_on_entry_line": market["bookmakers_on_line"],
    }

    append_csv(SIGNAL_FILE, SIGNAL_FIELDS, entry)
    state["signal_entries"][fixture_id] = entry
    save_state()

    print()
    print("##############################################")
    print("FRESH POST-LINEUP ENTRY PRICE CAPTURED")
    print("##############################################")
    print(fixture["home_team"], "-", fixture["away_team"])
    print("SIGNAL:", signal)
    print("ShockDiff:", f"{result['shock_diff']:+.2f}")
    print("Quality:", result["data_quality"])
    print("Freshness:", freshness_reason)
    print("AH:", market["handicap"])
    print("Average odds:", f"{market['average_odds']:.3f}")
    print(
        "Best odds:",
        f"{market['best_odds']:.3f}",
        "@",
        market["best_bookmaker"],
    )
    print("T-:", f"{minutes_entry:.1f} min")
    print("##############################################")


# =========================================================
# PROCESS LINEUP
# =========================================================

def process_lineup(fixture, lineups):
    fixture_id = fixture["fixture_id"]

    if fixture_id in state["lineup_results"]:
        return

    now = utc_now()
    state["lineup_first_seen"][fixture_id] = now.isoformat()
    save_lineups(fixture, lineups, now.isoformat())

    try:
        result = engine.calculate_from_api_response(
            home_team=fixture["home_team"],
            away_team=fixture["away_team"],
            api_lineups=lineups,
            threshold=SHOCK_THRESHOLD,
        )
    except Exception as e:
        print("LINEUP ENGINE ERROR:", repr(e))
        return

    engine.print_result(result)
    store_signal_result(fixture, result)

    if result["signal"] in ("HOME", "AWAY"):
        print("\n*** EXTREME LINEUP SIGNAL DETECTED ***")
        freshness = state["odds_freshness"].get(fixture_id, {})
        first_seen = parse_dt(freshness.get("first_seen_utc"))
        signal_time = parse_dt(
            state["lineup_results"][fixture_id].get("signal_time")
        )
        if first_seen and signal_time and first_seen < signal_time:
            print("Pre-lineup AH baseline: YES")
        else:
            print("Pre-lineup AH baseline: NO")
    else:
        print("\nNo extreme signal.")


# =========================================================
# ONE FIXTURE
# =========================================================

def process_fixture(fixture):
    fixture_id = fixture["fixture_id"]
    kickoff = parse_dt(fixture["kickoff"])
    now = utc_now()
    minutes_to_kickoff = (kickoff - now).total_seconds() / 60

    if minutes_to_kickoff <= 0:
        return

    lineup_seen = fixture_id in state["lineup_results"]

    print()
    print(
        fixture["home_team"],
        "-",
        fixture["away_team"],
        "|",
        f"T-{minutes_to_kickoff:.0f}m",
    )

    lineup_interval = lineup_interval_minutes(minutes_to_kickoff)

    if (
        lineup_interval is not None
        and not lineup_seen
        and request_is_due(
            fixture.get("last_lineup_check"),
            lineup_interval,
        )
    ):
        lineups = get_lineups(fixture_id)
        fixture["last_lineup_check"] = now.isoformat()

        if confirmed_lineups_ready(fixture, lineups):
            print("Confirmed XI: YES")
            process_lineup(fixture, lineups)
            lineup_seen = fixture_id in state["lineup_results"]
        else:
            print("Confirmed XI: NO / INCOMPLETE")

    odds_interval = odds_interval_minutes(minutes_to_kickoff, lineup_seen)

    if request_is_due(fixture.get("last_odds_check"), odds_interval):
        odds, meta = get_ah_odds(fixture_id)
        fixture["last_odds_check"] = now.isoformat()

        print("AH provider:", meta.get("provider") or odds_provider.name)
        print("AH rows:", len(odds))

        if odds:
            observation = register_odds_observation(
                fixture_id,
                odds,
                meta,
            )

            print(
                "AH payload changed:",
                "YES" if observation["changed_this_poll"] else "NO",
            )
            print(
                "AH last change UTC:",
                observation.get("last_change_utc"),
            )
            if observation.get("provider_update_utc"):
                print(
                    "Provider update UTC:",
                    observation["provider_update_utc"],
                )

            save_snapshot(fixture, odds, observation)
            capture_entry_if_needed(fixture, odds, observation)

    state["fixtures"][fixture_id] = fixture
    save_state()


# =========================================================
# REFRESH / CYCLE
# =========================================================

def fixture_refresh_due():
    last = state.get("last_fixture_refresh")
    if not last:
        return True
    dt = parse_dt(last)
    if dt is None:
        return True
    minutes = (utc_now() - dt).total_seconds() / 60
    return minutes >= 30


def run_cycle():
    print("\n============================================================")
    print("FOOTBALL AI — MARKET MONITOR V2")
    print(utc_now().isoformat())
    print("============================================================")

    if fixture_refresh_due():
        refresh_fixtures()

    fixtures = list(state["fixtures"].values())

    if not fixtures:
        print(f"No EPL fixtures inside {LOOKAHEAD_HOURS}h window.")
        return

    fixtures.sort(key=lambda x: x["kickoff"])
    for fixture in fixtures:
        process_fixture(fixture)


# =========================================================
# MAIN
# =========================================================

print("\n============================================================")
print("FOOTBALL AI — MARKET MONITOR V2")
print("============================================================")
print("League: EPL")
print("Season:", SEASON)
print("AH Bet ID:", AH_BET_ID)
print("Odds provider:", odds_provider.name)
print("Lineup Shock threshold:", SHOCK_THRESHOLD)
print("Historical engine:", "READY")
print("Freshness guard:", "ENABLED")
print("Snapshots:", SNAPSHOT_FILE)
print("Signals:", SIGNAL_FILE)
print("State:", STATE_FILE)
print("Main loop:", MAIN_LOOP_SECONDS // 60, "minutes")
print("Press CTRL+C to stop.")

while True:
    try:
        run_cycle()
        print(
            "\nNext scheduler cycle in",
            MAIN_LOOP_SECONDS // 60,
            "minutes...",
        )
        time.sleep(MAIN_LOOP_SECONDS)

    except KeyboardInterrupt:
        print("\nMonitor stopped.")
        break

    except Exception as e:
        print("\nUNEXPECTED ERROR:", repr(e))
        print("Retry in 60 seconds...")
        time.sleep(60)
