"""Read-only queries used by the API (courses, students, requests, events)."""
from __future__ import annotations

import json

from sqlalchemy import bindparam, text

from app.core.clock import iso, ms_between
from app.core.errors import NotFoundError
from app.models.enums import RequestStatus


def _event_row(row) -> dict:
    meta = row["metadata"]
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {"raw": meta}
    return {
        "event_id": row["event_id"],
        "request_id": row["request_id"],
        "student_id": row["student_id"],
        "course_id": row["course_id"],
        "event_type": row["event_type"],
        "attempt": row["attempt"],
        "worker_name": row["worker_name"],
        "message": row["message"],
        "metadata": meta,
        "experiment_id": row["experiment_id"],
        "created_at": iso(row["created_at"]),
    }


EVENT_COLUMNS = ("event_id, request_id, student_id, course_id, event_type, attempt, worker_name, "
                 "message, metadata, experiment_id, created_at")


# ---------------------------------------------------------------- courses / students
def list_courses(conn, include_experiment: bool = False) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT c.course_id, c.course_name, c.capacity, c.confirmed_count, c.kind, "
            "       COALESCE(SUM(r.status = 'CONFIRMED'), 0) AS confirmed_actual, "
            "       COALESCE(SUM(r.status = 'WAITLISTED'), 0) AS waitlisted "
            "FROM courses c LEFT JOIN registrations r ON r.course_id = c.course_id "
            "WHERE (:inc = 1 OR c.kind = 'CATALOG') "
            "GROUP BY c.course_id ORDER BY c.kind, c.course_id"
        ),
        {"inc": 1 if include_experiment else 0},
    ).mappings().all()
    return [_course_dict(r) for r in rows]


def _course_dict(r) -> dict:
    return {
        "course_id": r["course_id"],
        "course_name": r["course_name"],
        "capacity": r["capacity"],
        "confirmed_count": r["confirmed_count"],
        "confirmed_actual": int(r["confirmed_actual"]),
        "waitlisted": int(r["waitlisted"]),
        "available_seats": max(0, r["capacity"] - r["confirmed_count"]),
        "kind": r["kind"],
        "consistent": r["confirmed_count"] == int(r["confirmed_actual"]) and r["confirmed_count"] <= r["capacity"],
    }


def get_course(conn, course_id: str) -> dict:
    row = conn.execute(
        text(
            "SELECT c.course_id, c.course_name, c.capacity, c.confirmed_count, c.kind, "
            "       COALESCE(SUM(r.status = 'CONFIRMED'), 0) AS confirmed_actual, "
            "       COALESCE(SUM(r.status = 'WAITLISTED'), 0) AS waitlisted "
            "FROM courses c LEFT JOIN registrations r ON r.course_id = c.course_id "
            "WHERE c.course_id = :cid GROUP BY c.course_id"
        ),
        {"cid": course_id},
    ).mappings().first()
    if row is None:
        raise NotFoundError(f"Unknown course_id '{course_id}'")
    return _course_dict(row)


def course_registrations(conn, course_id: str) -> dict:
    course = get_course(conn, course_id)
    rows = conn.execute(
        text(
            "SELECT r.registration_id, r.student_id, s.full_name, r.status, r.waitlist_seq, r.request_id, "
            "       r.created_at, r.promoted_at "
            "FROM registrations r JOIN students s ON s.student_id = r.student_id "
            "WHERE r.course_id = :cid "
            "ORDER BY r.status = 'WAITLISTED', r.waitlist_seq, r.created_at, r.registration_id"
        ),
        {"cid": course_id},
    ).mappings().all()
    confirmed, waitlist = [], []
    for r in rows:
        item = {
            "registration_id": r["registration_id"],
            "student_id": r["student_id"],
            "full_name": r["full_name"],
            "status": r["status"],
            "request_id": r["request_id"],
            "created_at": iso(r["created_at"]),
            "promoted_at": iso(r["promoted_at"]),
        }
        if r["status"] == "CONFIRMED":
            confirmed.append(item)
        else:
            item["waitlist_seq"] = r["waitlist_seq"]
            item["position"] = len(waitlist) + 1
            waitlist.append(item)
    return {"course": course, "confirmed": confirmed, "waitlist": waitlist}


def list_students(conn, search: str | None, limit: int, offset: int) -> dict:
    params = {"limit": limit, "offset": offset, "q": f"%{search.strip()}%" if search else None}
    where = "WHERE (:q IS NULL OR student_id LIKE :q OR full_name LIKE :q)"
    total = conn.execute(text(f"SELECT COUNT(*) FROM students {where}"), params).scalar_one()
    rows = conn.execute(
        text(f"SELECT student_id, full_name, email FROM students {where} "
             "ORDER BY student_id LIMIT :limit OFFSET :offset"),
        params,
    ).mappings().all()
    return {"total": total, "students": [dict(r) for r in rows]}


