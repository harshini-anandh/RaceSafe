"""FairQueue experiment: Global FIFO baseline vs Contention-Aware FairQueue.

Both modes run the SAME workload (same students, same courses, same shuffled
arrival order from a fixed random seed):

1. Reset the isolated experiment courses XP-1..XP-n (never catalog courses).
2. Pause the scheduler, persist every request through the normal intake path,
   enqueue them in workload order, then release them all at once (a burst,
   like registration opening). release time = arrival time for every request.
3. Workers process them through the normal seat-allocation transaction.
4. Measure from the committed DB rows:
     wait        = started_at   - released_at   (time spent queued)
     turnaround  = completed_at - released_at
     completion  = last completed_at - released_at
     throughput  = completed requests / completion time
     fairness    = Jain's index over the number of requests each course had
                   dispatched while ALL courses still had requests waiting
                   (the "contention window"). Round-robin aims for equal service
                   among backlogged courses, so this is the property it claims.
                   1.0 = equal service, 1/n = one course got all the workers.
Nothing here is hard-coded: whichever mode is better shows up in the numbers.
"""
from __future__ import annotations

import json
import logging
import random
import statistics
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, wait as wait_futures

from sqlalchemy import text

from app.core.clock import iso, ms_between, utcnow
from app.core.errors import ConflictError, NotFoundError, RaceSafeError
from app.models.enums import SchedulerMode
from app.services.demo_data import all_student_ids, pending_requests_for, prepare_experiment_courses
from app.services.metrics import jain_index

logger = logging.getLogger("racesafe.experiments")

MODES = (SchedulerMode.GLOBAL_FIFO.value, SchedulerMode.FAIR_QUEUE.value)


def allocate_counts(total: int, weights: list[float]) -> list[int]:
    """Split `total` requests by weights (largest remainder, every course >= 1)."""
    s = sum(weights)
    raw = [total * w / s for w in weights]
    counts = [max(1, int(x)) for x in raw]
    order = sorted(range(len(weights)), key=lambda i: raw[i] - int(raw[i]), reverse=True)
    i = 0
    while sum(counts) < total:
        counts[order[i % len(order)]] += 1
        i += 1
    while sum(counts) > total:
        j = max(range(len(counts)), key=lambda k: counts[k])
        counts[j] -= 1
    return counts


def build_weights(config: dict) -> list[float]:
    n = config["course_count"]
    dist = config["distribution"]
    if dist == "uniform":
        return [1.0] * n
    if dist == "custom":
        weights = config.get("weights") or []
        if len(weights) != n or any(w <= 0 for w in weights):
            raise RaceSafeError("custom distribution needs one positive weight per course")
        return [float(w) for w in weights]
    hot = config["hot_share"]
    return [hot] + [(1 - hot) / (n - 1)] * (n - 1)


def build_workload(config: dict, students: list[str]) -> tuple[list[dict], list[dict]]:
    weights = build_weights(config)
    counts = allocate_counts(config["total_requests"], weights)
    rng = random.Random(config["seed"])
    max_w = max(weights)
    courses, items = [], []
    for index, (w, count) in enumerate(zip(weights, counts), start=1):
        course_id = f"XP-{index}"
        if count > len(students):
            raise RaceSafeError(f"{course_id} would need {count} distinct students, only {len(students)} exist")
        label = "hot" if (config["distribution"] != "uniform" and w == max_w) else "normal"
        courses.append({"course_id": course_id, "name": f"Experiment course {index} ({label})",
                        "requests": count, "weight": round(w / sum(weights), 4), "label": label})
        for sid in rng.sample(students, count):
            items.append({"student_id": sid, "course_id": course_id})
    rng.shuffle(items)  # same arrival order for every run in the group
    return courses, items


