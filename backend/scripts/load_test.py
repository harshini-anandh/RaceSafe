"""Concurrent HTTP load test against a RUNNING RaceSafe backend.

Every scenario fires its requests at the same instant using a
threading.Barrier, then checks the invariants through the API.

Usage (backend running in another terminal):
    python scripts\\load_test.py                       # all scenarios
    python scripts\\load_test.py --scenario capacity   # one scenario
    python scripts\\load_test.py --requests 500 --capacity 10

Scenarios
    capacity      N distinct students -> one course with C seats (default 500 -> 10)
    last-seat     50 students race for a course with exactly 1 seat
    same-uuid     20 concurrent submissions with the SAME request_id
    duplicates    20 concurrent submissions, same student, DIFFERENT request_ids
    multi-course  4 courses x 100 requests at once (independent progress)

Uses only EXPERIMENT courses (LOAD-*), never catalog courses.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
import uuid
from collections import Counter

import httpx

PASS, FAIL = "PASS", "FAIL"


class LoadTester:
    def __init__(self, base_url: str, timeout: float):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.results: list[tuple[str, str, str]] = []

    # ------------------------------------------------------------------ helpers
    def api(self, method: str, path: str, **kwargs):
        with httpx.Client(timeout=self.timeout) as client:
            r = client.request(method, f"{self.base}/api{path}", **kwargs)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text}")
        return r.json()

    def prepare(self, course_id: str, capacity: int) -> None:
        self.api("POST", "/admin/prepare-course", json={"course_id": course_id, "capacity": capacity})

    def burst(self, payloads: list[dict]) -> list[tuple[int, dict, float]]:
        """Send all payloads at the same instant (one thread + client each)."""
        barrier = threading.Barrier(len(payloads))
        out: list = [None] * len(payloads)

        def fire(i: int, body: dict) -> None:
            with httpx.Client(timeout=self.timeout) as client:
                try:
                    barrier.wait(timeout=60)
                except threading.BrokenBarrierError:
                    pass
                t0 = time.perf_counter()
                try:
                    r = client.post(f"{self.base}/api/registrations", params={"wait": "true", "timeout": 120},
                                    json=body)
                    data = r.json()
                    out[i] = (r.status_code, data, (time.perf_counter() - t0) * 1000)
                except Exception as exc:  # network errors are reported, not hidden
                    out[i] = (0, {"error": str(exc)}, (time.perf_counter() - t0) * 1000)

        threads = [threading.Thread(target=fire, args=(i, p)) for i, p in enumerate(payloads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return out

    def check(self, scenario: str, name: str, ok: bool, detail: str = "") -> None:
        self.results.append((scenario, name, PASS if ok else FAIL))
        print(f"  [{PASS if ok else FAIL}] {name}" + (f"  ({detail})" if detail else ""))

    def course(self, course_id: str) -> dict:
        return self.api("GET", f"/courses/{course_id}")

    @staticmethod
    def summarize(responses) -> Counter:
        return Counter(
            (d.get("outcome") or d.get("code") or f"HTTP {code}") if code else "NETWORK_ERROR"
            for code, d, _ in responses
        )

    # ------------------------------------------------------------------ scenarios
    def scenario_capacity(self, n: int, capacity: int) -> None:
        print(f"\n== capacity: {n} distinct students -> LOAD-CAP ({capacity} seats) ==")
        self.prepare("LOAD-CAP", capacity)
        payloads = [{"student_id": f"S{i:04d}", "course_id": "LOAD-CAP", "request_id": str(uuid.uuid4())}
                    for i in range(1, n + 1)]
        t0 = time.perf_counter()
        responses = self.burst(payloads)
        elapsed = time.perf_counter() - t0
        counts = self.summarize(responses)
        latencies = [ms for code, _, ms in responses if code == 200]
        course = self.course("LOAD-CAP")
        print(f"  outcomes: {dict(counts)}")
        print(f"  wall time {elapsed:.2f}s, throughput {n / elapsed:.1f} req/s, "
              f"latency p50 {statistics.median(latencies):.0f} ms, max {max(latencies):.0f} ms" if latencies else "")
        expected_conf = min(capacity, n)
        self.check("capacity", f"exactly {expected_conf} CONFIRMED responses", counts["CONFIRMED"] == expected_conf,
                   f"got {counts['CONFIRMED']}")
        self.check("capacity", f"exactly {n - expected_conf} WAITLISTED responses",
                   counts["WAITLISTED"] == n - expected_conf, f"got {counts['WAITLISTED']}")
        self.check("capacity", "DB confirmed rows never exceed capacity", course["confirmed_actual"] <= capacity,
                   f"{course['confirmed_actual']}/{capacity}")
        self.check("capacity", "confirmed_count == confirmed rows",
                   course["confirmed_count"] == course["confirmed_actual"])
        self.check("capacity", "every request got a final result", sum(counts.values()) == n and all(
            code == 200 for code, _, _ in responses))

    def scenario_last_seat(self) -> None:
        print("\n== last-seat: 50 students race for 1 seat ==")
        self.prepare("LOAD-LAST", 1)
        payloads = [{"student_id": f"S{i:04d}", "course_id": "LOAD-LAST"} for i in range(1, 51)]
        counts = self.summarize(self.burst(payloads))
        print(f"  outcomes: {dict(counts)}")
        self.check("last-seat", "exactly 1 CONFIRMED", counts["CONFIRMED"] == 1, f"got {counts['CONFIRMED']}")
        self.check("last-seat", "49 WAITLISTED", counts["WAITLISTED"] == 49, f"got {counts['WAITLISTED']}")

    def scenario_same_uuid(self) -> None:
        print("\n== same-uuid: 20 concurrent submissions with ONE request_id ==")
        self.prepare("LOAD-IDEM", 5)
        rid = str(uuid.uuid4())
        payloads = [{"student_id": "S0001", "course_id": "LOAD-IDEM", "request_id": rid}] * 20
        responses = self.burst(payloads)
        counts = self.summarize(responses)
        course = self.course("LOAD-IDEM")
        print(f"  outcomes: {dict(counts)}")
        self.check("same-uuid", "all 20 responses report the same result",
                   len(counts) == 1 and counts.get("CONFIRMED") == 20, str(dict(counts)))
        self.check("same-uuid", "only one registration row", course["confirmed_actual"] + course["waitlisted"] == 1)
        replays = sum(1 for _, d, _ in responses if d.get("replayed"))
        self.check("same-uuid", "19 responses were idempotent replays", replays == 19, f"{replays}")

    def scenario_duplicates(self) -> None:
        print("\n== duplicates: same student, 20 different request_ids at once ==")
        self.prepare("LOAD-DUP", 5)
        payloads = [{"student_id": "S0002", "course_id": "LOAD-DUP", "request_id": str(uuid.uuid4())}
                    for _ in range(20)]
        counts = self.summarize(self.burst(payloads))
        course = self.course("LOAD-DUP")
        print(f"  outcomes: {dict(counts)}")
        self.check("duplicates", "1 CONFIRMED + 19 ALREADY_REGISTERED",
                   counts["CONFIRMED"] == 1 and counts["ALREADY_REGISTERED"] == 19)
        self.check("duplicates", "one registration row in MySQL", course["confirmed_actual"] == 1)

    def scenario_multi_course(self) -> None:
        print("\n== multi-course: 4 courses x 100 requests at the same time ==")
        courses = ["LOAD-A", "LOAD-B", "LOAD-C", "LOAD-D"]
        for c in courses:
            self.prepare(c, 25)
        payloads = [{"student_id": f"S{i:04d}", "course_id": c} for c in courses for i in range(101, 201)]
        responses = self.burst(payloads)
        intervals = []  # (start, end, course) of each transaction, from the API's own timestamps
        for code, d, _ in responses:
            if code == 200 and d.get("started_at") and d.get("completed_at"):
                intervals.append((d["started_at"], d["completed_at"], d["course_id"]))
        for c in courses:
            info = self.course(c)
            self.check("multi-course", f"{c}: 25 confirmed, 75 waitlisted",
                       info["confirmed_actual"] == 25 and info["waitlisted"] == 75,
                       f"{info['confirmed_actual']} / {info['waitlisted']}")
        # Sweep over start/end points: how many different courses were inside a
        # transaction at the same moment?
        points = sorted([(s, 1, c) for s, e, c in intervals] + [(e, -1, c) for s, e, c in intervals],
                        key=lambda p: (p[0], p[1]))
        active, peak = Counter(), 0
        for _, delta, c in points:
            active[c] += delta
            peak = max(peak, sum(1 for v in active.values() if v > 0))
        self.check("multi-course", "transactions of different courses overlapped in time",
                   peak > 1, f"max courses processing simultaneously = {peak}")

    def consistency(self) -> None:
        print("\n== database invariants ==")
        report = self.api("GET", "/consistency")
        for c in report["checks"]:
            self.check("invariants", c["name"], c["passed"], "" if c["passed"] else str(c["details"])[:200])


def main() -> int:
    parser = argparse.ArgumentParser(description="RaceSafe concurrent load test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--scenario", default="all",
                        choices=["all", "capacity", "last-seat", "same-uuid", "duplicates", "multi-course"])
    parser.add_argument("--requests", type=int, default=500, help="requests for the capacity scenario (<= 600)")
    parser.add_argument("--capacity", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()

    tester = LoadTester(args.base_url, args.timeout)
    try:
        health = tester.api("GET", "/health")
    except Exception as exc:
        print(f"Cannot reach the backend at {args.base_url}: {exc}\nStart it first (see README).")
        return 2
    print(f"Backend OK - scheduler {health['scheduler_mode']}, {health['workers']} workers, "
          f"database ok={health['database']['ok']}")

    s = args.scenario
    if s in ("all", "capacity"):
        tester.scenario_capacity(min(args.requests, 600), args.capacity)
    if s in ("all", "last-seat"):
        tester.scenario_last_seat()
    if s in ("all", "same-uuid"):
        tester.scenario_same_uuid()
    if s in ("all", "duplicates"):
        tester.scenario_duplicates()
    if s in ("all", "multi-course"):
        tester.scenario_multi_course()
    tester.consistency()

    failed = [r for r in tester.results if r[2] == FAIL]
    print(f"\n{len(tester.results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
