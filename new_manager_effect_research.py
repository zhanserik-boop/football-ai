import os
import pandas as pd
import numpy as np

INPUT_FILE = "epl_coach_context.csv"
EVENTS_OUTPUT = "new_manager_effect_events.csv"
SUMMARY_OUTPUT = "new_manager_effect_summary.csv"
SEASON_OUTPUT = "new_manager_effect_by_season.csv"
MATCHED_OUTPUT = "new_manager_effect_matched_controls.csv"
MATCHED_SUMMARY_OUTPUT = "new_manager_effect_matched_summary.csv"
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


def _prepare(df):
    data = df.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data["fixture_id"] = pd.to_numeric(data["fixture_id"], errors="coerce")
    data["coach_change_flag"] = pd.to_numeric(
        data["coach_change_flag"], errors="coerce"
    ).fillna(0).astype(int)
    return data.sort_values(["team", "date", "fixture_id"]).reset_index(drop=True)


def _window_record(team_rows, pos, post_window, pre_window=PRE_WINDOW):
    if pos < pre_window:
        return None
    pre = team_rows.iloc[pos - pre_window:pos]
    post = team_rows.iloc[pos:pos + post_window]
    if len(pre) < pre_window or len(post) < post_window:
        return None

    anchor_spell = team_rows.iloc[pos].get("coach_spell_id")
    if "coach_spell_id" in team_rows.columns:
        if pre["coach_spell_id"].nunique(dropna=False) != 1:
            return None
        if post["coach_spell_id"].nunique(dropna=False) != 1:
            return None
        if post.iloc[0].get("coach_spell_id") != anchor_spell:
            return None

    rec = {}
    for metric in METRICS:
        pre_avg = _mean(pre[metric])
        post_avg = _mean(post[metric])
        rec[f"pre_{metric}"] = pre_avg
        rec[f"post_{metric}"] = post_avg
        rec[f"delta_{metric}"] = (
            post_avg - pre_avg if pd.notna(pre_avg) and pd.notna(post_avg) else np.nan
        )
    return rec


def build_event_study(df, pre_window=PRE_WINDOW, post_windows=POST_WINDOWS):
    data = _prepare(df)
    events = []

    for team, team_rows in data.groupby("team", sort=False):
        team_rows = team_rows.reset_index(drop=True)
        change_positions = team_rows.index[team_rows["coach_change_flag"] == 1]
        for pos in change_positions:
            row = team_rows.loc[pos]
            for n in post_windows:
                metrics = _window_record(team_rows, int(pos), int(n), pre_window=pre_window)
                if metrics is None:
                    continue
                rec = {
                    "team": team,
                    "season": row.get("season"),
                    "change_fixture_id": row.get("fixture_id"),
                    "change_date": row.get("date"),
                    "previous_coach": row.get("previous_coach"),
                    "new_coach": row.get("coach"),
                    "coach_spell_id": row.get("coach_spell_id"),
                    "pre_matches": pre_window,
                    "post_window": int(n),
                }
                rec.update(metrics)
                events.append(rec)

    return pd.DataFrame(events)


def build_placebo_candidates(df, pre_window=PRE_WINDOW, post_windows=POST_WINDOWS):
    data = _prepare(df)
    rows = []
    for team, team_rows in data.groupby("team", sort=False):
        team_rows = team_rows.reset_index(drop=True)
        for pos in range(len(team_rows)):
            anchor = team_rows.iloc[pos]
            if int(anchor.get("coach_change_flag", 0)) != 0:
                continue
            for n in post_windows:
                metrics = _window_record(team_rows, pos, int(n), pre_window=pre_window)
                if metrics is None:
                    continue
                # A placebo must be a stable-coach window, not a hidden manager transition.
                pre = team_rows.iloc[pos - pre_window:pos]
                post = team_rows.iloc[pos:pos + int(n)]
                if int(pre["coach_change_flag"].sum()) != 0 or int(post["coach_change_flag"].sum()) != 0:
                    continue
                rec = {
                    "team": team,
                    "season": anchor.get("season"),
                    "control_fixture_id": anchor.get("fixture_id"),
                    "control_date": anchor.get("date"),
                    "control_coach": anchor.get("coach"),
                    "control_spell_id": anchor.get("coach_spell_id"),
                    "post_window": int(n),
                }
                rec.update(metrics)
                rows.append(rec)
    return pd.DataFrame(rows)