class ExperimentManager:
    def __init__(self, system):
        self.system = system
        self._lock = threading.Lock()
        self._groups: dict[str, dict] = {}  # live progress of running/recent groups

    # ------------------------------------------------------------------ public API
    def start(self, config: dict) -> dict:
        system = self.system
        if not system.experiment_lock.acquire(blocking=False):
            raise ConflictError("Another experiment or race-lab run is in progress")
        try:
            if not system.scheduler.is_idle():
                raise ConflictError("The queue still has pending requests; wait for them to finish first")
            with system.engine.connect() as conn:
                students = all_student_ids(conn)
            courses, workload = build_workload(config, students)
            modes = [config["mode"]] if config["mode"] in MODES else list(MODES)
            plan = [m for _ in range(config["repeats"]) for m in modes]
            group_id = str(uuid.uuid4())
            progress = {
                "group_id": group_id, "status": "RUNNING", "config": config, "courses": courses,
                "runs_total": len(plan), "runs_done": 0, "current_mode": None,
                "current_done": 0, "current_total": len(workload), "experiment_ids": [],
                "error": None, "started_at": iso(utcnow()),
            }
            with self._lock:
                self._groups[group_id] = progress
            thread = threading.Thread(target=self._run_group, args=(group_id, config, courses, workload, plan),
                                      name=f"experiment-{group_id[:8]}", daemon=True)
            thread.start()
            return dict(progress)
        except BaseException:
            system.experiment_lock.release()
            raise

    def get_group(self, group_id: str) -> dict:
        with self.system.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT experiment_id, scheduler_mode, run_index, status, config, summary, error, "
                    "created_at, finished_at FROM experiments "
                    "WHERE group_id = :gid AND experiment_type = 'FAIRQUEUE' ORDER BY experiment_id"
                ),
                {"gid": group_id},
            ).mappings().all()
        with self._lock:
            live = dict(self._groups[group_id]) if group_id in self._groups else None
        if not rows and live is None:
            raise NotFoundError(f"Unknown experiment group {group_id}")
        runs = [self._run_row(r) for r in rows]
        if live is None:
            config = runs[0]["config"]
            live = {"group_id": group_id, "status": self._group_status(runs), "config": config,
                    "courses": config.get("courses", []), "runs_total": len(runs),
                    "runs_done": sum(1 for r in runs if r["status"] != "RUNNING")}
        live["runs"] = runs
        live["comparison"] = compare_runs(runs)
        return live

    def list_groups(self, limit: int = 20) -> list[dict]:
        with self.system.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT group_id, MIN(created_at) AS created_at, COUNT(*) AS runs, "
                    "  GROUP_CONCAT(DISTINCT scheduler_mode ORDER BY scheduler_mode) AS modes, "
                    "  SUM(status = 'COMPLETED') AS completed, SUM(status = 'FAILED') AS failed, "
                    "  MAX(experiment_id) AS last_id "
                    "FROM experiments WHERE experiment_type = 'FAIRQUEUE' AND group_id IS NOT NULL "
                    "GROUP BY group_id ORDER BY last_id DESC LIMIT :n"
                ),
                {"n": limit},
            ).mappings().all()
        return [{"group_id": r["group_id"], "created_at": iso(r["created_at"]), "runs": int(r["runs"]),
                 "modes": (r["modes"] or "").split(","), "completed": int(r["completed"] or 0),
                 "failed": int(r["failed"] or 0)} for r in rows]

    # ------------------------------------------------------------------ worker thread
    def _update(self, group_id: str, **values) -> None:
        with self._lock:
            self._groups[group_id].update(values)

    def _run_group(self, group_id, config, courses, workload, plan) -> None:
        system = self.system
        previous_mode = system.scheduler.mode
        try:
            for run_index, mode in enumerate(plan, start=1):
                self._update(group_id, current_mode=mode, current_done=0)
                exp_id = self._run_one(group_id, run_index, mode, config, courses, workload)
                with self._lock:
                    self._groups[group_id]["experiment_ids"].append(exp_id)
                    self._groups[group_id]["runs_done"] = run_index
            self._update(group_id, status="COMPLETED", current_mode=None)
        except Exception as exc:
            logger.exception("Experiment group %s failed", group_id)
            self._update(group_id, status="FAILED", error=str(exc))
        finally:
            system.scheduler.set_mode(previous_mode)
            system.scheduler.resume()
            system.experiment_lock.release()

    def _run_one(self, group_id, run_index, mode, config, courses, workload) -> int:
        system = self.system
        course_ids = [c["course_id"] for c in courses]
        stored_config = {**config, "courses": courses}
        with system.engine.begin() as conn:
            if pending_requests_for(conn, course_ids):
                raise ConflictError("Experiment courses still have pending requests")
            prepare_experiment_courses(conn, [(c["course_id"], c["name"], config["capacity"]) for c in courses])
            exp_id = conn.execute(
                text(
                    "INSERT INTO experiments (group_id, experiment_type, scheduler_mode, run_index, status, "
                    "config, created_at) VALUES (:gid, 'FAIRQUEUE', :mode, :idx, 'RUNNING', :config, :now)"
                ),
                {"gid": group_id, "mode": mode, "idx": run_index, "config": json.dumps(stored_config),
                 "now": utcnow()},
            ).lastrowid

        scheduler = system.scheduler
        try:
            if not scheduler.wait_until_idle(timeout=30):
                raise ConflictError("Scheduler did not become idle")
            scheduler.pause()
            scheduler.set_mode(mode)
            scheduler.reset_stats()

            # Persist all requests through the normal intake path (in parallel),
            # but do not enqueue yet.
            def persist(item):
                return system.intake.submit(item["student_id"], item["course_id"], experiment_id=exp_id,
                                            work_ms=config["work_ms"], enqueue=False)
            with ThreadPoolExecutor(max_workers=16, thread_name_prefix="xp-intake") as pool:
                results = list(pool.map(persist, workload))
            waiters = [system.completions.add_waiter(r.request_id) for r in results]
            for r in results:  # identical arrival order for both modes
                scheduler.submit(r.job, force=True)
            released_at = utcnow()
        finally:
            scheduler.resume()  # the burst is released here
        with system.engine.begin() as conn:
            conn.execute(text("UPDATE experiments SET released_at = :t WHERE experiment_id = :id"),
                         {"t": released_at, "id": exp_id})

        done = 0
        pending = set(waiters)
        while pending:
            finished, pending = wait_futures(pending, timeout=0.5)
            done += len(finished)
            self._update(group_id, current_done=done)
            if (utcnow() - released_at).total_seconds() > 900:
                raise RaceSafeError("Experiment timed out after 15 minutes")

        stats = scheduler.snapshot()
        summary = summarize_run(system.engine, exp_id, released_at, courses)
        summary["peak_workers_busy"] = stats["peak_in_flight"]
        summary["peak_courses_in_flight"] = stats["peak_courses_in_flight"]
        with system.engine.begin() as conn:
            conn.execute(
                text("UPDATE experiments SET status = 'COMPLETED', summary = :s, finished_at = :t "
                     "WHERE experiment_id = :id"),
                {"s": json.dumps(summary), "t": utcnow(), "id": exp_id},
            )
        return exp_id

    @staticmethod
    def _run_row(r) -> dict:
        def load(v):
            return json.loads(v) if isinstance(v, str) else v
        return {"experiment_id": r["experiment_id"], "scheduler_mode": r["scheduler_mode"],
                "run_index": r["run_index"], "status": r["status"], "config": load(r["config"]) or {},
                "summary": load(r["summary"]), "error": r["error"], "created_at": iso(r["created_at"]),
                "finished_at": iso(r["finished_at"])}

    @staticmethod
    def _group_status(runs: list[dict]) -> str:
        if any(r["status"] == "FAILED" for r in runs):
            return "FAILED"
        if any(r["status"] == "RUNNING" for r in runs):
            return "INTERRUPTED"
        return "COMPLETED"


