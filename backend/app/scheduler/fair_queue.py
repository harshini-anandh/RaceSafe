"""Request scheduler: Global FIFO baseline and Contention-Aware FairQueue.

The scheduler decides only the ORDER in which requests reach worker threads.
It never decides who gets a seat - that is done by the MySQL transaction.

GLOBAL_FIFO  One shared deque. Workers take requests in arrival order. A burst
             for one hot course fills every worker, so other courses wait.

FAIR_QUEUE   One FIFO deque per course. Workers are handed requests by
             round-robin over the non-empty course queues, FIFO inside a course,
             and at most `per_course_limit` requests of a course are in flight
             at once (default 1, as in the design document).

Synchronisation: one threading.Condition protects all queue state. Idle
workers block in Condition.wait() (no busy-waiting) and are woken by
notify_all() when a request arrives or a course becomes free.
"""
from __future__ import annotations

import itertools
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime

from app.core.clock import utcnow
from app.models.enums import SchedulerMode

_sequence = itertools.count(1)


@dataclass
class Job:
    """One registration request travelling through the scheduler."""

    request_id: str
    student_id: str
    course_id: str
    attempt: int = 1                     # attempt number this dispatch will run as
    experiment_id: int | None = None
    work_ms: int = 0                     # simulated work inside the transaction
    seq: int = field(default_factory=lambda: next(_sequence))  # global arrival order
    enqueued_at: datetime | None = None  # wall clock (UTC) of first enqueue
    dispatched_at: datetime | None = None
    dispatched_perf: float | None = None


