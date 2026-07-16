# Rollout — WNBA In-Play Simulator

One page: what ships, and the exact commands to make the public URL live the
moment root access to the sandbox exists.

## What ships

- **Branch:** `model-optimization-v3` (a strict superset of `fix/runtime-integrity`).
- **Pricing is unchanged.** No calibrator was modified: the model-optimization
  work added the per-market OOS gate, a hierarchical calibrator, and honest
  per-market validation labels, but **no model improvement passed its gate**, so
  no new calibrators were promoted. `metadata.calibration_parameters` is
  byte-identical to the deployed bundle. The deployable model is exactly the
  `fix/runtime-integrity` model plus honest transparency.
- **Re-validated production bundle:** `production.revalidated.json` — the current
  bundle with an added `validation_report.per_market_gate`. Only **h2h** is
  marked `passed: true` (166 selected bets, calibration slope 0.864, bootstrap
  L95 +0.239 on real OOS data). All other markets are labeled not-validated.
  Generate/refresh it with:
  ```bash
  python scripts/revalidate_bundle.py \
    <production.json> <oos_predictions.jsonl> production.revalidated.json
  ```

## Status of gates (rollout blockers)

| Check | State |
|---|---|
| Full test suite (100 wizard incl. Playwright + 49 reference = 149) | PASS |
| Acceptance driver | 20/20 |
| Container image builds from `model-optimization-v3` | see `gh run` below |
| GitHub deploy secrets (`SPORTSODDS_HOST/USER/SSH_KEY/KNOWN_HOSTS`) | SET |
| Host has Docker + `woo` can run it / sudo | **MISSING — root required** |
| nginx route for the In-Play subpath | **MISSING — root required** |

The only blockers are host-side and require **root on `45.79.0.107`
(sportsodds.wizardofodds.com)**, which the deploy user `woo` does not have
(no passwordless sudo, Docker not installed).

## Step 1 — build the image from the final branch (no root needed)

```bash
gh workflow run "Build production image" --ref model-optimization-v3
# image -> ghcr.io/risky-scout/wizardofodds-com-wnba-in-play-simulation:<SHA of model-optimization-v3 HEAD>
```

## Step 2 — one-time host provisioning (ROOT on 45.79.0.107)

```bash
# Docker (host prerequisite, currently absent) + let woo run it
curl -fsSL https://get.docker.com | sh
usermod -aG docker woo

# App dir owned by woo, and let the workflow's `sudo install` run non-interactively
install -d -o woo -g woo -m 0750 /opt/wizard-wnba/data/models
printf 'woo ALL=(root) NOPASSWD: /usr/bin/install\n' > /etc/sudoers.d/wizard-wnba
chmod 0440 /etc/sudoers.d/wizard-wnba

# Runtime env (supply the 2 API keys) + the re-validated model (staged in ~woo)
cd /path/to/repo && APP_DIR=/opt/wizard-wnba ./scripts/configure_runtime_secrets.sh
printf 'ROOT_PATH=/tools/odds-scanner/predictions/WNBA/In-Play/Simulation\n' >> /opt/wizard-wnba/.env
install -m 0640 -o woo -g woo \
  /var/www/sportsodds/wizard-wnba-staging/production.json \
  /opt/wizard-wnba/data/models/production.json
# (use production.revalidated.json here to ship h2h's validated badge)

# nginx: paste the two location blocks into the sportsodds server{} block
$EDITOR /etc/nginx/sites-available/sportsodds   # see deploy/sportsodds/nginx-in-play.conf.example
nginx -t && systemctl reload nginx
```

## Step 3 — deploy (no root; secrets are already set)

```bash
gh workflow enable "Deploy sportsodds"
gh workflow run "Deploy sportsodds" --ref model-optimization-v3 \
  -f image_uri=ghcr.io/risky-scout/wizardofodds-com-wnba-in-play-simulation:<SHA>
```

## Step 4 — verify the live URL

```bash
curl -fsS  https://sportsodds.wizardofodds.com/tools/odds-scanner/predictions/WNBA/In-Play/Simulation/ | grep "Scenario Lab"
curl -o /dev/null -w '%{http_code}\n' https://sportsodds.wizardofodds.com/tools/odds-scanner/predictions/WNBA/In-Play/Simulation/static/app.js
curl -sS https://sportsodds.wizardofodds.com/tools/odds-scanner/predictions/WNBA/In-Play/Simulation/api/v1/live-markets | head -c 200
```

Expected: the Scenario Lab page loads; `/static/app.js` returns 200; the
live-markets JSON returns with each market carrying `oos_validated` (h2h true).
The Scenario Lab result shows the per-market badge — "OOS validated" for h2h,
"Pricing live — validation accumulating" for the others.
