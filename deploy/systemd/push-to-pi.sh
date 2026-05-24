#!/usr/bin/env bash
set -euo pipefail

PI_HOST="${PI_HOST:-100.104.99.55}"
PI_USER="${PI_USER:-sunrya888}"
REMOTE_STAGE_DIR="${REMOTE_STAGE_DIR:-/home/${PI_USER}/huawei100ktl_exporter}"
APP_DIR="${APP_DIR:-/opt/huawei100ktl_exporter}"
SERVICE_NAME="${SERVICE_NAME:-huawei-exporter}"
SSH_OPTS=(
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=10
)
RSYNC_EXCLUDES=(
  --exclude '.git'
  --exclude '.venv'
  --exclude '__pycache__'
  --exclude '*.pyc'
  --exclude '.env'
)
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

require_tool() {
  local tool="$1"
  if ! command -v "${tool}" >/dev/null 2>&1; then
    echo "Missing required tool: ${tool}" >&2
    exit 1
  fi
}

print_config() {
  cat <<EOF
Deploying exporter to Pi with:
  PI_USER=${PI_USER}
  PI_HOST=${PI_HOST}
  REMOTE_STAGE_DIR=${REMOTE_STAGE_DIR}
  APP_DIR=${APP_DIR}
  SERVICE_NAME=${SERVICE_NAME}
EOF
}

sync_repo() {
  echo "==> Syncing repo to ${PI_USER}@${PI_HOST}:${REMOTE_STAGE_DIR}"
  ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}" "mkdir -p '${REMOTE_STAGE_DIR}'"
  rsync -az --delete "${RSYNC_EXCLUDES[@]}" \
    "${SOURCE_DIR}/" "${PI_USER}@${PI_HOST}:${REMOTE_STAGE_DIR}/"
}

install_and_restart() {
  echo "==> Installing app tree and restarting ${SERVICE_NAME}.service on Pi"
  ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}" "\
    cd '${REMOTE_STAGE_DIR}' && \
    sudo APP_DIR='${APP_DIR}' bash deploy/systemd/install-systemd.sh && \
    sudo systemctl restart '${SERVICE_NAME}.service'"
}

verify_remote() {
  echo "==> Verifying remote service"
  ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}" "\
    sudo systemctl status --no-pager --lines=20 '${SERVICE_NAME}.service' && \
    echo '---' && \
    '${APP_DIR}/deploy/systemd/verify-local.sh'"
}

main() {
  require_tool ssh
  require_tool rsync
  print_config
  sync_repo
  install_and_restart
  verify_remote
}

main "$@"
