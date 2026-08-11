import csv
import os
from collections import defaultdict, deque

import pandas as pd


# ============================================================
# CONFIG
# ============================================================

XG_FILE = "epl_xg_history.csv"

BASE_ODDS_FILES = {
    2022: "epl_odds_2022.csv",
    2023: "epl_odds_2023.csv",
    2024: "epl_odds_2024.csv",
    2025: "epl_odds_2025.csv",
}

OPTIONAL_ODDS_FILES = {
    2026: "epl_odds_2026.csv",
}

FIXTURES_FILE = "btts_live_fixtures.csv"

OUTPUT_FILE = "btts_live_features.csv"

ROLLING_WINDOW = 10
RECENT_WINDOW = 5

MIN_HISTORY_MATCHES = 5


# ============================================================
# OUTPUT
# ============================================================

OUTPUT_COLUMNS = [
    "fixture_id",
    "kickoff_utc",
    "home_team",
    "away_team",
    "home_team_model",
    "away_team_model",

    "home_xgf10",
    "home_xga10",
    "away_xgf10",
    "away_xga10",

    "home_xgf5",
    "home_xga5",
    "away_xgf5",
    "away_xga5",

    "home_venue_xgf",
    "home_venue_xga",
    "away_venue_xgf",
    "away_venue_xga",

    "home_sot10",
    "away_sot10",

    "expected_home_xg",
    "expected_away_xg",
    "expected_total_xg",
    "expected_xg_balance",

    "home_xg_matches",
    "away_xg_matches",
    "home_sot_matches",
    "away_sot_matches",

    "data_quality",
]


# ============================================================
# TEAM NORMALIZATION
# ============================================================

API_TO_MARKET = {

    "Manchester United": "Man United",
    "Man United": "Man United",

    "Manchester City": "Man City",
    "Man City": "Man City",

    "Nottingham Forest": "Nott'm Forest",
    "Nott'm Forest": "Nott'm Forest",

    "Wolverhampton Wanderers": "Wolves",
    "Wolverhampton": "Wolves",
    "Wolves": "Wolves",

    "Newcastle United": "Newcastle",
    "Newcastle": "Newcastle",

    "Sheffield Utd": "Sheffield United",
    "Sheffield United": "Sheffield United",

    "Tottenham Hotspur": "Tottenham",
    "Tottenham": "Tottenham",

    "West Ham United": "West Ham",
    "West Ham": "West Ham",

    "Brighton and Hove Albion": "Brighton",
    "Brighton & Hove Albion": "Brighton",
    "Brighton": "Brighton",

    "Coventry City": "Coventry",
    "Coventry": "Coventry",

    "Hull City": "Hull",
    "Hull": "Hull",

    "Ipswich Town": "Ipswich",
    "Ipswich": "Ipswich",
}


XG_TO_MARKET = {

    "Manchester United": "Man United",
    "Man United": "Man United",

    "Manchester City": "Man City",
    "Man City": "Man City",

    "Nottingham Forest": "Nott'm Forest",
    "Nott'm Forest": "Nott'm Forest",

    "Wolverhampton Wanderers": "Wolves",
    "Wolves": "Wolves",

    "Newcastle United": "Newcastle",
    "Newcastle": "Newcastle",

    "Sheffield United": "Sheffield United",

    "Tottenham": "Tottenham",
    "Tottenham Hotspur": "Tottenham",

    "West Ham": "West Ham",
    "West Ham United": "West Ham",

    "Brighton": "Brighton",
    "Brighton and Hove Albion": "Brighton",

    "Coventry City": "Coventry",
    "Coventry": "Coventry",

    "Hull City": "Hull",
    "Hull": "Hull",

    "Ipswich Town": "Ipswich",
    "Ipswich": "Ipswich",
}


def market_team(name):

    name = str(
        name
    ).strip()

    return API_TO_MARKET.get(
        name,
        name
    )


def xg_team(name):

    name = str(
        name
    ).strip()

    return XG_TO_MARKET.get(
        name,
        market_team(
            name
        )
    )


