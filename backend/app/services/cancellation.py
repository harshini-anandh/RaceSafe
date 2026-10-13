"""Cancellation with atomic FIFO waitlist promotion.

One transaction:
    lock course row -> lock the student's registration row
    DELETE the registration
    if it was CONFIRMED:
        confirmed_count - 1
        lock the earliest WAITLISTED row (lowest waitlist_seq)
        promote it to CONFIRMED, confirmed_count + 1
    COMMIT
Because the course row stays locked until COMMIT, no new request can take the
freed seat before the first waitlisted student gets it.
"""
from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.clock import iso, utcnow
from app.core.errors import NotFoundError
from app.database.connection import RETRYABLE_ERRORS, mysql_error_code
from app.models.enums import EventType
from app.services.events import write_event

MAX_CANCEL_ATTEMPTS = 3


def cancel_registration(engine, student_id: str, course_id: str, hold_ms: int = 0) -> dict:
    """hold_ms (tests/challenges only): keep the course lock this long after the
    DELETE, to force a concurrent registration to arrive inside the window."""
    for attempt in range(1, MAX_CANCEL_ATTEMPTS + 1):
        try:
            started_at = utcnow()
            with engine.begin() as conn:
                result = _cancel_txn(conn, student_id, course_id, hold_ms)
            result["started_at"] = iso(started_at)
            result["committed_at"] = iso(utcnow())
            return result
        except DBAPIError as exc:
            if mysql_error_code(exc) in RETRYABLE_ERRORS and attempt < MAX_CANCEL_ATTEMPTS:
                time.sleep(0.02 * attempt)
                continue
            raise
    raise RuntimeError("unreachable")


def _cancel_txn(conn, student_id: str, course_id: str, hold_ms: int = 0) -> dict:
    course = conn.execute(
        text("SELECT course_id, capacity, confirmed_count FROM courses WHERE course_id = :cid FOR UPDATE"),
        {"cid": course_id},
    ).mappings().first()
    if course is None:
        raise NotFoundError(f"Unknown course_id '{course_id}'")
    lock_acquired_at = utcnow()

    reg = conn.execute(
        text(
            "SELECT registration_id, status, waitlist_seq, request_id FROM registrations "
            "WHERE student_id = :sid AND course_id = :cid FOR UPDATE"
        ),
        {"sid": student_id, "cid": course_id},
    ).mappings().first()
    if reg is None:
        raise NotFoundError(f"Student {student_id} has no registration in {course_id}")

    now = utcnow()
    conn.execute(text("DELETE FROM registrations WHERE registration_id = :id"), {"id": reg["registration_id"]})
    write_event(conn, EventType.REGISTRATION_CANCELLED, request_id=reg["request_id"], student_id=student_id,
                course_id=course_id, created_at=now,
                message=f"{student_id} cancelled a {reg['status']} registration",
                metadata={"previous_status": reg["status"], "waitlist_seq": reg["waitlist_seq"]})

    if hold_ms:
        time.sleep(hold_ms / 1000.0)  # the seat is free right now, but the course row is still locked

    promoted = None
    if reg["status"] == "CONFIRMED":
        conn.execute(
            text("UPDATE courses SET confirmed_count = confirmed_count - 1 WHERE course_id = :cid"),
            {"cid": course_id},
        )
        nxt = conn.execute(
            text(
                "SELECT registration_id, student_id, waitlist_seq, request_id FROM registrations "
                "WHERE course_id = :cid AND status = 'WAITLISTED' "
                "ORDER BY waitlist_seq ASC LIMIT 1 FOR UPDATE"
            ),
            {"cid": course_id},
        ).mappings().first()
        if nxt is not None:
            conn.execute(
                text(
                    "UPDATE registrations SET status = 'CONFIRMED', waitlist_seq = NULL, promoted_at = :now "
                    "WHERE registration_id = :id AND status = 'WAITLISTED'"
                ),
                {"now": now, "id": nxt["registration_id"]},
            )
            conn.execute(
                text("UPDATE courses SET confirmed_count = confirmed_count + 1 WHERE course_id = :cid"),
                {"cid": course_id},
            )
            promoted = {"student_id": nxt["student_id"], "waitlist_seq": nxt["waitlist_seq"]}
            write_event(conn, EventType.WAITLIST_PROMOTED, request_id=nxt["request_id"],
                        student_id=nxt["student_id"], course_id=course_id, created_at=now,
                        message=f"{nxt['student_id']} promoted from waitlist (seq {nxt['waitlist_seq']}) "
                                f"after {student_id} cancelled",
                        metadata={"waitlist_seq": nxt["waitlist_seq"], "freed_by": student_id})

    confirmed_now = conn.execute(
        text("SELECT confirmed_count FROM courses WHERE course_id = :cid"), {"cid": course_id}
    ).scalar_one()
    return {
        "student_id": student_id,
        "course_id": course_id,
        "cancelled_status": reg["status"],
        "promoted": promoted,
        "confirmed_count": confirmed_now,
        "capacity": course["capacity"],
        "lock_acquired_at": iso(lock_acquired_at),
    }
