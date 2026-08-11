import os
import time
import argparse
from datetime import datetime, timezone

import numpy as np
import pandas as pd


# ============================================================
# FILES
# ============================================================

# AH / MASTER
AH_FILE = "ah_agent_v2_latest.csv"
MASTER_FILE = "master_decisions_live.csv"
SIGNAL_FILE = "lineup_signals_live.csv"
CLV_FILE = "post_lineup_clv_report.csv"
RESULT_FILE = "fixture_results_live.csv"

# BTTS SHADOW
BTTS_FILE = "btts_live_watch.csv"


# ============================================================
# HELPERS
# ============================================================

def utc_now():

    return datetime.now(
        timezone.utc
    )


def load_csv(filename):

    if not os.path.exists(
        filename
    ):
        return pd.DataFrame()

    try:

        if os.path.getsize(
            filename
        ) == 0:
            return pd.DataFrame()

        return pd.read_csv(
            filename
        )

    except pd.errors.EmptyDataError:
        return pd.DataFrame()

    except Exception as exc:

        print(
            "ERROR loading",
            filename,
            ":",
            repr(exc)
        )

        return pd.DataFrame()


def safe_float(value):

    try:

        if pd.isna(
            value
        ):
            return np.nan

        return float(
            value
        )

    except Exception:
        return np.nan


def fmt_num(
    value,
    digits=2
):

    value = safe_float(
        value
    )

    if pd.isna(
        value
    ):
        return "-"

    return f"{value:.{digits}f}"


def fmt_signed(
    value,
    digits=2
):

    value = safe_float(
        value
    )

    if pd.isna(
        value
    ):
        return "-"

    return f"{value:+.{digits}f}"


def fmt_pct(
    value,
    digits=1
):

    value = safe_float(
        value
    )

    if pd.isna(
        value
    ):
        return "-"

    return f"{value:.{digits}%}"


def fmt_odds(
    value
):

    value = safe_float(
        value
    )

    if pd.isna(
        value
    ):
        return "-"

    return f"{value:.3f}"


def clean_text(
    value,
    default="-"
):

    if value is None:
        return default

    try:

        if pd.isna(
            value
        ):
            return default

    except Exception:
        pass

    text = str(
        value
    ).strip()

    if not text:
        return default

    return text


# ============================================================
# AH / MASTER FIXTURE BASE
# ============================================================

