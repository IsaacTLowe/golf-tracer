from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from tracer_core import db

from .. import service
from ..auth import AuthUser, current_user
from ..errors import ERRORS
from ..schemas import ProfileOut, ProfileUpdate

router = APIRouter(prefix="/v1", responses=ERRORS, tags=["profile"])

User = Annotated[AuthUser, Depends(current_user)]


def _out(row: dict[str, Any]) -> ProfileOut:
    return ProfileOut.model_validate(
        {
            "id": str(row["id"]),
            "display_name": row["display_name"],
            "default_tracer_style": row["default_tracer_style"],
            "created_at": row["created_at"],
        }
    )


@router.get("/me", response_model=ProfileOut, operation_id="getMe")
def get_me(user: User) -> ProfileOut:
    with db.transaction() as conn:
        row = service.update_profile(conn, user.id, {})
    return _out(row)


@router.patch("/me", response_model=ProfileOut, operation_id="updateMe")
def update_me(body: ProfileUpdate, user: User) -> ProfileOut:
    fields = body.model_dump(include=body.model_fields_set)
    with db.transaction() as conn:
        row = service.update_profile(conn, user.id, fields)
    return _out(row)
