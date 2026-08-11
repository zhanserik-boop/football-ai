# Football AI V4 Multi-League Shadow

V4 is an isolated research runner for cross-league European qualifiers. It does
not import, edit, or influence the frozen V3 EPL runtime. It cannot place bets.

## Agents

1. Quant Agent: recent weighted team strength and Fair Asian Handicap.
2. Market Agent: current/opening AH, bookmaker consensus, movement and freshness.
   When a bookmaker supplies an alternative-line ladder, V4 selects that
   bookmaker's balanced main line before building cross-book consensus.
3. Lineup Agent: confirmed XI availability and injury imbalance. The first
   release explicitly marks cross-league player values as unvalidated.
4. Matchup Agent: attack-versus-defence pressure.
5. Underdog Resistance Agent: draw, unbeaten, low-scoring and narrow-loss profile.
6. Draw Pressure Agent: protection against overrating favourites.
7. Context Agent: rest difference and qualification-stage volatility.
8. Data Quality Agent: fail-closed evidence validation.
9. Risk/Moderator Agent: compares independent evidence and issues `SHADOW BET`,
   `WATCH`, or `PASS` without majority voting.

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

## Safety and timing

- Before confirmed lineups, an otherwise valid candidate remains `WATCH`.
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
