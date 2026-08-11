import json
import os
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests
from dotenv import load_dotenv


# ============================================================
# CONFIG
# ============================================================

PREDICTIONS_FILE = "btts_live_predictions.csv"

CACHE_FILE = "btts_odds_cache.json"

LATEST_FILE = "btts_live_watch.csv"
HISTORY_FILE = "btts_live_history.csv"

BASE_URL = "https://v3.football.api-sports.io"

BTTS_BET_ID = 8

CACHE_HOURS = 3

MODEL_LOW = 0.60
MODEL_HIGH = 0.65
MIN_EDGE = 0.03

MIN_BOOKMAKERS = 1


# ============================================================
# OUTPUT SCHEMA
# ============================================================

OUTPUT_COLUMNS = [
    "checked_utc",
    "fixture_id",
    "kickoff_utc",
    "home_team",
    "away_team",
    "data_quality",
    "model_yes",
    "model_zone",
    "bookmakers",
    "avg_yes_odds",
    "avg_no_odds",
    "market_yes",
    "edge_yes",
    "best_yes_odds",
    "best_yes_bookmaker",
    "decision",
    "reason",
]


# ============================================================
# ENV
# ============================================================

load_dotenv()

API_KEY = (
    os.getenv("API_FOOTBALL_KEY")
    or os.getenv("API_KEY")
    or os.getenv("APISPORTS_KEY")
)

if not API_KEY:
    raise RuntimeError(
        "API key not found in .env"
    )

HEADERS = {
    "x-apisports-key": API_KEY
}


# ============================================================
# TIME HELPERS
# ============================================================

def now_utc():

    return datetime.now(
        timezone.utc
    )


def parse_iso(value):

    if not value:
        return None

    try:

        dt = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00"
            )
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    except Exception:
        return None


# ============================================================
# CACHE
# ============================================================

