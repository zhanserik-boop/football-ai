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


# ------------------------------------------------------------
# CORE / AH
# ------------------------------------------------------------

MARKET_MONITOR = "market_monitor_v2.py"
AH_AGENT = "ah_agent_v2.py"
MASTER_AGENT = "master_agent_v1.py"
CLV_REPORT = "post_lineup_clv_report.py"


# ------------------------------------------------------------
# BTTS SHADOW
# ------------------------------------------------------------

BTTS_FIXTURE_FEED = "btts_fixture_feed.py"
BTTS_FEATURES = "btts_live_features.py"
BTTS_PREDICT = "btts_live_predict.py"
BTTS_AGENT = "btts_live_agent.py"


# ------------------------------------------------------------
# CURRENT-SEASON DATA UPDATERS
# ------------------------------------------------------------

XG_UPDATER = "download_xg.py"
SOT_UPDATER = "download_old_odds.py"


# ------------------------------------------------------------
# TELEGRAM
# ------------------------------------------------------------

AH_LATEST_FILE = "ah_agent_v2_latest.csv"
MASTER_LIVE_FILE = "master_decisions_live.csv"
BTTS_WATCH_FILE = "btts_live_watch.csv"

TELEGRAM_STATE_FILE = "telegram_alert_state.json"


# ------------------------------------------------------------
# CYCLES
# ------------------------------------------------------------

# AH + Master
LOCAL_CYCLE_SECONDS = 60

# BTTS pipeline
BTTS_CYCLE_SECONDS = 5 * 60

# CLV / settlement
REPORT_CYCLE_SECONDS = 15 * 60

# Current-season xG + SOT refresh
HISTORY_UPDATE_SECONDS = 6 * 60 * 60


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(
        timezone.utc
    )


# ============================================================
# SAFE VALUES
# ============================================================

def clean_value(value):

    if value is None:
        return ""

    text = str(value).strip()

    if text.lower() in (
        "",
        "nan",
        "none",
        "null",
    ):
        return ""

    return text


def first_value(
    row,
    names,
    default=""
):

    for name in names:

        if name not in row:
            continue

        value = clean_value(
            row.get(name)
        )

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


def format_number(
    value,
    decimals=2
):

    text = clean_value(value)

    if not text:
        return "-"

    try:
        number = float(text)
        return f"{number:.{decimals}f}"

    except Exception:
        return text


def format_signed(
    value,
    decimals=2
):

    text = clean_value(value)

    if not text:
        return "-"

    try:
        number = float(text)
        return f"{number:+.{decimals}f}"

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

        "telegram_notifier.py",

        "btts_core_model_2026.joblib",
        "btts_core_model_2026_meta.json",
    ]

    missing = [

        filename

        for filename in required

        if not os.path.exists(
            filename
        )
    ]

    if not missing:
        return

    print()
    print(
        "Missing required files:"
    )

    for filename in missing:

        print(
            " -",
            filename
        )

    raise SystemExit(1)


# ============================================================
# RUN ONE SCRIPT
# ============================================================

def run_script(
    filename
):

    print()
    print(
        "-" * 72
    )

    print(
        "RUN:",
        filename
    )

    print(
        "TIME:",
        utc_now().isoformat()
    )

    print(
        "-" * 72
    )

    try:

        result = subprocess.run(

            [
                PYTHON,
                filename
            ],

            check=False
        )

        if result.returncode != 0:

            print()

            print(
                filename,
                "returned code",
                result.returncode
            )

            return False

        return True

    except Exception as exc:

        print()

        print(
            filename,
            "ERROR:",
            repr(
                exc
            )
        )

        return False


# ============================================================
# START MARKET MONITOR
# ============================================================

def start_market_monitor():

    print()
    print(
        "=" * 72
    )

    print(
        "STARTING MARKET MONITOR"
    )

    print(
        "=" * 72
    )

    process = subprocess.Popen(

        [
            PYTHON,
            MARKET_MONITOR
        ]
    )

    print(
        "Market Monitor PID:",
        process.pid
    )

    return process


# ============================================================
# STOP MARKET MONITOR
# ============================================================

def stop_market_monitor(
    process
):

    if process is None:
        return

    if process.poll() is not None:
        return

    process.terminate()

    try:

        process.wait(
            timeout=10
        )

    except subprocess.TimeoutExpired:

        process.kill()


