"""Health endpoint for service monitoring."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter


router = APIRouter(tags=["health"])
_start_time = time.monotonic()


@router.get("/healthz", summary="Service health status")
async def healthcheck() -> dict[str, Any]:
    """Return basic health details for uptime and readiness probes."""

    uptime_seconds = round(time.monotonic() - _start_time, 2)
    return {"status": "ok", "uptime_seconds": uptime_seconds}


__all__ = ["router", "healthcheck"]