def load_cache():

    if not os.path.exists(
        CACHE_FILE
    ):
        return {}

    try:

        with open(
            CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if isinstance(
            data,
            dict
        ):
            return data

    except Exception:
        pass

    return {}


def save_cache(cache):

    with open(
        CACHE_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            cache,
            f,
            indent=2,
            ensure_ascii=False
        )


def cache_is_fresh(entry):

    checked = parse_iso(
        entry.get(
            "checked_utc"
        )
    )

    if checked is None:
        return False

    age = (
        now_utc()
        -
        checked
    )

    return age < timedelta(
        hours=CACHE_HOURS
    )


# ============================================================
# API
# ============================================================

def fetch_fixture_btts_odds(
    fixture_id
):

    params = {
        "fixture":
            int(fixture_id),

        "bet":
            BTTS_BET_ID,
    }

    response = requests.get(
        f"{BASE_URL}/odds",
        headers=HEADERS,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# PARSE BTTS MARKET
# ============================================================

def extract_btts_market(
    payload
):

    rows = []

    for item in payload.get(
        "response",
        []
    ):

        for bookmaker in item.get(
            "bookmakers",
            []
        ):

            bookmaker_name = (
                bookmaker.get(
                    "name"
                )
            )

            bookmaker_id = (
                bookmaker.get(
                    "id"
                )
            )

            for bet in bookmaker.get(
                "bets",
                []
            ):

                try:

                    bet_id = int(
                        bet.get(
                            "id",
                            -1
                        )
                    )

                except Exception:
                    continue

                if bet_id != BTTS_BET_ID:
                    continue

                yes_odd = None
                no_odd = None

                for value in bet.get(
                    "values",
                    []
                ):

                    name = str(
                        value.get(
                            "value",
                            ""
                        )
                    ).strip().lower()

                    try:

                        odd = float(
                            value.get(
                                "odd"
                            )
                        )

                    except Exception:
                        continue

                    if odd <= 1:
                        continue

                    if name == "yes":
                        yes_odd = odd

                    elif name == "no":
                        no_odd = odd

                if (
                    yes_odd is None
                    or
                    no_odd is None
                ):
                    continue

                yes_imp = (
                    1.0
                    /
                    yes_odd
                )

                no_imp = (
                    1.0
                    /
                    no_odd
                )

                total = (
                    yes_imp
                    +
                    no_imp
                )

                if total <= 0:
                    continue

                fair_yes = (
                    yes_imp
                    /
                    total
                )

                rows.append({

                    "bookmaker_id":
                        bookmaker_id,

                    "bookmaker":
                        bookmaker_name,

                    "yes_odds":
                        yes_odd,

                    "no_odds":
                        no_odd,

                    "fair_yes":
                        fair_yes,
                })

    return rows


# ============================================================
# CONSENSUS
# ============================================================

def consensus_market(
    bookmaker_rows
):

    if not bookmaker_rows:
        return None

    yes_odds = [
        x["yes_odds"]
        for x in bookmaker_rows
    ]

    no_odds = [
        x["no_odds"]
        for x in bookmaker_rows
    ]

    fair_yes = [
        x["fair_yes"]
        for x in bookmaker_rows
    ]

    best_yes_row = max(
        bookmaker_rows,
        key=lambda x:
            x["yes_odds"]
    )

    return {

        "bookmakers":
            len(
                bookmaker_rows
            ),

        "avg_yes_odds":
            sum(
                yes_odds
            ) / len(
                yes_odds
            ),

        "avg_no_odds":
            sum(
                no_odds
            ) / len(
                no_odds
            ),

        "market_yes":
            sum(
                fair_yes
            ) / len(
                fair_yes
            ),

        "best_yes_odds":
            best_yes_row[
                "yes_odds"
            ],

        "best_yes_bookmaker":
            best_yes_row[
                "bookmaker"
            ],
    }


# ============================================================
# HISTORY HELPERS
# ============================================================

def values_equal(
    a,
    b
):

    a_missing = (
        pd.isna(a)
        or
        a is None
        or
        str(a).strip() == ""
    )

    b_missing = (
        pd.isna(b)
        or
        b is None
        or
        str(b).strip() == ""
    )

    if (
        a_missing
        and
        b_missing
    ):
        return True

    try:

        return abs(
            float(a)
            -
            float(b)
        ) <= 1e-9

    except Exception:

        return (
            str(a).strip()
            ==
            str(b).strip()
        )


def append_history(
    row
):

    new_row = pd.DataFrame(
        [row],
        columns=OUTPUT_COLUMNS
    )

    if not os.path.exists(
        HISTORY_FILE
    ):

        new_row.to_csv(
            HISTORY_FILE,
            index=False,
            encoding="utf-8-sig"
        )

        return

    try:

        old = pd.read_csv(
            HISTORY_FILE
        )

    except pd.errors.EmptyDataError:

        old = pd.DataFrame(
            columns=OUTPUT_COLUMNS
        )

    except Exception as e:

        print(
            "History read warning:",
            repr(e)
        )

        old = pd.DataFrame(
            columns=OUTPUT_COLUMNS
        )

    compare_cols = [
        "fixture_id",
        "data_quality",
        "decision",
        "model_yes",
        "market_yes",
        "edge_yes",
        "best_yes_odds",
        "best_yes_bookmaker",
        "bookmakers",
        "reason",
    ]

    if (
        len(old)
        and
        "fixture_id" in old.columns
    ):

        fixture_id = str(
            row.get(
                "fixture_id",
                ""
            )
        )

        same_fixture = old[
            old[
                "fixture_id"
            ].astype(str)
            ==
            fixture_id
        ]

        if len(
            same_fixture
        ):

            last = (
                same_fixture
                .iloc[-1]
            )

            same = True

            for col in compare_cols:

                if col not in old.columns:

                    same = False
                    break

                if not values_equal(
                    last.get(
                        col,
                        ""
                    ),
                    row.get(
                        col,
                        ""
                    )
                ):

                    same = False
                    break

            if same:
                return

    out = pd.concat(
        [
            old,
            new_row
        ],
        ignore_index=True
    )

    for col in OUTPUT_COLUMNS:

        if col not in out.columns:
            out[col] = None

    out = out[
        OUTPUT_COLUMNS
    ]

    out.to_csv(
        HISTORY_FILE,
        index=False,
        encoding="utf-8-sig"
    )


# ============================================================
# BASE ROW
# ============================================================

def make_base_row(
    match,
    fixture_id,
    home,
    away,
    data_quality,
    model_yes,
    model_zone
):

    return {

        "checked_utc":
            now_utc().isoformat(),

        "fixture_id":
            fixture_id,

        "kickoff_utc":
            match.get(
                "kickoff_utc",
                ""
            ),

        "home_team":
            home,

        "away_team":
            away,

        "data_quality":
            data_quality,

        "model_yes":
            model_yes,

        "model_zone":
            model_zone,

        "bookmakers":
            0,

        "avg_yes_odds":
            None,

        "avg_no_odds":
            None,

        "market_yes":
            None,

        "edge_yes":
            None,

        "best_yes_odds":
            None,

        "best_yes_bookmaker":
            None,

        "decision":
            "PASS",

        "reason":
            "",
    }


# ============================================================
# SAVE LATEST
# ============================================================

def save_latest(
    rows
):

    latest = pd.DataFrame(
        rows,
        columns=OUTPUT_COLUMNS
    )

    latest.to_csv(
        LATEST_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    return latest


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 82)
    print(
        "FOOTBALL AI — "
        "BTTS LIVE SHADOW AGENT"
    )
    print("=" * 82)

    if not os.path.exists(
        PREDICTIONS_FILE
    ):

        raise FileNotFoundError(
            PREDICTIONS_FILE
        )

    try:

        predictions = pd.read_csv(
            PREDICTIONS_FILE
        ).copy()

    except pd.errors.EmptyDataError:

        predictions = pd.DataFrame()

    # ========================================================
    # EMPTY PREDICTIONS
    #
    # Clear previous latest output so stale live decisions
    # cannot survive when there are no current fixtures.
    # ========================================================

    if len(
        predictions
    ) == 0:

        save_latest(
            []
        )

        print(
            "No live BTTS predictions."
        )

        print(
            "Saved empty:",
            LATEST_FILE
        )

        print(
            "API REQUESTS USED: 0"
        )

        return

    required = [
        "fixture_id",
        "kickoff_utc",
        "home_team",
        "away_team",
        "data_quality",
        "model_yes",
    ]

    missing = [
        col
        for col in required
        if col not in predictions.columns
    ]

    if missing:

        raise RuntimeError(
            "Missing prediction columns: "
            +
            ", ".join(
                missing
            )
        )

    predictions[
        "model_yes"
    ] = pd.to_numeric(
        predictions[
            "model_yes"
        ],
        errors="coerce"
    )

    cache = load_cache()

    api_requests = 0

    output_rows = []

    for _, match in (
        predictions.iterrows()
    ):

        fixture_id = int(
            match[
                "fixture_id"
            ]
        )

        home = str(
            match[
                "home_team"
            ]
        )

        away = str(
            match[
                "away_team"
            ]
        )

        data_quality = str(
            match.get(
                "data_quality",
                ""
            )
        ).strip().upper()

        model_yes_raw = (
            match.get(
                "model_yes"
            )
        )

        model_yes = (
            None
            if pd.isna(
                model_yes_raw
            )
            else
            float(
                model_yes_raw
            )
        )

        kickoff = parse_iso(
            match.get(
                "kickoff_utc",
                ""
            )
        )

        # ====================================================
        # PRE-MATCH INTEGRITY
        # ====================================================

        if (
            kickoff is not None
            and
            now_utc()
            >=
            kickoff
        ):

            base = make_base_row(
                match,
                fixture_id,
                home,
                away,
                data_quality,
                model_yes,
                False
            )

            base[
                "reason"
            ] = "Kickoff already started"

            output_rows.append(
                base
            )

            append_history(
                base
            )

            print()
            print(
                f"{home} vs {away}"
            )

            print(
                "  Decision: PASS"
            )

            print(
                "  Reason:",
                base[
                    "reason"
                ]
            )

            continue

        # ====================================================
        # DATA QUALITY
        #
        # No odds/API request is allowed when historical
        # xG/SOT data is insufficient.
        # ====================================================

        if data_quality != "OK":

            base = make_base_row(
                match,
                fixture_id,
                home,
                away,
                data_quality,
                model_yes,
                False
            )

            base[
                "reason"
            ] = (
                "Insufficient EPL xG/SOT history"
            )

            output_rows.append(
                base
            )

            append_history(
                base
            )

            print()
            print(
                f"{home} vs {away}"
            )

            print(
                "  Data quality:",
                data_quality
            )

            print(
                "  Decision: PASS"
            )

            print(
                "  Reason:",
                base[
                    "reason"
                ]
            )

            continue

        # ====================================================
        # MODEL PROBABILITY VALIDATION
        # ====================================================

        if model_yes is None:

            base = make_base_row(
                match,
                fixture_id,
                home,
                away,
                data_quality,
                None,
                False
            )

            base[
                "reason"
            ] = (
                "Missing model probability"
            )

            output_rows.append(
                base
            )

            append_history(
                base
            )

            print()
            print(
                f"{home} vs {away}"
            )

            print(
                "  Decision: PASS"
            )

            print(
                "  Reason:",
                base[
                    "reason"
                ]
            )

            continue

        # ====================================================
        # MODEL ZONE
        # ====================================================

        model_zone = (
            model_yes
            >=
            MODEL_LOW
            and
            model_yes
            <
            MODEL_HIGH
        )

        base = make_base_row(
            match,
            fixture_id,
            home,
            away,
            data_quality,
            model_yes,
            model_zone
        )

        print()
        print(
            f"{home} vs {away}"
        )

        print(
            "  Data quality:",
            data_quality
        )

        print(
            "  Model YES:",
            f"{model_yes:.2%}"
        )

        # ====================================================
        # OUTSIDE FROZEN ZONE
        # ====================================================

        if not model_zone:

            base[
                "reason"
            ] = (
                "Outside frozen "
                "60-65% model zone"
            )

            output_rows.append(
                base
            )

            append_history(
                base
            )

            print(
                "  Decision: PASS"
            )

            print(
                "  Reason:",
                base[
                    "reason"
                ]
            )

            continue

        # ====================================================
        # TEST FIXTURE PROTECTION
        # ====================================================

        if fixture_id >= 999000:

            base[
                "decision"
            ] = "WAIT"

            base[
                "reason"
            ] = (
                "Test fixture — "
                "API request blocked"
            )

            output_rows.append(
                base
            )

            append_history(
                base
            )

            print(
                "  Decision: WAIT"
            )

            print(
                "  Reason:",
                base[
                    "reason"
                ]
            )

            continue

        # ====================================================
        # CACHE / API
        # ====================================================

        key = str(
            fixture_id
        )

        cached = cache.get(
            key
        )

        if (
            cached
            and
            cache_is_fresh(
                cached
            )
        ):

            payload = cached.get(
                "payload",
                {}
            )

            source = "CACHE"

        else:

            try:

                payload = (
                    fetch_fixture_btts_odds(
                        fixture_id
                    )
                )

                api_requests += 1

                cache[
                    key
                ] = {

                    "checked_utc":
                        now_utc().isoformat(),

                    "payload":
                        payload,
                }

                save_cache(
                    cache
                )

                source = "API"

            except Exception as e:

                # Cache failure timestamp too, so we do not retry
                # every 5 minutes during a temporary outage.

                cache[
                    key
                ] = {

                    "checked_utc":
                        now_utc().isoformat(),

                    "payload":
                        {},
                }

                save_cache(
                    cache
                )

                base[
                    "decision"
                ] = "WAIT"

                base[
                    "reason"
                ] = (
                    "BTTS API error: "
                    +
                    str(e)
                )

                output_rows.append(
                    base
                )

                append_history(
                    base
                )

                print(
                    "  Decision: WAIT"
                )

                print(
                    "  Reason:",
                    base[
                        "reason"
                    ]
                )

                continue

        # ====================================================
        # MARKET
        # ====================================================

        bookmaker_rows = (
            extract_btts_market(
                payload
            )
        )

        market = (
            consensus_market(
                bookmaker_rows
            )
        )

        if market is None:

            base[
                "decision"
            ] = "WAIT"

            base[
                "reason"
            ] = (
                "No BTTS YES/NO "
                f"market yet ({source})"
            )

            output_rows.append(
                base
            )

            append_history(
                base
            )

            print(
                "  Decision: WAIT"
            )

            print(
                "  Reason:",
                base[
                    "reason"
                ]
            )

            continue

        # ====================================================
        # EDGE
        # ====================================================

        base[
            "bookmakers"
        ] = market[
            "bookmakers"
        ]

        base[
            "avg_yes_odds"
        ] = market[
            "avg_yes_odds"
        ]

        base[
            "avg_no_odds"
        ] = market[
            "avg_no_odds"
        ]

        base[
            "market_yes"
        ] = market[
            "market_yes"
        ]

        base[
            "best_yes_odds"
        ] = market[
            "best_yes_odds"
        ]

        base[
            "best_yes_bookmaker"
        ] = market[
            "best_yes_bookmaker"
        ]

        edge = (
            model_yes
            -
            market[
                "market_yes"
            ]
        )

        base[
            "edge_yes"
        ] = edge

        # ====================================================
        # FROZEN DECISION
        # ====================================================

        if (
            market[
                "bookmakers"
            ]
            <
            MIN_BOOKMAKERS
        ):

            base[
                "decision"
            ] = "WAIT"

            base[
                "reason"
            ] = (
                "Insufficient bookmaker coverage"
            )

        elif edge >= MIN_EDGE:

            base[
                "decision"
            ] = "SHADOW BET"

            base[
                "reason"
            ] = (
                "Frozen BTTS rule passed"
            )

        else:

            base[
                "decision"
            ] = "PASS"

            base[
                "reason"
            ] = (
                "Edge below frozen 3%"
            )

        output_rows.append(
            base
        )

        append_history(
            base
        )

        print(
            "  Market YES:",
            f"{market['market_yes']:.2%}"
        )

        print(
            "  Edge:",
            f"{edge:+.2%}"
        )

        print(
            "  Best YES odds:",
            f"{market['best_yes_odds']:.3f}",
            "|",
            market[
                "best_yes_bookmaker"
            ]
        )

        print(
            "  Bookmakers:",
            market[
                "bookmakers"
            ]
        )

        print(
            "  Source:",
            source
        )

        print(
            "  Decision:",
            base[
                "decision"
            ]
        )

    # ========================================================
    # SAVE LATEST
    # ========================================================

    latest = save_latest(
        output_rows
    )

    # ========================================================
    # SUMMARY
    # ========================================================

    print()
    print("=" * 82)
    print("SUMMARY")
    print("=" * 82)

    print(
        "Fixtures:",
        len(latest)
    )

    if len(
        latest
    ):

        print(
            "SHADOW BET:",
            int(
                (
                    latest[
                        "decision"
                    ]
                    ==
                    "SHADOW BET"
                ).sum()
            )
        )

        print(
            "WAIT:",
            int(
                (
                    latest[
                        "decision"
                    ]
                    ==
                    "WAIT"
                ).sum()
            )
        )

        print(
            "PASS:",
            int(
                (
                    latest[
                        "decision"
                    ]
                    ==
                    "PASS"
                ).sum()
            )
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
        "API REQUESTS USED:",
        api_requests
    )

    print("=" * 82)


if __name__ == "__main__":

    main()