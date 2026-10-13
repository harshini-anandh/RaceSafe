"""Request intake: validation, idempotency, persistence, enqueue.

1. Validate IDs (student and course must exist, request_id must be a UUID).
2. INSERT the request row (status RECEIVED) + REQUEST_RECEIVED/QUEUED events
   in one short transaction. The request_id PRIMARY KEY is the idempotency key.
3. If the request_id already exists -> do NOT run it again; return its saved
   state (or wait for it, if it is still in progress).
4. After COMMIT, hand the job to the scheduler. No DB transaction is held
   open while a request waits in memory.
"""
from __future__ import annotations

import uuid
from concurrent.futures import Future
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.clock import utcnow
from app.core.errors import ConflictError, NotFoundError, QueueFullError, RaceSafeError
from app.database.connection import ER_DUP_ENTRY, mysql_error_code
from app.models.enums import EventType, RequestStatus
from app.scheduler.fair_queue import Job
from app.services.events import event_params, write_event, write_events


def normalize_request_id(request_id: str | None) -> str:
    if request_id is None or str(request_id).strip() == "":
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(str(request_id).strip()))
    except ValueError as exc:
        raise RaceSafeError("request_id must be a UUID, e.g. 3f1c2a9e-4b7d-4c1e-9a55-0d3b8f6e2a10") from exc


@dataclass
class IntakeResult:
    request_id: str
    status: str                 # RECEIVED / COMPLETED / FAILED at the time of the response
    replayed: bool              # True when this request_id had been seen before
    waiter: Future | None = None
    job: Job | None = None


class IntakeService:
    def __init__(self, system):
        self.system = system
        self.engine = system.engine
        self.scheduler = system.scheduler
        self.completions = system.completions
        self.events = system.events

    def submit(self, student_id: str, course_id: str, request_id: str | None = None, *,
               wait: bool = False, experiment_id: int | None = None, work_ms: int | None = None,
               enqueue: bool = True) -> IntakeResult:
        request_id = normalize_request_id(request_id)
        work_ms = self.system.settings.simulated_work_ms if work_ms is None else work_ms

        if enqueue and not self.scheduler.has_room():
            raise QueueFullError("Registration queue is full, please retry shortly (HTTP 503 back-pressure)")

        now = utcnow()
        try:
            with self.engine.begin() as conn:
                self._validate_ids(conn, student_id, course_id)
                conn.execute(
                    text(
                        "INSERT INTO registration_requests "
                        "(request_id, student_id, course_id, status, attempts, experiment_id, received_at, queued_at) "
                        "VALUES (:rid, :sid, :cid, 'RECEIVED', 0, :exp, :now, :now)"
                    ),
                    {"rid": request_id, "sid": student_id, "cid": course_id, "exp": experiment_id, "now": now},
                )
                common = dict(request_id=request_id, student_id=student_id, course_id=course_id,
                              experiment_id=experiment_id, created_at=now)
                write_events(conn, [
                    event_params(EventType.REQUEST_RECEIVED, message="Request validated and persisted", **common),
                    event_params(EventType.QUEUED, message=f"Queued ({self.scheduler.mode})",
                                 metadata={"scheduler_mode": self.scheduler.mode}, **common),
                ])
        except IntegrityError as exc:
            if mysql_error_code(exc) == ER_DUP_ENTRY:
                return self._replay(request_id, student_id, course_id, wait)
            raise

        job = Job(request_id=request_id, student_id=student_id, course_id=course_id,
                  experiment_id=experiment_id, work_ms=work_ms, enqueued_at=now)
        waiter = self.completions.add_waiter(request_id) if wait else None
        if enqueue and not self.scheduler.submit(job):
            if waiter is not None:
                self.completions.discard(request_id, waiter)
            self._reject(job, "Queue full at enqueue time")
            raise QueueFullError("Registration queue is full, please retry shortly (HTTP 503 back-pressure)")
        return IntakeResult(request_id, RequestStatus.RECEIVED.value, replayed=False, waiter=waiter, job=job)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _validate_ids(conn, student_id: str, course_id: str) -> None:
        if conn.execute(text("SELECT 1 FROM students WHERE student_id = :sid"), {"sid": student_id}).first() is None:
            raise NotFoundError(f"Unknown student_id '{student_id}'")
        if conn.execute(text("SELECT 1 FROM courses WHERE course_id = :cid"), {"cid": course_id}).first() is None:
            raise NotFoundError(f"Unknown course_id '{course_id}'")

    def _replay(self, request_id: str, student_id: str, course_id: str, wait: bool) -> IntakeResult:
        """Same request_id seen again: never re-run it, return what we know."""
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT student_id, course_id, status, attempts, experiment_id "
                    "FROM registration_requests WHERE request_id = :rid"
                ),
                {"rid": request_id},
            ).mappings().first()
            if row is None:  # extremely unlikely: deleted by a reset in between
                raise ConflictError("Request id collision, please retry with a new request_id")
            if row["student_id"] != student_id or row["course_id"] != course_id:
                raise ConflictError(
                    f"request_id {request_id} was already used for student {row['student_id']} / "
                    f"course {row['course_id']}; a request_id must not be reused for different data"
                )
            write_event(conn, EventType.DUPLICATE_REQUEST, request_id=request_id, student_id=student_id,
                        course_id=course_id, message=f"Duplicate submission, stored status {row['status']} returned",
                        metadata={"stored_status": row["status"]})

        waiter = None
        status = row["status"]
        if status == RequestStatus.RECEIVED.value and wait:
            waiter = self.completions.add_waiter(request_id)
        if status == RequestStatus.RECEIVED.value and not self.scheduler.is_tracked(request_id):
            # Persisted but not in any in-memory queue (e.g. lost in a crash):
            # requeue it. Safe even if it races with another enqueue, because
            # the worker re-checks the request row under a lock.
            self.system.requeue_persisted(request_id, student_id, course_id, row["attempts"],
                                          row["experiment_id"], reason="Client retry found an orphaned request")
        if waiter is not None:
            # Re-check after registering the waiter, so a commit that happened
            # in between is not missed.
            with self.engine.connect() as conn:
                status = conn.execute(
                    text("SELECT status FROM registration_requests WHERE request_id = :rid"), {"rid": request_id}
                ).scalar_one()
            if status != RequestStatus.RECEIVED.value:
                self.completions.discard(request_id, waiter)
                waiter = None
        return IntakeResult(request_id, status, replayed=True, waiter=waiter)

    def _reject(self, job: Job, reason: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE registration_requests SET status = 'FAILED', last_error = :reason, completed_at = :now "
                    "WHERE request_id = :rid AND status = 'RECEIVED'"
                ),
                {"reason": reason, "now": utcnow(), "rid": job.request_id},
            )
            write_event(conn, EventType.REQUEST_FAILED, request_id=job.request_id, student_id=job.student_id,
                        course_id=job.course_id, experiment_id=job.experiment_id, message=reason)
