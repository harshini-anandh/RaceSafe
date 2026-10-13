"""Create the database and run database/schema.sql and database/seed.sql."""
from __future__ import annotations

import re
from pathlib import Path

import pymysql

from app.core.config import DATABASE_DIR, Settings

SCHEMA_FILE = DATABASE_DIR / "schema.sql"
SEED_FILE = DATABASE_DIR / "seed.sql"
MIN_MYSQL_VERSION = (8, 0, 16)


def split_sql(script: str) -> list[str]:
    """Split a .sql file into statements (our files have no ';' inside strings)."""
    lines = [line for line in script.splitlines() if not line.strip().startswith("--")]
    statements = re.split(r";\s*(?:\n|$)", "\n".join(lines))
    return [s.strip() for s in statements if s.strip()]


def _connect(settings: Settings, with_database: bool):
    return pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name if with_database else None,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=5,
        init_command="SET time_zone = '+00:00'",
    )


def server_version(settings: Settings) -> str:
    conn = _connect(settings, with_database=False)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            return cur.fetchone()[0]
    finally:
        conn.close()


def check_server_version(settings: Settings) -> str:
    version = server_version(settings)
    numbers = tuple(int(p) for p in re.findall(r"\d+", version)[:3])
    if numbers < MIN_MYSQL_VERSION:
        raise RuntimeError(
            f"MySQL {version} found, but RaceSafe needs 8.0.16 or newer (CHECK constraints are enforced from 8.0.16)."
        )
    return version


def create_database(settings: Settings, drop_existing: bool = False) -> None:
    # settings.validate() guarantees db_name is [A-Za-z0-9_] only, so it is
    # safe to place inside backticks (identifiers cannot be parameterised).
    conn = _connect(settings, with_database=False)
    try:
        with conn.cursor() as cur:
            if drop_existing:
                cur.execute(f"DROP DATABASE IF EXISTS `{settings.db_name}`")
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.db_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
    finally:
        conn.close()


def run_sql_file(settings: Settings, path: Path) -> int:
    conn = _connect(settings, with_database=True)
    count = 0
    try:
        with conn.cursor() as cur:
            for statement in split_sql(path.read_text(encoding="utf-8")):
                cur.execute(statement)
                count += 1
    finally:
        conn.close()
    return count


# Idempotent upgrades for databases created by an older schema.sql.
UPGRADES = [
    ("experiments.experiment_type accepts CHALLENGE",
     "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
     "AND TABLE_NAME = 'experiments' AND COLUMN_NAME = 'experiment_type' AND COLUMN_TYPE LIKE '%CHALLENGE%'",
     "ALTER TABLE experiments MODIFY experiment_type "
     "ENUM('FAIRQUEUE', 'RACE_UNSAFE', 'RACE_SAFE', 'CHALLENGE') NOT NULL"),
]


def apply_upgrades(settings: Settings) -> list[str]:
    """Run every upgrade whose check query returns 0. Safe to call repeatedly."""
    applied = []
    conn = _connect(settings, with_database=True)
    try:
        with conn.cursor() as cur:
            for name, check_sql, upgrade_sql in UPGRADES:
                cur.execute(check_sql)
                if cur.fetchone()[0] == 0:
                    cur.execute(upgrade_sql)
                    applied.append(name)
    finally:
        conn.close()
    return applied


def initialize_database(settings: Settings, seed: bool = True, drop_existing: bool = False) -> str:
    version = check_server_version(settings)
    create_database(settings, drop_existing=drop_existing)
    run_sql_file(settings, SCHEMA_FILE)
    apply_upgrades(settings)
    if seed:
        run_sql_file(settings, SEED_FILE)
    return version
