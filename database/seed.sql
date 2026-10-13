-- =====================================================================
-- RaceSafe sample data
--   * 600 students  S0001 .. S0600
--   * 8 catalog courses with different capacities
-- Safe to run more than once (INSERT IGNORE).
-- =====================================================================

INSERT IGNORE INTO students (student_id, full_name, email)
WITH RECURSIVE seq (n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM seq WHERE n < 600
)
SELECT
    CONCAT('S', LPAD(n, 4, '0')),
    CONCAT(
        ELT(1 + MOD(n, 16), 'Aarav', 'Diya', 'Vihaan', 'Ananya', 'Arjun', 'Ishita', 'Kabir', 'Meera',
                            'Rohan', 'Saanvi', 'Aditya', 'Kavya', 'Nikhil', 'Priya', 'Rahul', 'Tara'),
        ' ',
        ELT(1 + MOD(FLOOR(n / 16), 12), 'Sharma', 'Iyer', 'Reddy', 'Nair', 'Menon', 'Gupta',
                                        'Rao', 'Pillai', 'Krishnan', 'Das', 'Joshi', 'Verma')
    ),
    CONCAT('student', LPAD(n, 4, '0'), '@racesafe.edu')
FROM seq;

INSERT IGNORE INTO courses (course_id, course_name, capacity, kind) VALUES
    ('CS301', 'Operating Systems',       5,  'CATALOG'),
    ('CS302', 'Database Systems',        40, 'CATALOG'),
    ('CS305', 'Computer Networks',       30, 'CATALOG'),
    ('CS401', 'Distributed Systems',     10, 'CATALOG'),
    ('MA201', 'Linear Algebra',          50, 'CATALOG'),
    ('PH110', 'Engineering Physics',     60, 'CATALOG'),
    ('EE150', 'Digital Electronics',     25, 'CATALOG'),
    ('HS101', 'Professional Ethics',     80, 'CATALOG');
