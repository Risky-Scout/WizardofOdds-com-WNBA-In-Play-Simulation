# Data and historical backfill

## Live collection

BALLDONTLIE provides the primary game-state and player-stat feed.

The Odds API provides the primary multi-book offer feed. The collector monitors
quota headers:

```text
x-requests-remaining
x-requests-used
x-requests-last
```

All responses are saved before normalization.

## Historical limitations

The Odds API historical additional markets, including player props, are
point-in-time snapshots rather than every individual price movement. Use them
for model and recommendation research with the correct timestamp resolution.

Do not treat a five-minute historical snapshot as proof that a price was
continuously executable throughout that interval.

## Event backfill

```bash
export THE_ODDS_API_KEY=...
wizard-wnba-backfill \
  --event-id EVENT_ID \
  --start 2025-07-01T22:00:00Z \
  --end 2025-07-02T03:00:00Z \
  --interval-minutes 5 \
  --markets player_points,player_rebounds,player_assists,player_threes
```

Each response is stored under:

```text
/data/raw/provider=the_odds_api/
```

## Cost controls

Historical event odds have materially higher quota costs than live calls.
Before a season-scale backfill:

1. Estimate snapshots per event.
2. Multiply by events, regions, and requested markets.
3. Test one event.
4. Inspect `x-requests-last`.
5. Set a hard budget and stop threshold.
6. Avoid requesting markets that are not used by the model.

## Training join rules

Join historical game states and market snapshots using:

- canonical event identity;
- exact UTC timestamp;
- the closest market snapshot at or before prediction time;
- a declared maximum age;
- no post-prediction corrections.

Never join against:

- final box score;
- future substitution;
- later injury confirmation;
- later sportsbook price;
- a corrected play not available at prediction time.

## Prospective archive

Even with historical data, continuously archive all live responses. The
prospective archive provides the most defensible evaluation of:

- feed latency;
- publication delay;
- offer availability;
- withdrawals;
- execution-window assumptions;
- schema changes.
