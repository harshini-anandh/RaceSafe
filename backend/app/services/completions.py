"""Lets an API call wait for its request to finish (POST ...?wait=true).

Each waiter gets its own concurrent.futures.Future. Workers call resolve()
AFTER commit, so a waiter that is woken can read the committed result.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from concurrent.futures import Future, InvalidStateError


class CompletionRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._waiters: dict[str, list[Future]] = defaultdict(list)

    def add_waiter(self, request_id: str) -> Future:
        future: Future = Future()
        with self._lock:
            self._waiters[request_id].append(future)
        return future

    def discard(self, request_id: str, future: Future) -> None:
        with self._lock:
            waiters = self._waiters.get(request_id)
            if waiters and future in waiters:
                waiters.remove(future)
                if not waiters:
                    del self._waiters[request_id]

    def resolve(self, request_id: str, value: str) -> None:
        with self._lock:
            waiters = self._waiters.pop(request_id, [])
        for future in waiters:
            try:
                future.set_result(value)
            except InvalidStateError:
                pass  # waiter already gave up

    def pending_count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._waiters.values())
