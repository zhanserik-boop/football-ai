import os
import csv
from datetime import datetime, timezone, timedelta

import numpy as np
import requests

from dotenv import load_dotenv
from asian_handicap_v3_r2 import signal_market


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

API_KEY = (
    os.getenv("API_FOOTBALL_KEY")
    or os.getenv("API_KEY")
    or os.getenv("APISPORTS_KEY")
)

BASE_URL = "https://v3.football.api-sports.io"


SIGNAL_FILE = "lineup_signals_live.csv"

SNAPSHOT_FILE = "market_snapshots_v2.csv"

AH_AGENT_HISTORY = "ah_agent_v2_history.csv"

RESULT_CACHE = "fixture_results_live.csv"

OUTPUT_ALL = "post_lineup_clv_report.csv"

OUTPUT_BETS = "post_lineup_bets_report.csv"


# Do not request result immediately after kickoff
RESULT_MINUTES_AFTER_KICKOFF = 120

# Recheck non-final / unavailable results at most once every 6 hours.
RESULT_RECHECK_HOURS = 6

# Once one of these statuses is cached, never query the fixture again.
FINAL_STATUSES = {
    "FT",
    "AET",
    "PEN",
}


# ============================================================
# HELPERS
# ============================================================

def safe_float(value):

    try:

        if value in (
            "",
            None,
            "None"
        ):
            return None

        return float(value)

    except:
        return None


def safe_int(value):

    try:

        if value in (
            "",
            None,
            "None"
        ):
            return None

        return int(
            float(value)
        )

    except:
        return None


def parse_dt(value):

    if not value:
        return None

    try:

        return datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00"
            )
        )

    except:
        return None


def utc_now():

    return datetime.now(
        timezone.utc
    )


# ============================================================
# API
# ============================================================

def api_get(
    endpoint,
    params=None
):

    if not API_KEY:

        print(
            "API key not found."
        )

        return None


    try:

        response = requests.get(
            BASE_URL + endpoint,
            headers={
                "x-apisports-key":
                    API_KEY
            },
            params=params or {},
            timeout=30
        )


        if response.status_code != 200:

            print(
                "HTTP ERROR:",
                response.status_code,
                endpoint
            )

            return None


        data = response.json()


        if data.get(
            "errors"
        ):

            print(
                "API ERROR:",
                data["errors"]
            )

            return None


        return data


    except Exception as e:

        print(
            "REQUEST ERROR:",
            repr(e)
        )

        return None


# ============================================================
# CSV LOADER
# ============================================================

def load_csv_rows(
    filename
):

    rows = []


    if not os.path.exists(
        filename
    ):

        return rows


    if os.path.getsize(
        filename
    ) == 0:

        return rows


    try:

        with open(
            filename,
            encoding="utf-8-sig"
        ) as f:

            reader = csv.DictReader(
                f
            )

            for row in reader:

                rows.append(
                    row
                )


    except Exception as e:

        print(
            "ERROR reading",
            filename,
            repr(e)
        )


    return rows


# ============================================================
# SIGNALS
# ============================================================

signals = load_csv_rows(
    SIGNAL_FILE
)


signal_by_fixture = {}


for row in signals:

    fixture_id = str(
        row.get(
            "fixture_id",
            ""
        )
    )

    if fixture_id:

        signal_by_fixture[
            fixture_id
        ] = row


# ============================================================
# AH AGENT HISTORY
# ============================================================

ah_history = load_csv_rows(
    AH_AGENT_HISTORY
)


# First actual BET decision per fixture
bet_entries = {}


sorted_history = sorted(
    ah_history,
    key=lambda x:
        parse_dt(
            x.get(
                "decision_time_utc"
            )
        )
        or datetime.min.replace(
            tzinfo=timezone.utc
        )
)


for row in sorted_history:

    fixture_id = str(
        row.get(
            "fixture_id",
            ""
        )
    )

    decision = str(
        row.get(
            "decision",
            ""
        )
    ).upper()


    if (
        not fixture_id
        or
        decision != "BET"
    ):

        continue


    if fixture_id in bet_entries:

        continue


    bet_entries[
        fixture_id
    ] = row


# ============================================================
# SNAPSHOTS
# ============================================================

