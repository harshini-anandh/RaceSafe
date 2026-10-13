"""Race Condition Lab and FairQueue experiment runner."""
from __future__ import annotations

import time

import pytest
from sqlalchemy import text

from tests.conftest import consistency

pytestmark = pytest.mark.mysql

LAB = {"capacity": 10, "students": 100, "client_threads": 20, "duplicate_ratio": 0.2,
       "race_window_ms": 5, "force_interleaving": True}


def test_unsafe_path_overbooks_under_forced_interleaving(system):
    result = system.race_lab.run_unsafe(LAB)
    # 20 clients all read "0 confirmed" before anyone writes -> 20 confirmations.
    assert result["confirmed"] >= 20
    assert result["overbooked"] >= 10
    assert result["correct"] is False


def test_unsafe_path_is_isolated_from_real_tables(system):
    system.race_lab.run_unsafe(LAB)
    with system.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM registrations")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM courses WHERE course_id = 'LAB-UNSAFE'")).scalar() == 0


def test_safe_path_never_overbooks_same_burst(system):
    result = system.race_lab.run_safe(LAB)
    assert result["confirmed"] == 10
    assert result["waitlisted"] == 90
    assert result["already_registered"] == 20  # the double clicks
    assert result["overbooked"] == 0 and result["duplicate_registrations"] == 0
    assert result["stored_confirmed_count"] == 10
    assert result["correct"] is True
    assert consistency(system)["all_passed"]


def test_fairqueue_experiment_runs_both_modes_on_same_workload(system):
    config = {"mode": "BOTH", "total_requests": 120, "course_count": 4, "distribution": "skewed",
              "hot_share": 0.7, "weights": None, "capacity": 20, "work_ms": 2, "repeats": 1, "seed": 1}
    previous_mode = system.scheduler.mode
    group = system.experiments.start(config)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = system.experiments.get_group(group["group_id"])
        if state["status"] != "RUNNING":
            break
        time.sleep(0.3)
    assert state["status"] == "COMPLETED", state.get("error")
    assert [r["scheduler_mode"] for r in state["runs"]] == ["GLOBAL_FIFO", "FAIR_QUEUE"]
    for run in state["runs"]:
        s = run["summary"]
        assert s["completed"] == 120 and s["totals"]["failed"] == 0
        for course in s["per_course"]:
            assert course["confirmed"] <= 20
            assert course["confirmed"] + course["waitlisted"] == course["requests"]
        assert 0 < s["fairness"]["jain_index"] <= 1
    by_mode = state["comparison"]["by_mode"]
    assert set(by_mode) == {"GLOBAL_FIFO", "FAIR_QUEUE"}
    assert system.scheduler.mode == previous_mode  # restored after the experiment
    assert consistency(system)["all_passed"]
