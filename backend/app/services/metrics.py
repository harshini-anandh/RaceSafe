"""Live dashboard metrics (always computed from MySQL + live scheduler state)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from app.core.clock import iso, utcnow


def jain_index(values: list[float]) -> float | None:
    """Jain's fairness index: (sum x)^2 / (n * sum x^2).

    1.0 = perfectly equal shares; 1/n = one participant got everything.
    """
    values = [float(v) for v in values]
    if not values:
        return None
    squares = sum(v * v for v in values)
    if squares == 0:
        return None
    return (sum(values) ** 2) / (len(values) * squares)


def dashboard_metrics(system, conn, scope: str = "catalog") -> dict:
    catalog_only = 1 if scope == "catalog" else 0
    req = conn.execute(
        text(
            "SELECT COUNT(*) AS total, "
            "  COALESCE(SUM(q.status = 'RECEIVED'), 0) AS pending, "
            "  COALESCE(SUM(q.status = 'FAILED'), 0) AS failed, "
            "  COALESCE(SUM(q.result = 'CONFIRMED'), 0) AS result_confirmed, "
            "  COALESCE(SUM(q.result = 'WAITLISTED'), 0) AS result_waitlisted, "
            "  COALESCE(SUM(q.result = 'ALREADY_REGISTERED'), 0) AS result_already, "
            "  COALESCE(SUM(q.attempts > 1), 0) AS retried, "
            "  AVG(TIMESTAMPDIFF(MICROSECOND, q.started_at, q.completed_at)) / 1000 AS avg_processing_ms, "
            "  AVG(TIMESTAMPDIFF(MICROSECOND, q.queued_at, q.started_at)) / 1000 AS avg_queue_wait_ms, "
            "  MAX(TIMESTAMPDIFF(MICROSECOND, q.queued_at, q.started_at)) / 1000 AS max_queue_wait_ms "
            "FROM registration_requests q JOIN courses c ON c.course_id = q.course_id "
            "WHERE (:catalog = 0 OR c.kind = 'CATALOG')"
        ),
        {"catalog": catalog_only},
    ).mappings().first()
    seats = conn.execute(
        text(
            "SELECT COALESCE(SUM(capacity), 0) AS capacity, COALESCE(SUM(confirmed_count), 0) AS confirmed_count, "
            "  COUNT(*) AS courses "
            "FROM courses WHERE (:catalog = 0 OR kind = 'CATALOG')"
        ),
        {"catalog": catalog_only},
    ).mappings().first()
    regs = conn.execute(
        text(
            "SELECT COALESCE(SUM(r.status = 'CONFIRMED'), 0) AS confirmed, "
            "  COALESCE(SUM(r.status = 'WAITLISTED'), 0) AS waitlisted "
            "FROM registrations r JOIN courses c ON c.course_id = r.course_id "
            "WHERE (:catalog = 0 OR c.kind = 'CATALOG')"
        ),
        {"catalog": catalog_only},
    ).mappings().first()

    def num(v, digits=2):
        return None if v is None else round(float(v), digits)

    return {
        "scope": scope,
        "generated_at": iso(utcnow()),
        "requests": {
            "total": int(req["total"]),
            "pending": int(req["pending"]),
            "failed": int(req["failed"]),
            "confirmed": int(req["result_confirmed"]),
            "waitlisted": int(req["result_waitlisted"]),
            "already_registered": int(req["result_already"]),
            "retried": int(req["retried"]),
            "avg_processing_ms": num(req["avg_processing_ms"]),
            "avg_queue_wait_ms": num(req["avg_queue_wait_ms"]),
            "max_queue_wait_ms": num(req["max_queue_wait_ms"]),
        },
        "registrations": {"confirmed": int(regs["confirmed"]), "waitlisted": int(regs["waitlisted"])},
        "seats": {
            "courses": int(seats["courses"]),
            "capacity": int(seats["capacity"]),
            "confirmed_count": int(seats["confirmed_count"]),
            "remaining": int(seats["capacity"]) - int(seats["confirmed_count"]),
        },
        "live": live_state(system),
    }


def live_state(system) -> dict:
    return {
        "scheduler": system.scheduler.snapshot(),
        "workers": system.pool.snapshot(),
        "failures_armed": system.injector.snapshot(),
        "waiting_api_calls": system.completions.pending_count(),
    }


def throughput_series(conn, seconds: int = 120, scope: str = "catalog") -> list[dict]:
    """Requests completed per second over the last `seconds` seconds."""
    now = utcnow().replace(microsecond=0)
    since = now - timedelta(seconds=seconds - 1)
    rows = conn.execute(
        text(
            "SELECT TIMESTAMPDIFF(SECOND, :since, q.completed_at) AS bucket, COUNT(*) AS n, "
            "  SUM(q.status = 'FAILED') AS failed "
            "FROM registration_requests q JOIN courses c ON c.course_id = q.course_id "
            "WHERE q.completed_at >= :since AND (:catalog = 0 OR c.kind = 'CATALOG') "
            "GROUP BY bucket"
        ),
        {"since": since, "catalog": 1 if scope == "catalog" else 0},
    ).mappings().all()
    counts = {int(r["bucket"]): (int(r["n"]), int(r["failed"] or 0)) for r in rows if r["bucket"] is not None}
    series = []
    for i in range(seconds):
        n, failed = counts.get(i, (0, 0))
        series.append({"t": iso(since + timedelta(seconds=i)), "offset_s": i - seconds + 1,
                       "completed": n, "failed": failed})
    return series