# ============================================================
# TELEGRAM STATE
# ============================================================

def default_telegram_state():

    return {
        "master": {},
        "ah_late": {},
        "btts": {},
    }


def load_telegram_state():

    if not os.path.exists(
        TELEGRAM_STATE_FILE
    ):

        return default_telegram_state()

    try:

        with open(
            TELEGRAM_STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            loaded = json.load(
                f
            )

        if not isinstance(
            loaded,
            dict
        ):

            return default_telegram_state()

        state = default_telegram_state()

        for key in state:

            value = loaded.get(
                key,
                {}
            )

            if isinstance(
                value,
                dict
            ):

                state[key] = value

        return state

    except Exception as exc:

        print(
            "Telegram state warning:",
            repr(exc)
        )

        return default_telegram_state()


def save_telegram_state(
    state
):

    temp_file = (
        TELEGRAM_STATE_FILE
        +
        ".tmp"
    )

    try:

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            temp_file,
            TELEGRAM_STATE_FILE
        )

    except Exception as exc:

        print(
            "Telegram state save warning:",
            repr(exc)
        )


# ============================================================
# CSV
# ============================================================

def read_csv_rows(
    filename
):

    if not os.path.exists(
        filename
    ):
        return []

    try:

        if os.path.getsize(
            filename
        ) == 0:
            return []

        with open(
            filename,
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as f:

            reader = csv.DictReader(
                f
            )

            if not reader.fieldnames:
                return []

            return list(
                reader
            )

    except Exception as exc:

        print(
            "Telegram CSV warning:",
            filename,
            repr(exc)
        )

        return []


# ============================================================
# TELEGRAM HELPERS
# ============================================================

def send_alert(
    message
):

    try:

        return send_telegram(
            message
        )

    except Exception as exc:

        print(
            "Telegram alert error:",
            repr(exc)
        )

        return False


def fixture_key(
    row
):

    fixture_id = first_value(
        row,
        [
            "fixture_id",
            "id",
        ]
    )

    if fixture_id:
        return fixture_id

    home = first_value(
        row,
        [
            "home_team",
            "home",
        ]
    )

    away = first_value(
        row,
        [
            "away_team",
            "away",
        ]
    )

    kickoff = first_value(
        row,
        [
            "kickoff_utc",
            "kickoff",
        ]
    )

    return (
        home
        +
        "|"
        +
        away
        +
        "|"
        +
        kickoff
    )


def match_name(
    row
):

    home = first_value(
        row,
        [
            "home_team",
            "home",
        ],
        "HOME"
    )

    away = first_value(
        row,
        [
            "away_team",
            "away",
        ],
        "AWAY"
    )

    return (
        f"{home} — {away}"
    )


# ============================================================
# MASTER TELEGRAM
# ============================================================

def notify_master(
    telegram_state
):

    rows = read_csv_rows(
        MASTER_LIVE_FILE
    )

    if not rows:
        return

    for row in rows:

        decision = first_value(
            row,
            [
                "master_decision",
                "decision",
                "master",
            ]
        ).upper()

        if decision not in (
            "BET",
            "WATCH",
        ):
            continue

        key = fixture_key(
            row
        )

        home = first_value(
            row,
            [
                "home_team",
                "home",
            ],
            "HOME"
        )

        away = first_value(
            row,
            [
                "away_team",
                "away",
            ],
            "AWAY"
        )

        side = first_value(
            row,
            [
                "primary_side",
                "signal",
                "side",
            ]
        ).upper()

        if side == "HOME":
            bet_team = home

        elif side == "AWAY":
            bet_team = away

        else:
            bet_team = side

        ah_decision = first_value(
            row,
            [
                "ah_decision",
            ]
        ).upper()

        handicap = first_value(
            row,
            [
                "ah_handicap",
                "current_handicap",
                "handicap",
            ]
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
            ]
        )

        bookmaker = first_value(
            row,
            [
                "ah_best_bookmaker",
                "current_best_bookmaker",
                "best_bookmaker",
                "bookmaker",
            ]
        )

        confidence = first_value(
            row,
            [
                "confidence",
            ]
        )

        line_move = first_value(
            row,
            [
                "line_move_toward_signal",
                "line_move",
            ]
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

        if (
            telegram_state[
                "master"
            ].get(
                key
            )
            ==
            fingerprint
        ):
            continue

        icon = (
            "??"
            if decision == "BET"
            else "??"
        )

        lines = [
            f"{icon} LineupAI ? {decision}",
            "",
            match_name(row),
            "",
        ]

        if bet_team and handicap:

            label = (
                "BET"
                if decision == "BET"
                else "WATCH"
            )

            lines.append(
                f"{label}: {bet_team} {handicap}"
            )

        elif handicap:

            lines.append(
                f"Asian Handicap: {handicap}"
            )

        if odds:

            lines.append(
                f"Best odds: {format_number(odds, 3)}"
            )

        if bookmaker:

            lines.append(
                f"Bookmaker: {bookmaker}"
            )

        if confidence:

            lines.append(
                "Confidence: "
                +
                format_probability(
                    confidence
                )
            )

        if ah_decision:

            lines.append(
                f"AH Agent: {ah_decision}"
            )

        if line_move:

            lines.append(
                "Line move: "
                +
                format_signed(
                    line_move,
                    3
                )
            )

        message = "\n".join(
            lines
        )

        if send_alert(
            message
        ):

            telegram_state[
                "master"
            ][
                key
            ] = fingerprint

            save_telegram_state(
                telegram_state
            )


# ============================================================
# AH LATE TELEGRAM
# ============================================================

def notify_ah_late(
    telegram_state
):

    rows = read_csv_rows(
        AH_LATEST_FILE
    )

    if not rows:
        return

    for row in rows:

        decision = first_value(
            row,
            [
                "decision",
                "ah_decision",
            ]
        ).upper()

        if decision != "LATE":
            continue

        key = fixture_key(
            row
        )

        handicap = first_value(
            row,
            [
                "current_handicap",
                "ah_handicap",
                "handicap",
            ]
        )

        odds = first_value(
            row,
            [
                "current_avg_odds",
                "ah_avg_odds",
                "odds",
            ]
        )

        line_move = first_value(
            row,
            [
                "line_move_toward_signal",
                "line_move",
            ]
        )

        reason = first_value(
            row,
            [
                "reason",
            ]
        )

        fingerprint = "|".join(
            [
                decision,
                handicap,
                odds,
                line_move,
                reason,
            ]
        )

        if (
            telegram_state[
                "ah_late"
            ].get(
                key
            )
            ==
            fingerprint
        ):
            continue

        lines = [
            "⏰ LineupAI — LATE",
            "",
            match_name(row),
            "",
            "AH Agent: LATE",
        ]

        if handicap:

            lines.append(
                f"Current AH: {handicap}"
            )

        if odds:

            lines.append(
                f"Current odds: {format_number(odds, 3)}"
            )

        if line_move:

            lines.append(
                "Line move: "
                +
                format_signed(
                    line_move,
                    3
                )
            )

        if reason:

            lines.extend(
                [
                    "",
                    reason,
                ]
            )

        lines.extend(
            [
                "",
                "Market has already moved. "
                "Do not chase the price."
            ]
        )

        message = "\n".join(
            lines
        )

        if send_alert(
            message
        ):

            telegram_state[
                "ah_late"
            ][
                key
            ] = fingerprint

            save_telegram_state(
                telegram_state
            )


# ============================================================
# BTTS TELEGRAM
# ============================================================

def notify_btts(
    telegram_state
):

    rows = read_csv_rows(
        BTTS_WATCH_FILE
    )

    if not rows:
        return

    for row in rows:

        decision = first_value(
            row,
            [
                "decision",
                "agent_decision",
                "status",
            ]
        ).upper()

        normalized = (
            decision
            .replace(
                "_",
                " "
            )
            .replace(
                "-",
                " "
            )
        )

        if normalized != "SHADOW BET":
            continue

        key = fixture_key(
            row
        )

        model_yes = first_value(
            row,
            [
                "model_yes",
            ]
        )

        market_yes = first_value(
            row,
            [
                "market_yes",
            ]
        )

        edge_yes = first_value(
            row,
            [
                "edge_yes",
                "edge",
            ]
        )

        best_odds = first_value(
            row,
            [
                "best_yes_odds",
                "best_odds",
            ]
        )

        bookmaker = first_value(
            row,
            [
                "best_yes_bookmaker",
                "best_bookmaker",
            ]
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

        if (
            telegram_state[
                "btts"
            ].get(
                key
            )
            ==
            fingerprint
        ):
            continue

        lines = [
            "⚽ LineupAI — BTTS SHADOW BET",
            "",
            match_name(row),
            "",
            "Market: Both Teams To Score — YES",
        ]

        if model_yes:

            lines.append(
                "Model YES: "
                +
                format_probability(
                    model_yes
                )
            )

        if market_yes:

            lines.append(
                "Market YES: "
                +
                format_probability(
                    market_yes
                )
            )

        if edge_yes:

            lines.append(
                "Edge: "
                +
                format_probability(
                    edge_yes
                )
            )

        if best_odds:

            lines.append(
                "Best odds: "
                +
                format_number(
                    best_odds,
                    3
                )
            )

        if bookmaker:

            lines.append(
                f"Bookmaker: {bookmaker}"
            )

        lines.extend(
            [
                "",
                "Status: SHADOW FORWARD TEST",
            ]
        )

        message = "\n".join(
            lines
        )

        if send_alert(
            message
        ):

            telegram_state[
                "btts"
            ][
                key
            ] = fingerprint

            save_telegram_state(
                telegram_state
            )


# ============================================================
# ALL TELEGRAM SIGNALS
# ============================================================

def process_telegram_alerts(
    telegram_state
):

    notify_ah_late(
        telegram_state
    )

    notify_master(
        telegram_state
    )

    notify_btts(
        telegram_state
    )


# ============================================================
# CURRENT-SEASON HISTORY UPDATE
#
# xG:
#   Understat EPL 2026
#   -> epl_xg_history.csv
#
# SOT:
#   Football-Data EPL 2026/27
#   -> epl_odds_2026.csv
#
# Both updaters are defensive:
# - no future/incomplete matches
# - no API-Football usage
# - no destructive empty overwrite
# - SOT validates EPL identity
# ============================================================

def run_history_update():

    print()
    print(
        "=" * 72
    )

    print(
        "CURRENT-SEASON xG / SOT UPDATE"
    )

    print(
        "=" * 72
    )

    xg_ok = run_script(
        XG_UPDATER
    )

    if not xg_ok:

        print()
        print(
            "WARNING: xG updater failed."
        )

        print(
            "Existing xG history remains in use."
        )

    sot_ok = run_script(
        SOT_UPDATER
    )

    if not sot_ok:

        print()
        print(
            "WARNING: SOT updater failed."
        )

        print(
            "Existing SOT files remain in use."
        )

    print()
    print(
        "Current-season data update finished."
    )

    return (
        xg_ok
        and
        sot_ok
    )


# ============================================================
# BTTS PIPELINE
#
# Always runs all local stages.
#
# Empty current state propagates safely:
#
# fixture feed
#    ↓
# features
#    ↓
# predictions
#    ↓
# shadow agent
#
# This prevents stale decisions.
# ============================================================

def run_btts_pipeline():

    print()
    print(
        "=" * 72
    )

    print(
        "BTTS SHADOW PIPELINE"
    )

    print(
        "=" * 72
    )

    # --------------------------------------------------------
    # 1. FIXTURES
    # API = 0
    # --------------------------------------------------------

    ok = run_script(
        BTTS_FIXTURE_FEED
    )

    if not ok:

        print(
            "BTTS pipeline stopped: "
            "fixture feed failed."
        )

        return False

    # --------------------------------------------------------
    # 2. FEATURES
    # API = 0
    # --------------------------------------------------------

    ok = run_script(
        BTTS_FEATURES
    )

    if not ok:

        print(
            "BTTS pipeline stopped: "
            "feature engine failed."
        )

        return False

    # --------------------------------------------------------
    # 3. FROZEN MODEL
    # API = 0
    # --------------------------------------------------------

    ok = run_script(
        BTTS_PREDICT
    )

    if not ok:

        print(
            "BTTS pipeline stopped: "
            "prediction engine failed."
        )

        return False

    # --------------------------------------------------------
    # 4. SHADOW AGENT
    #
    # API-Football odds request only when:
    #
    # data_quality == OK
    # AND
    # model probability is inside 60-65%
    #
    # Otherwise API = 0.
    # --------------------------------------------------------

    ok = run_script(
        BTTS_AGENT
    )

    if not ok:

        print(
            "BTTS pipeline stopped: "
            "shadow agent failed."
        )

        return False

    print()
    print(
        "BTTS shadow pipeline completed."
    )

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    check_files()

    telegram_state = (
        load_telegram_state()
    )

    print()
    print(
        "=" * 72
    )

    print(
        "FOOTBALL AI — LIVE SYSTEM"
    )

    print(
        "=" * 72
    )

    print(
        "Python:",
        PYTHON
    )

    print(
        "AH / Master cycle:",
        LOCAL_CYCLE_SECONDS,
        "seconds"
    )

    print(
        "BTTS cycle:",
        BTTS_CYCLE_SECONDS // 60,
        "minutes"
    )

    print(
        "CLV / settlement cycle:",
        REPORT_CYCLE_SECONDS // 60,
        "minutes"
    )

    print(
        "xG / SOT update cycle:",
        HISTORY_UPDATE_SECONDS // 3600,
        "hours"
    )

    print()

    print(
        "AH STATUS: LIVE FORWARD TEST"
    )

    print(
        "BTTS STATUS: SHADOW FORWARD TEST"
    )

    print()

    print(
        "Press CTRL+C to stop entire system."
    )

    # ========================================================
    # MARKET MONITOR
    # ========================================================

    market_process = (
        start_market_monitor()
    )

    send_alert(
        "✅ LineupAI — Football AI started\n\n"
        "Market Monitor: running\n"
        "AH Agent: running\n"
        "Master Agent: running\n"
        "BTTS Shadow: running"
    )

    # ========================================================
    # FORCE ALL SCHEDULED JOBS ON FIRST LOOP
    # ========================================================

    last_report_time = 0
    last_btts_time = 0
    last_history_update_time = 0

    try:

        while True:

            # =================================================
            # MARKET MONITOR HEALTH
            # =================================================

            if (
                market_process.poll()
                is not None
            ):

                print()

                print(
                    "Market Monitor stopped "
                    "unexpectedly."
                )

                send_alert(
                    "⚠️ LineupAI\n\n"
                    "Market Monitor stopped unexpectedly.\n"
                    "Automatic restart in 10 seconds."
                )

                print(
                    "Restarting in 10 seconds..."
                )

                time.sleep(
                    10
                )

                market_process = (
                    start_market_monitor()
                )

                send_alert(
                    "✅ LineupAI\n\n"
                    "Market Monitor restarted successfully."
                )

            now_ts = time.time()

            # =================================================
            # CURRENT-SEASON xG / SOT
            #
            # Run before BTTS features so completed matches
            # can enter rolling state first.
            # =================================================

            if (
                now_ts
                -
                last_history_update_time
                >=
                HISTORY_UPDATE_SECONDS
            ):

                run_history_update()

                last_history_update_time = (
                    time.time()
                )

            # =================================================
            # AH AGENT
            # =================================================

            run_script(
                AH_AGENT
            )

            # =================================================
            # MASTER AGENT
            # =================================================

            run_script(
                MASTER_AGENT
            )

            # After AH + Master decisions are refreshed,
            # send only new/changed important alerts.

            notify_ah_late(
                telegram_state
            )

            notify_master(
                telegram_state
            )

            now_ts = time.time()

            # =================================================
            # BTTS SHADOW
            # =================================================

            if (
                now_ts
                -
                last_btts_time
                >=
                BTTS_CYCLE_SECONDS
            ):

                run_btts_pipeline()

                notify_btts(
                    telegram_state
                )

                last_btts_time = (
                    time.time()
                )

            now_ts = time.time()

            # =================================================
            # CLV / SETTLEMENT
            # =================================================

            if (
                now_ts
                -
                last_report_time
                >=
                REPORT_CYCLE_SECONDS
            ):

                run_script(
                    CLV_REPORT
                )

                last_report_time = (
                    time.time()
                )

            # =================================================
            # NEXT LOOP
            # =================================================

            print()
            print(
                "=" * 72
            )

            print(
                "NEXT AH / MASTER CYCLE IN",
                LOCAL_CYCLE_SECONDS,
                "SECONDS"
            )

            print(
                "=" * 72
            )

            time.sleep(
                LOCAL_CYCLE_SECONDS
            )

    except KeyboardInterrupt:

        print()

        print(
            "Stopping Football AI..."
        )

        stop_market_monitor(
            market_process
        )

        send_alert(
            "⏹ LineupAI — Football AI stopped."
        )

        print(
            "Football AI stopped."
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()