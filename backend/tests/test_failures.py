"""Failure injection, retries, lost responses and restart recovery (tests 6, 7, 9)."""
from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.scheduler.fair_queue import Job
from app.workers.failure_injection import FailureKind
from tests.conftest import consistency, course_counts, events_for, make_course, outcome, submit_and_wait

pytestmark = pytest.mark.mysql


def test_worker_failure_before_commit_rolls_back_and_retry_succeeds(system):
    make_course(system, "T-FAIL", 3)
    rid = str(uuid.uuid4())
    system.injector.arm(FailureKind.BEFORE_COMMIT, request_id=rid, count=1)
    result = submit_and_wait(system, "S0001", "T-FAIL", rid)

    assert result["outcome"] == "CONFIRMED"
    assert result["attempts"] == 2
    events = events_for(system, rid)
    for expected in ("WORKER_FAILURE", "TRANSACTION_ROLLED_BACK", "RETRY_SCHEDULED", "REQUEST_CONFIRMED"):
        assert expected in events
    assert events.count("TRANSACTION_COMMITTED") == 1
    assert events.index("TRANSACTION_ROLLED_BACK") < events.index("TRANSACTION_COMMITTED")
    course = course_counts(system, "T-FAIL")
    assert course["confirmed_count"] == course["confirmed_actual"] == 1  # rollback left nothing behind


def test_three_failures_mark_request_failed_without_partial_state(system):
    make_course(system, "T-FAIL3", 3)
    rid = str(uuid.uuid4())
    system.injector.arm(FailureKind.BEFORE_COMMIT, request_id=rid, count=3)
    result = submit_and_wait(system, "S0001", "T-FAIL3", rid)

    assert result["status"] == "FAILED" and result["attempts"] == 3
    assert events_for(system, rid).count("WORKER_FAILURE") == 3
    assert "REQUEST_FAILED" in events_for(system, rid)
    course = course_counts(system, "T-FAIL3")
    assert course["confirmed_count"] == 0 and course["confirmed_actual"] == 0 and course["waitlisted"] == 0
    assert consistency(system)["all_passed"]


def test_committed_request_is_never_applied_twice(system):
    """Recovery/retry of an already-committed request must be a no-op."""
    make_course(system, "T-ONCE", 3)
    rid = str(uuid.uuid4())
    submit_and_wait(system, "S0001", "T-ONCE", rid)
    duplicate_job = Job(request_id=rid, student_id="S0001", course_id="T-ONCE", attempt=2)
    system.processor.process(duplicate_job, "TEST")
    assert outcome(system, rid)["attempts"] == 1
    assert events_for(system, rid).count("TRANSACTION_COMMITTED") == 1
    assert course_counts(system, "T-ONCE")["confirmed_actual"] == 1


def test_restart_recovery_requeues_pending_requests(db_settings):
    from app.services.demo_data import reset_all_data
    from app.system import RaceSafeSystem

    first = RaceSafeSystem(db_settings)
    with first.engine.begin() as conn:
        reset_all_data(conn)
    first.start()
    make_course(first, "T-RECOVER", 4)
    first.scheduler.pause()  # requests are persisted but never processed ...
    rids = [first.intake.submit(f"S{i:04d}", "T-RECOVER").request_id for i in range(1, 11)]
    first.stop()  # ... and then the process "dies", losing the in-memory queue

    second = RaceSafeSystem(db_settings)
    second.start()  # startup recovery scan
    try:
        assert second.recovered_on_startup == 10
        assert second.scheduler.wait_until_idle(timeout=60)
        results = [outcome(second, rid) for rid in rids]
        assert sorted(r["outcome"] for r in results) == ["CONFIRMED"] * 4 + ["WAITLISTED"] * 6
        assert all("RECOVERY_REQUEUED" in events_for(second, rid) for rid in rids)
        with second.engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM registrations WHERE course_id = 'T-RECOVER'")).scalar() == 10
        assert consistency(second)["all_passed"]
    finally:
        second.stop()


def test_simulated_crash_and_restart_endpoint_logic(system):
    make_course(system, "T-CRASH", 2)
    system.scheduler.pause()
    rids = [system.intake.submit(f"S{i:04d}", "T-CRASH").request_id for i in range(1, 6)]
    info = system.simulate_crash_and_restart()
    assert info["dropped_from_memory"] == 5 and info["requeued_from_database"] == 5
    assert system.scheduler.wait_until_idle(timeout=30)
    assert sorted(outcome(system, r)["outcome"] for r in rids) == ["CONFIRMED"] * 2 + ["WAITLISTED"] * 3


def test_lost_response_then_safe_retry_over_http(db_settings):
    from app.main import create_app
    from app.services.demo_data import prepare_experiment_courses, reset_all_data

    app = create_app(db_settings)
    with TestClient(app) as client:
        system = app.state.system
        with system.engine.begin() as conn:
            reset_all_data(conn)
            prepare_experiment_courses(conn, [("T-LOST", "Lost response", 2)])
        rid = str(uuid.uuid4())
        r = client.post("/api/failures/inject", json={"kind": "LOST_RESPONSE", "request_id": rid})
        assert r.status_code == 200

        body = {"student_id": "S0001", "course_id": "T-LOST", "request_id": rid}
        first = client.post("/api/registrations", json=body)
        assert first.status_code == 504 and first.json()["code"] == "RESPONSE_LOST"

        retry = client.post("/api/registrations", json=body)  # same request_id
        assert retry.status_code == 200
        assert retry.json()["outcome"] == "CONFIRMED" and retry.json()["replayed"] is True

        new_id = client.post("/api/registrations", json={**body, "request_id": str(uuid.uuid4())})
        assert new_id.json()["outcome"] == "ALREADY_REGISTERED"

        course = client.get("/api/courses/T-LOST").json()
        assert course["confirmed_actual"] == 1 and course["waitlisted"] == 0
        events = [e["event_type"] for e in client.get(f"/api/requests/{rid}/events").json()["events"]]
        assert events.count("TRANSACTION_COMMITTED") == 1
        assert "RESPONSE_LOST" in events and "DUPLICATE_REQUEST" in events
        assert client.get("/api/consistency").json()["all_passed"]


def test_api_validation_and_errors(db_settings):
    from app.main import create_app

    app = create_app(db_settings)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["database"]["ok"] is True
        bad = client.post("/api/registrations", json={"student_id": "S0001", "course_id": "CS301",
                                                       "request_id": "nope"})
        assert bad.status_code == 400
        unknown = client.post("/api/registrations", json={"student_id": "S9999", "course_id": "CS301"})
        assert unknown.status_code == 404
        assert client.post("/api/registrations", json={"student_id": "bad id!", "course_id": "CS301"}).status_code == 422
        assert client.get(f"/api/requests/{uuid.uuid4()}").status_code == 404
        assert client.post("/api/admin/reset", json={"confirm": "no"}).status_code == 409
