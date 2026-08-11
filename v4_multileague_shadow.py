"""Football AI V4 Multi-League Shadow.

Independent, fail-closed research runner for cross-league European qualifiers.
It does not import or modify the frozen V3 runtime and never places real bets.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date as date_type, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path


BASE_URL = "https://v3.football.api-sports.io"
DEFAULT_INPUT = "v4_target_matches_20260811.csv"
DEFAULT_CACHE_DIR = "v4_cache"
DEFAULT_STATE = "v4_multileague_state.json"
DEFAULT_CSV = "v4_multileague_predictions.csv"
DEFAULT_JSON = "v4_multileague_predictions.json"
DEFAULT_MARKET_AUDIT_JSON = "v4_market_diagnostics.json"
AH_BET_ID = 4
MIN_FORM_MATCHES = 5
MIN_BOOKMAKERS = 2
LINEUP_QUERY_WINDOW_MINUTES = 90.0
SHADOW_ONLY = True
MARKET_CONSENSUS_VERSION = 3


def utc_now():
    return datetime.now(timezone.utc)


def clean(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def safe_float(value):
    try:
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    except Exception:
        return None


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def parse_dt(value):
    text = clean(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def round_quarter(value):
    return round(float(value) * 4.0) / 4.0


def load_dotenv_minimal(path=".env"):
    file_path = Path(path)
    if not file_path.exists():
        return
    for raw in file_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def api_key_from_env():
    load_dotenv_minimal()
    return (
        os.getenv("API_FOOTBALL_KEY")
        or os.getenv("API_KEY")
        or os.getenv("APISPORTS_KEY")
    )


def normalize_name(value):
    text = unicodedata.normalize("NFKD", clean(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("ø", "o").replace("ß", "ss")
    text = re.sub(r"\b(fc|fk|cf|afc|sk|nk|rc|ac)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


TEAM_ALIASES = {
    "kairat almaty": "kairat",
    "levski sofia": "levski",
    "union saint gilloise": "union st gilloise",
    "union sg": "union st gilloise",
    "sabah fa": "sabah",
    "agf aarhus": "aarhus",
    "kauno zalgiris": "zalgiris kaunas",
    "dinamo zagreb": "dinamo zagreb",
    "nec nijmegen": "nijmegen",
    "red star belgrade": "crvena zvezda",
    "hapoel beer sheva": "hapoel beer sheva",
    "slovan bratislava": "slovan bratislava",
    "mjallby": "mjallby",
    "mjallby aif": "mjallby",
    "celta vigo": "celta vigo",
    "ararat armenia": "ararat armenia",
    "sturm graz": "sturm graz",
    "fenerbahce": "fenerbahce",
    "iberia 1999": "iberia 1999",
    "saburtalo": "iberia 1999",
    "apollon limassol": "apollon limassol",
    "cska 1948 sofia": "cska 1948",
}


def canonical_team(value):
    normalized = normalize_name(value)
    return TEAM_ALIASES.get(normalized, normalized)


def name_similarity(left, right):
    a = canonical_team(left)
    b = canonical_team(right)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def atomic_json(path, value):
    target = Path(path)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, target)


def read_json(path, default=None):
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        return {} if default is None else default
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


class ApiFootballClient:
    def __init__(self, api_key, cache_dir=DEFAULT_CACHE_DIR, now_fn=utc_now):
        if not api_key:
            raise RuntimeError("API-Football key is missing from .env")
        self.api_key = api_key
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.now_fn = now_fn
        self.api_requests = 0
        self.remaining = None
        self.errors = []

    def _cache_path(self, endpoint, params):
        canonical = json.dumps(
            [endpoint, sorted((str(k), str(v)) for k, v in params.items())],
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def get(self, endpoint, params=None, ttl_minutes=30, allow_stale=False):
        params = params or {}
        cache_path = self._cache_path(endpoint, params)
        cached = read_json(cache_path, {})
        saved = parse_dt(cached.get("saved_utc")) if cached else None
        age = (
            (self.now_fn() - saved).total_seconds() / 60.0
            if saved is not None
            else None
        )
        if cached and age is not None and age <= ttl_minutes:
            return cached.get("payload", {}), {
                "source": "CACHE", "fetched_utc": cached.get("saved_utc"),
                "age_minutes": round(age, 2),
            }

        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            BASE_URL + endpoint + ("?" + query if query else ""),
            headers={"x-apisports-key": self.api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                self.api_requests += 1
                raw = response.read().decode("utf-8")
                payload = json.loads(raw)
                self.remaining = response.headers.get("x-ratelimit-requests-remaining")
            if payload.get("errors"):
                raise RuntimeError(str(payload["errors"]))
            saved_utc = self.now_fn().isoformat()
            atomic_json(cache_path, {"saved_utc": saved_utc, "payload": payload})
            return payload, {
                "source": "API", "fetched_utc": saved_utc, "age_minutes": 0.0,
            }
        except Exception as exc:
            message = f"{endpoint}: {exc}"
            self.errors.append(message)
            if cached and allow_stale:
                return cached.get("payload", {}), {
                    "source": "STALE_CACHE",
                    "fetched_utc": cached.get("saved_utc"),
                    "age_minutes": None if age is None else round(age, 2),
                    "error": message,
                }
            return {}, {"source": "ERROR", "error": message}


def load_targets(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    required = {"date_local", "kickoff_local", "competition", "home_team", "away_team"}
    missing = required - set(reader.fieldnames or [])
    if missing:
        raise RuntimeError("Target CSV missing columns: " + ", ".join(sorted(missing)))
    return rows


def fixture_candidate(item):
    fixture = item.get("fixture", {})
    league = item.get("league", {})
    teams = item.get("teams", {})
    return {
        "fixture_id": str(fixture.get("id") or ""),
        "kickoff_utc": clean(fixture.get("date")),
        "status": clean((fixture.get("status") or {}).get("short")),
        "venue": clean((fixture.get("venue") or {}).get("name")),
        "league_id": league.get("id"),
        "competition": clean(league.get("name")),
        "round": clean(league.get("round")),
        "home_team": clean((teams.get("home") or {}).get("name")),
        "home_team_id": (teams.get("home") or {}).get("id"),
        "away_team": clean((teams.get("away") or {}).get("name")),
        "away_team_id": (teams.get("away") or {}).get("id"),
    }


def match_target(target, fixtures):
    scored = []
    for item in fixtures:
        candidate = fixture_candidate(item)
        home_score = name_similarity(target["home_team"], candidate["home_team"])
        away_score = name_similarity(target["away_team"], candidate["away_team"])
        score = (home_score + away_score) / 2.0
        if min(home_score, away_score) >= 0.55:
            scored.append((score, candidate))
    if not scored:
        return None, 0.0
    score, candidate = max(scored, key=lambda value: value[0])
    return (candidate, score) if score >= 0.70 else (None, score)


def closest_fixture_candidates(target, fixtures, limit=3):
    scored = []
    for item in fixtures:
        candidate = fixture_candidate(item)
        home_score = name_similarity(target["home_team"], candidate["home_team"])
        away_score = name_similarity(target["away_team"], candidate["away_team"])
        scored.append(((home_score + away_score) / 2.0, candidate))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [
        {
            "score": round(score, 3),
            "fixture_id": candidate["fixture_id"],
            "kickoff_utc": candidate["kickoff_utc"],
            "home_team": candidate["home_team"],
            "away_team": candidate["away_team"],
            "competition": candidate["competition"],
        }
        for score, candidate in scored[:limit]
    ]


def completed_team_metrics(payload, team_id, now=None):
    now = now or utc_now()
    matches = []
    for item in payload.get("response", []):
        fixture = item.get("fixture", {})
        status = clean((fixture.get("status") or {}).get("short")).upper()
        if status not in {"FT", "AET", "PEN"}:
            continue
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        league_name = clean((item.get("league") or {}).get("name")).casefold()
        home_id = (teams.get("home") or {}).get("id")
        away_id = (teams.get("away") or {}).get("id")
        home_goals = safe_float(goals.get("home"))
        away_goals = safe_float(goals.get("away"))
        if home_goals is None or away_goals is None or team_id not in {home_id, away_id}:
            continue
        is_home = team_id == home_id
        gf = home_goals if is_home else away_goals
        ga = away_goals if is_home else home_goals
        played = parse_dt(fixture.get("date"))
        matches.append({
            "date": played, "gf": gf, "ga": ga,
            "friendly": "friendly" in league_name,
        })

    matches.sort(
        key=lambda row: row["date"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    matches = matches[:10]
    if not matches:
        return {
            "matches": 0, "ppg": 0.0, "gf": 0.0, "ga": 0.0, "gd": 0.0,
            "draw_rate": 0.0, "unbeaten_rate": 0.0, "low_scoring_rate": 0.0,
            "one_goal_loss_rate": 0.0, "rest_days": None,
        }

    weights = [
        (0.86 ** index) * (0.35 if match["friendly"] else 1.0)
        for index, match in enumerate(matches)
    ]
    total_weight = sum(weights)
    weighted = lambda values: sum(w * v for w, v in zip(weights, values)) / total_weight
    points = [3 if m["gf"] > m["ga"] else 1 if m["gf"] == m["ga"] else 0 for m in matches]
    rest_days = None
    if matches[0]["date"] is not None:
        rest_days = max(0.0, (now - matches[0]["date"]).total_seconds() / 86400.0)
    return {
        "matches": len(matches),
        "competitive_matches": sum(not match["friendly"] for match in matches),
        "ppg": round(weighted(points), 3),
        "gf": round(weighted([m["gf"] for m in matches]), 3),
        "ga": round(weighted([m["ga"] for m in matches]), 3),
        "gd": round(weighted([m["gf"] - m["ga"] for m in matches]), 3),
        "draw_rate": round(sum(m["gf"] == m["ga"] for m in matches) / len(matches), 3),
        "unbeaten_rate": round(sum(m["gf"] >= m["ga"] for m in matches) / len(matches), 3),
        "low_scoring_rate": round(sum(m["gf"] + m["ga"] <= 2 for m in matches) / len(matches), 3),
        "one_goal_loss_rate": round(sum(m["ga"] - m["gf"] == 1 for m in matches) / len(matches), 3),
        "rest_days": None if rest_days is None else round(rest_days, 2),
    }


def previous_meeting_context(payload, home_team_id, away_team_id, kickoff):
    candidates = []
    for item in payload.get("response", []):
        fixture = item.get("fixture", {})
        status = clean((fixture.get("status") or {}).get("short")).upper()
        teams = item.get("teams") or {}
        ids = {(teams.get("home") or {}).get("id"), (teams.get("away") or {}).get("id")}
        played = parse_dt(fixture.get("date"))
        if status not in {"FT", "AET", "PEN"} or ids != {home_team_id, away_team_id}:
            continue
        if played is None or kickoff is None or played >= kickoff:
            continue
        age_days = (kickoff - played).total_seconds() / 86400.0
        if age_days > 21:
            continue
        goals = item.get("goals") or {}
        previous_home_id = (teams.get("home") or {}).get("id")
        previous_home_goals = safe_float(goals.get("home"))
        previous_away_goals = safe_float(goals.get("away"))
        if previous_home_goals is None or previous_away_goals is None:
            continue
        if previous_home_id == home_team_id:
            current_home_goals = previous_home_goals
            current_away_goals = previous_away_goals
        else:
            current_home_goals = previous_away_goals
            current_away_goals = previous_home_goals
        candidates.append((played, current_home_goals, current_away_goals))
    if not candidates:
        return {"second_leg": False, "aggregate_margin_home": None, "reason": "first-leg result unavailable"}
    played, home_goals, away_goals = max(candidates, key=lambda row: row[0])
    margin = home_goals - away_goals
    state = "LEVEL" if margin == 0 else "HOME_AHEAD" if margin > 0 else "HOME_TRAILING"
    return {
        "second_leg": True, "first_leg_utc": played.isoformat(),
        "first_leg_home_perspective": f"{int(home_goals)}-{int(away_goals)}",
        "aggregate_margin_home": margin, "aggregate_state": state,
        "reason": f"second leg; current home perspective {int(home_goals)}-{int(away_goals)}",
    }


def parse_handicap_value(value):
    text = clean(value).replace("−", "-").replace("–", "-")
    lowered = text.casefold()
    side = "HOME" if "home" in lowered else "AWAY" if "away" in lowered else ""
    numbers = [safe_float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?", text)]
    numbers = [x for x in numbers if x is not None]
    if not side or not numbers:
        return None
    handicap = sum(numbers) / len(numbers)
    return side, round_quarter(handicap)


def extract_ah_rows(payload):
    rows = []
    provider_updates = []
    for fixture_data in payload.get("response", []):
        if fixture_data.get("update"):
            provider_updates.append(clean(fixture_data.get("update")))
        for bookmaker in fixture_data.get("bookmakers", []):
            book_id = bookmaker.get("id")
            book_name = clean(bookmaker.get("name"))
            for bet in bookmaker.get("bets", []):
                try:
                    bet_id = int(bet.get("id", -1))
                except Exception:
                    continue
                if bet_id != AH_BET_ID:
                    continue
                for value in bet.get("values", []):
                    raw_value = clean(value.get("value"))
                    parsed = parse_handicap_value(raw_value)
                    odd = safe_float(value.get("odd"))
                    if parsed is None or odd is None or odd <= 1.0:
                        continue
                    side, handicap = parsed
                    rows.append({
                        "bookmaker_id": str(book_id or book_name),
                        "bookmaker": book_name,
                        "side": side,
                        "handicap": handicap,
                        "odd": odd,
                        "raw_value": raw_value,
                        "bet_name": clean(bet.get("name")),
                    })
    return rows, max(provider_updates) if provider_updates else ""


def market_consensus(rows):
    by_book_line = defaultdict(dict)
    for row in rows:
        # API-Football's AH ladder pairs Home and Away prices under the same
        # numeric provider label (for example Home -0.75 / Away -0.75).
        # The label itself is the home-team handicap. Negating the Away label
        # cross-pairs unrelated alternatives and pushes selection to the edge
        # of the ladder.
        home_line = row["handicap"]
        by_book_line[(row["bookmaker_id"], home_line)][row["side"]] = row

    paired = []
    for (book_id, home_line), sides in by_book_line.items():
        if "HOME" not in sides or "AWAY" not in sides:
            continue
        paired.append({
            "bookmaker_id": book_id,
            "bookmaker": sides["HOME"]["bookmaker"],
            "home_line": home_line,
            "home_odd": sides["HOME"]["odd"],
            "away_odd": sides["AWAY"]["odd"],
        })
    if not paired:
        return None

    # API-Football can return a ladder of alternative handicaps for every
    # bookmaker. The market's main line is the paired price closest to balanced,
    # not the median of every offered alternative line.
    per_bookmaker = defaultdict(list)
    for row in paired:
        per_bookmaker[row["bookmaker_id"]].append(row)

    selected = []
    for book_rows in per_bookmaker.values():
        main = min(
            book_rows,
            key=lambda row: (
                abs(math.log(row["home_odd"] / row["away_odd"])),
                abs((1.0 / row["home_odd"] + 1.0 / row["away_odd"]) - 1.05),
                abs(row["home_line"]),
            ),
        )
        selected.append(main)

    counts = Counter(row["home_line"] for row in selected)
    max_count = max(counts.values())
    candidate_lines = [line for line, count in counts.items() if count == max_count]
    ordered = sorted(row["home_line"] for row in selected)
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else round_quarter((ordered[middle - 1] + ordered[middle]) / 2.0)
    )
    consensus_line = min(candidate_lines, key=lambda line: abs(line - median))
    same = [row for row in selected if row["home_line"] == consensus_line]
    home_avg = sum(row["home_odd"] for row in same) / len(same)
    away_avg = sum(row["away_odd"] for row in same) / len(same)
    home_imp = 1.0 / home_avg
    away_imp = 1.0 / away_avg
    line_counts = [
        {"home_line": line, "bookmakers": count}
        for line, count in sorted(counts.items())
    ]
    selected_lines = sorted(
        selected,
        key=lambda row: (row["bookmaker"].casefold(), row["bookmaker_id"]),
    )
    return {
        "home_handicap": consensus_line,
        "home_avg_odds": round(home_avg, 4),
        "away_avg_odds": round(away_avg, 4),
        "fair_home_cover_probability": round(home_imp / (home_imp + away_imp), 4),
        "bookmakers": len(same),
        "best_home_odds": round(max(row["home_odd"] for row in same), 4),
        "best_away_odds": round(max(row["away_odd"] for row in same), 4),
        "bookmakers_with_main_line": len(selected),
        "main_line_agreement": round(max_count / len(selected), 4),
        "main_line_spread": round(max(counts) - min(counts), 3),
        "line_vote_counts": line_counts,
        "selected_bookmaker_lines": selected_lines,
        "consensus_version": MARKET_CONSENSUS_VERSION,
    }


def odds_fingerprint(rows):
    canonical = sorted(
        (r["bookmaker_id"], r["side"], r["handicap"], round(r["odd"], 4))
        for r in rows
    )
    return hashlib.sha256(json.dumps(canonical).encode("utf-8")).hexdigest()


def lineup_summary(payload, home_team_id, away_team_id, query_attempted=False):
    provider_rows = payload.get("response", [])
    result = {
        "confirmed": False, "home_starters": 0, "away_starters": 0,
        "formations": {}, "home_starter_ids": [], "away_starter_ids": [],
        "home_starter_names": [], "away_starter_names": [],
        "query_attempted": bool(query_attempted),
        "provider_records": len(provider_rows),
    }
    for team_data in provider_rows:
        team = team_data.get("team", {})
        team_id = team.get("id")
        starters = team_data.get("startXI") or []
        if team_id == home_team_id:
            result["home_starters"] = len(starters)
            result["formations"]["home"] = clean(team_data.get("formation"))
            result["home_starter_ids"] = [
                (row.get("player") or {}).get("id") for row in starters
                if (row.get("player") or {}).get("id") is not None
            ]
            result["home_starter_names"] = [
                clean((row.get("player") or {}).get("name")) for row in starters
            ]
        elif team_id == away_team_id:
            result["away_starters"] = len(starters)
            result["formations"]["away"] = clean(team_data.get("formation"))
            result["away_starter_ids"] = [
                (row.get("player") or {}).get("id") for row in starters
                if (row.get("player") or {}).get("id") is not None
            ]
            result["away_starter_names"] = [
                clean((row.get("player") or {}).get("name")) for row in starters
            ]
    result["confirmed"] = result["home_starters"] >= 11 and result["away_starters"] >= 11
    return result


def injuries_by_fixture(payload):
    grouped = defaultdict(lambda: {"home": [], "away": []})
    for item in payload.get("response", []):
        fixture_id = str((item.get("fixture") or {}).get("id") or "")
        team = item.get("team") or {}
        player = item.get("player") or {}
        if not fixture_id:
            continue
        grouped[fixture_id][str(team.get("id"))].append(clean(player.get("name")))
    return grouped


def quant_agent(home, away):
    enough = home["matches"] >= MIN_FORM_MATCHES and away["matches"] >= MIN_FORM_MATCHES
    margin = 0.55 * (home["gd"] - away["gd"]) + 0.22 * (home["ppg"] - away["ppg"]) + 0.18
    margin = clamp(margin, -2.5, 2.5)
    fair_home_ah = -round_quarter(margin)
    if fair_home_ah <= -0.25:
        side = "HOME"
    elif fair_home_ah >= 0.25:
        side = "AWAY"
    else:
        side = "NEUTRAL"
    sample_factor = min(home["matches"], away["matches"]) / 10.0
    confidence = clamp(0.38 + abs(margin) * 0.12 + sample_factor * 0.15, 0.30, 0.72)
    if not enough:
        confidence = min(confidence, 0.44)
    return {
        "side": side, "fair_home_ah": fair_home_ah,
        "expected_goal_margin_home": round(margin, 3),
        "confidence": round(confidence, 3),
        "status": "OK" if enough else "LOW_SAMPLE",
        "reason": f"recent weighted margin {margin:+.2f}; cross-league cap applied",
    }


def market_agent(consensus, provider_update, state_row, fingerprint, fetched_utc, now):
    if consensus is None:
        return {
            "status": "MISSING", "side": "NEUTRAL", "reason": "Asian Handicap market unavailable"
        }
    state_version = int(safe_float(state_row.get("market_consensus_version")) or 0)
    opening = (
        state_row.get("opening_home_handicap")
        if state_version == MARKET_CONSENSUS_VERSION
        else None
    )
    if safe_float(opening) is None:
        opening = consensus["home_handicap"]
    current = consensus["home_handicap"]
    home_move = float(opening) - current
    previous_fingerprint = clean(state_row.get("last_odds_fingerprint"))
    changed = bool(previous_fingerprint and previous_fingerprint != fingerprint)
    update_dt = parse_dt(provider_update)
    provider_age = (now - update_dt).total_seconds() / 60.0 if update_dt else None
    if provider_age is not None and -5 <= provider_age <= 210:
        freshness = "FRESH"
    elif provider_age is None:
        freshness = "UNPROVEN"
    else:
        freshness = "STALE"
    side = "HOME" if home_move >= 0.25 else "AWAY" if home_move <= -0.25 else "NEUTRAL"
    return {
        "status": "OK", "side": side, "opening_home_handicap": opening,
        "current_home_handicap": current, "home_line_move": round(home_move, 3),
        "bookmakers": consensus["bookmakers"], "provider_update_utc": provider_update,
        "provider_age_minutes": None if provider_age is None else round(provider_age, 2),
        "freshness": freshness, "changed_since_last_run": changed,
        "fetched_utc": fetched_utc, "reason": f"AH {current:+.2f} across {consensus['bookmakers']} books",
        "consensus_version": MARKET_CONSENSUS_VERSION,
        **consensus,
    }


def lineup_agent(lineup, home_injuries, away_injuries):
    home_count = len([x for x in home_injuries if x])
    away_count = len([x for x in away_injuries if x])
    injury_delta = away_count - home_count
    side = "HOME" if injury_delta >= 2 else "AWAY" if injury_delta <= -2 else "NEUTRAL"
    if lineup.get("confirmed"):
        status = "CONFIRMED"
        reason = "confirmed XI received for both teams; cross-league player values are not yet validated"
    elif not lineup.get("query_attempted"):
        status = "NOT_QUERIED"
        reason = "outside the active lineup query attempt or no query has run yet"
    elif lineup.get("provider_records", 0) == 0:
        status = "NOT_PUBLISHED"
        reason = "API-Football has not published lineup records yet; retry is required"
    else:
        status = "INCOMPLETE"
        reason = (
            "API-Football returned an incomplete XI: "
            f"home {lineup.get('home_starters', 0)}/11, "
            f"away {lineup.get('away_starters', 0)}/11"
        )
    return {
        "status": status, "side": side, "home_injuries": home_count,
        "away_injuries": away_count, "home_starters": lineup.get("home_starters", 0),
        "away_starters": lineup.get("away_starters", 0),
        "availability_delta_home": injury_delta,
        "value_quality": "UNVALIDATED_CROSS_LEAGUE",
        "query_attempted": lineup.get("query_attempted", False),
        "provider_records": lineup.get("provider_records", 0),
        "home_starter_ids": lineup.get("home_starter_ids", []),
        "away_starter_ids": lineup.get("away_starter_ids", []),
        "home_starter_names": lineup.get("home_starter_names", []),
        "away_starter_names": lineup.get("away_starter_names", []),
        "formations": lineup.get("formations", {}),
        "reason": reason,
    }


def matchup_agent(home, away):
    home_pressure = math.sqrt(max(0.05, home["gf"]) * max(0.05, away["ga"]))
    away_pressure = math.sqrt(max(0.05, away["gf"]) * max(0.05, home["ga"]))
    edge = home_pressure - away_pressure
    side = "HOME" if edge >= 0.25 else "AWAY" if edge <= -0.25 else "NEUTRAL"
    return {
        "side": side, "edge": round(edge, 3),
        "confidence": round(clamp(0.40 + abs(edge) * 0.18, 0.35, 0.65), 3),
        "reason": f"attack-v-defence pressure edge {edge:+.2f}",
    }


def underdog_agent(home, away, market):
    if market.get("status") != "OK" or market.get("current_home_handicap") == 0:
        return {"underdog": "NONE", "resistance": 0.0, "level": "UNKNOWN", "reason": "no clear market favourite"}
    underdog = "AWAY" if market["current_home_handicap"] < 0 else "HOME"
    metrics = away if underdog == "AWAY" else home
    resistance = (
        0.35 * metrics["draw_rate"] + 0.35 * metrics["unbeaten_rate"]
        + 0.20 * metrics["low_scoring_rate"] + 0.10 * metrics["one_goal_loss_rate"]
    )
    level = "HIGH" if resistance >= 0.62 else "MEDIUM" if resistance >= 0.48 else "LOW"
    return {
        "underdog": underdog, "resistance": round(resistance, 3), "level": level,
        "reason": f"{underdog} weighted resistance {resistance:.2f}",
    }


def draw_pressure_agent(home, away):
    draw_rate = (home["draw_rate"] + away["draw_rate"]) / 2.0
    low_rate = (home["low_scoring_rate"] + away["low_scoring_rate"]) / 2.0
    pressure = 0.55 * draw_rate + 0.45 * low_rate
    level = "HIGH" if pressure >= 0.58 else "MEDIUM" if pressure >= 0.43 else "LOW"
    return {
        "pressure": round(pressure, 3), "level": level,
        "reason": f"draw {draw_rate:.2f}; low-scoring {low_rate:.2f}",
    }


def context_agent(home, away, fixture, previous_meeting=None):
    home_rest = home.get("rest_days")
    away_rest = away.get("rest_days")
    side = "NEUTRAL"
    rest_delta = None
    if home_rest is not None and away_rest is not None:
        rest_delta = home_rest - away_rest
        if rest_delta >= 3:
            side = "HOME"
        elif rest_delta <= -3:
            side = "AWAY"
    previous_meeting = previous_meeting or {
        "second_leg": False, "aggregate_margin_home": None,
        "reason": "first-leg result unavailable",
    }
    return {
        "side": side, "home_rest_days": home_rest, "away_rest_days": away_rest,
        "rest_delta_home": None if rest_delta is None else round(rest_delta, 2),
        "round": fixture.get("round", ""),
        "second_leg": previous_meeting.get("second_leg", False),
        "aggregate_margin_home": previous_meeting.get("aggregate_margin_home"),
        "aggregate_state": previous_meeting.get("aggregate_state", "UNKNOWN"),
        "reason": previous_meeting.get("reason", "") + "; qualification volatility; " + (
            "rest advantage " + side if side != "NEUTRAL" else "no material rest edge"
        ),
    }


def data_quality_agent(
    fixture, home, away, market, lineup, api_errors, post_lineup_market,
    pre_match=True,
):
    codes = []
    if not fixture:
        codes.append("FIXTURE_NOT_FOUND")
    elif not pre_match:
        codes.append("NOT_PREMATCH")
    if min(home.get("matches", 0), away.get("matches", 0)) < MIN_FORM_MATCHES:
        codes.append("FORM_LOW_SAMPLE")
    if market.get("status") != "OK":
        codes.append("AH_MISSING")
    elif market.get("bookmakers", 0) < MIN_BOOKMAKERS:
        codes.append("AH_THIN")
    if market.get("freshness") in {"STALE", "UNPROVEN"}:
        codes.append("MARKET_FRESHNESS_" + market.get("freshness", "UNKNOWN"))
    if lineup.get("status") != "CONFIRMED":
        codes.append("LINEUP_NOT_CONFIRMED")
    elif not post_lineup_market:
        codes.append("POST_LINEUP_MARKET_UNPROVEN")
    if lineup.get("value_quality") != "VALIDATED":
        codes.append("LINEUP_VALUE_UNVALIDATED")
    if api_errors:
        codes.append("API_ERRORS")

    critical = {
        "FIXTURE_NOT_FOUND", "NOT_PREMATCH", "AH_MISSING",
        "FORM_LOW_SAMPLE", "API_ERRORS",
    }
    if any(code in critical for code in codes):
        grade = "LOW"
    elif not codes:
        grade = "HIGH"
    else:
        grade = "MEDIUM"
    return {
        "grade": grade, "codes": codes,
        "reason": "all required evidence verified" if not codes else ", ".join(codes),
    }


def moderator_agent(quant, market, lineup, matchup, underdog, draw, context, quality, post_lineup_market):
    if quality["grade"] == "LOW":
        return {"decision": "PASS", "side": "", "confidence": 0.0, "reason": "Data Quality veto: " + quality["reason"]}
    if market.get("status") != "OK":
        return {"decision": "PASS", "side": "", "confidence": quant["confidence"], "reason": "No tradeable Asian Handicap market"}

    fair = quant["fair_home_ah"]
    current = market["current_home_handicap"]
    home_value = current - fair
    if home_value >= 0.25:
        side = "HOME"
        line_value = home_value
    elif home_value <= -0.25:
        side = "AWAY"
        line_value = -home_value
    else:
        return {
            "decision": "PASS", "side": "", "confidence": quant["confidence"],
            "line_value": round(abs(home_value), 3), "conflicts": [],
            "reason": f"No material fair-v-market AH edge ({home_value:+.2f} home)",
        }
    directional_move = market.get("home_line_move", 0.0)
    directional_move = directional_move if side == "HOME" else -directional_move
    conflicts = []
    if matchup["side"] not in {"NEUTRAL", side}:
        conflicts.append("MATCHUP")
    if context["side"] not in {"NEUTRAL", side}:
        conflicts.append("CONTEXT")
    if underdog["level"] == "HIGH" and underdog["underdog"] != side:
        conflicts.append("UNDERDOG_RESISTANCE")
    if draw["level"] == "HIGH" and ((side == "HOME" and current < 0) or (side == "AWAY" and current > 0)):
        conflicts.append("DRAW_PRESSURE")

    confidence = quant["confidence"]
    confidence += 0.04 if matchup["side"] == side else -0.04 if "MATCHUP" in conflicts else 0
    confidence += 0.03 if context["side"] == side else -0.03 if "CONTEXT" in conflicts else 0
    confidence += clamp(line_value, -0.5, 0.5) * 0.12
    confidence = round(clamp(confidence, 0.20, 0.82), 3)

    if lineup["status"] != "CONFIRMED":
        decision = "WATCH"
        reason = "Preliminary edge only; " + lineup.get("reason", "waiting for confirmed XI")
    elif not post_lineup_market:
        decision = "WATCH"
        reason = "Confirmed XI available; post-lineup market reaction not proven"
    elif directional_move >= 0.25:
        decision = "PASS"
        reason = "Market already moved at least 0.25 toward the signal; do not chase"
    elif line_value < 0.25:
        decision = "WATCH"
        reason = f"Fair-v-market AH value only {line_value:+.2f}"
    elif quality["grade"] != "HIGH":
        decision = "WATCH"
        reason = "Edge exists but Data Quality is not HIGH"
    elif len(conflicts) >= 2:
        decision = "WATCH"
        reason = "Independent-agent conflicts: " + ", ".join(conflicts)
    else:
        decision = "SHADOW BET"
        reason = f"Quant fair AH edge {line_value:+.2f}; verified post-lineup market"

    return {
        "decision": decision, "side": side, "confidence": confidence,
        "line_value": round(line_value, 3),
        "home_value": round(home_value, 3),
        "quant_strength_side": quant.get("side", "NEUTRAL"),
        "conflicts": conflicts, "reason": reason,
    }


def snapshot_state_update(state, fixture_id, market, fingerprint, lineup, now):
    row = state.setdefault("fixtures", {}).setdefault(fixture_id, {})
    if market.get("status") == "OK":
        if row.get("market_consensus_version") != MARKET_CONSENSUS_VERSION:
            row.pop("opening_home_handicap", None)
            row.pop("opening_seen_utc", None)
        row.setdefault("opening_home_handicap", market["current_home_handicap"])
        row.setdefault("opening_seen_utc", now.isoformat())
        row["market_consensus_version"] = MARKET_CONSENSUS_VERSION
        row["last_home_handicap"] = market["current_home_handicap"]
        row["last_odds_fingerprint"] = fingerprint
        row["last_odds_seen_utc"] = now.isoformat()
    if lineup.get("confirmed"):
        row.setdefault("lineup_first_seen_utc", now.isoformat())
    return row


def post_lineup_market_evidence(state_row, market, now):
    lineup_seen = parse_dt(state_row.get("lineup_first_seen_utc"))
    if lineup_seen is None or market.get("status") != "OK":
        return False
    provider_update = parse_dt(market.get("provider_update_utc"))
    if provider_update is not None and provider_update >= lineup_seen:
        return True
    if market.get("changed_since_last_run"):
        return True
    return False


def pre_match_status(fixture, now):
    kickoff = parse_dt((fixture or {}).get("kickoff_utc"))
    minutes = (kickoff - now).total_seconds() / 60.0 if kickoff else None
    status = clean((fixture or {}).get("status")).upper()
    eligible = minutes is not None and minutes > 0 and status in {"NS", "TBD"}
    return eligible, minutes


def collect_and_analyze(targets, client, timezone_name, state, now=None):
    now = now or utc_now()
    target_dates = sorted({row["date_local"] for row in targets})
    all_fixture_items = []
    fixture_errors = []
    for date in target_dates:
        payload, meta = client.get(
            "/fixtures", {"date": date, "timezone": timezone_name}, ttl_minutes=15,
            allow_stale=True,
        )
        all_fixture_items.extend(payload.get("response", []))
        if meta.get("error"):
            fixture_errors.append(meta["error"])

    injury_payloads = []
    injury_errors = []
    for date in target_dates:
        payload, injury_meta = client.get(
            "/injuries", {"date": date, "timezone": timezone_name}, ttl_minutes=30,
            allow_stale=True,
        )
        injury_payloads.extend(payload.get("response", []))
        if injury_meta.get("error"):
            injury_errors.append(injury_meta["error"])
    injury_map = injuries_by_fixture({"response": injury_payloads})

    def build_matches():
        result = []
        for target in targets:
            fixture, score = match_target(target, all_fixture_items)
            result.append((target, fixture, score))
        return result

    matched = build_matches()

    # Some providers assign late local kickoffs to the adjacent schedule date.
    # Query the neighbouring dates only when the primary date leaves targets
    # unmatched, keeping the normal request budget unchanged.
    if any(fixture is None for _, fixture, _ in matched):
        primary = {date_type.fromisoformat(value) for value in target_dates}
        adjacent = sorted(
            (
                {(day - timedelta(days=1)).isoformat() for day in primary}
                | {(day + timedelta(days=1)).isoformat() for day in primary}
            )
            - set(target_dates)
        )
        for adjacent_date in adjacent:
            payload, meta = client.get(
                "/fixtures",
                {"date": adjacent_date, "timezone": timezone_name},
                ttl_minutes=15,
                allow_stale=True,
            )
            all_fixture_items.extend(payload.get("response", []))
            if meta.get("error"):
                fixture_errors.append(meta["error"])
        matched = build_matches()

    team_ids = {}
    for _, fixture, _ in matched:
        if fixture and pre_match_status(fixture, now)[0]:
            team_ids[fixture["home_team_id"]] = fixture["home_team"]
            team_ids[fixture["away_team_id"]] = fixture["away_team"]

    team_metrics = {}
    team_payloads = {}
    team_errors = defaultdict(list)
    for team_id in team_ids:
        payload, team_meta = client.get(
            "/fixtures", {"team": team_id, "last": 10}, ttl_minutes=360,
            allow_stale=True,
        )
        team_payloads[team_id] = payload
        team_metrics[team_id] = completed_team_metrics(payload, team_id, now=now)
        if team_meta.get("error"):
            team_errors[team_id].append(team_meta["error"])

    output = []
    for target, fixture, match_score in matched:
        if not fixture:
            empty = completed_team_metrics({}, None, now=now)
            quality = data_quality_agent(
                None, empty, empty, {"status": "MISSING"},
                {"status": "WAITING"}, [], False,
            )
            output.append({
                "target": target, "fixture": None, "match_score": round(match_score, 3),
                "closest_candidates": closest_fixture_candidates(target, all_fixture_items),
                "agents": {"data_quality": quality},
                "moderator": {
                    "decision": "PASS", "side": "", "confidence": 0.0,
                    "reason": f"Fixture not found in API-Football; best name score {match_score:.2f}",
                },
                "shadow_only": True,
            })
            continue

        pre_match, minutes_to_kickoff = pre_match_status(fixture, now)
        kickoff = parse_dt(fixture["kickoff_utc"])
        fixture_id = fixture["fixture_id"]
        if not pre_match:
            quality = {
                "grade": "LOW", "codes": ["NOT_PREMATCH"],
                "reason": "NOT_PREMATCH",
            }
            output.append({
                "target": target,
                "fixture": {
                    **fixture,
                    "minutes_to_kickoff": (
                        None if minutes_to_kickoff is None
                        else round(minutes_to_kickoff, 1)
                    ),
                },
                "match_score": round(match_score, 3),
                "analysis_status": "EXCLUDED_NOT_PREMATCH",
                "agents": {"data_quality": quality},
                "moderator": {
                    "decision": "PASS", "side": "", "confidence": 0.0,
                    "reason": "Fixture is no longer pre-match; analysis skipped before per-match API calls",
                },
                "shadow_only": True,
            })
            continue

        prior_state = state.setdefault("fixtures", {}).setdefault(fixture_id, {})

        odds_ttl = 5 if minutes_to_kickoff is not None and minutes_to_kickoff <= 120 else 30
        odds_payload, odds_meta = client.get(
            "/odds", {"fixture": fixture_id, "bet": AH_BET_ID},
            ttl_minutes=odds_ttl, allow_stale=True,
        )
        ah_rows, provider_update = extract_ah_rows(odds_payload)
        consensus = market_consensus(ah_rows)
        fingerprint = odds_fingerprint(ah_rows) if ah_rows else ""
        market = market_agent(
            consensus, provider_update, prior_state, fingerprint,
            odds_meta.get("fetched_utc", ""), now,
        )
        market_audit = {
            "fixture_id": fixture_id,
            "home_team": fixture["home_team"],
            "away_team": fixture["away_team"],
            "provider_update_utc": provider_update,
            "raw_ah_rows": ah_rows,
            "selected_bookmaker_lines": (
                (consensus or {}).get("selected_bookmaker_lines", [])
            ),
            "line_vote_counts": (consensus or {}).get("line_vote_counts", []),
            "consensus_home_handicap": (
                (consensus or {}).get("home_handicap")
            ),
            "main_line_agreement": (
                (consensus or {}).get("main_line_agreement")
            ),
            "main_line_spread": (consensus or {}).get("main_line_spread"),
        }

        lineup_payload = {}
        lineup_meta = {}
        should_query_lineup = (
            minutes_to_kickoff is not None
            and -5 <= minutes_to_kickoff <= LINEUP_QUERY_WINDOW_MINUTES
        )
        if should_query_lineup:
            lineup_payload, lineup_meta = client.get(
                "/fixtures/lineups", {"fixture": fixture_id}, ttl_minutes=3,
                allow_stale=False,
            )
        lineup_raw = lineup_summary(
            lineup_payload, fixture["home_team_id"], fixture["away_team_id"],
            query_attempted=should_query_lineup,
        )
        injury_entry = injury_map.get(fixture_id, {})
        home_injuries = injury_entry.get(str(fixture["home_team_id"]), [])
        away_injuries = injury_entry.get(str(fixture["away_team_id"]), [])

        updated_state = snapshot_state_update(
            state, fixture_id, market, fingerprint, lineup_raw, now
        )
        post_lineup = post_lineup_market_evidence(updated_state, market, now)
        home = team_metrics.get(fixture["home_team_id"], completed_team_metrics({}, None, now))
        away = team_metrics.get(fixture["away_team_id"], completed_team_metrics({}, None, now))

        quant = quant_agent(home, away)
        lineup = lineup_agent(lineup_raw, home_injuries, away_injuries)
        matchup = matchup_agent(home, away)
        underdog = underdog_agent(home, away, market)
        draw = draw_pressure_agent(home, away)
        previous_meeting = previous_meeting_context(
            team_payloads.get(fixture["home_team_id"], {}),
            fixture["home_team_id"], fixture["away_team_id"], kickoff,
        )
        context = context_agent(home, away, fixture, previous_meeting)
        local_errors = list(fixture_errors) + list(injury_errors)
        local_errors += team_errors.get(fixture["home_team_id"], [])
        local_errors += team_errors.get(fixture["away_team_id"], [])
        if odds_meta.get("error"):
            local_errors.append(odds_meta["error"])
        if should_query_lineup and lineup_meta.get("error"):
            local_errors.append(lineup_meta["error"])
        quality = data_quality_agent(
            fixture, home, away, market, lineup, local_errors, post_lineup,
            pre_match=pre_match,
        )
        moderator = moderator_agent(
            quant, market, lineup, matchup, underdog, draw, context, quality, post_lineup
        )

        output.append({
            "target": target,
            "fixture": {**fixture, "minutes_to_kickoff": None if minutes_to_kickoff is None else round(minutes_to_kickoff, 1)},
            "match_score": round(match_score, 3),
            "team_metrics": {"home": home, "away": away},
            "agents": {
                "quant": quant, "market": market, "lineup": lineup,
                "matchup": matchup, "underdog_resistance": underdog,
                "draw_pressure": draw, "context": context, "data_quality": quality,
            },
            "post_lineup_market_evidence": post_lineup,
            "market_audit": market_audit,
            "analysis_status": "ANALYZED_PREMATCH",
            "moderator": moderator,
            "shadow_only": True,
        })
    return output


CSV_FIELDS = [
    "generated_utc", "fixture_id", "kickoff_utc", "competition", "round",
    "home_team", "away_team", "minutes_to_kickoff", "analysis_status", "data_quality",
    "quant_side", "quant_fair_home_ah", "quant_confidence",
    "market_home_ah", "market_bookmakers", "market_freshness",
    "lineup_status", "matchup_side", "underdog_resistance",
    "draw_pressure", "context_side", "decision", "bet_side",
    "confidence", "line_value", "data_quality_codes", "match_score",
    "market_provider_update_utc", "post_lineup_market_evidence",
    "reason", "shadow_only",
]


def flatten_result(result, generated_utc):
    fixture = result.get("fixture") or {}
    agents = result.get("agents") or {}
    quant = agents.get("quant") or {}
    market = agents.get("market") or {}
    moderator = result.get("moderator") or {}
    return {
        "generated_utc": generated_utc,
        "fixture_id": fixture.get("fixture_id", ""),
        "kickoff_utc": fixture.get("kickoff_utc", ""),
        "competition": fixture.get("competition") or (result.get("target") or {}).get("competition", ""),
        "round": fixture.get("round", ""),
        "home_team": fixture.get("home_team") or (result.get("target") or {}).get("home_team", ""),
        "away_team": fixture.get("away_team") or (result.get("target") or {}).get("away_team", ""),
        "minutes_to_kickoff": fixture.get("minutes_to_kickoff", ""),
        "analysis_status": result.get("analysis_status", "FIXTURE_NOT_FOUND"),
        "data_quality": (agents.get("data_quality") or {}).get("grade", "LOW"),
        "quant_side": quant.get("side", ""),
        "quant_fair_home_ah": quant.get("fair_home_ah", ""),
        "quant_confidence": quant.get("confidence", ""),
        "market_home_ah": market.get("current_home_handicap", ""),
        "market_bookmakers": market.get("bookmakers", ""),
        "market_freshness": market.get("freshness", ""),
        "lineup_status": (agents.get("lineup") or {}).get("status", ""),
        "matchup_side": (agents.get("matchup") or {}).get("side", ""),
        "underdog_resistance": (agents.get("underdog_resistance") or {}).get("level", ""),
        "draw_pressure": (agents.get("draw_pressure") or {}).get("level", ""),
        "context_side": (agents.get("context") or {}).get("side", ""),
        "decision": moderator.get("decision", "PASS"),
        "bet_side": moderator.get("side", ""),
        "confidence": moderator.get("confidence", ""),
        "line_value": moderator.get("line_value", ""),
        "data_quality_codes": ",".join(
            (agents.get("data_quality") or {}).get("codes", [])
        ),
        "match_score": result.get("match_score", ""),
        "market_provider_update_utc": market.get("provider_update_utc", ""),
        "post_lineup_market_evidence": result.get("post_lineup_market_evidence", False),
        "reason": moderator.get("reason", ""),
        "shadow_only": "YES",
    }


def write_csv(path, rows):
    temporary = str(path) + ".tmp"
    with open(temporary, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def build_market_audit_document(results, generated_utc):
    matches = []
    for result in results:
        audit = result.get("market_audit")
        if audit is not None:
            matches.append(audit)
    return {
        "schema_version": 1,
        "generated_utc": generated_utc,
        "purpose": "Raw API-Football Asian Handicap mapping audit",
        "contains_secrets": False,
        "matches": matches,
    }


def print_report(results, api_requests, remaining):
    print("\n" + "=" * 88)
    print("FOOTBALL AI V4 — MULTI-LEAGUE SHADOW PREDICTIONS")
    print("=" * 88)
    counts = Counter(result["moderator"]["decision"] for result in results)
    for result in results:
        fixture = result.get("fixture") or {}
        target = result.get("target") or {}
        home = fixture.get("home_team") or target.get("home_team", "HOME")
        away = fixture.get("away_team") or target.get("away_team", "AWAY")
        quality = (result.get("agents", {}).get("data_quality") or {}).get("grade", "LOW")
        moderator = result["moderator"]
        side = (" " + moderator.get("side", "")) if moderator.get("side") else ""
        print(f"[{moderator['decision']}{side}] {home} — {away} | DQ {quality}")
        print("  " + moderator["reason"])
    print("-" * 88)
    print("MATCHES:", len(results))
    print("SHADOW BET:", counts.get("SHADOW BET", 0))
    print("WATCH:", counts.get("WATCH", 0))
    print("PASS:", counts.get("PASS", 0))
    print("API REQUESTS USED THIS RUN:", api_requests)
    print("API REQUESTS REMAINING:", remaining if remaining is not None else "UNKNOWN")
    print("AUTOMATIC REAL BETTING: NO")
    print("SHADOW ONLY: YES")
    print("=" * 88)


def build_parser():
    parser = argparse.ArgumentParser(description="Football AI V4 multi-league shadow runner")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--timezone", default="Asia/Almaty")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--json", default=DEFAULT_JSON)
    parser.add_argument("--market-audit-json", default=DEFAULT_MARKET_AUDIT_JSON)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not re.fullmatch(r"[A-Za-z_+-]+(?:/[A-Za-z0-9_+-]+)+", args.timezone):
        raise SystemExit(f"Invalid API timezone: {args.timezone!r}")
    targets = load_targets(args.input)
    state = read_json(args.state, {"schema_version": 1, "fixtures": {}})
    state.setdefault("schema_version", 1)
    state.setdefault("fixtures", {})
    client = ApiFootballClient(api_key_from_env(), cache_dir=args.cache_dir)
    results = collect_and_analyze(targets, client, args.timezone, state)
    generated = utc_now().isoformat()
    document = {
        "schema_version": 1, "generated_utc": generated,
        "mode": "SHADOW_ONLY", "automatic_real_betting_enabled": False,
        "api_requests_used": client.api_requests, "api_requests_remaining": client.remaining,
        "api_errors": client.errors, "results": results,
    }
    atomic_json(args.json, document)
    atomic_json(
        args.market_audit_json,
        build_market_audit_document(results, generated),
    )
    write_csv(args.csv, [flatten_result(row, generated) for row in results])
    atomic_json(args.state, state)
    print_report(results, client.api_requests, client.remaining)
    print("CSV:", args.csv)
    print("JSON:", args.json)
    print("MARKET AUDIT JSON:", args.market_audit_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
