from __future__ import annotations

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.core.routing import APIRouter
from app.models import User
from app.schemas import HaNodeCreate, HaNodeUpdate, HaSettingsUpdate, HaSyncImport
from app.services import changes, ha

router = APIRouter(prefix="/ha", tags=["ha"])

READ = require_permission("read")
WRITE = require_permission("settings.write")
REQUEST = require_permission("changes.request")


@router.get("")
def status(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return ha.status(db)


@router.get("/settings")
def get_settings(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return ha.settings_dict(db, ha.get_settings(db))


@router.patch("/settings")
def update_settings(
    payload: HaSettingsUpdate,
    request: Request,
    user: User = Depends(WRITE),
    db: Session = Depends(get_db),
) -> dict:
    record = ha.get_settings(db)
    data = payload.model_dump(exclude_unset=True)
    record = ha.update_settings(db, record, data, user=user)
    return {
        "settings": ha.settings_dict(db, record),
        "applied_to_host": False,
        "note": "Stored in the database only. Create and approve ha.apply to install keepalived.",
    }


@router.get("/preview")
def preview(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    try:
        return ha.render_keepalived(db)
    except Exception as exc:
        return {
            "content": None,
            "hash": None,
            "errors": [f"{type(exc).__name__}: {exc}"],
            "settings": ha.settings_dict(db, ha.get_settings(db)),
        }


@router.post("/apply", status_code=202)
def apply_ha(user: User = Depends(REQUEST), db: Session = Depends(get_db)) -> dict:
    change = changes.create_change(db, action_name="ha.apply", payload={}, user=user)
    return changes.change_dict(change)


@router.get("/nodes")
def list_nodes(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return {"items": ha.list_nodes(db)}


@router.post("/nodes", status_code=201)
def create_node(
    payload: HaNodeCreate,
    request: Request,
    user: User = Depends(WRITE),
    db: Session = Depends(get_db),
) -> dict:
    node = ha.create_node(db, payload.model_dump(), user=user)
    return next(item for item in ha.list_nodes(db) if item["id"] == node.id)


@router.patch("/nodes/{node_id}")
def update_node(
    node_id: int,
    payload: HaNodeUpdate,
    request: Request,
    user: User = Depends(WRITE),
    db: Session = Depends(get_db),
) -> dict:
    node = ha.update_node(db, node_id, payload.model_dump(exclude_unset=True), user=user)
    return next(item for item in ha.list_nodes(db) if item["id"] == node.id)


@router.delete("/nodes/{node_id}")
def delete_node(
    node_id: int,
    request: Request,
    user: User = Depends(WRITE),
    db: Session = Depends(get_db),
) -> dict:
    ha.delete_node(db, node_id, user=user)
    return {"deleted": True, "id": node_id}


@router.post("/nodes/check")
async def check_nodes(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return {"items": await ha.check_nodes(db)}


@router.get("/export")
def export_config(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return ha.sync_export(db)


@router.post("/import")
def import_config(
    payload: HaSyncImport,
    x_ha_sync_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Node-to-node configuration sync. Requires the shared HA_SYNC_TOKEN header."""
    ha.require_sync_token(x_ha_sync_token)
    return ha.sync_import(db, payload.model_dump(), user=None)
