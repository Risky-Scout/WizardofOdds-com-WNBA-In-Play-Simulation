# Operations runbook

## Normal state

```text
engine_status=HEALTHY
data_status=LIVE
game state age <= configured maximum
market age <= configured maximum
model bundle valid
zero collector errors
zero engine errors
```

## Fail-closed state

```text
engine_status=DEGRADED
data_status=LIVE_COLLECTION_ACTIVE_PUBLICATION_FAIL_CLOSED
recommendations=[]
```

Collection continues so the incident can be replayed.

## Common incidents

### API key rejected

- confirm the runtime secret exists;
- confirm no whitespace was added;
- confirm the BALLDONTLIE key is sent in `Authorization`;
- check provider tier access;
- rotate the key if exposed;
- never print the value.

### Odds quota low

- inspect quota headers;
- reduce requested markets and bookmakers;
- slow non-live polling;
- preserve game-state polling;
- stop historical backfills;
- alert before exhaustion.

### Provider disagreement

- mark reconciliation failed;
- suppress affected game;
- preserve both raw snapshots;
- inspect score, clock, period, and event sequence;
- resume only after deterministic reconciliation.

### Model bundle rejected

- run the validator;
- inspect missing profiles/calibrators;
- compare bundle version with current roster;
- do not copy the example bundle into production.

### WebSocket disconnected

- the browser automatically reconnects;
- confirm `/api/v1/snapshot` still works;
- confirm proxy WebSocket upgrade headers;
- confirm the API container is healthy.

### Disk pressure

- stop historical backfill first;
- retain recommendation audit history;
- upload/compress older raw snapshots;
- never delete the current incident window.

## Rollback

Deploy the previous immutable GHCR SHA:

```bash
IMAGE_URI=ghcr.io/OWNER/REPOSITORY:PREVIOUS_SHA \
  /opt/wizard-wnba/deploy.sh
```

Model rollback is independent:

```bash
sudo cp approved-previous-production.json \
  /opt/wizard-wnba/data/models/production.json
docker compose -f /opt/wizard-wnba/docker-compose.prod.yml restart worker
```
