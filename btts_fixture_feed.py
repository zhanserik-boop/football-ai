import json
import os
from datetime import datetime, timezone

import pandas as pd


# ============================================================
# CONFIG
# ============================================================

STATE_FILE = "market_monitor_v2_state.json"
OUTPUT_FILE = "btts_live_fixtures.csv"

MAX_HOURS_TO_KICKOFF = 48


# ============================================================
# HELPERS
# ============================================================

def utc_now():
    return datetime.now(
        timezone.utc
    )


def parse_dt(value):

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
# MAIN
# ============================================================

def main():

    print()
    print("=" * 80)
    print("FOOTBALL AI — BTTS FIXTURE FEED")
    print("=" * 80)


    if not os.path.exists(
        STATE_FILE
    ):

        print(
            "No market monitor state yet:"
        )

        print(
            STATE_FILE
        )

        print()
        print(
            "Nothing to update."
        )

        print(
            "API REQUESTS USED: 0"
        )

        return


    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            state = json.load(f)

    except Exception as exc:

        print(
            "Could not read state:",
            repr(exc)
        )

        print(
            "API REQUESTS USED: 0"
        )

        return


    fixtures = state.get(
        "fixtures",
        {}
    )


    now = utc_now()

    rows = []


    for fixture_id, item in fixtures.items():

        kickoff = parse_dt(
            item.get(
                "kickoff"
            )
        )

        if kickoff is None:
            continue


        hours_to_kickoff = (
            kickoff
            -
            now
        ).total_seconds() / 3600.0


        if not (
            0
            <
            hours_to_kickoff
            <=
            MAX_HOURS_TO_KICKOFF
        ):

            continue


        try:

            fixture_id_int = int(
                float(
                    fixture_id
                )
            )

        except Exception:
            continue


        home_team = str(
            item.get(
                "home_team",
                ""
            )
        ).strip()


        away_team = str(
            item.get(
                "away_team",
                ""
            )
        ).strip()


        if (
            not home_team
            or
            not away_team
        ):
            continue


        rows.append({

            "fixture_id":
                fixture_id_int,

            "kickoff_utc":
                kickoff.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),

            "home_team":
                home_team,

            "away_team":
                away_team,

            "hours_to_kickoff":
                round(
                    hours_to_kickoff,
                    2
                ),
        })


    output = pd.DataFrame(
        rows
    )


    if len(output):

        output = (
            output
            .sort_values(
                [
                    "kickoff_utc",
                    "fixture_id",
                ]
            )
            .drop_duplicates(
                subset=[
                    "fixture_id"
                ],
                keep="last"
            )
            .reset_index(
                drop=True
            )
        )


    output.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )


    print(
        "Fixtures in monitor state:",
        len(fixtures)
    )

    print(
        "Future fixtures inside 48h:",
        len(output)
    )


    if len(output):

        print()
        print("FIXTURES")

        for _, row in (
            output.iterrows()
        ):

            print(
                f"{row['fixture_id']} | "
                f"{row['home_team']} "
                f"vs "
                f"{row['away_team']} | "
                f"{row['kickoff_utc']} | "
                f"T-{row['hours_to_kickoff']:.1f}h"
            )


    print()
    print(
        "Saved:",
        OUTPUT_FILE
    )

    print(
        "API REQUESTS USED: 0"
    )

    print("=" * 80)


if __name__ == "__main__":

    main()