"""Shared fixtures.

Unit tests run anywhere. Tests marked `mysql` need MySQL and use a SEPARATE
database (TEST_DB_NAME, default racesafe_test) that is dropped and recreated
at the start of the test session. They are skipped if MySQL is unreachable.
"""
from __future__ import annotations

import os
import threading
import time
import uuid

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.services import queries
from app.services.demo_data import prepare_experiment_courses, reset_all_data


def _test_settings(**overrides) -> Settings:
    return Settings.from_env(db_name=os.getenv("TEST_DB_NAME", "racesafe_test"), environment="test",
                             simulated_work_ms=0, **overrides)


@pytest.fixture(scope="session")
def db_settings() -> Settings:
    from app.database.setup import initialize_database

    settings = _test_settings()
    if settings.db_name == Settings.from_env().db_name:
        pytest.fail("TEST_DB_NAME must differ from DB_NAME - the tests drop and recreate the test database")
    try:
        initialize_database(settings, seed=True, drop_existing=True)
    except Exception as exc:
        pytest.skip(f"MySQL not available for integration tests: {type(exc).__name__}: {exc}")
    return settings


@pytest.fixture
def system(db_settings):
    from app.system import RaceSafeSystem

    rs = RaceSafeSystem(db_settings)
    with rs.engine.begin() as conn:
        reset_all_data(conn)
    rs.start()
    yield rs
    rs.stop()


# ---------------------------------------------------------------- helpers
def make_course(system, course_id: str, capacity: int) -> None:
    with system.engine.begin() as conn:
        prepare_experiment_courses(conn, [(course_id, f"Test {course_id}", capacity)])


def submit_and_wait(system, student_id: str, course_id: str, request_id: str | None = None,
                    timeout: float = 60, **kwargs) -> dict:
    r = system.intake.submit(student_id, course_id, request_id, wait=True, **kwargs)
    if r.waiter is not None:
        r.waiter.result(timeout=timeout)
    return outcome(system, r.request_id)


def outcome(system, request_id: str) -> dict:
    with system.engine.connect() as conn:
        return queries.request_outcome(conn, request_id)


def burst(system, pairs: list[tuple[str, str]], clients: int = 50, request_ids: list[str] | None = None,
          timeout: float = 120) -> list[dict]:
    """Submit (student, course) pairs from `clients` threads released by one Barrier."""
    request_ids = request_ids or [str(uuid.uuid4()) for _ in pairs]
    clients = min(clients, len(pairs))
    buckets = [list(range(i, len(pairs), clients)) for i in range(clients)]
    barrier = threading.Barrier(clients)
    results: list = [None] * len(pairs)
    errors: list = []

    def run(indexes):
        try:
            barrier.wait(timeout=30)
        except threading.BrokenBarrierError:
            pass
        for i in indexes:
            try:
                r = system.intake.submit(pairs[i][0], pairs[i][1], request_ids[i], wait=True)
                results[i] = r
            except Exception as exc:  # pragma: no cover - surfaced by the assert below
                errors.append(exc)

    threads = [threading.Thread(target=run, args=(b,)) for b in buckets]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    deadline = time.monotonic() + timeout
    for r in results:
        if r.waiter is not None:
            r.waiter.result(timeout=max(0.1, deadline - time.monotonic()))
    return [outcome(system, r.request_id) for r in results]


def course_counts(system, course_id: str) -> dict:
    with system.engine.connect() as conn:
        return queries.get_course(conn, course_id)


def consistency(system) -> dict:
    with system.engine.connect() as conn:
        return queries.consistency_report(conn)


def events_for(system, request_id: str) -> list[str]:
    with system.engine.connect() as conn:
        return [r[0] for r in conn.execute(
            text("SELECT event_type FROM registration_events WHERE request_id = :r ORDER BY event_id"),
            {"r": request_id},
        ).all()]


def students(n: int, start: int = 1) -> list[str]:
    return [f"S{i:04d}" for i in range(start, start + n)]
