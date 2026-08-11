import os
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

API_KEY = (
    os.getenv("API_FOOTBALL_KEY")
    or os.getenv("API_KEY")
    or os.getenv("APISPORTS_KEY")
)

if not API_KEY:
    raise RuntimeError("API-Football key not found in .env")

BASE_URL = "https://v3.football.api-sports.io"

HEADERS = {
    "x-apisports-key": API_KEY
}

LEAGUE_ID = 39
SEASON = 2026

SQUADS_FILE = "current_squads_2026.csv"
TRANSFERS_FILE = "transfers_2026.csv"
STATE_FILE = "squad_transfer_update_state.json"

REQUEST_PAUSE_SECONDS = 0.15


# ============================================================
# HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def api_get(endpoint, params=None):
    response = requests.get(
        BASE_URL + endpoint,
        headers=HEADERS,
        params=params or {},
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    errors = data.get("errors")

    if errors:
        raise RuntimeError(
            f"API error {endpoint}: {errors}"
        )

    return data


def atomic_write_csv(filename, fieldnames, rows):
    target = Path(filename)
    temp = target.with_suffix(
        target.suffix + ".tmp"
    )

    with open(
        temp,
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )

        writer.writeheader()
        writer.writerows(rows)

    os.replace(temp, target)


def save_state(data):
    temp = STATE_FILE + ".tmp"

    with open(
        temp,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(
        temp,
        STATE_FILE,
    )


# ============================================================
# EPL TEAMS
# ============================================================

def get_epl_teams():
    data = api_get(
        "/teams",
        {
            "league": LEAGUE_ID,
            "season": SEASON,
        },
    )

    rows = []

    for item in data.get(
        "response",
        []
    ):
        team = item.get(
            "team",
            {}
        )

        team_id = team.get("id")
        team_name = team.get("name")

        if not team_id or not team_name:
            continue

        rows.append({
            "team_id": int(team_id),
            "team_name": str(team_name),
        })

    rows.sort(
        key=lambda x:
            x["team_name"]
    )

    return rows


# ============================================================
# CURRENT SQUAD
# ============================================================

def get_team_squad(
    team_id,
    fallback_team_name,
):

    data = api_get(
        "/players/squads",
        {
            "team": team_id
        },
    )

    output = []

    for block in data.get(
        "response",
        []
    ):

        team = block.get(
            "team",
            {}
        )

        api_team_id = (
            team.get("id")
            or team_id
        )

        team_name = (
            team.get("name")
            or fallback_team_name
        )

        for player in block.get(
            "players",
            []
        ):

            player_id = player.get("id")

            if not player_id:
                continue

            output.append({
                "snapshot_utc":
                    utc_now().isoformat(),

                "season":
                    SEASON,

                "league_id":
                    LEAGUE_ID,

                "team_id":
                    api_team_id,

                "team_name":
                    team_name,

                "player_id":
                    player_id,

                "player_name":
                    player.get(
                        "name",
                        ""
                    ),

                "age":
                    player.get(
                        "age",
                        ""
                    ),

                "number":
                    player.get(
                        "number",
                        ""
                    ),

                "position":
                    player.get(
                        "position",
                        ""
                    ),
            })

    return output


# ============================================================
# TRANSFERS
# ============================================================

def get_team_transfers(
    team_id,
    team_name,
):

    data = api_get(
        "/transfers",
        {
            "team": team_id
        },
    )

    output = []

    for item in data.get(
        "response",
        []
    ):

        player = item.get(
            "player",
            {}
        )

        player_id = player.get("id")

        if not player_id:
            continue

        for transfer in item.get(
            "transfers",
            []
        ):

            teams = transfer.get(
                "teams",
                {}
            )

            team_out = teams.get(
                "out",
                {}
            ) or {}

            team_in = teams.get(
                "in",
                {}
            ) or {}

            out_id = team_out.get("id")
            in_id = team_in.get("id")

            direction = "OTHER"

            if str(in_id) == str(team_id):
                direction = "IN"

            elif str(out_id) == str(team_id):
                direction = "OUT"

            output.append({
                "snapshot_utc":
                    utc_now().isoformat(),

                "team_id":
                    team_id,

                "team_name":
                    team_name,

                "player_id":
                    player_id,

                "player_name":
                    player.get(
                        "name",
                        ""
                    ),

                "transfer_date":
                    transfer.get(
                        "date",
                        ""
                    ),

                "transfer_type":
                    transfer.get(
                        "type",
                        ""
                    ),

                "direction":
                    direction,

                "from_team_id":
                    out_id or "",

                "from_team":
                    team_out.get(
                        "name",
                        ""
                    ),

                "to_team_id":
                    in_id or "",

                "to_team":
                    team_in.get(
                        "name",
                        ""
                    ),
            })

    return output


# ============================================================
# VALIDATION
# ============================================================

def validate_squads(
    teams,
    squad_rows,
):

    expected_team_ids = {
        str(x["team_id"])
        for x in teams
    }

    actual_team_ids = {
        str(x["team_id"])
        for x in squad_rows
    }

    missing = (
        expected_team_ids
        -
        actual_team_ids
    )

    if missing:
        missing_names = [
            x["team_name"]
            for x in teams
            if str(
                x["team_id"]
            ) in missing
        ]

        raise RuntimeError(
            "Squad data missing for: "
            +
            ", ".join(
                missing_names
            )
        )

    players_by_team = {}

    for row in squad_rows:

        name = row["team_name"]

        players_by_team.setdefault(
            name,
            0
        )

        players_by_team[name] += 1

    suspicious = {
        team:
            count

        for team, count
        in players_by_team.items()

        if count < 15
    }

    if suspicious:
        raise RuntimeError(
            "Suspiciously small squads: "
            +
            str(suspicious)
        )

    return players_by_team


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 72)
    print("FOOTBALL AI — CURRENT SQUAD / TRANSFER UPDATE")
    print("=" * 72)

    api_requests = 0

    # --------------------------------------------------------
    # TEAMS
    # --------------------------------------------------------

    print()
    print("Loading EPL 2026/27 teams...")

    teams = get_epl_teams()
    api_requests += 1

    print(
        "Teams returned:",
        len(teams)
    )

    if len(teams) != 20:

        raise RuntimeError(
            "Expected 20 EPL teams, "
            f"got {len(teams)}. "
            "Existing files were NOT changed."
        )

    print()

    for team in teams:
        print(
            " ",
            team["team_id"],
            team["team_name"]
        )

    # --------------------------------------------------------
    # SQUADS
    # --------------------------------------------------------

    all_squads = []

    print()
    print("=" * 72)
    print("CURRENT SQUADS")
    print("=" * 72)

    for index, team in enumerate(
        teams,
        start=1,
    ):

        team_id = team["team_id"]
        team_name = team["team_name"]

        print(
            f"[{index:02d}/20] "
            f"{team_name} ... ",
            end="",
            flush=True,
        )

        rows = get_team_squad(
            team_id,
            team_name,
        )

        api_requests += 1

        all_squads.extend(
            rows
        )

        print(
            f"{len(rows)} players"
        )

        time.sleep(
            REQUEST_PAUSE_SECONDS
        )

    players_by_team = (
        validate_squads(
            teams,
            all_squads,
        )
    )

    # --------------------------------------------------------
    # TRANSFERS
    # --------------------------------------------------------

    all_transfers = []

    print()
    print("=" * 72)
    print("TRANSFERS")
    print("=" * 72)

    for index, team in enumerate(
        teams,
        start=1,
    ):

        team_id = team["team_id"]
        team_name = team["team_name"]

        print(
            f"[{index:02d}/20] "
            f"{team_name} ... ",
            end="",
            flush=True,
        )

        rows = get_team_transfers(
            team_id,
            team_name,
        )

        api_requests += 1

        all_transfers.extend(
            rows
        )

        print(
            f"{len(rows)} transfer records"
        )

        time.sleep(
            REQUEST_PAUSE_SECONDS
        )

    # --------------------------------------------------------
    # DEDUPE SQUADS
    # --------------------------------------------------------

    squad_unique = {}

    for row in all_squads:

        key = (
            str(row["team_id"]),
            str(row["player_id"]),
        )

        squad_unique[key] = row

    all_squads = list(
        squad_unique.values()
    )

    all_squads.sort(
        key=lambda x: (
            str(x["team_name"]),
            str(x["position"]),
            str(x["player_name"]),
        )
    )

    # --------------------------------------------------------
    # DEDUPE TRANSFERS
    # --------------------------------------------------------

    transfer_unique = {}

    for row in all_transfers:

        key = (
            str(row["player_id"]),
            str(row["transfer_date"]),
            str(row["from_team_id"]),
            str(row["to_team_id"]),
            str(row["transfer_type"]),
        )

        transfer_unique[key] = row

    all_transfers = list(
        transfer_unique.values()
    )

    all_transfers.sort(
        key=lambda x: (
            str(x["transfer_date"]),
            str(x["player_name"]),
        ),
        reverse=True,
    )

    # --------------------------------------------------------
    # WRITE ONLY AFTER COMPLETE VALIDATION
    # --------------------------------------------------------

    squad_fields = [
        "snapshot_utc",
        "season",
        "league_id",
        "team_id",
        "team_name",
        "player_id",
        "player_name",
        "age",
        "number",
        "position",
    ]

    transfer_fields = [
        "snapshot_utc",
        "team_id",
        "team_name",
        "player_id",
        "player_name",
        "transfer_date",
        "transfer_type",
        "direction",
        "from_team_id",
        "from_team",
        "to_team_id",
        "to_team",
    ]

    atomic_write_csv(
        SQUADS_FILE,
        squad_fields,
        all_squads,
    )

    atomic_write_csv(
        TRANSFERS_FILE,
        transfer_fields,
        all_transfers,
    )

    state = {
        "last_successful_update_utc":
            utc_now().isoformat(),

        "league_id":
            LEAGUE_ID,

        "season":
            SEASON,

        "teams":
            len(teams),

        "current_players":
            len(all_squads),

        "transfer_records":
            len(all_transfers),

        "api_requests_used":
            api_requests,
    }

    save_state(
        state
    )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    print()
    print("=" * 72)
    print("UPDATE COMPLETE")
    print("=" * 72)

    print(
        "EPL teams:",
        len(teams)
    )

    print(
        "Current players:",
        len(all_squads)
    )

    print(
        "Transfer records:",
        len(all_transfers)
    )

    print(
        "API requests used:",
        api_requests
    )

    print()
    print(
        "Saved:",
        SQUADS_FILE
    )

    print(
        "Saved:",
        TRANSFERS_FILE
    )

    print(
        "State:",
        STATE_FILE
    )

    print()
    print("Players by team:")

    for team_name in sorted(
        players_by_team
    ):

        print(
            f"  {team_name}: "
            f"{players_by_team[team_name]}"
        )

    print("=" * 72)


if __name__ == "__main__":
    main()
