# RaceSafe — A Fair and Fault-Tolerant Course Registration System

**Kernel Ops OS Hackathon · Team Ctrl Alt Del · PS OSH001 (Race-Safe Course Registration Engine) · Theme: Process & Thread Management**

RaceSafe lets 500+ simulated students register for limited-seat courses **at the same moment** without
overbooking, without one global lock, with FIFO waitlists, and with safe recovery when a worker fails.

| Feature | How it works |
|---|---|
| Race-safe seats | Each seat is allocated in one MySQL transaction that locks the course row (`SELECT … FOR UPDATE`). `UNIQUE (student_id, course_id)` and `CHECK (confirmed_count <= capacity)` are the backstop. |
| No global lock | Locks are per course row, so different courses register in parallel. |
| **Contention-Aware FairQueue** (main idea) | One FIFO queue per course, served round-robin, at most one request per course in flight. Compared against a single **Global FIFO** queue on identical workloads. |
| FIFO waitlist | Full course → `WAITLISTED` with a per-course sequence number. Cancelling a seat promotes the first waitlisted student **in the same transaction**. |
| Fault tolerance | Request UUIDs make retries idempotent; worker failures roll back and retry (max 3 attempts); pending requests are rebuilt from MySQL after a restart. |
| Race Condition Lab | A deliberately unsafe check-then-act implementation (isolated tables) vs the real path, same burst. |
| Live dashboard | React + Recharts, every number read from the running backend and MySQL. |

---

## 1. Architecture

```
 Student UI / load-test script
          │ HTTP (request_id = UUID)
          ▼
 ┌───────────────────────── FastAPI backend (one Python process) ─────────────────────────┐
 │ 1 Registration API ──► 2 Validation & idempotency ──► 3 Per-course FIFO queues         │
 │                         (INSERT request row RECEIVED)        │                          │
 │                                                              ▼                          │
 │                                     4 Scheduler: FairQueue (round-robin) or Global FIFO │
 │                                                              │ Condition variable       │
 │                                                              ▼                          │
 │                                     5 Bounded worker pool (8 threads)                   │
 │                                                              │                          │
 │ 9 Metrics ◄── 8 Event tracker ◄── 6/7 Seat-allocation & cancellation transactions ──────┼──► MySQL 8 (InnoDB)
 └─────────────────────────────────────────────────────────────────────────────────────────┘    source of truth
          │ GET /api/metrics, /api/events …
          ▼
 React dashboard (Overview · Registration · Courses · FairQueue · Race Lab · Recovery · Architecture)
```

MySQL is the **final authority** on seats; the scheduler only decides the *order* in which requests reach
workers. More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Why Python threads?** The GIL means Python threads do not run Python code on several CPU cores at once.
That is fine here: each worker spends most of its time **waiting on MySQL** (network I/O and row locks), and
the GIL is released while waiting. A fixed pool of 8 threads also caps the number of DB connections.

## 2. Project structure

```
RaceSafe/
├── backend/
│   ├── app/
│   │   ├── main.py                 FastAPI app, startup/shutdown, error handlers
│   │   ├── system.py               wires everything together + restart recovery
│   │   ├── api/                    REST endpoints (routes_*.py)
│   │   ├── core/                   settings (.env), errors, UTC clock
│   │   ├── database/               SQLAlchemy engine, schema initialisation
│   │   ├── models/enums.py         statuses and event names (with descriptions)
│   │   ├── schemas/api.py          Pydantic request/response models
│   │   ├── scheduler/fair_queue.py Global FIFO + FairQueue scheduler (condition variable)
│   │   ├── workers/                worker pool, failure injection
│   │   └── services/               seat allocation, intake/idempotency, cancellation,
│   │                               queries, metrics, experiments, race lab
│   ├── scripts/init_db.py          creates the database + sample data
│   ├── scripts/load_test.py        500-request concurrent HTTP load test
│   ├── tests/                      pytest (unit + MySQL integration)
│   ├── requirements.txt
│   └── .env.example
├── frontend/                       React + Vite + Recharts dashboard
│   └── src/{pages,components,hooks,services,styles,utils}
├── database/schema.sql, seed.sql
└── docs/ARCHITECTURE.md, JUDGE_DEMO_CHECKLIST.md
```

