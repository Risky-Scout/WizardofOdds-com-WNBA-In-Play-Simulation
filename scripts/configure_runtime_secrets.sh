#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/wizard-wnba}"
sudo install -d -m 0750 "${APP_DIR}" "${APP_DIR}/data/models"

read -r -s -p "BALLDONTLIE API key: " BDL_KEY
printf "\n"
read -r -s -p "The Odds API key: " ODDS_KEY
printf "\n"

TMP_FILE="$(mktemp)"
trap 'rm -f "${TMP_FILE}"' EXIT

cat > "${TMP_FILE}" <<EOF
BALLDONTLIE_API_KEY=${BDL_KEY}
THE_ODDS_API_KEY=${ODDS_KEY}
ENVIRONMENT=production
DATA_DIR=/data
PUBLIC_BASE_URL=https://wnba-live.wizardofodds.com
LIVE_ENABLED=true
POLICY_MODE=adaptive
HARD_MIN_CONSERVATIVE_ROI=0.02
BASE_CONSERVATIVE_ROI=0.02
MIN_BOOK_COUNT=2
MAX_TOTAL_UNCERTAINTY=0.08
MIN_CALIBRATION_SCORE=0.75
MAX_GAME_STATE_AGE_SECONDS=4
MAX_MARKET_AGE_SECONDS=12
PUBLICATION_TTL_SECONDS=20
DEFAULT_SIMULATIONS=20000
ODDS_REGIONS=us
ODDS_BOOKMAKERS=
ODDS_MARKETS=player_points,player_rebounds,player_assists,player_threes,player_points_rebounds_assists,h2h,spreads,totals
EOF

sudo install -m 0600 "${TMP_FILE}" "${APP_DIR}/.env"
unset BDL_KEY ODDS_KEY
echo "Runtime secrets installed at ${APP_DIR}/.env with mode 0600."
