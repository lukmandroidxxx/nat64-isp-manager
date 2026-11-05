#!/usr/bin/env bash
# NAT64 ISP Manager - native Linux gateway installer.
#
# Supported out of the box: Debian 13 and Ubuntu 24.04 (Python 3.12+).
# Debian 12 and Ubuntu 22.04 work when Python 3.12+ is installed first and
# selected with PYTHON=/usr/bin/python3.12. Run as root from a checkout:
#   sudo ./deploy/scripts/install.sh
#   sudo ./deploy/scripts/install.sh --start
#
# The installer does NOT install or configure Jool, BIND9, nftables, netplan or
# PostgreSQL itself, and it does not modify unrelated host configuration. It
# installs the application, the root-owned host helper, sudoers policy and
# systemd units. Complete the remaining gateway steps from
# docs/02-INSTALLATION.md and the in-app Installation Wizard.
set -euo pipefail

APP_ROOT="/opt/nat64-manager"
CONFIG_DIR="/etc/nat64-manager"
DATA_DIR="/var/lib/nat64-manager"
BACKUP_DIR="/var/backups/nat64-manager"
APP_USER="nat64mgr"
APP_GROUP="nat64mgr"
PYTHON="${PYTHON:-python3}"
# PYTHON may be a command name (python3) or an absolute path
# (/usr/bin/python3.12). apt needs a package name, while sudoers and the
# environment file need the resolved absolute interpreter path.
PYTHON_PKG="$(basename "${PYTHON}")"
case "${PYTHON_PKG}" in
  python3|python3.[0-9]*) ;;
  *)
    echo "PYTHON must name a Python 3 interpreter (for example python3 or /usr/bin/python3.12)." >&2
    exit 2
    ;;
esac
START_SERVICE=false

usage() {
  cat <<'EOF'
Usage: install.sh [--start] [--help]

  --start   enable and start nat64-manager.service after installation
  --help    show this message

Environment:
  PYTHON    Python interpreter to use (default: python3)
EOF
}

for arg in "$@"; do
  case "$arg" in
    --start) START_SERVICE=true ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; usage; exit 2 ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer targets Linux. Use the Docker Compose stack for UI-only evaluation." >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This installer supports Debian/Ubuntu (apt). Install the files manually on other distributions." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [[ ! -f "${SRC_ROOT}/backend/app/main.py" ]]; then
  echo "Source tree not found at ${SRC_ROOT}." >&2
  exit 1
fi

