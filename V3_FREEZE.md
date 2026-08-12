# Football AI V3 — Shadow Freeze

Frozen release: `V3_SHADOW_FROZEN_R2`

Revision R2 repairs a critical API-Football Asian Handicap normalization defect.
The provider can label Home and Away prices with the same numeric home-team
handicap. R2 pairs both prices on the same bookmaker line, selects the balanced
main line before cross-book consensus, converts the result to the signal team's
perspective, and validates HOME/AWAY movement, CLV and settlement separately.

R1 AH-derived forward evidence is not compatible with R2. Before the first R2
startup, run the recoverable migration:

```powershell
python .\v3_r2_runtime_migration.py --apply
```

It moves only allow-listed runtime evidence into a timestamped
`runtime_archives\v3_r1_before_r2_*` directory, verifies SHA-256 after each
move, never includes secrets, and restarts the forward test from zero.

V3 is operationally complete and frozen in shadow-only mode. R2 changes only
the AH data contract and its downstream market calculations; decision
thresholds and Master policy remain unchanged. V3 does not place real bets
automatically.

## Frozen decision chain

The frozen chain combines Lineup Shock, Directional CLV prior, market freshness, new-manager and matchup context, the Value Gate, health kill switch, forward-test scorecard, drift monitoring, risk simulation, checkpoints, external backup, supervisor alerts, and the daily digest.

`v3_frozen_manifest.json` contains Git-compatible blob hashes for every frozen runtime file. `v3_freeze_guard.py` verifies those hashes and the local emergency drill before each V3 startup. A missing or changed frozen file blocks the next startup and does not rewrite the approved manifest.

## Change policy

Development of the decision engine is paused while forward evidence accumulates. Changes are allowed only for a critical bug, security issue, broken provider contract, or data-integrity failure. Every allowed change requires a reviewed pull request, all safety tests, a new emergency drill, a new manifest, and a new version identifier.

Threshold tuning, new predictors, and strategy expansion are postponed until the current frozen version has enough forward data. This prevents moving the goalposts while measuring performance.

## Promotion evidence

V3 remains shadow-only until all of these conditions are met:

- at least 50 qualifying closing-line observations;
- positive CLV with the lower bound of the 95% confidence interval above zero;
- at least 100 settled shadow bets;
- positive ROI with the lower bound of the 95% confidence interval above zero;
- at least 20 distinct forward-test days;
- Drift Watch is stable;
- health, supervisor, checkpoint, and external mirror are operational;
- a manual review explicitly approves a controlled pilot.

Passing these gates does not activate real betting automatically. It only permits a separate manual decision about a limited pilot.
