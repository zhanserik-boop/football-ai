import os
import csv
from datetime import datetime, timezone

import numpy as np
import pandas as pd


# ============================================================
# CONFIG
# ============================================================

AH_FILE = "ah_agent_v2_latest.csv"

BTTS_FILE = "btts_live_watch.csv"

OUTPUT_FILE = "master_decisions_live.csv"
HISTORY_FILE = "master_decisions_history.csv"


# ============================================================
# MASTER POLICY V1
# ============================================================

# AH Lineup Shock is currently the primary actionable signal.
#
# BTTS is NOT allowed to create a bet by itself in V1.
#
# Master confidence is deliberately conservative.

MIN_BET_CONFIDENCE = 0.65


# ============================================================
# HELPERS
# ============================================================

def utc_now():
    return datetime.now(
        timezone.utc
    )


def safe_float(value):

    try:

        if pd.isna(value):
            return np.nan

        return float(value)

    except:
        return np.nan


def load_csv(filename):

    if not os.path.exists(filename):
        return pd.DataFrame()

    try:

        if os.path.getsize(filename) == 0:
            return pd.DataFrame()

        return pd.read_csv(filename)

    except pd.errors.EmptyDataError:
        return pd.DataFrame()

    except Exception as e:

        print(
            "ERROR reading",
            filename,
            ":",
            repr(e)
        )

        return pd.DataFrame()


# ============================================================
# PREPARE AH
# ============================================================

def prepare_ah(df):

    if df.empty:
        return df

    d = df.copy()

    if "fixture_id" not in d.columns:
        raise RuntimeError(
            "ah_agent_v2_latest.csv has no fixture_id"
        )

    d["fixture_id"] = (
        d["fixture_id"]
        .astype(str)
    )

    if "decision" in d.columns:

        d["decision"] = (
            d["decision"]
            .astype(str)
            .str.upper()
            .str.strip()
        )

    else:

        d["decision"] = ""


    for col in [
        "shock_diff",
        "abs_shock",
        "current_handicap",
        "current_avg_odds",
        "current_best_odds",
        "line_move_toward_signal",
        "same_line_price_shortening",
        "home_coverage",
        "away_coverage",
        "minutes_to_kickoff",
    ]:

        if col in d.columns:

            d[col] = pd.to_numeric(
                d[col],
                errors="coerce"
            )


    return d


# ============================================================
# PREPARE BTTS OPTIONAL
# ============================================================

def prepare_btts(df):

    if df.empty:
        return df

    d = df.copy()

    if "fixture_id" not in d.columns:
        return pd.DataFrame()

    d["fixture_id"] = (
        d["fixture_id"]
        .astype(str)
    )

    return d


# ============================================================
# DATA QUALITY
# ============================================================

def quality_penalty(value):

    text = str(value).upper()

    if any(
        x in text
        for x in [
            "LOW",
            "POOR",
            "BAD",
            "WEAK",
            "INSUFFICIENT",
        ]
    ):
        return 0.20

    if "MEDIUM" in text:
        return 0.05

    return 0.0


# ============================================================
# AH CONFIDENCE
# ============================================================

def ah_confidence(row):

    decision = str(
        row.get(
            "decision",
            ""
        )
    ).upper()

    shock = safe_float(
        row.get(
            "abs_shock"
        )
    )

    books = safe_float(
        row.get(
            "current_bookmakers"
        )
    )

    quality = row.get(
        "data_quality",
        ""
    )


    # --------------------------------------------------------
    # BASE
    # --------------------------------------------------------

    if decision == "BET":
        score = 0.67

    elif decision == "WATCH":
        score = 0.48

    elif decision == "LATE":
        score = 0.30

    elif decision == "NO BET":
        score = 0.20

    else:
        score = 0.10


    # --------------------------------------------------------
    # SHOCK STRENGTH
    # Keep this deliberately small.
    # Historical ROI by shock bucket was unstable.
    # --------------------------------------------------------

    if pd.notna(shock):

        if shock >= 3.0:
            score += 0.04

        elif shock >= 2.5:
            score += 0.03

        elif shock >= 2.0:
            score += 0.02


    # --------------------------------------------------------
    # MARKET DEPTH
    # --------------------------------------------------------

    if pd.notna(books):

        if books >= 5:
            score += 0.03

        elif books >= 3:
            score += 0.02

        elif books < 2:
            score -= 0.10


    # --------------------------------------------------------
    # QUALITY
    # --------------------------------------------------------

    score -= quality_penalty(
        quality
    )


    return max(
        0.0,
        min(
            score,
            0.95
        )
    )