---

## 3. Windows setup (VS Code + PowerShell)

You will use **two terminals**: one runs the backend, the other runs the frontend.
In VS Code open the folder `C:\Users\<you>\OneDrive\Desktop\RaceSafe`, then **Terminal → New Terminal**
(PowerShell). Use the **+** button in the terminal panel to open a second one.

### 3.1 Prerequisites and version checks

```powershell
python --version                       # need 3.11 or newer
node --version                         # need 18 or newer (20 LTS recommended)
npm --version
Get-Service MySQL80                    # Status should be "Running"
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" --version   # need 8.0.16+
```

Missing something?
* **Node.js:** `winget install OpenJS.NodeJS.LTS`, then **close and reopen VS Code** so `node` is on PATH.
* **MySQL not running:** open PowerShell **as Administrator** and run `Start-Service MySQL80`.
* **Python:** install 3.11+ from python.org (tick "Add python.exe to PATH").

### 3.2 Backend (Terminal 1)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell says *running scripts is disabled*, run this once and try again:
`Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

Your prompt now starts with `(.venv)`. Install the dependencies:

```powershell
pip install -r requirements.txt
```

### 3.3 Configure MySQL credentials (.env)

```powershell
Copy-Item .env.example .env
notepad .env
```

Set `DB_PASSWORD=` to your MySQL root password (or another user that may create databases) and save.
`.env` is listed in `.gitignore` — never commit it. Passwords are **never** stored in source code.

### 3.4 Create the database

```powershell
python scripts\init_db.py
```

Expected: `Database 'racesafe' is ready with 600 students and 8 courses.`
(`python scripts\init_db.py --reset` drops and recreates it.)

### 3.5 Start FastAPI (keep this terminal open)

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Check http://127.0.0.1:8000/api/health (should say `"status": "ok"`) and the interactive API docs at
http://127.0.0.1:8000/docs.

### 3.6 Frontend (Terminal 2)

```powershell
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. The API address defaults to `http://127.0.0.1:8000`; to change it,
`Copy-Item .env.example .env.local` in `frontend/` and edit `VITE_API_BASE_URL`.

> Tip: OneDrive may try to sync `node_modules`. If `npm install` is slow, pause OneDrive syncing while installing.