def build_ah_base(
    master,
    ah,
    signals,
    clv,
    results
):

    fixture_ids = set()

    for df in [
        master,
        ah,
        signals,
        clv,
        results,
    ]:

        if (
            not df.empty
            and
            "fixture_id" in df.columns
        ):

            fixture_ids.update(
                df[
                    "fixture_id"
                ]
                .astype(str)
                .tolist()
            )

    rows = []

    for fixture_id in sorted(
        fixture_ids
    ):

        row = {
            "fixture_id":
                fixture_id
        }

        # ====================================================
        # MASTER
        # ====================================================

        if (
            not master.empty
            and
            "fixture_id" in master.columns
        ):

            x = master[
                master[
                    "fixture_id"
                ].astype(str)
                ==
                fixture_id
            ]

            if not x.empty:

                r = x.iloc[-1]

                row.update({

                    "kickoff":
                        r.get(
                            "kickoff_utc",
                            ""
                        ),

                    "home":
                        r.get(
                            "home_team",
                            ""
                        ),

                    "away":
                        r.get(
                            "away_team",
                            ""
                        ),

                    "minutes":
                        r.get(
                            "minutes_to_kickoff",
                            np.nan
                        ),

                    "master":
                        r.get(
                            "master_decision",
                            ""
                        ),

                    "confidence":
                        r.get(
                            "confidence",
                            np.nan
                        ),

                    "side":
                        r.get(
                            "primary_side",
                            ""
                        ),

                    "ah":
                        r.get(
                            "ah_handicap",
                            np.nan
                        ),

                    "odds":
                        r.get(
                            "ah_best_odds",
                            np.nan
                        ),

                    "shock":
                        r.get(
                            "shock_diff",
                            np.nan
                        ),

                    "ah_agent":
                        r.get(
                            "ah_decision",
                            ""
                        ),

                    "line_move":
                        r.get(
                            "line_move_toward_signal",
                            np.nan
                        ),

                    "quality":
                        r.get(
                            "data_quality",
                            ""
                        ),
                })

        # ====================================================
        # AH FALLBACK
        # ====================================================

        if (
            not ah.empty
            and
            "fixture_id" in ah.columns
        ):

            x = ah[
                ah[
                    "fixture_id"
                ].astype(str)
                ==
                fixture_id
            ]

            if not x.empty:

                r = x.iloc[-1]

                row.setdefault(
                    "kickoff",
                    r.get(
                        "kickoff_utc",
                        ""
                    )
                )

                row.setdefault(
                    "home",
                    r.get(
                        "home_team",
                        ""
                    )
                )

                row.setdefault(
                    "away",
                    r.get(
                        "away_team",
                        ""
                    )
                )

                row.setdefault(
                    "minutes",
                    r.get(
                        "minutes_to_kickoff",
                        np.nan
                    )
                )

                row.setdefault(
                    "shock",
                    r.get(
                        "shock_diff",
                        np.nan
                    )
                )

                row.setdefault(
                    "side",
                    r.get(
                        "signal",
                        ""
                    )
                )

                row.setdefault(
                    "ah",
                    r.get(
                        "current_handicap",
                        np.nan
                    )
                )

                row.setdefault(
                    "odds",
                    r.get(
                        "current_best_odds",
                        np.nan
                    )
                )

                row.setdefault(
                    "ah_agent",
                    r.get(
                        "decision",
                        ""
                    )
                )

                row.setdefault(
                    "line_move",
                    r.get(
                        "line_move_toward_signal",
                        np.nan
                    )
                )

                row.setdefault(
                    "quality",
                    r.get(
                        "data_quality",
                        ""
                    )
                )

        # ====================================================
        # LINEUP SIGNAL
        # ====================================================

        if (
            not signals.empty
            and
            "fixture_id" in signals.columns
        ):

            x = signals[
                signals[
                    "fixture_id"
                ].astype(str)
                ==
                fixture_id
            ]

            if not x.empty:

                r = x.iloc[-1]

                row.setdefault(
                    "kickoff",
                    r.get(
                        "kickoff_utc",
                        ""
                    )
                )

                row.setdefault(
                    "home",
                    r.get(
                        "home_team",
                        ""
                    )
                )

                row.setdefault(
                    "away",
                    r.get(
                        "away_team",
                        ""
                    )
                )

                row.setdefault(
                    "shock",
                    r.get(
                        "shock_diff",
                        np.nan
                    )
                )

                row.setdefault(
                    "side",
                    r.get(
                        "signal",
                        ""
                    )
                )

                row[
                    "lineups"
                ] = "YES"

        # ====================================================
        # CLV / SETTLEMENT
        # ====================================================

        if (
            not clv.empty
            and
            "fixture_id" in clv.columns
        ):

            x = clv[
                clv[
                    "fixture_id"
                ].astype(str)
                ==
                fixture_id
            ]

            if not x.empty:

                r = x.iloc[-1]

                row[
                    "clv"
                ] = r.get(
                    "line_clv",
                    np.nan
                )

                row[
                    "profit"
                ] = r.get(
                    "profit",
                    np.nan
                )

                row[
                    "result_status"
                ] = r.get(
                    "match_status",
                    ""
                )

                hg = r.get(
                    "home_goals",
                    np.nan
                )

                ag = r.get(
                    "away_goals",
                    np.nan
                )

                if (
                    pd.notna(
                        hg
                    )
                    and
                    pd.notna(
                        ag
                    )
                ):

                    row[
                        "result"
                    ] = (
                        f"{int(float(hg))}-"
                        f"{int(float(ag))}"
                    )

        # ====================================================
        # RESULT CACHE FALLBACK
        # ====================================================

        if (
            not results.empty
            and
            "fixture_id" in results.columns
        ):

            x = results[
                results[
                    "fixture_id"
                ].astype(str)
                ==
                fixture_id
            ]

            if not x.empty:

                r = x.iloc[-1]

                row.setdefault(
                    "result_status",
                    r.get(
                        "status",
                        ""
                    )
                )

                hg = r.get(
                    "home_goals",
                    np.nan
                )

                ag = r.get(
                    "away_goals",
                    np.nan
                )

                if (
                    "result" not in row
                    and
                    pd.notna(
                        hg
                    )
                    and
                    pd.notna(
                        ag
                    )
                ):

                    row[
                        "result"
                    ] = (
                        f"{int(float(hg))}-"
                        f"{int(float(ag))}"
                    )

        row.setdefault(
            "lineups",
            "NO"
        )

        row.setdefault(
            "master",
            "-"
        )

        row.setdefault(
            "confidence",
            np.nan
        )

        row.setdefault(
            "side",
            "-"
        )

        row.setdefault(
            "ah_agent",
            "-"
        )

        row.setdefault(
            "clv",
            np.nan
        )

        row.setdefault(
            "profit",
            np.nan
        )

        row.setdefault(
            "result",
            "-"
        )

        row.setdefault(
            "quality",
            "-"
        )

        rows.append(
            row
        )

    return pd.DataFrame(
        rows
    )


