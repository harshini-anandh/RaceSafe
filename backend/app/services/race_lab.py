"""Race Condition Lab: unsafe check-then-act vs the real RaceSafe path.

UNSAFE (demo only, isolated tables lab_courses / lab_registrations):
    read  confirmed_count            -- no lock
    check confirmed_count < capacity
    ...race window...                -- other clients read the same old count
    INSERT registration
    UPDATE confirmed_count = <old value> + 1   -- lost update
Each statement auto-commits, there is no row lock and no UNIQUE constraint,
so concurrent clients can all see the "last seat" as free.

To make the race demonstrable on demand, clients start together at a
threading.Barrier, and optionally a second barrier forces every client to
finish its first READ before anyone WRITES (the worst-case interleaving).
Without that option the outcome depends on the real thread schedule and
may differ from run to run.

SAFE: the same students go through the real RaceSafe pipeline (intake ->
scheduler -> worker -> locked MySQL transaction) on course LAB-SAFE.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import wait as wait_futures

from sqlalchemy import text

from app.core.clock import iso, utcnow
from app.core.errors import ConflictError
from app.database.connection import create_lab_engine
from app.services.demo_data import prepare_experiment_courses, student_ids

UNSAFE_COURSE = "LAB-UNSAFE"
SAFE_COURSE = "LAB-SAFE"


def build_attempts(students: list[str], duplicate_ratio: float) -> list[str]:
    """Each student once; the first `duplicate_ratio` share also 'double-click'."""
    n_dup = int(round(len(students) * duplicate_ratio))
    attempts = []
    for i, sid in enumerate(students):
        attempts.append(sid)
        if i < n_dup:
            attempts.append(sid)  # adjacent -> dealt to neighbouring clients -> concurrent
    return attempts


def deal(attempts: list[str], clients: int) -> list[list[str]]:
    buckets = [[] for _ in range(clients)]
    for i, sid in enumerate(attempts):
        buckets[i % clients].append(sid)
    return [b for b in buckets if b]


class RaceLab:
    def __init__(self, system):
        self.system = system
        self._lab_engine = None
        self.last_results: dict[str, dict] = {}

    @property
    def lab_engine(self):
        if self._lab_engine is None:
            self._lab_engine = create_lab_engine(self.system.settings)
        return self._lab_engine

    def dispose(self) -> None:
        if self._lab_engine is not None:
            self._lab_engine.dispose()

    def _exclusive(self):
        if not self.system.experiment_lock.acquire(blocking=False):
            raise ConflictError("Another experiment or race-lab run is in progress")

    # ------------------------------------------------------------------ unsafe
    def run_unsafe(self, cfg: dict) -> dict:
        self._exclusive()
        try:
            return self._run_unsafe(cfg)
        finally:
            self.system.experiment_lock.release()

    def _run_unsafe(self, cfg: dict) -> dict:
        capacity, n = cfg["capacity"], cfg["students"]
        with self.system.engine.connect() as conn:
            students = student_ids(conn, n)
        attempts = build_attempts(students, cfg["duplicate_ratio"])
        clients = min(cfg["client_threads"], len(attempts))
        buckets = deal(attempts, clients)
        window_s = cfg["race_window_ms"] / 1000.0

        engine = self.lab_engine
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM lab_registrations WHERE course_id = :c"), {"c": UNSAFE_COURSE})
            conn.execute(text("DELETE FROM lab_courses WHERE course_id = :c"), {"c": UNSAFE_COURSE})
            conn.execute(text("INSERT INTO lab_courses (course_id, capacity, confirmed_count) VALUES (:c, :cap, 0)"),
                         {"c": UNSAFE_COURSE, "cap": capacity})

        start_barrier = threading.Barrier(len(buckets))
        read_barrier = threading.Barrier(len(buckets)) if cfg["force_interleaving"] else None
        outcomes = {"CONFIRMED": 0, "WAITLISTED": 0, "ALREADY_REGISTERED": 0, "ERROR": 0}
        lock = threading.Lock()  # protects only the local result counters, not the database

        def client(items: list[str]) -> None:
            local = {k: 0 for k in outcomes}
            with engine.connect() as conn:
                try:
                    start_barrier.wait(timeout=30)
                except threading.BrokenBarrierError:
                    pass
                for k, sid in enumerate(items):
                    try:
                        local[self._unsafe_register(conn, sid, window_s, read_barrier if k == 0 else None)] += 1
                    except Exception:
                        local["ERROR"] += 1
            with lock:
                for key, value in local.items():
                    outcomes[key] += value

        started = time.perf_counter()
        threads = [threading.Thread(target=client, args=(b,), name=f"unsafe-client-{i}")
                   for i, b in enumerate(buckets)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed_ms = (time.perf_counter() - started) * 1000

        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT status, COUNT(*) AS n, COUNT(DISTINCT student_id) AS students "
                     "FROM lab_registrations WHERE course_id = :c GROUP BY status"),
                {"c": UNSAFE_COURSE},
            ).mappings().all()
            dup = conn.execute(
                text("SELECT COUNT(*) - COUNT(DISTINCT student_id) FROM lab_registrations WHERE course_id = :c"),
                {"c": UNSAFE_COURSE},
            ).scalar_one()
            counter = conn.execute(
                text("SELECT confirmed_count FROM lab_courses WHERE course_id = :c"), {"c": UNSAFE_COURSE}
            ).scalar_one()
        by_status = {r["status"]: int(r["n"]) for r in rows}
        confirmed = by_status.get("CONFIRMED", 0)
        result = {
            "mode": "UNSAFE",
            "course_id": UNSAFE_COURSE,
            "capacity": capacity,
            "students": n,
            "attempts": len(attempts),
            "client_threads": len(buckets),
            "confirmed": confirmed,
            "waitlisted": by_status.get("WAITLISTED", 0),
            "already_registered": outcomes["ALREADY_REGISTERED"],
            "failed": outcomes["ERROR"],
            "duplicate_registrations": int(dup),
            "overbooked": max(0, confirmed - capacity),
            "stored_confirmed_count": int(counter),
            "lost_updates": max(0, confirmed - int(counter)),
            "elapsed_ms": round(elapsed_ms, 1),
            "config": cfg,
            "finished_at": iso(utcnow()),
        }
        result["correct"] = (result["overbooked"] == 0 and result["duplicate_registrations"] == 0
                             and result["lost_updates"] == 0)
        self._store("RACE_UNSAFE", cfg, result)
        return result

    @staticmethod
    def _unsafe_register(conn, student_id: str, window_s: float, read_barrier) -> str:
        """DELIBERATELY BROKEN check-then-act. Never used by the real API path."""
        row = conn.execute(
            text("SELECT capacity, confirmed_count FROM lab_courses WHERE course_id = :c"), {"c": UNSAFE_COURSE}
        ).mappings().first()
        already = conn.execute(
            text("SELECT COUNT(*) FROM lab_registrations WHERE course_id = :c AND student_id = :s"),
            {"c": UNSAFE_COURSE, "s": student_id},
        ).scalar_one()
        if read_barrier is not None:
            try:
                read_barrier.wait(timeout=30)  # everyone has read; now everyone writes
            except threading.BrokenBarrierError:
                pass
        if already:
            return "ALREADY_REGISTERED"
        if row["confirmed_count"] < row["capacity"]:
            if window_s:
                time.sleep(window_s)  # the race window between check and act
            conn.execute(
                text("INSERT INTO lab_registrations (course_id, student_id, status) VALUES (:c, :s, 'CONFIRMED')"),
                {"c": UNSAFE_COURSE, "s": student_id},
            )
            conn.execute(
                text("UPDATE lab_courses SET confirmed_count = :v WHERE course_id = :c"),
                {"v": row["confirmed_count"] + 1, "c": UNSAFE_COURSE},  # stale value -> lost update
            )
            return "CONFIRMED"
        conn.execute(
            text("INSERT INTO lab_registrations (course_id, student_id, status) VALUES (:c, :s, 'WAITLISTED')"),
            {"c": UNSAFE_COURSE, "s": student_id},
        )
        return "WAITLISTED"

    # ------------------------------------------------------------------ safe
    def run_safe(self, cfg: dict) -> dict:
        self._exclusive()
        try:
            return self._run_safe(cfg)
        finally:
            self.system.experiment_lock.release()

    def _run_safe(self, cfg: dict) -> dict:
        system = self.system
        capacity, n = cfg["capacity"], cfg["students"]
        with system.engine.begin() as conn:
            students = student_ids(conn, n)
            pending = conn.execute(
                text("SELECT COUNT(*) FROM registration_requests WHERE course_id = :c AND status = 'RECEIVED'"),
                {"c": SAFE_COURSE},
            ).scalar_one()
            if pending:
                raise ConflictError("LAB-SAFE still has pending requests from a previous run")
            prepare_experiment_courses(conn, [(SAFE_COURSE, "Race Lab (RaceSafe path)", capacity)])
            exp_id = conn.execute(
                text("INSERT INTO experiments (experiment_type, status, config, created_at) "
                     "VALUES ('RACE_SAFE', 'RUNNING', :cfg, :now)"),
                {"cfg": json.dumps(cfg), "now": utcnow()},
            ).lastrowid

        attempts = build_attempts(students, cfg["duplicate_ratio"])
        clients = min(cfg["client_threads"], len(attempts))
        buckets = deal(attempts, clients)
        start_barrier = threading.Barrier(len(buckets))
        waiters, errors = [], []
        lock = threading.Lock()

        def client(items: list[str]) -> None:
            try:
                start_barrier.wait(timeout=30)
            except threading.BrokenBarrierError:
                pass
            for sid in items:
                try:
                    # every click is a new request_id (worst case for duplicates)
                    r = system.intake.submit(sid, SAFE_COURSE, str(uuid.uuid4()), wait=True,
                                             experiment_id=exp_id, work_ms=0)
                    with lock:
                        if r.waiter is not None:
                            waiters.append(r.waiter)
                except Exception as exc:
                    with lock:
                        errors.append(str(exc))

        started = time.perf_counter()
        threads = [threading.Thread(target=client, args=(b,), name=f"safe-client-{i}") for i, b in enumerate(buckets)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        _, not_done = wait_futures(waiters, timeout=300)
        elapsed_ms = (time.perf_counter() - started) * 1000

        with system.engine.connect() as conn:
            regs = conn.execute(
                text("SELECT status, COUNT(*) AS n FROM registrations WHERE course_id = :c GROUP BY status"),
                {"c": SAFE_COURSE},
            ).mappings().all()
            reqs = conn.execute(
                text("SELECT status, result, COUNT(*) AS n FROM registration_requests "
                     "WHERE experiment_id = :id GROUP BY status, result"),
                {"id": exp_id},
            ).mappings().all()
            dup = conn.execute(
                text("SELECT COUNT(*) - COUNT(DISTINCT student_id) FROM registrations WHERE course_id = :c"),
                {"c": SAFE_COURSE},
            ).scalar_one()
            counter = conn.execute(
                text("SELECT confirmed_count FROM courses WHERE course_id = :c"), {"c": SAFE_COURSE}
            ).scalar_one()
        by_status = {r["status"]: int(r["n"]) for r in regs}
        already = sum(int(r["n"]) for r in reqs if r["result"] == "ALREADY_REGISTERED")
        failed = sum(int(r["n"]) for r in reqs if r["status"] == "FAILED") + len(errors)
        pending = sum(int(r["n"]) for r in reqs if r["status"] == "RECEIVED")
        confirmed = by_status.get("CONFIRMED", 0)
        result = {
            "mode": "SAFE",
            "course_id": SAFE_COURSE,
            "capacity": capacity,
            "students": n,
            "attempts": len(attempts),
            "client_threads": len(buckets),
            "confirmed": confirmed,
            "waitlisted": by_status.get("WAITLISTED", 0),
            "already_registered": already,
            "failed": failed,
            "pending": pending + len(not_done),
            "duplicate_registrations": int(dup),
            "overbooked": max(0, confirmed - capacity),
            "stored_confirmed_count": int(counter),
            "lost_updates": max(0, confirmed - int(counter)),
            "elapsed_ms": round(elapsed_ms, 1),
            "errors": errors[:5],
            "config": cfg,
            "experiment_id": exp_id,
            "finished_at": iso(utcnow()),
        }
        result["correct"] = (result["overbooked"] == 0 and result["duplicate_registrations"] == 0
                             and result["lost_updates"] == 0 and int(counter) == confirmed)
        with system.engine.begin() as conn:
            conn.execute(
                text("UPDATE experiments SET status = 'COMPLETED', summary = :s, finished_at = :t "
                     "WHERE experiment_id = :id"),
                {"s": json.dumps(result), "t": utcnow(), "id": exp_id},
            )
        self.last_results["SAFE"] = result
        return result

    def _store(self, kind: str, cfg: dict, result: dict) -> None:
        self.last_results["UNSAFE" if kind == "RACE_UNSAFE" else "SAFE"] = result
        with self.system.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO experiments (experiment_type, status, config, summary, created_at, finished_at) "
                     "VALUES (:k, 'COMPLETED', :cfg, :s, :now, :now)"),
                {"k": kind, "cfg": json.dumps(cfg), "s": json.dumps(result), "now": utcnow()},
            )

    def history(self, limit: int = 20) -> list[dict]:
        with self.system.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT experiment_id, experiment_type, summary, finished_at FROM experiments "
                     "WHERE experiment_type IN ('RACE_UNSAFE', 'RACE_SAFE') AND status = 'COMPLETED' "
                     "ORDER BY experiment_id DESC LIMIT :n"),
                {"n": limit},
            ).mappings().all()
        out = []
        for r in rows:
            summary = json.loads(r["summary"]) if isinstance(r["summary"], str) else r["summary"]
            out.append({"experiment_id": r["experiment_id"], "type": r["experiment_type"],
                        "finished_at": iso(r["finished_at"]), "result": summary})
        return out
