"""Seat allocation: the critical section, protected by MySQL row locks.

One attempt = one transaction:

    BEGIN
      SELECT ... FROM registration_requests WHERE request_id = ? FOR UPDATE   -- 1. request row
          (already has a result? -> stop, never repeat a committed request)
      SELECT ... FROM courses WHERE course_id = ? FOR UPDATE                  -- 2. course row
      SELECT ... FROM registrations WHERE student_id = ? AND course_id = ?
                 FOR UPDATE                                                   -- 3. registration row
      INSERT registration (CONFIRMED or WAITLISTED) / UPDATE confirmed_count
      UPDATE registration_requests SET status = 'COMPLETED', result = ...
      INSERT events TRANSACTION_COMMITTED + REQUEST_<RESULT>
    COMMIT

The lock order (request -> course -> registration) is the same everywhere,
which prevents circular waits. Two requests for the same course queue up on
the course row lock; requests for different courses do not block each other.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.clock import ms_between, utcnow
from app.database.connection import RETRYABLE_ERRORS, mysql_error_code
from app.models.enums import RESULT_EVENT, EventType, RequestResult, RequestStatus
from app.scheduler.fair_queue import Job
from app.services.events import write_event
from app.workers.failure_injection import InjectedWorkerFailure

logger = logging.getLogger("racesafe.registration")


@dataclass
class AttemptOutcome:
    status: str
    result: str | None
    already_processed: bool = False


class RegistrationProcessor:
    """Runs on worker threads. One call = one attempt for one job."""

    def __init__(self, system):
        self.system = system
        self.engine = system.engine
        self.events = system.events
        self.injector = system.injector
        self.scheduler = system.scheduler
        self.completions = system.completions
        self.max_attempts = system.settings.max_attempts

    # ------------------------------------------------------------------ entry point
    def process(self, job: Job, worker: str) -> None:
        queue_wait_ms = ms_between(job.enqueued_at, job.dispatched_at)
        self.events.log(
            EventType.WORKER_STARTED, request_id=job.request_id, student_id=job.student_id,
            course_id=job.course_id, attempt=job.attempt, worker_name=worker,
            experiment_id=job.experiment_id,
            message=f"{worker} started attempt {job.attempt}",
            metadata={"queue_wait_ms": round(queue_wait_ms or 0, 3), "scheduler_mode": self.scheduler.mode},
        )
        try:
            with self.engine.connect() as conn:
                with conn.begin():  # COMMIT on success, ROLLBACK on any exception
                    outcome = self._allocate_seat(conn, job, worker)
        except InjectedWorkerFailure as exc:
            self._record_failure(job, worker, str(exc), kind="INJECTED")
            return
        except IntegrityError as exc:
            # UNIQUE (student, course) backstop fired. The retry will see the
            # existing row and answer ALREADY_REGISTERED.
            self._record_failure(job, worker, f"Constraint violation: {exc.orig}", kind="CONSTRAINT")
            return
        except DBAPIError as exc:
            code = mysql_error_code(exc)
            kind = "DEADLOCK" if code in RETRYABLE_ERRORS else "DATABASE"
            self._record_failure(job, worker, f"MySQL error {code}: {exc.orig}", kind=kind)
            return
        except Exception as exc:  # unexpected bug: still roll back + retry, never crash the pool
            logger.exception("Unexpected error processing %s", job.request_id)
            self._record_failure(job, worker, f"{type(exc).__name__}: {exc}", kind="UNEXPECTED")
            return

        # Committed (or found already committed). Wake any API call waiting for it.
        self.completions.resolve(job.request_id, outcome.result or outcome.status)

    # ------------------------------------------------------------------ the transaction
    def _allocate_seat(self, conn, job: Job, worker: str) -> AttemptOutcome:
        # 1. lock the request row - makes processing idempotent
        request = conn.execute(
            text(
                "SELECT status, result FROM registration_requests "
                "WHERE request_id = :rid FOR UPDATE"
            ),
            {"rid": job.request_id},
        ).mappings().first()
        if request is None:
            raise RuntimeError(f"Request {job.request_id} is not persisted")
        if request["status"] != RequestStatus.RECEIVED.value:
            # Already finished (e.g. recovered twice). Do not repeat its effect.
            return AttemptOutcome(request["status"], request["result"], already_processed=True)

        # 2. lock the course row - the critical section for this course starts here
        course = conn.execute(
            text("SELECT capacity, confirmed_count FROM courses WHERE course_id = :cid FOR UPDATE"),
            {"cid": job.course_id},
        ).mappings().first()
        if course is None:
            raise RuntimeError(f"Course {job.course_id} does not exist")

        # 3. lock (or find absence of) this student's registration for the course
        existing = conn.execute(
            text(
                "SELECT registration_id, status FROM registrations "
                "WHERE student_id = :sid AND course_id = :cid FOR UPDATE"
            ),
            {"sid": job.student_id, "cid": job.course_id},
        ).mappings().first()

        now = utcnow()
        metadata: dict = {"capacity": course["capacity"], "confirmed_before": course["confirmed_count"]}
        if existing is not None:
            result = RequestResult.ALREADY_REGISTERED.value
            metadata["existing_status"] = existing["status"]
        elif course["confirmed_count"] < course["capacity"]:
            conn.execute(
                text(
                    "INSERT INTO registrations (student_id, course_id, status, request_id, created_at) "
                    "VALUES (:sid, :cid, 'CONFIRMED', :rid, :now)"
                ),
                {"sid": job.student_id, "cid": job.course_id, "rid": job.request_id, "now": now},
            )
            conn.execute(
                text("UPDATE courses SET confirmed_count = confirmed_count + 1 WHERE course_id = :cid"),
                {"cid": job.course_id},
            )
            result = RequestResult.CONFIRMED.value
        else:
            next_seq = conn.execute(
                text("SELECT COALESCE(MAX(waitlist_seq), 0) + 1 FROM registrations WHERE course_id = :cid"),
                {"cid": job.course_id},
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO registrations (student_id, course_id, status, waitlist_seq, request_id, created_at) "
                    "VALUES (:sid, :cid, 'WAITLISTED', :seq, :rid, :now)"
                ),
                {"sid": job.student_id, "cid": job.course_id, "seq": next_seq, "rid": job.request_id, "now": now},
            )
            position = conn.execute(
                text(
                    "SELECT COUNT(*) FROM registrations "
                    "WHERE course_id = :cid AND status = 'WAITLISTED' AND waitlist_seq <= :seq"
                ),
                {"cid": job.course_id, "seq": next_seq},
            ).scalar_one()
            result = RequestResult.WAITLISTED.value
            metadata.update(waitlist_seq=next_seq, waitlist_position=position)

        if job.work_ms > 0:
            time.sleep(job.work_ms / 1000.0)  # simulated work while holding the course lock

        # Demo hook: crash here = after the writes, before COMMIT -> full rollback.
        self.injector.maybe_fail_before_commit(job.request_id, job.attempt)

        completed_at = utcnow()
        conn.execute(
            text(
                "UPDATE registration_requests "
                "SET status = 'COMPLETED', result = :result, attempts = :attempt, last_error = NULL, "
                "    worker_name = :worker, started_at = :started, completed_at = :completed "
                "WHERE request_id = :rid"
            ),
            {"result": result, "attempt": job.attempt, "worker": worker, "started": job.dispatched_at,
             "completed": completed_at, "rid": job.request_id},
        )
        common = dict(request_id=job.request_id, student_id=job.student_id, course_id=job.course_id,
                      attempt=job.attempt, worker_name=worker, experiment_id=job.experiment_id)
        write_event(conn, EventType.TRANSACTION_COMMITTED, message=f"Committed on attempt {job.attempt}",
                    metadata={"processing_ms": round(ms_between(job.dispatched_at, completed_at) or 0, 3)},
                    created_at=completed_at, **common)
        write_event(conn, RESULT_EVENT[result], message=f"Result: {result}", metadata=metadata,
                    created_at=completed_at, **common)
        return AttemptOutcome(RequestStatus.COMPLETED.value, result)

    # ------------------------------------------------------------------ failure path
    def _record_failure(self, job: Job, worker: str, error: str, kind: str) -> None:
        """The transaction has already been rolled back when we get here."""
        common = dict(request_id=job.request_id, student_id=job.student_id, course_id=job.course_id,
                      attempt=job.attempt, worker_name=worker, experiment_id=job.experiment_id)
        self.events.log(EventType.WORKER_FAILURE, message=error, metadata={"kind": kind}, **common)
        self.events.log(EventType.TRANSACTION_ROLLED_BACK,
                        message="Rolled back: no registration, count or result was written", **common)
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE registration_requests SET attempts = :attempt, last_error = :error "
                        "WHERE request_id = :rid AND status = 'RECEIVED'"
                    ),
                    {"attempt": job.attempt, "error": error[:255], "rid": job.request_id},
                )
        except Exception:
            logger.exception("Could not record attempt count for %s", job.request_id)

        if job.attempt >= self.max_attempts:
            self._mark_failed(job, worker, error)
            return

        job.attempt += 1
        backoff_ms = 20 * job.attempt + random.randint(0, 20)
        self.events.log(EventType.RETRY_SCHEDULED,
                        message=f"Retry as attempt {job.attempt} of {self.max_attempts} (front of queue)",
                        metadata={"backoff_ms": backoff_ms}, **{**common, "attempt": job.attempt})
        time.sleep(backoff_ms / 1000.0)
        self.scheduler.submit(job, front=True)

    def _mark_failed(self, job: Job, worker: str, error: str) -> None:
        try:
            with self.engine.begin() as conn:
                updated = conn.execute(
                    text(
                        "UPDATE registration_requests "
                        "SET status = 'FAILED', completed_at = :now, worker_name = :worker, last_error = :error "
                        "WHERE request_id = :rid AND status = 'RECEIVED'"
                    ),
                    {"now": utcnow(), "worker": worker, "error": error[:255], "rid": job.request_id},
                ).rowcount
                if updated:
                    write_event(conn, EventType.REQUEST_FAILED, request_id=job.request_id,
                                student_id=job.student_id, course_id=job.course_id, attempt=job.attempt,
                                worker_name=worker, experiment_id=job.experiment_id,
                                message=f"Gave up after {job.attempt} attempts: {error}")
        except Exception:
            # Still RECEIVED in the DB -> restart recovery will pick it up later.
            logger.exception("Could not mark %s as FAILED", job.request_id)
        self.completions.resolve(job.request_id, RequestStatus.FAILED.value)
