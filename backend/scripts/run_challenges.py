"""Run the KO-06 live technical challenges against a RUNNING backend.

Usage (backend running in another terminal, venv active):
    python scripts\\run_challenges.py            # C1..C5
    python scripts\\run_challenges.py C2 C4      # selected challenges
    python scripts\\run_challenges.py --verbose  # also print every step
"""
from __future__ import annotations

import argparse
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Run KO-06 challenges C1..C5")
    parser.add_argument("challenges", nargs="*", default=["C1", "C2", "C3", "C4", "C5"])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    failed = 0
    with httpx.Client(base_url=args.base_url.rstrip("/") + "/api", timeout=600) as client:
        try:
            client.get("/health").raise_for_status()
        except Exception as exc:
            print(f"Cannot reach the backend at {args.base_url}: {exc}")
            return 2
        for cid in args.challenges:
            r = client.post(f"/challenges/{cid.upper()}")
            if r.status_code != 200:
                print(f"\n{cid}: HTTP {r.status_code} {r.text}")
                failed += 1
                continue
            rep = r.json()
            ok = rep["passed"]
            failed += 0 if ok else 1
            passed = sum(c["passed"] for c in rep["checks"])
            print(f"\n=== {rep['code']} [{rep['difficulty']}] {rep['title']}: "
                  f"{'PASS' if ok else 'FAIL'} ({passed}/{len(rep['checks'])} checks, {rep['duration_ms']:.0f} ms)")
            if args.verbose:
                for step in rep["steps"]:
                    print(f"  - {step['message']}")
            for c in rep["checks"]:
                if args.verbose or not c["passed"]:
                    print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['name']}: "
                          f"expected {c['expected']}, actual {c['actual']}")
    print(f"\n{len(args.challenges) - failed} of {len(args.challenges)} challenges passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
