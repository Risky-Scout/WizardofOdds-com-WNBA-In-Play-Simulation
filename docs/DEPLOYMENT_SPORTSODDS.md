# Deploying to the sportsodds sandbox

## Recommendation

Run production on `sportsodds`, with GitHub handling CI/CD.

Do not run the live in-play loop on GitHub-hosted runners. The application
requires persistent second-level polling, durable storage, stable network
identity, predictable restarts, and WebSocket connections.

## Host requirements

Recommended minimum for one concurrent WNBA slate:

- Linux;
- Docker Engine and Docker Compose v2;
- 4 dedicated CPU cores;
- 8 GB RAM;
- 50 GB durable storage initially;
- outbound HTTPS;
- inbound access only through the existing reverse proxy;
- synchronized system clock;
- log rotation and disk alerts.

Increase CPU when using 50,000+ simulations across several simultaneous games.

## Host preparation

```bash
sudo install -d -m 0750 \
  /opt/wizard-wnba \
  /opt/wizard-wnba/data/raw \
  /opt/wizard-wnba/data/normalized \
  /opt/wizard-wnba/data/recommendations \
  /opt/wizard-wnba/data/models
```

Copy:

```text
deploy/sportsodds/docker-compose.prod.yml
deploy/sportsodds/deploy.sh
```

to `/opt/wizard-wnba/`.

## Runtime secrets

Run on the host:

```bash
sudo APP_DIR=/opt/wizard-wnba ./scripts/configure_runtime_secrets.sh
```

Do not send keys through GitHub Actions unless the live collector itself runs
inside an Actions job—which is not recommended.

## Production model

Copy the approved bundle:

```bash
sudo install -m 0640 production.json \
  /opt/wizard-wnba/data/models/production.json
```

Validate it before deployment:

```bash
PYTHONPATH=src python scripts/validate_model_bundle.py \
  /opt/wizard-wnba/data/models/production.json
```

## Reverse proxy

Route a dedicated same-origin path or subdomain to:

```text
http://127.0.0.1:8080
```

The proxy must support WebSocket upgrade for `/ws/live`.

Preferred public options:

```text
https://www.wizardofodds.com/wnba/live/
```

or:

```text
https://wnba-live.wizardofodds.com/
```

A same-origin path is preferable when integrating user identity, analytics, and
the existing WizardofOdds.com navigation.

## GitHub deployment secrets

Configure these in the protected `production` environment:

```text
SPORTSODDS_HOST
SPORTSODDS_USER
SPORTSODDS_SSH_KEY
SPORTSODDS_KNOWN_HOSTS
```

Do not configure provider API keys in GitHub unless there is a specific CI
contract test that requires them.

## Deploy

The container workflow pushes:

```text
ghcr.io/OWNER/REPOSITORY:GIT_SHA
```

The production workflow deploys that immutable SHA.

## Verify

```bash
curl -fsS http://127.0.0.1:8080/health
docker compose -f /opt/wizard-wnba/docker-compose.prod.yml ps
docker compose -f /opt/wizard-wnba/docker-compose.prod.yml logs --tail=200 worker
```

The live dashboard must show:

- current game state;
- current update timestamp;
- provider and engine status;
- no recommendation when a gate fails;
- no demo data in production;
- the exact model and calibrator version.
