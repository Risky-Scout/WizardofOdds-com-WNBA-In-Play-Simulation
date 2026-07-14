# WizardofOdds.com WNBA In-Play Simulation

A production-oriented WNBA in-play decision engine for **WizardofOdds.com**.

It ingests live game state and sportsbook offers, simulates correlated player and
game outcomes, applies market-specific calibration, and automatically publishes
only opportunities that pass data-integrity, freshness, uncertainty, calibration,
and conservative-ROI gates.

The repository title is **WizardofOdds.com WNBA In-Play Simulation**. The recommended
GitHub slug is:

```text
WizardofOdds-com-WNBA-In-Play-Simulation
```

GitHub repository names cannot contain spaces.

## Correct deployment architecture

**GitHub is the control plane, not the live runtime.**

Use GitHub for:

- version control;
- pull requests and review;
- automated tests;
- container image builds;
- immutable release history;
- controlled deployment to production.

Run the live collector, simulator, recommendation engine, and API as persistent
containers on the `sportsodds` WizardofOdds.com sandbox. GitHub Actions scheduled
workflows are not appropriate for second-level in-play polling.

```text
GitHub repository
      │
      ├── CI and container build
      │
      └── protected deployment
                 │
                 ▼
sportsodds sandbox
  ├── live worker
  │     ├── BALLDONTLIE
  │     ├── The Odds API
  │     ├── optional Sportsdataverse/ESPN reconciliation
  │     ├── simulation and calibration
  │     └── adaptive recommendation policy
  └── FastAPI + WebSocket dashboard
                 │
                 ▼
wizardofodds.com reverse proxy
```

## Recommendation policy

The configured strategy is **D: adaptive**, with a hard minimum conservative ROI
of **2%**.

A recommendation is published only when:

```text
conservative ROI >= adaptive required ROI >= 2%
```

The adaptive requirement increases for:

- higher model uncertainty;
- older game state;
- older sportsbook price;
- fewer independent books;
- weaker calibration;
- late-game states.

Expected ROI is:

```text
p_win × (decimal_odds - 1) - p_loss
```

Push probability is returned to the bettor and is not counted as a win or loss.

The conservative calculation deducts a one-sided uncertainty allowance from
`p_win` and adds it to `p_loss`.

## Data sources

### BALLDONTLIE

Primary live game-state source:

- games;
- play-by-play;
- real-time player stats;
- injuries;
- odds and player props when enabled.

Authentication uses the `Authorization` request header.

### The Odds API

Primary price-comparison and historical-market source:

- current WNBA game lines and player props;
- event-level sportsbook offers;
- historical featured markets;
- historical player props and other additional markets.

Historical event snapshots are archived through:

```bash
wizard-wnba-backfill \
  --event-id EVENT_ID \
  --start 2025-06-01T23:00:00Z \
  --end 2025-06-02T03:00:00Z \
  --interval-minutes 5 \
  --markets player_points,player_rebounds,player_assists
```

### Sportsdataverse 0.0.70

Optional secondary adapter for ESPN play personnel, game situation, rosters,
and historical dataset loaders. It is intentionally isolated from the core
runtime and installed only with:

```bash
python -m pip install -e ".[sportsdataverse]"
```

## Quick start in demo mode

```bash
cp .env.example .env
docker compose up --build
```

Open:

```text
http://localhost:8080
```

Demo mode produces synthetic recommendations to verify the complete API,
WebSocket, dashboard, simulation, calibration, and publication path. Demo
outputs must never be represented as historical or live performance.

## Test locally

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
python -m compileall -q src
```

## Production setup on sportsodds

1. Create the GitHub repository and push this code.
2. Configure the protected GitHub `production` environment.
3. Copy `deploy/sportsodds/docker-compose.prod.yml` and `deploy.sh` to
   `/opt/wizard-wnba/`.
4. Run `scripts/configure_runtime_secrets.sh` directly on sportsodds.
5. Mount a validated model bundle at:

```text
/opt/wizard-wnba/data/models/production.json
```

6. Configure the existing WizardofOdds.com reverse proxy to route HTTPS and
   WebSocket traffic to `127.0.0.1:8080`.
7. Deploy an immutable GHCR image.
8. Confirm `/health`, the dashboard, live feed freshness, and publication gates.

See [the sportsodds deployment guide](docs/DEPLOYMENT_SPORTSODDS.md).

## API key handling

Do **not** paste provider keys into source files, issues, pull requests, build
logs, or chat transcripts.

The live keys are runtime secrets on sportsodds. Configure them interactively:

```bash
sudo APP_DIR=/opt/wizard-wnba ./scripts/configure_runtime_secrets.sh
```

The resulting `/opt/wizard-wnba/.env` has mode `0600`.

GitHub does not need the BALLDONTLIE or The Odds API keys to run tests or build
the image.

## Production model requirement

The included simulator and demo profiles are a working reference implementation,
not a claim of a trained profitable production model.

Live recommendation publication remains fail-closed until
`data/models/production.json` contains:

- profiles for every active player;
- target/remaining-minutes information;
- fitted market-specific beta calibrators;
- model and calibrator version identifiers;
- out-of-sample validation metrics;
- a calibration score above the production gate.

Use `config/model_bundle.example.json` only as a schema example.

## Public terminology

The product reports:

- model probability;
- fair odds;
- no-vig consensus probability;
- expected ROI;
- conservative ROI;
- required ROI;
- uncertainty.

It does not label a current model edge as CLV. True closing-line value can be
reported only after a later market observation exists.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [sportsodds deployment](docs/DEPLOYMENT_SPORTSODDS.md)
- [Data and historical backfill](docs/DATA_AND_BACKFILL.md)
- [Model governance](docs/MODEL_GOVERNANCE.md)
- [Operations runbook](docs/RUNBOOK.md)
- [Security](docs/SECURITY.md)
- [User experience](docs/USER_EXPERIENCE.md)
