import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import v3_runtime_checkpoint as checkpoint


CHECKPOINT_STATUS_FILE = checkpoint.STATUS_FILE
OUTPUT_FILE = "v3_backup_guard_status.json"
NOTIFY_STATE_FILE = "v3_backup_guard_notify_state.json"
MIRROR_ENV = "FOOTBALL_AI_BACKUP_MIRROR_DIR"

CHECKPOINT_STATUS_MAX_AGE_MINUTES = 45
INTEGRITY_VERIFY_HOURS = 24
MIRROR_RETENTION = 120

SEVERITY = {"HEALTHY": 0, "DEGRADED": 1, "CRITICAL": 2}


def utc_now():
    return datetime.now(timezone.utc)


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


def read_env_file(path):
    values = {}
    path = Path(path)
    if not path.exists():
        return values
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    values[key] = value
    except Exception:
        return {}
    return values


def effective_env(root, environ=None):
    values = read_env_file(Path(root) / ".env")
    values.update(dict(os.environ if environ is None else environ))
    return values


def file_signature(path):
    stat = Path(path).stat()
    raw = f"{Path(path).name}:{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue(severity, code, message):
    return {"severity": severity, "code": code, "message": message}


def mirror_directory(root, env):
    raw = str(env.get(MIRROR_ENV, "")).strip()
    if not raw:
        return None, "NOT_CONFIGURED"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return None, "MIRROR_PATH_NOT_ABSOLUTE"
    resolved = path.resolve()
    project = Path(root).resolve()
    if str(resolved) == resolved.anchor:
        return None, "MIRROR_PATH_IS_DRIVE_ROOT"
    try:
        resolved.relative_to(project)
        return None, "MIRROR_INSIDE_PROJECT"
    except ValueError:
        pass
    return resolved, "CONFIGURED"


def verify_due(path, prior_path, prior_signature, prior_verified, now):
    verified = checkpoint.parse_dt(prior_verified)
    if verified is None:
        return True
    return any([
        str(path) != str(prior_path or ""),
        file_signature(path) != str(prior_signature or ""),
        now - verified >= timedelta(hours=INTEGRITY_VERIFY_HOURS),
    ])


def prune_mirror(directory, keep=MIRROR_RETENTION):
    paths = sorted(Path(directory).glob(f"{checkpoint.ARCHIVE_PREFIX}*.zip"))
    removed = []
    for path in paths[:-max(1, keep)]:
        path.unlink()
        removed.append(path.name)
    return removed


def sync_mirror(source, mirror, prior, now):
    mirror.mkdir(parents=True, exist_ok=True)
    target = mirror / source.name
    source_manifest = checkpoint.manifest_from_archive(source)
    source_fingerprint = source_manifest.get("content_fingerprint")
    copied = False

    target_is_current = False
    if target.exists():
        prior_mirror = prior.get("mirror", {}) if isinstance(prior.get("mirror"), dict) else {}
        if not verify_due(
            target,
            prior_mirror.get("checkpoint"),
            prior_mirror.get("signature"),
            prior_mirror.get("verified_utc"),
            now,
        ):
            target_is_current = True
        else:
            verification = checkpoint.verify_checkpoint(target)
            target_fingerprint = verification["manifest"].get("content_fingerprint")
            target_is_current = verification["valid"] and target_fingerprint == source_fingerprint

    if not target_is_current:
        temporary = mirror / f".{target.name}.tmp"
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        verification = checkpoint.verify_checkpoint(target)
        target_fingerprint = verification["manifest"].get("content_fingerprint")
        if not verification["valid"] or target_fingerprint != source_fingerprint:
            raise RuntimeError(
                "Mirrored checkpoint verification failed: "
                + "; ".join(verification["errors"] or ["fingerprint mismatch"])
            )
        copied = True

    removed = prune_mirror(mirror)
    return {
        "status": "COPIED" if copied else "SYNCED",
        "checkpoint": str(target),
        "checkpoint_name": target.name,
        "signature": file_signature(target),
        "verified_utc": now.isoformat(),
        "removed_old_checkpoints": removed,
    }


