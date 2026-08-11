from pathlib import Path
import os
import numpy as np
import pandas as pd

CONTEXT_FILE = "epl_context_history.csv"
FORMATION_FILE = "epl_formation_context.csv"
COACH_FILE = "epl_coach_context.csv"
OUTPUT_FILE = "epl_matchup_context.csv"
STYLE_WINDOW = 8
H2H_WINDOW = 6
MIN_STYLE = 4


def _num(v):
    return pd.to_numeric(v, errors="coerce")


def _safe_mean(values):
    s = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    return float(s.mean()) if len(s) else np.nan


def _ah_cover_score(home_goals, away_goals, home_line):
    if pd.isna(home_goals) or pd.isna(away_goals) or pd.isna(home_line):
        return np.nan
    margin = float(home_goals) - float(away_goals) + float(home_line)
    if margin > 0:
        return 1.0
    if margin < 0:
        return 0.0
    return 0.5


def to_team_matches(matches):
    rows = []
    for _, r in matches.iterrows():
        common = {
            "season": r["season"], "date": r["date"],
            "home_team": r["home_team"], "away_team": r["away_team"],
        }
        home_cover = _ah_cover_score(r.get("home_goals"), r.get("away_goals"), r.get("open_ah_home_line"))
        for side in ("home", "away"):
            is_home = side == "home"
            team = r[f"{side}_team"]
            opp = r["away_team"] if is_home else r["home_team"]
            xgf = r.get("home_xg") if is_home else r.get("away_xg")
            xga = r.get("away_xg") if is_home else r.get("home_xg")
            sf = r.get("home_shots") if is_home else r.get("away_shots")
            sa = r.get("away_shots") if is_home else r.get("home_shots")
            sotf = r.get("home_sot") if is_home else r.get("away_sot")
            sota = r.get("away_sot") if is_home else r.get("home_sot")
            cf = r.get("home_corners") if is_home else r.get("away_corners")
            ca = r.get("away_corners") if is_home else r.get("home_corners")
            ff = r.get("home_fouls") if is_home else r.get("away_fouls")
            fa = r.get("away_fouls") if is_home else r.get("home_fouls")
            gf = r.get("home_goals") if is_home else r.get("away_goals")
            ga = r.get("away_goals") if is_home else r.get("home_goals")
            rows.append({
                **common, "team": team, "opponent": opp, "is_home": int(is_home),
                "xg_for": xgf, "xg_against": xga, "shots_for": sf, "shots_against": sa,
                "sot_for": sotf, "sot_against": sota, "corners_for": cf, "corners_against": ca,
                "fouls_for": ff, "fouls_against": fa, "goals_for": gf, "goals_against": ga,
                "ah_cover": home_cover if is_home else (1.0 - home_cover if pd.notna(home_cover) else np.nan),
            })
    d = pd.DataFrame(rows)
    d["date"] = pd.to_datetime(d["date"], errors="coerce").dt.normalize()
    return d.sort_values(["team", "date", "season"]).reset_index(drop=True)


def add_style_profiles(team_matches, window=STYLE_WINDOW, min_style=MIN_STYLE):
    rows = []
    metrics = [
        "xg_for", "xg_against", "shots_for", "shots_against", "sot_for", "sot_against",
        "corners_for", "corners_against", "fouls_for", "fouls_against", "ah_cover",
    ]
    for team, part in team_matches.groupby("team", sort=False):
        hist = []
        for _, r in part.sort_values(["date", "season"]).iterrows():
            prior = hist[-window:]
            rec = r.to_dict()
            rec["style_prior_matches"] = len(prior)
            for m in metrics:
                rec[f"prior_{m}"] = _safe_mean([x[m] for x in prior])
            if len(prior) >= min_style:
                rec["prior_xg_balance"] = rec["prior_xg_for"] - rec["prior_xg_against"]
                rec["prior_attack_volume"] = rec["prior_shots_for"]
                rec["prior_sot_rate"] = rec["prior_sot_for"] / rec["prior_shots_for"] if rec["prior_shots_for"] and pd.notna(rec["prior_shots_for"]) else np.nan
                rec["prior_shot_quality"] = rec["prior_xg_for"] / rec["prior_shots_for"] if rec["prior_shots_for"] and pd.notna(rec["prior_shots_for"]) else np.nan
                rec["prior_tempo_proxy"] = rec["prior_shots_for"] + rec["prior_shots_against"]
            else:
                for c in ("prior_xg_balance", "prior_attack_volume", "prior_sot_rate", "prior_shot_quality", "prior_tempo_proxy"):
                    rec[c] = np.nan
            rows.append(rec)
            hist.append(r.to_dict())
    return pd.DataFrame(rows)