def student_registrations(conn, student_id: str) -> dict:
    student = conn.execute(
        text("SELECT student_id, full_name, email FROM students WHERE student_id = :sid"), {"sid": student_id}
    ).mappings().first()
    if student is None:
        raise NotFoundError(f"Unknown student_id '{student_id}'")
    rows = conn.execute(
        text(
            "SELECT r.course_id, c.course_name, r.status, r.waitlist_seq, r.request_id, r.created_at, r.promoted_at, "
            "       (SELECT COUNT(*) FROM registrations w "
            "         WHERE w.course_id = r.course_id AND w.status = 'WAITLISTED' "
            "           AND w.waitlist_seq <= r.waitlist_seq) AS waitlist_position "
            "FROM registrations r JOIN courses c ON c.course_id = r.course_id "
            "WHERE r.student_id = :sid ORDER BY c.kind, r.course_id"
        ),
        {"sid": student_id},
    ).mappings().all()
    regs = [{
        "course_id": r["course_id"],
        "course_name": r["course_name"],
        "status": r["status"],
        "waitlist_position": int(r["waitlist_position"]) if r["status"] == "WAITLISTED" else None,
        "request_id": r["request_id"],
        "created_at": iso(r["created_at"]),
        "promoted_at": iso(r["promoted_at"]),
    } for r in rows]
    return {"student": dict(student), "registrations": regs}


# ---------------------------------------------------------------- requests
def request_outcome(conn, request_id: str) -> dict:
    row = conn.execute(
        text(
            "SELECT q.request_id, q.student_id, q.course_id, q.status, q.result, q.attempts, q.last_error, "
            "       q.worker_name, q.experiment_id, q.received_at, q.queued_at, q.started_at, q.completed_at, "
            "       r.status AS registration_status, r.waitlist_seq "
            "FROM registration_requests q "
            "LEFT JOIN registrations r ON r.student_id = q.student_id AND r.course_id = q.course_id "
            "WHERE q.request_id = :rid"
        ),
        {"rid": request_id},
    ).mappings().first()
    if row is None:
        raise NotFoundError(f"Unknown request_id '{request_id}'")
    position = None
    if row["registration_status"] == "WAITLISTED":
        position = conn.execute(
            text(
                "SELECT COUNT(*) FROM registrations WHERE course_id = :cid AND status = 'WAITLISTED' "
                "AND waitlist_seq <= :seq"
            ),
            {"cid": row["course_id"], "seq": row["waitlist_seq"]},
        ).scalar_one()
    status = row["status"]
    return {
        "request_id": row["request_id"],
        "student_id": row["student_id"],
        "course_id": row["course_id"],
        "status": status,
        "result": row["result"],
        "outcome": row["result"] if status == RequestStatus.COMPLETED.value else
                   ("PENDING" if status == RequestStatus.RECEIVED.value else status),
        "attempts": row["attempts"],
        "last_error": row["last_error"],
        "worker_name": row["worker_name"],
        "registration_status": row["registration_status"],
        "waitlist_position": int(position) if position is not None else None,
        "experiment_id": row["experiment_id"],
        "received_at": iso(row["received_at"]),
        "started_at": iso(row["started_at"]),
        "completed_at": iso(row["completed_at"]),
        "queue_wait_ms": ms_between(row["queued_at"], row["started_at"]),
        "processing_ms": ms_between(row["started_at"], row["completed_at"]),
    }


# ---------------------------------------------------------------- events / timeline
def _event_filters(request_id, course_id, event_types, include_experiment, alias=""):
    p = alias
    clauses, params = [], {}
    if request_id:
        clauses.append(f"{p}request_id LIKE :rid")
        params["rid"] = request_id.strip().lower() + "%"
    if course_id:
        clauses.append(f"{p}course_id = :cid")
        params["cid"] = course_id
    if event_types:
        clauses.append(f"{p}event_type IN :types")
        params["types"] = list(event_types)
    if not include_experiment:
        clauses.append(f"{p}experiment_id IS NULL")
    return clauses, params


def list_events(conn, *, request_id=None, course_id=None, event_types=None, include_experiment=False,
                limit=100, after_id=None) -> list[dict]:
    clauses, params = _event_filters(request_id, course_id, event_types, include_experiment)
    if after_id:
        clauses.append("event_id > :after")
        params["after"] = after_id
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    stmt = text(f"SELECT {EVENT_COLUMNS} FROM registration_events {where} ORDER BY event_id DESC LIMIT :limit")
    if event_types:
        stmt = stmt.bindparams(bindparam("types", expanding=True))
    params["limit"] = limit
    return [_event_row(r) for r in conn.execute(stmt, params).mappings().all()]