def _distance_scales(candidates):
    scales = {}
    for metric in METRICS:
        col = f"pre_{metric}"
        vals = pd.to_numeric(candidates[col], errors="coerce").dropna()
        scale = float(vals.std(ddof=0)) if len(vals) else np.nan
        scales[metric] = scale if pd.notna(scale) and scale > 1e-9 else 1.0
    return scales


def match_placebo_controls(events, candidates):
    if events.empty or candidates.empty:
        return pd.DataFrame()
    matched = []
    scales_by_window = {
        int(n): _distance_scales(part)
        for n, part in candidates.groupby("post_window", sort=False)
    }

    for _, event in events.iterrows():
        n = int(event["post_window"])
        pool = candidates[candidates["post_window"] == n].copy()
        same_team_season = pool[
            (pool["team"] == event["team"]) & (pool["season"] == event["season"])
        ]
        if not same_team_season.empty:
            pool = same_team_season
            scope = "SAME_TEAM_SEASON"
        else:
            same_team = pool[pool["team"] == event["team"]]
            if same_team.empty:
                continue
            pool = same_team
            scope = "SAME_TEAM_OTHER_SEASON"

        scales = scales_by_window[n]
        distance = np.zeros(len(pool), dtype=float)
        valid = np.ones(len(pool), dtype=bool)
        for metric in METRICS:
            event_val = pd.to_numeric(pd.Series([event[f"pre_{metric}"]]), errors="coerce").iloc[0]
            pool_vals = pd.to_numeric(pool[f"pre_{metric}"], errors="coerce").to_numpy(dtype=float)
            if pd.isna(event_val):
                continue
            valid &= np.isfinite(pool_vals)
            distance += ((pool_vals - float(event_val)) / scales[metric]) ** 2
        if not valid.any():
            continue
        usable = pool.loc[pool.index[valid]].copy()
        usable["_distance"] = np.sqrt(distance[valid])
        control = usable.sort_values(["_distance", "control_date", "control_fixture_id"]).iloc[0]

        rec = {
            "team": event["team"],
            "season": event["season"],
            "change_fixture_id": event["change_fixture_id"],
            "change_date": event["change_date"],
            "new_coach": event["new_coach"],
            "post_window": n,
            "control_fixture_id": control["control_fixture_id"],
            "control_date": control["control_date"],
            "control_coach": control["control_coach"],
            "match_scope": scope,
            "match_distance": float(control["_distance"]),
        }
        for metric in METRICS:
            rec[f"event_pre_{metric}"] = event[f"pre_{metric}"]
            rec[f"control_pre_{metric}"] = control[f"pre_{metric}"]
            rec[f"event_delta_{metric}"] = event[f"delta_{metric}"]
            rec[f"control_delta_{metric}"] = control[f"delta_{metric}"]
            if pd.notna(event[f"delta_{metric}"]) and pd.notna(control[f"delta_{metric}"]):
                rec[f"adjusted_delta_{metric}"] = (
                    float(event[f"delta_{metric}"]) - float(control[f"delta_{metric}"])
                )
            else:
                rec[f"adjusted_delta_{metric}"] = np.nan
        matched.append(rec)
    return pd.DataFrame(matched)


