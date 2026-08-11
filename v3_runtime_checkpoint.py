import argparse
import csv
import hashlib
import io
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath


CHECKPOINT_DIR = "runtime_checkpoints"
STATUS_FILE = "v3_runtime_checkpoint_status.json"
MANIFEST_NAME = "_manifest.json"
ARCHIVE_PREFIX = "football_ai_v3_"

MIN_CHECKPOINT_INTERVAL_HOURS = 6
VERIFY_INTERVAL_HOURS = 24
MAX_CHECKPOINTS = 120

# Deliberately explicit allow-list. Secrets such as .env and notification
# credentials/states can never enter a checkpoint by directory traversal.
EVIDENCE_FILES = (
    "market_snapshots_v2.csv",
    "live_lineups_v2.csv",
    "lineup_signals_live.csv",
    "ah_agent_v2_history.csv",
    "master_decisions_history.csv",
    "fixture_results_live.csv",
    "post_lineup_clv_report.csv",
    "post_lineup_bets_report.csv",
    "market_timeline_live.csv",
    "signal_audit_ledger.csv",
    "live_coach_observations.csv",
    "coach_context_live.csv",
    "shadow_value_gate_history.csv",
    "shadow_value_gate_outcomes.csv",
    "shadow_value_gate_outcome_summary.csv",
    "system_health_history.csv",
    "v3_readiness_report.csv",
    "v3_readiness_summary.json",
    "v3_forward_test_scorecard.csv",
    "v3_forward_test_summary.json",
    "v3_drift_baseline.json",
    "v3_drift_watch.csv",
    "v3_drift_watch_summary.json",
    "v3_shadow_risk_report.csv",
    "v3_shadow_risk_summary.json",
    "current_squads_2026.csv",
    "market_monitor_v2_state.json",
)


def utc_now():
    return datetime.now(timezone.utc)


