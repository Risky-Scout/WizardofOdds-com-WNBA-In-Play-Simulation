# Architecture

## Product boundary

WizardofOdds.com is an analytics publisher, not the sportsbook accepting the
wager. The engine therefore does not:

- generate sportsbook prices;
- shade probabilities for inventory;
- accept stakes;
- use account classifications;
- optimize operator liabilities.

It estimates fair probabilities and evaluates exact sportsbook offers.

## Runtime processes

### API process

- serves the dashboard;
- serves JSON endpoints;
- broadcasts publication snapshots over WebSocket;
- reads atomic current-cycle output from shared durable storage.

### Worker process

- discovers WNBA events;
- polls live state and prices;
- archives every raw response;
- reconciles game and player identity;
- loads the signed/versioned model bundle;
- simulates each active game;
- applies required market calibrators;
- constructs leave-one-book-out consensus;
- evaluates every line/price independently;
- publishes or suppresses recommendations.

## Anti-stale design

The live worker rebuilds the current recommendation snapshot from every
collection cycle.

It does not merge live output with:

- demo data;
- prior-run recommendations;
- old market rows;
- a previous model version.

If the current cycle cannot be priced safely, the dashboard displays no current
recommendation and reports a degraded/fail-closed state.

## Core data flow

```text
Provider response
  → append-only raw snapshot
  → normalized game/player/market records
  → identity reconciliation
  → authoritative game state
  → model bundle and remaining-minute update
  → correlated Monte Carlo paths
  → discrete PMFs
  → market-specific calibration
  → leave-one-book-out no-vig consensus
  → exact-offer ROI
  → conservative ROI
  → adaptive publication policy
  → atomic public snapshot
```

## Recommendation state

```text
WATCH
QUALIFIED
PUBLISHED
PRICE_CHANGED
WITHDRAWN
SUSPENDED
SETTLED
```

The initial implementation publishes a new immutable recommendation ID whenever
the event sequence, market update, model version, or calibrator changes.

## Horizontal scaling

The included shared-file publication store is optimal for a single `sportsodds`
host and keeps the initial system operationally simple.

Before running multiple worker hosts:

- move current state to Redis or another shared low-latency store;
- move audit records and normalized facts to PostgreSQL;
- use object storage for raw snapshots;
- elect one publisher per game;
- preserve idempotency keys across workers.
