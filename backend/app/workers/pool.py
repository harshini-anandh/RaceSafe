"""Bounded worker pool: a fixed number of threads pulling from the scheduler.

Python threads run one bytecode at a time (the GIL), so they do not give CPU
parallelism. They are a good fit here because each worker spends most of its
time waiting on MySQL (network I/O and row locks), and the GIL is released
while waiting. A fixed pool also caps threads and DB connections.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from app.scheduler.fair_queue import Job, RequestScheduler

logger = logging.getLogger("racesafe.workers")


class WorkerPool:
    def __init__(self, scheduler: RequestScheduler, handler: Callable[[Job, str], None], size: int = 8):
        self.scheduler = scheduler
        self.handler = handler
        self.size = size
        self._executor: ThreadPoolExecutor | None = None
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self.worker_states: list[dict] = [
            {"worker": f"W{i + 1}", "state": "stopped", "request_id": None, "course_id": None, "processed": 0}
            for i in range(size)
        ]

    def start(self) -> None:
        if self._executor is not None:
            return
        self._stop.clear()
        self._executor = ThreadPoolExecutor(max_workers=self.size, thread_name_prefix="racesafe-worker")
        for index in range(self.size):
            self._executor.submit(self._run, index)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self.scheduler.close()
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None

    def _set_state(self, index: int, **values) -> None:
        with self._state_lock:
            self.worker_states[index].update(values)

    def _run(self, index: int) -> None:
        name = f"W{index + 1}"
        self._set_state(index, state="idle")
        while not self._stop.is_set():
            job = self.scheduler.get(timeout=0.5)
            if job is None:
                continue
            self._set_state(index, state="busy", request_id=job.request_id, course_id=job.course_id)
            try:
                self.handler(job, name)
            except Exception:  # last safety net: one bad request never kills a worker
                logger.exception("Unhandled error in worker %s for request %s", name, job.request_id)
            finally:
                self.scheduler.task_done(job)
                with self._state_lock:
                    state = self.worker_states[index]
                    state.update(state="idle", request_id=None, course_id=None)
                    state["processed"] += 1
        self._set_state(index, state="stopped")

    def snapshot(self) -> dict:
        with self._state_lock:
            states = [dict(s) for s in self.worker_states]
        return {
            "size": self.size,
            "active": sum(1 for s in states if s["state"] == "busy"),
            "workers": states,
        }
