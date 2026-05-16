"""Liveness probe."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from caesar import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
