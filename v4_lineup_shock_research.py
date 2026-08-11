"""Research-only V4 numerical lineup shock and Adjusted Fair AH proxy."""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter

import v4_multileague_shadow as v4


DEFAULT_PREDICTIONS = "v4_multileague_predictions.json"
DEFAULT_PLAYER_VALUES = "v4_player_values.json"
DEFAULT_LINEUP_AUDIT = "v4_lineup_source_audit.json"
DEFAULT_JSON = "v4_lineup_shock_research.json"
DEFAULT_CSV = "v4_lineup_shock_research.csv"
GOAL_PENALTY_SCALE = 1.5
MAX_AUDIT_AGE_MINUTES = 120.0


def profile_map(document):
    return {str(row["team_id"]): row for row in document.get("teams", [])}


def normalized_player_name(value):
    return v4.normalize_name(value)


def team_lineup_value(profile, starter_ids, starter_names):
    if not profile:
        return {"status": "BLOCKED", "reason": "player profile missing"}
    if profile.get("data_quality") != "HIGH":
        return {
            "status": "BLOCKED",
            "reason": f"player profile quality {profile.get('data_quality', 'UNKNOWN')}",
            "profile_quality": profile.get("data_quality", "UNKNOWN"),
        }
    if not profile.get("baseline_valid") or profile.get("baseline_score", 0) <= 0:
        return {"status": "BLOCKED", "reason": "baseline XI is invalid"}
    players = {str(row["player_id"]): row for row in profile.get("players", [])}
    players_by_name = {
        normalized_player_name(row.get("player_name")): row
        for row in profile.get("players", [])
        if normalized_player_name(row.get("player_name"))
    }
    known = []
    unknown = []
    for index in range(max(len(starter_ids), len(starter_names))):
        player_id = str(starter_ids[index]) if index < len(starter_ids) else ""
        player_name = starter_names[index] if index < len(starter_names) else ""
        row = players.get(player_id)
        if row is None:
            row = players_by_name.get(normalized_player_name(player_name))
        if row is None:
            unknown.append({"player_id": player_id, "player_name": player_name})
        elif all(str(existing["player_id"]) != str(row["player_id"]) for existing in known):
            known.append(row)
    known_with_stats = [row for row in known if row.get("minutes_weighted", 0) > 0]
    unknown.extend({
        "player_id": row.get("player_id", ""),
        "player_name": row.get("player_name", ""),
    } for row in known if row.get("minutes_weighted", 0) <= 0)
    coverage = len(known_with_stats) / 11.0
    baseline_ids = {str(player_id) for player_id in profile.get("baseline_player_ids", [])}
    resolved_ids = {str(row["player_id"]) for row in known}
    missing_ids = baseline_ids - resolved_ids
    missing = [players[key] for key in missing_ids if key in players]
    actual_score = sum(row.get("importance", 0.0) for row in known_with_stats)
    baseline_score = float(profile["baseline_score"])
    raw_loss = v4.clamp(1.0 - actual_score / baseline_score, 0.0, 0.50)
    penalty = raw_loss * GOAL_PENALTY_SCALE
    status = "READY_RESEARCH" if len(known_with_stats) >= 10 else "BLOCKED"
    reason = (
        "high-quality current-squad profile and at least 10/11 starters valued"
        if status == "READY_RESEARCH"
        else f"only {len(known_with_stats)}/11 starters have validated profile statistics"
    )
    return {
        "status": status,
        "reason": reason,
        "profile_quality": profile.get("data_quality"),
        "baseline_formation": profile.get("baseline_formation"),
        "baseline_score": round(baseline_score, 4),
        "actual_xi_score": round(actual_score, 4),
        "valued_starters": len(known_with_stats),
        "starter_coverage": round(coverage, 4),
        "raw_strength_loss": round(raw_loss, 4),
        "goal_penalty_proxy": round(penalty, 4),
        "missing_baseline_players": [
            {
                "player_id": row["player_id"],
                "player_name": row["player_name"],
                "importance": row["importance"],
            }
            for row in sorted(missing, key=lambda row: -row.get("importance", 0.0))
        ],
        "unknown_or_unrated_starters": unknown,
    }


