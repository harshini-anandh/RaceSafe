"""Controlled failure injection (development/demo mode only).

BEFORE_COMMIT  the worker raises an error after doing its writes but before
               COMMIT, so MySQL rolls the transaction back.
LOST_RESPONSE  the transaction commits normally, but the API drops the HTTP
               response, as if the network failed on the way back.

A failure can be armed for one specific request ID (precise demos and tests)
or for "the next N requests" processed by any worker.
"""
from __future__ import annotations

import threading
from collections import Counter
from enum import Enum

from app.core.errors import DemoOnlyError


class FailureKind(str, Enum):
    BEFORE_COMMIT = "BEFORE_COMMIT"
    LOST_RESPONSE = "LOST_RESPONSE"


class InjectedWorkerFailure(RuntimeError):
    """Raised inside a transaction to simulate a worker crashing before commit."""


class FailureInjector:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self._lock = threading.Lock()
        self._by_request: Counter[tuple[str, str]] = Counter()
        self._next_any: Counter[str] = Counter()

    def arm(self, kind: FailureKind, request_id: str | None = None, count: int = 1) -> None:
        if not self.enabled:
            raise DemoOnlyError("Failure injection is only available when RACESAFE_ENV=development")
        with self._lock:
            if request_id:
                self._by_request[(kind.value, request_id)] += count
            else:
                self._next_any[kind.value] += count

    def consume(self, kind: FailureKind, request_id: str) -> bool:
        """True if a failure of this kind should happen now (and uses it up)."""
        if not self.enabled:
            return False
        with self._lock:
            key = (kind.value, request_id)
            if self._by_request[key] > 0:
                self._by_request[key] -= 1
                if self._by_request[key] == 0:
                    del self._by_request[key]
                return True
            self._by_request.pop(key, None)
            if self._next_any[kind.value] > 0:
                self._next_any[kind.value] -= 1
                return True
            return False

    def maybe_fail_before_commit(self, request_id: str, attempt: int) -> None:
        if self.consume(FailureKind.BEFORE_COMMIT, request_id):
            raise InjectedWorkerFailure(f"Injected worker failure before COMMIT (attempt {attempt})")

    def clear(self) -> None:
        with self._lock:
            self._by_request.clear()
            self._next_any.clear()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "armed_for_requests": [
                    {"kind": kind, "request_id": rid, "remaining": n} for (kind, rid), n in self._by_request.items()
                ],
                "armed_next_any": {k: v for k, v in self._next_any.items() if v > 0},
            }