# ============================================================
# OPTIONAL BTTS CONTEXT
# ============================================================

def get_btts_context(
    fixture_id,
    btts
):

    if btts.empty:

        return {
            "available": False,
            "decision": "",
            "probability": np.nan,
            "edge": np.nan,
            "text": "BTTS module unavailable",
        }


    x = btts[
        btts["fixture_id"]
        ==
        fixture_id
    ]


    if x.empty:

        return {
            "available": False,
            "decision": "",
            "probability": np.nan,
            "edge": np.nan,
            "text": "No BTTS signal",
        }


    r = x.iloc[-1]


    decision = str(
        r.get(
            "decision",
            r.get(
                "signal",
                ""
            )
        )
    ).upper()


    probability = safe_float(
        r.get(
            "model_probability",
            r.get(
                "p_btts",
                np.nan
            )
        )
    )


    edge = safe_float(
        r.get(
            "edge",
            np.nan
        )
    )


    return {
        "available": True,
        "decision": decision,
        "probability": probability,
        "edge": edge,
        "text": (
            f"BTTS {decision}"
            if decision
            else "BTTS data available"
        ),
    }


# ============================================================
# MASTER DECISION
# ============================================================

def make_master_decision(
    ah_row,
    btts_context
):

    ah_decision = str(
        ah_row.get(
            "decision",
            ""
        )
    ).upper()


    confidence = ah_confidence(
        ah_row
    )


    reason = str(
        ah_row.get(
            "reason",
            ""
        )
    )


    # ========================================================
    # NO SIGNAL
    # ========================================================

    if ah_decision in [
        "",
        "NO SIGNAL"
    ]:

        return {
            "master_decision": "PASS",
            "confidence": confidence,
            "primary_market": "",
            "primary_side": "",
            "reason": (
                "No actionable AH lineup signal"
            ),
        }


    # ========================================================
    # NO BET
    # ========================================================

    if ah_decision == "NO BET":

        return {
            "master_decision": "PASS",
            "confidence": confidence,
            "primary_market": "AH",
            "primary_side": str(
                ah_row.get(
                    "signal",
                    ""
                )
            ),
            "reason": (
                "AH Agent blocked trade: "
                + reason
            ),
        }


    # ========================================================
    # LATE
    # ========================================================

    if ah_decision == "LATE":

        return {
            "master_decision": "PASS",
            "confidence": confidence,
            "primary_market": "AH",
            "primary_side": str(
                ah_row.get(
                    "signal",
                    ""
                )
            ),
            "reason": (
                "Lineup edge likely already priced "
                "into AH market"
            ),
        }


    # ========================================================
    # WATCH
    # ========================================================

    if ah_decision == "WATCH":

        return {
            "master_decision": "WATCH",
            "confidence": confidence,
            "primary_market": "AH",
            "primary_side": str(
                ah_row.get(
                    "signal",
                    ""
                )
            ),
            "reason": (
                "AH signal exists but entry conditions "
                "are not yet clean"
            ),
        }


    # ========================================================
    # BET
    # ========================================================

    if ah_decision == "BET":

        if confidence < MIN_BET_CONFIDENCE:

            return {
                "master_decision": "WATCH",
                "confidence": confidence,
                "primary_market": "AH",
                "primary_side": str(
                    ah_row.get(
                        "signal",
                        ""
                    )
                ),
                "reason": (
                    "AH Agent says BET but Master "
                    "confidence is below threshold"
                ),
            }


        # ----------------------------------------------------
        # BTTS NEVER creates or blocks AH bet in V1.
        # It is context only.
        # ----------------------------------------------------

        extra = ""

        if btts_context[
            "available"
        ]:

            extra = (
                " | "
                +
                btts_context[
                    "text"
                ]
            )


        return {
            "master_decision": "BET",
            "confidence": confidence,
            "primary_market": "AH",
            "primary_side": str(
                ah_row.get(
                    "signal",
                    ""
                )
            ),
            "reason": (
                "Confirmed extreme lineup signal "
                "with tradeable AH before full "
                "market repricing"
                +
                extra
            ),
        }


    return {
        "master_decision": "PASS",
        "confidence": confidence,
        "primary_market": "",
        "primary_side": "",
        "reason": "Unknown AH decision",
    }


