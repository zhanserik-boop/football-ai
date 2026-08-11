import csv
import os
from datetime import datetime

from understatapi import UnderstatClient


# ============================================================
# CONFIG
# ============================================================

SEASON = 2026

HISTORY_FILE = "epl_xg_history.csv"

SEASON_FILE = "epl_xg_2026.csv"

FIELDNAMES = [
    "season",
    "date",
    "home_team",
    "away_team",
    "home_goals",
    "away_goals",
    "home_xg",
    "away_xg",
]


# ============================================================
# HELPERS
# ============================================================

def has_value(value):

    if value is None:
        return False

    text = str(
        value
    ).strip()

    if text == "":
        return False

    if text.lower() in {
        "none",
        "nan",
        "null"
    }:
        return False

    return True


def safe_float(value):

    try:
        return float(
            value
        )

    except Exception:
        return None


def safe_int(value):

    try:
        return int(
            float(
                value
            )
        )

    except Exception:
        return None


def match_key(row):

    return (
        str(
            row.get(
                "season",
                ""
            )
        ).strip(),

        str(
            row.get(
                "date",
                ""
            )
        ).strip(),

        str(
            row.get(
                "home_team",
                ""
            )
        ).strip(),

        str(
            row.get(
                "away_team",
                ""
            )
        ).strip(),
    )


def date_sort_key(row):

    value = str(
        row.get(
            "date",
            ""
        )
    ).strip()

    try:

        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00"
            )
        )

    except Exception:

        # Safe fallback for unexpected date strings.
        return datetime.min


def write_csv_atomic(
    filename,
    rows
):

    temp_file = (
        filename
        +
        ".tmp"
    )

    with open(
        temp_file,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=FIELDNAMES
        )

        writer.writeheader()

        writer.writerows(
            rows
        )

    os.replace(
        temp_file,
        filename
    )


# ============================================================
# LOAD EXISTING HISTORY
# ============================================================

