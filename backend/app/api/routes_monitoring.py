"""Events, recovery timeline, live metrics, invariants and scheduler control."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_system
from app.models.enums import EVENT_DESCRIPTIONS
from app.schemas.api import SchedulerModeUpdate, SchedulerPauseUpdate
from app.services import metrics, queries

router = APIRouter(tags=["monitoring"])


@router.get("/events", summary="Recent registration events (newest first)")
def list_events(
    request_id: str | None = Query(None, max_length=36, description="Full UUID or prefix"),
    course_id: str | None = Query(None, max_length=16),
    event_type: list[str] | None = Query(None),
    include_experiment: bool = False,
    limit: int = Query(100, ge=1, le=1000),
    after_id: int | None = Query(None, ge=0),
    system=Depends(get_system),
):
    with system.engine.connect() as conn:
        return {"events": queries.list_events(conn, request_id=request_id, course_id=course_id,
                                              event_types=event_type, include_experiment=include_experiment,
                                              limit=limit, after_id=after_id)}


@router.get("/events/timeline", summary="Failure Recovery Timeline: events grouped by request_id")
def timeline(
    request_id: str | None = Query(None, max_length=36),
    course_id: str | None = Query(None, max_length=16),
    event_type: str | None = Query(None, max_length=40),
    include_experiment: bool = False,
    limit_requests: int = Query(25, ge=1, le=200),
    system=Depends(get_system),
):
    with system.engine.connect() as conn:
        return {"requests": queries.timeline(conn, request_id=request_id, course_id=course_id,
                                             event_type=event_type, include_experiment=include_experiment,
                                             limit_requests=limit_requests)}


@router.get("/events/types", summary="Event names and their meaning")
def event_types():
    return {"event_types": [{"event_type": k, "description": v} for k, v in EVENT_DESCRIPTIONS.items()]}


@router.get("/metrics", summary="Live dashboard metrics from MySQL and the scheduler")
def get_metrics(scope: str = Query("catalog", pattern="^(catalog|all)$"), system=Depends(get_system)):
    with system.engine.connect() as conn:
        return metrics.dashboard_metrics(system, conn, scope)


@router.get("/metrics/live", summary="Scheduler queues, in-flight requests and worker states (no DB)")
def live(system=Depends(get_system)):
    return metrics.live_state(system)


@router.get("/metrics/throughput", summary="Completed requests per second")
def throughput(seconds: int = Query(120, ge=10, le=600), scope: str = Query("all", pattern="^(catalog|all)$"),
               system=Depends(get_system)):
    with system.engine.connect() as conn:
        return {"series": metrics.throughput_series(conn, seconds, scope)}


@router.get("/consistency", summary="Check correctness invariants directly in MySQL")
def consistency(system=Depends(get_system)):
    with system.engine.connect() as conn:
        return queries.consistency_report(conn)


@router.get("/scheduler", summary="Scheduler mode and queue state")
def scheduler_state(system=Depends(get_system)):
    return system.scheduler.snapshot()


@router.put("/scheduler/mode", summary="Switch between GLOBAL_FIFO and FAIR_QUEUE")
def set_scheduler_mode(body: SchedulerModeUpdate, system=Depends(get_system)):
    system.scheduler.set_mode(body.mode)
    return system.scheduler.snapshot()


@router.put("/scheduler/paused", summary="Pause/resume dispatching (requests keep queueing)")
def set_paused(body: SchedulerPauseUpdate, system=Depends(get_system)):
    (system.scheduler.pause if body.paused else system.scheduler.resume)()
    return system.scheduler.snapshot()
