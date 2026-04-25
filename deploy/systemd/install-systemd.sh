#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-huawei-exporter}"
SERVICE_USER="${SERVICE_USER:-huawei-exporter}"
APP_DIR="${APP_DIR:-/opt/huawei100ktl_exporter}"
ENV_FILE="${ENV_FILE:-/etc/huawei-exporter.env}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
UNIT_PATH="${SYSTEMD_DIR}/${SERVICE_NAME}.service"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must be run as root." >&2
    exit 1
  fi
}

ensure_user() {
  if id "${SERVICE_USER}" >/dev/null 2>&1; then
    echo "Service user ${SERVICE_USER} already exists."
    return
  fi

  useradd --system --home "${APP_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
  echo "Created service user ${SERVICE_USER}."
}

install_app_tree() {
  mkdir -p "${APP_DIR}"
  rsync -a \
    --delete \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.env' \
    "${SOURCE_DIR}/" "${APP_DIR}/"
  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}"
  echo "Installed application tree to ${APP_DIR}."
}

setup_virtualenv() {
  python3 -m venv "${APP_DIR}/.venv"
  "${APP_DIR}/.venv/bin/pip" install --upgrade pip
  "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}/.venv"
  echo "Virtual environment created at ${APP_DIR}/.venv."
}

install_env_file() {
  if [[ -f "${ENV_FILE}" ]]; then
    echo "Environment file ${ENV_FILE} already exists; leaving it in place."
    return
  fi

  install -m 600 "${APP_DIR}/deploy/systemd/huawei-exporter.env.example" "${ENV_FILE}"
  echo "Created ${ENV_FILE} from example. Edit it before relying on production data flow."
}

install_unit() {
  mkdir -p "${SYSTEMD_DIR}"
  sed \
    -e "s|/opt/huawei100ktl_exporter|${APP_DIR}|g" \
    -e "s|/etc/huawei-exporter.env|${ENV_FILE}|g" \
    -e "s|User=huawei-exporter|User=${SERVICE_USER}|g" \
    -e "s|Group=huawei-exporter|Group=${SERVICE_USER}|g" \
    "${APP_DIR}/deploy/systemd/huawei-exporter.service" > "${UNIT_PATH}"
  chmod 644 "${UNIT_PATH}"
  echo "Installed systemd unit to ${UNIT_PATH}."
}

enable_service() {
  systemctl daemon-reload
  systemctl enable --now "${SERVICE_NAME}.service"
  echo "Enabled and started ${SERVICE_NAME}.service."
}

print_next_steps() {
  cat <<EOF

Install complete.

Next steps:
1. Edit ${ENV_FILE} with the real inverter and InfluxDB values.
2. Restart the service:
   sudo systemctl restart ${SERVICE_NAME}.service
3. Check status:
   sudo systemctl status ${SERVICE_NAME}.service
4. Verify locally:
   ${APP_DIR}/deploy/systemd/verify-local.sh
EOF
}

main() {
  require_root
  ensure_user
  install_app_tree
  setup_virtualenv
  install_env_file
  install_unit
  enable_service
  print_next_steps
}

main "$@"