def timeline(conn, *, request_id=None, course_id=None, event_type=None, include_experiment=False,
             limit_requests=25) -> list[dict]:
    """Events grouped by request_id, newest requests first."""
    clauses, params = _event_filters(request_id, course_id, [event_type] if event_type else None,
                                     include_experiment)
    clauses.append("request_id IS NOT NULL")
    stmt = text(
        "SELECT request_id, MAX(event_id) AS last_event FROM registration_events "
        f"WHERE {' AND '.join(clauses)} GROUP BY request_id ORDER BY last_event DESC LIMIT :limit"
    )
    if event_type:
        stmt = stmt.bindparams(bindparam("types", expanding=True))
    params["limit"] = limit_requests
    ids = [r["request_id"] for r in conn.execute(stmt, params).mappings().all()]
    if not ids:
        return []

    events = conn.execute(
        text(f"SELECT {EVENT_COLUMNS} FROM registration_events WHERE request_id IN :ids ORDER BY event_id")
        .bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    ).mappings().all()
    requests = conn.execute(
        text(
            "SELECT request_id, student_id, course_id, status, result, attempts, received_at, completed_at "
            "FROM registration_requests WHERE request_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    ).mappings().all()
    req_by_id = {r["request_id"]: r for r in requests}

    groups: dict[str, dict] = {}
    for rid in ids:
        req = req_by_id.get(rid)
        groups[rid] = {
            "request_id": rid,
            "student_id": req["student_id"] if req else None,
            "course_id": req["course_id"] if req else None,
            "status": req["status"] if req else None,
            "result": req["result"] if req else None,
            "attempts": req["attempts"] if req else None,
            "received_at": iso(req["received_at"]) if req else None,
            "completed_at": iso(req["completed_at"]) if req else None,
            "events": [],
        }
    for e in events:
        group = groups[e["request_id"]]
        group["events"].append(_event_row(e))
        if group["student_id"] is None:
            group["student_id"], group["course_id"] = e["student_id"], e["course_id"]
    return [groups[rid] for rid in ids]


# ---------------------------------------------------------------- invariants
def consistency_report(conn) -> dict:
    """Checks the invariants the judges care about, straight from the DB."""
    over_capacity = conn.execute(
        text(
            "SELECT c.course_id, c.capacity, COUNT(r.registration_id) AS confirmed "
            "FROM courses c JOIN registrations r ON r.course_id = c.course_id AND r.status = 'CONFIRMED' "
            "GROUP BY c.course_id HAVING COUNT(r.registration_id) > c.capacity"
        )
    ).mappings().all()
    count_mismatch = conn.execute(
        text(
            "SELECT c.course_id, c.confirmed_count, COALESCE(SUM(r.status = 'CONFIRMED'), 0) AS actual "
            "FROM courses c LEFT JOIN registrations r ON r.course_id = c.course_id "
            "GROUP BY c.course_id HAVING c.confirmed_count <> COALESCE(SUM(r.status = 'CONFIRMED'), 0)"
        )
    ).mappings().all()
    duplicates = conn.execute(
        text(
            "SELECT student_id, course_id, COUNT(*) AS n FROM registrations "
            "GROUP BY student_id, course_id HAVING COUNT(*) > 1"
        )
    ).mappings().all()
    seats_free_with_waitlist = conn.execute(
        text(
            "SELECT c.course_id FROM courses c "
            "WHERE c.confirmed_count < c.capacity AND EXISTS "
            "  (SELECT 1 FROM registrations w WHERE w.course_id = c.course_id AND w.status = 'WAITLISTED')"
        )
    ).mappings().all()
    results_without_registration = conn.execute(
        text(
            "SELECT COUNT(*) FROM registration_requests q JOIN courses c ON c.course_id = q.course_id "
            "WHERE c.kind = 'CATALOG' "  # experiment courses are wiped between runs
            "AND q.status = 'COMPLETED' AND q.result IN ('CONFIRMED', 'WAITLISTED') AND NOT EXISTS "
            "  (SELECT 1 FROM registration_events e WHERE e.request_id = q.request_id "
            "     AND e.event_type = 'REGISTRATION_CANCELLED') "
            "AND NOT EXISTS (SELECT 1 FROM registrations r "
            "     WHERE r.student_id = q.student_id AND r.course_id = q.course_id)"
        )
    ).scalar_one()
    totals = conn.execute(
        text(
            "SELECT COUNT(*) AS courses, "
            "  (SELECT COUNT(*) FROM registrations WHERE status = 'CONFIRMED') AS confirmed, "
            "  (SELECT COUNT(*) FROM registrations WHERE status = 'WAITLISTED') AS waitlisted "
            "FROM courses"
        )
    ).mappings().first()
    checks = [
        {"name": "No course exceeds its capacity", "passed": not over_capacity,
         "details": [dict(r) for r in over_capacity]},
        {"name": "confirmed_count matches CONFIRMED registration rows", "passed": not count_mismatch,
         "details": [{k: int(v) if k == "actual" else v for k, v in dict(r).items()} for r in count_mismatch]},
        {"name": "No student registered twice for the same course", "passed": not duplicates,
         "details": [dict(r) for r in duplicates]},
        {"name": "No free seat while students wait (promotion is not skipped)",
         "passed": not seats_free_with_waitlist, "details": [dict(r) for r in seats_free_with_waitlist]},
        {"name": "Every committed CONFIRMED/WAITLISTED result has its registration row (unless cancelled)",
         "passed": results_without_registration == 0, "details": {"missing": int(results_without_registration)}},
    ]
    return {"all_passed": all(c["passed"] for c in checks), "checks": checks,
            "totals": {k: int(v) for k, v in dict(totals).items()}}
