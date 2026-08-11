import os
from pathlib import Path

import numpy as np
import pandas as pd


ODDS_FILES = {
    2022: "epl_odds_2022.csv",
    2023: "epl_odds_2023.csv",
    2024: "epl_odds_2024.csv",
    2025: "epl_odds_2025.csv",
}
XG_FILE = "epl_xg_history.csv"
OUTPUT_FILE = "epl_context_history.csv"
COVERAGE_FILE = "epl_context_coverage.csv"
EXPECTED_MATCHES_PER_SEASON = 380

TEAM_ALIASES = {
    "Man City": "Manchester City",
    "Man United": "Manchester United",
    "Newcastle": "Newcastle United",
    "Nott'm Forest": "Nottingham Forest",
    "Nottm Forest": "Nottingham Forest",
    "Sheffield Utd": "Sheffield United",
    "Sheff Utd": "Sheffield United",
    "Tottenham Hotspur": "Tottenham",
    "West Brom": "West Bromwich Albion",
    "Wolves": "Wolverhampton Wanderers",
}

RAW_TO_NORMALIZED = {
    "HomeTeam": "home_team",
    "AwayTeam": "away_team",
    "FTHG": "home_goals",
    "FTAG": "away_goals",
    "FTR": "result_1x2",
    "HTHG": "home_goals_ht",
    "HTAG": "away_goals_ht",
    "HTR": "result_ht_1x2",
    "Referee": "referee",
    "HS": "home_shots",
    "AS": "away_shots",
    "HST": "home_sot",
    "AST": "away_sot",
    "HF": "home_fouls",
    "AF": "away_fouls",
    "HC": "home_corners",
    "AC": "away_corners",
    "HY": "home_yellow",
    "AY": "away_yellow",
    "HR": "home_red",
    "AR": "away_red",
    "AvgH": "open_home_odds",
    "AvgD": "open_draw_odds",
    "AvgA": "open_away_odds",
    "AvgCH": "close_home_odds",
    "AvgCD": "close_draw_odds",
    "AvgCA": "close_away_odds",
    "Avg>2.5": "open_over25_odds",
    "Avg<2.5": "open_under25_odds",
    "AvgC>2.5": "close_over25_odds",
    "AvgC<2.5": "close_under25_odds",
    "AHh": "open_ah_home_line",
    "AvgAHH": "open_ah_home_odds",
    "AvgAHA": "open_ah_away_odds",
    "AHCh": "close_ah_home_line",
    "AvgCAHH": "close_ah_home_odds",
    "AvgCAHA": "close_ah_away_odds",
}

NUMERIC_COLUMNS = [
    value
    for key, value in RAW_TO_NORMALIZED.items()
    if key not in {"HomeTeam", "AwayTeam", "FTR", "HTR", "Referee"}
]


def normalize_team_name(value):
    if pd.isna(value):
        return value
    text = str(value).strip()
    return TEAM_ALIASES.get(text, text)


def parse_football_data_date(series):
    # football-data.co.uk files use dd/mm/YYYY. dayfirst=True also handles
    # occasional two-digit years in older downloads without guessing US dates.
    return pd.to_datetime(series, dayfirst=True, errors="coerce").dt.normalize()


def load_odds_season(season, filename):
    if not os.path.exists(filename):
        raise FileNotFoundError(filename)

    raw = pd.read_csv(filename, encoding="utf-8-sig")
    if "Date" not in raw.columns or "HomeTeam" not in raw.columns or "AwayTeam" not in raw.columns:
        raise ValueError(f"{filename}: required Date/HomeTeam/AwayTeam columns missing")

    out = pd.DataFrame()
    out["season"] = int(season)
    out["date"] = parse_football_data_date(raw["Date"])

    for source, target in RAW_TO_NORMALIZED.items():
        if source in raw.columns:
            out[target] = raw[source]
        else:
            out[target] = np.nan

    out["home_team"] = out["home_team"].map(normalize_team_name)
    out["away_team"] = out["away_team"].map(normalize_team_name)

    for column in NUMERIC_COLUMNS:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")

    # Some football-data seasons do not expose a separate closing AH line.
    # In that case we preserve the known line rather than inventing one.
    if out["close_ah_home_line"].isna().all():
        out["close_ah_home_line"] = out["open_ah_home_line"]

    return out


def load_xg_history(filename=XG_FILE):
    if not os.path.exists(filename):
        raise FileNotFoundError(filename)

    xg = pd.read_csv(filename, encoding="utf-8-sig")
    required = {
        "season",
        "date",
        "home_team",
        "away_team",
        "home_xg",
        "away_xg",
    }
    missing = required - set(xg.columns)
    if missing:
        raise ValueError(f"{filename}: missing columns {sorted(missing)}")

    xg = xg.copy()
    xg["season"] = pd.to_numeric(xg["season"], errors="coerce").astype("Int64")
    xg["date"] = pd.to_datetime(xg["date"], errors="coerce").dt.normalize()
    xg["home_team"] = xg["home_team"].map(normalize_team_name)
    xg["away_team"] = xg["away_team"].map(normalize_team_name)
    xg["home_xg"] = pd.to_numeric(xg["home_xg"], errors="coerce")
    xg["away_xg"] = pd.to_numeric(xg["away_xg"], errors="coerce")

    keep = ["season", "date", "home_team", "away_team", "home_xg", "away_xg"]
    return xg[keep].drop_duplicates(
        subset=["season", "date", "home_team", "away_team"],
        keep="last",
    )


