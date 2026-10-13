"""Helpers that prepare isolated experiment courses and reset demo data."""
from __future__ import annotations

from sqlalchemy import bindparam, text

from app.core.errors import ConflictError, NotFoundError


def prepare_experiment_courses(conn, courses: list[tuple[str, str, int]]) -> None:
    """Create/reset EXPERIMENT courses: (course_id, name, capacity).

    Deletes their registrations and sets confirmed_count = 0. Refuses to touch
    CATALOG courses, so experiments can never wipe real student data.
    """
    ids = sorted(c[0] for c in courses)
    existing = conn.execute(
        text("SELECT course_id, kind FROM courses WHERE course_id IN :ids FOR UPDATE")
        .bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    ).mappings().all()
    for row in existing:
        if row["kind"] != "EXPERIMENT":
            raise ConflictError(f"{row['course_id']} is a catalog course and cannot be used for experiments")
    conn.execute(
        text("DELETE FROM registrations WHERE course_id IN :ids").bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    )
    for course_id, name, capacity in courses:
        conn.execute(
            text(
                "INSERT INTO courses (course_id, course_name, capacity, confirmed_count, kind) "
                "VALUES (:cid, :name, :cap, 0, 'EXPERIMENT') "
                "ON DUPLICATE KEY UPDATE course_name = :name, confirmed_count = 0, capacity = :cap"
            ),
            {"cid": course_id, "name": name, "cap": capacity},
        )


def pending_requests_for(conn, course_ids: list[str]) -> int:
    return conn.execute(
        text("SELECT COUNT(*) FROM registration_requests WHERE status = 'RECEIVED' AND course_id IN :ids")
        .bindparams(bindparam("ids", expanding=True)),
        {"ids": course_ids},
    ).scalar_one()


def all_student_ids(conn) -> list[str]:
    ids = [r[0] for r in conn.execute(text("SELECT student_id FROM students ORDER BY student_id")).all()]
    if not ids:
        raise NotFoundError("No students found - run scripts/init_db.py to load the sample data")
    return ids


def student_ids(conn, limit: int) -> list[str]:
    ids = [r[0] for r in conn.execute(
        text("SELECT student_id FROM students ORDER BY student_id LIMIT :n"), {"n": limit}
    ).all()]
    if len(ids) < limit:
        raise NotFoundError(f"Need {limit} students but only {len(ids)} exist - re-run scripts/init_db.py")
    return ids


def reset_all_data(conn) -> dict:
    """Delete all registrations, requests, events, experiments and lab rows.

    Students and catalog courses are kept; confirmed counts go back to 0.
    Experiment courses are removed.
    """
    counts = {}
    for table in ("registration_events", "registrations", "registration_requests", "experiments",
                  "lab_registrations", "lab_courses"):
        counts[table] = conn.execute(text(f"DELETE FROM {table}")).rowcount  # fixed table names only
    counts["experiment_courses"] = conn.execute(text("DELETE FROM courses WHERE kind = 'EXPERIMENT'")).rowcount
    conn.execute(text("UPDATE courses SET confirmed_count = 0"))
    return counts
