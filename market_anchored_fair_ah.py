from pathlib import Path
import os
import numpy as np
import pandas as pd

CONTEXT_FILE = "epl_context_history.csv"
MATCHUP_FILE = "epl_matchup_context.csv"
OUTPUT_FILE = "epl_market_anchored_fair_ah.csv"
SUMMARY_FILE = "epl_market_anchored_fair_ah_summary.csv"
MIN_TRAIN_ROWS = 250
RIDGE_ALPHA = 8.0

KEYS = ["season", "date", "home_team", "away_team"]
FEATURES = [
    "open_ah_home_line",
    "market_home_prob",
    "market_draw_prob",
    "market_away_prob",
    "matchup_xg_balance_edge_home",
    "ah_cover_edge_home",
    "attack_volume_edge_home",
    "shot_quality_edge_home",
    "tempo_mean",
    "h2h_relevant_xg_diff_home",
    "h2h_relevant_ah_cover_home",
    "h2h_relevant_count",
    "underdog_resistance_score",
    "draw_pressure_score",
]


def _date(series):
    return pd.to_datetime(series, utc=True, errors="coerce").dt.tz_convert(None).dt.normalize()


def _num(series):
    return pd.to_numeric(series, errors="coerce")


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -20.0, 20.0)))


def _normalized_probs(h, d, a):
    h, d, a = float(h), float(d), float(a)
    if min(h, d, a) <= 1.0:
        return np.nan, np.nan, np.nan
    inv = np.array([1.0 / h, 1.0 / d, 1.0 / a], dtype=float)
    inv /= inv.sum()
    return tuple(inv.tolist())


def _ah_cover(home_goals, away_goals, home_line):
    if pd.isna(home_goals) or pd.isna(away_goals) or pd.isna(home_line):
        return np.nan
    margin = float(home_goals) - float(away_goals) + float(home_line)
    if margin > 0:
        return 1.0
    if margin < 0:
        return 0.0
    return 0.5


def load_inputs(context_file=CONTEXT_FILE, matchup_file=MATCHUP_FILE):
    if not os.path.exists(context_file):
        raise FileNotFoundError(f"{context_file} not found")
    if not os.path.exists(matchup_file):
        raise FileNotFoundError(f"{matchup_file} not found")

    c = pd.read_csv(context_file, encoding="utf-8-sig")
    m = pd.read_csv(matchup_file, encoding="utf-8-sig")
    for d in (c, m):
        d["season"] = _num(d["season"]).astype("Int64")
        d["date"] = _date(d["date"])

    keep = [x for x in m.columns if x in set(KEYS + [
        "home_style_prior_matches", "away_style_prior_matches",
        "home_xg_balance", "away_xg_balance", "matchup_xg_balance_edge_home",
        "home_attack_volume", "away_attack_volume", "home_shot_quality", "away_shot_quality",
        "home_tempo_proxy", "away_tempo_proxy", "home_ah_cover_8", "away_ah_cover_8",
        "h2h_prior_count", "h2h_relevant_count", "h2h_relevant_xg_diff_home",
        "h2h_relevant_ah_cover_home",
    ])]
    m = m[keep].drop_duplicates(KEYS, keep="last")
    return c.merge(m, on=KEYS, how="left", validate="one_to_one")


