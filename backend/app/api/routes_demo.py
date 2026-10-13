"""Experiments, Race Condition Lab, failure injection and demo admin.

Everything that injects failures, runs unsafe code or deletes data is
DEVELOPMENT/DEMO ONLY: it returns 403 unless RACESAFE_ENV=development.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text

from app.api.deps import get_system, require_demo_mode, require_local_demo
from app.core.errors import ConflictError, NotFoundError
from app.schemas.api import (ExperimentConfig, FailureInjection, PrepareCourse, RaceLabConfig, ResetRequest,
                             SimulateRegistrations)
from app.services.demo_data import prepare_experiment_courses, reset_all_data
from app.services.intake import normalize_request_id
from app.workers.failure_injection import FailureKind

router = APIRouter()


# ---------------------------------------------------------------- FairQueue experiments
@router.post("/experiments", tags=["experiments"], status_code=202,
             summary="Start a FairQueue vs Global FIFO experiment (runs in the background)")
def start_experiment(config: ExperimentConfig, system=Depends(require_demo_mode)):
    return system.experiments.start(config.model_dump())


@router.get("/experiments", tags=["experiments"], summary="Previous experiment groups")
def list_experiments(limit: int = Query(20, ge=1, le=100), system=Depends(get_system)):
    return {"groups": system.experiments.list_groups(limit)}


@router.get("/experiments/{group_id}", tags=["experiments"], summary="Progress, results and comparison")
def get_experiment(group_id: str, system=Depends(get_system)):
    return system.experiments.get_group(group_id)


# ---------------------------------------------------------------- race lab
@router.post("/race-lab/unsafe", tags=["race-lab"],
             summary="DEMO ONLY: run the deliberately unsafe check-then-act registration on isolated tables")
async def race_unsafe(config: RaceLabConfig, system=Depends(require_demo_mode)):
    return await run_in_threadpool(system.race_lab.run_unsafe, config.model_dump())


@router.post("/race-lab/safe", tags=["race-lab"],
             summary="Run the same burst through the real RaceSafe pipeline (course LAB-SAFE)")
async def race_safe(config: RaceLabConfig, system=Depends(require_demo_mode)):
    return await run_in_threadpool(system.race_lab.run_safe, config.model_dump())


@router.get("/race-lab/history", tags=["race-lab"], summary="Previous race-lab results")
def race_history(limit: int = Query(20, ge=1, le=100), system=Depends(get_system)):
    return {"latest": system.race_lab.last_results, "history": system.race_lab.history(limit)}


# ---------------------------------------------------------------- failure injection
@router.post("/failures/inject", tags=["failures"], summary="DEMO ONLY: arm a controlled worker failure")
def inject_failure(body: FailureInjection, system=Depends(require_demo_mode)):
    request_id = normalize_request_id(body.request_id) if body.request_id else None
    system.injector.arm(FailureKind(body.kind), request_id=request_id, count=body.count)
    return {"armed": {"kind": body.kind, "request_id": request_id, "count": body.count},
            "state": system.injector.snapshot()}


@router.get("/failures", tags=["failures"], summary="Currently armed failures")
def armed_failures(system=Depends(get_system)):
    return system.injector.snapshot()


@router.delete("/failures", tags=["failures"], summary="DEMO ONLY: disarm all failures")
def clear_failures(system=Depends(require_demo_mode)):
    system.injector.clear()
    return system.injector.snapshot()


@router.post("/failures/simulate-restart", tags=["failures"],
             summary="DEMO ONLY: drop the in-memory queues (crash) and rebuild them from MySQL (restart)")
def simulate_restart(system=Depends(require_demo_mode)):
    return system.simulate_crash_and_restart()


# ---------------------------------------------------------------- admin (demo)
@router.post("/admin/reset", tags=["admin"],
             summary="DEMO ONLY (localhost): delete all registrations, requests, events and experiments")
def reset(body: ResetRequest, system=Depends(require_local_demo)):
    if body.confirm != "RESET":
        raise ConflictError('Send {"confirm": "RESET"} to confirm deleting all demo data')
    if not system.experiment_lock.acquire(blocking=False):
        raise ConflictError("An experiment is running; wait for it to finish")
    try:
        if not system.scheduler.is_idle():
            raise ConflictError("Requests are still queued or running; wait for them to finish")
        system.injector.clear()
        with system.engine.begin() as conn:
            deleted = reset_all_data(conn)
        system.scheduler.reset_stats()
        return {"reset": True, "deleted": deleted}
    finally:
        system.experiment_lock.release()


@router.post("/admin/prepare-course", tags=["admin"],
             summary="DEMO ONLY: create or empty an EXPERIMENT course (used by the load-test script)")
def prepare_course(body: PrepareCourse, system=Depends(require_local_demo)):
    with system.engine.begin() as conn:
        pending = conn.execute(
            text("SELECT COUNT(*) FROM registration_requests WHERE course_id = :c AND status = 'RECEIVED'"),
            {"c": body.course_id},
        ).scalar_one()
        if pending:
            raise ConflictError(f"{body.course_id} still has {pending} pending requests")
        prepare_experiment_courses(conn, [(body.course_id, body.course_name or f"Load test {body.course_id}",
                                           body.capacity)])
    return {"course_id": body.course_id, "capacity": body.capacity, "confirmed_count": 0}


@router.post("/admin/simulate-registrations", tags=["admin"],
             summary="DEMO ONLY: N not-yet-registered students register for a course (fills it quickly)")
def simulate_registrations(body: SimulateRegistrations, system=Depends(require_demo_mode)):
    with system.engine.connect() as conn:
        if conn.execute(text("SELECT 1 FROM courses WHERE course_id = :c"), {"c": body.course_id}).first() is None:
            raise NotFoundError(f"Unknown course_id '{body.course_id}'")
        students = [r[0] for r in conn.execute(
            text("SELECT s.student_id FROM students s WHERE NOT EXISTS "
                 "(SELECT 1 FROM registrations r WHERE r.student_id = s.student_id AND r.course_id = :c) "
                 "ORDER BY s.student_id LIMIT :n"),
            {"c": body.course_id, "n": body.count},
        ).all()]
    ids = []
    for sid in students:
        r = system.intake.submit(sid, body.course_id, str(uuid.uuid4()))
        ids.append(r.request_id)
    return {"course_id": body.course_id, "submitted": len(ids), "request_ids": ids}