def build_guard(root=".", environ=None, now=None):
    root = Path(root)
    now = now or utc_now()
    env = effective_env(root, environ)
    prior = read_json(root / OUTPUT_FILE)
    issues = []
    local_info = {}
    mirror_info = {"status": "NOT_CONFIGURED"}

    checkpoint_status = read_json(root / CHECKPOINT_STATUS_FILE)
    checked = checkpoint.parse_dt(checkpoint_status.get("checked_utc"))
    checked_age = None if checked is None else (now - checked).total_seconds() / 60
    if checked_age is None:
        issues.append(issue(
            "DEGRADED", "CHECKPOINT_STATUS_MISSING",
            "Runtime checkpoint status is missing or unreadable",
        ))
    elif checked_age > CHECKPOINT_STATUS_MAX_AGE_MINUTES:
        issues.append(issue(
            "DEGRADED", "CHECKPOINT_RUN_STALE",
            f"Checkpoint cycle has not reported for {checked_age:.1f} minutes",
        ))

    latest = checkpoint.latest_checkpoint(root)
    local_valid = False
    if latest is None:
        issues.append(issue(
            "CRITICAL", "LOCAL_CHECKPOINT_MISSING",
            "No local runtime checkpoint exists",
        ))
    else:
        prior_local = prior.get("local", {}) if isinstance(prior.get("local"), dict) else {}
        due = verify_due(
            latest,
            prior_local.get("checkpoint"),
            prior_local.get("signature"),
            prior_local.get("verified_utc"),
            now,
        )
        if due:
            verification = checkpoint.verify_checkpoint(latest)
            local_valid = verification["valid"]
            verified_utc = now.isoformat() if local_valid else ""
            if not local_valid:
                issues.append(issue(
                    "CRITICAL", "LOCAL_CHECKPOINT_CORRUPT",
                    "Latest local checkpoint failed integrity verification",
                ))
        else:
            local_valid = True
            verified_utc = prior_local.get("verified_utc", "")
        local_info = {
            "status": "VALID" if local_valid else "CORRUPT",
            "checkpoint": str(latest),
            "checkpoint_name": latest.name,
            "signature": file_signature(latest),
            "verified_utc": verified_utc,
        }

    mirror, mirror_config = mirror_directory(root, env)
    if mirror is None:
        mirror_info = {"status": mirror_config}
        code = (
            "MIRROR_NOT_CONFIGURED"
            if mirror_config == "NOT_CONFIGURED" else mirror_config
        )
        message = (
            f"Set {MIRROR_ENV} to an external drive or synced cloud folder"
            if mirror_config == "NOT_CONFIGURED"
            else "External mirror path is unsafe or invalid"
        )
        issues.append(issue("DEGRADED", code, message))
    elif local_valid:
        try:
            mirror_info = sync_mirror(latest, mirror, prior, now)
        except Exception as exc:
            mirror_info = {"status": "FAILED", "error": repr(exc)}
            issues.append(issue(
                "DEGRADED", "MIRROR_SYNC_FAILED",
                "External checkpoint mirror failed",
            ))

    overall = "HEALTHY"
    if issues:
        overall = max(issues, key=lambda row: SEVERITY[row["severity"]])["severity"]
    return {
        "checked_utc": now.isoformat(),
        "overall_status": overall,
        "issues": issues,
        "local": local_info,
        "mirror": mirror_info,
        "mirror_configured": mirror is not None,
        "secrets_copied": False,
        "football_data_api_requests_used": 0,
    }


def issue_fingerprint(status):
    values = sorted(
        f"{row['severity']}:{row['code']}" for row in status.get("issues", [])
    )
    return "|".join(values)


def load_notify_state(path):
    state = read_json(path)
    return state if state else {"fingerprint": "", "overall_status": ""}


def notification_event(status, state):
    fingerprint = issue_fingerprint(status)
    previous = str(state.get("fingerprint", ""))
    overall = status.get("overall_status", "UNKNOWN")
    previous_overall = state.get("overall_status", "")
    if overall == "HEALTHY":
        if previous and previous_overall in {"DEGRADED", "CRITICAL"}:
            return "RECOVERED", backup_message(status, recovered=True)
        return None
    if fingerprint != previous:
        return "ISSUE", backup_message(status)
    return None


def backup_message(status, recovered=False):
    if recovered:
        return "\n".join([
            "V3 BACKUP RECOVERED",
            "",
            "Local checkpoint and external mirror are healthy.",
            "No betting decision was changed.",
        ])
    lines = [
        f"V3 BACKUP GUARD — {status.get('overall_status', 'UNKNOWN')}",
        "",
    ]
    for row in status.get("issues", []):
        lines.append(f"{row['code']}: {row['message']}")
    lines.extend([
        "",
        f"Local: {status.get('local', {}).get('status', 'MISSING')}",
        f"Mirror: {status.get('mirror', {}).get('status', 'UNKNOWN')}",
        "No betting decision was changed.",
    ])
    return "\n".join(lines)


def run_once(sender=None, environ=None):
    status = build_guard(environ=environ)
    write_json_atomic(OUTPUT_FILE, status)
    state = load_notify_state(NOTIFY_STATE_FILE)
    event = notification_event(status, state)
    sent = False
    if event:
        if sender is None:
            from telegram_notifier import send_telegram
            sender = send_telegram
        sent = bool(sender(event[1]))
        if sent:
            state = {
                "fingerprint": issue_fingerprint(status),
                "overall_status": status["overall_status"],
                "updated_utc": status["checked_utc"],
            }
            write_json_atomic(NOTIFY_STATE_FILE, state)
    elif not state.get("overall_status"):
        state = {
            "fingerprint": issue_fingerprint(status),
            "overall_status": status["overall_status"],
            "updated_utc": status["checked_utc"],
        }
        write_json_atomic(NOTIFY_STATE_FILE, state)

    print("\n" + "=" * 72)
    print("FOOTBALL AI V3 — BACKUP GUARD")
    print("=" * 72)
    print("STATUS:", status["overall_status"])
    print("LOCAL:", status.get("local", {}).get("status", "MISSING"))
    print("MIRROR:", status.get("mirror", {}).get("status", "UNKNOWN"))
    print("ISSUES:", len(status["issues"]))
    print("TELEGRAM SENT:", "YES" if sent else "NO")
    print("SECRETS COPIED: NO")
    print("FOOTBALL DATA API REQUESTS USED: 0")
    print("=" * 72)
    return status, event, sent


if __name__ == "__main__":
    run_once()
