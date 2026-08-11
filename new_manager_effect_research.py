import os
import pandas as pd
import numpy as np

INPUT_FILE = "epl_coach_context.csv"
EVENTS_OUTPUT = "new_manager_effect_events.csv"
SUMMARY_OUTPUT = "new_manager_effect_summary.csv"
SEASON_OUTPUT = "new_manager_effect_by_season.csv"
PRE_WINDOW = 5
POST_WINDOWS = (1, 3, 5)
METRICS = (
    "actual_xg_diff",
    "actual_goal_diff",
    "actual_ah_cover_score",
)
BOOTSTRAP_SAMPLES = 10000
PERMUTATION_SAMPLES = 10000
RANDOM_SEED = 20260811


def _mean(series):
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float(s.mean()) if len(s) else np.nan


def _bootstrap_ci(values, samples=BOOTSTRAP_SAMPLES, seed=RANDOM_SEED):
    arr = np.asarray(pd.to_numeric(pd.Series(values), errors="coerce").dropna(), dtype=float)
    if len(arr) == 0:
        return np.nan, np.nan
    if len(arr) == 1:
        return float(arr[0]), float(arr[0])
    rng = np.random.default_rng(seed)
    draws = rng.choice(arr, size=(samples, len(arr)), replace=True).mean(axis=1)
    low, high = np.quantile(draws, [0.025, 0.975])
    return float(low), float(high)


def _sign_flip_p(values, samples=PERMUTATION_SAMPLES, seed=RANDOM_SEED):
    arr = np.asarray(pd.to_numeric(pd.Series(values), errors="coerce").dropna(), dtype=float)
    if len(arr) == 0:
        return np.nan
    observed = abs(float(arr.mean()))
    if observed == 0:
        return 1.0
    rng = np.random.default_rng(seed)
    exceed = 0
    for _ in range(samples):
        signs = rng.choice((-1.0, 1.0), size=len(arr))
        if abs(float((arr * signs).mean())) >= observed - 1e-12:
            exceed += 1
    return float((exceed + 1) / (samples + 1))


def build_event_study(df, pre_window=PRE_WINDOW, post_windows=POST_WINDOWS):
    data = df.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data["fixture_id"] = pd.to_numeric(data["fixture_id"], errors="coerce")
    data = data.sort_values(["team", "date", "fixture_id"]).reset_index(drop=True)

    events = []
    change_rows = data.index[
        pd.to_numeric(data["coach_change_flag"], errors="coerce").fillna(0).astype(int) == 1
    ]

    for idx in change_rows:
        row = data.loc[idx]
        team = row["team"]
        team_rows = data[data["team"] == team].reset_index()
        pos_matches = team_rows.index[team_rows["index"] == idx]
        if len(pos_matches) != 1:
            continue
        pos = int(pos_matches[0])

        pre = team_rows.iloc[max(0, pos - pre_window):pos]
        if len(pre) < pre_window:
            continue

        for n in post_windows:
            post = team_rows.iloc[pos:pos + n]
            if len(post) < n:
                continue
            spell_id = row.get("coach_spell_id")
            if "coach_spell_id" in post.columns and post["coach_spell_id"].nunique(dropna=False) != 1:
                continue
            if "coach_spell_id" in post.columns and post.iloc[0]["coach_spell_id"] != spell_id:
                continue

            rec = {
                "team": team,
                "season": row.get("season"),
                "change_fixture_id": row.get("fixture_id"),
                "change_date": row.get("date"),
                "previous_coach": row.get("previous_coach"),
                "new_coach": row.get("coach"),
                "coach_spell_id": spell_id,
                "pre_matches": len(pre),
                "post_window": n,
            }
            for metric in METRICS:
                pre_avg = _mean(pre[metric])
                post_avg = _mean(post[metric])
                rec[f"pre_{metric}"] = pre_avg
                rec[f"post_{metric}"] = post_avg
                rec[f"delta_{metric}"] = (
                    post_avg - pre_avg if pd.notna(pre_avg) and pd.notna(post_avg) else np.nan
                )
            events.append(rec)

    return pd.DataFrame(events)


