"""Build cached, team-relative player importance profiles for V4 research.

The builder uses paginated team-season requests, never one request per player.
Its output is research-only until forward validation approves the lineup model.
"""

from __future__ import annotations

import argparse
from collections import defaultdict

import v4_multileague_shadow as v4


DEFAULT_INPUT = "v4_multileague_predictions.json"
DEFAULT_OUTPUT = "v4_player_values.json"
DEFAULT_SEASONS = "2025,2026"
BASELINE_FORMATIONS = {
    "4-3-3": {"DEFENDER": 4, "MIDFIELDER": 3, "ATTACKER": 3},
    "4-4-2": {"DEFENDER": 4, "MIDFIELDER": 4, "ATTACKER": 2},
    "3-5-2": {"DEFENDER": 3, "MIDFIELDER": 5, "ATTACKER": 2},
    "3-4-3": {"DEFENDER": 3, "MIDFIELDER": 4, "ATTACKER": 3},
    "5-3-2": {"DEFENDER": 5, "MIDFIELDER": 3, "ATTACKER": 2},
    "5-4-1": {"DEFENDER": 5, "MIDFIELDER": 4, "ATTACKER": 1},
}


def parse_seasons(value):
    seasons = sorted({int(part.strip()) for part in str(value).split(",") if part.strip()})
    if not seasons:
        raise ValueError("At least one player-stat season is required")
    return seasons


def teams_from_predictions(document):
    teams = {}
    for result in document.get("results", []):
        fixture = result.get("fixture") or {}
        for side in ("home", "away"):
            team_id = fixture.get(f"{side}_team_id")
            name = v4.clean(fixture.get(f"{side}_team"))
            if team_id is not None and name:
                teams[str(team_id)] = {"team_id": int(team_id), "team_name": name}
    return [teams[key] for key in sorted(teams, key=lambda item: int(item))]


def normalize_position(value):
    position = v4.clean(value).upper()
    if position in {"FORWARD", "STRIKER"}:
        return "ATTACKER"
    return position or "UNKNOWN"


def aggregate_player_item(item, expected_team_id):
    player = item.get("player") or {}
    totals = {
        "player_id": player.get("id"), "player_name": v4.clean(player.get("name")),
        "age": player.get("age"), "position": "UNKNOWN", "minutes": 0.0,
        "appearances": 0.0, "starts": 0.0, "goals": 0.0, "assists": 0.0,
        "rating_weight": 0.0, "rating_minutes": 0.0,
    }
    for stats in item.get("statistics", []):
        team_id = (stats.get("team") or {}).get("id")
        if team_id != expected_team_id:
            continue
        games = stats.get("games") or {}
        goals = stats.get("goals") or {}
        minutes = v4.safe_float(games.get("minutes")) or 0.0
        appearances = v4.safe_float(games.get("appearences")) or 0.0
        starts = v4.safe_float(games.get("lineups")) or 0.0
        rating = v4.safe_float(games.get("rating"))
        position = normalize_position(games.get("position"))
        if position:
            totals["position"] = position
        totals["minutes"] += minutes
        totals["appearances"] += appearances
        totals["starts"] += starts
        totals["goals"] += v4.safe_float(goals.get("total")) or 0.0
        totals["assists"] += v4.safe_float(goals.get("assists")) or 0.0
        if rating is not None and minutes > 0:
            totals["rating_weight"] += rating * minutes
            totals["rating_minutes"] += minutes
    if totals["player_id"] is None or not totals["player_name"]:
        return None
    totals["rating"] = (
        totals["rating_weight"] / totals["rating_minutes"]
        if totals["rating_minutes"] > 0 else None
    )
    return totals


def merge_player_season(target, row, season_weight):
    target["player_id"] = row["player_id"]
    target["player_name"] = row["player_name"]
    target["age"] = row.get("age")
    if row.get("position") not in {"", "UNKNOWN", None}:
        target["position"] = row["position"]
    for key in ("minutes", "appearances", "starts", "goals", "assists"):
        target[key] += row[key] * season_weight
    if row.get("rating") is not None and row["minutes"] > 0:
        target["rating_weight"] += row["rating"] * row["minutes"] * season_weight
        target["rating_minutes"] += row["minutes"] * season_weight


