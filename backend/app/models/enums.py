"""Status values and event names used across the backend, DB and frontend."""
from __future__ import annotations

from enum import Enum


class RequestStatus(str, Enum):
    RECEIVED = "RECEIVED"      # persisted, result not committed yet
    COMPLETED = "COMPLETED"    # result committed (see RequestResult)
    FAILED = "FAILED"          # terminal failure (max attempts / queue full)


class RequestResult(str, Enum):
    CONFIRMED = "CONFIRMED"
    WAITLISTED = "WAITLISTED"
    ALREADY_REGISTERED = "ALREADY_REGISTERED"


class RegistrationStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    WAITLISTED = "WAITLISTED"


class SchedulerMode(str, Enum):
    GLOBAL_FIFO = "GLOBAL_FIFO"
    FAIR_QUEUE = "FAIR_QUEUE"


class EventType(str, Enum):
    REQUEST_RECEIVED = "REQUEST_RECEIVED"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    QUEUED = "QUEUED"
    WORKER_STARTED = "WORKER_STARTED"
    TRANSACTION_COMMITTED = "TRANSACTION_COMMITTED"
    TRANSACTION_ROLLED_BACK = "TRANSACTION_ROLLED_BACK"
    WORKER_FAILURE = "WORKER_FAILURE"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    REQUEST_CONFIRMED = "REQUEST_CONFIRMED"
    REQUEST_WAITLISTED = "REQUEST_WAITLISTED"
    REQUEST_ALREADY_REGISTERED = "REQUEST_ALREADY_REGISTERED"
    REQUEST_FAILED = "REQUEST_FAILED"
    RESPONSE_LOST = "RESPONSE_LOST"
    REGISTRATION_CANCELLED = "REGISTRATION_CANCELLED"
    WAITLIST_PROMOTED = "WAITLIST_PROMOTED"
    RECOVERY_REQUEUED = "RECOVERY_REQUEUED"
    SIMULATED_CRASH = "SIMULATED_CRASH"


EVENT_DESCRIPTIONS: dict[str, str] = {
    "REQUEST_RECEIVED": "API validated the request and saved it with status RECEIVED.",
    "DUPLICATE_REQUEST": "A request with an already-known request ID arrived; the stored state was returned instead of re-running it.",
    "QUEUED": "Request was placed on the scheduler queue (per-course queue in FairQueue mode).",
    "WORKER_STARTED": "A worker thread took the request from the scheduler and began a transaction attempt.",
    "TRANSACTION_COMMITTED": "The seat-allocation transaction committed. Its effects are durable.",
    "TRANSACTION_ROLLED_BACK": "The transaction was rolled back; none of its writes were kept.",
    "WORKER_FAILURE": "The worker failed during the attempt (injected failure, deadlock, DB error).",
    "RETRY_SCHEDULED": "The request was put back at the front of its queue for another attempt.",
    "REQUEST_CONFIRMED": "Final result: the student got a seat.",
    "REQUEST_WAITLISTED": "Final result: the course was full; the student joined the FIFO waitlist.",
    "REQUEST_ALREADY_REGISTERED": "Final result: the student already had a registration for this course.",
    "REQUEST_FAILED": "Terminal failure: the request gave up after the maximum number of attempts (or was rejected).",
    "RESPONSE_LOST": "Demo injection: the result was committed but the HTTP response was dropped.",
    "REGISTRATION_CANCELLED": "A registration was cancelled.",
    "WAITLIST_PROMOTED": "The earliest waitlisted student was promoted to CONFIRMED in the cancellation transaction.",
    "RECOVERY_REQUEUED": "After a (simulated) restart, a persisted RECEIVED request was put back on its queue.",
    "SIMULATED_CRASH": "Demo: the in-memory queues were discarded as if the backend process had crashed.",
}

RESULT_EVENT = {
    RequestResult.CONFIRMED.value: EventType.REQUEST_CONFIRMED,
    RequestResult.WAITLISTED.value: EventType.REQUEST_WAITLISTED,
    RequestResult.ALREADY_REGISTERED.value: EventType.REQUEST_ALREADY_REGISTERED,
}
