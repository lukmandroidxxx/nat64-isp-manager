from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.core.routing import APIRouter
from app.models import User
from app.schemas import WizardStep
from app.services import wizard

router = APIRouter(prefix="/wizard", tags=["wizard"])

READ = require_permission("read")
WRITE = require_permission("settings.write")


@router.get("")
def get_wizard(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return wizard.get_state(db)


@router.post("/check")
def check_wizard(
    request: Request, user: User = Depends(WRITE), db: Session = Depends(get_db)
) -> dict:
    return wizard.check_all(db, user=user)


@router.get("/checks")
def last_checks(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    state = wizard.get_state(db)
    return {
        "last_check": state["last_check"],
        "current_step": state["current_step"],
        "completed": state["completed"],
    }


@router.post("/step")
def save_step(
    payload: WizardStep,
    request: Request,
    user: User = Depends(WRITE),
    db: Session = Depends(get_db),
) -> dict:
    return wizard.update_step(db, payload.step, payload.data, user=user)


@router.post("/complete")
def complete_wizard(
    request: Request, user: User = Depends(WRITE), db: Session = Depends(get_db)
) -> dict:
    return wizard.complete(db, user=user)


@router.post("/reset")
def reset_wizard(
    request: Request, user: User = Depends(WRITE), db: Session = Depends(get_db)
) -> dict:
    return wizard.reset(db, user=user)
