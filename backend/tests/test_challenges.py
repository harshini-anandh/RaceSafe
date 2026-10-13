"""KO-06 live technical challenges C1-C5 (Ctrl Alt Del, PS OSH001).

Each test runs the same scenario as scripts/run_challenges.py and
asserts every individual check, so a failure names the exact broken property.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import consistency

pytestmark = pytest.mark.mysql


def assert_report(report: dict) -> None:
    failed = [c for c in report["checks"] if not c["passed"]]
    assert not failed, "\n".join(f"{c['name']}: expected {c['expected']!r}, got {c['actual']!r}" for c in failed)
    assert report["passed"] is True


def test_c1_500_transactional_requests(system):
    report = system.challenges.run("C1", {"scheduler_mode": "GLOBAL_FIFO", "students": 500, "capacity": 10})
    assert_report(report)
    counts = report["data"]["counts"]
    assert counts["confirmed_rows"] == 10 and counts["waitlisted_rows"] == 490 and counts["confirmed_count"] == 10
    assert report["data"]["peak_workers_on_course"] == system.settings.worker_count == 8
    assert consistency(system)["all_passed"]


def test_c1_also_holds_under_fairqueue(system):
    assert_report(system.challenges.run("C1", {"scheduler_mode": "FAIR_QUEUE", "students": 500, "capacity": 10}))


def test_c2_reply_lost_and_fifo_promotion(system):
    report = system.challenges.run("C2", {})
    assert_report(report)
    assert report["data"]["promotions"] == ["S4", "S5", None]
    assert report["data"]["final_confirmed"] == ["S4", "S5"]
    events = report["data"]["s1_events"]
    assert events.count("TRANSACTION_COMMITTED") == 1 and "RESPONSE_LOST" in events and "DUPLICATE_REQUEST" in events


def test_c3_head_of_line_model_and_mysql(system):
    report = system.challenges.run("C3", {"include_real_run": True, "tick_ms": 2})
    assert_report(report)
    model = report["data"]["model"]
    assert model["GLOBAL_FIFO"]["per_course"]["B"]["avg_wait"] == 930
    assert model["FAIR_QUEUE"]["per_course"]["B"]["avg_wait"] == 0
    assert model["GLOBAL_FIFO"]["active_slots"]["peak_busy_workers"] == 8
    assert model["FAIR_QUEUE"]["active_slots"]["peak_busy_workers"] == 2


def test_c4_failure_while_holding_course_slot(system):
    report = system.challenges.run("C4", {})
    assert_report(report)
    kinds = [(s["kind"], s["failures"], s["r1"]["outcome"], s["r2"]["outcome"]) for s in report["data"]["scenarios"]]
    assert kinds == [("AFTER_DEQUEUE", 1, "CONFIRMED", "WAITLISTED"),
                     ("BEFORE_COMMIT", 1, "CONFIRMED", "WAITLISTED"),
                     ("WORKER_CRASH", 1, "CONFIRMED", "WAITLISTED"),
                     ("WORKER_CRASH", 3, "FAILED", "CONFIRMED")]
    assert system.pool.live_workers() == system.pool.size
    # the pool still works after the crashes
    assert system.scheduler.is_idle()


def test_c5_cancel_versus_register_race(system):
    report = system.challenges.run("C5", {"hold_ms": 400, "iterations": 15})
    assert_report(report)
    assert report["data"]["violations"] == []


def test_challenges_over_http(db_settings):
    from app.main import create_app

    app = create_app(db_settings)
    with TestClient(app) as client:
        listing = client.get("/api/challenges").json()["challenges"]
        assert [c["id"] for c in listing] == ["C1", "C2", "C3", "C4", "C5"]
        r = client.post("/api/challenges/C2")
        assert r.status_code == 200 and r.json()["passed"] is True
        assert client.post("/api/challenges/C9").status_code == 404
        assert client.get("/api/challenges/C2").json()["report"]["passed"] is True
