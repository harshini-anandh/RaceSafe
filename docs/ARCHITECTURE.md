# RaceSafe architecture notes

## Request lifecycle

1. **Client** (dashboard or `load_test.py`) sends `POST /api/registrations` with `student_id`, `course_id`
   and a client-generated `request_id` (UUID). A retry re-sends the same UUID.
2. **Intake** (`services/intake.py`) validates the IDs, then in one short transaction inserts the
   `registration_requests` row (status `RECEIVED`) plus `REQUEST_RECEIVED` and `QUEUED` events.
   If the UUID already exists, nothing is re-run: the stored status is returned (`DUPLICATE_REQUEST`).
   A UUID reused for a *different* student/course gets `409`.
3. After the commit the job is handed to the **scheduler** (`scheduler/fair_queue.py`). No transaction is held
   while a job waits in memory. If the queue is full the API returns `503` (back-pressure).
4. A **worker thread** (`workers/pool.py`, 8 threads from a `ThreadPoolExecutor`) blocks in
   `scheduler.get()` (a `threading.Condition`) until a job may be dispatched.
5. The **seat-allocation transaction** (`services/registration.py`):
   ```sql
   BEGIN;
   SELECT status FROM registration_requests WHERE request_id = ? FOR UPDATE;   -- stop if already finished
   SELECT capacity, confirmed_count FROM courses WHERE course_id = ? FOR UPDATE;
   SELECT … FROM registrations WHERE student_id = ? AND course_id = ? FOR UPDATE;
   -- exists -> ALREADY_REGISTERED ; seat free -> INSERT CONFIRMED + confirmed_count+1 ; else INSERT WAITLISTED(seq)
   UPDATE registration_requests SET status='COMPLETED', result=?, attempts=? …;
   INSERT INTO registration_events … TRANSACTION_COMMITTED, REQUEST_<RESULT>;
   COMMIT;
   ```
6. After `COMMIT` the worker wakes any API call waiting for that request (`services/completions.py`), which
   then reads the committed result from MySQL and returns it.

## Scheduler

| | Global FIFO (baseline) | FairQueue |
|---|---|---|
| Structure | one `deque` | one `deque` per course + a ring of course IDs |
| Dispatch | head of the queue | next course in round-robin order whose in-flight count < limit (default 1) |
| Within a course | arrival order | arrival order |
| Burst for one course | all 8 workers end up waiting on that course's row lock; other courses wait in the queue | at most 1 worker on that course; others serve the remaining courses |

Retries go to the **front** of their queue. Switching modes migrates queued jobs in arrival order (a global
sequence number), so a switch never reorders or drops requests.

## Locking and deadlocks

* Lock order is always **request row → course row → registration rows** (cancellation: course → registrations).
  The same order everywhere means no circular wait.
* Isolation level **READ COMMITTED**: the course-row lock serialises everything that matters for one course;
  gap locks (REPEATABLE READ) would cause needless deadlocks between different courses.
* MySQL error 1213 (deadlock) and 1205 (lock wait timeout) are rolled back and retried as a normal attempt.

## Failure handling

| Failure | What happens |
|---|---|
| Exception before `COMMIT` (injected, DB error, deadlock) | The `with conn.begin()` block rolls back. `WORKER_FAILURE` + `TRANSACTION_ROLLED_BACK` are logged in their own transactions, `attempts` is stored, and the job is requeued at the front (`RETRY_SCHEDULED`). After 3 attempts → `FAILED` + `REQUEST_FAILED`. |
| Unique-constraint violation (backstop) | Treated like a failure; the retry sees the existing row and answers `ALREADY_REGISTERED`. |
| Commit succeeded, response lost | Client retries with the same UUID → stored result (idempotent). With a new UUID → `ALREADY_REGISTERED` via the registration-row check and `UNIQUE (student_id, course_id)`. |
| Process crash / restart | Transactions in progress are rolled back by MySQL when the connection drops. On startup every `RECEIVED` request is requeued (`RECOVERY_REQUEUED`); if it had actually committed, the request-row check makes the retry a no-op. |
| A worker raises an unexpected exception | Caught per job; the worker thread keeps running; the job is retried. |
| MySQL down at startup | The API still starts, `/api/health` reports the error, and the startup recovery is retried every 5 s. |

## Measurements (FairQueue experiment)

All requests of a run are persisted while dispatch is paused, enqueued in a fixed (seeded) shuffled order,
and released at one instant `T0`:

* **wait** = `started_at − T0` (time spent queued before a worker started the successful attempt)
* **turnaround** = `completed_at − T0`
* **completion time** = last `completed_at − T0`; **throughput** = completed / completion time
* **Jain's fairness index** over the number of requests each course had *dispatched* up to the moment the first
  course's queue emptied (all courses backlogged). 1.0 = equal service.

Each run is stored in the `experiments` table with its configuration and summary; runs are grouped by
`group_id`, so results of different experiments never mix.
