"""Application errors. The API layer turns these into JSON responses."""
from __future__ import annotations


class RaceSafeError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(RaceSafeError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(RaceSafeError):
    status_code = 409
    code = "CONFLICT"


class QueueFullError(RaceSafeError):
    status_code = 503
    code = "QUEUE_FULL"


class DemoOnlyError(RaceSafeError):
    status_code = 403
    code = "DEMO_MODE_ONLY"


class DatabaseUnavailableError(RaceSafeError):
    status_code = 503
    code = "DATABASE_UNAVAILABLE"
