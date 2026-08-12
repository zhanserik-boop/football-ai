"""Recoverable one-time reset of V3 R1 AH-derived evidence for V3 R2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


RELEASE = "V3_SHADOW_FROZEN_R2"
MARKER_FILE = "v3_r2_migration_marker.json"
ARCHIVE_ROOT = "runtime_archives"

# Only evidence affected directly or transitively by the R1 AH normalization.
# Credentials, source files, squad snapshots and backups are deliberately absent.
AFFECTED_FILES = (
    "market_snapshots_v2.csv",
    "live_lineups_v2.csv",
    "lineup_signals_live.csv",
    "ah_agent_v2_latest.csv",
    "ah_agent_v2_history.csv",
    "master_decisions_live.csv",
    "master_decisions_history.csv",
    "fixture_results_live.csv",
    "post_lineup_clv_report.csv",
    "post_lineup_bets_report.csv",
    "market_timeline_live.csv",
    "signal_audit_ledger.csv",
    "shadow_value_gate_live.csv",
    "shadow_value_gate_history.csv",
    "shadow_value_gate_outcomes.csv",
    "shadow_value_gate_outcome_summary.csv",
    "v3_forward_test_scorecard.csv",
    "v3_forward_test_summary.json",
    "v3_drift_baseline.json",
    "v3_drift_watch.csv",
    "v3_drift_watch_summary.json",
    "v3_drift_watch_notify_state.json",
    "v3_shadow_risk_report.csv",
    "v3_shadow_risk_summary.json",
    "shadow_value_gate_notify_state.json",
    "system_health_live.csv",
    "system_health_history.csv",
    "system_health_notify_state.json",
    "v3_readiness_report.csv",
    "v3_readiness_summary.json",
    "v3_daily_digest_state.json",
    "v3_daily_digest_status.json",
    "market_monitor_v2_state.json",
)


def utc_now():
    return datetime.now(timezone.utc)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_marker(root):
    path = Path(root) / MARKER_FILE
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def migration_complete(root="."):
    marker = read_marker(root)
    return marker.get("status") == "COMPLETED" and marker.get("release") == RELEASE


def migrate(root=".", now=None, apply=False):
    root = Path(root).resolve()
    now = now or utc_now()
    existing = [name for name in AFFECTED_FILES if (root / name).is_file()]
    if migration_complete(root):
        return {
            "status": "ALREADY_COMPLETED", "release": RELEASE,
            "files_found": len(existing), "files_archived": 0,
            "archive_directory": read_marker(root).get("archive_directory", ""),
        }
    if not apply:
        return {
            "status": "APPLY_REQUIRED", "release": RELEASE,
            "files_found": len(existing), "files_archived": 0,
            "archive_directory": "",
        }

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    archive = root / ARCHIVE_ROOT / f"v3_r1_before_r2_{stamp}"
    archive.mkdir(parents=True, exist_ok=False)
    archived = []
    for name in existing:
        source = root / name
        target = archive / name
        before = sha256_file(source)
        source.replace(target)
        after = sha256_file(target)
        if before != after:
            raise RuntimeError(f"archive verification failed for {name}")
        archived.append({
            "path": name, "sha256": after, "size_bytes": target.stat().st_size,
        })
    marker = {
        "status": "COMPLETED",
        "release": RELEASE,
        "completed_utc": now.isoformat(),
        "archive_directory": str(archive.relative_to(root)),
        "files_archived": len(archived),
        "files": archived,
        "r1_forward_evidence_compatible": False,
        "secrets_included": False,
        "recoverable": True,
    }
    temporary = root / (MARKER_FILE + ".tmp")
    temporary.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, root / MARKER_FILE)
    return marker


def require_migration(root="."):
    if not migration_complete(root):
        raise SystemExit(
            "V3 R2 migration required. Run: python .\\v3_r2_runtime_migration.py --apply"
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Archive incompatible V3 R1 AH evidence")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    report = migrate(apply=args.apply)
    print("\n" + "=" * 72)
    print("FOOTBALL AI V3 R2 — RUNTIME MIGRATION")
    print("=" * 72)
    print("STATUS:", report["status"])
    print("FILES FOUND:", report.get("files_found", report.get("files_archived", 0)))
    print("FILES ARCHIVED:", report.get("files_archived", 0))
    print("ARCHIVE:", report.get("archive_directory") or "NOT CREATED")
    print("RECOVERABLE: YES")
    print("SECRETS INCLUDED: NO")
    print("API REQUESTS USED: 0")
    print("=" * 72)
    if report["status"] == "APPLY_REQUIRED":
        print("Run: python .\\v3_r2_runtime_migration.py --apply")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