def add_market_features(df):
    d = df.copy()
    numeric = [
        "open_home_odds", "open_draw_odds", "open_away_odds", "open_ah_home_line",
        "close_ah_home_line", "home_goals", "away_goals",
        "home_xg_balance", "away_xg_balance", "matchup_xg_balance_edge_home",
        "home_attack_volume", "away_attack_volume", "home_shot_quality", "away_shot_quality",
        "home_tempo_proxy", "away_tempo_proxy", "home_ah_cover_8", "away_ah_cover_8",
        "h2h_relevant_count", "h2h_relevant_xg_diff_home", "h2h_relevant_ah_cover_home",
    ]
    for col in numeric:
        if col in d.columns:
            d[col] = _num(d[col])

    probs = d.apply(
        lambda r: _normalized_probs(r.get("open_home_odds"), r.get("open_draw_odds"), r.get("open_away_odds"))
        if pd.notna(r.get("open_home_odds")) and pd.notna(r.get("open_draw_odds")) and pd.notna(r.get("open_away_odds"))
        else (np.nan, np.nan, np.nan),
        axis=1,
        result_type="expand",
    )
    probs.columns = ["market_home_prob", "market_draw_prob", "market_away_prob"]
    d[probs.columns] = probs

    d["ah_cover_edge_home"] = d.get("home_ah_cover_8", np.nan) - d.get("away_ah_cover_8", np.nan)
    d["attack_volume_edge_home"] = d.get("home_attack_volume", np.nan) - d.get("away_attack_volume", np.nan)
    d["shot_quality_edge_home"] = d.get("home_shot_quality", np.nan) - d.get("away_shot_quality", np.nan)
    d["tempo_mean"] = (d.get("home_tempo_proxy", np.nan) + d.get("away_tempo_proxy", np.nan)) / 2.0

    # Side is defined by the opening AH line; pick'em falls back to market probability.
    side = []
    for _, r in d.iterrows():
        line = r.get("open_ah_home_line")
        if pd.notna(line) and line > 0:
            side.append("HOME")
        elif pd.notna(line) and line < 0:
            side.append("AWAY")
        elif pd.notna(r.get("market_home_prob")) and pd.notna(r.get("market_away_prob")):
            side.append("HOME" if r["market_home_prob"] < r["market_away_prob"] else "AWAY")
        else:
            side.append("")
    d["underdog_side"] = side

    # These are contextual indices, not betting rules. 0.50 is neutral.
    resist = []
    drawp = []
    for _, r in d.iterrows():
        home_dog = r["underdog_side"] == "HOME"
        xg_edge = r.get("matchup_xg_balance_edge_home")
        ah_edge = r.get("ah_cover_edge_home")
        quality_edge = r.get("shot_quality_edge_home")
        h2h_cover = r.get("h2h_relevant_ah_cover_home")

        sign = 1.0 if home_dog else -1.0
        pieces = []
        if pd.notna(xg_edge): pieces.append(0.65 * sign * float(xg_edge))
        if pd.notna(ah_edge): pieces.append(1.25 * sign * float(ah_edge))
        if pd.notna(quality_edge): pieces.append(2.0 * sign * float(quality_edge))
        if pd.notna(h2h_cover):
            dog_h2h = float(h2h_cover) if home_dog else 1.0 - float(h2h_cover)
            pieces.append(0.8 * (dog_h2h - 0.5))
        resistance_raw = sum(pieces) / max(1, len(pieces))
        resist.append(float(_sigmoid(resistance_raw)))

        draw_prob = r.get("market_draw_prob")
        balance = abs(float(xg_edge)) if pd.notna(xg_edge) else np.nan
        tempo = r.get("tempo_mean")
        terms = []
        if pd.notna(draw_prob): terms.append(5.0 * (float(draw_prob) - 0.25))
        if pd.notna(balance): terms.append(-0.8 * balance)
        if pd.notna(tempo): terms.append(-0.06 * (float(tempo) - 24.0))
        drawp.append(float(_sigmoid(sum(terms) / max(1, len(terms)))))

    d["underdog_resistance_score"] = resist
    d["draw_pressure_score"] = drawp
    d["actual_home_ah_cover"] = d.apply(
        lambda r: _ah_cover(r.get("home_goals"), r.get("away_goals"), r.get("open_ah_home_line")), axis=1
    )
    d["actual_draw"] = (d.get("home_goals") == d.get("away_goals")).astype(float)
    d["close_move_home"] = d.get("close_ah_home_line") - d.get("open_ah_home_line")
    return d


def _fit_ridge(train, features=FEATURES, alpha=RIDGE_ALPHA):
    x = train[features].copy()
    y = _num(train["close_move_home"])
    valid_y = y.notna()
    x = x.loc[valid_y]
    y = y.loc[valid_y].to_numpy(dtype=float)

    med = x.median(numeric_only=True).reindex(features).fillna(0.0)
    x = x.fillna(med).to_numpy(dtype=float)
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
    x = frame[features].copy().to_numpy(dtype=float)
    med = model["median"]
    for j in range(x.shape[1]):
        bad = ~np.isfinite(x[:, j])
        x[bad, j] = med[j]
    z = (x - model["mean"]) / model["std"]
    design = np.column_stack([np.ones(len(z)), z])
    return design @ model["beta"]


def walk_forward(df):
    d = df.copy()
    d["fair_ah_proxy_home"] = np.nan
    d["predicted_close_move_home"] = np.nan
    d["model_train_rows"] = 0
    d["model_train_through_season"] = np.nan

    seasons = sorted(int(x) for x in d["season"].dropna().unique())
    for season in seasons[1:]:
        train = d[d["season"] < season].copy()
        test = d[d["season"] == season].copy()
        train = train[train["close_move_home"].notna()]
        if len(train) < MIN_TRAIN_ROWS or test.empty:
            continue
        model = _fit_ridge(train)
        pred = _predict(model, test)
        d.loc[test.index, "predicted_close_move_home"] = pred
        d.loc[test.index, "fair_ah_proxy_home"] = _num(test["open_ah_home_line"]).to_numpy(dtype=float) + pred
        d.loc[test.index, "model_train_rows"] = len(train)
        d.loc[test.index, "model_train_through_season"] = season - 1
    d["fair_ah_proxy_home_quarter"] = (d["fair_ah_proxy_home"] * 4.0).round() / 4.0
    return d


