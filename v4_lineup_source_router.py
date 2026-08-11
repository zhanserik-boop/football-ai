"""Research-only UEFA lineup fallback and two-source audit for Football AI V4.

The module reads an existing V4 prediction document, discovers the matching
fixture in UEFA's public match feed, and compares official UEFA starters with
API-Football starters.  It never changes the V4 decision, never removes a data
quality veto, and never places a bet.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import v4_multileague_shadow as v4


UEFA_MATCH_BASE = "https://match.uefa.com/v5/matches"
UEFA_COMPETITION_BASE = "https://comp.uefa.com/v2/competitions"
DEFAULT_PREDICTIONS = "v4_multileague_predictions.json"
DEFAULT_JSON = "v4_lineup_source_audit.json"
DEFAULT_CSV = "v4_lineup_source_audit.csv"
DEFAULT_CACHE_DIR = "v4_uefa_cache"
ACTIVE_WINDOW_MINUTES = 90.0
MATCH_LIMIT = 100
MAX_MATCH_PAGES = 5


def translated(value, preferred=("EN", "en")):
    if not isinstance(value, dict):
        return v4.clean(value)
    for key in preferred:
        if v4.clean(value.get(key)):
            return v4.clean(value[key])
    for item in value.values():
        if v4.clean(item):
            return v4.clean(item)
    return ""


def uefa_team_name(team):
    team = team or {}
    translations = team.get("translations") or {}
    return (
        v4.clean(team.get("internationalName"))
        or translated(translations.get("displayName"))
        or translated(translations.get("displayOfficialName"))
    )


def uefa_player_name(player):
    player = player or {}
    return (
        v4.clean(player.get("internationalName"))
        or translated((player.get("translations") or {}).get("name"))
        or translated((player.get("translations") or {}).get("shortName"))
    )


def comparable_name(value):
    normalized = v4.normalize_name(value)
    ignored = {"pfc", "fcsb", "ssc", "acb"}
    return " ".join(token for token in normalized.split() if token not in ignored)


def team_similarity(left, right):
    a = comparable_name(left)
    b = comparable_name(right)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def payload_rows(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "matches", "response", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


class UefaPublicClient:
    def __init__(self, cache_dir=DEFAULT_CACHE_DIR, now_fn=v4.utc_now):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.now_fn = now_fn
        self.requests = 0
        self.errors = []

    def _cache_path(self, url, params):
        key = json.dumps([url, sorted(params.items())], separators=(",", ":"))
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def get(self, url, params=None, ttl_minutes=15, allow_stale=False):
        params = {str(k): str(v) for k, v in (params or {}).items()}
        cache_path = self._cache_path(url, params)
        cached = v4.read_json(cache_path, {})
        saved = v4.parse_dt(cached.get("saved_utc")) if cached else None
        age = (
            (self.now_fn() - saved).total_seconds() / 60.0
            if saved is not None else None
        )
        if cached and age is not None and age <= ttl_minutes:
            return cached.get("payload"), {
                "source": "CACHE", "age_minutes": round(age, 2),
            }

        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url + ("?" + query if query else ""),
            headers={
                "Accept": "application/json",
                "User-Agent": "Football-AI-V4-Shadow/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.requests += 1
            saved_utc = self.now_fn().isoformat()
            v4.atomic_json(cache_path, {"saved_utc": saved_utc, "payload": payload})
            return payload, {"source": "UEFA", "age_minutes": 0.0}
        except Exception as exc:
            error = f"{url}: {exc}"
            self.errors.append(error)
            if cached and allow_stale:
                return cached.get("payload"), {
                    "source": "STALE_CACHE", "age_minutes": age, "error": error,
                }
            return None, {"source": "ERROR", "error": error}

    def competition_ids(self):
        payload, _ = self.get(
            UEFA_COMPETITION_BASE, ttl_minutes=24 * 60, allow_stale=True
        )
        selected = []
        for item in payload_rows(payload):
            metadata = item.get("metaData") or {}
            translations = item.get("translations") or {}
            name = (
                v4.clean(metadata.get("name"))
                or translated(translations.get("name"))
            ).casefold()
            adult_male_club = (
                item.get("teamCategory") in {None, "CLUB"}
                and item.get("age") in {None, "ADULT"}
                and item.get("sex") in {None, "MALE"}
            )
            target = any(phrase in name for phrase in (
                "champions league", "europa league", "conference league",
            ))
            if adult_male_club and target and item.get("id") is not None:
                selected.append(str(item["id"]))
        return sorted(set(selected))

    def season_matches(self, season_year):
        matches = []
        competition_ids = self.competition_ids()
        scopes = competition_ids or [None]
        for competition_id in scopes:
            for page in range(MAX_MATCH_PAGES):
                offset = page * MATCH_LIMIT
                params = {
                    "seasonYear": season_year,
                    "limit": MATCH_LIMIT,
                    "offset": offset,
                    "order": "ASC",
                }
                if competition_id is not None:
                    params["competitionId"] = competition_id
                payload, meta = self.get(
                    UEFA_MATCH_BASE, params, ttl_minutes=30, allow_stale=True,
                )
                rows = payload_rows(payload)
                matches.extend(rows)
                if meta.get("source") == "ERROR" or len(rows) < MATCH_LIMIT:
                    break
        return matches

    def lineup(self, match_id):
        return self.get(
            f"{UEFA_MATCH_BASE}/{match_id}/lineups",
            ttl_minutes=3,
            allow_stale=False,
        )


def uefa_kickoff(item):
    kick = item.get("kickOffTime") or {}
    return v4.parse_dt(kick.get("dateTime"))


def match_uefa_fixture(result, candidates):
    fixture = result.get("fixture") or {}
    target = result.get("target") or {}
    home = fixture.get("home_team") or target.get("home_team", "")
    away = fixture.get("away_team") or target.get("away_team", "")
    kickoff = v4.parse_dt(fixture.get("kickoff_utc"))
    scored = []
    for item in candidates:
        candidate_kickoff = uefa_kickoff(item)
        if kickoff is None or candidate_kickoff is None:
            continue
        kickoff_delta = abs((candidate_kickoff - kickoff).total_seconds()) / 60.0
        if kickoff_delta > 180:
            continue
        home_score = team_similarity(home, uefa_team_name(item.get("homeTeam")))
        away_score = team_similarity(away, uefa_team_name(item.get("awayTeam")))
        if min(home_score, away_score) < 0.60:
            continue
        score = (home_score + away_score) / 2.0 - min(kickoff_delta, 180) / 1800.0
        scored.append((score, kickoff_delta, item))
    if not scored:
        return None, 0.0, None
    score, kickoff_delta, item = max(scored, key=lambda row: row[0])
    if score < 0.72:
        return None, round(score, 3), round(kickoff_delta, 1)
    return item, round(score, 3), round(kickoff_delta, 1)


def player_rows(team_lineup):
    output = []
    for row in (team_lineup or {}).get("field") or []:
        player = row.get("player") or {}
        output.append({
            "uefa_player_id": v4.clean(player.get("id")),
            "player_name": uefa_player_name(player),
            "jersey_number": row.get("jerseyNumber"),
            "late_update": bool(row.get("isLateUpdate")),
        })
    return output


def summarize_uefa_lineup(payload, attempted):
    payload = payload if isinstance(payload, dict) else {}
    home = player_rows(payload.get("homeTeam"))
    away = player_rows(payload.get("awayTeam"))
    provider_status = v4.clean(payload.get("lineupStatus")) or "NOT_AVAILABLE"
    confirmed = len(home) >= 11 and len(away) >= 11
    if confirmed:
        status = "CONFIRMED"
    elif not attempted:
        status = "NOT_QUERIED"
    elif home or away:
        status = "INCOMPLETE"
    else:
        status = "NOT_PUBLISHED"
    return {
        "status": status,
        "confirmed": confirmed,
        "provider_status": provider_status,
        "home_starters": home,
        "away_starters": away,
    }


def normalized_player_set(names):
    return {comparable_name(name) for name in names if comparable_name(name)}


def compare_sources(api_lineup, uefa_lineup):
    api_confirmed = api_lineup.get("status") == "CONFIRMED"
    uefa_confirmed = bool(uefa_lineup.get("confirmed"))
    if not api_confirmed and not uefa_confirmed:
        return "WAITING", None, None
    if api_confirmed and not uefa_confirmed:
        return "API_FOOTBALL_ONLY", None, None
    if uefa_confirmed and not api_confirmed:
        return "UEFA_ONLY_RESEARCH", None, None

    api_home = normalized_player_set(api_lineup.get("home_starter_names", []))
    api_away = normalized_player_set(api_lineup.get("away_starter_names", []))
    uefa_home = normalized_player_set(
        row["player_name"] for row in uefa_lineup.get("home_starters", [])
    )
    uefa_away = normalized_player_set(
        row["player_name"] for row in uefa_lineup.get("away_starters", [])
    )
    home_overlap = len(api_home & uefa_home)
    away_overlap = len(api_away & uefa_away)
    status = (
        "VERIFIED_TWO_SOURCES"
        if home_overlap >= 10 and away_overlap >= 10
        else "SOURCE_CONFLICT"
    )
    return status, home_overlap, away_overlap


def audit_result(result, matches, client, now=None, include_finished=False):
    now = now or v4.utc_now()
    fixture = result.get("fixture") or {}
    target = result.get("target") or {}
    api_lineup = (result.get("agents") or {}).get("lineup") or {}
    base = {
        "fixture_id": fixture.get("fixture_id", ""),
        "home_team": fixture.get("home_team") or target.get("home_team", ""),
        "away_team": fixture.get("away_team") or target.get("away_team", ""),
        "analysis_status": result.get("analysis_status", "FIXTURE_NOT_FOUND"),
        "api_football_status": api_lineup.get("status", "UNAVAILABLE"),
        "approved_for_value_gate": False,
    }
    if not fixture:
        return {**base, "status": "FIXTURE_NOT_FOUND", "reason": "API-Football fixture unresolved"}

    kickoff = v4.parse_dt(fixture.get("kickoff_utc"))
    minutes = (kickoff - now).total_seconds() / 60.0 if kickoff else None
    active = (
        include_finished
        or (
            result.get("analysis_status") == "ANALYZED_PREMATCH"
            and minutes is not None
            and -5 <= minutes <= ACTIVE_WINDOW_MINUTES
        )
    )
    matched, score, kickoff_delta = match_uefa_fixture(result, matches)
    if not matched:
        return {
            **base, "status": "UEFA_FIXTURE_NOT_FOUND", "uefa_match_score": score,
            "kickoff_delta_minutes": kickoff_delta,
            "reason": "no safe UEFA fixture match",
        }

    lineup_payload = None
    lineup_meta = {"source": "NOT_QUERIED"}
    if active:
        lineup_payload, lineup_meta = client.lineup(matched.get("id"))
    uefa_lineup = summarize_uefa_lineup(lineup_payload, attempted=active)
    source_status, home_overlap, away_overlap = compare_sources(api_lineup, uefa_lineup)
    reason_by_status = {
        "WAITING": "neither provider has a complete XI",
        "API_FOOTBALL_ONLY": "API-Football XI available; UEFA XI unavailable",
        "UEFA_ONLY_RESEARCH": "UEFA XI available first; research fallback only",
        "VERIFIED_TWO_SOURCES": "both providers agree on at least 10/11 starters per team",
        "SOURCE_CONFLICT": "complete provider lineups disagree; Value Gate must remain blocked",
    }
    return {
        **base,
        "status": source_status,
        "uefa_match_id": v4.clean(matched.get("id")),
        "uefa_match_score": score,
        "kickoff_delta_minutes": kickoff_delta,
        "minutes_to_kickoff": None if minutes is None else round(minutes, 1),
        "uefa_status": uefa_lineup["status"],
        "uefa_provider_status": uefa_lineup["provider_status"],
        "uefa_request_source": lineup_meta.get("source"),
        "home_name_overlap": home_overlap,
        "away_name_overlap": away_overlap,
        "uefa_home_starters": uefa_lineup["home_starters"],
        "uefa_away_starters": uefa_lineup["away_starters"],
        "reason": reason_by_status[source_status],
    }


CSV_FIELDS = [
    "generated_utc", "fixture_id", "home_team", "away_team", "analysis_status",
    "minutes_to_kickoff", "status", "api_football_status", "uefa_status",
    "uefa_match_id", "uefa_match_score", "kickoff_delta_minutes",
    "home_name_overlap", "away_name_overlap", "reason",
    "approved_for_value_gate",
]


def write_csv(path, rows):
    temporary = str(path) + ".tmp"
    with open(temporary, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def build_parser():
    parser = argparse.ArgumentParser(description="V4 UEFA lineup source router audit")
    parser.add_argument("--predictions", default=DEFAULT_PREDICTIONS)
    parser.add_argument("--json", default=DEFAULT_JSON)
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    parser.add_argument("--include-finished", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    predictions = v4.read_json(args.predictions, {})
    results = predictions.get("results", [])
    years = sorted({
        v4.parse_dt((row.get("fixture") or {}).get("kickoff_utc")).year
        for row in results
        if v4.parse_dt((row.get("fixture") or {}).get("kickoff_utc")) is not None
    })
    client = UefaPublicClient(cache_dir=args.cache_dir)
    matches = []
    for year in years:
        matches.extend(client.season_matches(year))
    generated = v4.utc_now().isoformat()
    rows = [
        audit_result(row, matches, client, include_finished=args.include_finished)
        for row in results
    ]
    document = {
        "schema_version": 1,
        "generated_utc": generated,
        "mode": "RESEARCH_ONLY",
        "approved_for_value_gate": False,
        "source": "UEFA public match feed",
        "uefa_requests_used": client.requests,
        "errors": client.errors,
        "results": rows,
    }
    v4.atomic_json(args.json, document)
    write_csv(
        args.csv,
        [{**row, "generated_utc": generated, "approved_for_value_gate": "NO"} for row in rows],
    )

    counts = Counter(row["status"] for row in rows)
    print("\n" + "=" * 80)
    print("FOOTBALL AI V4 — LINEUP SOURCE ROUTER")
    print("=" * 80)
    for row in rows:
        print(f"[{row['status']}] {row['home_team']} — {row['away_team']}")
    print("-" * 80)
    print("VERIFIED TWO SOURCES:", counts["VERIFIED_TWO_SOURCES"])
    print("UEFA ONLY RESEARCH:", counts["UEFA_ONLY_RESEARCH"])
    print("SOURCE CONFLICTS:", counts["SOURCE_CONFLICT"])
    print("UEFA REQUESTS USED:", client.requests)
    print("VALUE GATE APPROVED: NO")
    print("SHADOW ONLY: YES")
    print("=" * 80)
    print("CSV:", args.csv)
    print("JSON:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
