from pathlib import Path
import os
import numpy as np
import pandas as pd

import market_anchored_fair_ah as v1

EXTENDED_FILE = "epl_extended_style_context.csv"
OUTPUT_FILE = "epl_market_anchored_fair_ah_v2.csv"
SUMMARY_FILE = "epl_market_anchored_fair_ah_v2_summary.csv"

EXTENDED_FEATURES = [
    "possession_edge_home",
    "shot_dominance_edge_home",
    "sot_dominance_edge_home",
    "sot_rate_edge_home",
    "corners_edge_home",
    "cards_edge_home",
    "first_half_shot_share_edge_home",
    "second_half_surge_edge_home",
]
FEATURES_V2 = v1.FEATURES + EXTENDED_FEATURES


def _date(series):
    return pd.to_datetime(series, utc=True, errors="coerce").dt.tz_convert(None).dt.normalize()


def load_enriched():
    base = v1.add_market_features(v1.load_inputs())
    if not os.path.exists(EXTENDED_FILE):
        raise FileNotFoundError(f"{EXTENDED_FILE} not found. Run extended_style_context_builder.py first.")
    ext = pd.read_csv(EXTENDED_FILE, encoding="utf-8-sig")
    ext["season"] = pd.to_numeric(ext["season"], errors="coerce").astype("Int64")
    ext["date"] = _date(ext["date"])
    keep = v1.KEYS + [x for x in EXTENDED_FEATURES if x in ext.columns]
    ext = ext[keep].drop_duplicates(v1.KEYS, keep="last")
    d = base.merge(ext, on=v1.KEYS, how="left", validate="one_to_one")
    for f in EXTENDED_FEATURES:
        if f not in d.columns:
            d[f] = np.nan
        d[f] = pd.to_numeric(d[f], errors="coerce")
    return d


def _predict_writable(model, frame, features):
    x = np.array(frame[features].to_numpy(dtype=float), dtype=float, copy=True)
    med = model["median"]
    for j in range(x.shape[1]):
        bad = ~np.isfinite(x[:, j])
        x[bad, j] = med[j]
    z = (x - model["mean"]) / model["std"]
    design = np.column_stack([np.ones(len(z)), z])
    return design @ model["beta"]


def walk_forward_features(df, features):
    d = df.copy()
    pred_col = "predicted_close_move_home_v2"
    fair_col = "fair_ah_proxy_home_v2"
    d[pred_col] = np.nan
    d[fair_col] = np.nan
    d["model_train_rows_v2"] = 0
    d["model_train_through_season_v2"] = np.nan
    seasons = sorted(int(x) for x in d["season"].dropna().unique())
    for season in seasons[1:]:
        train = d[d["season"] < season].copy()
        test = d[d["season"] == season].copy()
        train = train[train["close_move_home"].notna()]
        if len(train) < v1.MIN_TRAIN_ROWS or test.empty:
            continue
        model = v1._fit_ridge(train, features=features)
        pred = _predict_writable(model, test, features)
        d.loc[test.index, pred_col] = pred
        d.loc[test.index, fair_col] = pd.to_numeric(test["open_ah_home_line"], errors="coerce").to_numpy(dtype=float) + pred
        d.loc[test.index, "model_train_rows_v2"] = len(train)
        d.loc[test.index, "model_train_through_season_v2"] = season - 1
    d["fair_ah_proxy_home_v2_quarter"] = (d[fair_col] * 4.0).round() / 4.0
    return d


def _metrics(df, pred_col):
    x = df[df[pred_col].notna() & df["close_move_home"].notna()].copy()
    actual = pd.to_numeric(x["close_move_home"], errors="coerce")
    pred = pd.to_numeric(x[pred_col], errors="coerce")
    baseline_mae = float(np.abs(actual).mean()) if len(x) else np.nan
    model_mae = float(np.abs(actual - pred).mean()) if len(x) else np.nan
    moved = x[np.abs(actual) >= 0.25]
    direction = float((np.sign(pd.to_numeric(moved[pred_col], errors="coerce")) == np.sign(pd.to_numeric(moved["close_move_home"], errors="coerce"))).mean()) if len(moved) else np.nan
    return {"rows": len(x), "baseline_mae": baseline_mae, "model_mae": model_mae, "mae_improvement_vs_open": baseline_mae - model_mae if len(x) else np.nan, "large_move_rows": len(moved), "large_move_direction_hit": direction}


def build():
    d = load_enriched()
    v1_out = v1.walk_forward(d)
    out = walk_forward_features(v1_out, FEATURES_V2)
    m1 = _metrics(out, "predicted_close_move_home")
    m2 = _metrics(out, "predicted_close_move_home_v2")
    summary = pd.DataFrame([
        {"model": "OPENING_LINE_BASELINE", "rows": m2["rows"], "mae": m2["baseline_mae"], "large_move_direction_hit": np.nan},
        {"model": "FAIR_AH_V1", "rows": m1["rows"], "mae": m1["model_mae"], "large_move_direction_hit": m1["large_move_direction_hit"]},
        {"model": "FAIR_AH_V2_ENRICHED", "rows": m2["rows"], "mae": m2["model_mae"], "large_move_direction_hit": m2["large_move_direction_hit"]},
    ])
    out["shadow_only"] = 1
    out.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    summary.to_csv(SUMMARY_FILE, index=False, encoding="utf-8-sig")
    print("Market-Anchored Fair AH V2 — incremental enriched-context test")
    print("Walk-forward rows:", m2["rows"])
    print("Opening baseline MAE:", f"{m2['baseline_mae']:.4f}")
    print("V1 MAE:", f"{m1['model_mae']:.4f}")
    print("V2 enriched MAE:", f"{m2['model_mae']:.4f}")
    print("V2 improvement vs opening:", f"{m2['baseline_mae'] - m2['model_mae']:+.4f}")
    print("V2 improvement vs V1:", f"{m1['model_mae'] - m2['model_mae']:+.4f}")
    print("V1 large-move direction:", f"{100.0*m1['large_move_direction_hit']:.1f}%")
    print("V2 large-move direction:", f"{100.0*m2['large_move_direction_hit']:.1f}%")
    print("Decision rule: V2 is promoted only if it beats opening-line MAE out-of-sample; otherwise it stays research-only.")
    print("Status: SHADOW ONLY")
    print("Output:", Path(OUTPUT_FILE).resolve())
    print("Summary:", Path(SUMMARY_FILE).resolve())
    return out, summary


if __name__ == "__main__":
    build()