def select_formation_baseline(players):
    by_position = defaultdict(list)
    for row in players:
        by_position[row["position"]].append(row)
    for rows in by_position.values():
        rows.sort(key=lambda row: (-row["importance"], row["player_name"]))
    if not by_position["GOALKEEPER"]:
        return [], "UNAVAILABLE"
    candidates = []
    for formation, required in BASELINE_FORMATIONS.items():
        if any(len(by_position[position]) < count for position, count in required.items()):
            continue
        selected = by_position["GOALKEEPER"][:1]
        for position, count in required.items():
            selected += by_position[position][:count]
        candidates.append((sum(row["importance"] for row in selected), formation, selected))
    if not candidates:
        return [], "UNAVAILABLE"
    _, formation, selected = max(candidates, key=lambda row: (row[0], row[1]))
    return selected, formation


def current_squad_rows(payload, expected_team_id):
    players = {}
    for item in payload.get("response", []):
        if (item.get("team") or {}).get("id") != expected_team_id:
            continue
        for player in item.get("players", []):
            player_id = player.get("id")
            if player_id is None:
                continue
            players[str(player_id)] = {
                "player_id": player_id,
                "player_name": v4.clean(player.get("name")),
                "age": player.get("age"),
                "position": normalize_position(player.get("position")),
            }
    return players


def finalize_team_profile(team, players, current_squad, seasons, api_errors):
    squad_available = bool(current_squad)
    if squad_available:
        for player_id, squad_row in current_squad.items():
            row = players[player_id]
            row["player_id"] = squad_row["player_id"]
            row["player_name"] = squad_row["player_name"] or row["player_name"]
            row["age"] = squad_row.get("age") or row.get("age")
            row["position"] = squad_row["position"]
        candidates = [players[player_id] for player_id in current_squad]
    else:
        candidates = list(players.values())
    active = [row for row in candidates if row["minutes"] > 0]
    max_minutes = max((row["minutes"] for row in active), default=0.0)
    max_starts = max((row["starts"] for row in active), default=0.0)
    output_players = []
    for row in candidates:
        rating = (
            row["rating_weight"] / row["rating_minutes"]
            if row["rating_minutes"] > 0 else None
        )
        minute_share = row["minutes"] / max_minutes if max_minutes else 0.0
        start_share = row["starts"] / max_starts if max_starts else 0.0
        rating_score = v4.clamp(((rating or 6.0) - 5.5) / 2.0)
        contribution_per90 = (
            90.0 * (row["goals"] + row["assists"]) / row["minutes"]
            if row["minutes"] else 0.0
        )
        production_score = v4.clamp(contribution_per90 / 0.8)
        importance = (
            0.55 * minute_share + 0.25 * start_share
            + 0.15 * rating_score + 0.05 * production_score
        )
        output_players.append({
            "player_id": row["player_id"], "player_name": row["player_name"],
            "age": row.get("age"), "position": row.get("position", "UNKNOWN"),
            "minutes_weighted": round(row["minutes"], 1),
            "starts_weighted": round(row["starts"], 1),
            "appearances_weighted": round(row["appearances"], 1),
            "rating": None if rating is None else round(rating, 3),
            "goal_contributions_weighted": round(row["goals"] + row["assists"], 2),
            "importance": round(importance, 4),
        })
    output_players.sort(key=lambda row: (-row["importance"], row["player_name"]))
    baseline, baseline_formation = select_formation_baseline(output_players)
    baseline_score = sum(row["importance"] for row in baseline)
    coverage = len(active) / len(current_squad) if current_squad else 0.0
    baseline_valid = len(baseline) == 11 and baseline_formation != "UNAVAILABLE"
    if (
        squad_available and baseline_valid and len(current_squad) >= 18
        and len(active) >= 15 and coverage >= 0.65 and max_minutes >= 900
    ):
        quality = "HIGH"
    elif (
        squad_available and baseline_valid and len(active) >= 11
        and coverage >= 0.50 and max_minutes >= 450
    ):
        quality = "MEDIUM"
    else:
        quality = "LOW"
    if api_errors:
        quality = "LOW"
    return {
        **team,
        "seasons": seasons,
        "data_quality": quality,
        "squad_filter_status": "CURRENT_SQUAD" if squad_available else "MISSING",
        "current_squad_size": len(current_squad),
        "current_squad_players_with_stats": len(active),
        "current_squad_coverage": round(coverage, 4),
        "players_with_minutes": len(active),
        "max_weighted_minutes": round(max_minutes, 1),
        "baseline_player_ids": [row["player_id"] for row in baseline],
        "baseline_formation": baseline_formation,
        "baseline_valid": baseline_valid,
        "baseline_score": round(baseline_score, 4),
        "players": output_players,
        "api_errors": api_errors,
    }


