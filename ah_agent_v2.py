import os
import csv
import time
import argparse

from datetime import datetime, timezone

import numpy as np
import pandas as pd


# ============================================================
# CONFIG
# ============================================================

SNAPSHOT_FILE = "market_snapshots_v2.csv"
SIGNAL_FILE = "lineup_signals_live.csv"

LATEST_FILE = "ah_agent_v2_latest.csv"
HISTORY_FILE = "ah_agent_v2_history.csv"


# Historical Lineup Shock threshold
SHOCK_THRESHOLD = 1.50


# ------------------------------------------------------------
# MARKET REACTION RULES
#
# AH is stored from the signal team's perspective.
#
# Example:
#
# HOME +0.75 -> HOME +0.50
# market strengthened HOME
#
# AWAY 0.00 -> AWAY -0.25
# market strengthened AWAY
#
# In both cases:
#
# pre_handicap - current_handicap = +0.25
#
# Therefore positive line_move means market has moved
# IN THE DIRECTION of our lineup signal.
# ------------------------------------------------------------

LATE_LINE_MOVE = 0.25

PRICE_LATE_DROP = 0.10
PRICE_WATCH_DROP = 0.05


# We do not want a consensus based on a single bookmaker
MIN_BOOKMAKERS = 2


# ============================================================
# UTILS
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


def parse_time(value):

    if value is None:
        return pd.NaT

    return pd.to_datetime(
        value,
        utc=True,
        errors="coerce"
    )


def quality_is_low(value):

    text = str(value).strip().upper()

    bad_words = [
        "LOW",
        "POOR",
        "BAD",
        "WEAK",
        "INSUFFICIENT",
    ]

    return any(
        word in text
        for word in bad_words
    )


# ============================================================
# LOAD CSV SAFELY
# ============================================================

def load_csv(filename):

    if not os.path.exists(filename):
        return pd.DataFrame()

    try:

        if os.path.getsize(filename) == 0:
            return pd.DataFrame()

        return pd.read_csv(
            filename
        )

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
# PREPARE SNAPSHOTS
# ============================================================