# ---------------------------------------------------------------------- analysis
def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def summarize_run(engine, exp_id: int, released_at, courses: list[dict]) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT course_id, status, result, attempts, started_at, completed_at "
                "FROM registration_requests WHERE experiment_id = :id"
            ),
            {"id": exp_id},
        ).mappings().all()

    per_course = {}
    starts_by_course: dict[str, list] = {}
    all_waits, all_turnaround, completed_times = [], [], []
    totals = {"requests": len(rows), "confirmed": 0, "waitlisted": 0, "already_registered": 0, "failed": 0}
    for c in courses:
        per_course[c["course_id"]] = {"course_id": c["course_id"], "label": c["label"], "requests": 0,
                                      "confirmed": 0, "waitlisted": 0, "failed": 0, "_waits": [], "_turn": []}
    for r in rows:
        pc = per_course[r["course_id"]]
        pc["requests"] += 1
        if r["status"] != "COMPLETED":
            pc["failed"] += 1
            totals["failed"] += 1
            continue
        key = {"CONFIRMED": "confirmed", "WAITLISTED": "waitlisted"}.get(r["result"])
        if key:
            pc[key] += 1
            totals[key] += 1
        else:
            totals["already_registered"] += 1
        wait_ms = max(0.0, ms_between(released_at, r["started_at"]))
        turn_ms = max(0.0, ms_between(released_at, r["completed_at"]))
        pc["_waits"].append(wait_ms)
        pc["_turn"].append(turn_ms)
        all_waits.append(wait_ms)
        all_turnaround.append(turn_ms)
        completed_times.append(r["completed_at"])
        starts_by_course.setdefault(r["course_id"], []).append(r["started_at"])

    course_rows = []
    for pc in per_course.values():
        waits, turn = pc.pop("_waits"), pc.pop("_turn")
        pc.update(
            avg_wait_ms=round(statistics.fmean(waits), 2) if waits else None,
            max_wait_ms=round(max(waits), 2) if waits else None,
            p95_wait_ms=round(_percentile(waits, 0.95), 2) if waits else None,
            avg_turnaround_ms=round(statistics.fmean(turn), 2) if turn else None,
            max_turnaround_ms=round(max(turn), 2) if turn else None,
        )
        course_rows.append(pc)

    completion_ms = max(0.0, ms_between(released_at, max(completed_times))) if completed_times else None
    completed = len(completed_times)

    # Fairness: service each course received during the contention window.
    window_end = min(max(s) for s in starts_by_course.values()) if starts_by_course else None
    served = {c: sum(1 for s in starts if s <= window_end) for c, starts in starts_by_course.items()}
    fairness = jain_index(list(served.values())) if len(served) > 1 else None

    return {
        "totals": totals,
        "completed": completed,
        "completion_time_ms": round(completion_ms, 2) if completion_ms is not None else None,
        "throughput_rps": round(completed / (completion_ms / 1000), 2) if completion_ms else None,
        "avg_wait_ms": round(statistics.fmean(all_waits), 2) if all_waits else None,
        "max_wait_ms": round(max(all_waits), 2) if all_waits else None,
        "avg_turnaround_ms": round(statistics.fmean(all_turnaround), 2) if all_turnaround else None,
        "fairness": {
            "jain_index": round(fairness, 4) if fairness is not None else None,
            "window_ms": round(ms_between(released_at, window_end), 2) if window_end else None,
            "served_in_window": served,
            "definition": "Jain's index over requests dispatched per course while every course still "
                          "had requests waiting (1.0 = equal service, 1/n = one course took all workers)",
        },
        "per_course": course_rows,
    }


