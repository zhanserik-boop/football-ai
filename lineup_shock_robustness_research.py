from pathlib import Path

import numpy as np
import pandas as pd

import historical_lineup_shock_builder as lineup


OUTPUT_FILE = "epl_lineup_shock_robustness.csv"
SUMMARY_FILE = "epl_lineup_shock_robustness_summary.csv"

MIN_TOTAL_ROWS = 100
MIN_SEASON_ROWS = 20
MIN_SIDE_ROWS = 30
LARGE_MOVE_THRESHOLD = 0.25


def _date(series):
    return pd.to_datetime(series, utc=True, errors="coerce").dt.tz_convert(None).dt.normalize()


def wilson_interval(successes, total, z=1.96):
    if total <= 0:
        return np.nan, np.nan
    p = successes / total
    denom = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denom
    half = z * np.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total) / denom
    return max(0.0, center - half), min(1.0, center + half)


def mean_interval(values, z=1.96):
    x = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    if len(x) == 0:
        return np.nan, np.nan
    mean = float(x.mean())
    if len(x) == 1:
        return mean, mean
    half = z * float(x.std(ddof=1)) / np.sqrt(len(x))
    return mean - half, mean + half


def shock_bucket(value):
    try:
        x = abs(float(value))
    except (TypeError, ValueError):
        return "UNKNOWN"
    if not np.isfinite(x):
        return "UNKNOWN"
    if x < 2.0:
        return "1.5_TO_2.0"
    if x < 2.5:
        return "2.0_TO_2.5"
    return "GE_2.5"


def attach_market(lineup_df, market_df):
    left = lineup_df.copy()
    right = market_df.copy()
    for frame in (left, right):
        frame["season"] = pd.to_numeric(frame["season"], errors="coerce").astype("Int64")
        frame["date"] = _date(frame["date"])

    keys = ["season", "date", "home_team", "away_team"]
    keep = keys + ["open_ah_home_line", "close_ah_home_line", "close_move_home"]
    right = right[keep].drop_duplicates(keys, keep="last")
    out = left.merge(right, on=keys, how="left", validate="one_to_one")

    move = pd.to_numeric(out["close_move_home"], errors="coerce")
    # Football-Data stores the home handicap: home strengthening is a more
    # negative line, while away strengthening makes the home line positive.
    out["signed_close_move_for_lineup"] = np.where(
        out["signal"] == "HOME",
        -move,
        np.where(out["signal"] == "AWAY", move, np.nan),
    )
    out["shock_bucket"] = out["abs_shock"].map(shock_bucket)
    out["large_move"] = (move.abs() >= LARGE_MOVE_THRESHOLD).astype(int)
    out["shadow_only"] = 1
    return out


def _metrics(scope, frame):
    x = frame[frame["signed_close_move_for_lineup"].notna()].copy()
    signed = pd.to_numeric(x["signed_close_move_for_lineup"], errors="coerce")
    nonflat = signed[signed != 0]
    large = signed[x["large_move"] == 1]

    mean_low, mean_high = mean_interval(signed)
    nonflat_success = int((nonflat > 0).sum())
    nonflat_low, nonflat_high = wilson_interval(nonflat_success, len(nonflat))
    large_success = int((large > 0).sum())
    large_low, large_high = wilson_interval(large_success, len(large))

    return {
        "scope": scope,
        "rows": len(x),
        "avg_signed_close_move": float(signed.mean()) if len(x) else np.nan,
        "avg_signed_move_ci_low": mean_low,
        "avg_signed_move_ci_high": mean_high,
        "positive_move_rate_all": float((signed > 0).mean()) if len(x) else np.nan,
        "nonflat_rows": len(nonflat),
        "nonflat_direction_hit": nonflat_success / len(nonflat) if len(nonflat) else np.nan,
        "nonflat_hit_ci_low": nonflat_low,
        "nonflat_hit_ci_high": nonflat_high,
        "large_move_rows": len(large),
        "large_move_direction_hit": large_success / len(large) if len(large) else np.nan,
        "large_move_hit_ci_low": large_low,
        "large_move_hit_ci_high": large_high,
    }