def external_lineup_from_audit(audit_row):
    if not audit_row or audit_row.get("status") not in {
        "ESPN_ONLY_RESEARCH", "VERIFIED_TWO_SOURCES",
    }:
        return None
    home = audit_row.get("espn_home_starters") or []
    away = audit_row.get("espn_away_starters") or []
    if len(home) != 11 or len(away) != 11 or audit_row.get("espn_status") != "PUBLISHED_XI":
        return None
    return {
        "status": "CONFIRMED",
        "source": "ESPN_RESEARCH",
        "home_starter_ids": [row.get("source_player_id", "") for row in home],
        "away_starter_ids": [row.get("source_player_id", "") for row in away],
        "home_starter_names": [row.get("player_name", "") for row in home],
        "away_starter_names": [row.get("player_name", "") for row in away],
    }


def fresh_audit_map(document, now=None):
    now = now or v4.utc_now()
    generated = v4.parse_dt((document or {}).get("generated_utc"))
    if generated is None:
        return {}
    age = (now - generated).total_seconds() / 60.0
    if age < -5 or age > MAX_AUDIT_AGE_MINUTES:
        return {}
    return {
        str(row.get("fixture_id")): row
        for row in (document or {}).get("results", [])
        if row.get("fixture_id")
    }


def evaluate_result(result, profiles, audit_row=None):
    fixture = result.get("fixture") or {}
    agents = result.get("agents") or {}
    lineup = agents.get("lineup") or {}
    quant = agents.get("quant") or {}
    lineup_source = "API_FOOTBALL"
    if audit_row and audit_row.get("status") == "SOURCE_CONFLICT":
        return {
            "fixture_id": fixture.get("fixture_id", ""),
            "home_team": fixture.get("home_team") or (result.get("target") or {}).get("home_team", ""),
            "away_team": fixture.get("away_team") or (result.get("target") or {}).get("away_team", ""),
            "lineup_status": lineup.get("status", "UNAVAILABLE"),
            "lineup_source": "SOURCE_CONFLICT",
            "base_fair_home_ah": quant.get("fair_home_ah"),
            "approved_for_value_gate": False,
            "status": "BLOCKED",
            "reason": "API-Football and ESPN published XI disagree",
        }
    if lineup.get("status") != "CONFIRMED":
        external = external_lineup_from_audit(audit_row)
        if external:
            lineup = external
            lineup_source = external["source"]
    base = {
        "fixture_id": fixture.get("fixture_id", ""),
        "home_team": fixture.get("home_team") or (result.get("target") or {}).get("home_team", ""),
        "away_team": fixture.get("away_team") or (result.get("target") or {}).get("away_team", ""),
        "lineup_status": lineup.get("status", "UNAVAILABLE"),
        "lineup_source": lineup_source,
        "base_fair_home_ah": quant.get("fair_home_ah"),
        "approved_for_value_gate": False,
    }
    if lineup.get("status") != "CONFIRMED":
        return {**base, "status": "WAITING_FOR_CONFIRMED_XI", "reason": lineup.get("reason", "confirmed XI unavailable")}
    home = team_lineup_value(
        profiles.get(str(fixture.get("home_team_id"))),
        lineup.get("home_starter_ids", []), lineup.get("home_starter_names", []),
    )
    away = team_lineup_value(
        profiles.get(str(fixture.get("away_team_id"))),
        lineup.get("away_starter_ids", []), lineup.get("away_starter_names", []),
    )
    ready = home.get("status") == "READY_RESEARCH" and away.get("status") == "READY_RESEARCH"
    fair = v4.safe_float(quant.get("fair_home_ah"))
    if not ready or fair is None:
        return {
            **base, "status": "BLOCKED", "reason": "both HIGH profiles and valued XI coverage are required",
            "home_lineup": home, "away_lineup": away,
        }
    home_adjustment = away["goal_penalty_proxy"] - home["goal_penalty_proxy"]
    adjusted_fair = v4.round_quarter(fair - home_adjustment)
    return {
        **base,
        "status": "READY_RESEARCH",
        "reason": "numerical lineup proxy calculated; historical calibration still required",
        "home_lineup": home, "away_lineup": away,
        "home_goal_margin_adjustment_proxy": round(home_adjustment, 4),
        "adjusted_fair_home_ah_proxy": adjusted_fair,
    }