snapshots = {}


snapshot_rows = load_csv_rows(
    SNAPSHOT_FILE
)


for row in snapshot_rows:

    fixture_id = str(
        row.get(
            "fixture_id",
            ""
        )
    )


    snapshot_time = parse_dt(
        row.get(
            "snapshot_utc"
        )
    )


    kickoff = parse_dt(
        row.get(
            "kickoff_utc"
        )
    )


    parsed_handicap = safe_float(
        row.get(
            "parsed_handicap"
        )
    )


    odd = safe_float(
        row.get(
            "odd"
        )
    )


    if (
        not fixture_id
        or
        snapshot_time is None
        or
        kickoff is None
        or
        parsed_handicap is None
        or
        odd is None
    ):

        continue


    snapshots.setdefault(
        fixture_id,
        []
    )


    snapshots[
        fixture_id
    ].append({

        "snapshot_time":
            snapshot_time,

        "kickoff":
            kickoff,

        "side":
            str(
                row.get(
                    "parsed_side",
                    ""
                )
            ).upper(),

        "handicap":
            parsed_handicap,

        "odd":
            odd,

        "bookmaker":
            row.get(
                "bookmaker",
                ""
            ),
    })


# ============================================================
# MARKET CONSENSUS
# ============================================================

def market_consensus(
    rows,
    signal
):
    market = signal_market(rows, signal)
    if market is None:
        return None
    return {
        "handicap": market["handicap"],
        "avg_odds": market["average_odds"],
        "best_odds": market["best_odds"],
        "best_bookmaker": market["best_bookmaker"],
        "books": market["bookmakers"],
    }


# ============================================================
# GET CLOSING MARKET
# ============================================================

def get_closing_market(
    fixture_id,
    signal
):

    rows = snapshots.get(
        fixture_id,
        []
    )


    if not rows:

        return None


    # strictly before kickoff
    rows = [

        x
        for x in rows

        if (
            x[
                "snapshot_time"
            ]
            <
            x[
                "kickoff"
            ]
        )

    ]


    if not rows:

        return None


    timestamps = sorted(

        set(

            x[
                "snapshot_time"
            ]

            for x in rows

        ),

        reverse=True
    )


    # Work backwards until valid market
    for latest_time in timestamps:

        latest_rows = [

            x
            for x in rows

            if x[
                "snapshot_time"
            ]
            ==
            latest_time

        ]


        consensus = market_consensus(
            latest_rows,
            signal
        )


        if consensus is None:

            continue


        kickoff = latest_rows[
            0
        ][
            "kickoff"
        ]


        consensus[
            "snapshot_time"
        ] = latest_time


        consensus[
            "minutes_to_kickoff"
        ] = (
            kickoff
            -
            latest_time
        ).total_seconds() / 60


        return consensus


    return None


# ============================================================
# CLV
# ============================================================

def calculate_line_clv(
    entry_handicap,
    close_handicap
):

    # handicap already expressed
    # from selected side's perspective.
    #
    # Entry -0.50
    # Close -0.75
    #
    # +0.25 CLV

    return (
        entry_handicap
        -
        close_handicap
    )


def calculate_price_clv(
    entry_odds,
    close_odds
):

    # Positive means we took
    # a larger decimal price.

    return (
        entry_odds
        -
        close_odds
    )


# ============================================================
# RESULT CACHE
# ============================================================

cached_results_rows = load_csv_rows(
    RESULT_CACHE
)


cached_results = {}


for row in cached_results_rows:

    fixture_id = str(
        row.get(
            "fixture_id",
            ""
        )
    )

    if fixture_id:

        cached_results[
            fixture_id
        ] = row


