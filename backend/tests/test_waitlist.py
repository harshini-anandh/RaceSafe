"""Cancellation and FIFO waitlist promotion (test 8)."""
from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import text

from app.core.errors import NotFoundError
from app.services import queries
from app.services.cancellation import cancel_registration
from tests.conftest import burst, consistency, course_counts, make_course, outcome, students, submit_and_wait

pytestmark = pytest.mark.mysql


def waitlist(system, course_id):
    with system.engine.connect() as conn:
        return [w["student_id"] for w in queries.course_registrations(conn, course_id)["waitlist"]]


def test_five_cancellations_promote_first_five_waitlisted_in_order(system):
    make_course(system, "T-WAIT", 10)
    for sid in students(20):  # sequential -> known FIFO order
        submit_and_wait(system, sid, "T-WAIT")
    assert waitlist(system, "T-WAIT") == students(10, start=11)

    promoted = []
    for sid in students(5):
        result = cancel_registration(system.engine, sid, "T-WAIT")
        promoted.append(result["promoted"]["student_id"])
        assert result["confirmed_count"] == 10

    assert promoted == students(5, start=11)
    assert waitlist(system, "T-WAIT") == students(5, start=16)
    with system.engine.connect() as conn:
        positions = queries.student_registrations(conn, "S0016")["registrations"]
    assert positions[0]["waitlist_position"] == 1
    course = course_counts(system, "T-WAIT")
    assert course["confirmed_count"] == course["confirmed_actual"] == 10
    assert consistency(system)["all_passed"]


def test_cancelling_a_waitlisted_entry_does_not_promote(system):
    make_course(system, "T-WAIT2", 1)
    for sid in students(3):
        submit_and_wait(system, sid, "T-WAIT2")
    result = cancel_registration(system.engine, "S0002", "T-WAIT2")
    assert result["cancelled_status"] == "WAITLISTED" and result["promoted"] is None
    assert waitlist(system, "T-WAIT2") == ["S0003"]
    with pytest.raises(NotFoundError):
        cancel_registration(system.engine, "S0002", "T-WAIT2")


def test_freed_seat_goes_to_waitlist_not_to_concurrent_newcomer(system):
    """A cancel racing with 30 new requests: the first waitlisted student gets the seat."""
    make_course(system, "T-JUMP", 5)
    for sid in students(8):
        submit_and_wait(system, sid, "T-JUMP")  # S0001-5 confirmed, S0006-8 waitlisted

    barrier = threading.Barrier(2)
    cancel_result = {}

    def cancel():
        barrier.wait()
        cancel_result.update(cancel_registration(system.engine, "S0001", "T-JUMP"))

    t = threading.Thread(target=cancel)
    t.start()
    barrier.wait()
    newcomers = burst(system, [(s, "T-JUMP") for s in students(30, start=101)], clients=30)
    t.join()

    assert cancel_result["promoted"]["student_id"] == "S0006"
    assert all(r["outcome"] == "WAITLISTED" for r in newcomers)
    with system.engine.connect() as conn:
        confirmed = {r[0] for r in conn.execute(
            text("SELECT student_id FROM registrations WHERE course_id = 'T-JUMP' AND status = 'CONFIRMED'")
        ).all()}
    assert confirmed == {"S0002", "S0003", "S0004", "S0005", "S0006"}
    assert consistency(system)["all_passed"]


def test_student_can_register_again_after_cancelling(system):
    make_course(system, "T-AGAIN", 2)
    submit_and_wait(system, "S0001", "T-AGAIN")
    cancel_registration(system.engine, "S0001", "T-AGAIN")
    again = submit_and_wait(system, "S0001", "T-AGAIN", str(uuid.uuid4()))
    assert again["outcome"] == "CONFIRMED"
    assert outcome(system, again["request_id"])["registration_status"] == "CONFIRMED"
