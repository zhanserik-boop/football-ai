from pathlib import Path
import os
import numpy as np
import pandas as pd

ATTACK_FILES = [
    "Database - Attack  Poss - England Premier League - LS-15-25yy.csv",
    "Database - Attack  Poss - England Premier League - CS.csv",
]
CORNERS_FILES = [
    "Database - Corners  Cards - England Premier League - LS-15-25yy.csv",
    "Database - Corners  Cards - England Premier League - CS.csv",
]
OUTPUT_FILE = "epl_extended_style_context.csv"
WINDOW = 8
MIN_HISTORY = 4

ALIASES = {
    "Manchester Utd": "Manchester United",
    "Man Utd": "Manchester United",
    "Nottingham": "Nottingham Forest",
    "Wolves": "Wolverhampton Wanderers",
    "Tottenham Hotspur": "Tottenham",
    "Newcastle": "Newcastle United",
}
KEYS = ["season", "date", "home_team", "away_team"]


def _team(v):
    s = str(v).strip()
    return ALIASES.get(s, s)


def _date(s):
    return pd.to_datetime(s, dayfirst=True, errors="coerce").dt.normalize()


def _season(s):
    return pd.to_numeric(s.astype(str).str.slice(0, 4), errors="coerce").astype("Int64")


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def _load(files, rename, required):
    parts = []
    for filename in files:
        if not os.path.exists(filename):
            continue
        d = pd.read_csv(filename, encoding="utf-8-sig")
        missing = set(required) - set(d.columns)
        if missing:
            raise ValueError(f"{filename}: missing {sorted(missing)}")
        d = d.rename(columns=rename).copy()
        d["season"] = _season(d["season"])
        d["date"] = _date(d["date"])
        d["home_team"] = d["home_team"].map(_team)
        d["away_team"] = d["away_team"].map(_team)
        parts.append(d)
    if not parts:
        raise FileNotFoundError("No extended EPL source files found")
    out = pd.concat(parts, ignore_index=True)
    out = out.dropna(subset=["season", "date", "home_team", "away_team"])
    return out.drop_duplicates(KEYS, keep="last")


def load_match_stats():
    attack_rename = {
        "Season": "season", "matchDate": "date", "homeTeam": "home_team", "awayTeam": "away_team",
        "HBPFT": "home_poss", "ABPFT": "away_poss",
        "HTSFT": "home_shots", "ATSFT": "away_shots",
        "HSONFT": "home_sot", "ASONFT": "away_sot",
        "HSOFFFT": "home_soff", "ASOFFFT": "away_soff",
        "HTS1H": "home_shots_1h", "ATS1H": "away_shots_1h",
        "HTS2H": "home_shots_2h", "ATS2H": "away_shots_2h",
        "HSON1H": "home_sot_1h", "ASON1H": "away_sot_1h",
        "HSON2H": "home_sot_2h", "ASON2H": "away_sot_2h",
    }
    required_attack = list(attack_rename)
    attack = _load(ATTACK_FILES, attack_rename, required_attack)

    corner_rename = {
        "Season": "season", "matchDate": "date", "homeTeam": "home_team", "awayTeam": "away_team",
        "HCFT": "home_corners", "ACFT": "away_corners",
        "HYCFT": "home_yellow", "AYCFT": "away_yellow",
    }
    corners = _load(CORNERS_FILES, corner_rename, list(corner_rename))
    keep_c = KEYS + ["home_corners", "away_corners", "home_yellow", "away_yellow"]
    d = attack.merge(corners[keep_c], on=KEYS, how="left", validate="one_to_one")
    for c in d.columns:
        if c.startswith("home_") or c.startswith("away_"):
            if c not in {"home_team", "away_team"}:
                d[c] = _num(d[c])
    return d.sort_values(["date", "home_team", "away_team"]).reset_index(drop=True)


def to_team_matches(matches):
    rows = []
    metrics = ["poss", "shots", "sot", "soff", "shots_1h", "shots_2h", "sot_1h", "sot_2h", "corners", "yellow"]
    for _, r in matches.iterrows():
        for side in ("home", "away"):
            opp = "away" if side == "home" else "home"
            rec = {
                "season": r["season"], "date": r["date"], "team": r[f"{side}_team"],
                "opponent": r[f"{opp}_team"], "is_home": int(side == "home"),
            }
            for m in metrics:
                rec[f"{m}_for"] = r.get(f"{side}_{m}", np.nan)
                rec[f"{m}_against"] = r.get(f"{opp}_{m}", np.nan)
            rows.append(rec)
    return pd.DataFrame(rows).sort_values(["team", "date", "season"]).reset_index(drop=True)