CSV_FIELDS = [
    "generated_utc", "fixture_id", "home_team", "away_team", "lineup_status",
    "lineup_source", "status", "base_fair_home_ah", "home_goal_margin_adjustment_proxy",
    "adjusted_fair_home_ah_proxy", "home_profile_quality", "away_profile_quality",
    "home_starter_coverage", "away_starter_coverage", "reason",
    "approved_for_value_gate",
]


def flatten(row, generated):
    home = row.get("home_lineup") or {}
    away = row.get("away_lineup") or {}
    return {
        "generated_utc": generated,
        "fixture_id": row.get("fixture_id", ""),
        "home_team": row.get("home_team", ""),
        "away_team": row.get("away_team", ""),
        "lineup_status": row.get("lineup_status", ""),
        "lineup_source": row.get("lineup_source", ""),
        "status": row.get("status", ""),
        "base_fair_home_ah": row.get("base_fair_home_ah", ""),
        "home_goal_margin_adjustment_proxy": row.get("home_goal_margin_adjustment_proxy", ""),
        "adjusted_fair_home_ah_proxy": row.get("adjusted_fair_home_ah_proxy", ""),
        "home_profile_quality": home.get("profile_quality", ""),
        "away_profile_quality": away.get("profile_quality", ""),
        "home_starter_coverage": home.get("starter_coverage", ""),
        "away_starter_coverage": away.get("starter_coverage", ""),
        "reason": row.get("reason", ""),
        "approved_for_value_gate": "NO",
    }


def write_csv(path, rows):
    temporary = str(path) + ".tmp"
    with open(temporary, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def build_parser():
    parser = argparse.ArgumentParser(description="V4 research-only numerical lineup shock")
    parser.add_argument("--predictions", default=DEFAULT_PREDICTIONS)
    parser.add_argument("--player-values", default=DEFAULT_PLAYER_VALUES)
    parser.add_argument("--lineup-audit", default=DEFAULT_LINEUP_AUDIT)
    parser.add_argument("--json", default=DEFAULT_JSON)
    parser.add_argument("--csv", default=DEFAULT_CSV)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    predictions = v4.read_json(args.predictions, {})
    values = v4.read_json(args.player_values, {})
    audit = v4.read_json(args.lineup_audit, {})
    audits = fresh_audit_map(audit)
    generated = v4.utc_now().isoformat()
    rows = [
        evaluate_result(
            result,
            profile_map(values),
            audits.get(str((result.get("fixture") or {}).get("fixture_id"))),
        )
        for result in predictions.get("results", [])
    ]
    document = {
        "schema_version": 1, "generated_utc": generated,
        "mode": "RESEARCH_ONLY", "approved_for_value_gate": False,
        "goal_penalty_scale": GOAL_PENALTY_SCALE,
        "lineup_audit": args.lineup_audit,
        "results": rows,
    }
    v4.atomic_json(args.json, document)
    write_csv(args.csv, [flatten(row, generated) for row in rows])
    counts = Counter(row["status"] for row in rows)
    print("\n" + "=" * 76)
    print("FOOTBALL AI V4 — NUMERICAL LINEUP SHOCK RESEARCH")
    print("=" * 76)
    print("MATCHES:", len(rows))
    print("READY RESEARCH:", counts["READY_RESEARCH"])
    print("WAITING FOR XI:", counts["WAITING_FOR_CONFIRMED_XI"])
    print("BLOCKED:", counts["BLOCKED"])
    print("VALUE GATE APPROVED: NO")
    print("API REQUESTS USED: 0")
    print("=" * 76)
    print("CSV:", args.csv)
    print("JSON:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
