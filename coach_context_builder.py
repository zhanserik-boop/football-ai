import os
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

from historical_context_builder import normalize_team_name


COACH_FILE = "epl_coach_history.csv"
CONTEXT_FILE = "epl_context_history.csv"
OUTPUT_FILE = "epl_coach_context.csv"
MIN_PROFILE_MATCHES = 3
NEW_MANAGER_WINDOW = 5


def _clean_text(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null"}:
        return ""
    return text


def load_coach_history(filename=COACH_FILE):
    if not os.path.exists(filename):
        raise FileNotFoundError(
            f"{filename} not found. Run historical_coach_builder.py first."
        )
    df = pd.read_csv(filename, encoding="utf-8-sig")
    required = {
        "season", "fixture_id", "date", "match_home", "match_away",
        "team", "coach", "formation", "coach_status"
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{filename}: missing columns {sorted(missing)}")

    df = df.copy()
    df["season"] = pd.to_numeric(df["season"], errors="coerce").astype("Int64")
    df["fixture_id"] = pd.to_numeric(df["fixture_id"], errors="coerce").astype("Int64")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    for col in ["match_home", "match_away", "team"]:
        df[col] = df[col].map(normalize_team_name)
    for col in ["coach", "formation", "coach_status"]:
        df[col] = df[col].map(_clean_text)
    return df


def load_context(filename=CONTEXT_FILE):
    if not os.path.exists(filename):
        raise FileNotFoundError(
            f"{filename} not found. Run historical_context_builder.py first."
        )
    df = pd.read_csv(filename, encoding="utf-8-sig")
    required = {
        "season", "date", "home_team", "away_team", "home_goals", "away_goals",
        "home_xg", "away_xg", "close_ah_home_line"
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{filename}: missing columns {sorted(missing)}")
    df = df.copy()
    df["season"] = pd.to_numeric(df["season"], errors="coerce").astype("Int64")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df["home_team"] = df["home_team"].map(normalize_team_name)
    df["away_team"] = df["away_team"].map(normalize_team_name)
    return df


def attach_match_metrics(coaches, context):
    merged = coaches.merge(
        context,
        left_on=["season", "date", "match_home", "match_away"],
        right_on=["season", "date", "home_team", "away_team"],
        how="left",
        validate="many_to_one",
    )

    is_home = merged["team"] == merged["home_team"]
    is_away = merged["team"] == merged["away_team"]
    merged["side"] = np.where(is_home, "HOME", np.where(is_away, "AWAY", "UNKNOWN"))

    merged["goals_for"] = np.where(is_home, merged["home_goals"], merged["away_goals"])
    merged["goals_against"] = np.where(is_home, merged["away_goals"], merged["home_goals"])
    merged["xg_for"] = np.where(is_home, merged["home_xg"], merged["away_xg"])
    merged["xg_against"] = np.where(is_home, merged["away_xg"], merged["home_xg"])
    merged["goal_diff"] = merged["goals_for"] - merged["goals_against"]
    merged["xg_diff"] = merged["xg_for"] - merged["xg_against"]
    merged["close_ah_team_line"] = np.where(
        is_home,
        merged["close_ah_home_line"],
        -pd.to_numeric(merged["close_ah_home_line"], errors="coerce"),
    )
    merged["ah_margin"] = merged["goal_diff"] + merged["close_ah_team_line"]
    merged["ah_cover_score"] = np.where(
        merged["ah_margin"].isna(),
        np.nan,
        np.where(merged["ah_margin"] > 1e-9, 1.0, np.where(merged["ah_margin"] < -1e-9, 0.0, 0.5)),
    )
    return merged


def _mean_or_nan(values):
    vals = [float(v) for v in values if pd.notna(v)]
    return float(np.mean(vals)) if vals else np.nan


def build_coach_context(data):
    data = data.sort_values(["date", "fixture_id", "team"]).copy()
    out = []

    team_previous_coach = {}
    coach_first_date = {}
    coach_hist = defaultdict(list)

    for _, row in data.iterrows():
        team = _clean_text(row.get("team"))
        coach = _clean_text(row.get("coach"))
        status = _clean_text(row.get("coach_status")).upper()
        date = row.get("date")
        key = (team, coach)

        previous_coach = team_previous_coach.get(team, "")
        coach_known = status == "OK" and bool(coach)
        change_flag = bool(coach_known and previous_coach and previous_coach != coach)

        history = coach_hist[key] if coach_known else []
        prior_matches = len(history)
        coach_match_number = prior_matches + 1 if coach_known else np.nan
        new_manager_flag = bool(coach_known and coach_match_number <= NEW_MANAGER_WINDOW)
        first_match_flag = bool(coach_known and coach_match_number == 1)

        first_date = coach_first_date.get(key)
        tenure_days = (date - first_date).days if coach_known and first_date is not None and pd.notna(date) else 0 if coach_known else np.nan

        formations = [_clean_text(h.get("formation")) for h in history]
        formations = [f for f in formations if f]
        dominant_formation = ""
        dominant_share = np.nan
        if formations:
            counts = Counter(formations)
            dominant_formation, dominant_count = counts.most_common(1)[0]
            dominant_share = dominant_count / len(formations)

        current_formation = _clean_text(row.get("formation"))
        formation_vs_coach_norm = (
            "CHANGE" if dominant_formation and current_formation and current_formation != dominant_formation
            else "SAME" if dominant_formation and current_formation == dominant_formation
            else "UNKNOWN"
        )

        record = {
            "season": row.get("season"),
            "fixture_id": row.get("fixture_id"),
            "date": row.get("date"),
            "match_home": row.get("match_home"),
            "match_away": row.get("match_away"),
            "team": team,
            "side": row.get("side"),
            "coach": coach,
            "coach_status": status,
            "formation": current_formation,
            "previous_coach": previous_coach,
            "coach_change_flag": int(change_flag),
            "new_manager_first_match": int(first_match_flag),
            "new_manager_window": int(new_manager_flag),
            "coach_match_number": coach_match_number,
            "coach_tenure_days": tenure_days,
            "coach_prior_matches": prior_matches,
            "coach_profile_ready": int(prior_matches >= MIN_PROFILE_MATCHES),
            "coach_prior_avg_xg_for": _mean_or_nan(h.get("xg_for") for h in history),
            "coach_prior_avg_xg_against": _mean_or_nan(h.get("xg_against") for h in history),
            "coach_prior_avg_xg_diff": _mean_or_nan(h.get("xg_diff") for h in history),
            "coach_prior_avg_goal_diff": _mean_or_nan(h.get("goal_diff") for h in history),
            "coach_prior_ah_cover_rate": _mean_or_nan(h.get("ah_cover_score") for h in history),
            "coach_dominant_formation": dominant_formation,
            "coach_dominant_formation_share": dominant_share,
            "formation_vs_coach_norm": formation_vs_coach_norm,
            "actual_xg_for": row.get("xg_for"),
            "actual_xg_against": row.get("xg_against"),
            "actual_xg_diff": row.get("xg_diff"),
            "actual_goal_diff": row.get("goal_diff"),
            "actual_ah_cover_score": row.get("ah_cover_score"),
            "close_ah_team_line": row.get("close_ah_team_line"),
        }
        out.append(record)

        if coach_known:
            if key not in coach_first_date and pd.notna(date):
                coach_first_date[key] = date
            coach_hist[key].append(row.to_dict())
            team_previous_coach[team] = coach

    return pd.DataFrame(out)


def build(coach_file=COACH_FILE, context_file=CONTEXT_FILE, output_file=OUTPUT_FILE):
    coaches = load_coach_history(coach_file)
    context = load_context(context_file)
    attached = attach_match_metrics(coaches, context)
    result = build_coach_context(attached)
    result.to_csv(output_file, index=False, encoding="utf-8-sig")

    print("Coach Context Database built")
    print("Team-fixtures:", len(result))
    print("Unique fixtures:", result["fixture_id"].nunique())
    print("Coach rows OK:", int((result["coach_status"] == "OK").sum()))
    print("Coach changes:", int(result["coach_change_flag"].sum()))
    print("New-manager first matches:", int(result["new_manager_first_match"].sum()))
    print("New-manager window rows:", int(result["new_manager_window"].sum()))
    print("Profiles ready (>=3 prior matches):", int(result["coach_profile_ready"].sum()))
    print("Output:", os.path.abspath(output_file))
    return result


if __name__ == "__main__":
    build()