### 3.7 Automated tests (Terminal 3, venv active)

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pytest                       # all tests
pytest -m "not mysql"        # only the unit tests (no database needed)
```

Integration tests use a **separate** database (`TEST_DB_NAME`, default `racesafe_test`) that is dropped
and recreated on every run, so your demo data is never touched. If MySQL is unreachable they are reported
as *skipped*, not passed.

### 3.8 500-request load test (backend must be running)

```powershell
python scripts\load_test.py                         # all scenarios
python scripts\load_test.py --scenario capacity     # 500 students -> 10 seats
python scripts\load_test.py --requests 500 --capacity 10
```

Each scenario releases all its HTTP requests at once with a `threading.Barrier` and then checks the
invariants through the API. It only uses its own `LOAD-*` experiment courses.

---

## 4. Demonstrating the features

The full judge script is in [docs/JUDGE_DEMO_CHECKLIST.md](docs/JUDGE_DEMO_CHECKLIST.md). In short:

| Feature | Where | What to do |
|---|---|---|
| Race condition | **Race Condition Lab** | Capacity 10, 500 students → *Run unsafe* (tick *force worst-case interleaving* for a guaranteed demo) → *Run RaceSafe*. Compare overbooking/duplicates. |
| FairQueue | **FairQueue Experiment** | *Compare both*, skewed demand, 500 requests → *Run experiment*. Read the per-course wait chart. |
| Worker failure | **Failure Recovery** | *Inject & submit* with 1 failure → timeline shows `WORKER_FAILURE → TRANSACTION_ROLLED_BACK → RETRY_SCHEDULED → TRANSACTION_COMMITTED`. With 3 failures → `REQUEST_FAILED`. |
| Lost response | **Failure Recovery** | *Inject lost response & submit* → HTTP 504 → *Retry same ID* (stored result, `idempotent replay`) → *Retry with new ID* (`ALREADY_REGISTERED`). |
| Restart recovery | **Failure Recovery** | Pause workers → submit requests → *Simulate crash + restart*. Or pause, submit, press **Ctrl+C** in the backend terminal and start uvicorn again: the log prints `Restart recovery requeued N pending requests`. |
| Cancel & promote | **Courses & Waitlists** | CS301 has 5 seats: *Submit requests* with 10 → cancel a confirmed student → waitlist #1 becomes confirmed. |

## 5. REST API

All endpoints are under `/api`; full schemas at `/docs`. **Demo-only** endpoints return `403` unless
`RACESAFE_ENV=development`; destructive ones also require a request from `localhost`.

| Method & path | Purpose |
|---|---|
| `GET /health` | Backend, DB and scheduler status |
| `GET /courses` · `GET /courses/{id}` | Courses, capacity, confirmed, waitlist size (`include_experiment=true` for experiment courses) |
| `GET /courses/{id}/registrations` · `GET /courses/{id}/waitlist` | Confirmed students and FIFO waitlist |
| `GET /students?search=` · `GET /students/{id}/registrations` | Sample students; a student's registrations + waitlist positions |
| `POST /registrations?wait=true&timeout=30` | Submit `{student_id, course_id, request_id}`. `200` final result, `202` still processing, `404` unknown ID, `409` request_id reused for other data, `503` queue full |
| `GET /requests/{request_id}` · `GET /requests/{request_id}/events` | Status / events of one request |
| `POST /registrations/cancel` | Cancel `{student_id, course_id}`; promotes the first waitlisted student atomically |
| `GET /events` · `GET /events/timeline` · `GET /events/types` | Event log, timeline grouped by request ID, glossary |
| `GET /metrics` · `GET /metrics/live` · `GET /metrics/throughput` | Dashboard metrics, live queues/workers, completions per second |
| `GET /consistency` | Invariant checks run directly in MySQL |
| `GET /scheduler` · `PUT /scheduler/mode` · `PUT /scheduler/paused` | Scheduler state, switch GLOBAL_FIFO / FAIR_QUEUE, pause/resume dispatch |
| `POST /experiments` · `GET /experiments` · `GET /experiments/{group_id}` | *(demo)* FairQueue vs FIFO experiment, history, results |
| `POST /race-lab/unsafe` · `POST /race-lab/safe` · `GET /race-lab/history` | *(demo)* Race Condition Lab |
| `POST /failures/inject` · `GET/DELETE /failures` · `POST /failures/simulate-restart` | *(demo)* Failure injection and crash/restart simulation |
| `POST /admin/reset` · `POST /admin/prepare-course` · `POST /admin/simulate-registrations` | *(demo, localhost)* Reset data, prepare load-test course, quick-fill a course |

## 6. Database

Schema: [database/schema.sql](database/schema.sql) · sample data: [database/seed.sql](database/seed.sql).

| Table | Purpose / key constraints |
|---|---|
| `students` | 600 sample students (`S0001`…`S0600`) |
| `courses` | `capacity`, `confirmed_count`, `kind` (CATALOG / EXPERIMENT); `CHECK (0 <= confirmed_count <= capacity)` |
| `registrations` | `CONFIRMED` / `WAITLISTED`, `waitlist_seq`; `UNIQUE (student_id, course_id)`, `UNIQUE (course_id, waitlist_seq)` |
| `registration_requests` | `request_id` UUID primary key (idempotency key), `status` RECEIVED/COMPLETED/FAILED, `result`, `attempts`, timestamps |
| `registration_events` | Append-only event log (Failure Recovery Timeline) |
| `experiments` | FairQueue and race-lab runs with their configuration and measured summary |
| `lab_courses`, `lab_registrations` | **Race Lab only** — intentionally no constraints |

`confirmed_count` is only changed inside the same transaction that inserts/deletes/promotes the
registration row, and `GET /api/consistency` reconciles it against the actual rows.

## 7. Event names

| Event | Meaning |
|---|---|
| `REQUEST_RECEIVED` | Request validated and saved with status RECEIVED |
| `DUPLICATE_REQUEST` | Same request_id submitted again; stored state returned, not re-run |
| `QUEUED` | Placed on the scheduler queue |
| `WORKER_STARTED` | A worker thread began an attempt |
| `TRANSACTION_COMMITTED` | The seat transaction committed (written inside that transaction) |
| `TRANSACTION_ROLLED_BACK` | The attempt was rolled back; nothing it wrote was kept |
| `WORKER_FAILURE` | Injected failure, deadlock or DB error during the attempt |
| `RETRY_SCHEDULED` | Requeued at the front of its queue for the next attempt |
| `REQUEST_CONFIRMED` / `REQUEST_WAITLISTED` / `REQUEST_ALREADY_REGISTERED` | Final result |
| `REQUEST_FAILED` | Gave up after 3 attempts (or rejected because the queue was full) |
| `RESPONSE_LOST` | Demo: result committed but the HTTP reply was dropped |
| `REGISTRATION_CANCELLED` / `WAITLIST_PROMOTED` | Cancellation and the promotion done in the same transaction |
| `RECOVERY_REQUEUED` | Pending request rebuilt from MySQL after a (simulated) restart |
| `SIMULATED_CRASH` | Demo: in-memory queues discarded |

## 8. Design decisions and honest limitations

* **Correctness comes from MySQL, not the scheduler.** FairQueue changes processing *order* only; it does not add seats. A hot course still shares one row lock, so FairQueue cannot make *that* course faster — it stops it from occupying every worker.
* **READ COMMITTED isolation.** Seat allocation is serialised by the explicit course-row lock; REPEATABLE READ's gap locks would only add deadlocks between different courses. Deadlocks/lock timeouts that still happen are rolled back and retried (counted as attempts).
* **`registration_requests.course_id` has no foreign key** on purpose: an FK check takes a shared lock on the course row, which would make every incoming request wait behind the worker holding the hot course's lock. The API validates the course instead.
* **Simulated work.** Experiments have a configurable `work_ms` sleep *inside* the transaction to emulate a slower database/server; it is shown on screen and stored with the results. With `work_ms = 0` the differences between schedulers are small because local transactions take only a few milliseconds.
* **Timings are noisy** on a single laptop. Use *Repeats* (up to 5) — the summary shows the mean per mode. Results are never hard-coded; if FairQueue is not better for a workload, the page says so.
* **Fairness metric:** Jain's index over the number of requests each course had dispatched while *every* course still had requests waiting (equal service among backlogged courses, which is what round-robin promises). It is not a measure of equal *waiting times*.
* **Lost response** is simulated by the API returning HTTP 504 after the commit; a real network drop looks the same to the client.
* **Restart recovery** rebuilds queues from rows with status `RECEIVED`. A request committed just before a crash is never applied twice, because the worker re-checks the request row under a lock.
* **Unsafe lab determinism:** with *force worst-case interleaving* all clients read before anyone writes, so overbooking is guaranteed; without it, the result depends on the real thread schedule and may vary.
* **The safe race-lab run** submits through the internal intake function (same validation/queue/worker/transaction path, without HTTP); `scripts/load_test.py` covers the full HTTP path.
* **Experiments pause dispatching briefly** while they load the workload, and reset only their own `XP-*` / `LAB-SAFE` courses. Avoid using the app while an experiment runs.
* Out of scope (as in the design document): login, prerequisites, timetable clashes, multiple servers, replication, notifications, production deployment.

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| `Access denied for user 'root'@'localhost'` | Wrong `DB_PASSWORD` in `backend/.env` |
| `Can't connect to MySQL server` | `Start-Service MySQL80` in an Administrator PowerShell |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` |
| `'node' is not recognized` | Install Node.js LTS and restart VS Code |
| Dashboard shows "Backend not reachable" | Terminal 1 must be running `uvicorn …`; check http://127.0.0.1:8000/api/health |
| `Port 5173 is in use` / `8000` in use | Close the other dev server, or `uvicorn … --port 8001` and set `VITE_API_BASE_URL` |
| `cryptography package is required` | `pip install -r requirements.txt` inside the venv |
| CORS error in the browser console | Add your frontend URL to `CORS_ORIGINS` in `backend/.env` and restart uvicorn |
