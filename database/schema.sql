-- =====================================================================
-- RaceSafe database schema  (MySQL 8.0.16+ / InnoDB)
--
-- Run through:  python scripts\init_db.py   (from the backend folder)
-- The script creates the database itself, then executes this file.
--
-- Design rules (see docs/ARCHITECTURE.md):
--   * MySQL is the final authority on seats. Row locks (SELECT ... FOR UPDATE)
--     on the course row serialise seat allocation per course only.
--   * UNIQUE (student_id, course_id) is the backstop against duplicates.
--   * CHECK (confirmed_count <= capacity) is the backstop against overbooking.
--   * Fixed lock order everywhere: request row -> course row -> registration rows.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. students
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS students (
    student_id   VARCHAR(16)  NOT NULL,
    full_name    VARCHAR(100) NOT NULL,
    email        VARCHAR(120) NOT NULL,
    created_at   DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (student_id),
    UNIQUE KEY uq_students_email (email)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- 2. courses
--    kind = CATALOG     -> real courses shown on the student page
--    kind = EXPERIMENT  -> courses created by experiments / load tests
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS courses (
    course_id        VARCHAR(16)  NOT NULL,
    course_name      VARCHAR(120) NOT NULL,
    capacity         INT          NOT NULL,
    confirmed_count  INT          NOT NULL DEFAULT 0,
    kind             ENUM('CATALOG', 'EXPERIMENT') NOT NULL DEFAULT 'CATALOG',
    created_at       DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at       DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (course_id),
    KEY idx_courses_kind (kind),
    CONSTRAINT chk_courses_capacity CHECK (capacity >= 0),
    CONSTRAINT chk_courses_count    CHECK (confirmed_count >= 0 AND confirmed_count <= capacity)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- 3. registrations
--    One row per (student, course). WAITLISTED rows carry a per-course
--    waitlist_seq that defines strict FIFO order for promotion.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS registrations (
    registration_id  BIGINT       NOT NULL AUTO_INCREMENT,
    student_id       VARCHAR(16)  NOT NULL,
    course_id        VARCHAR(16)  NOT NULL,
    status           ENUM('CONFIRMED', 'WAITLISTED') NOT NULL,
    waitlist_seq     BIGINT       NULL,
    request_id       CHAR(36)     NULL,
    promoted_at      DATETIME(6)  NULL,
    created_at       DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at       DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (registration_id),
    UNIQUE KEY uq_registrations_student_course (student_id, course_id),
    UNIQUE KEY uq_registrations_waitlist (course_id, waitlist_seq),
    KEY idx_registrations_course_status (course_id, status, waitlist_seq),
    KEY idx_registrations_request (request_id),
    CONSTRAINT fk_registrations_student FOREIGN KEY (student_id) REFERENCES students (student_id),
    CONSTRAINT fk_registrations_course  FOREIGN KEY (course_id)  REFERENCES courses (course_id),
    CONSTRAINT chk_registrations_waitlist CHECK (
        (status = 'WAITLISTED' AND waitlist_seq IS NOT NULL) OR
        (status = 'CONFIRMED'  AND waitlist_seq IS NULL)
    )
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- 4. registration_requests  (durable request state + idempotency key)
--    status RECEIVED  = persisted, not yet finished (recovered after restart)
--    status COMPLETED = result committed (result column says which)
--    status FAILED    = gave up after max attempts or rejected (queue full)
--
--    Note: course_id deliberately has NO foreign key. An FK check takes a
--    shared lock on the parent course row, which would make every new request
--    wait behind the worker holding the hot course's row lock. The API checks
--    that the course exists before inserting instead.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS registration_requests (
    request_id     CHAR(36)     NOT NULL,
    student_id     VARCHAR(16)  NOT NULL,
    course_id      VARCHAR(16)  NOT NULL,
    status         ENUM('RECEIVED', 'COMPLETED', 'FAILED') NOT NULL DEFAULT 'RECEIVED',
    result         ENUM('CONFIRMED', 'WAITLISTED', 'ALREADY_REGISTERED') NULL,
    attempts       INT          NOT NULL DEFAULT 0,
    last_error     VARCHAR(255) NULL,
    worker_name    VARCHAR(32)  NULL,
    experiment_id  BIGINT       NULL,
    received_at    DATETIME(6)  NOT NULL,
    queued_at      DATETIME(6)  NULL,
    started_at     DATETIME(6)  NULL,
    completed_at   DATETIME(6)  NULL,
    updated_at     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (request_id),
    KEY idx_requests_status (status, received_at),
    KEY idx_requests_course (course_id, received_at),
    KEY idx_requests_student (student_id),
    KEY idx_requests_experiment (experiment_id),
    KEY idx_requests_completed (completed_at),
    CONSTRAINT fk_requests_student FOREIGN KEY (student_id) REFERENCES students (student_id),
    CONSTRAINT chk_requests_attempts CHECK (attempts >= 0),
    CONSTRAINT chk_requests_result CHECK (
        (status = 'COMPLETED' AND result IS NOT NULL) OR
        (status <> 'COMPLETED' AND result IS NULL)
    )
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- 5. registration_events  (append-only log -> Failure Recovery Timeline)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS registration_events (
    event_id       BIGINT       NOT NULL AUTO_INCREMENT,
    request_id     CHAR(36)     NULL,
    student_id     VARCHAR(16)  NULL,
    course_id      VARCHAR(16)  NULL,
    event_type     VARCHAR(40)  NOT NULL,
    attempt        INT          NULL,
    worker_name    VARCHAR(32)  NULL,
    message        VARCHAR(500) NOT NULL DEFAULT '',
    metadata       JSON         NULL,
    experiment_id  BIGINT       NULL,
    created_at     DATETIME(6)  NOT NULL,
    PRIMARY KEY (event_id),
    KEY idx_events_request (request_id, event_id),
    KEY idx_events_course (course_id, event_id),
    KEY idx_events_type (event_type, event_id),
    KEY idx_events_experiment (experiment_id),
    KEY idx_events_created (created_at)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- 6. experiments  (FairQueue runs, race-lab runs; keeps results separate)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id    BIGINT       NOT NULL AUTO_INCREMENT,
    group_id         CHAR(36)     NULL,
    experiment_type  ENUM('FAIRQUEUE', 'RACE_UNSAFE', 'RACE_SAFE') NOT NULL,
    scheduler_mode   VARCHAR(20)  NULL,
    run_index        INT          NOT NULL DEFAULT 1,
    status           ENUM('RUNNING', 'COMPLETED', 'FAILED') NOT NULL DEFAULT 'RUNNING',
    config           JSON         NOT NULL,
    summary          JSON         NULL,
    error            VARCHAR(500) NULL,
    created_at       DATETIME(6)  NOT NULL,
    released_at      DATETIME(6)  NULL,
    finished_at      DATETIME(6)  NULL,
    PRIMARY KEY (experiment_id),
    KEY idx_experiments_group (group_id),
    KEY idx_experiments_type (experiment_type, experiment_id)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- 7. Race Condition Lab tables  (ISOLATED, DEMO ONLY)
--    These tables intentionally have NO unique constraint and NO check
--    constraint, so the unsafe check-then-act code can visibly overbook.
--    The real registration path never touches them.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lab_courses (
    course_id        VARCHAR(16) NOT NULL,
    capacity         INT         NOT NULL,
    confirmed_count  INT         NOT NULL DEFAULT 0,
    PRIMARY KEY (course_id)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS lab_registrations (
    lab_registration_id  BIGINT      NOT NULL AUTO_INCREMENT,
    course_id            VARCHAR(16) NOT NULL,
    student_id           VARCHAR(16) NOT NULL,
    status               VARCHAR(16) NOT NULL,
    created_at           DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (lab_registration_id),
    KEY idx_lab_registrations_course (course_id, student_id)
) ENGINE = InnoDB;
