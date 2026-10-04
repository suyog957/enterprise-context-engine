from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import Header, HTTPException, status
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from enterprise_context.config import get_settings


class PrincipalContext(BaseModel):
    principal_id: str
    display_name: str
    buyer_id: str | None
    roles: list[str]
    business_unit_ids: list[str]
    approval_limit_minor: int = Field(ge=0)


def load_principal(principal_id: str) -> PrincipalContext | None:
    settings = get_settings()
    with psycopg.connect(
        settings.database_url, connect_timeout=3, row_factory=dict_row
    ) as connection:
        row: Any = connection.execute(
            """SELECT principal_id, display_name, buyer_id, roles, business_unit_ids,
                      approval_limit_minor
               FROM principal
               WHERE principal_id = %s AND active = true""",
            (principal_id,),
        ).fetchone()
    return PrincipalContext.model_validate(row) if row else None


def get_current_principal(
    principal_id: Annotated[str | None, Header(alias="X-Dev-Principal")] = None,
) -> PrincipalContext:
    settings = get_settings()
    if settings.environment != "local":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="production_identity_provider_not_configured",
        )
    if not principal_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="principal_required")
    principal = load_principal(principal_id)
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="principal_not_found")
    return principal
