#!/bin/bash
set +e

REPO="${1:-$PWD}"
cd "$REPO" || exit 2

STAMP="$(date +%Y%m%d-%H%M%S)"
HOST_ARCHIVE="$REPO/build/historical-walkforward/POSSESSION-REPAIR-V2-$STAMP"
mkdir -p "$HOST_ARCHIVE"

WORKER_ID="$(docker compose ps -q worker)"
if [ -z "$WORKER_ID" ]; then
  echo "ERROR: worker container is not running"
  exit 3
fi

echo "===== PRESERVING CURRENT DATASETS ====="
for source in \
  /data/calibration/oos_predictions.jsonl \
  /data/calibration/oos_predictions.repaired.jsonl \
  /data/calibration/walkforward_report.json \
  /data/calibration/walkforward_repair_report.json \
  /data/historical_walkforward/output/raw_replay_rows.jsonl \
  /data/historical_walkforward/output/walkforward_validation_predictions.jsonl \
  /data/models/candidate.walkforward.json
do
  name="$(basename "$source")"
  if docker compose exec -T worker test -f "$source"; then
    docker cp "$WORKER_ID:$source" "$HOST_ARCHIVE/$name"
  fi
done

tar -czf "$HOST_ARCHIVE.tar.gz" -C "$(dirname "$HOST_ARCHIVE")" "$(basename "$HOST_ARCHIVE")"
shasum -a 256 "$HOST_ARCHIVE.tar.gz" > "$HOST_ARCHIVE.tar.gz.sha256"

echo "$HOST_ARCHIVE" > build/historical-walkforward/LAST_POSSESSION_REPAIR_ARCHIVE.txt

echo "HOST ARCHIVE: $HOST_ARCHIVE"
echo "COMPRESSED ARCHIVE: $HOST_ARCHIVE.tar.gz"
cat "$HOST_ARCHIVE.tar.gz.sha256"

echo
echo "===== INSTALLING POSSESSION REPAIR V2 ====="
docker cp \
  scripts/repair_possession_walkforward_v2.py \
  "$WORKER_ID:/tmp/repair_possession_walkforward_v2.py"

docker compose exec -T worker python -m py_compile \
  /tmp/repair_possession_walkforward_v2.py

docker compose exec -T worker python \
  /tmp/repair_possession_walkforward_v2.py \
  --data-dir /data \
  --bootstrap-samples 5000

STATUS=$?

echo
echo "POSSESSION REPAIR EXIT STATUS: $STATUS"

for source in \
  /data/calibration/oos_predictions.possession_v2.jsonl \
  /data/calibration/walkforward_possession_v2_report.json \
  /data/models/candidate.walkforward.possession_v2.json \
  /data/models/production.json \
  /data/calibration/PROMOTION_APPROVED
do
  name="$(basename "$source")"
  if docker compose exec -T worker test -f "$source"; then
    docker cp "$WORKER_ID:$source" "$HOST_ARCHIVE/$name"
  else
    echo "NOT CREATED: $source"
  fi
done

docker compose exec -T worker python - <<'PY'
from pathlib import Path
import json

report_path = Path("/data/calibration/walkforward_possession_v2_report.json")
approval = Path("/data/calibration/PROMOTION_APPROVED")
production = Path("/data/models/production.json")

print()
print("===== FINAL POSSESSION V2 DECISION =====")
print("PROMOTION_APPROVED:", approval.exists())
print("PRODUCTION_MODEL:", production.exists())

if report_path.exists():
    report = json.loads(report_path.read_text())
    metrics = report.get("oos_metrics", {})
    for key in (
        "sample_size",
        "selected_bets",
        "game_count",
        "calibration_slope",
        "calibration_intercept",
        "oos_brier",
        "consensus_rows",
        "consensus_games",
        "model_brier_on_consensus_rows",
        "consensus_brier",
        "brier_gap_to_consensus",
        "after_vig_roi",
        "bootstrap_roi_lower_95",
        "selected_stale_count",
        "selected_unavailable_count",
        "selected_roster_proxy_count",
        "selected_alignment_violation_count",
        "passed",
        "blockers",
    ):
        print(f"{key}: {metrics.get(key)}")
PY

echo
echo "ARCHIVE LOCATION: $HOST_ARCHIVE"
echo "ARCHIVE CHECKSUM:"
cat "$HOST_ARCHIVE.tar.gz.sha256"

exit "$STATUS"
