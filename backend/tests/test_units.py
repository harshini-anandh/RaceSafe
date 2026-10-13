"""Unit tests for metrics, workload generation, failure injection, idempotency keys."""
from __future__ import annotations

import pytest

from app.core.errors import DemoOnlyError, RaceSafeError
from app.database.setup import SCHEMA_FILE, SEED_FILE, split_sql
from app.services.experiments import allocate_counts, build_workload
from app.services.intake import normalize_request_id
from app.services.metrics import jain_index
from app.services.race_lab import build_attempts, deal
from app.workers.failure_injection import FailureInjector, FailureKind, InjectedWorkerFailure


def test_jain_index_bounds():
    assert jain_index([5, 5, 5, 5]) == pytest.approx(1.0)
    assert jain_index([10, 0, 0, 0]) == pytest.approx(0.25)
    assert 0.25 < jain_index([7, 1, 1, 1]) < 1.0
    assert jain_index([]) is None


def test_allocate_counts_sums_to_total():
    counts = allocate_counts(500, [0.7, 0.075, 0.075, 0.075, 0.075])
    assert sum(counts) == 500
    assert counts[0] == 350
    assert all(c >= 1 for c in counts)


def test_workload_is_deterministic_for_a_seed():
    cfg = {"course_count": 4, "distribution": "skewed", "hot_share": 0.7, "weights": None,
           "total_requests": 200, "seed": 7}
    students = [f"S{i:04d}" for i in range(1, 601)]
    c1, w1 = build_workload(cfg, students)
    c2, w2 = build_workload(cfg, students)
    assert w1 == w2 and c1 == c2
    assert len(w1) == 200
    assert len({(x["student_id"], x["course_id"]) for x in w1}) == 200  # no duplicate pairs
    assert c1[0]["label"] == "hot"


def test_failure_injector_targets_one_request():
    inj = FailureInjector(enabled=True)
    inj.arm(FailureKind.BEFORE_COMMIT, request_id="r1", count=2)
    with pytest.raises(InjectedWorkerFailure):
        inj.maybe_fail_before_commit("r1", 1)
    inj.maybe_fail_before_commit("r2", 1)  # other requests unaffected
    with pytest.raises(InjectedWorkerFailure):
        inj.maybe_fail_before_commit("r1", 2)
    inj.maybe_fail_before_commit("r1", 3)  # used up


def test_failure_injection_disabled_outside_demo_mode():
    inj = FailureInjector(enabled=False)
    with pytest.raises(DemoOnlyError):
        inj.arm(FailureKind.LOST_RESPONSE)
    assert inj.consume(FailureKind.LOST_RESPONSE, "x") is False


def test_request_id_normalisation():
    rid = "3F1C2A9E-4B7D-4C1E-9A55-0D3B8F6E2A10"
    assert normalize_request_id(rid) == rid.lower()
    assert len(normalize_request_id(None)) == 36
    with pytest.raises(RaceSafeError):
        normalize_request_id("not-a-uuid")


def test_race_lab_duplicates_are_dealt_to_neighbouring_clients():
    attempts = build_attempts(["S1", "S2", "S3", "S4"], duplicate_ratio=0.5)
    assert attempts == ["S1", "S1", "S2", "S2", "S3", "S4"]
    buckets = deal(attempts, 3)
    assert buckets[0][0] == "S1" and buckets[1][0] == "S1"  # same wave, different clients


def test_sql_files_parse():
    schema = split_sql(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert sum(1 for s in schema if s.upper().startswith("CREATE TABLE")) == 8
    seed = split_sql(SEED_FILE.read_text(encoding="utf-8"))
    assert len(seed) == 2
