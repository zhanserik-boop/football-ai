import csv
import io
import os
from datetime import datetime, timezone

import requests


# ============================================================
# CONFIG
# ============================================================

SEASON = 2026

SEASON_FILE = "epl_odds_2026.csv"

INVALID_FILE = "epl_odds_2026.invalid.csv"

URL = (
    "https://www.football-data.co.uk/"
    "mmz4281/2627/E0.csv"
)

TIMEOUT_SECONDS = 30

SEASON_START = datetime(
    2026,
    8,
    21,
    tzinfo=timezone.utc
).date()

SEASON_END = datetime(
    2027,
    5,
    30,
    tzinfo=timezone.utc
).date()


# ============================================================
# FOOTBALL-DATA EPL TEAM NAMES
# ============================================================

EPL_TEAMS = {
    "Arsenal",
    "Aston Villa",
    "Bournemouth",
    "Brentford",
    "Brighton",
    "Chelsea",
    "Coventry",
    "Crystal Palace",
    "Everton",
    "Fulham",
    "Hull",
    "Ipswich",
    "Leeds",
    "Liverpool",
    "Man City",
    "Man United",
    "Newcastle",
    "Nott'm Forest",
    "Sunderland",
    "Tottenham",
}


# ============================================================
# REQUIRED COLUMNS
# ============================================================

REQUIRED_COLUMNS = {
    "Date",
    "HomeTeam",
    "AwayTeam",
    "FTHG",
    "FTAG",
    "HST",
    "AST",
}


# ============================================================
# HELPERS
# ============================================================

def has_value(value):

    if value is None:
        return False

    text = str(
        value
    ).strip()

    if not text:
        return False

    if text.lower() in {
        "none",
        "nan",
        "null"
    }:
        return False

    return True


def safe_int(value):

    try:

        return int(
            float(
                value
            )
        )

    except Exception:

        return None


def parse_match_date(value):

    text = str(
        value
    ).strip()

    formats = [
        "%d/%m/%Y",
        "%d/%m/%y",
    ]

    for fmt in formats:

        try:

            return datetime.strptime(
                text,
                fmt
            ).date()

        except ValueError:
            continue

    return None


# ============================================================
# READ / VALIDATE CSV
# ============================================================

