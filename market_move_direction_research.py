from pathlib import Path
import numpy as np
import pandas as pd

import market_anchored_fair_ah as v1
import market_anchored_fair_ah_v2 as v2

OUTPUT_FILE = "epl_market_move_direction.csv"
SUMMARY_FILE = "epl_market_move_direction_summary.csv"
FEATURES = v2.FEATURES_V2
MIN_TRAIN_ROWS = 250
RIDGE_ALPHA = 10.0
MOVE_THRESHOLD = 0.25
CONFIDENCE_QUANTILE = 0.75


def _fit_direction_ridge(train, features=FEATURES, alpha=RIDGE_ALPHA):
    x = train[features].copy()
    move = pd.to_numeric(train["close_move_home"], errors="coerce")
    y = np.where(move >= MOVE_THRESHOLD, 1.0, np.where(move <= -MOVE_THRESHOLD, -1.0, 0.0))
    valid = move.notna()
    x = x.loc[valid]
    y = y[valid.to_numpy()]

    med = x.median(numeric_only=True).reindex(features).fillna(0.0)
    x = x.fillna(med).to_numpy(dtype=float, copy=True)
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std[std < 1e-9] = 1.0
    z = (x - mean) / std
    design = np.column_stack([np.ones(len(z)), z])
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    return {"beta": beta, "median": med.to_numpy(dtype=float), "mean": mean, "std": std}


def _predict(model, frame, features=FEATURES):
    x = frame[features].copy().to_numpy(dtype=float, copy=True)
    for j in range(x.shape[1]):
        bad = ~np.isfinite(x[:, j])
        x[bad, j] = model["median"][j]
    z = (x - model["mean"]) / model["std"]
    design = np.column_stack([np.ones(len(z)), z])
    return design @ model["beta"]


def walk_forward(df):
    d = df.copy()
    d["direction_score"] = np.nan
    d["direction_signal"] = ""
    d["confidence_cutoff"] = np.nan
    d["high_confidence"] = 0
    d["model_train_rows_direction"] = 0
    d["model_train_through_season_direction"] = np.nan

    seasons = sorted(int(x) for x in d["season"].dropna().unique())
    for season in seasons[1:]:
        train = d[(d["season"] < season) & d["close_move_home"].notna()].copy()
        test = d[d["season"] == season].copy()
        if len(train) < MIN_TRAIN_ROWS or test.empty:
            continue
        model = _fit_direction_ridge(train)
        train_scores = _predict(model, train)
        cutoff = float(np.quantile(np.abs(train_scores), CONFIDENCE_QUANTILE))
        pred = _predict(model, test)
        d.loc[test.index, "direction_score"] = pred
        d.loc[test.index, "direction_signal"] = np.where(pred > 0, "HOME_STRENGTHEN", np.where(pred < 0, "AWAY_STRENGTHEN", "FLAT"))
        d.loc[test.index, "confidence_cutoff"] = cutoff
        d.loc[test.index, "high_confidence"] = (np.abs(pred) >= cutoff).astype(int)
        d.loc[test.index, "model_train_rows_direction"] = len(train)
        d.loc[test.index, "model_train_through_season_direction"] = season - 1
    return d


def summarize(df):
    tested = df[df["direction_score"].notna() & df["close_move_home"].notna()].copy()
    move = pd.to_numeric(tested["close_move_home"], errors="coerce")
    score = pd.to_numeric(tested["direction_score"], errors="coerce")
    large = tested[np.abs(move) >= MOVE_THRESHOLD].copy()
    large_hit = float((np.sign(large["direction_score"]) == np.sign(large["close_move_home"])).mean()) if len(large) else np.nan

    selected = tested[tested["high_confidence"] == 1].copy()
    selected_move = pd.to_numeric(selected["close_move_home"], errors="coerce")
    selected_large = selected[np.abs(selected_move) >= MOVE_THRESHOLD].copy()
    selected_large_hit = float((np.sign(selected_large["direction_score"]) == np.sign(selected_large["close_move_home"])).mean()) if len(selected_large) else np.nan
    signed_move = np.sign(pd.to_numeric(selected["direction_score"], errors="coerce")) * selected_move

    rows = [{
        "scope": "ALL_WALK_FORWARD",
        "rows": len(tested),
        "large_move_rows": len(large),
        "large_move_direction_hit": large_hit,
        "selected_rows": len(selected),
        "selected_share": len(selected) / len(tested) if len(tested) else np.nan,
        "selected_large_move_rows": len(selected_large),
        "selected_large_move_rate": len(selected_large) / len(selected) if len(selected) else np.nan,
        "selected_large_move_direction_hit": selected_large_hit,
        "selected_avg_signed_close_move": float(signed_move.mean()) if len(selected) else np.nan,
        "selected_positive_clv_direction_rate": float((signed_move > 0).mean()) if len(selected) else np.nan,
    }]

    for season, part in tested.groupby("season", sort=True):
        pmove = pd.to_numeric(part["close_move_home"], errors="coerce")
        plarge = part[np.abs(pmove) >= MOVE_THRESHOLD]
        phit = float((np.sign(plarge["direction_score"]) == np.sign(plarge["close_move_home"])).mean()) if len(plarge) else np.nan
        psel = part[part["high_confidence"] == 1]
        psel_move = pd.to_numeric(psel["close_move_home"], errors="coerce")
        psel_large = psel[np.abs(psel_move) >= MOVE_THRESHOLD]
        psel_hit = float((np.sign(psel_large["direction_score"]) == np.sign(psel_large["close_move_home"])).mean()) if len(psel_large) else np.nan
        rows.append({"scope": f"season_{int(season)}", "rows": len(part), "large_move_rows": len(plarge), "large_move_direction_hit": phit, "selected_rows": len(psel), "selected_large_move_rows": len(psel_large), "selected_large_move_direction_hit": psel_hit})
    return pd.DataFrame(rows)


def build():
    d = v2.load_enriched()
    out = walk_forward(d)
    summary = summarize(out)
    out["shadow_only"] = 1
    out.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    summary.to_csv(SUMMARY_FILE, index=False, encoding="utf-8-sig")

    r = summary.iloc[0]
    print("AH Market Movement Direction Research")
    print("Walk-forward rows:", int(r["rows"]))
    print("Large-move rows:", int(r["large_move_rows"]))
    print("All large-move direction hit:", f"{100*r['large_move_direction_hit']:.1f}%")
    print("High-confidence selected rows:", int(r["selected_rows"]), f"({100*r['selected_share']:.1f}%)")
    print("Selected large-move rate:", f"{100*r['selected_large_move_rate']:.1f}%")
    print("Selected large-move direction hit:", f"{100*r['selected_large_move_direction_hit']:.1f}%")
    print("Selected average signed close move:", f"{r['selected_avg_signed_close_move']:+.4f}")
    print("Selected positive-CLV direction rate:", f"{100*r['selected_positive_clv_direction_rate']:.1f}%")
    print("Interpretation: direction/ranking research only; not a fair-price model and not a BET rule.")
    print("Status: SHADOW ONLY")
    print("Output:", Path(OUTPUT_FILE).resolve())
    print("Summary:", Path(SUMMARY_FILE).resolve())
    return out, summary


if __name__ == "__main__":
    build()
