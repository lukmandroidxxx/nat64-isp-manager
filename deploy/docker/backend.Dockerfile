# syntax=docker/dockerfile:1
# Multi-stage image: build the React SPA, then install the FastAPI backend.
# This image is for the application components only. Jool, BIND9, nftables,
# netplan and the privileged host helper must run natively on the Linux
# gateway; see docs/02-INSTALLATION.md.

FROM node:22-bookworm AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/nat64-manager/venv/bin:${PATH}"

RUN groupadd --system --gid 995 nat64mgr \
    && useradd --system --uid 995 --gid nat64mgr --home-dir /var/lib/nat64-manager \
       --shell /usr/sbin/nologin nat64mgr \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/nat64-manager/backend

COPY backend/requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/nat64-manager/venv \
    && /opt/nat64-manager/venv/bin/pip install --upgrade pip \
    && /opt/nat64-manager/venv/bin/pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY backend/ /opt/nat64-manager/backend/
COPY deploy/hostctl/hostctl.py /opt/nat64-manager/hostctl.py
COPY --from=frontend /build/dist/ /opt/nat64-manager/frontend/dist/
COPY deploy/docker/entrypoint.sh /usr/local/bin/nat64-entrypoint
RUN chmod 0755 /usr/local/bin/nat64-entrypoint \
    && chown -R nat64mgr:nat64mgr /var/lib/nat64-manager \
    && mkdir -p /var/lib/nat64-manager/backups \
    && chown -R nat64mgr:nat64mgr /var/lib/nat64-manager \
    && chmod 0755 /opt/nat64-manager/hostctl.py
# The helper is present for inspection and for bind-mounted native use, but the
# container runs with HOST_CONTROL_MODE=disabled and has no sudo/Jool access.

USER nat64mgr
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/nat64-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