def add_rest_days(matches):
    result = matches.sort_values(["date", "season", "home_team", "away_team"]).copy()
    result["home_rest_days"] = np.nan
    result["away_rest_days"] = np.nan

    last_match = {}
    for idx, row in result.iterrows():
        date = row["date"]
        if pd.isna(date):
            continue

        for side in ("home", "away"):
            team = row[f"{side}_team"]
            previous = last_match.get(team)
            if previous is not None:
                result.at[idx, f"{side}_rest_days"] = float((date - previous).days)
            last_match[team] = date

    return result


def add_basic_derived_fields(matches):
    d = matches.copy()
    d["total_goals"] = d["home_goals"] + d["away_goals"]
    d["total_xg"] = d["home_xg"] + d["away_xg"]
    d["home_xg_diff"] = d["home_xg"] - d["away_xg"]
    d["home_shot_diff"] = d["home_shots"] - d["away_shots"]
    d["home_sot_diff"] = d["home_sot"] - d["away_sot"]
    d["total_yellow"] = d["home_yellow"] + d["away_yellow"]
    d["total_red"] = d["home_red"] + d["away_red"]
    d["total_fouls"] = d["home_fouls"] + d["away_fouls"]
    d["total_corners"] = d["home_corners"] + d["away_corners"]
    return d


def build_coverage_report(context):
    rows = []
    tracked = [
        "home_xg",
        "away_xg",
        "referee",
        "home_shots",
        "away_shots",
        "home_sot",
        "away_sot",
        "home_fouls",
        "away_fouls",
        "home_yellow",
        "away_yellow",
        "open_ah_home_line",
        "open_ah_home_odds",
        "open_ah_away_odds",
        "close_ah_home_line",
        "close_ah_home_odds",
        "close_ah_away_odds",
        "open_home_odds",
        "close_home_odds",
    ]

    for season, part in context.groupby("season", sort=True):
        for column in tracked:
            non_null = int(part[column].notna().sum()) if column in part.columns else 0
            total = int(len(part))
            rows.append(
                {
                    "season": int(season),
                    "column": column,
                    "non_null": non_null,
                    "total_matches": total,
                    "coverage_pct": round(100.0 * non_null / total, 2) if total else 0.0,
                }
            )
    return pd.DataFrame(rows)


def build_context_database(
    odds_files=ODDS_FILES,
    xg_file=XG_FILE,
    output_file=OUTPUT_FILE,
    coverage_file=COVERAGE_FILE,
):
    odds_parts = [load_odds_season(season, filename) for season, filename in odds_files.items()]
    odds = pd.concat(odds_parts, ignore_index=True)

    duplicate_key = ["season", "date", "home_team", "away_team"]
    if odds.duplicated(duplicate_key).any():
        duplicates = odds.loc[odds.duplicated(duplicate_key, keep=False), duplicate_key]
        raise ValueError(f"Duplicate historical fixtures detected:\n{duplicates.to_string(index=False)}")

    xg = load_xg_history(xg_file)
    context = odds.merge(
        xg,
        on=duplicate_key,
        how="left",
        validate="one_to_one",
    )

    context = add_rest_days(context)
    context = add_basic_derived_fields(context)
    context = context.sort_values(["season", "date", "home_team", "away_team"]).reset_index(drop=True)

    season_counts = context.groupby("season").size().to_dict()
    for season in odds_files:
        count = int(season_counts.get(season, 0))
        if count != EXPECTED_MATCHES_PER_SEASON:
            raise ValueError(
                f"Season {season}: expected {EXPECTED_MATCHES_PER_SEASON} matches, found {count}"
            )

    coverage = build_coverage_report(context)

    context.to_csv(output_file, index=False, encoding="utf-8-sig")
    coverage.to_csv(coverage_file, index=False, encoding="utf-8-sig")

    xg_coverage = float(context["home_xg"].notna().mean() * 100.0) if len(context) else 0.0
    print("Historical Context Database built")
    print("Matches:", len(context))
    print("Seasons:", ", ".join(str(x) for x in sorted(context["season"].unique())))
    print("xG coverage:", f"{xg_coverage:.1f}%")
    print("Output:", Path(output_file).resolve())
    print("Coverage report:", Path(coverage_file).resolve())

    return context, coverage


def main():
    build_context_database()


if __name__ == "__main__":
    main()
