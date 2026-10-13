"""Runnable versions of the five KO-06 live technical challenges (PS OSH001).

Each challenge runs on its own isolated EXPERIMENT course(s) (CH1-A, CH2-A, ...)
through the real pipeline (intake -> scheduler -> worker pool -> MySQL) and
returns a report: the steps taken, every check with expected vs actual, and
the data needed to explain it (counts, waitlist, traces, request IDs).
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime
import threading
import time
import uuid
from concurrent.futures import wait as wait_futures
from contextlib import contextmanager

from sqlalchemy import text

from app.core.clock import iso, ms_between, utcnow
from app.core.errors import ConflictError, NotFoundError
from app.scheduler.simulation import simulate
from app.services import queries
from app.services.cancellation import cancel_registration
from app.services.demo_data import prepare_experiment_courses, student_ids
from app.workers.failure_injection import FailureKind

CHALLENGES = [
    {"id": "C1", "code": "KO-06-C1", "difficulty": "Easy", "title": "500 transactional requests",
     "challenge": "Run 500 distinct students against one capacity-10 course with the MySQL-backed worker pool; "
                  "show authoritative counts and waitlist.",
     "setup": "Course CH1-A capacity 10; 500 unique students and UUIDs; 8 workers.",
     "focus": "Verify the database-backed critical section", "concepts": "Threads; row locks; transactions"},
    {"id": "C2", "code": "KO-06-C2", "difficulty": "Medium", "title": "Reply lost and waitlist promotion",
     "challenge": "Drop one response after commit, retry its UUID, then cancel three confirmed places and promote "
                  "in stored FIFO order.",
     "setup": "Capacity 3; S1..S5 arrival sequence; retry S1 with the same UUID; cancel S1, S2, S3.",
     "focus": "Idempotency and atomic promotion", "concepts": "Synchronization; producer-consumer; recovery"},
    {"id": "C3", "code": "KO-06-C3", "difficulty": "Medium", "title": "Hot-course head-of-line control",
     "challenge": "Run FairQueue and Global FIFO with eight workers on a skewed deterministic demand. Report "
                  "per-course waits and active slots without claiming universal speedup.",
     "setup": "100 A requests then 1 B request; A/B capacity 1; A service 10 ticks, B service 1 tick; "
              "one in progress per course.",
     "focus": "Scheduler fairness independent of seat safety", "concepts": "Round robin; bounded pool; contention"},
    {"id": "C4", "code": "KO-06-C4", "difficulty": "Hard", "title": "Fail worker holding course slot",
     "challenge": "Inject failure after dequeue / course-in-progress marking but before commit. Ensure rollback, "
                  "requeue and release of the scheduler slot.",
     "setup": "Capacity 1; r1 fails its first attempt; r2 queued; retry limit 3.",
     "focus": "Prevent liveness failure outside the DB", "concepts": "Worker failure; locks; condition variables"},
    {"id": "C5", "code": "KO-06-C5", "difficulty": "Hard", "title": "Cancel-versus-register race",
     "challenge": "Run cancellation with waitlist promotion at the same time as a fresh registration. Prevent the "
                  "new arrival taking a seat ahead of the stored waitlist.",
     "setup": "Capacity 1; S1 confirmed, S2 waitlisted; cancel S1 concurrently with new S3.",
     "focus": "Waitlist fairness under race", "concepts": "Atomicity; row locking; FIFO"},
]


class Report:
    def __init__(self, cid: str):
        self.meta = next(c for c in CHALLENGES if c["id"] == cid)
        self.steps: list[dict] = []
        self.checks: list[dict] = []
        self.data: dict = {}
        self.started = time.perf_counter()
        self.started_at = utcnow()

    def step(self, message: str, **details) -> None:
        self.steps.append({"t_ms": round((time.perf_counter() - self.started) * 1000, 1), "message": message,
                           **({"details": details} if details else {})})

    def check(self, name: str, expected, actual, passed: bool | None = None) -> bool:
        ok = (expected == actual) if passed is None else bool(passed)
        self.checks.append({"name": name, "expected": expected, "actual": actual, "passed": ok})
        return ok

    def result(self) -> dict:
        return {**self.meta, "passed": bool(self.checks) and all(c["passed"] for c in self.checks),
                "checks": self.checks, "steps": self.steps, "data": self.data,
                "started_at": iso(self.started_at),
                "duration_ms": round((time.perf_counter() - self.started) * 1000, 1)}


def _label(student_id: str) -> str:
    """S0001 -> S1 (the challenge sheet's naming)."""
    return f"S{int(student_id[1:])}" if student_id.startswith("S") and student_id[1:].isdigit() else student_id


def _parse_meta(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


class ChallengeRunner:
    def __init__(self, system):
        self.system = system
        self.last_reports: dict[str, dict] = {}

    # ------------------------------------------------------------------ public
    def list(self) -> list[dict]:
        return [{**c, "last_result": self._summary(self.last_reports.get(c["id"]))} for c in CHALLENGES]

    @staticmethod
    def _summary(report):
        if not report:
            return None
        return {"passed": report["passed"], "started_at": report["started_at"], "duration_ms": report["duration_ms"],
                "checks_passed": sum(c["passed"] for c in report["checks"]), "checks_total": len(report["checks"])}

    def run(self, cid: str, params: dict | None = None) -> dict:
        runner = {"C1": self.c1, "C2": self.c2, "C3": self.c3, "C4": self.c4, "C5": self.c5}.get(cid.upper())
        if runner is None:
            raise NotFoundError(f"Unknown challenge '{cid}' (use C1..C5)")
        if not self.system.experiment_lock.acquire(blocking=False):
            raise ConflictError("Another experiment or challenge is running")
        try:
            if not self.system.scheduler.wait_until_idle(timeout=30):
                raise ConflictError("The queue still has pending requests; wait for them to finish first")
            report = runner(params or {})
            self.last_reports[cid.upper()] = report
            return report
        finally:
            self.system.experiment_lock.release()

    # ------------------------------------------------------------------ helpers
    @property
    def engine(self):
        return self.system.engine

    def _prepare(self, courses: list[tuple[str, str, int]]) -> None:
        with self.engine.begin() as conn:
            prepare_experiment_courses(conn, courses)

    def _experiment(self, name: str, params: dict) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                text("INSERT INTO experiments (experiment_type, scheduler_mode, status, config, created_at) "
                     "VALUES ('CHALLENGE', :mode, 'COMPLETED', :cfg, :now)"),
                {"mode": self.system.scheduler.mode, "cfg": json.dumps({"challenge": name, **params}),
                 "now": utcnow()},
            ).lastrowid

    def _outcome(self, request_id: str) -> dict:
        with self.engine.connect() as conn:
            return queries.request_outcome(conn, request_id)

    def _submit_wait(self, student_id: str, course_id: str, request_id: str | None = None, timeout: float = 60,
                     **kwargs) -> dict:
        r = self.system.intake.submit(student_id, course_id, request_id or str(uuid.uuid4()), wait=True, **kwargs)
        if r.waiter is not None:
            r.waiter.result(timeout=timeout)
        out = self._outcome(r.request_id)
        out["replayed"] = r.replayed
        return out

    def _events(self, request_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            return list(reversed(queries.list_events(conn, request_id=request_id, include_experiment=True,
                                                     limit=500)))

    def _commit_meta(self, request_id: str) -> dict:
        for e in self._events(request_id):
            if e["event_type"] == "TRANSACTION_COMMITTED":
                return {**_parse_meta(e["metadata"]), "committed_at": e["created_at"]}
        return {}

    def _course(self, course_id: str) -> dict:
        with self.engine.connect() as conn:
            return queries.course_registrations(conn, course_id)

    @contextmanager
    def _mode(self, mode: str):
        previous = self.system.scheduler.mode
        self.system.scheduler.set_mode(mode)
        try:
            yield
        finally:
            self.system.scheduler.set_mode(previous)

    # ================================================================== C1
    def c1(self, params: dict) -> dict:
        rep = Report("C1")
        n, capacity, course = int(params.get("students", 500)), int(params.get("capacity", 10)), "CH1-A"
        mode = params.get("scheduler_mode", "GLOBAL_FIFO")
        workers = self.system.settings.worker_count
        self._prepare([(course, "Challenge C1 course A", capacity)])
        with self.engine.connect() as conn:
            students = student_ids(conn, n)
        exp_id = self._experiment("C1", {"students": n, "capacity": capacity, "scheduler_mode": mode})
        rep.step(f"Prepared {course} (capacity {capacity}); {n} distinct students; {workers} worker threads; "
                 f"scheduler {mode}")

        with self._mode(mode):
            self.system.scheduler.reset_stats()
            request_ids = [str(uuid.uuid4()) for _ in students]
            clients = 50
            barrier = threading.Barrier(clients)
            waiters, errors, lock = [], [], threading.Lock()

            def client(k: int) -> None:
                try:
                    barrier.wait(timeout=30)
                except threading.BrokenBarrierError:
                    pass
                for i in range(k, n, clients):
                    try:
                        r = self.system.intake.submit(students[i], course, request_ids[i], wait=True,
                                                      experiment_id=exp_id, work_ms=0)
                        with lock:
                            waiters.append(r.waiter)
                    except Exception as exc:
                        with lock:
                            errors.append(str(exc))

            t0 = time.perf_counter()
            threads = [threading.Thread(target=client, args=(k,)) for k in range(clients)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            done, not_done = wait_futures([w for w in waiters if w is not None], timeout=180)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            stats = self.system.scheduler.snapshot()
        rep.step(f"Fired {n} requests from {clients} client threads released by one barrier; all finished in "
                 f"{elapsed_ms:.0f} ms", errors=errors[:3])

        with self.engine.connect() as conn:
            state = queries.course_registrations(conn, course)
            req = conn.execute(
                text("SELECT COUNT(*) AS n, COUNT(DISTINCT request_id) AS ids, SUM(status = 'COMPLETED') AS done, "
                     "SUM(result = 'CONFIRMED') AS conf, SUM(result = 'WAITLISTED') AS wait "
                     "FROM registration_requests WHERE experiment_id = :e"), {"e": exp_id}).mappings().first()
            dup = conn.execute(text("SELECT COUNT(*) - COUNT(DISTINCT student_id) FROM registrations "
                                    "WHERE course_id = :c"), {"c": course}).scalar_one()
            commits = conn.execute(
                text("SELECT request_id, metadata, created_at FROM registration_events "
                     "WHERE experiment_id = :e AND event_type = 'TRANSACTION_COMMITTED'"), {"e": exp_id}).mappings().all()
            waitlisted_by_commit = [r[0] for r in conn.execute(
                text("SELECT student_id FROM registration_requests WHERE experiment_id = :e AND result = 'WAITLISTED' "
                     "ORDER BY completed_at, request_id"), {"e": exp_id}).all()]

        # Critical sections [lock acquired, commit] must never overlap for one course.
        sections = sorted((_parse_meta(c["metadata"]).get("lock_acquired_at"), iso(c["created_at"])) for c in commits)
        overlaps = sum(1 for a, b in zip(sections, sections[1:]) if b[0] < a[1])
        lock_waits = [_parse_meta(c["metadata"]).get("lock_wait_ms", 0) for c in commits]
        course_info = state["course"]
        waitlist = state["waitlist"]
        seqs = [w["waitlist_seq"] for w in waitlist]

        rep.check("Distinct requests persisted", n, int(req["ids"]))
        rep.check("Requests completed (none lost, none failed)", n, int(req["done"] or 0))
        rep.check("CONFIRMED registrations (rows in MySQL)", capacity, len(state["confirmed"]))
        rep.check("WAITLISTED registrations", n - capacity, len(waitlist))
        rep.check("courses.confirmed_count (authoritative counter)", capacity, course_info["confirmed_count"])
        rep.check("Duplicate (student, course) rows", 0, int(dup))
        rep.check("Waitlist positions are 1..N with strictly increasing waitlist_seq", True,
                  [w["position"] for w in waitlist] == list(range(1, len(waitlist) + 1)) and seqs == sorted(set(seqs)))
        rep.check("Waitlist order = order in which the waitlisting transactions committed", True,
                  [w["student_id"] for w in waitlist] == waitlisted_by_commit)
        rep.check("Overlapping critical sections on the course row lock", 0, overlaps)
        if mode == "GLOBAL_FIFO":
            rep.check(f"Workers simultaneously holding a request for the course (all {workers} raced for one row)",
                      workers, stats["peak_in_flight_by_course"].get(course))
        rep.data = {
            "course_id": course, "capacity": capacity, "scheduler_mode": mode, "workers": workers,
            "counts": {"confirmed_rows": len(state["confirmed"]), "waitlisted_rows": len(waitlist),
                       "confirmed_count": course_info["confirmed_count"], "results_confirmed": int(req["conf"] or 0),
                       "results_waitlisted": int(req["wait"] or 0)},
            "confirmed": [{"student_id": c["student_id"], "full_name": c["full_name"]} for c in state["confirmed"]],
            "waitlist_head": waitlist[:10], "waitlist_tail": waitlist[-5:],
            "elapsed_ms": round(elapsed_ms, 1), "throughput_rps": round(n / (elapsed_ms / 1000), 1),
            "lock_wait_ms": {"avg": round(statistics.fmean(lock_waits), 2) if lock_waits else None,
                             "max": round(max(lock_waits), 2) if lock_waits else None},
            "peak_workers_on_course": stats["peak_in_flight_by_course"].get(course),
            "experiment_id": exp_id,
        }
        return rep.result()

    # ================================================================== C2
    def c2(self, params: dict) -> dict:
        rep = Report("C2")
        course, capacity = "CH2-A", 3
        s = [f"S{i:04d}" for i in range(1, 6)]
        self._prepare([(course, "Challenge C2 course", capacity)])
        rep.step(f"Prepared {course} with capacity {capacity}; arrivals S1..S5 in that order")

        rid1 = str(uuid.uuid4())
        self.system.injector.arm(FailureKind.LOST_RESPONSE, request_id=rid1)
        first = self._submit_wait(s[0], course, rid1)
        lost = self.system.response_lost(rid1, first)
        rep.step(f"S1 submitted (request {rid1[:8]}); committed as {first['outcome']} but the reply was "
                 f"{'DROPPED' if lost else 'delivered'}")
        rep.check("S1's reply was lost after the commit", True, lost)
        rep.check("S1 is nevertheless committed in MySQL", "CONFIRMED", self._outcome(rid1)["outcome"])

        arrivals = {}
        for sid in s[1:]:
            o = self._submit_wait(sid, course)
            arrivals[_label(sid)] = {"request_id": o["request_id"], "outcome": o["outcome"],
                                     "waitlist_position": o["waitlist_position"]}
            rep.step(f"{_label(sid)} -> {o['outcome']}" + (f" (waitlist #{o['waitlist_position']})"
                                                           if o["waitlist_position"] else ""))
        rep.check("S2, S3 confirmed; S4, S5 waitlisted at positions 1, 2",
                  {"S2": "CONFIRMED", "S3": "CONFIRMED", "S4": "WAITLISTED #1", "S5": "WAITLISTED #2"},
                  {k: v["outcome"] + (f" #{v['waitlist_position']}" if v["waitlist_position"] else "")
                   for k, v in arrivals.items()})

        retry = self._submit_wait(s[0], course, rid1)
        rep.step(f"Client retried S1 with the SAME request ID -> {retry['outcome']} "
                 f"({'stored result replayed' if retry['replayed'] else 're-executed!'})")
        rep.check("Retry with the same UUID returns the stored result", "CONFIRMED", retry["outcome"])
        rep.check("Retry was an idempotent replay (not re-executed)", True, retry["replayed"])
        rep.check("Transactions committed for S1's UUID", 1,
                  sum(e["event_type"] == "TRANSACTION_COMMITTED" for e in self._events(rid1)))
        new_id = self._submit_wait(s[0], course)
        rep.step(f"Retry of S1 with a NEW request ID -> {new_id['outcome']} (UNIQUE student+course backstop)")
        rep.check("Retry with a new UUID cannot register S1 twice", "ALREADY_REGISTERED", new_id["outcome"])
        before = self._course(course)
        stored_waitlist = [_label(w["student_id"]) for w in before["waitlist"]]
        rep.check("Registration rows before cancelling (3 confirmed + 2 waitlisted)", 5,
                  len(before["confirmed"]) + len(before["waitlist"]))

        promotions, counts = [], []
        for sid in s[:3]:
            r = cancel_registration(self.engine, sid, course)
            promoted = _label(r["promoted"]["student_id"]) if r["promoted"] else None
            promotions.append(promoted)
            counts.append(r["confirmed_count"])
            rep.step(f"Cancel {_label(sid)} -> " + (f"{promoted} promoted in the same transaction"
                                                    if promoted else "waitlist empty, nobody to promote")
                     + f"; confirmed_count = {r['confirmed_count']}")
        after = self._course(course)
        rep.check("Stored FIFO waitlist before cancelling", ["S4", "S5"], stored_waitlist)
        rep.check("Promotions follow stored FIFO order (cancel S1, S2, S3)", ["S4", "S5", None], promotions)
        rep.check("confirmed_count after each cancellation", [3, 3, 2], counts)
        rep.check("Final confirmed students", ["S4", "S5"], sorted(_label(c["student_id"]) for c in after["confirmed"]))
        rep.check("Final waitlist", [], [w["student_id"] for w in after["waitlist"]])
        rep.check("confirmed_count equals CONFIRMED rows", after["course"]["confirmed_actual"],
                  after["course"]["confirmed_count"])
        rep.data = {"course_id": course, "s1_request_id": rid1, "arrivals": arrivals,
                    "stored_waitlist_before": stored_waitlist, "promotions": promotions,
                    "final_confirmed": [_label(c["student_id"]) for c in after["confirmed"]],
                    "s1_events": [e["event_type"] for e in self._events(rid1)]}
        return rep.result()

    # ================================================================== C3
    def c3(self, params: dict) -> dict:
        rep = Report("C3")
        workers = self.system.settings.worker_count
        a_service, b_service, a_count = 10, 1, 100
        arrivals = [("A", a_service)] * a_count + [("B", b_service)]
        sims = {m: simulate(m, arrivals, workers=workers, per_course_limit=1, capacities={"A": 1, "B": 1})
                for m in ("GLOBAL_FIFO", "FAIR_QUEUE")}
        for m, r in sims.items():
            r.pop("requests")
        fifo, fair = sims["GLOBAL_FIFO"], sims["FAIR_QUEUE"]
        rep.step(f"Logical-tick model ({workers} workers, real RequestScheduler code): {a_count} A requests "
                 f"({a_service} ticks each) arrive, then 1 B request ({b_service} tick)")
        rep.check("[model] Seat outcome is identical in both modes (A: 1 confirmed / 99 waitlisted; B: 1 confirmed)",
                  True, all(r["per_course"]["A"]["confirmed"] == 1 and r["per_course"]["A"]["waitlisted"] == 99
                            and r["per_course"]["B"]["confirmed"] == 1 for r in sims.values()))
        rep.check("[model] FairQueue keeps at most one request per course in progress", {"A": 1, "B": 1},
                  fair["active_slots"]["peak_in_flight_by_course"])
        rep.check("[model] B's wait (ticks) Global FIFO vs FairQueue", "FairQueue lower",
                  f"{fifo['per_course']['B']['avg_wait']:g} vs {fair['per_course']['B']['avg_wait']:g}",
                  passed=fair["per_course"]["B"]["avg_wait"] < fifo["per_course"]["B"]["avg_wait"])
        rep.check("[model] No universal speedup: A's average turnaround is the same in both modes (ticks)",
                  fifo["per_course"]["A"]["avg_turnaround"], fair["per_course"]["A"]["avg_turnaround"])
        rep.check("[model] No universal speedup: total completion time is the same (ticks)",
                  fifo["makespan_ticks"], fair["makespan_ticks"])

        real = None
        if params.get("include_real_run", True):
            real = self._c3_real(rep, int(params.get("tick_ms", 3)), a_count, a_service, b_service)
        rep.data = {"model": sims, "real": real, "workers": workers,
                    "note": "FairQueue does not make the hot course faster: A is limited by its single row lock in "
                            "both modes. It stops A's backlog from occupying every worker, so B is not stuck behind "
                            "A (head-of-line blocking)."}
        return rep.result()

    def _c3_real(self, rep: Report, tick_ms: int, a_count: int, a_service: int, b_service: int) -> dict:
        system, scheduler = self.system, self.system.scheduler
        a_students = [f"S{i:04d}" for i in range(1, a_count + 1)]
        b_student = f"S{a_count + 1:04d}"
        results = {}
        for mode in ("GLOBAL_FIFO", "FAIR_QUEUE"):
            self._prepare([("CH3-A", "Challenge C3 hot course A", 1), ("CH3-B", "Challenge C3 course B", 1)])
            exp_id = self._experiment("C3", {"scheduler_mode": mode, "tick_ms": tick_ms})
            with self._mode(mode):
                scheduler.pause()
                try:
                    scheduler.reset_stats()
                    plan = [(sid, "CH3-A", a_service * tick_ms) for sid in a_students] + \
                           [(b_student, "CH3-B", b_service * tick_ms)]
                    persisted = [system.intake.submit(sid, cid, str(uuid.uuid4()), experiment_id=exp_id,
                                                      work_ms=work, enqueue=False) for sid, cid, work in plan]
                    waiters = [system.completions.add_waiter(p.request_id) for p in persisted]
                    for p in persisted:
                        scheduler.submit(p.job, force=True)
                    samples, stop = [], threading.Event()

                    def sampler():
                        while not stop.is_set():
                            snap = scheduler.snapshot()
                            samples.append((snap["in_flight_total"], len(snap["in_flight_by_course"])))
                            time.sleep(0.001)
                    sampler_thread = threading.Thread(target=sampler, daemon=True)
                    released = utcnow()
                finally:
                    scheduler.resume()
                sampler_thread.start()
                wait_futures(waiters, timeout=300)
                stop.set()
                sampler_thread.join()
                stats = scheduler.snapshot()
            per_course = {}
            for course in ("CH3-A", "CH3-B"):
                rows = [p for p in persisted if p.job.course_id == course]
                waits, turns, results_ = [], [], []
                for p in rows:
                    o = self._outcome(p.request_id)
                    waits.append(ms_between(released, _from_iso(o["started_at"])))
                    turns.append(ms_between(released, _from_iso(o["completed_at"])))
                    results_.append(o["outcome"])
                per_course[course] = {
                    "requests": len(rows), "avg_dispatch_wait_ms": round(statistics.fmean(waits), 1),
                    "max_dispatch_wait_ms": round(max(waits), 1), "avg_turnaround_ms": round(statistics.fmean(turns), 1),
                    "max_turnaround_ms": round(max(turns), 1), "confirmed": results_.count("CONFIRMED"),
                    "waitlisted": results_.count("WAITLISTED"),
                }
            busy = [s[0] for s in samples] or [0]
            results[mode] = {"per_course": per_course,
                             "active_slots": {"peak_busy_workers": max(busy),
                                              "avg_busy_workers": round(statistics.fmean(busy), 2),
                                              "peak_in_flight_by_course": stats["peak_in_flight_by_course"],
                                              "samples": len(samples)},
                             "makespan_ms": round(max(pc["max_turnaround_ms"] for pc in per_course.values()), 1)}
            rep.step(f"[MySQL] {mode}: B waited {per_course['CH3-B']['avg_dispatch_wait_ms']:.0f} ms for a worker; "
                     f"peak {max(busy)} busy workers; A finished after {per_course['CH3-A']['max_turnaround_ms']:.0f} ms")
        fifo, fair = results["GLOBAL_FIFO"], results["FAIR_QUEUE"]
        rep.check("[MySQL] Seats correct in both modes (A 1 confirmed / 99 waitlisted, B 1 confirmed)", True,
                  all(r["per_course"]["CH3-A"]["confirmed"] == 1 and r["per_course"]["CH3-A"]["waitlisted"] == 99
                      and r["per_course"]["CH3-B"]["confirmed"] == 1 for r in results.values()))
        rep.check("[MySQL] FairQueue: at most one request per course in progress", True,
                  all(v <= 1 for v in fair["active_slots"]["peak_in_flight_by_course"].values()))
        rep.check("[MySQL] B's dispatch wait, Global FIFO vs FairQueue (ms)", "FairQueue lower",
                  f"{fifo['per_course']['CH3-B']['avg_dispatch_wait_ms']:.0f} vs "
                  f"{fair['per_course']['CH3-B']['avg_dispatch_wait_ms']:.0f}",
                  passed=fair["per_course"]["CH3-B"]["avg_dispatch_wait_ms"]
                  < fifo["per_course"]["CH3-B"]["avg_dispatch_wait_ms"])
        return {"tick_ms": tick_ms, "modes": results,
                "observation": f"Hot course A total time: {fifo['makespan_ms']:.0f} ms (FIFO) vs "
                               f"{fair['makespan_ms']:.0f} ms (FairQueue) - not a speedup claim; timings vary per run."}

    # ================================================================== C4
    def c4(self, params: dict) -> dict:
        rep = Report("C4")
        system = self.system
        scenarios = [
            ("AFTER_DEQUEUE", 1, "fails right after dequeue (slot marked in progress), before BEGIN"),
            ("BEFORE_COMMIT", 1, "fails after its writes, before COMMIT"),
            ("WORKER_CRASH", 1, "worker THREAD dies while holding the row lock and the slot"),
            ("WORKER_CRASH", 3, "worker thread dies on every attempt (retry limit 3)"),
        ]
        results = []
        with self._mode("FAIR_QUEUE"):
            for index, (kind, failures, description) in enumerate(scenarios, start=1):
                course = f"CH4-{index}"
                self._prepare([(course, f"Challenge C4 scenario {index}", 1)])
                restarts_before = system.pool.restarts
                r1_id, r2_id = str(uuid.uuid4()), str(uuid.uuid4())
                system.scheduler.pause()
                try:
                    r1 = system.intake.submit("S0001", course, r1_id, wait=True)
                    system.injector.arm(FailureKind(kind), request_id=r1_id, count=failures)
                    r2 = system.intake.submit("S0002", course, r2_id, wait=True)
                finally:
                    system.scheduler.resume()
                done, not_done = wait_futures([r1.waiter, r2.waiter], timeout=30)
                deadline = time.monotonic() + 5
                while system.pool.live_workers() < system.pool.size and time.monotonic() < deadline:
                    time.sleep(0.05)
                o1, o2 = self._outcome(r1_id), self._outcome(r2_id)
                with self.engine.connect() as conn:
                    started = conn.execute(
                        text("SELECT request_id, attempt FROM registration_events WHERE course_id = :c "
                             "AND event_type = 'WORKER_STARTED' ORDER BY event_id"), {"c": course}).all()
                    rollbacks = conn.execute(
                        text("SELECT COUNT(*) FROM registration_events WHERE request_id = :r "
                             "AND event_type = 'TRANSACTION_ROLLED_BACK'"), {"r": r1_id}).scalar_one()
                state = self._course(course)["course"]
                snap = system.scheduler.snapshot()
                order = [("r1" if rid == r1_id else "r2") + f"#{att}" for rid, att in started]
                max_attempts = system.settings.max_attempts
                exhaust = failures >= max_attempts
                r1_attempts = max_attempts if exhaust else failures + 1
                expected_order = [f"r1#{i}" for i in range(1, r1_attempts + 1)] + ["r2#1"]
                sc = {"scenario": index, "kind": kind, "failures": failures, "description": description,
                      "course_id": course, "r1": {"request_id": r1_id, "outcome": o1["outcome"],
                                                  "attempts": o1["attempts"]},
                      "r2": {"request_id": r2_id, "outcome": o2["outcome"]}, "dispatch_order": order,
                      "rollbacks": int(rollbacks), "slot_after": snap["in_flight_by_course"].get(course, 0),
                      "queued_after": snap["queued_by_course"].get(course, 0),
                      "worker_restarts": system.pool.restarts - restarts_before, "live_workers": system.pool.live_workers()}
                results.append(sc)
                tag = f"[{index}: {kind} x{failures}]"
                rep.step(f"{tag} r1 {description}: dispatch order {' -> '.join(order)}; r1 {o1['outcome']} after "
                         f"{o1['attempts']} attempt(s); r2 {o2['outcome']}")
                rep.check(f"{tag} Both requests finished (no liveness failure)", 0, len(not_done))
                rep.check(f"{tag} r1 final result", "FAILED" if exhaust else "CONFIRMED", o1["outcome"])
                rep.check(f"{tag} r2 final result (seat not leaked)", "CONFIRMED" if exhaust else "WAITLISTED",
                          o2["outcome"])
                rep.check(f"{tag} r1 requeued at the front: dispatch order", expected_order, order)
                rep.check(f"{tag} Failures rolled back and logged for r1", min(failures, max_attempts), int(rollbacks))
                rep.check(f"{tag} Scheduler slot released / queue empty for the course", (0, 0),
                          (sc["slot_after"], sc["queued_after"]))
                rep.check(f"{tag} No partial state: confirmed_count = CONFIRMED rows <= capacity", True,
                          state["confirmed_count"] == state["confirmed_actual"] <= state["capacity"])
                if kind == "WORKER_CRASH":
                    rep.check(f"{tag} Crashed worker replaced; pool back to {system.pool.size} live workers",
                              (min(failures, max_attempts), system.pool.size),
                              (sc["worker_restarts"], sc["live_workers"]))
        rep.data = {"scenarios": results, "max_attempts": system.settings.max_attempts,
                    "mechanism": "pool._run: try handler -> on failure requeue at FRONT (rollback already done by "
                                 "MySQL) -> finally scheduler.task_done() releases the course slot and "
                                 "notify_all() wakes idle workers; a dead worker is replaced."}
        return rep.result()

    # ================================================================== C5
    def c5(self, params: dict) -> dict:
        rep = Report("C5")
        course = "CH5-A"
        s1, s2, s3 = "S0001", "S0002", "S0003"
        hold_ms = int(params.get("hold_ms", 400))
        iterations = int(params.get("iterations", 20))

        def setup():
            self._prepare([(course, "Challenge C5 course", 1)])
            a = self._submit_wait(s1, course)
            b = self._submit_wait(s2, course)
            return a["outcome"], b["outcome"]

        def final_state():
            st = self._course(course)
            return {"confirmed": [_label(c["student_id"]) for c in st["confirmed"]],
                    "waitlist": [_label(w["student_id"]) for w in st["waitlist"]],
                    "confirmed_count": st["course"]["confirmed_count"]}

        expected = {"confirmed": ["S2"], "waitlist": ["S3"], "confirmed_count": 1}
        scenarios = {}

        # A: the cancellation holds the course lock; S3 arrives inside the window
        rep.check("[setup] S1 confirmed, S2 waitlisted", ("CONFIRMED", "WAITLISTED"), setup())
        cancel_out: dict = {}
        t = threading.Thread(target=lambda: cancel_out.update(
            cancel_registration(self.engine, s1, course, hold_ms=hold_ms)))
        t.start()
        time.sleep(min(0.1, hold_ms / 4000))
        s3_out = self._submit_wait(s3, course)
        t.join()
        s3_meta = self._commit_meta(s3_out["request_id"])
        fs = final_state()
        scenarios["cancel_first"] = {"cancel": cancel_out, "s3": {"outcome": s3_out["outcome"], **s3_meta},
                                     "final": fs}
        promoted_a = (cancel_out.get("promoted") or {}).get("student_id")
        rep.step(f"[A] Cancel S1 took the course lock and held it {hold_ms} ms; S3 was dispatched at "
                 f"{s3_meta.get('dispatched_at')} and waited {s3_meta.get('lock_wait_ms')} ms on the row lock; "
                 f"the cancellation promoted {_label(promoted_a) if promoted_a else 'nobody'}")
        rep.check("[A] S3 arrived while the cancellation was in progress (dispatched before its commit)", True,
                  bool(s3_meta.get("dispatched_at")) and s3_meta["dispatched_at"] < cancel_out.get("committed_at", ""))
        rep.check("[A] S3 was blocked by the row lock until the cancel+promote committed", True,
                  (s3_meta.get("lock_wait_ms") or 0) >= hold_ms * 0.4)
        rep.check("[A] Final state: S2 promoted, S3 waitlisted behind", expected, fs)

        # B: S3's registration holds the course lock; the cancellation arrives during it
        rep.check("[setup] S1 confirmed, S2 waitlisted (again)", ("CONFIRMED", "WAITLISTED"), setup())
        reg = self.system.intake.submit(s3, course, str(uuid.uuid4()), wait=True, work_ms=hold_ms)
        time.sleep(min(0.1, hold_ms / 4000))
        cancel_b = cancel_registration(self.engine, s1, course)
        reg.waiter.result(timeout=30)
        s3_b = self._outcome(reg.request_id)
        s3_b_meta = self._commit_meta(reg.request_id)
        fs_b = final_state()
        scenarios["register_first"] = {"cancel": cancel_b, "s3": {"outcome": s3_b["outcome"], **s3_b_meta},
                                       "final": fs_b}
        rep.step(f"[B] S3's transaction held the lock {hold_ms} ms (saw the course full -> WAITLISTED behind S2); "
                 f"the cancellation waited, then promoted "
                 f"{_label(cancel_b['promoted']['student_id']) if cancel_b['promoted'] else 'nobody'}")
        rep.check("[B] Cancellation had to wait for S3's transaction (lock acquired after S3 committed)", True,
                  cancel_b["lock_acquired_at"] >= s3_b_meta.get("committed_at", ""))
        rep.check("[B] Final state: S2 promoted, S3 waitlisted behind", expected, fs_b)

        # C: free-running races released by a barrier, repeated
        violations = []
        for i in range(iterations):
            setup()
            barrier = threading.Barrier(2)
            box: dict = {}

            def do_cancel():
                barrier.wait()
                box["cancel"] = cancel_registration(self.engine, s1, course)

            def do_register():
                barrier.wait()
                box["s3"] = self._submit_wait(s3, course)

            threads = [threading.Thread(target=do_cancel), threading.Thread(target=do_register)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            fs_i = final_state()
            if fs_i != expected:
                violations.append({"iteration": i + 1, "state": fs_i})
        rep.step(f"[C] {iterations} free-running races (cancel and S3 released together by a barrier): "
                 f"{iterations - len(violations)} ended with S2 confirmed and S3 waitlisted")
        rep.check(f"[C] Races where the new arrival jumped the stored waitlist", 0, len(violations))
        rep.data = {"course_id": course, "hold_ms": hold_ms, "iterations": iterations, "scenarios": scenarios,
                    "violations": violations,
                    "mechanism": "Cancellation deletes S1 and promotes the waitlist head inside ONE transaction "
                                 "that holds the course row lock (SELECT ... FOR UPDATE). S3's transaction must take "
                                 "the same lock, so it sees either 'S1 confirmed' (full) or 'S2 confirmed' (full) - "
                                 "never a free seat. As a second guard, a newcomer that finds a free seat while "
                                 "someone is waitlisted promotes the waitlist head first."}
        return rep.result()


def _from_iso(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value.rstrip("Z"))