def parse_dt(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def write_json_atomic(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def csv_row_count(data):
    try:
        text = data.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
        return max(0, len(rows) - 1) if rows else 0
    except Exception:
        return None


def source_signature(root, filenames=EVIDENCE_FILES):
    root = Path(root)
    parts = []
    for name in filenames:
        path = root / name
        if not path.is_file() or path.stat().st_size == 0:
            continue
        stat = path.stat()
        parts.append(f"{name}:{stat.st_size}:{stat.st_mtime_ns}")
    return sha256_bytes("\n".join(parts).encode("utf-8")), len(parts)


def collect_snapshots(root, filenames=EVIDENCE_FILES):
    root = Path(root)
    snapshots = {}
    for name in filenames:
        path = root / name
        if path.is_file() and path.stat().st_size > 0:
            snapshots[name] = path.read_bytes()
    return snapshots


def content_fingerprint(file_entries):
    lines = [
        f"{entry['path']}:{entry['sha256']}:{entry['size']}"
        for entry in sorted(file_entries, key=lambda item: item["path"])
    ]
    return sha256_bytes("\n".join(lines).encode("utf-8"))


def build_manifest(snapshots, now):
    entries = []
    for name, data in sorted(snapshots.items()):
        entries.append({
            "path": name,
            "size": len(data),
            "sha256": sha256_bytes(data),
            "rows": csv_row_count(data) if name.lower().endswith(".csv") else None,
        })
    return {
        "schema_version": 1,
        "created_utc": now.isoformat(),
        "file_count": len(entries),
        "content_fingerprint": content_fingerprint(entries),
        "files": entries,
        "secrets_included": False,
        "api_requests_used": 0,
    }


def archive_paths(root):
    directory = Path(root) / CHECKPOINT_DIR
    if not directory.exists():
        return []
    return sorted(directory.glob(f"{ARCHIVE_PREFIX}*.zip"))


def latest_checkpoint(root):
    paths = archive_paths(root)
    return paths[-1] if paths else None


def safe_member_name(name):
    path = PurePosixPath(name)
    return (
        bool(name)
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in name
    )


def verify_checkpoint(path):
    path = Path(path)
    errors = []
    manifest = {}
    if not path.is_file():
        return {"valid": False, "errors": ["Checkpoint does not exist"], "manifest": {}}
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            unsafe = [name for name in names if not safe_member_name(name)]
            if unsafe:
                errors.append("Unsafe archive paths: " + ", ".join(unsafe))
            if MANIFEST_NAME not in names:
                errors.append("Manifest is missing")
            else:
                manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
                if manifest.get("schema_version") != 1:
                    errors.append("Unsupported manifest schema")
                for entry in manifest.get("files", []):
                    name = entry.get("path", "")
                    if not safe_member_name(name) or name not in names:
                        errors.append(f"Missing or unsafe member: {name}")
                        continue
                    data = archive.read(name)
                    if len(data) != entry.get("size"):
                        errors.append(f"Size mismatch: {name}")
                    if sha256_bytes(data) != entry.get("sha256"):
                        errors.append(f"SHA-256 mismatch: {name}")
                bad_member = archive.testzip()
                if bad_member:
                    errors.append(f"ZIP CRC failure: {bad_member}")
    except Exception as exc:
        errors.append(f"Unreadable checkpoint: {exc!r}")
    return {"valid": not errors, "errors": errors, "manifest": manifest}


def create_checkpoint(root, snapshots, now):
    root = Path(root)
    directory = root / CHECKPOINT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"{ARCHIVE_PREFIX}{stamp}.zip"
    temporary = directory / f".{target.name}.tmp"
    manifest = build_manifest(snapshots, now)
    with zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name, data in sorted(snapshots.items()):
            archive.writestr(name, data)
        archive.writestr(
            MANIFEST_NAME,
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    os.replace(temporary, target)
    verification = verify_checkpoint(target)
    if not verification["valid"]:
        raise RuntimeError("Checkpoint verification failed: " + "; ".join(verification["errors"]))
    return target, manifest


def prune_checkpoints(root, keep=MAX_CHECKPOINTS):
    paths = archive_paths(root)
    if keep < 1:
        keep = 1
    removed = []
    for path in paths[:-keep]:
        path.unlink()
        removed.append(path.name)
    return removed


def manifest_from_archive(path):
    try:
        with zipfile.ZipFile(path, "r") as archive:
            return json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
    except Exception:
        return {}


def save_status(root, status):
    write_json_atomic(Path(root) / STATUS_FILE, status)
    return status


def checkpoint_once(
    root=".",
    now=None,
    min_interval_hours=MIN_CHECKPOINT_INTERVAL_HOURS,
    verify_interval_hours=VERIFY_INTERVAL_HOURS,
    retention=MAX_CHECKPOINTS,
    filenames=EVIDENCE_FILES,
):
    root = Path(root)
    now = now or utc_now()
    light_signature, source_count = source_signature(root, filenames)
    prior = read_json(root / STATUS_FILE)
    latest = latest_checkpoint(root)

    if source_count == 0:
        return save_status(root, {
            "checked_utc": now.isoformat(),
            "status": "NO_DATA",
            "message": "No runtime evidence files exist yet",
            "api_requests_used": 0,
        })

    force_create = latest is None
    last_checkpoint_utc = parse_dt(prior.get("last_checkpoint_utc"))
    last_verified_utc = parse_dt(prior.get("last_verified_utc"))

    if latest is not None and (
        last_verified_utc is None
        or now - last_verified_utc >= timedelta(hours=verify_interval_hours)
    ):
        verification = verify_checkpoint(latest)
        if not verification["valid"]:
            force_create = True
        else:
            last_verified_utc = now

    if (
        not force_create
        and prior.get("checkpoint_source_signature") == light_signature
    ):
        status = dict(prior)
        status.update({
            "checked_utc": now.isoformat(),
            "status": "UNCHANGED",
            "message": "Runtime evidence is unchanged; no duplicate checkpoint created",
            "last_verified_utc": (
                last_verified_utc.isoformat() if last_verified_utc else prior.get("last_verified_utc")
            ),
            "api_requests_used": 0,
        })
        return save_status(root, status)

    if (
        not force_create
        and last_checkpoint_utc is not None
        and now - last_checkpoint_utc < timedelta(hours=min_interval_hours)
    ):
        remaining = timedelta(hours=min_interval_hours) - (now - last_checkpoint_utc)
        status = dict(prior)
        status.update({
            "checked_utc": now.isoformat(),
            "status": "DEFERRED",
            "message": f"Evidence changed; next checkpoint eligible in {remaining.total_seconds() / 3600:.2f}h",
            "last_verified_utc": (
                last_verified_utc.isoformat() if last_verified_utc else prior.get("last_verified_utc")
            ),
            "api_requests_used": 0,
        })
        return save_status(root, status)

    snapshots = collect_snapshots(root, filenames)
    manifest = build_manifest(snapshots, now)
    if latest is not None and not force_create:
        previous_manifest = manifest_from_archive(latest)
        if previous_manifest.get("content_fingerprint") == manifest["content_fingerprint"]:
            status = dict(prior)
            status.update({
                "checked_utc": now.isoformat(),
                "status": "UNCHANGED",
                "message": "File metadata changed but evidence content is unchanged",
                "checkpoint_source_signature": light_signature,
                "last_verified_utc": now.isoformat(),
                "api_requests_used": 0,
            })
            return save_status(root, status)

    target, manifest = create_checkpoint(root, snapshots, now)
    removed = prune_checkpoints(root, keep=retention)
    return save_status(root, {
        "checked_utc": now.isoformat(),
        "status": "CREATED",
        "message": f"Verified checkpoint created with {manifest['file_count']} files",
        "checkpoint": str(target),
        "content_fingerprint": manifest["content_fingerprint"],
        "checkpoint_source_signature": light_signature,
        "last_checkpoint_utc": now.isoformat(),
        "last_verified_utc": now.isoformat(),
        "retention_limit": max(1, retention),
        "removed_old_checkpoints": removed,
        "secrets_included": False,
        "api_requests_used": 0,
    })


def restore_checkpoint(path, target):
    path = Path(path)
    target = Path(target)
    verification = verify_checkpoint(path)
    if not verification["valid"]:
        raise RuntimeError("Restore refused: " + "; ".join(verification["errors"]))
    if target.exists():
        raise FileExistsError(f"Restore target already exists: {target}")
    manifest = verification["manifest"]
    members = [entry["path"] for entry in manifest.get("files", [])]
    if any(not safe_member_name(name) for name in members):
        raise RuntimeError("Restore refused: unsafe member path")
    target.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(path, "r") as archive:
        for name in members:
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(name))
        (target / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return {"restored_files": len(members), "target": str(target), "verified": True}


def print_status(status):
    print("\n" + "=" * 72)
    print("FOOTBALL AI V3 — RUNTIME CHECKPOINT")
    print("=" * 72)
    print("STATUS:", status.get("status", "UNKNOWN"))
    print("DETAIL:", status.get("message", ""))
    if status.get("checkpoint"):
        print("CHECKPOINT:", status["checkpoint"])
    removed = status.get("removed_old_checkpoints", [])
    if removed:
        print("RETENTION REMOVED:", len(removed), "old checkpoint(s)")
    print("SECRETS INCLUDED: NO")
    print("API REQUESTS USED: 0")
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(description="Football AI V3 runtime checkpoint")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--verify", metavar="ZIP", help="Verify a checkpoint archive")
    actions.add_argument("--verify-latest", action="store_true", help="Verify latest checkpoint")
    actions.add_argument("--restore", metavar="ZIP", help="Restore into a new directory")
    parser.add_argument("--target", help="New restore directory (must not exist)")
    args = parser.parse_args()

    if args.verify or args.verify_latest:
        path = Path(args.verify) if args.verify else latest_checkpoint(".")
        result = verify_checkpoint(path) if path else {
            "valid": False, "errors": ["No checkpoint exists"], "manifest": {}
        }
        print("VALID:" if result["valid"] else "INVALID:", path or "-")
        for error in result["errors"]:
            print(" -", error)
        raise SystemExit(0 if result["valid"] else 1)

    if args.restore:
        stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        target = Path(args.target or f"runtime_restore_{stamp}")
        result = restore_checkpoint(args.restore, target)
        print("RESTORED:", result["restored_files"], "files")
        print("TARGET:", result["target"])
        print("Live files were not overwritten.")
        return

    status = checkpoint_once()
    print_status(status)


if __name__ == "__main__":
    main()
