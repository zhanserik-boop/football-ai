import os
import sys
import csv
import json
import time
import subprocess
from datetime import datetime, timezone

from telegram_notifier import send_telegram


# ============================================================
# CONFIG
# ============================================================

PYTHON = sys.executable

MARKET_MONITOR = "market_monitor_v2.py"
AH_AGENT = "ah_agent_v2.py"
MASTER_AGENT = "master_agent_v1.py"
CLV_REPORT = "post_lineup_clv_report.py"

BTTS_FIXTURE_FEED = "btts_fixture_feed.py"
BTTS_FEATURES = "btts_live_features.py"
BTTS_PREDICT = "btts_live_predict.py"
BTTS_AGENT = "btts_live_agent.py"

XG_UPDATER = "download_xg.py"
SOT_UPDATER = "download_old_odds.py"
SQUAD_UPDATER = "update_squads_transfers.py"
CURRENT_SQUADS_FILE = "current_squads_2026.csv"

AH_LATEST_FILE = "ah_agent_v2_latest.csv"
MASTER_LIVE_FILE = "master_decisions_live.csv"
BTTS_WATCH_FILE = "btts_live_watch.csv"
TELEGRAM_STATE_FILE = "telegram_alert_state.json"

LOCAL_CYCLE_SECONDS = 60
BTTS_CYCLE_SECONDS = 5 * 60
REPORT_CYCLE_SECONDS = 15 * 60
HISTORY_UPDATE_SECONDS = 6 * 60 * 60
SQUAD_UPDATE_SECONDS = 24 * 60 * 60


# ============================================================
# BASIC HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def clean_value(value):
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("", "nan", "none", "null"):
        return ""
    return text


def first_value(row, names, default=""):
    for name in names:
        if name not in row:
            continue
        value = clean_value(row.get(name))
        if value:
            return value
    return default


def format_probability(value):
    text = clean_value(value)
    if not text:
        return "-"
    try:
        number = float(text)
        if number <= 1:
            return f"{number * 100:.1f}%"
        return f"{number:.1f}%"
    except Exception:
        return text


def format_number(value, decimals=2):
    text = clean_value(value)
    if not text:
        return "-"
    try:
        return f"{float(text):.{decimals}f}"
    except Exception:
        return text


def format_signed(value, decimals=2):
    text = clean_value(value)
    if not text:
        return "-"
    try:
        return f"{float(text):+.{decimals}f}"
    except Exception:
        return text


# ============================================================
# REQUIRED FILES
# ============================================================

def check_files():
    required = [
        MARKET_MONITOR,
        AH_AGENT,
        MASTER_AGENT,
        CLV_REPORT,
        BTTS_FIXTURE_FEED,
        BTTS_FEATURES,
        BTTS_PREDICT,
        BTTS_AGENT,
        XG_UPDATER,
        SOT_UPDATER,
        SQUAD_UPDATER,
        "telegram_notifier.py",
        "btts_core_model_2026.joblib",
        "btts_core_model_2026_meta.json",
    ]

    missing = [x for x in required if not os.path.exists(x)]
    if not missing:
        return

    print("\nMissing required files:")
    for filename in missing:
        print(" -", filename)
    raise SystemExit(1)


# ============================================================
# SCRIPT PROCESS HELPERS
# ============================================================

def run_script(filename):
    print("\n" + "-" * 72)
    print("RUN:", filename)
    print("TIME:", utc_now().isoformat())
    print("-" * 72)

    try:
        result = subprocess.run([PYTHON, filename], check=False)
        if result.returncode != 0:
            print(filename, "returned code", result.returncode)
            return False
        return True
    except Exception as exc:
        print(filename, "ERROR:", repr(exc))
        return False


def start_market_monitor():
    print("\n" + "=" * 72)
    print("STARTING MARKET MONITOR")
    print("=" * 72)
    process = subprocess.Popen([PYTHON, MARKET_MONITOR])
    print("Market Monitor PID:", process.pid)
    return process


def stop_market_monitor(process):
    if process is None or process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


# ============================================================
# TELEGRAM STATE / CSV
# ============================================================

def default_telegram_state():
    return {"master": {}, "ah_late": {}, "btts": {}}


