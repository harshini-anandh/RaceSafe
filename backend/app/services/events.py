"""Event tracker: writes rows to registration_events (the recovery timeline).

write_event(conn, ...)  writes INSIDE the caller's transaction, so the event
                        commits or rolls back together with the real change
                        (used for TRANSACTION_COMMITTED, REQUEST_CONFIRMED...).
EventLogger.log(...)    writes in its own short transaction. Used for events
                        that must survive a rollback (WORKER_FAILURE,
                        TRANSACTION_ROLLED_BACK, RETRY_SCHEDULED...).
"""
from __future__ import annotations

import json
import logging
from enum import Enum

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.core.clock import utcnow

logger = logging.getLogger("racesafe.events")

INSERT_EVENT_SQL = text(
    """
    INSERT INTO registration_events
        (request_id, student_id, course_id, event_type, attempt, worker_name,
         message, metadata, experiment_id, created_at)
    VALUES
        (:request_id, :student_id, :course_id, :event_type, :attempt, :worker_name,
         :message, :metadata, :experiment_id, :created_at)
    """
)


def event_params(event_type, *, request_id=None, student_id=None, course_id=None, attempt=None,
                 worker_name=None, message="", metadata=None, experiment_id=None, created_at=None) -> dict:
    return {
        "request_id": request_id,
        "student_id": student_id,
        "course_id": course_id,
        "event_type": event_type.value if isinstance(event_type, Enum) else str(event_type),
        "attempt": attempt,
        "worker_name": worker_name,
        "message": (message or "")[:500],
        "metadata": json.dumps(metadata, default=str) if metadata else None,
        "experiment_id": experiment_id,
        "created_at": created_at or utcnow(),
    }


def write_event(conn: Connection, event_type, **fields) -> None:
    conn.execute(INSERT_EVENT_SQL, event_params(event_type, **fields))


def write_events(conn: Connection, rows: list[dict]) -> None:
    """Bulk insert of already-built event_params() dictionaries."""
    if rows:
        conn.execute(INSERT_EVENT_SQL, rows)


class EventLogger:
    def __init__(self, engine: Engine):
        self.engine = engine

    def log(self, event_type, **fields) -> None:
        try:
            with self.engine.begin() as conn:
                write_event(conn, event_type, **fields)
        except Exception:  # the audit log must never crash a worker
            logger.exception("Could not write %s event for request %s", event_type, fields.get("request_id"))