def fetch_team_profile(client, team, seasons):
    combined = defaultdict(lambda: {
        "player_id": None, "player_name": "", "age": None, "position": "UNKNOWN",
        "minutes": 0.0, "appearances": 0.0, "starts": 0.0,
        "goals": 0.0, "assists": 0.0, "rating_weight": 0.0,
        "rating_minutes": 0.0,
    })
    errors = []
    squad_payload, squad_meta = client.get(
        "/players/squads", {"team": team["team_id"]},
        ttl_minutes=1440, allow_stale=True,
    )
    if squad_meta.get("error"):
        errors.append(squad_meta["error"])
    current_squad = current_squad_rows(squad_payload, team["team_id"])
    if not current_squad:
        errors.append("current squad unavailable")
    newest = max(seasons)
    for season in seasons:
        season_weight = 1.0 if season == newest else 0.65 ** (newest - season)
        page = 1
        while True:
            payload, meta = client.get(
                "/players",
                {"team": team["team_id"], "season": season, "page": page},
                ttl_minutes=1440, allow_stale=True,
            )
            if meta.get("error"):
                errors.append(meta["error"])
            for item in payload.get("response", []):
                row = aggregate_player_item(item, team["team_id"])
                if row is not None:
                    merge_player_season(combined[str(row["player_id"])], row, season_weight)
            paging = payload.get("paging") or {}
            current = int(v4.safe_float(paging.get("current")) or page)
            total = int(v4.safe_float(paging.get("total")) or current)
            if current >= total or not payload.get("response"):
                break
            page += 1
            if page > 10:
                errors.append("players pagination safety limit exceeded")
                break
    return finalize_team_profile(team, combined, current_squad, seasons, errors)


def build_parser():
    parser = argparse.ArgumentParser(description="Build V4 research-only player values")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--seasons", default=DEFAULT_SEASONS)
    parser.add_argument("--cache-dir", default="v4_cache")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    seasons = parse_seasons(args.seasons)
    source = v4.read_json(args.input, {})
    teams = teams_from_predictions(source)
    if not teams:
        raise SystemExit("No fixture team IDs found; run v4_multileague_shadow.py first")
    client = v4.ApiFootballClient(v4.api_key_from_env(), cache_dir=args.cache_dir)
    profiles = [fetch_team_profile(client, team, seasons) for team in teams]
    document = {
        "schema_version": 1,
        "generated_utc": v4.utc_now().isoformat(),
        "mode": "RESEARCH_ONLY",
        "approved_for_value_gate": False,
        "method": "team-season paginated statistics; no per-player API calls",
        "seasons": seasons,
        "api_requests_used": client.api_requests,
        "api_requests_remaining": client.remaining,
        "api_errors": client.errors,
        "teams": profiles,
    }
    v4.atomic_json(args.output, document)
    counts = defaultdict(int)
    for profile in profiles:
        counts[profile["data_quality"]] += 1
    print("\n" + "=" * 72)
    print("FOOTBALL AI V4 — PLAYER VALUE BUILDER")
    print("=" * 72)
    print("TEAMS:", len(profiles))
    print("HIGH QUALITY:", counts["HIGH"])
    print("MEDIUM QUALITY:", counts["MEDIUM"])
    print("LOW QUALITY:", counts["LOW"])
    print("API REQUESTS USED:", client.api_requests)
    print("API REQUESTS REMAINING:", client.remaining or "UNKNOWN")
    print("VALUE GATE APPROVED: NO")
    print("MODE: RESEARCH ONLY")
    print("=" * 72)
    print("JSON:", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