class RequestScheduler:
    def __init__(self, mode: str = SchedulerMode.FAIR_QUEUE.value, per_course_limit: int = 1,
                 max_size: int = 5000, record_dispatch_order: bool = False):
        self._cond = threading.Condition()
        self._mode = SchedulerMode(mode)
        self.per_course_limit = per_course_limit
        self.max_size = max_size

        self._global: deque[Job] = deque()            # GLOBAL_FIFO structure
        self._per_course: dict[str, deque[Job]] = {}  # FAIR_QUEUE structure
        self._ring: list[str] = []                    # round-robin order of courses
        self._rr_index = 0

        self._size = 0
        self._in_flight: Counter[str] = Counter()     # course_id -> running jobs
        self._tracked: Counter[str] = Counter()       # request_id -> queued or running
        self._paused = False
        self._closed = False

        # statistics
        self.dispatched_total = 0
        self.peak_in_flight = 0
        self.peak_courses_in_flight = 0
        self.peak_in_flight_by_course: Counter[str] = Counter()
        self.record_dispatch_order = record_dispatch_order
        self.dispatch_order: list[tuple[str, str]] = []  # (request_id, course_id), tests only

    # ------------------------------------------------------------------ producers
    @property
    def mode(self) -> str:
        return self._mode.value

    def has_room(self) -> bool:
        with self._cond:
            return self._size < self.max_size

    def submit(self, job: Job, front: bool = False, force: bool = False) -> bool:
        """Add a job. front=True is used for retries ("requeue at the front").

        Returns False (job not added) when the queue is full, unless force=True
        (retries and restart recovery must never be dropped).
        """
        with self._cond:
            if self._closed:
                return False
            if not (front or force) and self._size >= self.max_size:
                return False
            if job.enqueued_at is None:
                job.enqueued_at = utcnow()
            if self._mode is SchedulerMode.GLOBAL_FIFO:
                (self._global.appendleft if front else self._global.append)(job)
            else:
                queue = self._course_queue(job.course_id)
                (queue.appendleft if front else queue.append)(job)
            self._size += 1
            self._tracked[job.request_id] += 1
            self._cond.notify_all()
            return True

    def _course_queue(self, course_id: str) -> deque[Job]:
        queue = self._per_course.get(course_id)
        if queue is None:
            queue = self._per_course[course_id] = deque()
            self._ring.append(course_id)
        return queue

    # ------------------------------------------------------------------ consumers
    def get(self, timeout: float | None = None) -> Job | None:
        """Block until a job may be dispatched. Returns None on timeout/close."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while True:
                if self._closed:
                    return None
                if not self._paused:
                    job = self._pick()
                    if job is not None:
                        return job
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                self._cond.wait(remaining)

    def _pick(self) -> Job | None:
        job: Job | None = None
        if self._mode is SchedulerMode.GLOBAL_FIFO:
            if self._global:
                job = self._global.popleft()
        else:
            n = len(self._ring)
            for step in range(n):
                index = (self._rr_index + step) % n
                course_id = self._ring[index]
                queue = self._per_course[course_id]
                if queue and self._in_flight[course_id] < self.per_course_limit:
                    job = queue.popleft()
                    self._rr_index = (index + 1) % n  # next turn starts after this course
                    break
        if job is None:
            return None
        self._size -= 1
        self._in_flight[job.course_id] += 1
        job.dispatched_at = utcnow()
        job.dispatched_perf = time.perf_counter()
        self.dispatched_total += 1
        in_flight_total = sum(self._in_flight.values())
        courses_in_flight = sum(1 for v in self._in_flight.values() if v > 0)
        self.peak_in_flight = max(self.peak_in_flight, in_flight_total)
        self.peak_courses_in_flight = max(self.peak_courses_in_flight, courses_in_flight)
        self.peak_in_flight_by_course[job.course_id] = max(self.peak_in_flight_by_course[job.course_id],
                                                           self._in_flight[job.course_id])
        if self.record_dispatch_order:
            self.dispatch_order.append((job.request_id, job.course_id))
        return job

    def task_done(self, job: Job) -> None:
        """Called by the worker when it has finished with a dispatched job."""
        with self._cond:
            self._in_flight[job.course_id] -= 1
            if self._in_flight[job.course_id] <= 0:
                del self._in_flight[job.course_id]
            self._untrack(job.request_id)
            self._cond.notify_all()

    def _untrack(self, request_id: str) -> None:
        self._tracked[request_id] -= 1
        if self._tracked[request_id] <= 0:
            del self._tracked[request_id]

    # ------------------------------------------------------------------ control
    def set_mode(self, mode: str) -> None:
        """Switch policy. Already-queued jobs are migrated, keeping arrival order."""
        new_mode = SchedulerMode(mode)
        with self._cond:
            if new_mode is self._mode:
                return
            if new_mode is SchedulerMode.GLOBAL_FIFO:
                jobs = sorted((j for q in self._per_course.values() for j in q), key=lambda j: j.seq)
                self._per_course.clear()
                self._ring.clear()
                self._rr_index = 0
                self._global.extend(jobs)
            else:
                jobs = list(self._global)
                self._global.clear()
                for job in jobs:
                    self._course_queue(job.course_id).append(job)
            self._mode = new_mode
            self._cond.notify_all()

    def pause(self) -> None:
        with self._cond:
            self._paused = True

    def resume(self) -> None:
        with self._cond:
            self._paused = False
            self._cond.notify_all()

    @property
    def paused(self) -> bool:
        return self._paused

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def drain(self) -> list[Job]:
        """Remove every queued (not running) job - used to simulate a crash."""
        with self._cond:
            jobs = list(self._global) + [j for q in self._per_course.values() for j in q]
            self._global.clear()
            for q in self._per_course.values():
                q.clear()
            self._size = 0
            for job in jobs:
                self._untrack(job.request_id)
            return jobs

    def is_tracked(self, request_id: str) -> bool:
        with self._cond:
            return self._tracked.get(request_id, 0) > 0

    def is_idle(self) -> bool:
        with self._cond:
            return self._size == 0 and not self._in_flight

    def wait_until_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._size > 0 or self._in_flight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True

    def reset_stats(self) -> None:
        with self._cond:
            self.dispatched_total = 0
            self.peak_in_flight = 0
            self.peak_courses_in_flight = 0
            self.peak_in_flight_by_course = Counter()
            self.dispatch_order = []

    def snapshot(self) -> dict:
        with self._cond:
            if self._mode is SchedulerMode.GLOBAL_FIFO:
                per_course = Counter(j.course_id for j in self._global)
            else:
                per_course = Counter({c: len(q) for c, q in self._per_course.items() if q})
            return {
                "mode": self._mode.value,
                "paused": self._paused,
                "queue_size": self._size,
                "max_queue_size": self.max_size,
                "per_course_limit": self.per_course_limit,
                "queued_by_course": dict(per_course),
                "in_flight_by_course": dict(self._in_flight),
                "in_flight_total": sum(self._in_flight.values()),
                "dispatched_total": self.dispatched_total,
                "peak_in_flight": self.peak_in_flight,
                "peak_courses_in_flight": self.peak_courses_in_flight,
                "peak_in_flight_by_course": dict(self.peak_in_flight_by_course),
            }
