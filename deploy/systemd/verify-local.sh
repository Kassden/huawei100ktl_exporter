#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"

check_endpoint() {
  local endpoint="$1"
  echo "Checking ${BASE_URL}${endpoint}"
  curl --fail --silent --show-error "${BASE_URL}${endpoint}" | python3 -m json.tool
  echo
}

check_endpoint "/live"
check_endpoint "/ready"
check_endpoint "/health"
check_endpoint "/collector/status"