def save_result_cache():

    fields = [
        "fixture_id",
        "kickoff_utc",
        "home_team",
        "away_team",
        "status",
        "home_goals",
        "away_goals",
        "checked_utc",
    ]


    with open(
        RESULT_CACHE,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()


        for row in cached_results.values():

            writer.writerow({

                key:
                    row.get(
                        key,
                        ""
                    )

                for key in fields
            })


# ============================================================
# FETCH MATCH RESULT
# ============================================================

def fetch_result(
    fixture_id
):

    data = api_get(
        "/fixtures",
        {
            "id":
                fixture_id
        }
    )


    if not data:

        return None


    response = data.get(
        "response",
        []
    )


    if not response:

        return None


    item = response[
        0
    ]


    fixture = item.get(
        "fixture",
        {}
    )


    teams = item.get(
        "teams",
        {}
    )


    goals = item.get(
        "goals",
        {}
    )


    status = (
        fixture
        .get(
            "status",
            {}
        )
        .get(
            "short",
            ""
        )
    )


    return {

        "fixture_id":
            str(
                fixture_id
            ),

        "kickoff_utc":
            fixture.get(
                "date",
                ""
            ),

        "home_team":
            (
                teams
                .get(
                    "home",
                    {}
                )
                .get(
                    "name",
                    ""
                )
            ),

        "away_team":
            (
                teams
                .get(
                    "away",
                    {}
                )
                .get(
                    "name",
                    ""
                )
            ),

        "status":
            status,

        "home_goals":
            goals.get(
                "home"
            ),

        "away_goals":
            goals.get(
                "away"
            ),

        "checked_utc":
            utc_now().isoformat(),
    }


# ============================================================
# GET RESULT
# ============================================================

api_requests_used = 0


def get_result(
    fixture_id,
    kickoff
):

    global api_requests_used


    cached = cached_results.get(
        fixture_id
    )


    # ========================================================
    # FINAL RESULT ALREADY CACHED
    # ========================================================

    if cached:

        status = str(
            cached.get(
                "status",
                ""
            )
        ).upper()


        if status in FINAL_STATUSES:

            return cached


    # ========================================================
    # NO KICKOFF
    # ========================================================

    if kickoff is None:

        return cached


    # ========================================================
    # DO NOT CHECK TOO SOON AFTER KICKOFF
    # ========================================================

    minutes_after = (
        utc_now()
        -
        kickoff
    ).total_seconds() / 60


    if (
        minutes_after
        <
        RESULT_MINUTES_AFTER_KICKOFF
    ):

        return cached


    # ========================================================
    # 6-HOUR COOLDOWN FOR NON-FINAL / EMPTY RESULT
    # ========================================================

    if cached:

        checked_utc = parse_dt(
            cached.get(
                "checked_utc",
                ""
            )
        )


        if checked_utc is not None:

            age = (
                utc_now()
                -
                checked_utc
            )


            if age < timedelta(
                hours=RESULT_RECHECK_HOURS
            ):

                return cached


    # ========================================================
    # API CHECK
    # ========================================================

    result = fetch_result(
        fixture_id
    )


    api_requests_used += 1


    # If API gave no usable result, still save checked_utc so
    # the report does not retry this fixture every 15 minutes.
    if result is None:

        if cached is None:

            cached = {
                "fixture_id":
                    str(
                        fixture_id
                    ),

                "kickoff_utc":
                    (
                        kickoff.isoformat()
                        if kickoff
                        else ""
                    ),

                "home_team":
                    "",

                "away_team":
                    "",

                "status":
                    "",

                "home_goals":
                    "",

                "away_goals":
                    "",

                "checked_utc":
                    utc_now().isoformat(),
            }

        else:

            cached[
                "checked_utc"
            ] = utc_now().isoformat()


        cached_results[
            fixture_id
        ] = cached


        save_result_cache()


        return cached


    # Save latest status/result.
    cached_results[
        fixture_id
    ] = result


    save_result_cache()


    return result


# ============================================================
# ASIAN HANDICAP SETTLEMENT
# ============================================================

def normalize_quarter_line(
    handicap
):

    return (
        round(
            handicap * 4
        )
        /
        4
    )


def split_asian_line(
    handicap
):

    h = normalize_quarter_line(
        handicap
    )


    quarter_units = round(
        h * 4
    )


    # odd quarter = x.25 or x.75
    if abs(
        quarter_units
    ) % 2 == 1:

        return [
            h - 0.25,
            h + 0.25
        ]


    return [
        h
    ]


def settle_single(
    goal_difference,
    handicap,
    odds
):

    result = (
        goal_difference
        +
        handicap
    )


    if result > 0:

        return (
            odds
            -
            1
        )


    if result < 0:

        return -1.0


    return 0.0


def settle_ah(
    signal,
    handicap,
    odds,
    home_goals,
    away_goals
):

    if (
        handicap is None
        or
        odds is None
        or
        home_goals is None
        or
        away_goals is None
    ):

        return None


    if signal == "HOME":

        goal_difference = (
            home_goals
            -
            away_goals
        )


    elif signal == "AWAY":

        goal_difference = (
            away_goals
            -
            home_goals
        )


    else:

        return None


    lines = split_asian_line(
        handicap
    )


    profits = [

        settle_single(
            goal_difference,
            line,
            odds
        )

        for line in lines
    ]


    return float(
        np.mean(
            profits
        )
    )


# ============================================================
# MAIN REPORT
# ============================================================

print()

print(
    "=" * 72
)

print(
    "POST-LINEUP CLV + BET SETTLEMENT"
)

print(
    "=" * 72
)


print(
    "Lineup signals:",
    len(
        signals
    )
)


print(
    "AH Agent BET decisions:",
    len(
        bet_entries
    )
)


# ============================================================
# NOTHING YET
# ============================================================

if not signals:

    print()
    print(
        "No live lineup signals yet."
    )

    print(
        "API REQUESTS USED: 0"
    )

    print(
        "=" * 72
    )

    raise SystemExit


# ============================================================
# BUILD REPORT
# ============================================================

report_rows = []


for signal_row in signals:

    fixture_id = str(
        signal_row.get(
            "fixture_id",
            ""
        )
    )


    if not fixture_id:

        continue


    signal = str(
        signal_row.get(
            "signal",
            ""
        )
    ).upper()


    kickoff = parse_dt(
        signal_row.get(
            "kickoff_utc"
        )
    )


    bet = bet_entries.get(
        fixture_id
    )


    has_bet = (
        bet is not None
    )


    # ========================================================
    # BET ENTRY
    # ========================================================

    entry_time = None
    entry_handicap = None
    entry_avg_odds = None
    entry_best_odds = None
    entry_best_bookmaker = ""
    bet_reason = ""


    if has_bet:

        entry_time = parse_dt(
            bet.get(
                "decision_time_utc"
            )
        )


        entry_handicap = safe_float(
            bet.get(
                "current_handicap"
            )
        )


        entry_avg_odds = safe_float(
            bet.get(
                "current_avg_odds"
            )
        )


        entry_best_odds = safe_float(
            bet.get(
                "current_best_odds"
            )
        )


        entry_best_bookmaker = str(
            bet.get(
                "current_best_bookmaker",
                ""
            )
        )


        bet_reason = str(
            bet.get(
                "reason",
                ""
            )
        )


    # ========================================================
    # CLOSING
    # ========================================================

    close = get_closing_market(
        fixture_id,
        signal
    )


    close_handicap = None
    close_avg_odds = None
    close_best_odds = None
    close_time = None
    minutes_close = None


    if close:

        close_handicap = close[
            "handicap"
        ]

        close_avg_odds = close[
            "avg_odds"
        ]

        close_best_odds = close[
            "best_odds"
        ]

        close_time = close[
            "snapshot_time"
        ]

        minutes_close = close[
            "minutes_to_kickoff"
        ]


    # ========================================================
    # CLV
    # ========================================================

    line_clv = None
    price_clv = None
    same_line = False


    if (
        has_bet
        and
        entry_handicap is not None
        and
        close_handicap is not None
    ):

        line_clv = calculate_line_clv(
            entry_handicap,
            close_handicap
        )


        same_line = (
            abs(
                entry_handicap
                -
                close_handicap
            )
            <
            0.00001
        )


        if (
            same_line
            and
            entry_best_odds is not None
            and
            close_avg_odds is not None
        ):

            price_clv = calculate_price_clv(
                entry_best_odds,
                close_avg_odds
            )


    # ========================================================
    # RESULT
    # ========================================================

    result = get_result(
        fixture_id,
        kickoff
    )


    status = ""
    home_goals = None
    away_goals = None


    if result:

        status = str(
            result.get(
                "status",
                ""
            )
        )


        home_goals = safe_int(
            result.get(
                "home_goals"
            )
        )


        away_goals = safe_int(
            result.get(
                "away_goals"
            )
        )


    # ========================================================
    # PROFIT
    # ========================================================

    profit = None


    if (
        has_bet
        and
        status.upper()
        in FINAL_STATUSES
    ):

        profit = settle_ah(

            signal=
                signal,

            handicap=
                entry_handicap,

            odds=
                entry_best_odds,

            home_goals=
                home_goals,

            away_goals=
                away_goals,
        )


    report_rows.append({

        "fixture_id":
            fixture_id,

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

        "kickoff_utc":
            signal_row.get(
                "kickoff_utc",
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

        "data_quality":
            signal_row.get(
                "data_quality",
                ""
            ),

        "has_bet":
            has_bet,

        "bet_reason":
            bet_reason,

        "entry_time":
            (
                entry_time.isoformat()
                if entry_time
                else ""
            ),

        "entry_handicap":
            entry_handicap,

        "entry_avg_odds":
            entry_avg_odds,

        "entry_best_odds":
            entry_best_odds,

        "entry_best_bookmaker":
            entry_best_bookmaker,

        "close_time":
            (
                close_time.isoformat()
                if close_time
                else ""
            ),

        "minutes_to_kickoff_close":
            minutes_close,

        "close_handicap":
            close_handicap,

        "close_avg_odds":
            close_avg_odds,

        "close_best_odds":
            close_best_odds,

        "line_clv":
            line_clv,

        "same_line":
            same_line,

        "price_clv":
            price_clv,

        "match_status":
            status,

        "home_goals":
            home_goals,

        "away_goals":
            away_goals,

        "profit":
            profit,
    })


# ============================================================
# SAVE ALL SIGNALS
# ============================================================

if report_rows:

    fields = list(
        report_rows[
            0
        ].keys()
    )


    with open(
        OUTPUT_ALL,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()

        writer.writerows(
            report_rows
        )


# ============================================================
# BET ONLY
# ============================================================

bet_rows = [

    x
    for x in report_rows

    if x[
        "has_bet"
    ]

]


if bet_rows:

    fields = list(
        bet_rows[
            0
        ].keys()
    )


    with open(
        OUTPUT_BETS,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()

        writer.writerows(
            bet_rows
        )


# ============================================================
# PRINT BETS
# ============================================================

print()
print(
    "=" * 72
)

print(
    "BET REPORT"
)

print(
    "=" * 72
)


if not bet_rows:

    print(
        "No AH Agent V2 BET entries yet."
    )


for row in bet_rows:

    print()

    print(
        row[
            "home_team"
        ],
        "-",
        row[
            "away_team"
        ]
    )


    print(
        "Signal:",
        row[
            "signal"
        ],
        "| ShockDiff:",
        (
            f"{row['shock_diff']:+.2f}"
            if row[
                "shock_diff"
            ] is not None
            else "N/A"
        )
    )


    print(
        "Entry:",
        (
            f"{row['entry_handicap']:+.2f}"
            if row[
                "entry_handicap"
            ] is not None
            else "N/A"
        ),
        "@",
        (
            f"{row['entry_best_odds']:.3f}"
            if row[
                "entry_best_odds"
            ] is not None
            else "N/A"
        )
    )


    if (
        row[
            "close_handicap"
        ]
        is not None
    ):

        print(
            "Close:",
            f"{row['close_handicap']:+.2f}",
            "@",
            f"{row['close_avg_odds']:.3f}"
        )


        print(
            "Line CLV:",
            f"{row['line_clv']:+.3f}"
        )


        if (
            row[
                "price_clv"
            ]
            is not None
        ):

            print(
                "Price CLV:",
                f"{row['price_clv']:+.3f}"
            )


    else:

        print(
            "Closing AH: pending"
        )


    if (
        row[
            "profit"
        ]
        is not None
    ):

        print(
            "Result:",
            f"{row['home_goals']}-"
            f"{row['away_goals']}"
        )


        print(
            "Profit:",
            f"{row['profit']:+.2f}u"
        )


    else:

        print(
            "Settlement: pending"
        )


# ============================================================
# AGGREGATE
# ============================================================

print()
print(
    "=" * 72
)

print(
    "AGGREGATE — BET ONLY"
)

print(
    "=" * 72
)


print(
    "Bets:",
    len(
        bet_rows
    )
)


# ============================================================
# CLV
# ============================================================

with_clv = [

    x
    for x in bet_rows

    if x[
        "line_clv"
    ] is not None

]


if with_clv:

    clv = np.array([

        x[
            "line_clv"
        ]

        for x in with_clv

    ])


    print(
        "Bets with closing AH:",
        len(
            with_clv
        )
    )


    print(
        "Average AH line CLV:",
        f"{clv.mean():+.3f}"
    )


    print(
        "Median AH line CLV:",
        f"{np.median(clv):+.3f}"
    )


    print(
        "Positive line CLV:",
        f"{np.mean(clv > 0)*100:.1f}%"
    )


    print(
        "Non-negative line CLV:",
        f"{np.mean(clv >= 0)*100:.1f}%"
    )


# ============================================================
# SAME-LINE PRICE CLV
# ============================================================

with_price = [

    x
    for x in bet_rows

    if x[
        "price_clv"
    ] is not None

]


if with_price:

    p = np.array([

        x[
            "price_clv"
        ]

        for x in with_price

    ])


    print()

    print(
        "Same-line bets:",
        len(
            with_price
        )
    )


    print(
        "Average price CLV:",
        f"{p.mean():+.3f}"
    )


    print(
        "Positive price CLV:",
        f"{np.mean(p > 0)*100:.1f}%"
    )


# ============================================================
# ROI
# ============================================================

settled = [

    x
    for x in bet_rows

    if x[
        "profit"
    ] is not None

]


if settled:

    profits = np.array([

        x[
            "profit"
        ]

        for x in settled

    ])


    print()

    print(
        "Settled bets:",
        len(
            settled
        )
    )


    print(
        "Profit:",
        f"{profits.sum():+.2f}u"
    )


    print(
        "ROI:",
        f"{profits.mean():+.2%}"
    )


    print(
        "Profitable:",
        f"{np.mean(profits > 0)*100:.1f}%"
    )


# ============================================================
# BY SIDE
# ============================================================

if settled:

    print()
    print(
        "=" * 72
    )

    print(
        "ROI BY SIDE"
    )

    print(
        "=" * 72
    )


    for side in [
        "HOME",
        "AWAY"
    ]:

        group = [

            x
            for x in settled

            if x[
                "signal"
            ] == side

        ]


        if not group:

            continue


        p = np.array([

            x[
                "profit"
            ]

            for x in group

        ])


        print(
            f"{side:5} | "
            f"N {len(group):3d} | "
            f"Profit {p.sum():+.2f}u | "
            f"ROI {p.mean():+.2%}"
        )


# ============================================================
# BY SHOCK
# ============================================================

if settled:

    print()
    print(
        "=" * 72
    )

    print(
        "ROI BY SHOCK SIZE"
    )

    print(
        "=" * 72
    )


    ranges = [

        (
            "1.5-2.0",
            1.5,
            2.0
        ),

        (
            "2.0-2.5",
            2.0,
            2.5
        ),

        (
            "2.5-3.0",
            2.5,
            3.0
        ),

        (
            "3.0+",
            3.0,
            100
        ),
    ]


    for label, low, high in ranges:

        group = [

            x
            for x in settled

            if (
                x[
                    "shock_diff"
                ] is not None

                and

                low
                <=
                abs(
                    x[
                        "shock_diff"
                    ]
                )
                <
                high
            )

        ]


        if not group:

            continue


        p = np.array([

            x[
                "profit"
            ]

            for x in group

        ])


        print(
            f"{label:8} | "
            f"N {len(group):3d} | "
            f"Profit {p.sum():+.2f}u | "
            f"ROI {p.mean():+.2%}"
        )


# ============================================================
# FILES
# ============================================================

print()
print(
    "=" * 72
)

print(
    "FILES"
)

print(
    "=" * 72
)


print(
    "All signals:",
    OUTPUT_ALL
)


print(
    "BET only:",
    OUTPUT_BETS
)


print(
    "Result cache:",
    RESULT_CACHE
)


print(
    "API REQUESTS USED:",
    api_requests_used
)


print()
print(
    "=" * 72
)

print(
    "DONE"
)

print(
    "=" * 72
)
