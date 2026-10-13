"""Unit tests for the FairQueue / Global FIFO scheduler (no database needed)."""
from __future__ import annotations

import threading
import time
from collections import Counter

from app.scheduler.fair_queue import Job, RequestScheduler


def job(course: str, n: int) -> Job:
    return Job(request_id=f"{course}-{n}", student_id=f"S{n:04d}", course_id=course)


def drain_in_order(s: RequestScheduler) -> list[str]:
    order = []
    while True:
        j = s.get(timeout=0.05)
        if j is None:
            return order
        order.append(j.request_id)
        s.task_done(j)


def test_global_fifo_preserves_arrival_order():
    s = RequestScheduler(mode="GLOBAL_FIFO")
    ids = []
    for i in range(5):
        for c in ("HOT", "HOT", "A"):
            j = job(c, len(ids))
            ids.append(j.request_id)
            s.submit(j)
    assert drain_in_order(s) == ids


def test_fairqueue_round_robin_and_fifo_within_course():
    s = RequestScheduler(mode="FAIR_QUEUE", per_course_limit=1)
    for i in range(6):
        s.submit(job("HOT", i))
    for i in range(2):
        s.submit(job("A", i))
        s.submit(job("B", i))
    assert drain_in_order(s) == ["HOT-0", "A-0", "B-0", "HOT-1", "A-1", "B-1", "HOT-2", "HOT-3", "HOT-4", "HOT-5"]


def test_fairqueue_limits_one_in_flight_per_course():
    s = RequestScheduler(mode="FAIR_QUEUE", per_course_limit=1)
    s.submit(job("HOT", 0))
    s.submit(job("HOT", 1))
    first = s.get(timeout=0.1)
    assert first.request_id == "HOT-0"
    assert s.get(timeout=0.1) is None  # HOT already has one in flight
    s.task_done(first)
    assert s.get(timeout=0.1).request_id == "HOT-1"


def test_global_fifo_has_no_per_course_limit():
    s = RequestScheduler(mode="GLOBAL_FIFO")
    s.submit(job("HOT", 0))
    s.submit(job("HOT", 1))
    assert s.get(timeout=0.1) is not None
    assert s.get(timeout=0.1) is not None


def test_retry_goes_to_front_of_its_course_queue():
    s = RequestScheduler(mode="FAIR_QUEUE")
    for i in range(3):
        s.submit(job("HOT", i))
    j0 = s.get(timeout=0.1)
    s.submit(j0, front=True)  # retry
    s.task_done(j0)
    assert drain_in_order(s) == ["HOT-0", "HOT-1", "HOT-2"]


def test_mode_switch_migrates_queued_jobs_in_arrival_order():
    s = RequestScheduler(mode="FAIR_QUEUE")
    submitted = []
    for i in range(4):
        for c in ("HOT", "A"):
            j = job(c, i)
            submitted.append(j.request_id)
            s.submit(j)
    s.set_mode("GLOBAL_FIFO")
    assert drain_in_order(s) == submitted


def test_queue_full_rejects_new_but_accepts_retries():
    s = RequestScheduler(mode="FAIR_QUEUE", max_size=2)
    assert s.submit(job("A", 0)) and s.submit(job("A", 1))
    assert s.submit(job("A", 2)) is False
    assert s.submit(job("A", 3), front=True) is True  # retries are never dropped


def test_idle_worker_blocks_instead_of_spinning():
    s = RequestScheduler()
    t0 = time.monotonic()
    assert s.get(timeout=0.3) is None
    assert time.monotonic() - t0 >= 0.25

    result = {}

    def consumer():
        result["job"] = s.get(timeout=5)

    t = threading.Thread(target=consumer)
    t.start()
    time.sleep(0.1)
    s.submit(job("A", 1))  # notify_all wakes the sleeping worker
    t.join(timeout=2)
    assert result["job"].request_id == "A-1"


def test_paused_scheduler_dispatches_nothing():
    s = RequestScheduler()
    s.submit(job("A", 0))
    s.pause()
    assert s.get(timeout=0.1) is None
    s.resume()
    assert s.get(timeout=0.1) is not None


def test_drain_simulates_crash():
    s = RequestScheduler()
    for i in range(3):
        s.submit(job("A", i))
    dropped = s.drain()
    assert len(dropped) == 3
    assert s.snapshot()["queue_size"] == 0
    assert not s.is_tracked("A-0")


def test_concurrent_workers_dispatch_each_job_once_and_respect_course_limit():
    s = RequestScheduler(mode="FAIR_QUEUE", per_course_limit=1)
    courses = ["HOT"] * 300 + ["A"] * 50 + ["B"] * 50 + ["C"] * 50
    for i, c in enumerate(courses):
        s.submit(job(c, i))
    seen = Counter()
    running = Counter()
    violations = []
    lock = threading.Lock()

    def worker():
        while True:
            j = s.get(timeout=0.2)
            if j is None:
                return
            with lock:
                running[j.course_id] += 1
                if running[j.course_id] > 1:
                    violations.append(j.course_id)
                seen[j.request_id] += 1
            time.sleep(0.0005)
            with lock:
                running[j.course_id] -= 1
            s.task_done(j)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(seen) == len(courses)
    assert all(n == 1 for n in seen.values())
    assert not violations
    assert s.peak_courses_in_flight > 1  # different courses progressed at the same time


def test_quiet_course_served_earlier_under_fairqueue_than_fifo():
    """Same arrival order; compare when the quiet course's last request is dispatched."""
    arrivals = ["HOT"] * 40 + ["QUIET"] * 5 + ["HOT"] * 40

    def last_quiet_position(mode):
        s = RequestScheduler(mode=mode, per_course_limit=1)
        for i, c in enumerate(arrivals):
            s.submit(job(c, i))
        order = drain_in_order(s)
        return max(i for i, rid in enumerate(order) if rid.startswith("QUIET"))

    assert last_quiet_position("FAIR_QUEUE") < last_quiet_position("GLOBAL_FIFO")
