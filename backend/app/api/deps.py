"""Shared FastAPI dependencies."""
from __future__ import annotations

from fastapi import Depends, Request

from app.core.errors import DemoOnlyError
from app.system import RaceSafeSystem

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def get_system(request: Request) -> RaceSafeSystem:
    return request.app.state.system


def require_demo_mode(system: RaceSafeSystem = Depends(get_system)) -> RaceSafeSystem:
    """Demo-only endpoints (failure injection, race lab, resets)."""
    if not system.settings.demo_mode:
        raise DemoOnlyError("This endpoint is available only when RACESAFE_ENV=development")
    return system


def require_local_demo(request: Request, system: RaceSafeSystem = Depends(require_demo_mode)) -> RaceSafeSystem:
    """Destructive endpoints additionally require a request from this machine."""
    host = request.client.host if request.client else ""
    if host not in LOCAL_HOSTS:
        raise DemoOnlyError("Destructive demo endpoints can only be called from localhost")
    return system
