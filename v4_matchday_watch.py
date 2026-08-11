"""Scheduled V4 pre-match refreshes around expected lineup publication.

Runs the shadow predictor at bounded checkpoints. It never places real bets.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


CHECKPOINTS_MINUTES = (60.0, 45.0, 30.0, 20.0, 10.0, 5.0)


def utc_now():
    return datetime.now(timezone.utc)


def load_document(path):
    target = Path(path)
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {}


def pending_lineups(document):
    pending = []
    for result in document.get("results", []):
        fixture = result.get("fixture") or {}
        lineup = (result.get("agents") or {}).get("lineup") or {}
        minutes = fixture.get("minutes_to_kickoff")
        if result.get("analysis_status") != "ANALYZED_PREMATCH":
            continue
        if not isinstance(minutes, (int, float)) or minutes <= 0:
            continue
        if lineup.get("status") == "CONFIRMED":
            continue
        pending.append({
            "fixture_id": fixture.get("fixture_id", ""),
            "home_team": fixture.get("home_team", "HOME"),
            "away_team": fixture.get("away_team", "AWAY"),
            "minutes_to_kickoff": float(minutes),
            "lineup_status": lineup.get("status", "UNKNOWN"),
        })
    return pending


def next_checkpoint_delay_minutes(pending):
    delays = []
    for row in pending:
        minutes = row["minutes_to_kickoff"]
        future_checkpoints = [
            checkpoint for checkpoint in CHECKPOINTS_MINUTES
            if checkpoint < minutes - 0.1
        ]
        if future_checkpoints:
            delays.append(minutes - max(future_checkpoints))
    return min(delays) if delays else None


def build_parser():
    parser = argparse.ArgumentParser(
        description="Football AI V4 bounded matchday lineup watcher"
    )
    parser.add_argument("--runner", default="v4_multileague_shadow.py")
    parser.add_argument("--input", default="v4_target_matches_20260811.csv")
    parser.add_argument("--timezone", default="Asia/Almaty")
    parser.add_argument("--json", default="v4_multileague_predictions.json")
    parser.add_argument("--lineup-router", default="v4_lineup_source_router.py")
    parser.add_argument("--lineup-audit-json", default="v4_lineup_source_audit.json")
    parser.add_argument("--lineup-shock-runner", default="v4_lineup_shock_research.py")
    parser.add_argument("--player-values", default="v4_player_values.json")
    parser.add_argument("--lineup-shock-json", default="v4_lineup_shock_research.json")
    parser.add_argument("--max-hours", type=float, default=12.0)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    started = utc_now()
    deadline = started + timedelta(hours=max(0.1, args.max_hours))
    cycle = 0
    while utc_now() < deadline:
        cycle += 1
        print(f"\nV4 MATCHDAY WATCH — CYCLE {cycle}")
        completed = subprocess.run(
            [
                sys.executable, args.runner,
                "--input", args.input,
                "--timezone", args.timezone,
                "--json", args.json,
            ],
            check=False,
        )
        if completed.returncode != 0:
            print("WATCH STATUS: RUNNER_FAILED")
            return completed.returncode

        router_path = Path(args.lineup_router)
        router_succeeded = False
        if router_path.exists():
            routed = subprocess.run(
                [
                    sys.executable, str(router_path),
                    "--predictions", args.json,
                    "--json", args.lineup_audit_json,
                ],
                check=False,
            )
            if routed.returncode != 0:
                print("LINEUP ROUTER STATUS: FAILED_CLOSED — API-Football watcher continues")
            else:
                router_succeeded = True

        shock_path = Path(args.lineup_shock_runner)
        if router_succeeded and shock_path.exists():
            shocked = subprocess.run(
                [
                    sys.executable, str(shock_path),
                    "--predictions", args.json,
                    "--player-values", args.player_values,
                    "--lineup-audit", args.lineup_audit_json,
                    "--json", args.lineup_shock_json,
                ],
                check=False,
            )
            if shocked.returncode != 0:
                print("LINEUP SHOCK STATUS: FAILED_CLOSED — no research adjustment accepted")

        document = load_document(args.json)
        pending = pending_lineups(document)
        if not pending:
            print("WATCH STATUS: COMPLETE — no pre-match fixture is waiting for XI")
            return 0

        for row in pending:
            print(
                f"[{row['lineup_status']}] {row['home_team']} — {row['away_team']} "
                f"| T-{row['minutes_to_kickoff']:.1f}m"
            )
        delay_minutes = next_checkpoint_delay_minutes(pending)
        if delay_minutes is None:
            print("WATCH STATUS: COMPLETE — final T-5 checkpoint has run")
            return 0

        delay_seconds = max(1.0, delay_minutes * 60.0)
        if utc_now() + timedelta(seconds=delay_seconds) >= deadline:
            print("WATCH STATUS: MAX_HOURS_REACHED")
            return 0
        print(f"NEXT LINEUP CHECK IN: {delay_minutes:.1f}m")
        try:
            time.sleep(delay_seconds)
        except KeyboardInterrupt:
            print("WATCH STATUS: STOPPED_BY_USER")
            return 130

    print("WATCH STATUS: MAX_HOURS_REACHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