# ============================================================
# BUILD OUTPUT
# ============================================================

def build_output(
    ah,
    btts
):

    rows = []


    for _, r in ah.iterrows():

        fixture_id = str(
            r[
                "fixture_id"
            ]
        )


        btts_context = (
            get_btts_context(
                fixture_id,
                btts
            )
        )


        master = (
            make_master_decision(
                r,
                btts_context
            )
        )


        rows.append({

            "master_time_utc":
                utc_now().isoformat(),

            "fixture_id":
                fixture_id,

            "kickoff_utc":
                r.get(
                    "kickoff_utc",
                    ""
                ),

            "minutes_to_kickoff":
                safe_float(
                    r.get(
                        "minutes_to_kickoff"
                    )
                ),

            "home_team":
                r.get(
                    "home_team",
                    ""
                ),

            "away_team":
                r.get(
                    "away_team",
                    ""
                ),

            # --------------------------------------------
            # MASTER
            # --------------------------------------------

            "master_decision":
                master[
                    "master_decision"
                ],

            "confidence":
                round(
                    master[
                        "confidence"
                    ],
                    3
                ),

            "primary_market":
                master[
                    "primary_market"
                ],

            "primary_side":
                master[
                    "primary_side"
                ],

            "reason":
                master[
                    "reason"
                ],

            # --------------------------------------------
            # AH
            # --------------------------------------------

            "ah_decision":
                r.get(
                    "decision",
                    ""
                ),

            "ah_signal":
                r.get(
                    "signal",
                    ""
                ),

            "shock_diff":
                safe_float(
                    r.get(
                        "shock_diff"
                    )
                ),

            "abs_shock":
                safe_float(
                    r.get(
                        "abs_shock"
                    )
                ),

            "data_quality":
                r.get(
                    "data_quality",
                    ""
                ),

            "home_coverage":
                safe_float(
                    r.get(
                        "home_coverage"
                    )
                ),

            "away_coverage":
                safe_float(
                    r.get(
                        "away_coverage"
                    )
                ),

            "ah_handicap":
                safe_float(
                    r.get(
                        "current_handicap"
                    )
                ),

            "ah_avg_odds":
                safe_float(
                    r.get(
                        "current_avg_odds"
                    )
                ),

            "ah_best_odds":
                safe_float(
                    r.get(
                        "current_best_odds"
                    )
                ),

            "ah_best_bookmaker":
                r.get(
                    "current_best_bookmaker",
                    ""
                ),

            "ah_bookmakers":
                safe_float(
                    r.get(
                        "current_bookmakers"
                    )
                ),

            "line_move_toward_signal":
                safe_float(
                    r.get(
                        "line_move_toward_signal"
                    )
                ),

            "price_shortening":
                safe_float(
                    r.get(
                        "same_line_price_shortening"
                    )
                ),

            # --------------------------------------------
            # BTTS
            # --------------------------------------------

            "btts_available":
                int(
                    btts_context[
                        "available"
                    ]
                ),

            "btts_decision":
                btts_context[
                    "decision"
                ],

            "btts_probability":
                btts_context[
                    "probability"
                ],

            "btts_edge":
                btts_context[
                    "edge"
                ],
        })


    return pd.DataFrame(
        rows
    )


# ============================================================
# HISTORY
# ============================================================

