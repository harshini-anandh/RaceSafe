"""Application settings, read from environment variables and backend/.env.

Real passwords live only in backend/.env (git-ignored). See .env.example.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent
DATABASE_DIR = PROJECT_DIR / "database"

# Values already present in the environment win over the .env file.
load_dotenv(BACKEND_DIR / ".env")

SCHEDULER_MODES = ("GLOBAL_FIFO", "FAIR_QUEUE")
_DB_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "racesafe"
    environment: str = "development"
    worker_count: int = 8
    max_attempts: int = 3
    per_course_in_flight: int = 1
    max_queue_size: int = 5000
    scheduler_mode: str = "FAIR_QUEUE"
    simulated_work_ms: int = 0
    db_pool_size: int = 20
    db_max_overflow: int = 40
    request_wait_timeout_s: float = 30.0
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        settings = cls(
            db_host=_env_str("DB_HOST", cls.db_host),
            db_port=_env_int("DB_PORT", cls.db_port),
            db_user=_env_str("DB_USER", cls.db_user),
            db_password=os.getenv("DB_PASSWORD", ""),
            db_name=_env_str("DB_NAME", cls.db_name),
            environment=_env_str("RACESAFE_ENV", cls.environment),
            worker_count=_env_int("WORKER_COUNT", cls.worker_count),
            max_attempts=_env_int("MAX_ATTEMPTS", cls.max_attempts),
            per_course_in_flight=_env_int("PER_COURSE_IN_FLIGHT", cls.per_course_in_flight),
            max_queue_size=_env_int("MAX_QUEUE_SIZE", cls.max_queue_size),
            scheduler_mode=_env_str("SCHEDULER_MODE", cls.scheduler_mode).upper(),
            simulated_work_ms=_env_int("SIMULATED_WORK_MS", cls.simulated_work_ms),
            cors_origins=tuple(
                o.strip() for o in _env_str("CORS_ORIGINS", ",".join(cls.cors_origins)).split(",") if o.strip()
            ),
        )
        settings = replace(settings, **overrides)
        settings.validate()
        return settings

    def validate(self) -> None:
        if not _DB_NAME_PATTERN.match(self.db_name):
            raise ValueError("DB_NAME may only contain letters, digits and underscores")
        if self.scheduler_mode not in SCHEDULER_MODES:
            raise ValueError(f"SCHEDULER_MODE must be one of {SCHEDULER_MODES}")
        if not 1 <= self.worker_count <= 64:
            raise ValueError("WORKER_COUNT must be between 1 and 64")
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("MAX_ATTEMPTS must be between 1 and 10")
        if self.per_course_in_flight < 1:
            raise ValueError("PER_COURSE_IN_FLIGHT must be at least 1")

    @property
    def demo_mode(self) -> bool:
        """Demo-only endpoints (failure injection, race lab, reset) need this."""
        return self.environment.lower() in ("development", "dev", "demo", "test")

    def database_url(self, include_database: bool = True) -> URL:
        # URL.create escapes special characters in the password correctly.
        return URL.create(
            "mysql+pymysql",
            username=self.db_user,
            password=self.db_password or None,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name if include_database else None,
            query={"charset": "utf8mb4"},
        )
