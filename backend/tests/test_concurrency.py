"""Concurrency invariants against real MySQL (tests 1-5 and 10 of the plan)."""
from __future__ import annotations

import time
import uuid
from collections import Counter

import pytest

from tests.conftest import burst, consistency, course_counts, make_course, students, submit_and_wait

pytestmark = pytest.mark.mysql


def test_capacity_10_with_500_distinct_students(system):
    make_course(system, "T-CAP", 10)
    results = burst(system, [(s, "T-CAP") for s in students(500)], clients=60)
    outcomes = Counter(r["outcome"] for r in results)
    assert outcomes == {"CONFIRMED": 10, "WAITLISTED": 490}
    course = course_counts(system, "T-CAP")
    assert course["confirmed_count"] == course["confirmed_actual"] == 10
    assert course["waitlisted"] == 490
    assert consistency(system)["all_passed"]


def test_simultaneous_attempts_for_the_final_seat(system):
    make_course(system, "T-LAST", 1)
    results = burst(system, [(s, "T-LAST") for s in students(50)], clients=50)
    assert Counter(r["outcome"] for r in results) == {"CONFIRMED": 1, "WAITLISTED": 49}
    assert course_counts(system, "T-LAST")["confirmed_actual"] == 1


def test_same_request_id_repeated_concurrently(system):
    make_course(system, "T-IDEM", 5)
    rid = str(uuid.uuid4())
    results = burst(system, [("S0001", "T-IDEM")] * 20, clients=20, request_ids=[rid] * 20)
    assert {r["request_id"] for r in results} == {rid}
    assert {r["outcome"] for r in results} == {"CONFIRMED"}
    course = course_counts(system, "T-IDEM")
    assert course["confirmed_actual"] + course["waitlisted"] == 1

    # Retrying later returns the stored result without re-running it
    again = system.intake.submit("S0001", "T-IDEM", rid, wait=True)
    assert again.replayed and again.status == "COMPLETED" and again.waiter is None


def test_reusing_request_id_for_other_data_is_rejected(system):
    from app.core.errors import ConflictError

    make_course(system, "T-IDEM2", 5)
    rid = str(uuid.uuid4())
    submit_and_wait(system, "S0001", "T-IDEM2", rid)
    with pytest.raises(ConflictError):
        system.intake.submit("S0002", "T-IDEM2", rid)


def test_duplicate_registration_with_different_request_ids(system):
    make_course(system, "T-DUP", 5)
    results = burst(system, [("S0002", "T-DUP")] * 20, clients=20)
    assert Counter(r["outcome"] for r in results) == {"CONFIRMED": 1, "ALREADY_REGISTERED": 19}
    assert course_counts(system, "T-DUP")["confirmed_actual"] == 1
    assert consistency(system)["all_passed"]


def test_different_courses_progress_concurrently(system):
    """With FairQueue, transactions of different courses overlap in time."""
    courses = ["T-M1", "T-M2", "T-M3", "T-M4"]
    for c in courses:
        make_course(system, c, 5)
    work_ms = 40
    system.scheduler.reset_stats()
    pairs = [(s, c) for c in courses for s in students(8)]
    rids = [str(uuid.uuid4()) for _ in pairs]
    t0 = time.perf_counter()
    for (sid, cid), rid in zip(pairs, rids):
        system.intake.submit(sid, cid, rid, work_ms=work_ms)
    assert system.scheduler.wait_until_idle(timeout=60)
    elapsed = time.perf_counter() - t0

    serial_time = len(pairs) * work_ms / 1000
    assert system.scheduler.peak_courses_in_flight >= 2
    assert elapsed < serial_time * 0.75, f"{elapsed:.2f}s is not faster than serial {serial_time:.2f}s"
    for c in courses:
        info = course_counts(system, c)
        assert info["confirmed_actual"] == 5 and info["waitlisted"] == 3


def test_both_scheduler_modes_keep_invariants(system):
    for mode in ("GLOBAL_FIFO", "FAIR_QUEUE"):
        system.scheduler.set_mode(mode)
        course = f"T-{mode[:4]}"
        make_course(system, course, 7)
        results = burst(system, [(s, course) for s in students(60)], clients=30)
        assert Counter(r["outcome"] for r in results) == {"CONFIRMED": 7, "WAITLISTED": 53}
    assert consistency(system)["all_passed"]