def compare_runs(runs: list[dict]) -> dict | None:
    """Average each metric per scheduler mode (over repeats)."""
    done = [r for r in runs if r["status"] == "COMPLETED" and r["summary"]]
    if not done:
        return None

    def mean(values):
        values = [v for v in values if v is not None]
        return round(statistics.fmean(values), 3) if values else None

    def stdev(values):
        values = [v for v in values if v is not None]
        return round(statistics.stdev(values), 3) if len(values) > 1 else None

    by_mode: dict[str, dict] = {}
    for mode in MODES:
        mode_runs = [r["summary"] for r in done if r["scheduler_mode"] == mode]
        if not mode_runs:
            continue
        course_ids = [c["course_id"] for c in mode_runs[0]["per_course"]]
        per_course = []
        for cid in course_ids:
            rows = [next(c for c in s["per_course"] if c["course_id"] == cid) for s in mode_runs]
            per_course.append({
                "course_id": cid, "label": rows[0]["label"], "requests": rows[0]["requests"],
                "avg_wait_ms": mean([r["avg_wait_ms"] for r in rows]),
                "max_wait_ms": mean([r["max_wait_ms"] for r in rows]),
                "avg_turnaround_ms": mean([r["avg_turnaround_ms"] for r in rows]),
                "confirmed": mean([r["confirmed"] for r in rows]),
                "waitlisted": mean([r["waitlisted"] for r in rows]),
                "failed": mean([r["failed"] for r in rows]),
            })
        by_mode[mode] = {
            "runs": len(mode_runs),
            "completion_time_ms": mean([s["completion_time_ms"] for s in mode_runs]),
            "completion_time_ms_stdev": stdev([s["completion_time_ms"] for s in mode_runs]),
            "throughput_rps": mean([s["throughput_rps"] for s in mode_runs]),
            "avg_wait_ms": mean([s["avg_wait_ms"] for s in mode_runs]),
            "max_wait_ms": mean([s["max_wait_ms"] for s in mode_runs]),
            "jain_index": mean([s["fairness"]["jain_index"] for s in mode_runs]),
            "confirmed": mean([s["totals"]["confirmed"] for s in mode_runs]),
            "waitlisted": mean([s["totals"]["waitlisted"] for s in mode_runs]),
            "failed": mean([s["totals"]["failed"] for s in mode_runs]),
            "peak_courses_in_flight": mean([s.get("peak_courses_in_flight") for s in mode_runs]),
            "per_course": per_course,
        }
    return {"by_mode": by_mode}
