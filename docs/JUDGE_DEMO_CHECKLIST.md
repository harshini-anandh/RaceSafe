# RaceSafe — Judge Demonstration Checklist (≈ 8–10 minutes)

**Before the judges arrive**
- [ ] MySQL running (`Get-Service MySQL80` → Running)
- [ ] Terminal 1: `cd backend; .\.venv\Scripts\Activate.ps1; uvicorn app.main:app --host 127.0.0.1 --port 8000`
- [ ] Terminal 2: `cd frontend; npm run dev` → browser at http://localhost:5173
- [ ] Terminal 3 ready: `cd backend; .\.venv\Scripts\Activate.ps1`
- [ ] Optional clean slate: *Architecture & Demo → Reset demo data* (type RESET)
- [ ] Top bar shows **API online · MySQL connected · Scheduler FairQueue · Workers 8**

| # | Step | Where | What to point at |
|---|------|-------|------------------|
| 1 | Architecture | *Architecture & Demo* | Request flow boxes with live queues and W1–W8 workers; lock order request → course → registration; OS concepts table. |
| 2 | Unsafe race | *Race Condition Lab* | Capacity 10, 500 students, 50 clients. Tick **Force worst-case interleaving**. *Run unsafe* → "Overbooked by N", counter ≠ rows (lost updates). Explain check-then-act with the code panel. |
| 3 | Safe, 500 requests | *Race Condition Lab* | *Run RaceSafe* → **10 / 10 confirmed, 490 waitlisted, 0 duplicates, 0 overbooked**. Optionally raise *double-click share* to show `ALREADY_REGISTERED`. |
| 4 | Capacity never exceeded | *Overview* | *Correctness invariants* card — all checks pass. Switch scope to *Include experiments*. |
| 5 | FairQueue vs FIFO | *FairQueue Experiment* | *Compare both*, 500 requests, 5 courses, skewed 70%, work 5 ms → *Run experiment*. Progress bar shows both runs. |
| 6 | Measured waiting time | *FairQueue Experiment* | *Average wait per course* chart (blue FIFO, orange FairQueue): quiet courses wait less with FairQueue; hot course about the same; read the "What the measurements say" box and Jain's index. These are measured, not hard-coded. |
| 7 | Worker failure | *Failure Recovery* | *Worker failure before commit*, 1 failure → *Inject & submit*. Timeline: `WORKER_FAILURE → TRANSACTION_ROLLED_BACK → RETRY_SCHEDULED → WORKER_STARTED (attempt 2) → TRANSACTION_COMMITTED → REQUEST_CONFIRMED`. Repeat with 3 failures → `REQUEST_FAILED`, no seat taken. |
| 8 | Lost response | *Failure Recovery* | *Inject lost response & submit* → HTTP 504. *Retry same ID* → CONFIRMED with "idempotent replay". *Retry with new ID* → ALREADY_REGISTERED. Only one `TRANSACTION_COMMITTED`. |
| 8b | Restart recovery | *Failure Recovery* | 1. Pause workers → 2. Submit 5 requests → 3. Simulate crash + restart → "5 lost from memory, 5 rebuilt from MySQL". (Or do a real Ctrl+C restart of uvicorn.) |
| 9 | Cancel & promote | *Courses & Waitlists* | CS301 (5 seats): *Submit requests* with 10 → 5 confirmed, 5 waitlisted. Cancel a confirmed student → waitlist #1 promoted in the same transaction; count stays 5/5; activity feed shows `WAITLIST_PROMOTED`. |
| 10 | Results | *Overview* + Terminal 3 | Final metrics; run `pytest` (show pass count) and `python scripts\load_test.py --scenario capacity` (500 HTTP requests → 10 confirmed, 490 waitlisted). |

**If the organizers assign a KO-06 challenge (C1–C5):** in Terminal 3 run
`python scripts\run_challenges.py C4 -v` (replace C4 with the assigned challenge). It prints every step and
each check as expected vs actual; the request IDs it uses can be pasted into the Failure Recovery timeline filter.

**Key sentences for judges**
- "The database is the final authority: a row lock per course plus UNIQUE and CHECK constraints. The scheduler only decides order."
- "No global lock — different courses lock different rows and run in parallel."
- "FairQueue = per-course FIFO queues served round-robin, so a burst for one course cannot take all eight workers."
- "Request IDs make retries idempotent; the UNIQUE constraint stops a second registration even with a new ID."
- "Python threads fit because the work is I/O-bound waiting on MySQL; the GIL is released while waiting."

**Automated vs demonstrated:** `pytest` and `load_test.py` are automated pass/fail checks; the dashboard runs
above are live demonstrations whose numbers vary slightly from run to run.
