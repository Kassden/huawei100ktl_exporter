#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/huawei-exporter.env}"
APP_DIR="${APP_DIR:-/opt/huawei100ktl_exporter}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing env file: ${ENV_FILE}" >&2
  exit 1
fi

set -a
. "${ENV_FILE}"
set +a

check_command() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "Missing required command: ${cmd}" >&2
    exit 1
  fi
}

print_section() {
  local title="$1"
  echo
  echo "== ${title} =="
}

check_tcp() {
  local host="$1"
  local port="$2"
  python3 - "$host" "$port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

socket.getaddrinfo(host, port)

with socket.create_connection((host, port), timeout=5):
    pass

print(f"TCP connect OK: {host}:{port}")
PY
}

check_http_json() {
  local endpoint="$1"
  echo "GET ${BASE_URL}${endpoint}"
  curl --fail --silent --show-error "${BASE_URL}${endpoint}" | python3 -m json.tool
}

check_command python3
check_command curl

print_section "Local Runtime"
python3 --version

if [[ -x "${APP_DIR}/.venv/bin/python" ]]; then
  echo "Virtualenv OK: ${APP_DIR}/.venv/bin/python"
else
  echo "Missing virtualenv python: ${APP_DIR}/.venv/bin/python" >&2
  exit 1
fi

print_section "Configured Targets"
echo "Modbus target: ${SUN2000_MODBUS_HOST}:${SUN2000_MODBUS_PORT}"
echo "Influx URL: ${INFLUXDB_URL}"
echo "Device ID: ${DEVICE_ID}"
echo "Site ID: ${SITE_ID}"

print_section "Network Reachability"
check_tcp "${SUN2000_MODBUS_HOST}" "${SUN2000_MODBUS_PORT}"

INFLUX_HOST="$(python3 - <<'PY'
from urllib.parse import urlparse
import os
parsed = urlparse(os.environ["INFLUXDB_URL"])
print(parsed.hostname or "")
PY
)"
INFLUX_PORT="$(python3 - <<'PY'
from urllib.parse import urlparse
import os
parsed = urlparse(os.environ["INFLUXDB_URL"])
if parsed.port:
    print(parsed.port)
elif parsed.scheme == "https":
    print(443)
else:
    print(80)
PY
)"
check_tcp "${INFLUX_HOST}" "${INFLUX_PORT}"

print_section "systemd Service"
if command -v systemctl >/dev/null 2>&1; then
  systemctl is-enabled huawei-exporter.service || true
  systemctl is-active huawei-exporter.service || true
  systemctl status --no-pager huawei-exporter.service || true
else
  echo "systemctl not available on this machine."
fi

print_section "Local API"
check_http_json "/live"
check_http_json "/ready"
check_http_json "/health"
check_http_json "/collector/status"