def summarize(combined):
    eligible = combined[
        combined["signal"].isin(["HOME", "AWAY"])
        & combined["data_quality"].isin(["MEDIUM", "HIGH"])
        & combined["signed_close_move_for_lineup"].notna()
    ].copy()

    groups = [("ALL", eligible)]
    groups.extend(
        (f"SEASON_{int(season)}", part)
        for season, part in eligible.groupby("season", sort=True)
    )
    groups.extend(
        (f"QUALITY_{quality}", eligible[eligible["data_quality"] == quality])
        for quality in ("HIGH", "MEDIUM")
    )
    groups.extend(
        (f"SIDE_{side}", eligible[eligible["signal"] == side])
        for side in ("HOME", "AWAY")
    )
    groups.extend(
        (f"SHOCK_{bucket}", eligible[eligible["shock_bucket"] == bucket])
        for bucket in ("1.5_TO_2.0", "2.0_TO_2.5", "GE_2.5")
    )

    return pd.DataFrame([_metrics(scope, frame) for scope, frame in groups])


def stability_gate(summary):
    all_row = summary[summary["scope"] == "ALL"]
    if all_row.empty:
        return False, "Missing ALL scope"
    all_row = all_row.iloc[0]
    if all_row["rows"] < MIN_TOTAL_ROWS:
        return False, "Insufficient total sample"
    if not (
        all_row["avg_signed_move_ci_low"] > 0
        and all_row["large_move_hit_ci_low"] > 0.55
    ):
        return False, "Aggregate confidence interval failed"

    seasons = summary[
        summary["scope"].str.startswith("SEASON_")
        & (summary["rows"] >= MIN_SEASON_ROWS)
    ]
    if len(seasons) < 2:
        return False, "Insufficient season coverage"
    if not (
        (seasons["avg_signed_close_move"] > 0).all()
        and (seasons["nonflat_direction_hit"] > 0.55).all()
    ):
        return False, "Season stability failed"

    sides = summary[
        summary["scope"].isin(["SIDE_HOME", "SIDE_AWAY"])
        & (summary["rows"] >= MIN_SIDE_ROWS)
    ]
    if len(sides) != 2:
        return False, "Insufficient HOME/AWAY coverage"
    if not (sides["avg_signed_close_move"] > 0).all():
        return False, "HOME/AWAY stability failed"
    return True, "Passed historical stability gate"


def build():
    import market_anchored_fair_ah as market

    if Path(lineup.OUTPUT_FILE).exists():
        lineup_df = pd.read_csv(lineup.OUTPUT_FILE, encoding="utf-8-sig")
    else:
        lineup_df = lineup.build()

    market_df = market.add_market_features(market.load_inputs())
    combined = attach_market(lineup_df, market_df)
    summary = summarize(combined)
    candidate, reason = stability_gate(summary)
    summary["historical_stability_candidate"] = int(candidate)
    summary["stability_reason"] = reason
    summary["shadow_only"] = 1

    combined.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    summary.to_csv(SUMMARY_FILE, index=False, encoding="utf-8-sig")

    print("Lineup Shock Robustness Research")
    print("Target: closing AH movement, not betting ROI")
    for row in summary.itertuples(index=False):
        if row.scope == "ALL" or row.scope.startswith(("SEASON_", "SIDE_", "QUALITY_", "SHOCK_")):
            print(
                f"{row.scope}: rows={row.rows}, "
                f"avg_signed_move={row.avg_signed_close_move:+.4f}, "
                f"nonflat_hit={100.0 * row.nonflat_direction_hit:.1f}%, "
                f"large_hit={100.0 * row.large_move_direction_hit:.1f}%"
                if pd.notna(row.avg_signed_close_move)
                and pd.notna(row.nonflat_direction_hit)
                and pd.notna(row.large_move_direction_hit)
                else f"{row.scope}: rows={row.rows}"
            )
    print("Historical stability candidate:", "YES" if candidate else "NO")
    print("Reason:", reason)
    print("Production AH Agent / Master changes: NONE")
    print("Status: SHADOW ONLY")
    print("Output:", Path(OUTPUT_FILE).resolve())
    print("Summary:", Path(SUMMARY_FILE).resolve())
    return combined, summary


if __name__ == "__main__":
    build()