# ============================================================
# AH DISPLAY
# ============================================================

def show_ah_section(
    data
):

    print()
    print(
        "=" * 132
    )

    print(
        "AH / LINEUP SHOCK / MASTER"
    )

    print(
        "=" * 132
    )

    if data.empty:

        print()
        print(
            "No AH / lineup signals yet."
        )

        return

    if "kickoff" in data.columns:

        data = data.copy()

        data[
            "_kickoff"
        ] = pd.to_datetime(
            data[
                "kickoff"
            ],
            utc=True,
            errors="coerce"
        )

        data = data.sort_values(
            "_kickoff"
        )

    header = (
        f"{'MATCH':34}"
        f"{'T-MIN':>7}"
        f"{'XI':>5}"
        f"{'SHOCK':>9}"
        f"{'SIDE':>7}"
        f"{'AH':>8}"
        f"{'ODDS':>8}"
        f"{'MOVE':>8}"
        f"{'AH AGENT':>11}"
        f"{'MASTER':>9}"
        f"{'CONF':>7}"
        f"{'CLV':>8}"
        f"{'RESULT':>9}"
        f"{'P/L':>8}"
    )

    print()
    print(
        header
    )

    print(
        "-" * 132
    )

    for _, r in data.iterrows():

        match = (
            f"{clean_text(r.get('home'))} - "
            f"{clean_text(r.get('away'))}"
        )

        if len(
            match
        ) > 33:

            match = match[
                :33
            ]

        minutes = safe_float(
            r.get(
                "minutes",
                np.nan
            )
        )

        minutes_text = (
            f"{minutes:.0f}"
            if pd.notna(
                minutes
            )
            else
            "-"
        )

        confidence = safe_float(
            r.get(
                "confidence",
                np.nan
            )
        )

        conf_text = (
            f"{confidence:.0%}"
            if pd.notna(
                confidence
            )
            else
            "-"
        )

        profit = safe_float(
            r.get(
                "profit",
                np.nan
            )
        )

        profit_text = (
            f"{profit:+.2f}"
            if pd.notna(
                profit
            )
            else
            "-"
        )

        print(

            f"{match:34}"

            f"{minutes_text:>7}"

            f"{clean_text(r.get('lineups')):>5}"

            f"{fmt_signed(r.get('shock')):>9}"

            f"{clean_text(r.get('side')):>7}"

            f"{fmt_signed(r.get('ah')):>8}"

            f"{fmt_odds(r.get('odds')):>8}"

            f"{fmt_signed(r.get('line_move')):>8}"

            f"{clean_text(r.get('ah_agent')):>11}"

            f"{clean_text(r.get('master')):>9}"

            f"{conf_text:>7}"

            f"{fmt_signed(r.get('clv'), 3):>8}"

            f"{clean_text(r.get('result')):>9}"

            f"{profit_text:>8}"
        )

    print()
    print(
        "AH / MASTER SUMMARY"
    )

    if (
        "master" in data.columns
        and
        len(
            data
        )
    ):

        print(
            data[
                "master"
            ]
            .value_counts()
            .to_string()
        )

    if "master" in data.columns:

        bets = data[
            data[
                "master"
            ]
            ==
            "BET"
        ]

        print()
        print(
            "Current Master BET candidates:",
            len(
                bets
            )
        )

    if "profit" in data.columns:

        settled = data[
            pd.to_numeric(
                data[
                    "profit"
                ],
                errors="coerce"
            ).notna()
        ]

        if len(
            settled
        ):

            profit = pd.to_numeric(
                settled[
                    "profit"
                ],
                errors="coerce"
            )

            print(
                "Settled bets:",
                len(
                    settled
                )
            )

            print(
                "Total profit:",
                f"{profit.sum():+.2f}u"
            )

            print(
                "ROI:",
                f"{profit.mean():+.2%}"
            )

            clv_values = pd.to_numeric(
                settled[
                    "clv"
                ],
                errors="coerce"
            ).dropna()

            if len(
                clv_values
            ):

                print(
                    "Average line CLV:",
                    f"{clv_values.mean():+.3f}"
                )


# ============================================================
# BTTS DISPLAY
# ============================================================