def load_optional_tactics(formation_file=FORMATION_FILE, coach_file=COACH_FILE):
    tactics = None
    if os.path.exists(formation_file):
        f = pd.read_csv(formation_file, encoding="utf-8-sig")
        f["date"] = pd.to_datetime(f["date"], errors="coerce").dt.normalize()
        f = f[["season", "date", "team", "formation"]].drop_duplicates(["season", "date", "team"], keep="last")
        tactics = f
    if os.path.exists(coach_file):
        c = pd.read_csv(coach_file, encoding="utf-8-sig")
        c["date"] = pd.to_datetime(c["date"], errors="coerce").dt.normalize()
        keep = [x for x in ["season", "date", "team", "coach"] if x in c.columns]
        c = c[keep].drop_duplicates(["season", "date", "team"], keep="last")
        tactics = c if tactics is None else tactics.merge(c, on=["season", "date", "team"], how="outer", validate="one_to_one")
    return tactics


def attach_tactics(team_styles, tactics):
    if tactics is None or tactics.empty:
        d = team_styles.copy()
        d["formation"] = ""
        d["coach"] = ""
        return d
    d = team_styles.merge(tactics, on=["season", "date", "team"], how="left", validate="many_to_one")
    for c in ("formation", "coach"):
        if c not in d.columns:
            d[c] = ""
        d[c] = d[c].fillna("").astype(str)
    return d


def _relevance_score(current_home, current_away, prior_home, prior_away):
    score = 0
    basis = []
    for label, cur, old in (
        ("home_coach", current_home.get("coach", ""), prior_home.get("coach", "")),
        ("away_coach", current_away.get("coach", ""), prior_away.get("coach", "")),
        ("home_formation", current_home.get("formation", ""), prior_home.get("formation", "")),
        ("away_formation", current_away.get("formation", ""), prior_away.get("formation", "")),
    ):
        cur = str(cur or "").strip(); old = str(old or "").strip()
        if cur and old and cur == old:
            score += 2 if "coach" in label else 1
            basis.append(label)
    return score, "+".join(basis)


