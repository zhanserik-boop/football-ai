from pathlib import Path

import numpy as np
import pandas as pd

import historical_lineup_shock_builder as lineup


OUTPUT_FILE = "epl_shadow_value_gate.csv"
SUMMARY_FILE = "epl_shadow_value_gate_summary.csv"
MIN_PROMOTION_ROWS = 50
MOVE_THRESHOLD = 0.25


def _date(series):
    return pd.to_datetime(series, utc=True, errors="coerce").dt.tz_convert(None).dt.normalize()


def combine(lineup_df, direction_df):
    left = lineup_df.copy()
    right = direction_df.copy()
    for frame in (left, right):
        frame["season"] = pd.to_numeric(frame["season"], errors="coerce").astype("Int64")
        frame["date"] = _date(frame["date"])

    keys = ["season", "date", "home_team", "away_team"]
    keep = keys + [
        "close_move_home", "direction_score", "direction_signal",
        "confidence_cutoff", "high_confidence",
        "model_train_through_season_direction",
    ]
    right = right[keep].drop_duplicates(keys, keep="last")
    out = left.merge(right, on=keys, how="left", validate="one_to_one")

    # Football-Data AHh/AHCh store the home handicap. A stronger home side
    # moves from e.g. -0.50 to -0.75, so close-open is NEGATIVE. A stronger
    # away side makes the home handicap more positive.
    out["direction_side"] = np.where(
        pd.to_numeric(out["direction_score"], errors="coerce") < 0,
        "HOME",
        np.where(pd.to_numeric(out["direction_score"], errors="coerce") > 0, "AWAY", ""),
    )
    out["direction_agrees"] = (
        out["signal"].isin(["HOME", "AWAY"])
        & out["signal"].eq(out["direction_side"])
    ).astype(int)
    move = pd.to_numeric(out["close_move_home"], errors="coerce")
    out["signed_close_move_for_lineup"] = np.where(
        out["signal"] == "HOME", -move,
        np.where(out["signal"] == "AWAY", move, np.nan),
    )
    out["large_move"] = (move.abs() >= MOVE_THRESHOLD).astype(int)
    out["shadow_only"] = 1
    return out


def _metrics(scope, frame):
    x = frame[frame["signed_close_move_for_lineup"].notna()].copy()
    signed = pd.to_numeric(x["signed_close_move_for_lineup"], errors="coerce")
    nonflat = x[signed != 0]
    large = x[x["large_move"] == 1]
    return {
        "scope": scope,
        "rows": len(x),
        "share_of_lineup_signals": np.nan,
        "avg_signed_close_move": float(signed.mean()) if len(x) else np.nan,
        "positive_clv_direction_rate": float((signed > 0).mean()) if len(x) else np.nan,
        "nonflat_direction_hit": float((nonflat["signed_close_move_for_lineup"] > 0).mean()) if len(nonflat) else np.nan,
        "large_move_rows": len(large),
        "large_move_direction_hit": float((large["signed_close_move_for_lineup"] > 0).mean()) if len(large) else np.nan,
    }


def summarize(combined):
    eligible = combined[
        combined["signal"].isin(["HOME", "AWAY"])
        & combined["data_quality"].isin(["MEDIUM", "HIGH"])
        & combined["direction_score"].notna()
    ].copy()

    groups = [
        ("ALL_LINEUP_SIGNALS", eligible),
        ("DIRECTION_AGREE", eligible[eligible["direction_agrees"] == 1]),
        ("DIRECTION_DISAGREE", eligible[eligible["direction_agrees"] == 0]),
        (
            "HIGH_CONFIDENCE_AGREE",
            eligible[(eligible["direction_agrees"] == 1) & (eligible["high_confidence"] == 1)],
        ),
        (
            "HIGH_CONFIDENCE_DISAGREE",
            eligible[(eligible["direction_agrees"] == 0) & (eligible["high_confidence"] == 1)],
        ),
    ]
    rows = [_metrics(name, frame) for name, frame in groups]
    total = rows[0]["rows"] if rows else 0
    for row in rows:
        row["share_of_lineup_signals"] = row["rows"] / total if total else np.nan

    summary = pd.DataFrame(rows)
    baseline = summary[summary["scope"] == "ALL_LINEUP_SIGNALS"]
    candidate = summary[summary["scope"] == "HIGH_CONFIDENCE_AGREE"]
    promote = False
    if not baseline.empty and not candidate.empty:
        b = baseline.iloc[0]
        c = candidate.iloc[0]
        promote = bool(
            c["rows"] >= MIN_PROMOTION_ROWS
            and pd.notna(c["avg_signed_close_move"])
            and pd.notna(b["avg_signed_close_move"])
            and c["avg_signed_close_move"] > b["avg_signed_close_move"] + 0.025
            and pd.notna(c["large_move_direction_hit"])
            and pd.notna(b["large_move_direction_hit"])
            and c["large_move_direction_hit"] > b["large_move_direction_hit"] + 0.03
        )
    summary["research_promotion_candidate"] = int(promote)
    summary["shadow_only"] = 1
    return summary


def build():
    import market_anchored_fair_ah_v2 as v2
    import market_move_direction_research as direction

    if Path(lineup.OUTPUT_FILE).exists():
        lineup_df = pd.read_csv(lineup.OUTPUT_FILE, encoding="utf-8-sig")
    else:
        lineup_df = lineup.build()
    enriched = v2.load_enriched()
    direction_df = direction.walk_forward(enriched)
    combined = combine(lineup_df, direction_df)
    summary = summarize(combined)
    combined.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    summary.to_csv(SUMMARY_FILE, index=False, encoding="utf-8-sig")

    print("Shadow Value Gate — Lineup Shock + Directional CLV")
    for row in summary.itertuples(index=False):
        print(
            f"{row.scope}: rows={row.rows}, "
            f"avg_signed_move={row.avg_signed_close_move:+.4f}, "
            f"large_move_hit={100.0 * row.large_move_direction_hit:.1f}%"
            if pd.notna(row.avg_signed_close_move) and pd.notna(row.large_move_direction_hit)
            else f"{row.scope}: rows={row.rows}"
        )
    promoted = bool(summary["research_promotion_candidate"].max())
    print("Research promotion candidate:", "YES" if promoted else "NO")
    print("Production AH Agent / Master changes: NONE")
    print("Status: SHADOW ONLY")
    print("Output:", Path(OUTPUT_FILE).resolve())
    print("Summary:", Path(SUMMARY_FILE).resolve())
    return combined, summary


if __name__ == "__main__":
    build()