def analyse_csv(text):

    try:

        reader = csv.DictReader(
            io.StringIO(
                text
            )
        )

        columns = set(
            reader.fieldnames
            or
            []
        )

        missing = (
            REQUIRED_COLUMNS
            -
            columns
        )

        if missing:

            return {
                "valid_league": False,
                "reason": (
                    "Missing required columns: "
                    +
                    ", ".join(
                        sorted(
                            missing
                        )
                    )
                ),
                "rows": [],
                "completed": [],
                "teams": set(),
            }

        rows = list(
            reader
        )

        if not rows:

            return {
                "valid_league": False,
                "reason": "CSV contains no match rows",
                "rows": [],
                "completed": [],
                "teams": set(),
            }

        teams = set()

        bad_team_rows = []

        valid_season_rows = []

        completed = []

        today_utc = datetime.now(
            timezone.utc
        ).date()

        for row in rows:

            home = str(
                row.get(
                    "HomeTeam",
                    ""
                )
            ).strip()

            away = str(
                row.get(
                    "AwayTeam",
                    ""
                )
            ).strip()

            if home:
                teams.add(
                    home
                )

            if away:
                teams.add(
                    away
                )

            # ------------------------------------------------
            # LEAGUE IDENTITY CHECK
            # ------------------------------------------------

            if (
                home not in EPL_TEAMS
                or
                away not in EPL_TEAMS
            ):

                bad_team_rows.append(
                    (
                        home,
                        away
                    )
                )

                continue

            # ------------------------------------------------
            # DATE CHECK
            # ------------------------------------------------

            match_date = parse_match_date(
                row.get(
                    "Date",
                    ""
                )
            )

            if match_date is None:
                continue

            if (
                match_date < SEASON_START
                or
                match_date > SEASON_END
            ):
                continue

            valid_season_rows.append(
                row
            )

            # ------------------------------------------------
            # FUTURE MATCH PROTECTION
            # ------------------------------------------------

            if match_date > today_utc:
                continue

            # ------------------------------------------------
            # COMPLETED MATCH CHECK
            # ------------------------------------------------

            home_goals = safe_int(
                row.get(
                    "FTHG"
                )
            )

            away_goals = safe_int(
                row.get(
                    "FTAG"
                )
            )

            home_sot = safe_int(
                row.get(
                    "HST"
                )
            )

            away_sot = safe_int(
                row.get(
                    "AST"
                )
            )

            if (
                home_goals is None
                or
                away_goals is None
                or
                home_sot is None
                or
                away_sot is None
            ):
                continue

            completed.append(
                row
            )

        # ====================================================
        # STRICT COMPETITION PROTECTION
        #
        # If even one populated match contains clubs outside
        # the official EPL 2026/27 club set, we reject the
        # entire source instead of filtering foreign rows.
        # ====================================================

        if bad_team_rows:

            examples = bad_team_rows[
                :5
            ]

            example_text = "; ".join(
                f"{home} vs {away}"
                for home, away in examples
            )

            return {
                "valid_league": False,
                "reason": (
                    "Source contains non-EPL 2026/27 teams: "
                    +
                    example_text
                ),
                "rows": rows,
                "completed": [],
                "teams": teams,
            }

        # ====================================================
        # DATE RANGE PROTECTION
        # ====================================================

        if not valid_season_rows:

            return {
                "valid_league": False,
                "reason": (
                    "No fixtures inside official "
                    "2026/27 EPL season date range"
                ),
                "rows": rows,
                "completed": [],
                "teams": teams,
            }

        return {
            "valid_league": True,
            "reason": "",
            "rows": rows,
            "completed": completed,
            "teams": teams,
        }

    except Exception as exc:

        return {
            "valid_league": False,
            "reason": repr(
                exc
            ),
            "rows": [],
            "completed": [],
            "teams": set(),
        }


# ============================================================
# DECODE RESPONSE
# ============================================================

def decode_response(response):

    try:

        return response.content.decode(
            "utf-8-sig"
        )

    except UnicodeDecodeError:

        return response.content.decode(
            "latin-1"
        )


# ============================================================
# EXISTING TARGET PROTECTION
# ============================================================

def quarantine_existing_invalid_file():

    if not os.path.exists(
        SEASON_FILE
    ):
        return

    try:

        with open(
            SEASON_FILE,
            "rb"
        ) as file:

            content = file.read()

        try:

            text = content.decode(
                "utf-8-sig"
            )

        except UnicodeDecodeError:

            text = content.decode(
                "latin-1"
            )

        analysis = analyse_csv(
            text
        )

        if analysis[
            "valid_league"
        ]:

            print(
                "Existing season file passes EPL identity check."
            )

            return

        print()
        print(
            "Existing season file is INVALID:"
        )

        print(
            analysis[
                "reason"
            ]
        )

        if os.path.exists(
            INVALID_FILE
        ):

            os.remove(
                INVALID_FILE
            )

        os.replace(
            SEASON_FILE,
            INVALID_FILE
        )

        print(
            "Moved invalid file to:",
            INVALID_FILE
        )

    except Exception as exc:

        raise RuntimeError(
            "Could not validate/quarantine existing "
            f"{SEASON_FILE}: {exc}"
        )


# ============================================================
# ATOMIC SAVE
# ============================================================