def build_matchup_context(matches, team_styles):
    lookup = {(r["season"], r["date"], r["team"]): r for _, r in team_styles.iterrows()}
    pair_history = {}
    output = []
    for _, m in matches.sort_values(["date", "season", "home_team", "away_team"]).iterrows():
        keyh = (m["season"], m["date"], m["home_team"])
        keya = (m["season"], m["date"], m["away_team"])
        h = lookup.get(keyh, {})
        a = lookup.get(keya, {})
        pair = tuple(sorted([m["home_team"], m["away_team"]]))
        prior_pair = pair_history.get(pair, [])[-H2H_WINDOW:]
        relevant = []
        bases = set()
        for old in prior_pair:
            # Align old team records to today's home/away team, regardless of old venue.
            ph = old["teams"].get(m["home_team"], {})
            pa = old["teams"].get(m["away_team"], {})
            score, basis = _relevance_score(h, a, ph, pa)
            if score >= 2:
                relevant.append(old)
                if basis:
                    bases.add(basis)

        def p(prefix, row, field):
            return row.get(field, np.nan) if isinstance(row, dict) else np.nan

        rec = {
            "season": m["season"], "date": m["date"], "home_team": m["home_team"], "away_team": m["away_team"],
            "home_style_prior_matches": h.get("style_prior_matches", 0),
            "away_style_prior_matches": a.get("style_prior_matches", 0),
            "home_xg_balance": h.get("prior_xg_balance", np.nan),
            "away_xg_balance": a.get("prior_xg_balance", np.nan),
            "matchup_xg_balance_edge_home": h.get("prior_xg_balance", np.nan) - a.get("prior_xg_balance", np.nan) if pd.notna(h.get("prior_xg_balance", np.nan)) and pd.notna(a.get("prior_xg_balance", np.nan)) else np.nan,
            "home_attack_volume": h.get("prior_attack_volume", np.nan), "away_attack_volume": a.get("prior_attack_volume", np.nan),
            "home_sot_rate": h.get("prior_sot_rate", np.nan), "away_sot_rate": a.get("prior_sot_rate", np.nan),
            "home_shot_quality": h.get("prior_shot_quality", np.nan), "away_shot_quality": a.get("prior_shot_quality", np.nan),
            "home_tempo_proxy": h.get("prior_tempo_proxy", np.nan), "away_tempo_proxy": a.get("prior_tempo_proxy", np.nan),
            "home_ah_cover_8": h.get("prior_ah_cover", np.nan), "away_ah_cover_8": a.get("prior_ah_cover", np.nan),
            "home_formation": h.get("formation", ""), "away_formation": a.get("formation", ""),
            "home_coach": h.get("coach", ""), "away_coach": a.get("coach", ""),
            "h2h_prior_count": len(prior_pair), "h2h_relevant_count": len(relevant),
            "h2h_relevance_basis": "|".join(sorted(bases)), "shadow_only": 1,
        }
        if relevant:
            rec["h2h_relevant_xg_diff_home"] = _safe_mean([x["xg_diff_for_team"].get(m["home_team"], np.nan) for x in relevant])
            rec["h2h_relevant_ah_cover_home"] = _safe_mean([x["ah_cover_for_team"].get(m["home_team"], np.nan) for x in relevant])
        else:
            rec["h2h_relevant_xg_diff_home"] = np.nan
            rec["h2h_relevant_ah_cover_home"] = np.nan
        output.append(rec)

        home_cover = _ah_cover_score(m.get("home_goals"), m.get("away_goals"), m.get("open_ah_home_line"))
        pair_history.setdefault(pair, []).append({
            "teams": {m["home_team"]: h, m["away_team"]: a},
            "xg_diff_for_team": {
                m["home_team"]: m.get("home_xg", np.nan) - m.get("away_xg", np.nan),
                m["away_team"]: m.get("away_xg", np.nan) - m.get("home_xg", np.nan),
            },
            "ah_cover_for_team": {
                m["home_team"]: home_cover,
                m["away_team"]: 1.0 - home_cover if pd.notna(home_cover) else np.nan,
            },
        })
    return pd.DataFrame(output)


def build(input_file=CONTEXT_FILE, output_file=OUTPUT_FILE):
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"{input_file} not found. Run historical_context_builder.py first.")
    matches = pd.read_csv(input_file, encoding="utf-8-sig")
    matches["date"] = pd.to_datetime(matches["date"], errors="coerce").dt.normalize()
    team_matches = to_team_matches(matches)
    styles = add_style_profiles(team_matches)
    styles = attach_tactics(styles, load_optional_tactics())
    out = build_matchup_context(matches, styles)
    out.to_csv(output_file, index=False, encoding="utf-8-sig")
    print("Matchup / Style / Relevant H2H Context built")
    print("Fixtures:", len(out))
    print("Style-ready home rows:", int((pd.to_numeric(out["home_style_prior_matches"], errors="coerce") >= MIN_STYLE).sum()))
    print("Style-ready away rows:", int((pd.to_numeric(out["away_style_prior_matches"], errors="coerce") >= MIN_STYLE).sum()))
    print("Relevant H2H rows:", int((out["h2h_relevant_count"] > 0).sum()))
    print("Status: SHADOW ONLY")
    print("Output:", Path(output_file).resolve())
    return out


if __name__ == "__main__":
    build()
