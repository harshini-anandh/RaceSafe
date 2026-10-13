"""Bounded worker pool: a fixed number of threads pulling from the scheduler.

Python threads run one bytecode at a time (the GIL), so they do not give CPU
parallelism. They are a good fit here because each worker spends most of its
time waiting on MySQL (network I/O and row locks), and the GIL is released
while waiting. A fixed pool also caps threads and DB connections.

Liveness rule: a dispatched job holds a scheduler "course slot" (FairQueue
allows one in-progress request per course). Whatever happens to the worker,
the slot MUST be released, otherwise that course would never be served again.
Order on failure: (1) the transaction is rolled back, (2) the job is put back
at the FRONT of its queue, (3) only then is the slot released - so a request
queued behind it cannot jump ahead. If the worker thread itself crashes, the
pool starts a replacement worker so the pool never shrinks.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from app.scheduler.fair_queue import Job, RequestScheduler

logger = logging.getLogger("racesafe.workers")


class WorkerPool:
    def __init__(self, scheduler: RequestScheduler, handler: Callable[[Job, str], None], size: int = 8,
                 on_unhandled: Callable[[Job, str, BaseException], None] | None = None):
        self.scheduler = scheduler
        self.handler = handler
        self.on_unhandled = on_unhandled
        self.size = size
        self._executor: ThreadPoolExecutor | None = None
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self.restarts = 0
        self.worker_states: list[dict] = [
            {"worker": f"W{i + 1}", "state": "stopped", "request_id": None, "course_id": None,
             "processed": 0, "generation": 0}
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
        with self._state_lock:
            self.worker_states[index]["generation"] += 1
            self.worker_states[index]["state"] = "idle"
        while not self._stop.is_set():
            job = self.scheduler.get(timeout=0.5)
            if job is None:
                continue
            self._set_state(index, state="busy", request_id=job.request_id, course_id=job.course_id)
            crashed: BaseException | None = None
            try:
                self.handler(job, name)
            except BaseException as exc:  # Exception = bug; BaseException = (simulated) thread death
                if not isinstance(exc, Exception):
                    crashed = exc
                logger.error("Worker %s lost request %s: %s", name, job.request_id, exc)
                try:
                    if self.on_unhandled:
                        self.on_unhandled(job, name, exc)  # rollback already happened; requeue at front
                except Exception:
                    logger.exception("Recovery of %s failed; restart recovery will pick it up", job.request_id)
            finally:
                self.scheduler.task_done(job)  # ALWAYS release the course slot
                with self._state_lock:
                    state = self.worker_states[index]
                    state.update(state="idle", request_id=None, course_id=None)
                    state["processed"] += 1
            if crashed is not None:
                self._replace(index, name)
                return  # this worker incarnation is dead
        self._set_state(index, state="stopped")

    def _replace(self, index: int, name: str) -> None:
        with self._state_lock:
            self.worker_states[index]["state"] = "restarting"
            self.restarts += 1
        if self._stop.is_set() or self._executor is None:
            return
        logger.warning("Worker %s crashed; starting a replacement", name)
        try:
            self._executor.submit(self._run, index)
        except RuntimeError:  # executor shutting down
            pass

    def live_workers(self) -> int:
        with self._state_lock:
            return sum(1 for s in self.worker_states if s["state"] in ("idle", "busy"))

    def snapshot(self) -> dict:
        with self._state_lock:
            states = [dict(s) for s in self.worker_states]
        return {
            "size": self.size,
            "active": sum(1 for s in states if s["state"] == "busy"),
            "live": sum(1 for s in states if s["state"] in ("idle", "busy")),
            "restarts": self.restarts,
            "workers": states,
        }
