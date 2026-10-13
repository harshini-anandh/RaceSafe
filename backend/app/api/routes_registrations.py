"""Submit, check and cancel registrations."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.deps import get_system
from app.models.enums import EventType
from app.schemas.api import CancelRequest, CancelResponse, RegistrationCreate, RequestOutcome
from app.services import queries
from app.services.cancellation import cancel_registration
from app.workers.failure_injection import FailureKind

router = APIRouter(tags=["registrations"])


def _read_outcome(system, request_id: str) -> dict:
    with system.engine.connect() as conn:
        return queries.request_outcome(conn, request_id)


def _message(o: dict) -> str:
    prefix = "Duplicate request_id - returning the stored result without re-running it. " if o.get("replayed") else ""
    if o["status"] == "RECEIVED":
        return prefix + "Accepted and queued; still being processed. Poll GET /api/requests/{request_id}."
    if o["status"] == "FAILED":
        return prefix + f"Failed after {o['attempts']} attempt(s): {o['last_error'] or 'unknown error'}"
    if o["result"] == "CONFIRMED":
        return prefix + f"Seat confirmed in {o['course_id']}."
    if o["result"] == "WAITLISTED":
        pos = o["waitlist_position"]
        if o["registration_status"] == "CONFIRMED":
            return prefix + f"Was waitlisted, and has since been promoted to CONFIRMED in {o['course_id']}."
        return prefix + f"{o['course_id']} is full - added to the FIFO waitlist" + (f" at position {pos}." if pos else ".")
    return prefix + f"Already registered for {o['course_id']} (current status: {o['registration_status'] or 'none'})."


@router.post(
    "/registrations",
    response_model=RequestOutcome,
    summary="Submit a registration request",
    responses={202: {"description": "Accepted, still processing"},
               504: {"description": "Demo: response deliberately lost after commit"},
               503: {"description": "Queue full (back-pressure) or database unavailable"}},
)
async def submit_registration(
    body: RegistrationCreate,
    response: Response,
    wait: bool = Query(True, description="Wait for the final result (up to `timeout` seconds)"),
    timeout: float = Query(30, ge=1, le=300),
    system=Depends(get_system),
):
    intake = await run_in_threadpool(system.intake.submit, body.student_id, body.course_id, body.request_id,
                                     wait=wait)
    if intake.waiter is not None:
        try:
            await asyncio.wait_for(asyncio.wrap_future(intake.waiter), timeout)
        except asyncio.TimeoutError:
            system.completions.discard(intake.request_id, intake.waiter)

    outcome = await run_in_threadpool(_read_outcome, system, intake.request_id)
    outcome["replayed"] = intake.replayed
    outcome["message"] = _message(outcome)
    if outcome["status"] == "RECEIVED":
        response.status_code = 202
        return outcome

    if system.injector.consume(FailureKind.LOST_RESPONSE, intake.request_id):
        await run_in_threadpool(
            system.events.log, EventType.RESPONSE_LOST, request_id=intake.request_id,
            student_id=outcome["student_id"], course_id=outcome["course_id"], attempt=outcome["attempts"],
            message=f"Result {outcome['outcome']} was committed, but the HTTP response was dropped (demo)",
        )
        return JSONResponse(status_code=504, content={
            "code": "RESPONSE_LOST",
            "request_id": intake.request_id,
            "detail": "Simulated lost response: the server committed the result but the reply never arrived. "
                      "Retry with the SAME request_id to get the stored result safely.",
        })
    return outcome


@router.get("/requests/{request_id}", response_model=RequestOutcome, summary="Status of one request")
def request_status(request_id: str, system=Depends(get_system)):
    outcome = _read_outcome(system, request_id.strip().lower())
    outcome["message"] = _message(outcome)
    return outcome


@router.get("/requests/{request_id}/events", summary="All events of one request, oldest first")
def request_events(request_id: str, system=Depends(get_system)):
    with system.engine.connect() as conn:
        events = queries.list_events(conn, request_id=request_id, include_experiment=True, limit=500)
    return {"request_id": request_id, "events": list(reversed(events))}


@router.post("/registrations/cancel", response_model=CancelResponse,
             summary="Cancel a registration; promotes the first waitlisted student atomically")
def cancel(body: CancelRequest, system=Depends(get_system)):
    return cancel_registration(system.engine, body.student_id, body.course_id)
