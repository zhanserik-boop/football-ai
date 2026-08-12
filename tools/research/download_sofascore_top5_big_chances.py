from __future__ import annotations

import argparse
import csv
import json
import random
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://www.sofascore.com/api/v1"

TOURNAMENTS = {
    "Premier League": 17,
    "La Liga": 8,
    "Serie A": 23,
    "Bundesliga": 35,
    "Ligue 1": 34,
}

TARGET_SEASONS = {"21/22", "22/23", "23/24", "24/25", "25/26"}

STAT_KEYS = {
    "expectedGoals": "xg",
    "bigChanceCreated": "big_chances",
    "bigChanceScored": "big_chances_scored",
    "bigChanceMissed": "big_chances_missed",
    "totalShotsOnGoal": "shots",
    "shotsOnGoal": "sot",
    "totalShotsInsideBox": "shots_inside_box",
    "totalShotsOutsideBox": "shots_outside_box",
    "touchesInOppBox": "touches_in_opp_box",
    "goalkeeperSaves": "goalkeeper_saves",
}

OUTPUT_COLUMNS = [
    "league", "season", "date_ts", "event_id", "home_team", "away_team",
    "home_goals", "away_goals",
    "home_xg", "away_xg",
    "home_big_chances", "away_big_chances",
    "home_big_chances_scored", "away_big_chances_scored",
    "home_big_chances_missed", "away_big_chances_missed",
    "home_shots", "away_shots",
    "home_sot", "away_sot",
    "home_shots_inside_box", "away_shots_inside_box",
    "home_shots_outside_box", "away_shots_outside_box",
    "home_touches_in_opp_box", "away_touches_in_opp_box",
    "home_goalkeeper_saves", "away_goalkeeper_saves",
    "stats_ok", "source",
]


def get_json(path: str, *, retries: int = 5, base_delay: float = 0.8) -> dict[str, Any]:
    url = f"{BASE}{path}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.sofascore.com/",
    }
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            wait = base_delay * (2 ** attempt) + random.uniform(0.1, 0.6)
            print(f"[WARN] {url} failed ({exc}); retry in {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"Failed after {retries} attempts: {url}: {last_error}")


def normalize_season_year(year: str) -> str:
    y = year.strip()
    if len(y) == 7 and "/" in y:  # 2023/24
        return y[2:]
    return y


def get_target_seasons(tournament_id: int) -> list[dict[str, Any]]:
    data = get_json(f"/unique-tournament/{tournament_id}/seasons")
    out = []
    for season in data.get("seasons", []):
        year = normalize_season_year(str(season.get("year", "")))
        if year in TARGET_SEASONS:
            out.append({"id": season["id"], "year": year, "name": season.get("name", year)})
    return sorted(out, key=lambda x: x["year"])


def get_season_events(tournament_id: int, season_id: int) -> list[dict[str, Any]]:
    events: dict[int, dict[str, Any]] = {}
    page = 0
    while True:
        data = get_json(f"/unique-tournament/{tournament_id}/season/{season_id}/events/last/{page}")
        chunk = data.get("events", [])
        if not chunk:
            break
        for event in chunk:
            event_id = event.get("id")
            if event_id:
                events[int(event_id)] = event
        if not data.get("hasNextPage", False):
            break
        page += 1
        if page > 60:
            raise RuntimeError(f"Unexpected pagination >60 pages for season {season_id}")
        time.sleep(random.uniform(0.15, 0.35))
    return sorted(events.values(), key=lambda e: e.get("startTimestamp", 0))


def extract_stats(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for period in payload.get("statistics", []):
        if period.get("period") != "ALL":
            continue
        for group in period.get("groups", []):
            for item in group.get("statisticsItems", []):
                key = item.get("key")
                if key not in STAT_KEYS:
                    continue
                name = STAT_KEYS[key]
                home = item.get("homeValue", item.get("home"))
                away = item.get("awayValue", item.get("away"))
                result[f"home_{name}"] = home
                result[f"away_{name}"] = away
    return result


def event_to_row(league: str, season: str, event: dict[str, Any]) -> dict[str, Any]:
    event_id = int(event["id"])
    row: dict[str, Any] = {col: "" for col in OUTPUT_COLUMNS}
    row.update({
        "league": league,
        "season": season,
        "date_ts": event.get("startTimestamp", ""),
        "event_id": event_id,
        "home_team": event.get("homeTeam", {}).get("name", ""),
        "away_team": event.get("awayTeam", {}).get("name", ""),
        "home_goals": event.get("homeScore", {}).get("normaltime", event.get("homeScore", {}).get("current", "")),
        "away_goals": event.get("awayScore", {}).get("normaltime", event.get("awayScore", {}).get("current", "")),
        "source": f"SofaScore event/{event_id}/statistics",
    })
    try:
        stats = extract_stats(get_json(f"/event/{event_id}/statistics"))
        row.update(stats)
        row["stats_ok"] = 1 if stats else 0
    except Exception as exc:
        print(f"[WARN] stats failed for event {event_id}: {exc}")
        row["stats_ok"] = 0
    return row


def existing_event_ids(path: Path) -> set[int]:
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return {int(r["event_id"]) for r in csv.DictReader(fh) if r.get("event_id")}


def append_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTPUT_COLUMNS)
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/research/sofascore_top5_2021_2026.csv")
    parser.add_argument("--max-events", type=int, default=0, help="0 = all; useful for smoke tests")
    parser.add_argument("--delay", type=float, default=0.28, help="Delay between event-stat requests")
    args = parser.parse_args()

    out = Path(args.out)
    seen = existing_event_ids(out)
    written = 0

    for league, tournament_id in TOURNAMENTS.items():
        seasons = get_target_seasons(tournament_id)
        print(f"[INFO] {league}: target seasons={[(s['year'], s['id']) for s in seasons]}")
        for season in seasons:
            events = get_season_events(tournament_id, int(season["id"]))
            finished = [e for e in events if e.get("status", {}).get("type") == "finished"]
            print(f"[INFO] {league} {season['year']}: {len(finished)} finished events")
            batch: list[dict[str, Any]] = []
            for event in finished:
                event_id = int(event["id"])
                if event_id in seen:
                    continue
                row = event_to_row(league, season["year"], event)
                batch.append(row)
                seen.add(event_id)
                written += 1
                if len(batch) >= 25:
                    append_rows(out, batch)
                    batch.clear()
                if args.max_events and written >= args.max_events:
                    if batch:
                        append_rows(out, batch)
                    print(f"[DONE] smoke limit reached: {written} rows -> {out}")
                    return
                time.sleep(max(0.0, args.delay) + random.uniform(0.0, 0.12))
            if batch:
                append_rows(out, batch)

    print(f"[DONE] wrote {written} new rows -> {out}")


if __name__ == "__main__":
    main()
