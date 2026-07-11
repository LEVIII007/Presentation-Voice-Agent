"""Read-only endpoints for latency dashboards."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..services.latency_report import build_latency_report

router = APIRouter(prefix="/api/latency", tags=["latency"])


def get_container(request: Request):
    return request.app.state.container


@router.get("/sessions")
async def latency_sessions(c=Depends(get_container)):
    settings = c.settings
    path = settings.data_dir / settings.latency_log_file
    return build_latency_report(path)
