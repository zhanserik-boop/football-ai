import csv
import json
import os
from datetime import datetime, timezone

import pandas as pd
import requests
from dotenv import load_dotenv

STATE_FILE = "market_monitor_v2_state.json"
HISTORY_FILE = "epl_coach_history.csv"
OBSERVATIONS_FILE = "live_coach_observations.csv"
OUTPUT_FILE = "coach_context_live.csv"
BASE_URL = "https://v3.football.api-sports.io"

OBS_FIELDS = [
    "observed_utc", "fixture_id", "kickoff_utc", "home_team", "away_team",
    "team", "coach_id", "coach", "formation",
]

OUTPUT_FIELDS = [
    "built_utc", "fixture_id", "kickoff_utc", "home_team", "away_team",
    "team", "coach_id", "coach", "formation", "previous_coach",
    "coach_change_flag", "new_manager_match_number", "new_manager_band",
    "shadow_strength", "shadow_only", "source",
]


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null"}:
        return ""
    return text


def read_csv_rows(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader) if reader.fieldnames else []


def write_csv_atomic(path, fields, rows):
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


def append_csv(path, fields, rows):
    if not rows:
        return
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    with open(path, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def load_state(path=STATE_FILE):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def api_key():
    load_dotenv()
    return (
        os.getenv("API_FOOTBALL_KEY")
        or os.getenv("API_KEY")
        or os.getenv("APISPORTS_KEY")
    )


def fetch_lineups(fixture_id, key=None):
    key = key or api_key()
    if not key:
        return []
    try:
        response = requests.get(
            BASE_URL + "/fixtures/lineups",
            headers={"x-apisports-key": key},
            params={"fixture": fixture_id},
            timeout=30,
        )
        if response.status_code != 200:
            return []
        payload = response.json()
        if payload.get("errors"):
            return []
        return payload.get("response", []) or []
    except Exception:
        return []


def extract_coach_rows(fixture, lineups, observed_utc=None):
    observed_utc = observed_utc or utc_now_iso()
    rows = []
    for item in lineups or []:
        team = item.get("team", {}) or {}
        coach = item.get("coach", {}) or {}
        team_name = clean(team.get("name"))
        coach_name = clean(coach.get("name"))
        if not team_name or not coach_name:
            continue
        rows.append({
            "observed_utc": observed_utc,
            "fixture_id": clean(fixture.get("fixture_id")),
            "kickoff_utc": clean(fixture.get("kickoff")),
            "home_team": clean(fixture.get("home_team")),
            "away_team": clean(fixture.get("away_team")),
            "team": team_name,
            "coach_id": clean(coach.get("id")),
            "coach": coach_name,
            "formation": clean(item.get("formation")),
        })
    return rows


def collect_missing_observations(state, existing_rows, fetcher=fetch_lineups):
    existing = {
        (clean(r.get("fixture_id")), clean(r.get("team")))
        for r in existing_rows
    }
    complete_fixtures = set()
    fixture_counts = {}
    for fixture_id, team in existing:
        if fixture_id and team:
            fixture_counts[fixture_id] = fixture_counts.get(fixture_id, 0) + 1
    for fixture_id, count in fixture_counts.items():
        if count >= 2:
            complete_fixtures.add(fixture_id)

    results = state.get("lineup_results", {}) or {}
    fixtures = state.get("fixtures", {}) or {}
    new_rows = []
    requests_made = 0

    for fixture_id in sorted(results):
        fixture_id = clean(fixture_id)
        if not fixture_id or fixture_id in complete_fixtures:
            continue
        fixture = fixtures.get(fixture_id, {}) or {}
        if not fixture:
            continue
        requests_made += 1
        lineups = fetcher(fixture_id)
        rows = extract_coach_rows(fixture, lineups)
        if len(rows) >= 2:
            new_rows.extend(rows)
            complete_fixtures.add(fixture_id)

    return new_rows, requests_made


def historical_last_coach(history_file=HISTORY_FILE):
    if not os.path.exists(history_file):
        return {}
    df = pd.read_csv(history_file, encoding="utf-8-sig")
    required = {"date", "team", "coach"}
    if not required.issubset(df.columns):
        return {}
    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    df = df.dropna(subset=["date"])
    df["team"] = df["team"].astype(str).str.strip()
    df["coach"] = df["coach"].astype(str).str.strip()
    if "coach_status" in df.columns:
        df = df[df["coach_status"].astype(str).str.upper() == "OK"]
    df = df[(df["team"] != "") & (df["coach"] != "")]
    if df.empty:
        return {}
    latest = df.sort_values(["team", "date"]).groupby("team", as_index=False).tail(1)
    return dict(zip(latest["team"], latest["coach"]))


def band_for_match_number(number):
    if number == 1:
        return "MATCH_1", "MODERATE"
    if number in (2, 3):
        return "MATCH_2_3", "STRONG"
    if number in (4, 5):
        return "MATCH_4_5", "WEAK"
    return "NEUTRAL", "NEUTRAL"


def build_live_context(observations, last_historical):
    if not observations:
        return []
    df = pd.DataFrame(observations).copy()
    df["kickoff_dt"] = pd.to_datetime(df["kickoff_utc"], errors="coerce", utc=True)
    df = df.sort_values(["team", "kickoff_dt", "fixture_id", "observed_utc"])
    df = df.drop_duplicates(["fixture_id", "team"], keep="first")

    built = utc_now_iso()
    output = []
    for team, part in df.groupby("team", sort=False):
        previous = clean(last_historical.get(team))
        active_new_manager = False
        spell_match = 0

        for _, row in part.iterrows():
            coach = clean(row.get("coach"))
            changed = int(bool(previous and coach and coach != previous))
            if changed:
                active_new_manager = True
                spell_match = 1
            elif active_new_manager:
                spell_match += 1
            else:
                spell_match = 0

            band, strength = band_for_match_number(spell_match)
            output.append({
                "built_utc": built,
                "fixture_id": clean(row.get("fixture_id")),
                "kickoff_utc": clean(row.get("kickoff_utc")),
                "home_team": clean(row.get("home_team")),
                "away_team": clean(row.get("away_team")),
                "team": team,
                "coach_id": clean(row.get("coach_id")),
                "coach": coach,
                "formation": clean(row.get("formation")),
                "previous_coach": previous,
                "coach_change_flag": changed,
                "new_manager_match_number": spell_match,
                "new_manager_band": band,
                "shadow_strength": strength,
                "shadow_only": 1,
                "source": "OFFICIAL_XI_COACH",
            })
            previous = coach or previous

    return output


def run_once():
    state = load_state()
    existing = read_csv_rows(OBSERVATIONS_FILE)
    new_rows, requests_made = collect_missing_observations(state, existing)
    if new_rows:
        append_csv(OBSERVATIONS_FILE, OBS_FIELDS, new_rows)
        existing.extend(new_rows)

    context = build_live_context(existing, historical_last_coach())
    write_csv_atomic(OUTPUT_FILE, OUTPUT_FIELDS, context)

    active = [r for r in context if int(r.get("new_manager_match_number") or 0) in (1, 2, 3)]
    print("Live Coach Context — SHADOW ONLY")
    print("Coach observations:", len(existing))
    print("New API requests this run:", requests_made)
    print("Context rows:", len(context))
    print("New-manager matches 1-3:", len(active))
    print("Output:", os.path.abspath(OUTPUT_FILE))
    return context


if __name__ == "__main__":
    run_once()