def summarize_events(events):
    rows = []
    for n, part in events.groupby("post_window", sort=True):
        rec = {"post_window": int(n), "events": int(len(part))}
        for metric in METRICS:
            delta_col = f"delta_{metric}"
            values = pd.to_numeric(part[delta_col], errors="coerce").dropna()
            low, high = _bootstrap_ci(values, seed=RANDOM_SEED + int(n))
            rec[f"mean_delta_{metric}"] = float(values.mean()) if len(values) else np.nan
            rec[f"median_delta_{metric}"] = float(values.median()) if len(values) else np.nan
            rec[f"positive_share_{metric}"] = float((values > 0).mean()) if len(values) else np.nan
            rec[f"ci95_low_{metric}"] = low
            rec[f"ci95_high_{metric}"] = high
            rec[f"signflip_p_{metric}"] = _sign_flip_p(values, seed=RANDOM_SEED + 100 + int(n))
        rows.append(rec)
    return pd.DataFrame(rows)


def summarize_by_season(events):
    rows = []
    if events.empty:
        return pd.DataFrame()
    for (season, n), part in events.groupby(["season", "post_window"], sort=True):
        rec = {"season": season, "post_window": int(n), "events": int(len(part))}
        for metric in METRICS:
            values = pd.to_numeric(part[f"delta_{metric}"], errors="coerce").dropna()
            rec[f"mean_delta_{metric}"] = float(values.mean()) if len(values) else np.nan
        rows.append(rec)
    return pd.DataFrame(rows)


def run(
    input_file=INPUT_FILE,
    events_output=EVENTS_OUTPUT,
    summary_output=SUMMARY_OUTPUT,
    season_output=SEASON_OUTPUT,
):
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"{input_file} not found. Run coach_context_builder.py first.")

    df = pd.read_csv(input_file, encoding="utf-8-sig")
    required = {
        "team", "season", "fixture_id", "date", "coach", "previous_coach",
        "coach_change_flag", "coach_spell_id", *METRICS,
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{input_file}: missing columns {sorted(missing)}")

    events = build_event_study(df)
    summary = summarize_events(events)
    by_season = summarize_by_season(events)
    events.to_csv(events_output, index=False, encoding="utf-8-sig")
    summary.to_csv(summary_output, index=False, encoding="utf-8-sig")
    by_season.to_csv(season_output, index=False, encoding="utf-8-sig")

    print("New Manager Effect Research")
    print(
        "Confirmed coach changes in source:",
        int(pd.to_numeric(df["coach_change_flag"], errors="coerce").fillna(0).sum()),
    )
    print("Eligible event-window rows:", len(events))
    for _, row in summary.iterrows():
        n = int(row["post_window"])
        print(
            f"Post {n}: events={int(row['events'])} "
            f"xGdiff_delta={row['mean_delta_actual_xg_diff']:+.3f} "
            f"CI=[{row['ci95_low_actual_xg_diff']:+.3f},{row['ci95_high_actual_xg_diff']:+.3f}] "
            f"p={row['signflip_p_actual_xg_diff']:.4f}"
        )
        print(
            f"        goal_diff_delta={row['mean_delta_actual_goal_diff']:+.3f} "
            f"CI=[{row['ci95_low_actual_goal_diff']:+.3f},{row['ci95_high_actual_goal_diff']:+.3f}] "
            f"p={row['signflip_p_actual_goal_diff']:.4f}"
        )
        print(
            f"        AH_cover_delta={row['mean_delta_actual_ah_cover_score']:+.3f} "
            f"CI=[{row['ci95_low_actual_ah_cover_score']:+.3f},{row['ci95_high_actual_ah_cover_score']:+.3f}] "
            f"p={row['signflip_p_actual_ah_cover_score']:.4f}"
        )
    print("Events output:", os.path.abspath(events_output))
    print("Summary output:", os.path.abspath(summary_output))
    print("Season output:", os.path.abspath(season_output))
    return events, summary, by_season


if __name__ == "__main__":
    run()
