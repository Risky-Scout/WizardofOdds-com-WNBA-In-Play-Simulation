#!/bin/bash
set -u

REPO="${1:-$PWD}"
cd "$REPO" || {
  echo "ERROR: repository not found: $REPO"
  exit 1
}

echo "===== APPLY RUNTIME OPTIMIZATION ====="
python3 scripts/apply_runtime_optimization.py || exit 10

echo "===== VALIDATE CODE ====="
python3 -m compileall -q src || exit 11

if python3 -m ruff --version >/dev/null 2>&1; then
  python3 -m ruff check src tests || exit 12
fi

PYTHONPATH=src python3 -m pytest -q \
  tests/test_runtime_optimization.py \
  tests/test_oos_validation.py \
  tests/test_live_reference_integration.py || exit 13

docker run --rm \
  -v "$PWD:/app:ro" \
  -w /app \
  node:24-alpine \
  node --check src/wizard_wnba/static/app.js || exit 14

echo "===== BUILD DURABLE LOCAL IMAGE ====="
docker build \
  -t wizardofodds/wnba-inplay:local \
  . || exit 20

docker compose up -d --force-recreate || exit 21

API_ID="$(docker compose ps -q api)"
if [ -z "$API_ID" ]; then
  echo "ERROR: API container is unavailable"
  exit 22
fi

READY=0
for attempt in $(seq 1 40); do
  if curl -fsS --max-time 2 \
    http://127.0.0.1:8080/health \
    >/tmp/wizard-optimized-health.json
  then
    READY=1
    break
  fi
  sleep 1
done

if [ "$READY" -ne 1 ]; then
  echo "ERROR: application did not become healthy"
  docker compose logs --tail=100 api worker
  exit 22
fi

echo "===== VERIFY OPTIMIZED ENGINE ====="
docker compose exec -T api python - <<'PY'
from wizard_wnba.optimization import (
    consensus_for_market,
    deterministic_seed,
    should_escalate_simulations,
)
from wnba_inplay.simulation import InPlaySimulator

print("OPTIMIZATION MODULE: READY")
print("ENGINE:", f"{InPlaySimulator.__module__}.{InPlaySimulator.__name__}")
print("DETERMINISTIC SEED:", deterministic_seed(
    base_seed=20260714,
    state_fingerprint="verification",
))
print("ADAPTIVE MONTE CARLO:", should_escalate_simulations(
    requested_simulations=20_000,
    monte_carlo_error=0.004,
    expected_roi=0.01,
))
PY

echo "===== OOS PROMOTION GATE ====="
DATASET="/data/calibration/oos_predictions.jsonl"

docker cp \
  scripts/evaluate_oos_gate.py \
  "$API_ID:/tmp/evaluate_oos_gate.py" \
  >/dev/null

if docker compose exec -T api test -f "$DATASET"; then
  docker compose exec -T api python \
    /tmp/evaluate_oos_gate.py \
    "$DATASET" \
    --report /data/calibration/oos_gate.json \
    --promotion-marker /data/calibration/PROMOTION_APPROVED
  GATE_STATUS=$?
else
  GATE_STATUS=3
  echo "OOS dataset not present yet: $DATASET"
  echo "Automated publication remains fail-closed."
fi

echo
docker compose ps
echo
python3 -m json.tool </tmp/wizard-optimized-health.json

echo
echo "RUNTIME OPTIMIZATION COMPLETE"
echo "OOS GATE EXIT STATUS: $GATE_STATUS"

exit 0
