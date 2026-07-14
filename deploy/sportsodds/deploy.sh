#!/usr/bin/env bash
set -euo pipefail

: "${IMAGE_URI:?IMAGE_URI must be set}"
APP_DIR="${APP_DIR:-/opt/wizard-wnba}"
COMPOSE_FILE="${APP_DIR}/docker-compose.prod.yml"

mkdir -p "${APP_DIR}/data/raw" \
         "${APP_DIR}/data/normalized" \
         "${APP_DIR}/data/recommendations" \
         "${APP_DIR}/data/models"

if [[ ! -f "${APP_DIR}/.env" ]]; then
  echo "Missing ${APP_DIR}/.env. Run configure_runtime_secrets.sh on sportsodds." >&2
  exit 1
fi

export IMAGE_URI
docker compose -f "${COMPOSE_FILE}" pull
docker compose -f "${COMPOSE_FILE}" up -d --remove-orphans
docker compose -f "${COMPOSE_FILE}" ps
curl --fail --silent --show-error http://127.0.0.1:8080/health