def summarize_events(events):
    rows = []
    for n, part in events.groupby("post_window", sort=True):
        rec = {"post_window": int(n), "events": int(len(part))}
        for metric in METRICS:
            values = pd.to_numeric(part[f"delta_{metric}"], errors="coerce").dropna()
            low, high = _bootstrap_ci(values, seed=RANDOM_SEED + int(n))
            rec[f"mean_delta_{metric}"] = float(values.mean()) if len(values) else np.nan
            rec[f"median_delta_{metric}"] = float(values.median()) if len(values) else np.nan
            rec[f"positive_share_{metric}"] = float((values > 0).mean()) if len(values) else np.nan
            rec[f"ci95_low_{metric}"] = low
            rec[f"ci95_high_{metric}"] = high
            rec[f"signflip_p_{metric}"] = _sign_flip_p(
                values, seed=RANDOM_SEED + 100 + int(n)
            )
        rows.append(rec)
    return pd.DataFrame(rows)


def summarize_matched(matched):
    rows = []
    if matched.empty:
        return pd.DataFrame()
    for n, part in matched.groupby("post_window", sort=True):
        rec = {"post_window": int(n), "pairs": int(len(part))}
        rec["same_team_season_share"] = float(
            (part["match_scope"] == "SAME_TEAM_SEASON").mean()
        )
        rec["mean_match_distance"] = float(
            pd.to_numeric(part["match_distance"], errors="coerce").mean()
        )
        for metric in METRICS:
            values = pd.to_numeric(
                part[f"adjusted_delta_{metric}"], errors="coerce"
            ).dropna()
            low, high = _bootstrap_ci(values, seed=RANDOM_SEED + 500 + int(n))
            rec[f"mean_adjusted_delta_{metric}"] = (
                float(values.mean()) if len(values) else np.nan
            )
            rec[f"ci95_low_adjusted_{metric}"] = low
            rec[f"ci95_high_adjusted_{metric}"] = high
            rec[f"signflip_p_adjusted_{metric}"] = _sign_flip_p(
                values, seed=RANDOM_SEED + 700 + int(n)
            )
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
    matched_output=MATCHED_OUTPUT,
    matched_summary_output=MATCHED_SUMMARY_OUTPUT,
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
    candidates = build_placebo_candidates(df)
    matched = match_placebo_controls(events, candidates)
    matched_summary = summarize_matched(matched)

    events.to_csv(events_output, index=False, encoding="utf-8-sig")
    summary.to_csv(summary_output, index=False, encoding="utf-8-sig")
    by_season.to_csv(season_output, index=False, encoding="utf-8-sig")
    matched.to_csv(matched_output, index=False, encoding="utf-8-sig")
    matched_summary.to_csv(matched_summary_output, index=False, encoding="utf-8-sig")

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

    print("Matched placebo control")
    if matched_summary.empty:
        print("No eligible matched controls")
    else:
        for _, row in matched_summary.iterrows():
            n = int(row["post_window"])
            print(
                f"Post {n}: pairs={int(row['pairs'])} "
                f"same_team_season={row['same_team_season_share']:.1%} "
                f"AH_adjusted={row['mean_adjusted_delta_actual_ah_cover_score']:+.3f} "
                f"CI=[{row['ci95_low_adjusted_actual_ah_cover_score']:+.3f},"
                f"{row['ci95_high_adjusted_actual_ah_cover_score']:+.3f}] "
                f"p={row['signflip_p_adjusted_actual_ah_cover_score']:.4f}"
            )
            print(
                f"        xGdiff_adjusted={row['mean_adjusted_delta_actual_xg_diff']:+.3f} "
                f"goal_diff_adjusted={row['mean_adjusted_delta_actual_goal_diff']:+.3f}"
            )

    print("Events output:", os.path.abspath(events_output))
    print("Summary output:", os.path.abspath(summary_output))
    print("Season output:", os.path.abspath(season_output))
    print("Matched output:", os.path.abspath(matched_output))
    print("Matched summary output:", os.path.abspath(matched_summary_output))
    return events, summary, by_season, matched, matched_summary


if __name__ == "__main__":
    run()
