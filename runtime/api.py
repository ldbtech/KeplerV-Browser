from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request

from runtime.models import ControlRequest, RunRequest
from runtime.guard.schemas import GuardTransitionRequest
from runtime.scheduler import RuntimeService


def create_app(service: RuntimeService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime_service = service or RuntimeService()
        yield

    app = FastAPI(title="KeplerV local runtime", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    async def authenticated(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = os.getenv("KEPLERV_RUNTIME_TOKEN", "")
        if not expected or authorization != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail="Runtime authentication required.")

    @app.get("/healthz")
    async def health(_: None = Depends(authenticated)):
        return {"status": "ready", "runtime": "keplerv-uar"}

    @app.post("/runs", status_code=202)
    async def create_run(body: RunRequest, request: Request, _: None = Depends(authenticated)):
        try:
            return request.app.state.runtime_service.agent_view(
                await request.app.state.runtime_service.create_run(body)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/snapshot")
    async def snapshot(request: Request, after: int = Query(default=0, ge=0), _: None = Depends(authenticated)):
        return request.app.state.runtime_service.snapshot(after)

    @app.get("/guard/cases")
    async def list_guard_cases(request: Request, goal_id: str | None = None, _: None = Depends(authenticated)):
        return request.app.state.runtime_service.guard_observer.cases.list_cases(goal_id)

    @app.get("/guard/cases/{case_id}")
    async def get_guard_case(case_id: str, request: Request, _: None = Depends(authenticated)):
        cases = request.app.state.runtime_service.guard_observer.cases
        if not cases.can_read(case_id):
            raise HTTPException(status_code=403, detail="Guard case read permission required.")
        case = cases.get_case(case_id)
        if not case:
            raise HTTPException(status_code=404, detail="Guard case not found.")
        return case

    @app.get("/guard/cases/{case_id}/integrity")
    async def verify_guard_case(case_id: str, request: Request, _: None = Depends(authenticated)):
        cases = request.app.state.runtime_service.guard_observer.cases
        if not cases.can_read(case_id):
            raise HTTPException(status_code=403, detail="Guard case read permission required.")
        case = cases.get_case(case_id)
        if not case:
            raise HTTPException(status_code=404, detail="Guard case not found.")
        valid = request.app.state.runtime_service.guard_observer.cases.verify_integrity(case_id)
        return {"case_id": case_id, "valid": valid}

    @app.post("/guard/cases/{case_id}/transition")
    async def transition_guard_case(case_id: str, body: GuardTransitionRequest, request: Request, _: None = Depends(authenticated)):
        cases = request.app.state.runtime_service.guard_observer.cases
        if not cases.can_read(case_id):
            raise HTTPException(status_code=403, detail="Guard case access required.")
        try:
            case = cases.transition(case_id, body.status, body.reason, "local_user")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not case:
            raise HTTPException(status_code=404, detail="Guard case not found.")
        return case

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str, request: Request, _: None = Depends(authenticated)):
        run = request.app.state.runtime_service.store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Agent run not found.")
        return request.app.state.runtime_service.agent_view(run)

    @app.post("/runs/{run_id}/control")
    async def control(run_id: str, body: ControlRequest, request: Request, _: None = Depends(authenticated)):
        try:
            return await request.app.state.runtime_service.control(run_id, body.action, body.call_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app


app = create_app()