def add_prior_profiles(team_matches, window=WINDOW):
    rows = []
    base_metrics = [
        "poss_for", "shots_for", "shots_against", "sot_for", "sot_against",
        "shots_1h_for", "shots_2h_for", "sot_1h_for", "sot_2h_for",
        "corners_for", "corners_against", "yellow_for",
    ]
    for team, part in team_matches.groupby("team", sort=False):
        hist = []
        for _, r in part.sort_values(["date", "season"]).iterrows():
            prior = hist[-window:]
            rec = r.to_dict()
            rec["extended_prior_matches"] = len(prior)
            for m in base_metrics:
                vals = pd.to_numeric(pd.Series([x.get(m) for x in prior]), errors="coerce").dropna()
                rec[f"prior_{m}"] = float(vals.mean()) if len(vals) else np.nan
            if len(prior) >= MIN_HISTORY:
                shots = rec["prior_shots_for"]
                sot = rec["prior_sot_for"]
                rec["prior_sot_rate"] = sot / shots if pd.notna(shots) and shots > 0 else np.nan
                rec["prior_shot_dominance"] = rec["prior_shots_for"] - rec["prior_shots_against"]
                rec["prior_sot_dominance"] = rec["prior_sot_for"] - rec["prior_sot_against"]
                total_shots = rec["prior_shots_1h_for"] + rec["prior_shots_2h_for"]
                rec["prior_first_half_shot_share"] = rec["prior_shots_1h_for"] / total_shots if pd.notna(total_shots) and total_shots > 0 else np.nan
                rec["prior_second_half_surge"] = rec["prior_shots_2h_for"] - rec["prior_shots_1h_for"]
            else:
                for c in ["prior_sot_rate", "prior_shot_dominance", "prior_sot_dominance", "prior_first_half_shot_share", "prior_second_half_surge"]:
                    rec[c] = np.nan
            rows.append(rec)
            hist.append(r.to_dict())
    return pd.DataFrame(rows)


def build_fixture_context(matches, profiles):
    lookup = {(r["season"], r["date"], r["team"]): r for _, r in profiles.iterrows()}
    out = []
    for _, m in matches.iterrows():
        h = lookup.get((m["season"], m["date"], m["home_team"]), {})
        a = lookup.get((m["season"], m["date"], m["away_team"]), {})
        def edge(field):
            hv, av = h.get(field, np.nan), a.get(field, np.nan)
            return float(hv) - float(av) if pd.notna(hv) and pd.notna(av) else np.nan
        out.append({
            "season": m["season"], "date": m["date"], "home_team": m["home_team"], "away_team": m["away_team"],
            "home_extended_prior_matches": h.get("extended_prior_matches", 0),
            "away_extended_prior_matches": a.get("extended_prior_matches", 0),
            "possession_edge_home": edge("prior_poss_for"),
            "shot_dominance_edge_home": edge("prior_shot_dominance"),
            "sot_dominance_edge_home": edge("prior_sot_dominance"),
            "sot_rate_edge_home": edge("prior_sot_rate"),
            "corners_edge_home": edge("prior_corners_for"),
            "cards_edge_home": edge("prior_yellow_for"),
            "first_half_shot_share_edge_home": edge("prior_first_half_shot_share"),
            "second_half_surge_edge_home": edge("prior_second_half_surge"),
            "home_prior_possession": h.get("prior_poss_for", np.nan),
            "away_prior_possession": a.get("prior_poss_for", np.nan),
            "home_prior_sot_rate": h.get("prior_sot_rate", np.nan),
            "away_prior_sot_rate": a.get("prior_sot_rate", np.nan),
            "shadow_only": 1,
        })
    return pd.DataFrame(out)


def build(output_file=OUTPUT_FILE):
    matches = load_match_stats()
    team_matches = to_team_matches(matches)
    profiles = add_prior_profiles(team_matches)
    out = build_fixture_context(matches, profiles)
    out.to_csv(output_file, index=False, encoding="utf-8-sig")
    ready = (pd.to_numeric(out["home_extended_prior_matches"], errors="coerce") >= MIN_HISTORY) & (pd.to_numeric(out["away_extended_prior_matches"], errors="coerce") >= MIN_HISTORY)
    print("Extended EPL Style Context built")
    print("Source fixtures:", len(out))
    print("Both teams style-ready:", int(ready.sum()))
    print("Seasons:", ", ".join(str(int(x)) for x in sorted(out["season"].dropna().unique())))
    print("Status: SHADOW ONLY")
    print("Output:", Path(output_file).resolve())
    return out


if __name__ == "__main__":
    build()