def show_btts_section(
    btts
):

    print()
    print(
        "=" * 132
    )

    print(
        "BTTS SHADOW — FROZEN 60-65% MODEL / EDGE >= 3%"
    )

    print(
        "=" * 132
    )

    if btts.empty:

        print()
        print(
            "No current BTTS shadow fixtures."
        )

        return

    if "kickoff_utc" in btts.columns:

        btts = btts.copy()

        btts[
            "_kickoff"
        ] = pd.to_datetime(
            btts[
                "kickoff_utc"
            ],
            utc=True,
            errors="coerce"
        )

        btts = btts.sort_values(
            "_kickoff"
        )

    header = (
        f"{'MATCH':34}"
        f"{'QUALITY':>14}"
        f"{'MODEL':>9}"
        f"{'MARKET':>9}"
        f"{'EDGE':>9}"
        f"{'ODDS':>8}"
        f"{'BOOKS':>7}"
        f"{'DECISION':>13}"
    )

    print()
    print(
        header
    )

    print(
        "-" * 132
    )

    for _, r in btts.iterrows():

        match = (
            f"{clean_text(r.get('home_team'))} - "
            f"{clean_text(r.get('away_team'))}"
        )

        if len(
            match
        ) > 33:

            match = match[
                :33
            ]

        books = safe_float(
            r.get(
                "bookmakers",
                np.nan
            )
        )

        books_text = (
            str(
                int(
                    books
                )
            )
            if pd.notna(
                books
            )
            else
            "-"
        )

        print(

            f"{match:34}"

            f"{clean_text(r.get('data_quality')):>14}"

            f"{fmt_pct(r.get('model_yes')):>9}"

            f"{fmt_pct(r.get('market_yes')):>9}"

            f"{fmt_signed(r.get('edge_yes'), 3):>9}"

            f"{fmt_odds(r.get('best_yes_odds')):>8}"

            f"{books_text:>7}"

            f"{clean_text(r.get('decision')):>13}"
        )

        reason = clean_text(
            r.get(
                "reason"
            ),
            default=""
        )

        if reason:

            print(
                " " * 4
                +
                "Reason: "
                +
                reason
            )

    print()
    print(
        "BTTS SUMMARY"
    )

    if "decision" in btts.columns:

        print(
            btts[
                "decision"
            ]
            .value_counts()
            .to_string()
        )

        shadow_bets = btts[
            btts[
                "decision"
            ]
            ==
            "SHADOW BET"
        ]

        print()

        print(
            "Current SHADOW BET candidates:",
            len(
                shadow_bets
            )
        )

    if "data_quality" in btts.columns:

        insufficient = btts[
            btts[
                "data_quality"
            ]
            .astype(str)
            .str.upper()
            !=
            "OK"
        ]

        print(
            "Insufficient-data fixtures:",
            len(
                insufficient
            )
        )


# ============================================================
# DASHBOARD
# ============================================================

def show_dashboard():

    master = load_csv(
        MASTER_FILE
    )

    ah = load_csv(
        AH_FILE
    )

    signals = load_csv(
        SIGNAL_FILE
    )

    clv = load_csv(
        CLV_FILE
    )

    results = load_csv(
        RESULT_FILE
    )

    btts = load_csv(
        BTTS_FILE
    )

    ah_data = build_ah_base(
        master,
        ah,
        signals,
        clv,
        results
    )

    os.system(
        "cls"
        if os.name == "nt"
        else
        "clear"
    )

    print(
        "=" * 132
    )

    print(
        "FOOTBALL AI — LIVE DASHBOARD"
    )

    print(
        "UTC:",
        utc_now().isoformat()
    )

    print(
        "=" * 132
    )

    show_ah_section(
        ah_data
    )

    show_btts_section(
        btts
    )

    print()
    print(
        "=" * 132
    )

    print(
        "SYSTEM"
    )

    print(
        "=" * 132
    )

    print(
        "AH/Master fixtures:",
        len(
            ah_data
        )
    )

    print(
        "BTTS shadow fixtures:",
        len(
            btts
        )
    )

    print(
        "API REQUESTS USED BY DASHBOARD: 0"
    )

    print(
        "=" * 132
    )


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--watch",
        action="store_true"
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=10
    )

    args = parser.parse_args()

    if not args.watch:

        show_dashboard()

        return

    while True:

        try:

            show_dashboard()

            time.sleep(
                max(
                    args.interval,
                    5
                )
            )

        except KeyboardInterrupt:

            print(
                "\nDashboard stopped."
            )

            break

        except Exception as exc:

            print(
                "DASHBOARD ERROR:",
                repr(exc)
            )

            time.sleep(
                10
            )


if __name__ == "__main__":

    main()