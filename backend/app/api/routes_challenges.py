"""KO-06 live technical challenges C1-C5 (demo mode only)."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from fastapi.concurrency import run_in_threadpool

from app.api.deps import get_system, require_demo_mode
from app.schemas.api import ChallengeParams

router = APIRouter(tags=["challenges"])


@router.get("/challenges", summary="The five KO-06 challenges and the result of their last run")
def list_challenges(system=Depends(get_system)):
    return {"challenges": system.challenges.list()}


@router.get("/challenges/{challenge_id}", summary="Full report of the last run of one challenge")
def last_report(challenge_id: str, system=Depends(get_system)):
    return {"report": system.challenges.last_reports.get(challenge_id.upper())}


@router.post("/challenges/{challenge_id}", summary="DEMO ONLY: run challenge C1..C5 and return its report")
async def run_challenge(challenge_id: str, params: ChallengeParams | None = Body(None),
                        system=Depends(require_demo_mode)):
    params = params or ChallengeParams()
    return await run_in_threadpool(system.challenges.run, challenge_id, params.model_dump())
