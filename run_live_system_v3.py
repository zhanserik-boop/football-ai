import os

import run_live_system as base

LIVE_COACH_CONTEXT = "live_coach_context.py"
MARKET_TIMELINE = "market_timeline_engine.py"
SHADOW_VALUE_GATE = "shadow_value_gate_v1.py"

_original_run_script = base.run_script


def run_script_with_v3_context(filename):
    # Build shadow coach context immediately before the AH/Master cycle.
    if filename == base.AH_AGENT:
        _original_run_script(LIVE_COACH_CONTEXT)

    ok = _original_run_script(filename)

    # After Master has written its decision, rebuild timeline/audit and then
    # run the independent shadow gate. Live AH/Master decisions are unchanged.
    if filename == base.MASTER_AGENT:
        _original_run_script(MARKET_TIMELINE)
        _original_run_script(SHADOW_VALUE_GATE)

    return ok


def main():
    missing = [
        path for path in (
            LIVE_COACH_CONTEXT,
            MARKET_TIMELINE,
            SHADOW_VALUE_GATE,
        )
        if not os.path.exists(path)
    ]
    if missing:
        raise SystemExit("Missing V3 live files: " + ", ".join(missing))

    base.run_script = run_script_with_v3_context
    print("V3 orchestration: Coach + Timeline + Shadow Value Gate enabled")
    base.main()


if __name__ == "__main__":
    main()
