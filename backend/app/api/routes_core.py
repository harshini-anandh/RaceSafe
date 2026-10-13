"""Health, courses and students."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.api.deps import get_system
from app.core.clock import iso, utcnow
from app.services import queries

router = APIRouter(tags=["core"])


@router.get("/health", summary="Backend, database and worker health")
def health(system=Depends(get_system)):
    db_ok, db_error, version = True, None, None
    try:
        with system.engine.connect() as conn:
            version = conn.execute(text("SELECT VERSION()")).scalar_one()
            conn.execute(text("SELECT 1 FROM registration_requests LIMIT 1"))
    except Exception as exc:
        db_ok, db_error = False, f"{type(exc).__name__}: {str(exc)[:300]}"
    return {
        "status": "ok" if db_ok else "degraded",
        "time": iso(utcnow()),
        "database": {"ok": db_ok, "error": db_error or system.startup_error, "version": version,
                     "name": system.settings.db_name},
        "environment": system.settings.environment,
        "demo_mode": system.settings.demo_mode,
        "scheduler_mode": system.scheduler.mode,
        "scheduler_paused": system.scheduler.paused,
        "workers": system.settings.worker_count,
        "max_attempts": system.settings.max_attempts,
        "per_course_in_flight": system.settings.per_course_in_flight,
        "recovered_on_startup": system.recovered_on_startup,
    }


@router.get("/courses", summary="List courses with capacity, confirmed seats and waitlist size")
def list_courses(include_experiment: bool = False, system=Depends(get_system)):
    with system.engine.connect() as conn:
        return {"courses": queries.list_courses(conn, include_experiment)}


@router.get("/courses/{course_id}", summary="One course")
def get_course(course_id: str, system=Depends(get_system)):
    with system.engine.connect() as conn:
        return queries.get_course(conn, course_id)


@router.get("/courses/{course_id}/registrations", summary="Confirmed students and FIFO waitlist of a course")
def course_registrations(course_id: str, system=Depends(get_system)):
    with system.engine.connect() as conn:
        return queries.course_registrations(conn, course_id)


@router.get("/courses/{course_id}/waitlist", summary="FIFO waitlist of a course (position 1 is promoted first)")
def course_waitlist(course_id: str, system=Depends(get_system)):
    with system.engine.connect() as conn:
        data = queries.course_registrations(conn, course_id)
    return {"course": data["course"], "waitlist": data["waitlist"]}


@router.get("/students", summary="List / search sample students")
def list_students(search: str | None = Query(None, max_length=50), limit: int = Query(100, ge=1, le=1000),
                  offset: int = Query(0, ge=0), system=Depends(get_system)):
    with system.engine.connect() as conn:
        return queries.list_students(conn, search, limit, offset)


@router.get("/students/{student_id}/registrations", summary="A student's registrations and waitlist positions")
def student_registrations(student_id: str, system=Depends(get_system)):
    with system.engine.connect() as conn:
        return queries.student_registrations(conn, student_id)
