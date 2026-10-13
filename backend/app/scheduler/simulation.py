"""Deterministic logical-tick simulation of the scheduler + worker pool + row locks.

Used for challenge KO-06-C3 (hot-course head-of-line blocking). Timings on a
real laptop are noisy; this model removes the noise so the scheduling effect
can be explained exactly. It uses the REAL RequestScheduler class for every
dispatch decision, and models only the parts outside Python:

* N worker threads: a worker takes the next job the scheduler hands out.
* MySQL row lock per course: a dispatched job must hold its course's lock for
  its whole service time; other jobs of that course wait in FIFO lock order
  (like InnoDB's lock queue) while still occupying their worker ("blocked").
* Seats: the job that holds the lock decides CONFIRMED / WAITLISTED, so seat
  safety does not depend on the scheduling policy at all.

Time is measured in logical ticks; all requests arrive at tick 0 in the given
order.
"""
from __future__ import annotations

from collections import Counter, deque

from app.scheduler.fair_queue import Job, RequestScheduler


def simulate(mode: str, arrivals: list[tuple[str, int]], workers: int = 8, per_course_limit: int = 1,
             capacities: dict[str, int] | None = None) -> dict:
    """arrivals: [(course_id, service_ticks), ...] in arrival order."""
    capacities = capacities or {}
    scheduler = RequestScheduler(mode=mode, per_course_limit=per_course_limit, max_size=10 ** 7)
    records: dict[str, dict] = {}
    per_course_counter: Counter[str] = Counter()
    for course, service in arrivals:
        per_course_counter[course] += 1
        rid = f"{course}{per_course_counter[course]}"
        records[rid] = {"request": rid, "course": course, "service": service, "arrival": 0,
                        "dispatch": None, "lock": None, "done": None, "worker": None, "result": None}
        scheduler.submit(Job(request_id=rid, student_id=rid, course_id=course, work_ms=service))

    free_workers = list(range(1, workers + 1))
    holder: dict[str, tuple[str, int]] = {}          # course -> (request, end tick)
    lock_queue: dict[str, deque[str]] = {}            # course -> waiting requests
    jobs: dict[str, Job] = {}
    confirmed: Counter[str] = Counter()
    timeline: list[dict] = []
    busy_ticks = blocked_ticks = 0
    t = 0

    def acquire(rid: str, now: int) -> None:
        rec = records[rid]
        rec["lock"] = now
        holder[rec["course"]] = (rid, now + rec["service"])
        if confirmed[rec["course"]] < capacities.get(rec["course"], 1):
            confirmed[rec["course"]] += 1
            rec["result"] = "CONFIRMED"
        else:
            rec["result"] = "WAITLISTED"

    while True:
        # 1. idle workers take whatever the scheduler is willing to dispatch now
        while free_workers:
            job = scheduler.get(timeout=0)
            if job is None:
                break
            worker = free_workers.pop(0)
            rec = records[job.request_id]
            rec["dispatch"], rec["worker"] = t, f"W{worker}"
            jobs[job.request_id] = job
            if rec["course"] in holder:
                lock_queue.setdefault(rec["course"], deque()).append(job.request_id)
            else:
                acquire(job.request_id, t)

        holding = len(holder)
        blocked = sum(len(q) for q in lock_queue.values())
        point = {"t": t, "busy": holding + blocked, "holding": holding, "blocked": blocked,
                 "courses": sorted(holder)}
        if not timeline or {k: v for k, v in timeline[-1].items() if k != "t"} != \
                {k: v for k, v in point.items() if k != "t"}:
            timeline.append(point)
        if not holder:
            break

        # 2. jump to the next completion
        t_next = min(end for _, end in holder.values())
        busy_ticks += (holding + blocked) * (t_next - t)
        blocked_ticks += blocked * (t_next - t)
        t = t_next
        for course in sorted(c for c, (_, end) in holder.items() if end == t):
            rid, _ = holder.pop(course)
            rec = records[rid]
            rec["done"] = t
            free_workers.append(int(rec["worker"][1:]))
            scheduler.task_done(jobs[rid])
            if lock_queue.get(course):
                acquire(lock_queue[course].popleft(), t)
        free_workers.sort()

    makespan = t
    per_course: dict[str, dict] = {}
    for rec in records.values():
        pc = per_course.setdefault(rec["course"], {"course": rec["course"], "requests": 0, "service_ticks": rec["service"],
                                                   "_dispatch": [], "_lockwait": [], "_start": [], "_done": [],
                                                   "confirmed": 0, "waitlisted": 0})
        pc["requests"] += 1
        pc["_dispatch"].append(rec["dispatch"])
        pc["_lockwait"].append(rec["lock"] - rec["dispatch"])
        pc["_start"].append(rec["lock"])
        pc["_done"].append(rec["done"])
        pc["confirmed" if rec["result"] == "CONFIRMED" else "waitlisted"] += 1
    for pc in per_course.values():
        n = pc["requests"]
        d, lw, st, dn = pc.pop("_dispatch"), pc.pop("_lockwait"), pc.pop("_start"), pc.pop("_done")
        pc.update(avg_dispatch_wait=sum(d) / n, avg_lock_wait=sum(lw) / n, avg_wait=sum(st) / n, max_wait=max(st),
                  avg_turnaround=sum(dn) / n, first_start=min(st), last_done=max(dn))

    stats = scheduler.snapshot()
    return {
        "mode": mode,
        "workers": workers,
        "per_course_limit": per_course_limit if mode == "FAIR_QUEUE" else None,
        "makespan_ticks": makespan,
        "per_course": per_course,
        "active_slots": {
            "peak_busy_workers": max(p["busy"] for p in timeline),
            "peak_blocked_on_lock": max(p["blocked"] for p in timeline),
            "avg_busy_workers": round(busy_ticks / makespan, 3) if makespan else 0,
            "busy_worker_ticks": busy_ticks,
            "blocked_worker_ticks": blocked_ticks,
            "peak_in_flight_by_course": stats["peak_in_flight_by_course"],
            "peak_courses_in_flight": stats["peak_courses_in_flight"],
        },
        "timeline": timeline,
        "requests": records,
    }
