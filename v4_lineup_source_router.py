"""Research-only ESPN lineup fallback and two-source audit for Football AI V4.

The router reads an existing V4 prediction document, finds the same fixture in
ESPN's public soccer scoreboard and compares ESPN's published starters with
API-Football.  ESPN-only data can feed isolated lineup-shock research, but this
module never changes a V4 decision, removes a data-quality veto, or places a bet.
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
from difflib import SequenceMatcher
from pathlib import Path

import v4_multileague_shadow as v4


ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/all"
ESPN_SCOREBOARD = f"{ESPN_BASE}/scoreboard"
ESPN_SUMMARY = f"{ESPN_BASE}/summary"
DEFAULT_PREDICTIONS = "v4_multileague_predictions.json"
DEFAULT_JSON = "v4_lineup_source_audit.json"
DEFAULT_CSV = "v4_lineup_source_audit.csv"
DEFAULT_CACHE_DIR = "v4_espn_cache"
ACTIVE_WINDOW_MINUTES = 75.0
MATCH_LIMIT = 1000


def comparable_name(value):
    normalized = v4.normalize_name(value)
    aliases = {
        "agf": "aarhus",
        "bodo glimt": "bodo glimt",
        "hapoel beer": "hapoel beer sheva",
        "iberia 1999": "saburtalo",
        "olympiacos": "olympiakos piraeus",
        "sk brann": "brann",
        "union st gilloise": "union saint gilloise",
    }
    normalized = aliases.get(normalized, normalized)
    ignored = {"fc", "fk", "pfc", "sk", "cf", "nk", "aif"}
    return " ".join(token for token in normalized.split() if token not in ignored)


def team_similarity(left, right):
    a = comparable_name(left)
    b = comparable_name(right)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


class EspnPublicClient:
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
        params = {str(key): str(value) for key, value in (params or {}).items()}
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
            v4.atomic_json(
                cache_path,
                {"saved_utc": self.now_fn().isoformat(), "payload": payload},
            )
            return payload, {"source": "ESPN", "age_minutes": 0.0}
        except Exception as exc:
            error = f"{url}: {exc}"
            self.errors.append(error)
            if cached and allow_stale:
                return cached.get("payload"), {
                    "source": "STALE_CACHE", "age_minutes": age, "error": error,
                }
            return None, {"source": "ERROR", "error": error}

    def scheduled_events(self, date_value):
        payload, _ = self.get(
            ESPN_SCOREBOARD,
            {"dates": date_value, "limit": MATCH_LIMIT},
            ttl_minutes=10,
            allow_stale=True,
        )
        if not isinstance(payload, dict):
            return []
        return payload.get("events", []) if isinstance(payload.get("events"), list) else []

    def lineup_summary(self, event_id):
        return self.get(
            ESPN_SUMMARY,
            {"event": event_id},
            ttl_minutes=3,
            allow_stale=False,
        )


def event_competition(event):
    competitions = (event or {}).get("competitions") or []
    return competitions[0] if competitions else {}


def event_competitor(event, home_away):
    for row in event_competition(event).get("competitors") or []:
        if v4.clean(row.get("homeAway")).casefold() == home_away:
            return row
    return {}


def espn_team_name(competitor):
    team = (competitor or {}).get("team") or {}
    return (
        v4.clean(team.get("displayName"))
        or v4.clean(team.get("shortDisplayName"))
        or v4.clean(team.get("name"))
    )


def event_state(event):
    return v4.clean((((event or {}).get("status") or {}).get("type") or {}).get("state")).casefold()


def match_espn_fixture(result, candidates):
    fixture = result.get("fixture") or {}
    target = result.get("target") or {}
    home = fixture.get("home_team") or target.get("home_team", "")
    away = fixture.get("away_team") or target.get("away_team", "")
    kickoff = v4.parse_dt(fixture.get("kickoff_utc"))
    scored = []
    for event in candidates:
        candidate_kickoff = v4.parse_dt(event.get("date"))
        if kickoff is None or candidate_kickoff is None:
            continue
        kickoff_delta = abs((candidate_kickoff - kickoff).total_seconds()) / 60.0
        if kickoff_delta > 180:
            continue
        home_score = team_similarity(
            home, espn_team_name(event_competitor(event, "home"))
        )
        away_score = team_similarity(
            away, espn_team_name(event_competitor(event, "away"))
        )
        if min(home_score, away_score) < 0.60:
            continue
        score = (home_score + away_score) / 2.0 - min(kickoff_delta, 180) / 1800.0
        scored.append((score, kickoff_delta, event))
    if not scored:
        return None, 0.0, None
    score, kickoff_delta, event = max(scored, key=lambda row: row[0])
    if score < 0.72:
        return None, round(score, 3), round(kickoff_delta, 1)
    return event, round(score, 3), round(kickoff_delta, 1)


def player_row(row):
    athlete = (row or {}).get("athlete") or {}
    position = (row or {}).get("position") or athlete.get("position") or {}
    return {
        "source_player_id": v4.clean(athlete.get("id")),
        "player_name": (
            v4.clean(athlete.get("displayName"))
            or v4.clean(athlete.get("fullName"))
            or v4.clean(athlete.get("shortName"))
        ),
        "jersey_number": row.get("jersey"),
        "position": v4.clean(position.get("displayName")),
    }


def summary_state(payload):
    header = (payload or {}).get("header") or {}
    competitions = header.get("competitions") or []
    status = (competitions[0].get("status") or {}) if competitions else {}
    return v4.clean((status.get("type") or {}).get("state")).casefold()


def summarize_espn_lineup(payload, attempted):
    payload = payload if isinstance(payload, dict) else {}
    sides = {"home": [], "away": []}
    formations = {}
    for team_row in payload.get("rosters") or []:
        side = v4.clean(team_row.get("homeAway")).casefold()
        if side not in sides:
            continue
        starters = [player_row(row) for row in team_row.get("roster") or [] if row.get("starter") is True]
        sides[side] = [row for row in starters if row["player_name"]]
        formations[side] = v4.clean(team_row.get("formation"))

    complete = len(sides["home"]) == 11 and len(sides["away"]) == 11
    state = summary_state(payload)
    if complete and state in {"", "pre"}:
        status = "PUBLISHED_XI"
    elif not attempted:
        status = "NOT_QUERIED"
    elif sides["home"] or sides["away"]:
        status = "INCOMPLETE"
    else:
        status = "NOT_PUBLISHED"
    return {
        "status": status,
        "complete": status == "PUBLISHED_XI",
        "event_state": state or "UNKNOWN",
        "home_starters": sides["home"],
        "away_starters": sides["away"],
        "formations": formations,
    }


def normalized_player_set(names):
    return {comparable_name(name) for name in names if comparable_name(name)}


def compare_sources(api_lineup, espn_lineup):
    api_confirmed = api_lineup.get("status") == "CONFIRMED"
    espn_complete = bool(espn_lineup.get("complete"))
    if not api_confirmed and not espn_complete:
        return "WAITING", None, None
    if api_confirmed and not espn_complete:
        return "API_FOOTBALL_ONLY", None, None
    if espn_complete and not api_confirmed:
        return "ESPN_ONLY_RESEARCH", None, None

    api_home = normalized_player_set(api_lineup.get("home_starter_names", []))
    api_away = normalized_player_set(api_lineup.get("away_starter_names", []))
    espn_home = normalized_player_set(
        row["player_name"] for row in espn_lineup.get("home_starters", [])
    )
    espn_away = normalized_player_set(
        row["player_name"] for row in espn_lineup.get("away_starters", [])
    )
    home_overlap = len(api_home & espn_home)
    away_overlap = len(api_away & espn_away)
    status = (
        "VERIFIED_TWO_SOURCES"
        if home_overlap >= 10 and away_overlap >= 10
        else "SOURCE_CONFLICT"
    )
    return status, home_overlap, away_overlap


def audit_result(result, events, client, now=None, include_finished=False):
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
            and 0 < minutes <= ACTIVE_WINDOW_MINUTES
        )
    )
    matched, score, kickoff_delta = match_espn_fixture(result, events)
    if not matched:
        return {
            **base,
            "status": "ESPN_FIXTURE_NOT_FOUND",
            "espn_match_score": score,
            "kickoff_delta_minutes": kickoff_delta,
            "reason": "no safe ESPN fixture match",
        }

    if not include_finished and event_state(matched) != "pre":
        return {
            **base,
            "status": "NOT_PREMATCH",
            "espn_event_id": v4.clean(matched.get("id")),
            "minutes_to_kickoff": None if minutes is None else round(minutes, 1),
            "espn_event_state": event_state(matched) or "UNKNOWN",
            "reason": "ESPN fixture is no longer pre-match; lineup request skipped",
        }

    lineup_payload = None
    lineup_meta = {"source": "NOT_QUERIED"}
    if active:
        lineup_payload, lineup_meta = client.lineup_summary(matched.get("id"))
    espn_lineup = summarize_espn_lineup(lineup_payload, attempted=active)
    source_status, home_overlap, away_overlap = compare_sources(api_lineup, espn_lineup)
    reason_by_status = {
        "WAITING": "neither provider has a complete published XI",
        "API_FOOTBALL_ONLY": "API-Football XI available; ESPN XI unavailable",
        "ESPN_ONLY_RESEARCH": "ESPN published XI available; research fallback only",
        "VERIFIED_TWO_SOURCES": "both providers agree on at least 10/11 starters per team",
        "SOURCE_CONFLICT": "complete provider lineups disagree; Value Gate remains blocked",
    }
    return {
        **base,
        "status": source_status,
        "espn_event_id": v4.clean(matched.get("id")),
        "espn_match_score": score,
        "kickoff_delta_minutes": kickoff_delta,
        "minutes_to_kickoff": None if minutes is None else round(minutes, 1),
        "espn_status": espn_lineup["status"],
        "espn_event_state": espn_lineup["event_state"],
        "espn_request_source": lineup_meta.get("source"),
        "home_name_overlap": home_overlap,
        "away_name_overlap": away_overlap,
        "espn_home_starters": espn_lineup["home_starters"],
        "espn_away_starters": espn_lineup["away_starters"],
        "espn_formations": espn_lineup["formations"],
        "reason": reason_by_status[source_status],
    }


CSV_FIELDS = [
    "generated_utc", "fixture_id", "home_team", "away_team", "analysis_status",
    "minutes_to_kickoff", "status", "api_football_status", "espn_status",
    "espn_event_id", "espn_match_score", "kickoff_delta_minutes",
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


def schedule_dates(results):
    dates = set()
    for result in results:
        kickoff = v4.parse_dt((result.get("fixture") or {}).get("kickoff_utc"))
        if kickoff is None:
            continue
        dates.add(kickoff.strftime("%Y%m%d"))
    return sorted(dates)


def build_parser():
    parser = argparse.ArgumentParser(description="V4 ESPN lineup source router audit")
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
    client = EspnPublicClient(cache_dir=args.cache_dir)
    events = []
    for date_value in schedule_dates(results):
        events.extend(client.scheduled_events(date_value))
    generated = v4.utc_now().isoformat()
    rows = [
        audit_result(row, events, client, include_finished=args.include_finished)
        for row in results
    ]
    document = {
        "schema_version": 2,
        "generated_utc": generated,
        "mode": "RESEARCH_ONLY",
        "approved_for_value_gate": False,
        "source": "ESPN public soccer JSON feed",
        "source_documentation_status": "UNDOCUMENTED_NO_SLA",
        "espn_requests_used": client.requests,
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
    print("ESPN ONLY RESEARCH:", counts["ESPN_ONLY_RESEARCH"])
    print("SOURCE CONFLICTS:", counts["SOURCE_CONFLICT"])
    print("ESPN REQUESTS USED:", client.requests)
    print("VALUE GATE APPROVED: NO")
    print("SHADOW ONLY: YES")
    print("=" * 80)
    print("CSV:", args.csv)
    print("JSON:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
