"""RaceSafeSystem: builds and owns every long-lived component.

    engine (MySQL pool) -> events, intake, processor
    scheduler (FIFO / FairQueue) <- intake ; -> worker pool -> processor

Restart recovery: the in-memory queues are only a cache of the
registration_requests table. On startup every request still marked RECEIVED
is put back on its course queue. The worker re-checks the request row under a
lock, so a request that had in fact committed is never applied twice.
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import text

from app.core.clock import utcnow
from app.core.config import Settings
from app.database.connection import create_db_engine
from app.database.setup import apply_upgrades
from app.models.enums import EventType
from app.scheduler.fair_queue import Job, RequestScheduler
from app.services.completions import CompletionRegistry
from app.services.events import EventLogger, event_params, write_event, write_events
from app.services.experiments import ExperimentManager
from app.services.intake import IntakeService
from app.services.race_lab import RaceLab
from app.services.challenges import ChallengeRunner
from app.services.registration import RegistrationProcessor
from app.workers.failure_injection import FailureInjector, FailureKind
from app.workers.pool import WorkerPool

logger = logging.getLogger("racesafe.system")


class RaceSafeSystem:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = create_db_engine(settings)
        self.events = EventLogger(self.engine)
        self.injector = FailureInjector(enabled=settings.demo_mode)
        self.scheduler = RequestScheduler(
            mode=settings.scheduler_mode,
            per_course_limit=settings.per_course_in_flight,
            max_size=settings.max_queue_size,
        )
        self.completions = CompletionRegistry()
        self.processor = RegistrationProcessor(self)
        self.pool = WorkerPool(self.scheduler, self.processor.process, size=settings.worker_count,
                               on_unhandled=self.processor.recover_unhandled)
        self.intake = IntakeService(self)
        self.experiment_lock = threading.Lock()  # one experiment at a time (not a registration lock)
        self.experiments = ExperimentManager(self)
        self.race_lab = RaceLab(self)
        self.challenges = ChallengeRunner(self)
        self.db_ready = False
        self.startup_error: str | None = None
        self.recovered_on_startup = 0
        self._stop = threading.Event()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.pool.start()
        try:
            self._startup_db()
        except Exception as exc:
            self.startup_error = f"{type(exc).__name__}: {exc}"
            logger.error("MySQL not reachable at startup (%s). Retrying every 5 s.", self.startup_error)
            threading.Thread(target=self._retry_startup, name="db-startup-retry", daemon=True).start()

    def _startup_db(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM registration_requests LIMIT 1"))
        applied = apply_upgrades(self.settings)
        if applied:
            logger.info("Applied schema upgrades: %s", ", ".join(applied))
        self.recovered_on_startup = self.recover_pending(reason="Backend (re)started")
        self.db_ready = True
        self.startup_error = None
        if self.recovered_on_startup:
            logger.info("Restart recovery requeued %d pending requests", self.recovered_on_startup)

    def _retry_startup(self) -> None:
        while not self._stop.wait(5):
            try:
                self._startup_db()
                logger.info("Connected to MySQL")
                return
            except Exception as exc:
                self.startup_error = f"{type(exc).__name__}: {exc}"

    def stop(self) -> None:
        self._stop.set()
        self.pool.stop()
        self.race_lab.dispose()
        self.engine.dispose()

    # ------------------------------------------------------------------ recovery
    def recover_pending(self, reason: str) -> int:
        """Requeue every persisted RECEIVED request that is not already queued."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT request_id, student_id, course_id, attempts, experiment_id, queued_at "
                    "FROM registration_requests WHERE status = 'RECEIVED' "
                    "ORDER BY received_at, request_id"
                )
            ).mappings().all()
        requeued, events, failed = 0, [], []
        for row in rows:
            if self.scheduler.is_tracked(row["request_id"]):
                continue
            if row["attempts"] >= self.settings.max_attempts:
                failed.append(row)
                continue
            job = Job(request_id=row["request_id"], student_id=row["student_id"], course_id=row["course_id"],
                      attempt=row["attempts"] + 1, experiment_id=row["experiment_id"],
                      work_ms=self.settings.simulated_work_ms)
            if self.scheduler.submit(job, force=True):
                requeued += 1
                events.append(event_params(
                    EventType.RECOVERY_REQUEUED, request_id=job.request_id, student_id=job.student_id,
                    course_id=job.course_id, attempt=job.attempt, experiment_id=job.experiment_id,
                    message=f"{reason}: pending request requeued from MySQL",
                ))
        with self.engine.begin() as conn:
            write_events(conn, events)
            for row in failed:
                conn.execute(
                    text("UPDATE registration_requests SET status = 'FAILED', completed_at = :now, "
                         "last_error = 'Max attempts reached before restart' "
                         "WHERE request_id = :rid AND status = 'RECEIVED'"),
                    {"now": utcnow(), "rid": row["request_id"]},
                )
                write_event(conn, EventType.REQUEST_FAILED, request_id=row["request_id"],
                            student_id=row["student_id"], course_id=row["course_id"], attempt=row["attempts"],
                            message="Max attempts already used before restart")
        for row in failed:
            self.completions.resolve(row["request_id"], "FAILED")
        return requeued

    def requeue_persisted(self, request_id, student_id, course_id, attempts, experiment_id, reason) -> None:
        job = Job(request_id=request_id, student_id=student_id, course_id=course_id,
                  attempt=min(attempts + 1, self.settings.max_attempts), experiment_id=experiment_id,
                  work_ms=self.settings.simulated_work_ms)
        if self.scheduler.submit(job, force=True):
            self.events.log(EventType.RECOVERY_REQUEUED, request_id=request_id, student_id=student_id,
                            course_id=course_id, attempt=job.attempt, experiment_id=experiment_id,
                            message=reason)

    def response_lost(self, request_id: str, outcome: dict) -> bool:
        """Demo: should the reply for this finished request be dropped?

        Used by the HTTP route and by the challenge runner, so both behave the
        same: the result is already committed; only the reply is lost.
        """
        if not self.injector.consume(FailureKind.LOST_RESPONSE, request_id):
            return False
        self.events.log(
            EventType.RESPONSE_LOST, request_id=request_id, student_id=outcome["student_id"],
            course_id=outcome["course_id"], attempt=outcome["attempts"], experiment_id=outcome.get("experiment_id"),
            message=f"Result {outcome['outcome']} was committed, but the reply was dropped (demo)",
        )
        return True

    def simulate_crash_and_restart(self) -> dict:
        """Demo: lose all in-memory queues (as in a crash), then run recovery.

        Requests already running finish normally; a real crash would have
        rolled their transactions back, and recovery would retry them.
        """
        was_paused = self.scheduler.paused
        self.scheduler.pause()
        dropped = self.scheduler.drain()
        self.events.log(EventType.SIMULATED_CRASH,
                        message=f"Simulated crash: {len(dropped)} queued requests lost from memory",
                        metadata={"dropped": [j.request_id for j in dropped][:50]})
        requeued = self.recover_pending(reason="Simulated restart")
        self.scheduler.resume()
        return {"dropped_from_memory": len(dropped), "requeued_from_database": requeued,
                "was_paused": was_paused}
