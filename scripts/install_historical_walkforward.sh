#!/bin/bash

set +e

REPO="${1:-$PWD}"
SEASONS="${WF_SEASONS:-2024,2025,2026}"
SIMULATIONS="${WF_HISTORICAL_SIMULATIONS:-1000}"
MAX_GAMES="${WF_MAX_GAMES:-0}"
BOOTSTRAP="${WF_BOOTSTRAP_SAMPLES:-5000}"

cd "$REPO" || {
  echo "ERROR: repository not found: $REPO"
  exit 1
}

export PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}"

echo "===== HISTORICAL WALK-FORWARD INSTALL ====="
echo "Repository: $REPO"
echo "Seasons: $SEASONS"
echo "Historical simulations/checkpoint: $SIMULATIONS"
echo "Max games: $MAX_GAMES (0 = all)"
echo

python3 -m compileall -q \
  src/wizard_wnba/historical_walkforward.py \
  scripts/run_historical_walkforward.py

if [ "$?" -ne 0 ]; then
  echo "ERROR: historical pipeline does not compile"
  exit 2
fi

python3 - <<'PY'
from wizard_wnba.historical_walkforward import (
    apply_beta,
    complementary_key,
    chronological_split,
    fit_beta,
)

assert complementary_key(
    {
        "market_key": "spreads",
        "description": "",
        "line": -5.5,
    }
)[2] == "5.5"

rows = [
    {
        "market_key": "totals",
        "raw_probability": 0.55,
        "binary_outcome": index % 2,
        "result": "win" if index % 2 else "loss",
    }
    for index in range(100)
]
parameters = fit_beta(rows)
assert 0.0 < apply_beta(0.55, (parameters.a, parameters.b, parameters.c)) < 1.0

split_rows = [
    {
        "game_date": f"{season}-06-01T00:00:00Z",
        "game_id": f"g-{season}-{index}",
        "season": season,
    }
    for season in (2024, 2025, 2026)
    for index in range(2)
]
train, calibration, oos = chronological_split(split_rows)
assert train and calibration and oos

print("HISTORICAL PIPELINE SELF-TEST PASSED")
PY

if [ "$?" -ne 0 ]; then
  echo "ERROR: historical pipeline self-test failed"
  exit 3
fi

WORKER_ID="$(docker compose ps -q worker)"

if [ -z "$WORKER_ID" ]; then
  docker compose up -d worker
  sleep 5
  WORKER_ID="$(docker compose ps -q worker)"
fi

if [ -z "$WORKER_ID" ]; then
  echo "ERROR: worker container is unavailable"
  exit 4
fi

WIZARD_DIR="$(
  docker compose exec -T worker python - <<'PY'
from pathlib import Path
import wizard_wnba
print(Path(wizard_wnba.__file__).resolve().parent)
PY
)"

echo "Worker: $WORKER_ID"
echo "Wizard package: $WIZARD_DIR"

docker cp \
  src/wizard_wnba/historical_walkforward.py \
  "$WORKER_ID:$WIZARD_DIR/historical_walkforward.py"

docker cp \
  src/wizard_wnba/oos_validation.py \
  "$WORKER_ID:$WIZARD_DIR/oos_validation.py"

docker cp \
  scripts/run_historical_walkforward.py \
  "$WORKER_ID:/tmp/run_historical_walkforward.py"

docker compose exec -T worker python - <<'PY'
from wizard_wnba.historical_walkforward import HistoricalWalkForwardPipeline
print("WORKER HISTORICAL PIPELINE IMPORT: READY")
PY

if [ "$?" -ne 0 ]; then
  echo "ERROR: worker could not import the historical pipeline"
  exit 5
fi

LOG_DIR="$REPO/build/historical-walkforward"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-$(date +%Y%m%d-%H%M%S).log"

echo
echo "===== FULL HISTORICAL BACKFILL + WALK-FORWARD ====="
echo "Log: $LOG_FILE"
echo "The pipeline is resumable. If interrupted, run this installer again."
echo

caffeinate -dimsu \
  docker compose exec -T worker \
  python /tmp/run_historical_walkforward.py \
    --data-dir /data \
    --seasons "$SEASONS" \
    --historical-simulations "$SIMULATIONS" \
    --max-games "$MAX_GAMES" \
    --minimum-rows 1000 \
    --minimum-selected-bets 200 \
    --bootstrap-samples "$BOOTSTRAP" \
  2>&1 | tee "$LOG_FILE"

PIPELINE_STATUS=${PIPESTATUS[0]}

echo
echo "HISTORICAL PIPELINE EXIT STATUS: $PIPELINE_STATUS"

mkdir -p "$LOG_DIR/artifacts"

for remote in \
  /data/calibration/walkforward_report.json \
  /data/calibration/oos_predictions.jsonl \
  /data/models/candidate.walkforward.json \
  /data/models/production.json \
  /data/calibration/PROMOTION_APPROVED
do
  name="$(basename "$remote")"
  docker cp "$WORKER_ID:$remote" "$LOG_DIR/artifacts/$name" \
    >/dev/null 2>&1 || true
done

if [ -f "$LOG_DIR/artifacts/walkforward_report.json" ]; then
  echo
  echo "===== WALK-FORWARD SUMMARY ====="
  python3 - <<PY
import json
from pathlib import Path

path = Path("$LOG_DIR/artifacts/walkforward_report.json")
report = json.loads(path.read_text())
metrics = report["oos_metrics"]

for key in (
    "sample_size",
    "selected_bets",
    "game_count",
    "calibration_slope",
    "calibration_intercept",
    "oos_brier",
    "consensus_brier",
    "brier_gap_to_consensus",
    "after_vig_roi",
    "bootstrap_roi_lower_95",
    "stale_residual_bias",
    "availability_residual_bias",
    "passed",
    "blockers",
):
    print(f"{key}: {metrics.get(key)}")
PY
fi

if [ "$PIPELINE_STATUS" -eq 0 ]; then
  echo
  echo "PROMOTION GATE PASSED"
  echo "Validated production.json installed."
  docker compose restart worker
  sleep 12
  curl -fsS http://127.0.0.1:8080/health \
    | python3 -m json.tool
elif [ "$PIPELINE_STATUS" -eq 4 ]; then
  echo
  echo "PROMOTION GATE DID NOT PASS."
  echo "No production model was installed."
  echo "The exact blockers are printed above and saved in:"
  echo "$LOG_DIR/artifacts/walkforward_report.json"
else
  echo
  echo "HISTORICAL PIPELINE FAILED BEFORE EVALUATION."
  echo "Review: $LOG_FILE"
fi

git add \
  src/wizard_wnba/historical_walkforward.py \
  src/wizard_wnba/oos_validation.py \
  scripts/run_historical_walkforward.py \
  scripts/install_historical_walkforward.sh \
  tests/test_historical_walkforward.py \
  docs/HISTORICAL_WALKFORWARD.md

if ! git diff --cached --quiet; then
  git commit \
    -m "Add historical WNBA backfill and walk-forward calibration"
  git push origin main
fi

echo
echo "SOURCE INTEGRATION COMPLETE"
echo "Pipeline status: $PIPELINE_STATUS"
exit "$PIPELINE_STATUS"
