"""Pydantic request/response schemas for the REST API."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

ID_PATTERN = r"^[A-Za-z0-9-]{1,16}$"


# ---------------------------------------------------------------- registrations
class RegistrationCreate(BaseModel):
    student_id: str = Field(..., pattern=ID_PATTERN, examples=["S0001"])
    course_id: str = Field(..., pattern=ID_PATTERN, examples=["CS301"])
    request_id: Optional[str] = Field(
        None, description="Client-generated UUID. Re-send the same value when retrying.",
        examples=["3f1c2a9e-4b7d-4c1e-9a55-0d3b8f6e2a10"],
    )


class CancelRequest(BaseModel):
    student_id: str = Field(..., pattern=ID_PATTERN)
    course_id: str = Field(..., pattern=ID_PATTERN)


class RequestOutcome(BaseModel):
    request_id: str
    student_id: str
    course_id: str
    status: str = Field(..., description="RECEIVED (pending), COMPLETED or FAILED")
    result: Optional[str] = Field(None, description="CONFIRMED, WAITLISTED or ALREADY_REGISTERED")
    outcome: str = Field(..., description="result if completed, otherwise PENDING / FAILED")
    attempts: int
    last_error: Optional[str] = None
    worker_name: Optional[str] = None
    registration_status: Optional[str] = None
    waitlist_position: Optional[int] = None
    experiment_id: Optional[int] = None
    received_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    queue_wait_ms: Optional[float] = None
    processing_ms: Optional[float] = None
    replayed: bool = Field(False, description="True if this request_id had been submitted before")
    message: str = ""


class CancelResponse(BaseModel):
    student_id: str
    course_id: str
    cancelled_status: str
    promoted: Optional[dict[str, Any]] = None
    confirmed_count: int
    capacity: int


# ---------------------------------------------------------------- scheduler
class SchedulerModeUpdate(BaseModel):
    mode: Literal["GLOBAL_FIFO", "FAIR_QUEUE"]


class SchedulerPauseUpdate(BaseModel):
    paused: bool


# ---------------------------------------------------------------- experiments
class ExperimentConfig(BaseModel):
    mode: Literal["GLOBAL_FIFO", "FAIR_QUEUE", "BOTH"] = "BOTH"
    total_requests: int = Field(500, ge=20, le=2000)
    course_count: int = Field(5, ge=2, le=8)
    distribution: Literal["skewed", "uniform", "custom"] = "skewed"
    hot_share: float = Field(0.7, ge=0.2, le=0.95, description="Share of requests for the hot course (skewed)")
    weights: Optional[list[float]] = Field(None, description="One weight per course (custom)")
    capacity: int = Field(50, ge=1, le=600, description="Seats in each experiment course")
    work_ms: int = Field(5, ge=0, le=100, description="Simulated work inside each transaction (ms)")
    repeats: int = Field(1, ge=1, le=5)
    seed: int = Field(42, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def _check_weights(self):
        if self.distribution == "custom":
            if not self.weights or len(self.weights) != self.course_count:
                raise ValueError("custom distribution needs exactly course_count weights")
            if any(w <= 0 for w in self.weights):
                raise ValueError("weights must be positive")
        return self


# ---------------------------------------------------------------- race lab
class RaceLabConfig(BaseModel):
    capacity: int = Field(10, ge=1, le=500)
    students: int = Field(500, ge=2, le=600)
    client_threads: int = Field(50, ge=2, le=64, description="Concurrent simulated clients")
    duplicate_ratio: float = Field(0.0, ge=0, le=0.5, description="Share of students who double-click")
    race_window_ms: int = Field(20, ge=0, le=200, description="Unsafe only: delay between check and update")
    force_interleaving: bool = Field(
        False, description="Unsafe only: every client finishes its first READ before anyone WRITES"
    )


# ---------------------------------------------------------------- failures / admin
class FailureInjection(BaseModel):
    kind: Literal["BEFORE_COMMIT", "LOST_RESPONSE"]
    request_id: Optional[str] = Field(None, description="Target one request; omit to hit the next N requests")
    count: int = Field(1, ge=1, le=10, description="How many times to fail")


class ResetRequest(BaseModel):
    confirm: str = Field(..., description='Must be exactly "RESET"')


class PrepareCourse(BaseModel):
    course_id: str = Field(..., pattern=ID_PATTERN)
    capacity: int = Field(..., ge=0, le=10_000)
    course_name: Optional[str] = None


class SimulateRegistrations(BaseModel):
    course_id: str = Field(..., pattern=ID_PATTERN)
    count: int = Field(10, ge=1, le=200)
