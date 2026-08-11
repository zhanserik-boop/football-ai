from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd


LINEUPS_FILE = "epl_lineups_4seasons.csv"
PLAYER_FILES = [
    "player_match_history_2022.csv",
    "player_match_history_2023.csv",
    "player_match_history_2024.csv",
    "player_match_history_2025.csv",
]
OUTPUT_FILE = "epl_historical_lineup_shock.csv"

WINDOW = 10
MIN_HISTORY = 3
ROSTER_RECENCY_MATCHES = 20
SHOCK_THRESHOLD = 1.5


def _num(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _date(series):
    return pd.to_datetime(series, utc=True, errors="coerce").dt.tz_convert(None)


def player_score(starts, minutes, ratings):
    n = len(starts)
    if n == 0:
        return 0.0, 0

    start_rate = sum(starts) / n
    minute_rate = min(1.0, max(0.0, sum(minutes) / (90.0 * n)))
    valid_ratings = [x for x in ratings if x is not None and np.isfinite(x)]
    if valid_ratings:
        average_rating = sum(valid_ratings) / len(valid_ratings)
        rating_score = min(1.0, max(0.0, (average_rating - 6.0) / 2.0))
    else:
        rating_score = 0.25

    score = 0.50 * start_rate + 0.35 * minute_rate + 0.15 * rating_score
    return float(score), n


def classify_signal(shock_diff, threshold=SHOCK_THRESHOLD):
    if shock_diff >= threshold:
        return "HOME"
    if shock_diff <= -threshold:
        return "AWAY"
    return "NO SIGNAL"


def load_inputs(lineups_file=LINEUPS_FILE, player_files=PLAYER_FILES):
    lineups = pd.read_csv(lineups_file, encoding="utf-8-sig", low_memory=False)
    parts = [
        pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
        for path in player_files
        if Path(path).exists()
    ]
    if not parts:
        raise FileNotFoundError("No player_match_history files found")
    return lineups, pd.concat(parts, ignore_index=True, sort=False)


def _prepare(lineups, player_stats):
    required = {
        "season", "fixture_id", "date", "match_home", "match_away",
        "team", "player_id", "starter",
    }
    missing = required - set(lineups.columns)
    if missing:
        raise ValueError(f"lineups missing columns: {sorted(missing)}")

    l = lineups.copy()
    l["fixture_id"] = pd.to_numeric(l["fixture_id"], errors="coerce").astype("Int64")
    l["season"] = pd.to_numeric(l["season"], errors="coerce").astype("Int64")
    l["date"] = _date(l["date"])
    l["player_id"] = l["player_id"].astype(str).str.strip()
    l["starter"] = pd.to_numeric(l["starter"], errors="coerce").fillna(0).astype(int)
    l = l[l["fixture_id"].notna() & l["date"].notna() & l["player_id"].ne("")]

    s = player_stats.copy()
    for col in ("fixture_id", "player_id", "team"):
        if col not in s.columns:
            raise ValueError(f"player stats missing column: {col}")
    s["fixture_id"] = pd.to_numeric(s["fixture_id"], errors="coerce").astype("Int64")
    s["player_id"] = s["player_id"].astype(str).str.strip()
    s["minutes"] = pd.to_numeric(s.get("minutes"), errors="coerce").fillna(0.0)
    s["rating"] = pd.to_numeric(s.get("rating"), errors="coerce")
    return l, s


def build_historical_lineup_shocks(lineups, player_stats):
    lineups, player_stats = _prepare(lineups, player_stats)

    fixtures = (
        lineups[
            ["season", "fixture_id", "date", "match_home", "match_away"]
        ]
        .drop_duplicates("fixture_id", keep="last")
        .sort_values(["date", "fixture_id"])
    )

    lineup_index = {}
    for (fixture_id, team), part in lineups.groupby(["fixture_id", "team"], sort=False):
        starters = set(part.loc[part["starter"] == 1, "player_id"])
        all_players = set(part["player_id"])
        lineup_index[(int(fixture_id), str(team))] = {
            "starters": starters,
            "all_players": all_players,
        }

    stats_index = {}
    for (fixture_id, team), part in player_stats.groupby(["fixture_id", "team"], sort=False):
        stats_index[(int(fixture_id), str(team))] = {
            str(row.player_id): {
                "minutes": _num(row.minutes),
                "rating": None if pd.isna(row.rating) else float(row.rating),
            }
            for row in part.itertuples()
        }

    starts = defaultdict(lambda: deque(maxlen=WINDOW))
    minutes = defaultdict(lambda: deque(maxlen=WINDOW))
    ratings = defaultdict(lambda: deque(maxlen=WINDOW))
    last_seen = {}
    team_games = defaultdict(int)
    rows = []

    def score(team, player_id):
        key = (team, player_id)
        return player_score(starts[key], minutes[key], ratings[key])

    def calculate_team(fixture_id, team):
        lineup = lineup_index.get((fixture_id, team), {"starters": set(), "all_players": set()})
        current_players = set(lineup["all_players"])
        current_players.update(stats_index.get((fixture_id, team), {}).keys())
        game_no = team_games[team]
        active = {
            player_id
            for (known_team, player_id), seen_game in last_seen.items()
            if known_team == team and game_no - seen_game <= ROSTER_RECENCY_MATCHES
        }
        active.update(current_players)

        candidate_scores = [score(team, player_id) for player_id in active]
        expected_scores = sorted((x[0] for x in candidate_scores), reverse=True)[:11]
        starter_scores = [score(team, player_id) for player_id in lineup["starters"]]
        known_starters = sum(history >= MIN_HISTORY for _, history in starter_scores)
        starter_count = len(lineup["starters"])
        coverage = known_starters / starter_count if starter_count else 0.0

        complete = starter_count == 11 and len(expected_scores) == 11
        actual_strength = sum(value for value, _ in starter_scores) if complete else np.nan
        expected_strength = sum(expected_scores) if complete else np.nan
        lineup_shock = actual_strength - expected_strength if complete else np.nan
        return {
            "starter_count": starter_count,
            "active_pool": len(active),
            "known_starters": known_starters,
            "coverage": coverage,
            "actual_strength": actual_strength,
            "expected_strength": expected_strength,
            "lineup_shock": lineup_shock,
            "current_players": current_players,
            "starters": lineup["starters"],
        }

    def update_team(fixture_id, team, calculated):
        game_no = team_games[team]
        for player_id in calculated["current_players"]:
            last_seen[(team, player_id)] = game_no

        active = {
            player_id
            for (known_team, player_id), seen_game in last_seen.items()
            if known_team == team and game_no - seen_game <= ROSTER_RECENCY_MATCHES
        }
        fixture_stats = stats_index.get((fixture_id, team), {})
        for player_id in active:
            stat = fixture_stats.get(player_id, {})
            starts[(team, player_id)].append(int(player_id in calculated["starters"]))
            minutes[(team, player_id)].append(_num(stat.get("minutes"), 0.0))
            rating = stat.get("rating")
            ratings[(team, player_id)].append(
                None if rating is None or not np.isfinite(rating) else float(rating)
            )
        team_games[team] += 1

    for match in fixtures.itertuples(index=False):
        fixture_id = int(match.fixture_id)
        home_team = str(match.match_home)
        away_team = str(match.match_away)
        home = calculate_team(fixture_id, home_team)
        away = calculate_team(fixture_id, away_team)

        complete = pd.notna(home["lineup_shock"]) and pd.notna(away["lineup_shock"])
        shock_diff = home["lineup_shock"] - away["lineup_shock"] if complete else np.nan
        minimum_coverage = min(home["coverage"], away["coverage"])
        quality = "HIGH" if minimum_coverage >= 0.80 else "MEDIUM" if minimum_coverage >= 0.60 else "LOW"
        signal = classify_signal(shock_diff) if pd.notna(shock_diff) else "NO SIGNAL"

        rows.append({
            "season": int(match.season),
            "fixture_id": fixture_id,
            "date": match.date,
            "home_team": home_team,
            "away_team": away_team,
            "home_actual_strength": home["actual_strength"],
            "home_expected_strength": home["expected_strength"],
            "home_lineup_shock": home["lineup_shock"],
            "away_actual_strength": away["actual_strength"],
            "away_expected_strength": away["expected_strength"],
            "away_lineup_shock": away["lineup_shock"],
            "shock_diff": shock_diff,
            "abs_shock": abs(shock_diff) if pd.notna(shock_diff) else np.nan,
            "signal": signal,
            "home_coverage": home["coverage"],
            "away_coverage": away["coverage"],
            "minimum_coverage": minimum_coverage,
            "data_quality": quality,
            "threshold": SHOCK_THRESHOLD,
            "prior_only": 1,
            "shadow_only": 1,
        })

        update_team(fixture_id, home_team, home)
        update_team(fixture_id, away_team, away)

    return pd.DataFrame(rows)


def build(output_file=OUTPUT_FILE):
    lineups, player_stats = load_inputs()
    out = build_historical_lineup_shocks(lineups, player_stats)
    out.to_csv(output_file, index=False, encoding="utf-8-sig")
    eligible = out[(out["data_quality"] != "LOW") & out["signal"].isin(["HOME", "AWAY"])]
    print("Historical Prior-Only Lineup Shock built")
    print("Fixtures:", len(out))
    print("Medium/high-quality signals:", len(eligible))
    print("Status: SHADOW ONLY")
    print("Output:", Path(output_file).resolve())
    return out


if __name__ == "__main__":
    build()
