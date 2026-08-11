# Football AI V4 Multi-League Shadow

V4 is an isolated research runner for cross-league European qualifiers. It does
not import, edit, or influence the frozen V3 EPL runtime. It cannot place bets.

## Agents

1. Quant Agent: recent weighted team strength and Fair Asian Handicap.
2. Market Agent: current/opening AH, bookmaker consensus, movement and freshness.
   When a bookmaker supplies an alternative-line ladder, V4 selects that
   bookmaker's balanced main line before building cross-book consensus.
   API-Football's `Home` and `Away` prices are paired by the same numeric AH
   label; the label is interpreted as the home-team handicap.
3. Lineup Agent: confirmed XI availability and injury imbalance. The first
   release explicitly marks cross-league player values as unvalidated.
4. Matchup Agent: attack-versus-defence pressure.
5. Underdog Resistance Agent: draw, unbeaten, low-scoring and narrow-loss profile.
6. Draw Pressure Agent: protection against overrating favourites.
7. Context Agent: rest difference and qualification-stage volatility.
8. Data Quality Agent: fail-closed evidence validation.
9. Risk/Moderator Agent: compares independent evidence and issues `SHADOW BET`,
   `WATCH`, or `PASS` without majority voting. The bet side is derived from the
   signed Fair-AH-versus-market difference, not from the Quant strength side:
   a home market line at least `0.25` above Fair AH is HOME value, while a line
   at least `0.25` below Fair AH is AWAY value.

## Run

The existing `.env` API-Football credential is reused. No new key is required.

```powershell
python .\v4_multileague_shadow.py
```

Outputs:

- `v4_multileague_predictions.csv`
- `v4_multileague_predictions.json`
- `v4_market_diagnostics.json` (raw AH labels, normalized lines, per-book main-line selection)
- `v4_multileague_state.json`
- cached API responses under `v4_cache\`

The market diagnostics file contains no API credential or other secret. It is
intended for validating provider-specific Asian Handicap direction and
alternative-line mapping before any cross-league market signal is trusted.

The supplied target file contains the 12 matches from the 11 August 2026 image.
For a different slate, copy the CSV and pass `--input`.

For matchday monitoring, run the bounded watcher instead of repeatedly launching
the one-shot command by hand:

```powershell
python .\v4_matchday_watch.py
```

It refreshes at approximately T-60, T-45, T-30, T-20, T-10 and T-5 for fixtures
whose XI is still unavailable. It stops when no pre-match fixture is waiting, after
the final checkpoint, or at the configured `--max-hours` limit. This avoids
continuous polling while covering API-Football's usual publication window.

Each watcher cycle also runs `v4_lineup_source_router.py`. The router uses the
public JSON feed behind ESPN's soccer scoreboard, matches the fixture by teams
and kickoff, rejects any event that is no longer pre-match, and compares the
published starters with API-Football. It writes:

- `v4_lineup_source_audit.csv`
- `v4_lineup_source_audit.json`

The statuses are `ESPN_ONLY_RESEARCH`, `API_FOOTBALL_ONLY`,
`VERIFIED_TWO_SOURCES`, `SOURCE_CONFLICT`, or `WAITING`. ESPN-only data remains
research evidence: it cannot clear the lineup-value veto or enter Value Gate.
A source conflict is fail-closed. The ESPN feed is public but undocumented and
has no SLA, so it is isolated behind this adapter and cached rather than treated
as a production dependency. The previous UEFA fallback was removed because its
public match feed was not reliably reachable in the target environment.

After the source audit, the watcher automatically runs
`v4_lineup_shock_research.py`. When API-Football is still empty but ESPN has
exactly 11 published starters for both teams, the research script maps those
players to the API-Football player profiles by normalized name. That result is
labelled `ESPN_RESEARCH`, remains `approved_for_value_gate: false`, and cannot
change the base V4 `WATCH`/`PASS` decision. A missing player, stale audit, live
fixture, incomplete XI, unsafe fixture match, or provider conflict fails closed.

## Safety and timing

- A fixture whose kickoff has passed or whose provider status is no longer
  `NS`/`TBD` is excluded before team-form, odds, or lineup API calls. It remains
  visible in the report as `PASS` with `EXCLUDED_NOT_PREMATCH`.
- Before confirmed lineups, an otherwise valid candidate remains `WATCH`.
- Lineup status is explicit: `NOT_QUERIED`, `NOT_PUBLISHED`, `INCOMPLETE`, or
  `CONFIRMED`. `NOT_PUBLISHED` means the provider returned no lineup rows; it
  does not claim that the coaching staff has not selected its XI.
- A `SHADOW BET` requires HIGH data quality, validated numerical lineup values,
  and verified post-lineup market evidence. Until the cross-league player model
  is validated, real matches are capped at `WATCH` even when a directional edge
  is present.
- If the AH market already moved at least 0.25 toward the signal, the Moderator
  returns `PASS` to prevent chasing the price.
- Missing fixture, form, AH, stale evidence, or API failure causes a downgrade or
  a fail-closed `PASS`.
- No real-betting integration exists.
- Treat an implausible AH direction or a repeated boundary line such as `+1.5`
  as an audit failure. Do not promote that match beyond research output until
  `v4_market_diagnostics.json` has been inspected and the mapping is validated.

The first run stores the opening observed AH. A later run near kickoff can then
measure movement and establish whether the market updated after confirmed XI.
The runner also checks adjacent schedule dates only when a target cannot be
matched on its requested local date and records closest candidates for diagnosis.

## API budget

For 12 matches on one date, the expected maximum is approximately:

- 1 fixtures discovery request;
- 1 date-wide injuries request;
- 24 cached team-form requests;
- 12 AH requests;
- up to 12 lineup requests inside 90 minutes of kickoff.

Total: about 38 requests before the lineup window and up to 50 inside it. Cached
responses keep repeat runs within quota.

## Validation

```powershell
python -m unittest tests.test_v4_multileague_shadow -v
python .\v3_freeze_guard.py
```

## Player-value research

After a prediction run has resolved fixture team IDs, build cached team-relative
player profiles with paginated team-season requests:

```powershell
python .\v4_player_value_builder.py
```

The builder combines weighted minutes, starts, rating and goal contributions,
filters historical statistics through the current `/players/squads` roster,
selects the strongest valid baseline formation from common 3/4/5-defender
shapes, and writes `v4_player_values.json`. It does not make one request per
player. The output is explicitly `RESEARCH_ONLY` and cannot remove
`LINEUP_VALUE_UNVALIDATED` until coverage and forward outcome validation pass.

When confirmed XI data has been captured, calculate the isolated numerical
lineup proxy with no additional API calls:

```powershell
python .\v4_lineup_shock_research.py
```

The research report requires HIGH player profiles and at least 10/11 valued
starters for both teams. It records missing baseline players, XI coverage,
team-strength loss, a goal-margin adjustment proxy and an Adjusted Fair AH
proxy. The scale remains unapproved and cannot influence the live Value Gate
until historical calibration and forward validation pass.
