import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv


load_dotenv()

API_KEY = (
    os.getenv("API_FOOTBALL_KEY")
    or os.getenv("API_KEY")
    or os.getenv("APISPORTS_KEY")
)

BASE_URL = "https://v3.football.api-sports.io"
LINEUPS_FILE = "epl_lineups_4seasons.csv"
OUTPUT_FILE = "epl_coach_history.csv"
STATE_FILE = "historical_coach_builder_state.json"
DEFAULT_BATCH_SIZE = 5
REQUEST_PAUSE_SECONDS = 0.15
FILE_REPLACE_RETRIES = 20
FILE_REPLACE_RETRY_SECONDS = 0.5

OUTPUT_FIELDS = [
    "collected_utc",
    "season",
    "fixture_id",
    "date",
    "match_home",
    "match_away",
    "team_id",
    "team",
    "coach_id",
    "coach",
    "formation",
    "coach_status",
]


def utc_now():
    return datetime.now(timezone.utc)


def clean(value):
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "none", "nan", "null"}:
        return ""
    return text


def load_fixture_catalog(filename=LINEUPS_FILE):
    fixtures = {}
    with open(filename, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            fixture_id = clean(row.get("fixture_id"))
            if not fixture_id:
                continue
            item = fixtures.setdefault(
                fixture_id,
                {
                    "season": clean(row.get("season")),
                    "fixture_id": fixture_id,
                    "date": clean(row.get("date")),
                    "match_home": clean(row.get("match_home")),
                    "match_away": clean(row.get("match_away")),
                    "team_ids": set(),
                },
            )
            team_id = clean(row.get("team_id"))
            if team_id:
                item["team_ids"].add(team_id)
    return fixtures


def load_existing(filename=OUTPUT_FILE):
    rows = []
    if not os.path.exists(filename) or os.path.getsize(filename) == 0:
        return rows
    with open(filename, "r", encoding="utf-8-sig", newline="") as f:
        rows.extend(csv.DictReader(f))
    return rows


def completed_fixture_ids(rows, retry_unavailable=False):
    by_fixture = {}
    for row in rows:
        fixture_id = clean(row.get("fixture_id"))
        if not fixture_id:
            continue
        by_fixture.setdefault(fixture_id, []).append(row)

    completed = set()
    for fixture_id, fixture_rows in by_fixture.items():
        if len(fixture_rows) < 2:
            continue
        statuses = {clean(r.get("coach_status")).upper() for r in fixture_rows}
        if retry_unavailable and "UNAVAILABLE" in statuses:
            continue
        completed.add(fixture_id)
    return completed


def api_get(endpoint, params=None):
    if not API_KEY:
        raise RuntimeError("API-Football key not found in .env")
    response = requests.get(
        BASE_URL + endpoint,
        headers={"x-apisports-key": API_KEY},
        params=params or {},
        timeout=45,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(f"API error {endpoint}: {data['errors']}")
    return data


def parse_fixture_item(item, catalog_item, collected_utc):
    fixture = item.get("fixture") or {}
    fixture_id = clean(fixture.get("id")) or catalog_item["fixture_id"]
    teams = item.get("teams") or {}
    lineups = item.get("lineups") or []

    lineup_by_team = {}
    for lineup in lineups:
        team = lineup.get("team") or {}
        team_id = clean(team.get("id"))
        if team_id:
            lineup_by_team[team_id] = lineup

    output = []
    for side in ("home", "away"):
        team = teams.get(side) or {}
        team_id = clean(team.get("id"))
        team_name = clean(team.get("name"))
        if not team_id:
            continue

        lineup = lineup_by_team.get(team_id) or {}
        coach = lineup.get("coach") or {}
        coach_id = clean(coach.get("id"))
        coach_name = clean(coach.get("name"))
        formation = clean(lineup.get("formation"))
        status = "OK" if coach_id or coach_name else "UNAVAILABLE"

        output.append(
            {
                "collected_utc": collected_utc,
                "season": catalog_item["season"],
                "fixture_id": fixture_id,
                "date": catalog_item["date"],
                "match_home": catalog_item["match_home"],
                "match_away": catalog_item["match_away"],
                "team_id": team_id,
                "team": team_name,
                "coach_id": coach_id,
                "coach": coach_name,
                "formation": formation,
                "coach_status": status,
            }
        )
    return output


def parse_batch_response(data, catalog, requested_ids, collected_utc):
    parsed = []
    seen = set()
    for item in data.get("response", []):
        fixture_id = clean((item.get("fixture") or {}).get("id"))
        if not fixture_id or fixture_id not in catalog:
            continue
        seen.add(fixture_id)
        parsed.extend(parse_fixture_item(item, catalog[fixture_id], collected_utc))
    return parsed, seen


def placeholder_rows(catalog_item, collected_utc):
    return [
        {
            "collected_utc": collected_utc,
            "season": catalog_item["season"],
            "fixture_id": catalog_item["fixture_id"],
            "date": catalog_item["date"],
            "match_home": catalog_item["match_home"],
            "match_away": catalog_item["match_away"],
            "team_id": "",
            "team": side,
            "coach_id": "",
            "coach": "",
            "formation": "",
            "coach_status": "UNAVAILABLE",
        }
        for side in (catalog_item["match_home"], catalog_item["match_away"])
    ]


def replace_file_with_retry(temp, target):
    temp = Path(temp)
    target = Path(target)
    last_error = None
    for attempt in range(1, FILE_REPLACE_RETRIES + 1):
        try:
            os.replace(temp, target)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt == 1:
                print(
                    f"Output file is temporarily locked: {target}. "
                    "Retrying automatically..."
                )
            if attempt < FILE_REPLACE_RETRIES:
                time.sleep(FILE_REPLACE_RETRY_SECONDS)
    raise RuntimeError(
        f"Cannot replace {target} after {FILE_REPLACE_RETRIES} retries. "
        "Close the CSV/JSON file in Excel or another program and run the builder again; "
        "completed batches already saved will not be downloaded again."
    ) from last_error


def atomic_write_csv(rows, filename=OUTPUT_FILE):
    target = Path(filename)
    temp = target.with_suffix(target.suffix + ".tmp")
    with open(temp, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    replace_file_with_retry(temp, target)


def save_state(payload, filename=STATE_FILE):
    target = Path(filename)
    temp = target.with_suffix(target.suffix + ".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    replace_file_with_retry(temp, target)


def chunks(values, size):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def replace_fixture_rows(existing_rows, new_rows, fixture_ids):
    fixture_ids = {str(x) for x in fixture_ids}
    kept = [r for r in existing_rows if clean(r.get("fixture_id")) not in fixture_ids]
    return kept + new_rows


def build_plan(catalog, existing_rows, retry_unavailable=False, batch_size=DEFAULT_BATCH_SIZE):
    completed = completed_fixture_ids(existing_rows, retry_unavailable=retry_unavailable)
    missing = sorted(
        (fixture_id for fixture_id in catalog if fixture_id not in completed),
        key=lambda x: int(x),
    )
    estimated_requests = (len(missing) + batch_size - 1) // batch_size
    return missing, estimated_requests


def collect(retry_unavailable=False, batch_size=DEFAULT_BATCH_SIZE):
    catalog = load_fixture_catalog()
    existing = load_existing()
    missing, estimated_requests = build_plan(
        catalog,
        existing,
        retry_unavailable=retry_unavailable,
        batch_size=batch_size,
    )

    print("Historical Coach Builder")
    print("Fixtures in catalog:", len(catalog))
    print("Already complete:", len(catalog) - len(missing))
    print("Fixtures to fetch:", len(missing))
    print("Batch size:", batch_size)
    print("Estimated API requests:", estimated_requests)

    if not missing:
        return 0

    total_requests = 0
    collected_utc = utc_now().isoformat()

    for index, batch in enumerate(chunks(missing, batch_size), start=1):
        data = api_get("/fixtures", {"ids": "-".join(batch), "timezone": "UTC"})
        total_requests += 1
        parsed, seen = parse_batch_response(data, catalog, batch, collected_utc)

        successful_ids = set(seen)
        rows_by_fixture = {}
        for row in parsed:
            rows_by_fixture.setdefault(clean(row.get("fixture_id")), []).append(row)

        normalized = []
        for fixture_id in successful_ids:
            fixture_rows = rows_by_fixture.get(fixture_id, [])
            if len(fixture_rows) >= 2:
                normalized.extend(fixture_rows)
            else:
                normalized.extend(placeholder_rows(catalog[fixture_id], collected_utc))

        existing = replace_fixture_rows(existing, normalized, successful_ids)
        existing.sort(
            key=lambda r: (
                clean(r.get("date")),
                int(clean(r.get("fixture_id")) or 0),
                clean(r.get("team")),
            )
        )
        atomic_write_csv(existing)
        save_state(
            {
                "updated_utc": utc_now().isoformat(),
                "catalog_fixtures": len(catalog),
                "requests_this_run": total_requests,
                "last_batch": index,
                "last_batch_fixture_ids": batch,
                "remaining_after_batch": max(0, len(missing) - index * batch_size),
            }
        )
        print(
            f"Batch {index}/{estimated_requests}: requested={len(batch)} "
            f"returned={len(successful_ids)} rows={len(normalized)}"
        )
        time.sleep(REQUEST_PAUSE_SECONDS)

    final_rows = load_existing()
    final_complete = completed_fixture_ids(final_rows)
    ok_rows = sum(1 for r in final_rows if clean(r.get("coach_status")).upper() == "OK")
    unavailable_rows = sum(
        1 for r in final_rows if clean(r.get("coach_status")).upper() == "UNAVAILABLE"
    )
    print("Complete fixtures:", len(final_complete), "/", len(catalog))
    print("Coach rows OK:", ok_rows)
    print("Unavailable rows:", unavailable_rows)
    print("Actual API requests:", total_requests)
    print("Output:", Path(OUTPUT_FILE).resolve())
    return total_requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", action="store_true", help="Show request plan without API calls")
    parser.add_argument(
        "--retry-unavailable",
        action="store_true",
        help="Retry fixtures previously stored as UNAVAILABLE",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("FOOTBALL_AI_COACH_BATCH", DEFAULT_BATCH_SIZE)),
    )
    args = parser.parse_args()

    if args.batch_size < 1 or args.batch_size > 20:
        raise SystemExit("--batch-size must be between 1 and 20")

    if args.plan:
        catalog = load_fixture_catalog()
        existing = load_existing()
        missing, requests_count = build_plan(
            catalog,
            existing,
            retry_unavailable=args.retry_unavailable,
            batch_size=args.batch_size,
        )
        print("Historical Coach Builder — PLAN ONLY")
        print("Fixtures in catalog:", len(catalog))
        print("Fixtures to fetch:", len(missing))
        print("Batch size:", args.batch_size)
        print("Estimated API requests:", requests_count)
        return

    collect(
        retry_unavailable=args.retry_unavailable,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