def update_history(
    current
):

    if current.empty:
        return


    old = load_csv(
        HISTORY_FILE
    )


    if old.empty:

        current.to_csv(
            HISTORY_FILE,
            index=False,
            encoding="utf-8-sig"
        )

        return


    old["fixture_id"] = (
        old["fixture_id"]
        .astype(str)
    )


    new_rows = []


    for _, row in current.iterrows():

        fixture_id = str(
            row[
                "fixture_id"
            ]
        )


        previous = old[
            old["fixture_id"]
            ==
            fixture_id
        ]


        if previous.empty:

            new_rows.append(
                row
            )

            continue


        last = previous.iloc[-1]


        changed_fields = [
            "master_decision",
            "confidence",
            "ah_decision",
            "ah_handicap",
            "ah_avg_odds",
            "line_move_toward_signal",
        ]


        changed = False


        for col in changed_fields:

            old_value = last.get(
                col
            )

            new_value = row.get(
                col
            )


            if (
                pd.isna(old_value)
                and
                pd.isna(new_value)
            ):
                continue


            if str(
                old_value
            ) != str(
                new_value
            ):

                changed = True
                break


        if changed:

            new_rows.append(
                row
            )


    if not new_rows:
        return


    add = pd.DataFrame(
        new_rows
    )


    combined = pd.concat(
        [
            old,
            add
        ],
        ignore_index=True
    )


    combined.to_csv(
        HISTORY_FILE,
        index=False,
        encoding="utf-8-sig"
    )


# ============================================================
# PRINT
# ============================================================

def print_decision(row):

    print()
    print(
        "=" * 74
    )

    print(
        row["home_team"],
        "-",
        row["away_team"]
    )

    print(
        "=" * 74
    )


    print(
        "MASTER:",
        row[
            "master_decision"
        ]
    )


    print(
        "Confidence:",
        f"{row['confidence']:.0%}"
    )


    if row[
        "primary_market"
    ]:

        print(
            "Market:",
            row[
                "primary_market"
            ]
        )


    if row[
        "primary_side"
    ]:

        print(
            "Side:",
            row[
                "primary_side"
            ]
        )


    if pd.notna(
        row[
            "ah_handicap"
        ]
    ):

        print(
            "AH:",
            f"{row['ah_handicap']:+.2f}"
        )


    if pd.notna(
        row[
            "ah_best_odds"
        ]
    ):

        print(
            "Best odds:",
            f"{row['ah_best_odds']:.3f}",
            "@",
            row[
                "ah_best_bookmaker"
            ]
        )


    if pd.notna(
        row[
            "shock_diff"
        ]
    ):

        print(
            "ShockDiff:",
            f"{row['shock_diff']:+.2f}"
        )


    print(
        "AH Agent:",
        row[
            "ah_decision"
        ]
    )


    print(
        "Reason:",
        row[
            "reason"
        ]
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "=" * 74
    )

    print(
        "FOOTBALL AI — MASTER AGENT V1"
    )

    print(
        "=" * 74
    )

    print(
        "Time:",
        utc_now().isoformat()
    )


    ah_raw = load_csv(
        AH_FILE
    )


    if ah_raw.empty:

        print()
        print(
            "No AH Agent decisions yet."
        )

        print(
            "Waiting for:",
            AH_FILE
        )

        print(
            "API REQUESTS USED: 0"
        )

        return


    ah = prepare_ah(
        ah_raw
    )


    btts_raw = load_csv(
        BTTS_FILE
    )


    btts = prepare_btts(
        btts_raw
    )


    current = build_output(
        ah,
        btts
    )


    current = (
        current
        .sort_values(
            [
                "kickoff_utc",
                "fixture_id"
            ]
        )
        .reset_index(
            drop=True
        )
    )


    current.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )


    update_history(
        current
    )


    # --------------------------------------------------------
    # PRINT FUTURE / CURRENT MATCHES
    # --------------------------------------------------------

    for _, row in current.iterrows():

        minutes = row[
            "minutes_to_kickoff"
        ]


        if (
            pd.isna(minutes)
            or
            minutes >= -5
        ):

            print_decision(
                row
            )


    print()
    print(
        "=" * 74
    )

    print(
        "SUMMARY"
    )

    print(
        "=" * 74
    )


    print(
        current[
            "master_decision"
        ]
        .value_counts()
        .to_string()
    )


    bets = current[
        current[
            "master_decision"
        ]
        ==
        "BET"
    ]


    print()

    print(
        "Current BET candidates:",
        len(
            bets
        )
    )


    print(
        "Saved:",
        OUTPUT_FILE
    )


    print(
        "History:",
        HISTORY_FILE
    )


    print(
        "API REQUESTS USED: 0"
    )


if __name__ == "__main__":

    main()