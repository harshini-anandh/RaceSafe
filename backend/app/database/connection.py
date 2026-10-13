"""SQLAlchemy engine creation and MySQL error helpers.

We use SQLAlchemy Core with hand-written, parameterised SQL (text() with
:named parameters) so that every lock and transaction is visible in the code.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings

# MySQL error codes we react to
ER_DUP_ENTRY = 1062
ER_LOCK_WAIT_TIMEOUT = 1205
ER_LOCK_DEADLOCK = 1213
RETRYABLE_ERRORS = {ER_LOCK_WAIT_TIMEOUT, ER_LOCK_DEADLOCK}

# Every connection stores and compares timestamps in UTC.
_CONNECT_ARGS = {"init_command": "SET time_zone = '+00:00'", "connect_timeout": 5}


def create_db_engine(settings: Settings) -> Engine:
    """Pooled engine used by the API and the worker threads.

    READ COMMITTED: seat allocation is serialised by the explicit course-row
    lock (SELECT ... FOR UPDATE), so we do not need REPEATABLE READ's gap
    locks, which would cause needless deadlocks between different courses.
    """
    return create_engine(
        settings.database_url(),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=30,
        isolation_level="READ COMMITTED",
        connect_args=_CONNECT_ARGS,
    )


def create_lab_engine(settings: Settings) -> Engine:
    """Non-pooled AUTOCOMMIT engine used ONLY by the unsafe race-lab demo."""
    return create_engine(
        settings.database_url(),
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
        connect_args=_CONNECT_ARGS,
    )


def mysql_error_code(exc: BaseException) -> int | None:
    """Return the MySQL error number wrapped inside a SQLAlchemy exception."""
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", None) or ()
    if args and isinstance(args[0], int):
        return args[0]
    return None
