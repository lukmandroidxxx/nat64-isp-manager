#!/usr/bin/env bash
# Run the Linux NAT64 network-namespace integration suite.
#
# The suite is intentionally opt-in: it needs root, Linux, Jool 4, iproute2,
# iputils-ping and the Jool kernel module. It creates and removes three network
# namespaces owned by scripts/netns_lab.sh and does not touch the host's Jool,
# firewall or routing configuration.
#
# Usage (from the repository root):
#   sudo ./scripts/run-integration-tests.sh
#   sudo ./scripts/run-integration-tests.sh --load
#   sudo ./scripts/run-integration-tests.sh -k pool4 -v
#
# Options:
#   --load   also run the 500-client / 1000-session application load test
#            (writes tests/results/load-500x1000.json)
#   --help   show this message
#   --       pass all remaining arguments to pytest
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
RESULTS_DIR="${ROOT_DIR}/tests/results"
RUN_LOAD=false

usage() {
  sed -n '3,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

PYTEST_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --load) RUN_LOAD=true; shift ;;
    --help|-h) usage; exit 0 ;;
    --) shift; PYTEST_ARGS+=("$@"); break ;;
    *) PYTEST_ARGS+=("$1"); shift ;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This suite requires Linux; it is skipped on other platforms." >&2
  exit 1
fi
if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root (network namespaces and Jool require privileges)." >&2
  exit 1
fi

missing=()
for tool in ip jool ping sysctl; do
  command -v "${tool}" >/dev/null 2>&1 || missing+=("${tool}")
done
if ((${#missing[@]})); then
  echo "Missing required tools: ${missing[*]}" >&2
  echo "Install iproute2, iputils-ping and Jool 4 (jool-tools/jool-dkms)." >&2
  exit 1
fi

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="${PYTHON}"
elif [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "python3 is required (or set PYTHON=/path/to/python)." >&2
  exit 1
fi

if ! "${PYTHON_BIN}" -c 'import pytest' >/dev/null 2>&1; then
  echo "pytest is not installed for ${PYTHON_BIN}." >&2
  echo "Install backend/requirements-dev.txt into the project virtualenv first." >&2
  exit 1
fi

mkdir -p "${RESULTS_DIR}"
export NAT64_LAB_LOAD=0
if [[ "${RUN_LOAD}" == "true" ]]; then
  export NAT64_LAB_LOAD=1
fi

echo "==> Python: ${PYTHON_BIN}"
echo "==> Backend: ${BACKEND_DIR}"
echo "==> Results: ${RESULTS_DIR}"
if [[ "${RUN_LOAD}" == "true" ]]; then
  echo "==> Load test enabled (500 clients / 1000 sessions)"
fi

cd "${BACKEND_DIR}"
exec "${PYTHON_BIN}" -m pytest tests/integration -m integration -v "${PYTEST_ARGS[@]}"
