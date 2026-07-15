# Historical WNBA backfill and walk-forward calibration

This pipeline performs a real chronological validation of the WNBA in-play
simulator. It does not create a production model unless every configured
out-of-sample promotion gate passes.

## Data sources

The pipeline downloads and caches:

- BALLDONTLIE completed WNBA games;
- BALLDONTLIE play-by-play;
- BALLDONTLIE final player box scores;
- The Odds API historical event IDs;
- The Odds API historical event-level odds for game markets and player props.

The cache is resumable under:

```text
/data/historical_walkforward/cache
```

## Chronology

With the default seasons:

```text
2024 -> initial model/calibrator training
2025 -> walk-forward validation
2026 -> final untouched out-of-sample evaluation
```

The rolling player profiles used at each historical game are built only from
games dated before that game.

## Historical alignment

BALLDONTLIE play-by-play contains period and game clock but not a wall-clock
timestamp for every play. The pipeline therefore prices fixed game-clock
checkpoints against the nearest five-minute historical Odds API snapshot.
Snapshots whose returned timestamp differs from the request by more than the
configured tolerance are excluded.

This limitation is recorded in `walkforward_report.json`. Prospective exact
live-state validation should continue after historical promotion.

## Promotion gates

The candidate is installed as `/data/models/production.json` only when all
strict gates pass:

- at least 1,000 settled binary rows;
- at least 200 selected bets;
- calibration slope from 0.90 through 1.10;
- absolute calibration intercept no greater than 0.10;
- Brier score no worse than no-vig consensus by more than 0.002;
- positive after-vig ROI;
- positive game-cluster bootstrap lower 95% ROI bound;
- measurable and non-material stale-line residual bias;
- measurable and non-material availability residual bias.

A failed gate leaves automated publication closed and writes the exact blockers
to:

```text
/data/calibration/walkforward_report.json
```

## Run

```bash
bash scripts/install_historical_walkforward.sh \
  "/path/to/WizardofOdds-com-WNBA-In-Play-Simulation"
```

The full run is resumable. Re-running the same command uses cached API responses.

## Outputs

```text
/data/historical_walkforward/output/raw_replay_rows.jsonl
/data/historical_walkforward/output/walkforward_validation_predictions.jsonl
/data/calibration/oos_predictions.jsonl
/data/calibration/walkforward_report.json
/data/models/candidate.walkforward.json
/data/models/production.json                 # only after a pass
/data/calibration/PROMOTION_APPROVED          # only after a pass
```