def prepare_snapshots(df):

    if df.empty:
        return df

    required = [
        "snapshot_utc",
        "fixture_id",
        "parsed_side",
        "parsed_handicap",
        "odd",
    ]

    missing = [
        c for c in required
        if c not in df.columns
    ]

    if missing:

        raise RuntimeError(
            "Snapshot file missing columns: "
            + str(missing)
        )

    d = df.copy()

    d["fixture_id"] = (
        d["fixture_id"]
        .astype(str)
    )

    d["snapshot_dt"] = pd.to_datetime(
        d["snapshot_utc"],
        utc=True,
        errors="coerce"
    )

    d["parsed_side"] = (
        d["parsed_side"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    d["parsed_handicap"] = pd.to_numeric(
        d["parsed_handicap"],
        errors="coerce"
    )

    d["odd"] = pd.to_numeric(
        d["odd"],
        errors="coerce"
    )

    d = d.dropna(
        subset=[
            "snapshot_dt",
            "parsed_handicap",
            "odd",
        ]
    )

    d = d[
        d["parsed_side"].isin(
            ["HOME", "AWAY"]
        )
    ]

    d = d[
        d["odd"] > 1.0
    ]

    return d


# ============================================================
# PREPARE SIGNALS
# ============================================================

def prepare_signals(df):

    if df.empty:
        return df

    required = [
        "signal_time_utc",
        "fixture_id",
        "kickoff_utc",
        "home_team",
        "away_team",
        "shock_diff",
        "abs_shock",
        "signal",
        "data_quality",
        "entry_handicap",
        "entry_avg_odds",
        "entry_best_odds",
    ]

    missing = [
        c for c in required
        if c not in df.columns
    ]

    if missing:

        raise RuntimeError(
            "Signal file missing columns: "
            + str(missing)
        )

    d = df.copy()

    d["fixture_id"] = (
        d["fixture_id"]
        .astype(str)
    )

    d["signal_time_dt"] = pd.to_datetime(
        d["signal_time_utc"],
        utc=True,
        errors="coerce"
    )

    d["kickoff_dt"] = pd.to_datetime(
        d["kickoff_utc"],
        utc=True,
        errors="coerce"
    )

    if "entry_time_utc" in d.columns:

        d["entry_time_dt"] = pd.to_datetime(
            d["entry_time_utc"],
            utc=True,
            errors="coerce"
        )

    else:
        d["entry_time_dt"] = pd.NaT

    for col in [
        "shock_diff",
        "abs_shock",
        "entry_handicap",
        "entry_avg_odds",
        "entry_best_odds",
        "home_coverage",
        "away_coverage",
    ]:

        if col in d.columns:

            d[col] = pd.to_numeric(
                d[col],
                errors="coerce"
            )

    d["signal"] = (
        d["signal"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    return d


# ============================================================
# CONSENSUS MARKET FOR ONE SNAPSHOT
# ============================================================

def consensus_from_rows(rows, signal):

    if rows.empty:
        return None

    x = rows[
        rows["parsed_side"]
        ==
        signal
    ].copy()

    if x.empty:
        return None

    x = x.dropna(
        subset=[
            "parsed_handicap",
            "odd"
        ]
    )

    if x.empty:
        return None

    # Number of bookmakers on each exact AH line
    counts = (
        x.groupby(
            "parsed_handicap"
        )
        .size()
        .sort_values(
            ascending=False
        )
    )

    if len(counts) == 0:
        return None

    max_count = counts.iloc[0]

    candidate_lines = (
        counts[
            counts == max_count
        ]
        .index
        .tolist()
    )

    # Tie-break:
    # choose the line closest to the median market line
    median_line = (
        x["parsed_handicap"]
        .median()
    )

    consensus_line = min(
        candidate_lines,
        key=lambda line:
            abs(
                line
                -
                median_line
            )
    )

    same = x[
        x["parsed_handicap"]
        ==
        consensus_line
    ].copy()

    if same.empty:
        return None

    avg_odds = (
        same["odd"]
        .mean()
    )

    best_odds = (
        same["odd"]
        .max()
    )

    best_bookmaker = ""

    if "bookmaker" in same.columns:

        best_row = same.loc[
            same["odd"].idxmax()
        ]

        best_bookmaker = str(
            best_row.get(
                "bookmaker",
                ""
            )
        )

    snapshot_time = (
        same["snapshot_dt"]
        .max()
    )

    return {
        "snapshot_time":
            snapshot_time,

        "handicap":
            float(
                consensus_line
            ),

        "average_odds":
            float(
                avg_odds
            ),

        "best_odds":
            float(
                best_odds
            ),

        "best_bookmaker":
            best_bookmaker,

        "bookmakers":
            int(
                len(same)
            ),
    }


# ============================================================
# GET ONE EXACT SNAPSHOT TIME
# ============================================================

def consensus_at_time(
    fixture_snapshots,
    snapshot_time,
    signal
):

    x = fixture_snapshots[
        fixture_snapshots["snapshot_dt"]
        ==
        snapshot_time
    ]

    return consensus_from_rows(
        x,
        signal
    )


# ============================================================
# LAST PRE-LINEUP MARKET
# ============================================================

def get_pre_lineup_market(
    fixture_snapshots,
    signal_time,
    signal
):

    if fixture_snapshots.empty:
        return None

    before = fixture_snapshots[
        fixture_snapshots["snapshot_dt"]
        <
        signal_time
    ].copy()

    if before.empty:
        return None

    # Work backwards until we find a valid consensus
    times = (
        before["snapshot_dt"]
        .dropna()
        .drop_duplicates()
        .sort_values(
            ascending=False
        )
    )

    for ts in times:

        market = consensus_at_time(
            before,
            ts,
            signal
        )

        if market:
            return market

    return None


# ============================================================
# LATEST POST-LINEUP MARKET
# ============================================================

def get_latest_post_market(
    fixture_snapshots,
    signal_time,
    signal
):

    if fixture_snapshots.empty:
        return None

    after = fixture_snapshots[
        fixture_snapshots["snapshot_dt"]
        >=
        signal_time
    ].copy()

    if after.empty:
        return None

    times = (
        after["snapshot_dt"]
        .dropna()
        .drop_duplicates()
        .sort_values(
            ascending=False
        )
    )

    for ts in times:

        market = consensus_at_time(
            after,
            ts,
            signal
        )

        if market:
            return market

    return None


# ============================================================
# FALLBACK TO ENTRY PRICE
# ============================================================

def entry_market_from_signal(row):

    handicap = safe_float(
        row.get(
            "entry_handicap"
        )
    )

    avg_odds = safe_float(
        row.get(
            "entry_avg_odds"
        )
    )

    best_odds = safe_float(
        row.get(
            "entry_best_odds"
        )
    )

    if (
        np.isnan(handicap)
        or
        np.isnan(avg_odds)
    ):
        return None

    ts = row.get(
        "entry_time_dt"
    )

    bookmakers = row.get(
        "bookmakers_on_entry_line",
        0
    )

    try:
        bookmakers = int(
            float(bookmakers)
        )
    except:
        bookmakers = 0

    return {
        "snapshot_time":
            ts,

        "handicap":
            handicap,

        "average_odds":
            avg_odds,

        "best_odds":
            best_odds,

        "best_bookmaker":
            str(
                row.get(
                    "entry_best_bookmaker",
                    ""
                )
            ),

        "bookmakers":
            bookmakers,
    }


# ============================================================
# DECISION ENGINE
# ============================================================

def decide(
    row,
    pre_market,
    current_market
):

    signal = str(
        row["signal"]
    ).upper()

    shock_diff = safe_float(
        row["shock_diff"]
    )

    abs_shock = safe_float(
        row["abs_shock"]
    )

    quality = str(
        row.get(
            "data_quality",
            ""
        )
    )


    # --------------------------------------------------------
    # BASIC SIGNAL CHECK
    # --------------------------------------------------------

    if signal not in [
        "HOME",
        "AWAY"
    ]:

        return {
            "decision": "NO SIGNAL",
            "reason": "Lineup engine produced no HOME/AWAY signal",
        }


    if (
        np.isnan(abs_shock)
        or
        abs_shock < SHOCK_THRESHOLD
    ):

        return {
            "decision": "NO SIGNAL",
            "reason": (
                f"|ShockDiff| below "
                f"{SHOCK_THRESHOLD:.2f}"
            ),
        }


    # --------------------------------------------------------
    # DATA QUALITY
    # --------------------------------------------------------

    if quality_is_low(
        quality
    ):

        return {
            "decision": "NO BET",
            "reason": (
                "Lineup data quality is LOW/POOR"
            ),
        }


    # --------------------------------------------------------
    # NEED PRE-LINEUP MARKET
    # --------------------------------------------------------

    if pre_market is None:

        return {
            "decision": "WATCH",
            "reason": (
                "No valid pre-lineup AH baseline. "
                "Cannot measure market reaction."
            ),
        }


    # --------------------------------------------------------
    # NEED CURRENT MARKET
    # --------------------------------------------------------

    if current_market is None:

        return {
            "decision": "WATCH",
            "reason": (
                "Signal exists but no current "
                "tradeable AH market."
            ),
        }


    pre_line = pre_market[
        "handicap"
    ]

    current_line = current_market[
        "handicap"
    ]

    pre_avg = pre_market[
        "average_odds"
    ]

    current_avg = current_market[
        "average_odds"
    ]


    # ========================================================
    # MARKET MOVEMENT
    #
    # Positive:
    # market strengthened signal team
    #
    # e.g.
    #
    # HOME +0.75 -> +0.50
    # 0.75 - 0.50 = +0.25
    #
    # AWAY 0 -> -0.25
    # 0 - (-0.25) = +0.25
    # ========================================================

    line_move = (
        pre_line
        -
        current_line
    )


    same_line = (
        abs(
            current_line
            -
            pre_line
        )
        <
        0.001
    )


    price_shortening = np.nan

    if same_line:

        price_shortening = (
            pre_avg
            -
            current_avg
        )


    # --------------------------------------------------------
    # CONSENSUS QUALITY
    # --------------------------------------------------------

    if (
        current_market["bookmakers"]
        <
        MIN_BOOKMAKERS
    ):

        return {
            "decision": "WATCH",

            "reason": (
                "Current AH consensus has fewer than "
                f"{MIN_BOOKMAKERS} bookmakers"
            ),

            "line_move":
                line_move,

            "price_shortening":
                price_shortening,
        }


    # --------------------------------------------------------
    # LINE ALREADY MOVED 0.25+ TOWARD SIGNAL
    # --------------------------------------------------------

    if line_move >= LATE_LINE_MOVE - 0.001:

        return {
            "decision": "LATE",

            "reason": (
                "AH line already moved "
                f"{line_move:+.2f} toward signal"
            ),

            "line_move":
                line_move,

            "price_shortening":
                price_shortening,
        }


    # --------------------------------------------------------
    # MARKET MOVED AGAINST SIGNAL
    #
    # We do NOT automatically bet this.
    # It may be better price, but market is disagreeing.
    # No historical proof that this subset is profitable.
    # --------------------------------------------------------

    if line_move <= -0.249:

        return {
            "decision": "WATCH",

            "reason": (
                "AH market moved against lineup signal "
                f"({line_move:+.2f}). "
                "Do not auto-bet market disagreement."
            ),

            "line_move":
                line_move,

            "price_shortening":
                price_shortening,
        }


    # --------------------------------------------------------
    # SAME AH LINE — CHECK ODDS REACTION
    # --------------------------------------------------------

    if same_line:

        if (
            not np.isnan(
                price_shortening
            )
            and
            price_shortening
            >= PRICE_LATE_DROP
        ):

            return {
                "decision": "LATE",

                "reason": (
                    "Same AH line but average odds "
                    f"shortened by {price_shortening:.3f}"
                ),

                "line_move":
                    line_move,

                "price_shortening":
                    price_shortening,
            }


        if (
            not np.isnan(
                price_shortening
            )
            and
            price_shortening
            >= PRICE_WATCH_DROP
        ):

            return {
                "decision": "WATCH",

                "reason": (
                    "AH line unchanged but price "
                    f"already shortened by "
                    f"{price_shortening:.3f}"
                ),

                "line_move":
                    line_move,

                "price_shortening":
                    price_shortening,
            }


        # ----------------------------------------------------
        # BEST LIVE ENTRY CASE
        #
        # Extreme lineup signal exists,
        # confirmed lineup quality acceptable,
        # but market has not yet materially repriced it.
        # ----------------------------------------------------

        return {
            "decision": "BET",

            "reason": (
                "Extreme lineup signal confirmed; "
                "AH line has not materially moved yet"
            ),

            "line_move":
                line_move,

            "price_shortening":
                price_shortening,
        }


    # --------------------------------------------------------
    # SMALL LINE CHANGE BELOW 0.25
    # --------------------------------------------------------

    if 0 < line_move < LATE_LINE_MOVE:

        return {
            "decision": "WATCH",

            "reason": (
                "Market has started moving toward signal "
                f"({line_move:+.2f})"
            ),

            "line_move":
                line_move,

            "price_shortening":
                price_shortening,
        }


    # --------------------------------------------------------
    # SMALL MARKET MOVE AGAINST SIGNAL
    # --------------------------------------------------------

    if line_move < 0:

        return {
            "decision": "WATCH",

            "reason": (
                "Small market disagreement with signal "
                f"({line_move:+.2f})"
            ),

            "line_move":
                line_move,

            "price_shortening":
                price_shortening,
        }


    return {
        "decision": "WATCH",

        "reason": (
            "No clean entry classification"
        ),

        "line_move":
            line_move,

        "price_shortening":
            price_shortening,
    }


# ============================================================
# ONE SIGNAL
# ============================================================

def evaluate_signal(
    signal_row,
    snapshots
):

    fixture_id = str(
        signal_row[
            "fixture_id"
        ]
    )

    signal = str(
        signal_row[
            "signal"
        ]
    ).upper()

    signal_time = signal_row[
        "signal_time_dt"
    ]

    kickoff = signal_row[
        "kickoff_dt"
    ]


    fixture_snapshots = snapshots[
        snapshots["fixture_id"]
        ==
        fixture_id
    ].copy()


    pre_market = (
        get_pre_lineup_market(
            fixture_snapshots,
            signal_time,
            signal
        )
    )


    current_market = (
        get_latest_post_market(
            fixture_snapshots,
            signal_time,
            signal
        )
    )


    # If monitor has captured entry but current snapshot
    # cannot be reconstructed, use its first tradeable quote.
    if current_market is None:

        current_market = (
            entry_market_from_signal(
                signal_row
            )
        )


    result = decide(
        signal_row,
        pre_market,
        current_market
    )


    now = pd.Timestamp.now(
        tz="UTC"
    )

    minutes_to_kickoff = np.nan

    if pd.notna(kickoff):

        minutes_to_kickoff = (
            kickoff
            -
            now
        ).total_seconds() / 60.0


    output = {

        "decision_time_utc":
            now.isoformat(),

        "fixture_id":
            fixture_id,

        "kickoff_utc":
            signal_row.get(
                "kickoff_utc",
                ""
            ),

        "minutes_to_kickoff":
            round(
                minutes_to_kickoff,
                2
            )
            if not np.isnan(
                minutes_to_kickoff
            )
            else np.nan,

        "home_team":
            signal_row.get(
                "home_team",
                ""
            ),

        "away_team":
            signal_row.get(
                "away_team",
                ""
            ),

        "signal_time_utc":
            signal_row.get(
                "signal_time_utc",
                ""
            ),

        "signal":
            signal,

        "shock_diff":
            safe_float(
                signal_row.get(
                    "shock_diff"
                )
            ),

        "abs_shock":
            safe_float(
                signal_row.get(
                    "abs_shock"
                )
            ),

        "data_quality":
            signal_row.get(
                "data_quality",
                ""
            ),

        "home_coverage":
            safe_float(
                signal_row.get(
                    "home_coverage"
                )
            ),

        "away_coverage":
            safe_float(
                signal_row.get(
                    "away_coverage"
                )
            ),

        "pre_snapshot_utc":
            (
                pre_market[
                    "snapshot_time"
                ].isoformat()
                if pre_market
                and
                pd.notna(
                    pre_market[
                        "snapshot_time"
                    ]
                )
                else ""
            ),

        "pre_handicap":
            (
                pre_market[
                    "handicap"
                ]
                if pre_market
                else np.nan
            ),

        "pre_avg_odds":
            (
                pre_market[
                    "average_odds"
                ]
                if pre_market
                else np.nan
            ),

        "pre_best_odds":
            (
                pre_market[
                    "best_odds"
                ]
                if pre_market
                else np.nan
            ),

        "pre_bookmakers":
            (
                pre_market[
                    "bookmakers"
                ]
                if pre_market
                else 0
            ),

        "current_snapshot_utc":
            (
                current_market[
                    "snapshot_time"
                ].isoformat()
                if current_market
                and
                pd.notna(
                    current_market[
                        "snapshot_time"
                    ]
                )
                else ""
            ),

        "current_handicap":
            (
                current_market[
                    "handicap"
                ]
                if current_market
                else np.nan
            ),

        "current_avg_odds":
            (
                current_market[
                    "average_odds"
                ]
                if current_market
                else np.nan
            ),

        "current_best_odds":
            (
                current_market[
                    "best_odds"
                ]
                if current_market
                else np.nan
            ),

        "current_best_bookmaker":
            (
                current_market[
                    "best_bookmaker"
                ]
                if current_market
                else ""
            ),

        "current_bookmakers":
            (
                current_market[
                    "bookmakers"
                ]
                if current_market
                else 0
            ),

        "line_move_toward_signal":
            result.get(
                "line_move",
                np.nan
            ),

        "same_line_price_shortening":
            result.get(
                "price_shortening",
                np.nan
            ),

        "decision":
            result[
                "decision"
            ],

        "reason":
            result[
                "reason"
            ],
    }

    return output


# ============================================================
# PRINT SIGNAL
# ============================================================

def print_decision(row):

    print()
    print(
        "============================================================"
    )

    print(
        row["home_team"],
        "-",
        row["away_team"]
    )

    print(
        "============================================================"
    )

    print(
        "Signal:",
        row["signal"]
    )

    print(
        "ShockDiff:",
        (
            f"{row['shock_diff']:+.2f}"
            if pd.notna(
                row["shock_diff"]
            )
            else "N/A"
        )
    )

    print(
        "Quality:",
        row["data_quality"]
    )

    print(
        "T-:",
        (
            f"{row['minutes_to_kickoff']:.1f} min"
            if pd.notna(
                row["minutes_to_kickoff"]
            )
            else "N/A"
        )
    )

    print()

    print(
        "PRE-LINEUP AH:",
        (
            f"{row['pre_handicap']:+.2f}"
            if pd.notna(
                row["pre_handicap"]
            )
            else "N/A"
        )
    )

    print(
        "PRE avg odds:",
        (
            f"{row['pre_avg_odds']:.3f}"
            if pd.notna(
                row["pre_avg_odds"]
            )
            else "N/A"
        )
    )

    print()

    print(
        "CURRENT AH:",
        (
            f"{row['current_handicap']:+.2f}"
            if pd.notna(
                row["current_handicap"]
            )
            else "N/A"
        )
    )

    print(
        "CURRENT avg odds:",
        (
            f"{row['current_avg_odds']:.3f}"
            if pd.notna(
                row["current_avg_odds"]
            )
            else "N/A"
        )
    )

    print(
        "BEST:",
        (
            f"{row['current_best_odds']:.3f}"
            if pd.notna(
                row["current_best_odds"]
            )
            else "N/A"
        ),
        "@",
        row[
            "current_best_bookmaker"
        ]
    )

    print()

    print(
        "AH movement toward signal:",
        (
            f"{row['line_move_toward_signal']:+.2f}"
            if pd.notna(
                row[
                    "line_move_toward_signal"
                ]
            )
            else "N/A"
        )
    )

    print()

    print(
        ">>>",
        row["decision"],
        "<<<"
    )

    print(
        row["reason"]
    )


# ============================================================
# HISTORY
# ============================================================

def update_history(latest):

    if latest.empty:
        return

    old = load_csv(
        HISTORY_FILE
    )

    if old.empty:

        latest.to_csv(
            HISTORY_FILE,
            index=False,
            encoding="utf-8-sig"
        )

        return


    # Store only when the actual decision or market state changes.

    compare_cols = [
        "fixture_id",
        "decision",
        "current_handicap",
        "current_avg_odds",
        "line_move_toward_signal",
    ]


    new_rows = []

    for _, row in latest.iterrows():

        fixture_id = str(
            row["fixture_id"]
        )

        previous = old[
            old["fixture_id"]
            .astype(str)
            ==
            fixture_id
        ]

        if previous.empty:

            new_rows.append(
                row
            )

            continue


        last = previous.iloc[-1]


        changed = False

        for col in compare_cols[1:]:

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
# ONE RUN
# ============================================================

def run_once():

    print()
    print(
        "============================================================"
    )

    print(
        "FOOTBALL AI — AH AGENT V2"
    )

    print(
        "============================================================"
    )

    print(
        "Time:",
        utc_now().isoformat()
    )


    signals_raw = load_csv(
        SIGNAL_FILE
    )

    snapshots_raw = load_csv(
        SNAPSHOT_FILE
    )


    if signals_raw.empty:

        print()
        print(
            "No confirmed lineup signals yet."
        )

        print(
            "Waiting for:",
            SIGNAL_FILE
        )

        return


    signals = prepare_signals(
        signals_raw
    )

    snapshots = prepare_snapshots(
        snapshots_raw
    )


    if signals.empty:

        print(
            "No valid signal rows."
        )

        return


    # Keep one signal entry per fixture.
    # market_monitor_v2 already captures first entry only.

    signals = (
        signals
        .sort_values(
            "signal_time_dt"
        )
        .drop_duplicates(
            subset=[
                "fixture_id"
            ],
            keep="first"
        )
    )


    outputs = []


    for _, row in signals.iterrows():

        result = evaluate_signal(
            row,
            snapshots
        )

        outputs.append(
            result
        )


    latest = pd.DataFrame(
        outputs
    )


    latest = latest.sort_values(
        [
            "kickoff_utc",
            "fixture_id"
        ]
    )


    latest.to_csv(
        LATEST_FILE,
        index=False,
        encoding="utf-8-sig"
    )


    update_history(
        latest
    )


    # --------------------------------------------------------
    # PRINT ONLY FUTURE / VERY RECENT FIXTURES
    # --------------------------------------------------------

    for _, row in latest.iterrows():

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
        "============================================================"
    )

    print(
        "SUMMARY"
    )

    print(
        "============================================================"
    )

    print(
        latest[
            "decision"
        ]
        .value_counts()
        .to_string()
    )


    print()
    print(
        "Saved:",
        LATEST_FILE
    )

    print(
        "History:",
        HISTORY_FILE
    )

    print(
        "API REQUESTS USED: 0"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Continuously evaluate live signals "
            "without using API requests"
        )
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help=(
            "Seconds between checks in --watch mode"
        )
    )

    args = parser.parse_args()


    if not args.watch:

        run_once()
        return


    print()
    print(
        "AH Agent V2 WATCH MODE"
    )

    print(
        "Interval:",
        args.interval,
        "seconds"
    )

    print(
        "API requests: 0"
    )

    print(
        "Press CTRL+C to stop."
    )


    while True:

        try:

            run_once()

            time.sleep(
                max(
                    args.interval,
                    10
                )
            )


        except KeyboardInterrupt:

            print(
                "\nAH Agent V2 stopped."
            )

            break


        except Exception as e:

            print(
                "AH AGENT ERROR:",
                repr(e)
            )

            time.sleep(
                30
            )


if __name__ == "__main__":
    main()