def load_telegram_state():
    if not os.path.exists(TELEGRAM_STATE_FILE):
        return default_telegram_state()

    try:
        with open(TELEGRAM_STATE_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        if not isinstance(loaded, dict):
            return default_telegram_state()

        state = default_telegram_state()
        for key in state:
            value = loaded.get(key, {})
            if isinstance(value, dict):
                state[key] = value
        return state
    except Exception as exc:
        print("Telegram state warning:", repr(exc))
        return default_telegram_state()


def save_telegram_state(state):
    temp_file = TELEGRAM_STATE_FILE + ".tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, TELEGRAM_STATE_FILE)
    except Exception as exc:
        print("Telegram state save warning:", repr(exc))


def read_csv_rows(filename):
    if not os.path.exists(filename):
        return []
    try:
        if os.path.getsize(filename) == 0:
            return []
        with open(filename, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return []
            return list(reader)
    except Exception as exc:
        print("Telegram CSV warning:", filename, repr(exc))
        return []


def send_alert(message):
    try:
        return send_telegram(message)
    except Exception as exc:
        print("Telegram alert error:", repr(exc))
        return False


def fixture_key(row):
    fixture_id = first_value(row, ["fixture_id", "id"])
    if fixture_id:
        return fixture_id

    home = first_value(row, ["home_team", "home"])
    away = first_value(row, ["away_team", "away"])
    kickoff = first_value(row, ["kickoff_utc", "kickoff"])
    return home + "|" + away + "|" + kickoff


def match_name(row):
    home = first_value(row, ["home_team", "home"], "HOME")
    away = first_value(row, ["away_team", "away"], "AWAY")
    return f"{home} — {away}"


# ============================================================
# TELEGRAM NOTIFICATIONS
# ============================================================

def notify_master(telegram_state):
    rows = read_csv_rows(MASTER_LIVE_FILE)
    for row in rows:
        decision = first_value(
            row, ["master_decision", "decision", "master"]
        ).upper()
        if decision not in ("BET", "WATCH"):
            continue

        key = fixture_key(row)
        home = first_value(row, ["home_team", "home"], "HOME")
        away = first_value(row, ["away_team", "away"], "AWAY")
        side = first_value(row, ["primary_side", "signal", "side"]).upper()
        bet_team = home if side == "HOME" else away if side == "AWAY" else side

        ah_decision = first_value(row, ["ah_decision"]).upper()
        handicap = first_value(
            row, ["ah_handicap", "current_handicap", "handicap"]
        )
        odds = first_value(
            row,
            [
                "ah_best_odds",
                "current_best_odds",
                "ah_avg_odds",
                "current_avg_odds",
                "avg_odds",
                "odds",
            ],
        )
        bookmaker = first_value(
            row,
            [
                "ah_best_bookmaker",
                "current_best_bookmaker",
                "best_bookmaker",
                "bookmaker",
            ],
        )
        confidence = first_value(row, ["confidence"])
        line_move = first_value(
            row, ["line_move_toward_signal", "line_move"]
        )

        fingerprint = "|".join(
            [
                decision,
                side,
                handicap,
                odds,
                bookmaker,
                confidence,
                line_move,
            ]
        )
        if telegram_state["master"].get(key) == fingerprint:
            continue

        lines = [
            f"LineupAI — {decision}",
            "",
            match_name(row),
            "",
        ]

        if bet_team and handicap:
            label = "BET" if decision == "BET" else "WATCH"
            lines.append(f"{label}: {bet_team} {handicap}")
        elif handicap:
            lines.append(f"Asian Handicap: {handicap}")

        if odds:
            lines.append(f"Best odds: {format_number(odds, 3)}")
        if bookmaker:
            lines.append(f"Bookmaker: {bookmaker}")
        if confidence:
            lines.append("Confidence: " + format_probability(confidence))
        if ah_decision:
            lines.append(f"AH Agent: {ah_decision}")
        if line_move:
            lines.append("Line move: " + format_signed(line_move, 3))

        if send_alert("\n".join(lines)):
            telegram_state["master"][key] = fingerprint
            save_telegram_state(telegram_state)


def notify_ah_late(telegram_state):
    rows = read_csv_rows(AH_LATEST_FILE)
    for row in rows:
        decision = first_value(row, ["decision", "ah_decision"]).upper()
        if decision != "LATE":
            continue

        key = fixture_key(row)
        handicap = first_value(
            row, ["current_handicap", "ah_handicap", "handicap"]
        )
        odds = first_value(
            row, ["current_avg_odds", "ah_avg_odds", "odds"]
        )
        line_move = first_value(
            row, ["line_move_toward_signal", "line_move"]
        )
        reason = first_value(row, ["reason"])

        fingerprint = "|".join(
            [decision, handicap, odds, line_move, reason]
        )
        if telegram_state["ah_late"].get(key) == fingerprint:
            continue

        lines = [
            "LineupAI — LATE",
            "",
            match_name(row),
            "",
            "AH Agent: LATE",
        ]
        if handicap:
            lines.append(f"Current AH: {handicap}")
        if odds:
            lines.append(f"Current odds: {format_number(odds, 3)}")
        if line_move:
            lines.append("Line move: " + format_signed(line_move, 3))
        if reason:
            lines.extend(["", reason])
        lines.extend(["", "Market has already moved. Do not chase the price."])

        if send_alert("\n".join(lines)):
            telegram_state["ah_late"][key] = fingerprint
            save_telegram_state(telegram_state)


def notify_btts(telegram_state):
    rows = read_csv_rows(BTTS_WATCH_FILE)
    for row in rows:
        decision = first_value(
            row, ["decision", "agent_decision", "status"]
        ).upper()
        normalized = decision.replace("_", " ").replace("-", " ")
        if normalized != "SHADOW BET":
            continue

        key = fixture_key(row)
        model_yes = first_value(row, ["model_yes"])
        market_yes = first_value(row, ["market_yes"])
        edge_yes = first_value(row, ["edge_yes", "edge"])
        best_odds = first_value(row, ["best_yes_odds", "best_odds"])
        bookmaker = first_value(
            row, ["best_yes_bookmaker", "best_bookmaker"]
        )

        fingerprint = "|".join(
            [
                normalized,
                model_yes,
                market_yes,
                edge_yes,
                best_odds,
                bookmaker,
            ]
        )
        if telegram_state["btts"].get(key) == fingerprint:
            continue

        lines = [
            "LineupAI — BTTS SHADOW BET",
            "",
            match_name(row),
            "",
            "Market: Both Teams To Score — YES",
        ]
        if model_yes:
            lines.append("Model YES: " + format_probability(model_yes))
        if market_yes:
            lines.append("Market YES: " + format_probability(market_yes))
        if edge_yes:
            lines.append("Edge: " + format_probability(edge_yes))
        if best_odds:
            lines.append("Best odds: " + format_number(best_odds, 3))
        if bookmaker:
            lines.append(f"Bookmaker: {bookmaker}")
        lines.extend(["", "Status: SHADOW FORWARD TEST"])

        if send_alert("\n".join(lines)):
            telegram_state["btts"][key] = fingerprint
            save_telegram_state(telegram_state)


# ============================================================
# CURRENT SQUAD REFRESH
# ============================================================

def squad_update_due():
    if not os.path.exists(CURRENT_SQUADS_FILE):
        return True

    try:
        age_seconds = time.time() - os.path.getmtime(CURRENT_SQUADS_FILE)
        return age_seconds >= SQUAD_UPDATE_SECONDS
    except OSError:
        return True


def run_squad_update(force=False):
    if not force and not squad_update_due():
        age_hours = (
            time.time() - os.path.getmtime(CURRENT_SQUADS_FILE)
        ) / 3600.0
        print(
            f"Current squad snapshot is fresh ({age_hours:.1f}h old). "
            "Daily refresh not due."
        )
        return True, False

    print("\n" + "=" * 72)
    print("CURRENT EPL SQUADS / TRANSFERS UPDATE")
    print("=" * 72)

    ok = run_script(SQUAD_UPDATER)

    if not ok:
        if os.path.exists(CURRENT_SQUADS_FILE):
            print(
                "WARNING: squad updater failed. Existing validated squad "
                "snapshot remains in use."
            )
            return False, False

        print(
            "ERROR: squad updater failed and no current squad snapshot exists."
        )
        return False, False

    if not os.path.exists(CURRENT_SQUADS_FILE):
        print("ERROR: updater returned success but squad file is missing.")
        return False, False

    print("Current squad snapshot refreshed successfully.")
    return True, True


# ============================================================
# xG / SOT UPDATE
# ============================================================

def run_history_update():
    print("\n" + "=" * 72)
    print("CURRENT-SEASON xG / SOT UPDATE")
    print("=" * 72)

    xg_ok = run_script(XG_UPDATER)
    if not xg_ok:
        print("WARNING: xG updater failed. Existing xG history remains in use.")

    sot_ok = run_script(SOT_UPDATER)
    if not sot_ok:
        print("WARNING: SOT updater failed. Existing SOT files remain in use.")

    print("\nCurrent-season data update finished.")
    return xg_ok and sot_ok


# ============================================================
# BTTS PIPELINE
# ============================================================

def run_btts_pipeline():
    print("\n" + "=" * 72)
    print("BTTS SHADOW PIPELINE")
    print("=" * 72)

    stages = [
        (BTTS_FIXTURE_FEED, "fixture feed"),
        (BTTS_FEATURES, "feature engine"),
        (BTTS_PREDICT, "prediction engine"),
        (BTTS_AGENT, "shadow agent"),
    ]

    for filename, label in stages:
        if not run_script(filename):
            print(f"BTTS pipeline stopped: {label} failed.")
            return False

    print("\nBTTS shadow pipeline completed.")
    return True


# ============================================================
# MAIN
# ============================================================

def main():
    check_files()
    telegram_state = load_telegram_state()

    print("\n" + "=" * 72)
    print("FOOTBALL AI — LIVE SYSTEM")
    print("=" * 72)
    print("Python:", PYTHON)
    print("AH / Master cycle:", LOCAL_CYCLE_SECONDS, "seconds")
    print("BTTS cycle:", BTTS_CYCLE_SECONDS // 60, "minutes")
    print("CLV / settlement cycle:", REPORT_CYCLE_SECONDS // 60, "minutes")
    print("xG / SOT update cycle:", HISTORY_UPDATE_SECONDS // 3600, "hours")
    print("Squad / transfer update cycle:", SQUAD_UPDATE_SECONDS // 3600, "hours")
    print("\nAH STATUS: LIVE FORWARD TEST")
    print("BTTS STATUS: SHADOW FORWARD TEST")
    print("\nPress CTRL+C to stop entire system.")

    # The Lineup Engine loads current_squads_2026.csv at process start.
    # Therefore squad refresh must happen BEFORE Market Monitor starts.
    squad_ok, _ = run_squad_update(force=False)
    if not squad_ok and not os.path.exists(CURRENT_SQUADS_FILE):
        raise SystemExit(1)

    market_process = start_market_monitor()

    send_alert(
        "✅ LineupAI — Football AI started\n\n"
        "Market Monitor: running\n"
        "AH Agent: running\n"
        "Master Agent: running\n"
        "BTTS Shadow: running"
    )

    last_report_time = 0
    last_btts_time = 0
    last_history_update_time = 0

    try:
        while True:
            # Market Monitor health / restart.
            if market_process.poll() is not None:
                print("\nMarket Monitor stopped unexpectedly.")
                send_alert(
                    "⚠️ LineupAI\n\n"
                    "Market Monitor stopped unexpectedly.\n"
                    "Automatic restart in 10 seconds."
                )
                time.sleep(10)
                market_process = start_market_monitor()
                send_alert(
                    "✅ LineupAI\n\nMarket Monitor restarted successfully."
                )

            # Daily squad refresh. If a fresh snapshot is produced,
            # restart Market Monitor so LiveLineupEngine reloads it.
            if squad_update_due():
                squad_ok, refreshed = run_squad_update(force=True)
                if refreshed:
                    print(
                        "Restarting Market Monitor to load refreshed "
                        "current squads..."
                    )
                    stop_market_monitor(market_process)
                    market_process = start_market_monitor()
                elif not squad_ok:
                    print(
                        "Squad refresh failed; keeping existing monitor and "
                        "last validated squad snapshot."
                    )

            now_ts = time.time()

            if (
                now_ts - last_history_update_time
                >= HISTORY_UPDATE_SECONDS
            ):
                run_history_update()
                last_history_update_time = time.time()

            run_script(AH_AGENT)
            run_script(MASTER_AGENT)
            notify_ah_late(telegram_state)
            notify_master(telegram_state)

            now_ts = time.time()
            if now_ts - last_btts_time >= BTTS_CYCLE_SECONDS:
                run_btts_pipeline()
                notify_btts(telegram_state)
                last_btts_time = time.time()

            now_ts = time.time()
            if now_ts - last_report_time >= REPORT_CYCLE_SECONDS:
                run_script(CLV_REPORT)
                last_report_time = time.time()

            print("\n" + "=" * 72)
            print(
                "NEXT AH / MASTER CYCLE IN",
                LOCAL_CYCLE_SECONDS,
                "SECONDS",
            )
            print("=" * 72)
            time.sleep(LOCAL_CYCLE_SECONDS)

    except KeyboardInterrupt:
        print("\nStopping Football AI...")
        stop_market_monitor(market_process)
        send_alert("⏹ LineupAI — Football AI stopped.")
        print("Football AI stopped.")


if __name__ == "__main__":
    main()
