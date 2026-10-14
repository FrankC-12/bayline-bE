"""Authenticated, metadata-only error reports from web and mobile clients."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.core.audit_logging import record_error
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser

router = APIRouter(prefix="/telemetry", tags=["Bitácora"])


class ClientErrorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["web", "mobile"]
    kind: Literal["javascript_error", "unhandled_rejection", "render_error"]
    error_type: Literal[
        "Error", "TypeError", "RangeError", "ReferenceError", "SyntaxError", "Unknown"
    ]
    area: Literal["service_orders", "clients", "inventory", "finance", "settings", "other"] = (
        "other"
    )


@router.post(
    "/errors",
    status_code=204,
    summary="Registrar error del cliente web o móvil",
    description="Metadatos únicamente; la fecha, el usuario y la filial se toman en el servidor.",
)
async def report_client_error(
    payload: ClientErrorReport, user: Annotated[CurrentUser, Depends(get_current_user)]
):
    record_error("client_error", **payload.model_dump())
