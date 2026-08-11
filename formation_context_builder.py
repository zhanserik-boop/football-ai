from pathlib import Path
import os
import pandas as pd

LINEUPS_FILE = "epl_lineups_4seasons.csv"
OUTPUT_FILE = "epl_formation_context.csv"
WINDOW = 10
MIN_HISTORY = 5


def normalize_formation(value):
    if pd.isna(value):
        return ""
    text = str(value).strip().replace(" ", "")
    return text


def extract_team_fixtures(filename=LINEUPS_FILE):
    df = pd.read_csv(filename, encoding="utf-8-sig", low_memory=False)
    required = {"season", "fixture_id", "date", "team_id", "team", "formation"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{filename}: missing columns {sorted(missing)}")

    d = df[list(required)].copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d["formation"] = d["formation"].map(normalize_formation)
    d = d[d["formation"] != ""].copy()

    # Each team/fixture formation is repeated for every lineup player.
    # Collapse to one stable record without using any future information.
    grouped = (
        d.groupby(["season", "fixture_id", "date", "team_id", "team"], as_index=False)
        .agg(formation=("formation", lambda s: s.value_counts().index[0]))
    )
    return grouped.sort_values(["date", "fixture_id", "team_id"]).reset_index(drop=True)


def add_formation_history(team_fixtures, window=WINDOW, min_history=MIN_HISTORY):
    rows = []
    for _, part in team_fixtures.groupby("team_id", sort=False):
        history = []
        for _, row in part.sort_values(["date", "fixture_id"]).iterrows():
            recent = history[-window:]
            counts = pd.Series(recent, dtype="object").value_counts() if recent else pd.Series(dtype="int64")
            formation = row["formation"]
            prior_matches = len(recent)
            prior_count = int(counts.get(formation, 0)) if prior_matches else 0
            prior_share = prior_count / prior_matches if prior_matches else 0.0
            dominant = str(counts.index[0]) if len(counts) else ""
            dominant_share = float(counts.iloc[0] / prior_matches) if prior_matches else 0.0
            shock_score = 1.0 - prior_share if prior_matches >= min_history else 0.0
            shock_flag = int(prior_matches >= min_history and prior_share <= 0.20)

            item = row.to_dict()
            item.update(
                {
                    "formation_history_matches": prior_matches,
                    "formation_prior_count": prior_count,
                    "formation_prior_share": round(prior_share, 4),
                    "dominant_formation_prior": dominant,
                    "dominant_formation_share_prior": round(dominant_share, 4),
                    "formation_shock_score": round(shock_score, 4),
                    "formation_shock_flag": shock_flag,
                }
            )
            rows.append(item)
            history.append(formation)

    return pd.DataFrame(rows).sort_values(["date", "fixture_id", "team_id"]).reset_index(drop=True)


def build_formation_context(input_file=LINEUPS_FILE, output_file=OUTPUT_FILE):
    team_fixtures = extract_team_fixtures(input_file)
    context = add_formation_history(team_fixtures)
    context.to_csv(output_file, index=False, encoding="utf-8-sig")

    print("Formation Context Database built")
    print("Team-fixtures:", len(context))
    print("Unique fixtures:", context["fixture_id"].nunique())
    print("Formation coverage:", f"{100.0 * context['formation'].ne('').mean():.1f}%")
    print("Shock flags:", int(context["formation_shock_flag"].sum()))
    print("Output:", Path(output_file).resolve())
    return context


def main():
    build_formation_context()


if __name__ == "__main__":
    main()
