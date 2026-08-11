import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


MANIFEST_FILE = "v3_frozen_manifest.json"
DRILL_FILE = "v3_emergency_drill_report.json"
OUTPUT_FILE = "v3_freeze_guard_report.json"


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


def git_blob_sha1(path):
    data = Path(path).read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def issue(code, message, path="", expected="", actual=""):
    return {
        "code": code,
        "message": message,
        "path": path,
        "expected": expected,
        "actual": actual,
    }


def validate_manifest(manifest):
    files = manifest.get("files") if isinstance(manifest, dict) else None
    return all([
        manifest.get("schema_version") == 1,
        manifest.get("release") == "V3_SHADOW_FROZEN",
        isinstance(files, dict),
        bool(files),
        all(
            isinstance(path, str) and path
            and isinstance(value, str) and len(value) == 40
            for path, value in (files or {}).items()
        ),
    ])


def verify_files(root, files):
    root = Path(root)
    issues = []
    verified = 0
    for relative, expected in sorted(files.items()):
        path = root / relative
        if not path.is_file():
            issues.append(issue(
                "FROZEN_FILE_MISSING",
                "A frozen V3 file is missing",
                path=relative,
                expected=expected,
            ))
            continue
        actual = git_blob_sha1(path)
        if actual != expected:
            issues.append(issue(
                "FROZEN_FILE_CHANGED",
                "A frozen V3 file differs from the approved manifest",
                path=relative,
                expected=expected,
                actual=actual,
            ))
            continue
        verified += 1
    return verified, issues


def verify_drill(report):
    if not report:
        return issue(
            "EMERGENCY_DRILL_REQUIRED",
            "Run python v3_emergency_drill.py once on this machine",
        )
    passed = int(report.get("scenarios_passed") or 0)
    total = int(report.get("scenarios_total") or 0)
    if report.get("status") != "PASSED" or total < 3 or passed != total:
        return issue(
            "EMERGENCY_DRILL_FAILED",
            f"Latest emergency drill did not pass all scenarios ({passed}/{total})",
        )
    if any([
        report.get("real_telegram_messages_sent") != 0,
        report.get("live_runtime_files_modified") is not False,
        report.get("football_data_api_requests_used") != 0,
        report.get("secrets_included") is not False,
    ]):
        return issue(
            "EMERGENCY_DRILL_UNSAFE",
            "Emergency drill safety invariants were not satisfied",
        )
    return None


def build_guard(
    root=".", now=None, manifest_file=MANIFEST_FILE, drill_file=DRILL_FILE
):
    root = Path(root)
    now = now or utc_now()
    manifest = read_json(root / manifest_file)
    issues = []

    if not validate_manifest(manifest):
        issues.append(issue(
            "FREEZE_MANIFEST_INVALID",
            "Frozen V3 manifest is missing, unreadable, or malformed",
            path=manifest_file,
        ))
        files = {}
        verified = 0
    else:
        files = manifest["files"]
        verified, file_issues = verify_files(root, files)
        issues.extend(file_issues)

    drill = read_json(root / drill_file)
    drill_issue = verify_drill(drill)
    if drill_issue:
        issues.append(drill_issue)

    status = "FROZEN" if not issues else "BLOCKED"
    return {
        "checked_utc": now.isoformat(),
        "status": status,
        "release": manifest.get("release", "UNKNOWN"),
        "frozen_at_utc": manifest.get("frozen_at_utc", ""),
        "source_commit": manifest.get("source_commit", ""),
        "files_verified": verified,
        "files_expected": len(files),
        "emergency_drill_status": drill.get("status", "MISSING"),
        "issues": issues,
        "startup_allowed": status == "FROZEN",
        "changes_require_new_reviewed_version": True,
        "automatic_real_betting_enabled": False,
        "football_data_api_requests_used": 0,
        "shadow_only": True,
    }


def print_report(report):
    print("\n" + "=" * 72)
    print("FOOTBALL AI V3 — FREEZE GUARD")
    print("=" * 72)
    print("STATUS:", report["status"])
    print("RELEASE:", report["release"])
    print(
        "FROZEN FILES:",
        f"{report['files_verified']}/{report['files_expected']} verified",
    )
    print("EMERGENCY DRILL:", report["emergency_drill_status"])
    for row in report["issues"][:10]:
        suffix = f" ({row['path']})" if row.get("path") else ""
        print(f"[{row['code']}]{suffix}: {row['message']}")
    print("STARTUP ALLOWED:", "YES" if report["startup_allowed"] else "NO")
    print("AUTOMATIC REAL BETTING: NO")
    print("API REQUESTS USED: 0")
    print("SHADOW ONLY: YES")
    print("=" * 72)
    print("JSON:", OUTPUT_FILE)


def run_once(root=".", now=None):
    root = Path(root)
    report = build_guard(root, now=now)
    write_json_atomic(root / OUTPUT_FILE, report)
    print_report(report)
    return report


def enforce_freeze(root=".", now=None):
    report = run_once(root=root, now=now)
    if not report["startup_allowed"]:
        commands = []
        codes = {row["code"] for row in report["issues"]}
        if "EMERGENCY_DRILL_REQUIRED" in codes or "EMERGENCY_DRILL_FAILED" in codes:
            commands.append("python .\\v3_emergency_drill.py")
        if any(code.startswith("FROZEN_FILE_") or code == "FREEZE_MANIFEST_INVALID" for code in codes):
            commands.append("git status --short")
            commands.append("git pull")
        hint = " | ".join(commands) if commands else "Review v3_freeze_guard_report.json"
        raise SystemExit("V3 startup blocked by Freeze Guard. Next: " + hint)
    return report


if __name__ == "__main__":
    result = run_once()
    raise SystemExit(0 if result["startup_allowed"] else 1)