def load_history():

    if not os.path.exists(
        HISTORY_FILE
    ):

        raise FileNotFoundError(
            f"Missing historical xG database: "
            f"{HISTORY_FILE}"
        )

    rows = []

    with open(
        HISTORY_FILE,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.DictReader(
            file
        )

        missing = (
            set(
                FIELDNAMES
            )
            -
            set(
                reader.fieldnames
                or
                []
            )
        )

        if missing:

            raise RuntimeError(
                "Historical xG file is missing columns: "
                +
                ", ".join(
                    sorted(
                        missing
                    )
                )
            )

        for row in reader:

            rows.append({

                field:
                    row.get(
                        field,
                        ""
                    )

                for field in FIELDNAMES
            })

    return rows


# ============================================================
# DOWNLOAD CURRENT SEASON
# ============================================================

def download_current_season():

    print()
    print(
        "=" * 80
    )

    print(
        "FOOTBALL AI — EPL xG UPDATE"
    )

    print(
        "=" * 80
    )

    print(
        "Understat season:",
        SEASON
    )

    with UnderstatClient() as understat:

        matches = (
            understat
            .league(
                league="EPL"
            )
            .get_match_data(
                season=SEASON
            )
        )

    print(
        "Understat matches returned:",
        len(
            matches
        )
    )

    completed = []

    skipped = 0

    for match in matches:

        try:

            date = match.get(
                "datetime"
            )

            home = (
                match.get(
                    "h",
                    {}
                )
                .get(
                    "title"
                )
            )

            away = (
                match.get(
                    "a",
                    {}
                )
                .get(
                    "title"
                )
            )

            goals = match.get(
                "goals",
                {}
            )

            xg = match.get(
                "xG",
                {}
            )

            home_goals = goals.get(
                "h"
            )

            away_goals = goals.get(
                "a"
            )

            home_xg = xg.get(
                "h"
            )

            away_xg = xg.get(
                "a"
            )

            required = [
                date,
                home,
                away,
                home_goals,
                away_goals,
                home_xg,
                away_xg,
            ]

            if not all(
                has_value(
                    value
                )
                for value in required
            ):

                skipped += 1
                continue

            hg = safe_int(
                home_goals
            )

            ag = safe_int(
                away_goals
            )

            hxg = safe_float(
                home_xg
            )

            axg = safe_float(
                away_xg
            )

            if (
                hg is None
                or
                ag is None
                or
                hxg is None
                or
                axg is None
            ):

                skipped += 1
                continue

            # A future fixture should not have completed
            # goals + xG. Requiring all four prevents
            # scheduled matches from entering rolling state.

            completed.append({

                "season":
                    str(
                        SEASON
                    ),

                "date":
                    str(
                        date
                    ).strip(),

                "home_team":
                    str(
                        home
                    ).strip(),

                "away_team":
                    str(
                        away
                    ).strip(),

                "home_goals":
                    hg,

                "away_goals":
                    ag,

                "home_xg":
                    hxg,

                "away_xg":
                    axg,
            })

        except Exception:

            skipped += 1

    # Deduplicate inside the current Understat response too.

    unique = {}

    for row in completed:

        unique[
            match_key(
                row
            )
        ] = row

    completed = list(
        unique.values()
    )

    completed.sort(
        key=date_sort_key
    )

    print(
        "Completed matches with xG:",
        len(
            completed
        )
    )

    print(
        "Skipped incomplete/future matches:",
        skipped
    )

    return completed


# ============================================================
# MERGE INTO HISTORY
# ============================================================

def merge_history(
    historical,
    current
):

    merged = {}

    # Existing historical database goes first.

    for row in historical:

        merged[
            match_key(
                row
            )
        ] = row

    before_keys = set(
        merged.keys()
    )

    added = 0

    replaced = 0

    # Current-season Understat data goes second and therefore
    # has priority over an existing copy of the same match.

    for row in current:

        key = match_key(
            row
        )

        if key in before_keys:

            if merged[
                key
            ] != row:

                replaced += 1

        else:

            added += 1

        merged[
            key
        ] = row

    rows = list(
        merged.values()
    )

    rows.sort(
        key=date_sort_key
    )

    return (
        rows,
        added,
        replaced
    )


# ============================================================
# MAIN
# ============================================================

def main():

    historical = load_history()

    print(
        "Historical database before update:",
        len(
            historical
        )
    )

    current = (
        download_current_season()
    )

    # ========================================================
    # CRITICAL SAFETY RULE
    #
    # If Understat has not published 2026/27 yet,
    # DO NOT TOUCH either historical or season CSV.
    # ========================================================

    if len(
        current
    ) == 0:

        print()
        print(
            "=" * 80
        )

        print(
            "NO COMPLETED 2026/27 xG MATCHES AVAILABLE"
        )

        print(
            "=" * 80
        )

        print(
            "No files changed."
        )

        print(
            "Historical database remains:",
            len(
                historical
            ),
            "matches"
        )

        print(
            "API-FOOTBALL REQUESTS USED: 0"
        )

        return

    # ========================================================
    # SAVE CURRENT SEASON
    # ========================================================

    write_csv_atomic(
        SEASON_FILE,
        current
    )

    # ========================================================
    # MERGE CURRENT SEASON INTO FULL HISTORY
    # ========================================================

    (
        merged,
        added,
        replaced
    ) = merge_history(
        historical,
        current
    )

    if len(
        merged
    ) < len(
        historical
    ):

        raise RuntimeError(
            "Safety stop: merged xG history "
            "would be smaller than existing history."
        )

    write_csv_atomic(
        HISTORY_FILE,
        merged
    )

    # ========================================================
    # SUMMARY
    # ========================================================

    print()
    print(
        "=" * 80
    )

    print(
        "XG UPDATE COMPLETE"
    )

    print(
        "=" * 80
    )

    print(
        "Current season completed matches:",
        len(
            current
        )
    )

    print(
        "New matches added:",
        added
    )

    print(
        "Existing matches refreshed:",
        replaced
    )

    print(
        "Historical database before:",
        len(
            historical
        )
    )

    print(
        "Historical database after:",
        len(
            merged
        )
    )

    print(
        "Season file:",
        SEASON_FILE
    )

    print(
        "History file:",
        HISTORY_FILE
    )

    print(
        "API-FOOTBALL REQUESTS USED: 0"
    )

    print(
        "=" * 80
    )


if __name__ == "__main__":

    main()