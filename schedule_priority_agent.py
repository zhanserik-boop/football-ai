import csv
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv


load_dotenv()

API_KEY = (
    os.getenv("API_FOOTBALL_KEY")
    or os.getenv("API_KEY")
    or os.getenv("APISPORTS_KEY")
)

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY} if API_KEY else {}

EPL_LEAGUE_ID = 39
SEASON = 2026
LOOKAHEAD_HOURS = 48
TEAM_SCHEDULE_DAYS = 8
CACHE_HOURS = 6

CURRENT_SQUADS_FILE = "current_squads_2026.csv"
OUTPUT_FILE = "schedule_priority_live.csv"
CACHE_FILE = "schedule_priority_cache.json"

OUTPUT_FIELDS = [
    "built_utc",
    "fixture_id",
    "kickoff_utc",
    "home_team",
    "away_team",
    "team_id",
    "team_name",
    "current_competition",
    "current_round",
    "current_priority",
    "next_fixture_id",
    "next_kickoff_utc",
    "next_opponent",
    "next_competition",
    "next_round",
    "next_priority",
    "hours_to_next_match",
    "matches_next_7d_after_current",
    "schedule_pressure",
    "rotation_risk",
    "reason",
    "shadow_only",
]


def utc_now():
    return datetime.now(timezone.utc)


def parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def api_get(endpoint, params=None):
    if not API_KEY:
        raise RuntimeError("API-Football key not found in .env")
    response = requests.get(
        BASE_URL + endpoint,
        headers=HEADERS,
        params=params or {},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(f"API error {endpoint}: {data['errors']}")
    return data


def atomic_write_csv(filename, rows):
    temp = filename + ".tmp"
    with open(temp, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, filename)


def load_current_team_ids(filename=CURRENT_SQUADS_FILE):
    if not os.path.exists(filename):
        raise FileNotFoundError(filename)
    teams = {}
    with open(filename, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            team_id = str(row.get("team_id") or "").strip()
            team_name = str(row.get("team_name") or "").strip()
            if team_id and team_name:
                teams[int(team_id)] = team_name
    if len(teams) != 20:
        raise ValueError(f"Expected 20 current EPL teams, found {len(teams)}")
    return teams


def competition_priority(league_name, round_name=""):
    league = str(league_name or "").lower()
    round_text = str(round_name or "").lower()

    if "champions league" in league:
        score = 90
    elif "europa league" in league:
        score = 82
    elif "conference league" in league:
        score = 74
    elif "fa cup" in league:
        score = 76
    elif "league cup" in league or "efl cup" in league or "carabao" in league:
        score = 68
    elif "premier league" in league:
        score = 60
    elif "club world cup" in league:
        score = 84
    else:
        score = 50

    if "final" in round_text and "semi" not in round_text:
        score += 10
    elif "semi" in round_text:
        score += 7
    elif "quarter" in round_text:
        score += 5
    elif "round of 16" in round_text or "last 16" in round_text:
        score += 4
    elif "play-off" in round_text or "playoff" in round_text:
        score += 3

    return min(score, 100)


def classify_rotation_risk(current_priority, next_priority, hours_to_next, matches_next_7d):
    if hours_to_next is None:
        return "LOW", "LOW", "No confirmed next fixture in schedule window"

    if hours_to_next <= 72 and next_priority >= current_priority + 15:
        risk = "HIGH"
    elif hours_to_next <= 96 and next_priority >= current_priority + 20:
        risk = "HIGH"
    elif hours_to_next <= 96 and next_priority > current_priority:
        risk = "MEDIUM"
    elif hours_to_next <= 120 and next_priority >= 80:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    if hours_to_next <= 72 or matches_next_7d >= 3:
        pressure = "HIGH"
    elif hours_to_next <= 120 or matches_next_7d >= 2:
        pressure = "MEDIUM"
    else:
        pressure = "LOW"

    reason = (
        f"Next match in {hours_to_next:.1f}h; priority {current_priority}->{next_priority}; "
        f"{matches_next_7d} match(es) in next 7d after current fixture"
    )
    return pressure, risk, reason


def fixture_core(item):
    fixture = item.get("fixture") or {}
    league = item.get("league") or {}
    teams = item.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    return {
        "fixture_id": fixture.get("id"),
        "kickoff": parse_dt(fixture.get("date")),
        "league_id": league.get("id"),
        "competition": league.get("name") or "",
        "round": league.get("round") or "",
        "home_id": home.get("id"),
        "home_name": home.get("name") or "",
        "away_id": away.get("id"),
        "away_name": away.get("name") or "",
    }


def find_next_fixture(team_id, current_fixture, schedule_items):
    current_kickoff = current_fixture["kickoff"]
    future = []
    for item in schedule_items:
        row = fixture_core(item)
        if row["kickoff"] is None or row["kickoff"] <= current_kickoff:
            continue
        if team_id not in {row["home_id"], row["away_id"]}:
            continue
        future.append(row)
    future.sort(key=lambda x: x["kickoff"])
    return future[0] if future else None, future


def build_team_context(current_fixture, team_id, team_name, schedule_items):
    next_fixture, future = find_next_fixture(team_id, current_fixture, schedule_items)
    current_priority = competition_priority(
        current_fixture["competition"], current_fixture["round"]
    )

    if next_fixture:
        next_priority = competition_priority(next_fixture["competition"], next_fixture["round"])
        hours_to_next = (
            next_fixture["kickoff"] - current_fixture["kickoff"]
        ).total_seconds() / 3600.0
        opponent = (
            next_fixture["away_name"]
            if next_fixture["home_id"] == team_id
            else next_fixture["home_name"]
        )
    else:
        next_priority = 0
        hours_to_next = None
        opponent = ""

    seven_day_limit = current_fixture["kickoff"] + timedelta(days=7)
    matches_next_7d = sum(1 for x in future if x["kickoff"] <= seven_day_limit)
    pressure, risk, reason = classify_rotation_risk(
        current_priority, next_priority, hours_to_next, matches_next_7d
    )

    return {
        "team_id": team_id,
        "team_name": team_name,
        "current_priority": current_priority,
        "next_fixture_id": next_fixture["fixture_id"] if next_fixture else "",
        "next_kickoff_utc": next_fixture["kickoff"].isoformat() if next_fixture else "",
        "next_opponent": opponent,
        "next_competition": next_fixture["competition"] if next_fixture else "",
        "next_round": next_fixture["round"] if next_fixture else "",
        "next_priority": next_priority if next_fixture else "",
        "hours_to_next_match": round(hours_to_next, 2) if hours_to_next is not None else "",
        "matches_next_7d_after_current": matches_next_7d,
        "schedule_pressure": pressure,
        "rotation_risk": risk,
        "reason": reason,
    }


def build_rows(now=None):
    now = now or utc_now()
    teams = load_current_team_ids()
    end = now + timedelta(hours=LOOKAHEAD_HOURS)

    epl_data = api_get(
        "/fixtures",
        {
            "league": EPL_LEAGUE_ID,
            "season": SEASON,
            "from": now.date().isoformat(),
            "to": end.date().isoformat(),
            "timezone": "UTC",
        },
    )

    target_fixtures = []
    for item in epl_data.get("response", []):
        row = fixture_core(item)
        if row["kickoff"] and now <= row["kickoff"] <= end:
            target_fixtures.append(row)

    participating_ids = sorted(
        {
            team_id
            for fixture in target_fixtures
            for team_id in (fixture["home_id"], fixture["away_id"])
            if team_id in teams
        }
    )

    schedules = {}
    for team_id in participating_ids:
        data = api_get(
            "/fixtures",
            {
                "team": team_id,
                "from": now.date().isoformat(),
                "to": (now + timedelta(days=TEAM_SCHEDULE_DAYS)).date().isoformat(),
                "timezone": "UTC",
            },
        )
        schedules[team_id] = data.get("response", [])

    built = now.isoformat()
    output = []
    for fixture in sorted(target_fixtures, key=lambda x: x["kickoff"]):
        for team_id in (fixture["home_id"], fixture["away_id"]):
            if team_id not in teams:
                continue
            ctx = build_team_context(fixture, team_id, teams[team_id], schedules.get(team_id, []))
            output.append(
                {
                    "built_utc": built,
                    "fixture_id": fixture["fixture_id"],
                    "kickoff_utc": fixture["kickoff"].isoformat(),
                    "home_team": fixture["home_name"],
                    "away_team": fixture["away_name"],
                    "current_competition": fixture["competition"],
                    "current_round": fixture["round"],
                    **ctx,
                    "shadow_only": 1,
                }
            )

    return output, 1 + len(participating_ids)


def main():
    rows, calls = build_rows()
    atomic_write_csv(OUTPUT_FILE, rows)
    print("Schedule Priority Agent — SHADOW")
    print("Rows:", len(rows))
    print("API requests:", calls)
    print("Output:", Path(OUTPUT_FILE).resolve())
    if rows:
        for row in rows:
            print(
                f"{row['team_name']}: pressure={row['schedule_pressure']} "
                f"rotation={row['rotation_risk']} next={row['next_competition']} "
                f"in {row['hours_to_next_match']}h"
            )


if __name__ == "__main__":
    main()