# ============================================================
# HELPERS
# ============================================================

def mean(values):

    values = list(
        values
    )

    if not values:
        return 0.0

    return (
        sum(
            values
        )
        /
        len(
            values
        )
    )


def safe_float(value):

    try:

        if value in (
            "",
            None,
            "None",
        ):
            return None

        value = float(
            value
        )

        if pd.isna(
            value
        ):
            return None

        return value

    except Exception:
        return None


# ============================================================
# XG STATE
# ============================================================

def new_xg_state():

    return {

        "xgf":
            deque(
                maxlen=ROLLING_WINDOW
            ),

        "xga":
            deque(
                maxlen=ROLLING_WINDOW
            ),

        "home_xgf":
            deque(
                maxlen=ROLLING_WINDOW
            ),

        "home_xga":
            deque(
                maxlen=ROLLING_WINDOW
            ),

        "away_xgf":
            deque(
                maxlen=ROLLING_WINDOW
            ),

        "away_xga":
            deque(
                maxlen=ROLLING_WINDOW
            ),
    }


# ============================================================
# SOT STATE
# ============================================================

def new_team_state():

    return {

        "sot_for":
            deque(
                maxlen=ROLLING_WINDOW
            ),
    }


# ============================================================
# LOAD XG
# ============================================================

def build_xg_state():

    if not os.path.exists(
        XG_FILE
    ):

        raise FileNotFoundError(
            XG_FILE
        )

    matches = []

    with open(
        XG_FILE,
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.DictReader(
            file
        )

        for row in reader:

            try:

                date = str(
                    row["date"]
                )[:10]

                home = xg_team(
                    row["home_team"]
                )

                away = xg_team(
                    row["away_team"]
                )

                hxg = float(
                    row["home_xg"]
                )

                axg = float(
                    row["away_xg"]
                )

            except Exception:
                continue

            matches.append({

                "date":
                    date,

                "home":
                    home,

                "away":
                    away,

                "hxg":
                    hxg,

                "axg":
                    axg,
            })

    matches.sort(
        key=lambda x:
            x["date"]
    )

    state = defaultdict(
        new_xg_state
    )

    for match in matches:

        home = match[
            "home"
        ]

        away = match[
            "away"
        ]

        hs = state[
            home
        ]

        aws = state[
            away
        ]

        hs[
            "xgf"
        ].append(
            match[
                "hxg"
            ]
        )

        hs[
            "xga"
        ].append(
            match[
                "axg"
            ]
        )

        aws[
            "xgf"
        ].append(
            match[
                "axg"
            ]
        )

        aws[
            "xga"
        ].append(
            match[
                "hxg"
            ]
        )

        hs[
            "home_xgf"
        ].append(
            match[
                "hxg"
            ]
        )

        hs[
            "home_xga"
        ].append(
            match[
                "axg"
            ]
        )

        aws[
            "away_xgf"
        ].append(
            match[
                "axg"
            ]
        )

        aws[
            "away_xga"
        ].append(
            match[
                "hxg"
            ]
        )

    return (
        state,
        matches
    )


# ============================================================
# ACTIVE SOT FILES
# ============================================================

def get_active_odds_files():

    files = dict(
        BASE_ODDS_FILES
    )

    for season, filename in (
        OPTIONAL_ODDS_FILES.items()
    ):

        if os.path.exists(
            filename
        ):

            files[
                season
            ] = filename

    return files


# ============================================================
# LOAD SOT
# ============================================================

def build_sot_state():

    odds_files = (
        get_active_odds_files()
    )

    matches = []

    for season, filename in sorted(
        odds_files.items()
    ):

        # Historical files are mandatory.
        if (
            season in BASE_ODDS_FILES
            and
            not os.path.exists(
                filename
            )
        ):

            raise FileNotFoundError(
                filename
            )

        if not os.path.exists(
            filename
        ):
            continue

        with open(
            filename,
            encoding="utf-8-sig",
            newline=""
        ) as file:

            reader = csv.DictReader(
                file
            )

            required = {
                "Date",
                "HomeTeam",
                "AwayTeam",
                "HST",
                "AST",
            }

            missing = (
                required
                -
                set(
                    reader.fieldnames
                    or
                    []
                )
            )

            if missing:

                # Optional current-season file should never
                # break the whole live feature pipeline.
                if season in OPTIONAL_ODDS_FILES:

                    print(
                        "WARNING:",
                        filename,
                        "ignored because required columns "
                        "are missing:",
                        ", ".join(
                            sorted(
                                missing
                            )
                        )
                    )

                    continue

                raise RuntimeError(
                    f"{filename} missing columns: "
                    +
                    ", ".join(
                        sorted(
                            missing
                        )
                    )
                )

            for row in reader:

                try:

                    date = pd.to_datetime(
                        row[
                            "Date"
                        ],
                        dayfirst=True,
                        errors="coerce"
                    )

                    if pd.isna(
                        date
                    ):
                        continue

                    date_key = (
                        date.strftime(
                            "%Y-%m-%d"
                        )
                    )

                    home = market_team(
                        row[
                            "HomeTeam"
                        ]
                    )

                    away = market_team(
                        row[
                            "AwayTeam"
                        ]
                    )

                    hst = safe_float(
                        row.get(
                            "HST"
                        )
                    )

                    ast = safe_float(
                        row.get(
                            "AST"
                        )
                    )

                except Exception:
                    continue

                # Do not put incomplete matches into SOT state.
                if (
                    hst is None
                    or
                    ast is None
                ):
                    continue

                matches.append({

                    "season":
                        season,

                    "date":
                        date_key,

                    "home":
                        home,

                    "away":
                        away,

                    "hst":
                        hst,

                    "ast":
                        ast,
                })

    matches.sort(
        key=lambda x:
            x[
                "date"
            ]
    )

    state = defaultdict(
        new_team_state
    )

    for match in matches:

        home = match[
            "home"
        ]

        away = match[
            "away"
        ]

        state[
            home
        ][
            "sot_for"
        ].append(
            match[
                "hst"
            ]
        )

        state[
            away
        ][
            "sot_for"
        ].append(
            match[
                "ast"
            ]
        )

    return (
        state,
        matches,
        odds_files
    )


# ============================================================
# FEATURE CALCULATION
# ============================================================

def make_features(
    home,
    away,
    xg_state,
    sot_state
):

    home = market_team(
        home
    )

    away = market_team(
        away
    )

    hs = xg_state[
        home
    ]

    aws = xg_state[
        away
    ]

    # ========================================================
    # XG 10
    # ========================================================

    home_xgf10 = mean(
        hs[
            "xgf"
        ]
    )

    home_xga10 = mean(
        hs[
            "xga"
        ]
    )

    away_xgf10 = mean(
        aws[
            "xgf"
        ]
    )

    away_xga10 = mean(
        aws[
            "xga"
        ]
    )

    # ========================================================
    # XG 5
    # ========================================================

    home_xgf5 = mean(
        list(
            hs[
                "xgf"
            ]
        )[
            -RECENT_WINDOW:
        ]
    )

    home_xga5 = mean(
        list(
            hs[
                "xga"
            ]
        )[
            -RECENT_WINDOW:
        ]
    )

    away_xgf5 = mean(
        list(
            aws[
                "xgf"
            ]
        )[
            -RECENT_WINDOW:
        ]
    )

    away_xga5 = mean(
        list(
            aws[
                "xga"
            ]
        )[
            -RECENT_WINDOW:
        ]
    )

    # ========================================================
    # VENUE XG
    # ========================================================

    home_venue_xgf = (
        mean(
            hs[
                "home_xgf"
            ]
        )
        if hs[
            "home_xgf"
        ]
        else home_xgf10
    )

    home_venue_xga = (
        mean(
            hs[
                "home_xga"
            ]
        )
        if hs[
            "home_xga"
        ]
        else home_xga10
    )

    away_venue_xgf = (
        mean(
            aws[
                "away_xgf"
            ]
        )
        if aws[
            "away_xgf"
        ]
        else away_xgf10
    )

    away_venue_xga = (
        mean(
            aws[
                "away_xga"
            ]
        )
        if aws[
            "away_xga"
        ]
        else away_xga10
    )

    # ========================================================
    # EXPECTED XG
    # ========================================================

    expected_home_xg = (
        home_venue_xgf
        +
        away_venue_xga
    ) / 2.0

    expected_away_xg = (
        away_venue_xgf
        +
        home_venue_xga
    ) / 2.0

    expected_total_xg = (
        expected_home_xg
        +
        expected_away_xg
    )

    expected_xg_balance = abs(
        expected_home_xg
        -
        expected_away_xg
    )

    # ========================================================
    # SOT
    # ========================================================

    home_sot10 = mean(
        sot_state[
            home
        ][
            "sot_for"
        ]
    )

    away_sot10 = mean(
        sot_state[
            away
        ][
            "sot_for"
        ]
    )

    # ========================================================
    # HISTORY COUNTS
    # ========================================================

    home_xg_matches = len(
        hs[
            "xgf"
        ]
    )

    away_xg_matches = len(
        aws[
            "xgf"
        ]
    )

    home_sot_matches = len(
        sot_state[
            home
        ][
            "sot_for"
        ]
    )

    away_sot_matches = len(
        sot_state[
            away
        ][
            "sot_for"
        ]
    )

    # ========================================================
    # DATA QUALITY GATE
    # ========================================================

    data_quality = (
        "OK"
        if (
            home_xg_matches
            >=
            MIN_HISTORY_MATCHES
            and
            away_xg_matches
            >=
            MIN_HISTORY_MATCHES
            and
            home_sot_matches
            >=
            MIN_HISTORY_MATCHES
            and
            away_sot_matches
            >=
            MIN_HISTORY_MATCHES
        )
        else
        "INSUFFICIENT"
    )

    return {

        "home_xgf10":
            home_xgf10,

        "home_xga10":
            home_xga10,

        "away_xgf10":
            away_xgf10,

        "away_xga10":
            away_xga10,

        "home_xgf5":
            home_xgf5,

        "home_xga5":
            home_xga5,

        "away_xgf5":
            away_xgf5,

        "away_xga5":
            away_xga5,

        "home_venue_xgf":
            home_venue_xgf,

        "home_venue_xga":
            home_venue_xga,

        "away_venue_xgf":
            away_venue_xgf,

        "away_venue_xga":
            away_venue_xga,

        "home_sot10":
            home_sot10,

        "away_sot10":
            away_sot10,

        "expected_home_xg":
            expected_home_xg,

        "expected_away_xg":
            expected_away_xg,

        "expected_total_xg":
            expected_total_xg,

        "expected_xg_balance":
            expected_xg_balance,

        "home_xg_matches":
            home_xg_matches,

        "away_xg_matches":
            away_xg_matches,

        "home_sot_matches":
            home_sot_matches,

        "away_sot_matches":
            away_sot_matches,

        "data_quality":
            data_quality,
    }


# ============================================================
# FIXTURE FILE
# ============================================================

def load_fixtures():

    if not os.path.exists(
        FIXTURES_FILE
    ):

        raise FileNotFoundError(
            FIXTURES_FILE
        )

    try:

        fixtures = pd.read_csv(
            FIXTURES_FILE
        )

    except pd.errors.EmptyDataError:

        fixtures = pd.DataFrame(
            columns=[
                "fixture_id",
                "kickoff_utc",
                "home_team",
                "away_team",
            ]
        )

    required = {
        "fixture_id",
        "kickoff_utc",
        "home_team",
        "away_team",
    }

    missing = (
        required
        -
        set(
            fixtures.columns
        )
    )

    if missing:

        raise RuntimeError(
            "Missing fixture columns: "
            +
            ", ".join(
                sorted(
                    missing
                )
            )
        )

    return fixtures


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "=" * 80
    )

    print(
        "FOOTBALL AI — "
        "BTTS LIVE FEATURE ENGINE"
    )

    print(
        "=" * 80
    )

    # --------------------------------------------------------
    # BUILD ROLLING STATES
    # --------------------------------------------------------

    (
        xg_state,
        xg_matches
    ) = build_xg_state()

    (
        sot_state,
        sot_matches,
        active_odds_files
    ) = build_sot_state()

    print(
        "Historical + live xG matches:",
        len(
            xg_matches
        )
    )

    print(
        "Historical + live SOT matches:",
        len(
            sot_matches
        )
    )

    print(
        "xG teams:",
        len(
            xg_state
        )
    )

    print(
        "SOT teams:",
        len(
            sot_state
        )
    )

    print()

    print(
        "SOT files active:"
    )

    for season, filename in sorted(
        active_odds_files.items()
    ):

        print(
            f"  {season}: {filename}"
        )

    print()

    if os.path.exists(
        OPTIONAL_ODDS_FILES[
            2026
        ]
    ):

        print(
            "2026/27 SOT state: ACTIVE"
        )

    else:

        print(
            "2026/27 SOT state: NOT AVAILABLE YET"
        )

    # xG history itself is merged by download_xg.py.
    print(
        "xG state source:",
        XG_FILE
    )

    # --------------------------------------------------------
    # FUTURE FIXTURES
    # --------------------------------------------------------

    fixtures = load_fixtures()

    print()
    print(
        "Future fixtures:",
        len(
            fixtures
        )
    )

    rows = []

    for _, fixture in (
        fixtures.iterrows()
    ):

        fixture_id = (
            fixture[
                "fixture_id"
            ]
        )

        kickoff = (
            fixture[
                "kickoff_utc"
            ]
        )

        home_original = str(
            fixture[
                "home_team"
            ]
        ).strip()

        away_original = str(
            fixture[
                "away_team"
            ]
        ).strip()

        home = market_team(
            home_original
        )

        away = market_team(
            away_original
        )

        features = make_features(
            home,
            away,
            xg_state,
            sot_state
        )

        row = {

            "fixture_id":
                fixture_id,

            "kickoff_utc":
                kickoff,

            "home_team":
                home_original,

            "away_team":
                away_original,

            "home_team_model":
                home,

            "away_team_model":
                away,

            **features,
        }

        rows.append(
            row
        )

        print()

        print(
            f"{home_original} "
            f"vs "
            f"{away_original}"
        )

        print(
            "  xG history:",
            features[
                "home_xg_matches"
            ],
            "/",
            features[
                "away_xg_matches"
            ]
        )

        print(
            "  SOT history:",
            features[
                "home_sot_matches"
            ],
            "/",
            features[
                "away_sot_matches"
            ]
        )

        print(
            "  Expected xG:",
            f"{features['expected_home_xg']:.3f}",
            "-",
            f"{features['expected_away_xg']:.3f}"
        )

        print(
            "  Data quality:",
            features[
                "data_quality"
            ]
        )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    output = pd.DataFrame(
        rows,
        columns=OUTPUT_COLUMNS
    )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print()
    print(
        "=" * 80
    )

    print(
        "LIVE FEATURES READY"
    )

    print(
        "=" * 80
    )

    print(
        "Saved:",
        OUTPUT_FILE
    )

    print(
        "Fixtures:",
        len(
            output
        )
    )

    if len(
        output
    ):

        ok = int(
            (
                output[
                    "data_quality"
                ]
                ==
                "OK"
            ).sum()
        )

        insufficient = int(
            (
                output[
                    "data_quality"
                ]
                !=
                "OK"
            ).sum()
        )

        print(
            "Data quality OK:",
            ok
        )

        print(
            "Data quality INSUFFICIENT:",
            insufficient
        )

    print()

    print(
        "Minimum history required:",
        MIN_HISTORY_MATCHES
    )

    print(
        "API REQUESTS USED: 0"
    )

    print(
        "=" * 80
    )


if __name__ == "__main__":

    main()