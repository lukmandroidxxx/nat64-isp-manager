from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    audit,
    auth,
    backups,
    changes,
    clients,
    dashboard,
    dns64,
    firewall,
    ha,
    jool,
    network,
    pools,
    sessions,
    settings,
    users,
    wizard,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(users.roles_router)
api_router.include_router(dashboard.router)
api_router.include_router(jool.router)
api_router.include_router(pools.router)
api_router.include_router(clients.router)
api_router.include_router(sessions.router)
api_router.include_router(dns64.router)
api_router.include_router(firewall.router)
api_router.include_router(network.router)
api_router.include_router(changes.router)
api_router.include_router(backups.router)
api_router.include_router(settings.router)
api_router.include_router(wizard.router)
api_router.include_router(ha.router)
api_router.include_router(audit.router)

__all__ = ["api_router"]