def summarize(df):
    rows = []
    tested = df[df["predicted_close_move_home"].notna() & df["close_move_home"].notna()].copy()
    for season, part in tested.groupby("season", sort=True):
        base_err = np.abs(_num(part["close_move_home"]))
        model_err = np.abs(_num(part["close_move_home"]) - _num(part["predicted_close_move_home"]))
        moved = part[np.abs(_num(part["close_move_home"])) >= 0.25].copy()
        hit = np.nan
        if len(moved):
            hit = float((np.sign(_num(moved["predicted_close_move_home"])) == np.sign(_num(moved["close_move_home"]))).mean())
        rows.append({
            "scope": f"season_{int(season)}", "rows": len(part),
            "baseline_mae_open_to_close": float(base_err.mean()),
            "model_mae_predicted_close": float(model_err.mean()),
            "mae_improvement": float(base_err.mean() - model_err.mean()),
            "large_move_rows": len(moved), "large_move_direction_hit": hit,
        })
    if len(tested):
        base_err = np.abs(_num(tested["close_move_home"]))
        model_err = np.abs(_num(tested["close_move_home"]) - _num(tested["predicted_close_move_home"]))
        moved = tested[np.abs(_num(tested["close_move_home"])) >= 0.25]
        hit = float((np.sign(_num(moved["predicted_close_move_home"])) == np.sign(_num(moved["close_move_home"]))).mean()) if len(moved) else np.nan
        rows.append({
            "scope": "ALL_WALK_FORWARD", "rows": len(tested),
            "baseline_mae_open_to_close": float(base_err.mean()),
            "model_mae_predicted_close": float(model_err.mean()),
            "mae_improvement": float(base_err.mean() - model_err.mean()),
            "large_move_rows": len(moved), "large_move_direction_hit": hit,
        })

    # Descriptive validation of the two context indices. These do not tune the model.
    eligible = df[df["underdog_side"].isin(["HOME", "AWAY"])].copy()
    if len(eligible):
        q75 = eligible["underdog_resistance_score"].quantile(0.75)
        high = eligible[eligible["underdog_resistance_score"] >= q75].copy()
        dog_cover = np.where(high["underdog_side"] == "HOME", high["actual_home_ah_cover"], 1.0 - high["actual_home_ah_cover"])
        rows.append({"scope": "HIGH_UNDERDOG_RESISTANCE", "rows": len(high), "underdog_ah_cover": float(pd.Series(dog_cover).mean())})

        q75d = eligible["draw_pressure_score"].quantile(0.75)
        highd = eligible[eligible["draw_pressure_score"] >= q75d]
        rows.append({"scope": "HIGH_DRAW_PRESSURE", "rows": len(highd), "actual_draw_rate": float(_num(highd["actual_draw"]).mean())})
    return pd.DataFrame(rows)


def build(context_file=CONTEXT_FILE, matchup_file=MATCHUP_FILE, output_file=OUTPUT_FILE, summary_file=SUMMARY_FILE):
    df = add_market_features(load_inputs(context_file, matchup_file))
    df = walk_forward(df)
    summary = summarize(df)
    df["shadow_only"] = 1
    df.to_csv(output_file, index=False, encoding="utf-8-sig")
    summary.to_csv(summary_file, index=False, encoding="utf-8-sig")

    tested = df["predicted_close_move_home"].notna().sum()
    print("Underdog Resistance / Draw Pressure / Market-Anchored Fair AH built")
    print("Fixtures:", len(df))
    print("Walk-forward fair-AH rows:", int(tested))
    all_row = summary[summary["scope"] == "ALL_WALK_FORWARD"]
    if not all_row.empty:
        r = all_row.iloc[0]
        print("Opening-line baseline MAE:", f"{r['baseline_mae_open_to_close']:.4f}")
        print("Fair-AH proxy MAE:", f"{r['model_mae_predicted_close']:.4f}")
        print("MAE improvement:", f"{r['mae_improvement']:+.4f}")
        print("Large-move direction hit:", f"{100.0 * r['large_move_direction_hit']:.1f}%")
    print("Status: SHADOW ONLY — expected closing-line proxy, not a production fair price")
    print("Output:", Path(output_file).resolve())
    print("Summary:", Path(summary_file).resolve())
    return df, summary


if __name__ == "__main__":
    build()