def save_atomic(
    content
):

    temp_file = (
        SEASON_FILE
        +
        ".tmp"
    )

    with open(
        temp_file,
        "wb"
    ) as file:

        file.write(
            content
        )

    os.replace(
        temp_file,
        SEASON_FILE
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "=" * 80
    )

    print(
        "FOOTBALL AI — EPL 2026/27 SOT UPDATE"
    )

    print(
        "=" * 80
    )

    print(
        "Source: Football-Data"
    )

    print(
        "Season: 2026/27"
    )

    print(
        "Official season start:",
        SEASON_START
    )

    print(
        "Target:",
        SEASON_FILE
    )

    # ========================================================
    # CLEAN UP PREVIOUS INVALID DOWNLOAD
    # ========================================================

    quarantine_existing_invalid_file()

    # ========================================================
    # DOWNLOAD
    # ========================================================

    print()
    print(
        "Checking Football-Data source..."
    )

    try:

        response = requests.get(
            URL,
            timeout=TIMEOUT_SECONDS
        )

    except Exception as exc:

        print(
            "DOWNLOAD ERROR:",
            repr(
                exc
            )
        )

        print(
            "No valid season file written."
        )

        print(
            "API-FOOTBALL REQUESTS USED: 0"
        )

        return

    print(
        "HTTP:",
        response.status_code
    )

    if response.status_code != 200:

        print()
        print(
            "Football-Data EPL 2026/27 "
            "source is not available yet."
        )

        print(
            "No valid season file written."
        )

        print(
            "API-FOOTBALL REQUESTS USED: 0"
        )

        return

    # ========================================================
    # DECODE
    # ========================================================

    try:

        text = decode_response(
            response
        )

    except Exception as exc:

        print(
            "DECODE ERROR:",
            repr(
                exc
            )
        )

        print(
            "No valid season file written."
        )

        print(
            "API-FOOTBALL REQUESTS USED: 0"
        )

        return

    # ========================================================
    # VALIDATE COMPETITION
    # ========================================================

    analysis = analyse_csv(
        text
    )

    print(
        "Teams found:",
        len(
            analysis[
                "teams"
            ]
        )
    )

    if analysis[
        "teams"
    ]:

        print(
            "Sample teams:",
            ", ".join(
                sorted(
                    analysis[
                        "teams"
                    ]
                )[:10]
            )
        )

    if not analysis[
        "valid_league"
    ]:

        print()
        print(
            "=" * 80
        )

        print(
            "SOURCE REJECTED"
        )

        print(
            "=" * 80
        )

        print(
            "Reason:",
            analysis[
                "reason"
            ]
        )

        print()
        print(
            "This URL is currently NOT treated "
            "as EPL 2026/27 data."
        )

        print(
            "No valid season file written."
        )

        print(
            "API-FOOTBALL REQUESTS USED: 0"
        )

        return

    # ========================================================
    # VALID EPL SOURCE FOUND
    # ========================================================

    completed = analysis[
        "completed"
    ]

    print()
    print(
        "Valid EPL source detected."
    )

    print(
        "Completed matches with HST/AST:",
        len(
            completed
        )
    )

    # ========================================================
    # BEFORE SEASON / NO COMPLETED MATCHES
    #
    # Even if Football-Data begins publishing the real EPL
    # fixture CSV before kickoff, we do not save it into the
    # rolling SOT input until at least one real completed
    # match with HST/AST exists.
    # ========================================================

    if len(
        completed
    ) == 0:

        print()
        print(
            "=" * 80
        )

        print(
            "NO COMPLETED EPL 2026/27 SOT MATCHES"
        )

        print(
            "=" * 80
        )

        print(
            "No valid season file written."
        )

        print(
            "API-FOOTBALL REQUESTS USED: 0"
        )

        return

    # ========================================================
    # SAVE REAL EPL CSV
    # ========================================================

    save_atomic(
        response.content
    )

    print()
    print(
        "=" * 80
    )

    print(
        "SOT UPDATE COMPLETE"
    )

    print(
        "=" * 80
    )

    print(
        "Competition identity: EPL 2026/27"
    )

    print(
        "Completed matches with SOT:",
        len(
            completed
        )
    )

    print(
        "Saved:",
        SEASON_FILE
    )

    print(
        "API-FOOTBALL REQUESTS USED: 0"
    )

    print(
        "=" * 80
    )


if __name__ == "__main__":

    main()