echo "==> Installing OS packages"
apt-get update
APT_PACKAGES=(
  ca-certificates curl rsync sudo systemd
)
# An absolute PYTHON path means the operator already installed that interpreter
# (for example python3.12 on Debian 12). Installing a matching apt package may
# not be possible from the configured repositories, so do not guess.
if [[ "${PYTHON}" != */* ]]; then
  APT_PACKAGES+=("${PYTHON_PKG}" "${PYTHON_PKG}-venv" "${PYTHON_PKG}-pip")
fi
apt-get install -y --no-install-recommends "${APT_PACKAGES[@]}"

PYTHON_ABS="$(command -v "${PYTHON}" || true)"
if [[ -z "${PYTHON_ABS}" || ! -x "${PYTHON_ABS}" ]]; then
  echo "Python interpreter not found: ${PYTHON}" >&2
  exit 1
fi

if ! "${PYTHON_ABS}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "Python 3.12+ is required; ${PYTHON_ABS} is too old." >&2
  echo "Install python3.12 and re-run with PYTHON=/usr/bin/python3.12." >&2
  exit 1
fi

echo "==> Creating service account and directories"
if ! getent group "${APP_GROUP}" >/dev/null; then
  groupadd --system "${APP_GROUP}"
fi
if ! getent passwd "${APP_USER}" >/dev/null; then
  useradd --system --gid "${APP_GROUP}" --home-dir "${DATA_DIR}" \
    --shell /usr/sbin/nologin "${APP_USER}"
fi
install -d -m 0755 -o root -g root "${APP_ROOT}"
install -d -m 0755 -o root -g root "${APP_ROOT}/frontend"
install -d -m 0750 -o root -g "${APP_GROUP}" "${CONFIG_DIR}"
install -d -m 0750 -o "${APP_USER}" -g "${APP_GROUP}" "${DATA_DIR}"
install -d -m 0750 -o "${APP_USER}" -g "${APP_GROUP}" "${DATA_DIR}/backups"
install -d -m 0750 -o root -g "${APP_GROUP}" "${BACKUP_DIR}"
install -d -m 0755 -o root -g root /run/nat64-manager

echo "==> Copying application"
rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' \
  --exclude '.ruff_cache' --exclude 'backend/data' --exclude '.env' \
  --exclude 'venv' \
  "${SRC_ROOT}/" "${APP_ROOT}/"

if [[ ! -f "${APP_ROOT}/frontend/dist/index.html" ]]; then
  if command -v node >/dev/null 2>&1; then
    NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
    if [[ "${NODE_MAJOR}" -ge 20 ]] && command -v npm >/dev/null 2>&1; then
      echo "==> Building the frontend (Node ${NODE_MAJOR})"
      (cd "${APP_ROOT}/frontend" && npm ci --no-audit --no-fund && npm run build)
    else
      echo "Node 20+ is required to build the SPA. Install it or copy a prebuilt frontend/dist." >&2
      exit 1
    fi
  else
    echo "frontend/dist is missing and Node.js is not installed." >&2
    exit 1
  fi
fi

echo "==> Installing Python dependencies"
"${PYTHON_ABS}" -m venv "${APP_ROOT}/venv"
"${APP_ROOT}/venv/bin/pip" install --upgrade pip
"${APP_ROOT}/venv/bin/pip" install -r "${APP_ROOT}/backend/requirements.txt"

echo "==> Installing the root-owned host helper"
install -m 0755 -o root -g root "${SRC_ROOT}/deploy/hostctl/hostctl.py" "${APP_ROOT}/hostctl.py"
# The service account only needs read/execute access to the application tree.
# Everything under /opt is root-owned and not group/world writable.
chown -R root:root "${APP_ROOT}"
chmod -R go-w "${APP_ROOT}"

echo "==> Installing sudoers policy"
SUDOERS_TMP="$(mktemp)"
sed "s#^Cmnd_Alias NAT64_HOSTCTL = .*#Cmnd_Alias NAT64_HOSTCTL = ${PYTHON_ABS} ${APP_ROOT}/hostctl.py#" \
  "${SRC_ROOT}/deploy/sudoers/nat64-manager" > "${SUDOERS_TMP}"
if ! grep -Fq "Cmnd_Alias NAT64_HOSTCTL = ${PYTHON_ABS} ${APP_ROOT}/hostctl.py" "${SUDOERS_TMP}"; then
  echo "Failed to set the helper command in the sudoers policy." >&2
  rm -f "${SUDOERS_TMP}"
  exit 1
fi
install -m 0440 -o root -g root "${SUDOERS_TMP}" /etc/sudoers.d/nat64-manager
rm -f "${SUDOERS_TMP}"
visudo -cf /etc/sudoers.d/nat64-manager

echo "==> Preparing environment file"
if [[ ! -f "${CONFIG_DIR}/nat64-manager.env" ]]; then
  install -m 0640 -o root -g "${APP_GROUP}" "${SRC_ROOT}/.env.example" "${CONFIG_DIR}/nat64-manager.env"
  "${PYTHON_ABS}" - "${CONFIG_DIR}/nat64-manager.env" "${PYTHON_ABS}" <<'PY'
import base64
import secrets
import sys
from pathlib import Path

path = Path(sys.argv[1])
python = sys.argv[2]
text = path.read_text(encoding="utf-8")
text = text.replace("CHANGE_ME_AT_LEAST_32_RANDOM_CHARACTERS", secrets.token_urlsafe(48))
text = text.replace(
    "CHANGE_ME_FERNET_KEY",
    base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
)
text = text.replace("HOSTCTL_PYTHON=/usr/bin/python3", f"HOSTCTL_PYTHON={python}")
path.write_text(text, encoding="utf-8")
PY
  chown root:"${APP_GROUP}" "${CONFIG_DIR}/nat64-manager.env"
  chmod 0640 "${CONFIG_DIR}/nat64-manager.env"
  echo "    Generated SECRET_KEY and APP_ENCRYPTION_KEY."
  echo "    HOSTCTL_PYTHON is set to ${PYTHON_ABS} to match the sudoers policy."
  echo "    EDIT ${CONFIG_DIR}/nat64-manager.env before starting:"
  echo "      - DATABASE_URL (PostgreSQL password)"
  echo "      - DEFAULT_ADMIN_PASSWORD"
  echo "      - CORS_ORIGINS / TRUSTED_HOSTS"
else
  echo "    Keeping existing ${CONFIG_DIR}/nat64-manager.env"
fi

echo "==> Installing systemd units"
install -m 0644 -o root -g root "${SRC_ROOT}/deploy/systemd/nat64-manager.service" /etc/systemd/system/nat64-manager.service
install -m 0644 -o root -g root "${SRC_ROOT}/deploy/systemd/nat64-manager-migrate.service" /etc/systemd/system/nat64-manager-migrate.service
systemctl daemon-reload
systemctl enable nat64-manager-migrate.service nat64-manager.service >/dev/null

cat <<EOF

Installation complete.

Application:  ${APP_ROOT}
Config:       ${CONFIG_DIR}/nat64-manager.env
Data:         ${DATA_DIR}
Backups:      ${BACKUP_DIR}
Helper:       ${APP_ROOT}/hostctl.py (root:root 0755)
Sudoers:      /etc/sudoers.d/nat64-manager
Units:        nat64-manager-migrate.service, nat64-manager.service

Before starting:
  1. Edit ${CONFIG_DIR}/nat64-manager.env and replace every CHANGE_ME value.
  2. Create the PostgreSQL role/database, then run:
       systemctl start nat64-manager-migrate.service
     (The migrate unit reads the environment file and runs Alembic from
     ${APP_ROOT}/backend.)
  3. Configure BIND9 include, Jool, nftables and netplan as described in
     ${APP_ROOT}/docs/02-INSTALLATION.md.
  4. Install the nginx or Caddy TLS configuration from ${APP_ROOT}/deploy/.
  5. Start the API:  systemctl start nat64-manager.service
  6. Open the UI, sign in with the bootstrap administrator, change the password,
     then complete the 11-step Installation Wizard.

EOF

if [[ "${START_SERVICE}" == "true" ]]; then
  echo "==> Starting services"
  systemctl start nat64-manager-migrate.service
  systemctl start nat64-manager.service
  systemctl --no-pager --lines=0 status nat64-manager.service || true
fi
