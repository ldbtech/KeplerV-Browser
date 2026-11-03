from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request

from runtime.models import ControlRequest, MailListQuery, MailReadQuery, RunRequest
from runtime.guard.schemas import GuardContainmentRequest, GuardTransitionRequest
from runtime.guard.evidence import GuardEvidenceService
from runtime.guard.detective import GuardInvestigationError
from runtime.guard.interrogation import GuardInterviewError
from runtime.scheduler import RuntimeService
from runtime.connectors.base import AccountRef, AmbiguousAccountError, MailNotConnectedError, resolve_account
from runtime.services.registry import STATUS_CONNECTED
from runtime.voice.transcribe import MetaVoiceTranscriber, VoiceTranscriptionError
from runtime.voice.intent import InterpretationError, InterpretRequest, interpret as interpret_voice_command
from runtime.demo.university import PAGES, page_html


def create_app(service: RuntimeService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime_service = service or RuntimeService()
        try:
            await app.state.runtime_service.reschedule_guard_screening()
        except Exception:
            pass
        yield

    app = FastAPI(title="KeplerV local runtime", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    async def authenticated(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = os.getenv("KEPLERV_RUNTIME_TOKEN", "")
        if not expected or authorization != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail="Runtime authentication required.")

    @app.get("/healthz")
    async def health(_: None = Depends(authenticated)):
        return {"status": "ready", "runtime": "keplerv-uar"}

    @app.get("/demo/university/{page}")
    async def demo_university_page(page: str):
        from fastapi.responses import HTMLResponse
        if page not in PAGES:
            raise HTTPException(status_code=404, detail="Unknown demo page.")
        return HTMLResponse(page_html(page))

    @app.post("/transcribe")
    async def transcribe_voice(request: Request, _: None = Depends(authenticated)):
        audio = await request.body()
        if not audio:
            raise HTTPException(status_code=400, detail="Voice recording was empty.")
        try:
            return await MetaVoiceTranscriber().transcribe(audio)
        except VoiceTranscriptionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/runs/{run_id}/trace")
    async def run_trace(run_id: str, request: Request, _: None = Depends(authenticated)):
        try:
            return request.app.state.runtime_service.trace_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/interpret")
    async def interpret_voice(body: InterpretRequest, request: Request, _: None = Depends(authenticated)):
        service = request.app.state.runtime_service
        try:
            return (await interpret_voice_command(body.transcript, body.context, service.providers)).model_dump(mode="json")
        except InterpretationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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

    def _resolve_mail_account(service: RuntimeService, provider: str | None, email: str | None):
        registry = service._services()
        try:
            resolved = resolve_account(AccountRef(provider=provider, email=email), registry.list_accounts())
        except AmbiguousAccountError as exc:
            raise HTTPException(status_code=409, detail={"message": str(exc), "candidates": exc.candidates})
        except MailNotConnectedError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        meta = registry.get(resolved.provider, resolved.email)
        if meta is None:
            raise HTTPException(status_code=404, detail="That account is no longer connected.")
        status = registry.effective_status(meta)
        if status != STATUS_CONNECTED:
            raise HTTPException(status_code=403, detail=f"Account {meta.email} is {status}. Reconnect it to read mail.")
        try:
            connector = service._mail_connector(resolved.provider)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return resolved, connector

    @app.get("/accounts")
    async def list_service_accounts(request: Request, _: None = Depends(authenticated)):
        service = request.app.state.runtime_service
        registry = service._services()
        accounts = []
        for account in registry.list_accounts():
            accounts.append({
                "provider": account.provider,
                "email": account.email,
                "scopes": account.scopes,
                "expires_at": account.expires_at,
                "status": registry.effective_status(account),
            })
        return {"providers": registry.providers(), "accounts": accounts}

    @app.get("/mail/messages")
    async def list_mail_messages(
        request: Request,
        provider: str | None = Query(default=None, max_length=64),
        email: str | None = Query(default=None, max_length=256),
        q: str | None = Query(default=None, max_length=500),
        limit: int = Query(default=10, ge=1, le=25),
        _: None = Depends(authenticated),
    ):
        query = MailListQuery(provider=provider, email=email, query=q, limit=limit)
        service = request.app.state.runtime_service
        resolved, connector = _resolve_mail_account(service, query.provider, query.email)
        try:
            if query.query:
                return await connector.search(resolved, query.query, limit=query.limit)
            return await connector.list_messages(resolved, limit=query.limit)
        except MailNotConnectedError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, NotImplementedError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)[:500]) from exc

    @app.get("/mail/messages/{message_id}")
    async def read_mail_message(
        message_id: str,
        request: Request,
        provider: str | None = Query(default=None, max_length=64),
        email: str | None = Query(default=None, max_length=256),
        _: None = Depends(authenticated),
    ):
        query = MailReadQuery(provider=provider, email=email)
        service = request.app.state.runtime_service
        resolved, connector = _resolve_mail_account(service, query.provider, query.email)
        try:
            message = await connector.get_message(resolved, message_id[:256])
        except MailNotConnectedError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)[:300]) from exc
        except (ValueError, NotImplementedError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)[:500]) from exc
        return {**message, "account": resolved.email}

    @app.get("/mail/threads/{thread_id}")
    async def read_mail_thread(
        thread_id: str,
        request: Request,
        provider: str | None = Query(default=None, max_length=64),
        email: str | None = Query(default=None, max_length=256),
        _: None = Depends(authenticated),
    ):
        query = MailReadQuery(provider=provider, email=email)
        service = request.app.state.runtime_service
        resolved, connector = _resolve_mail_account(service, query.provider, query.email)
        try:
            return await connector.get_thread(resolved, thread_id[:256])
        except MailNotConnectedError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)[:300]) from exc
        except (ValueError, NotImplementedError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)[:500]) from exc

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

    @app.get("/guard/cases/{case_id}/evidence")
    async def reconstruct_guard_case(case_id: str, request: Request, _: None = Depends(authenticated)):
        service = request.app.state.runtime_service
        if not service.guard_observer.cases.can_read(case_id):
            raise HTTPException(status_code=403, detail="Guard case read permission required.")
        try:
            return GuardEvidenceService(service.store, service.guard_observer.cases).reconstruct_case(case_id, "local_user")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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

    @app.post("/guard/cases/{case_id}/investigate")
    async def investigate_guard_case(case_id: str, request: Request, _: None = Depends(authenticated)):
        service = request.app.state.runtime_service
        if not service.guard_observer.cases.can_read(case_id):
            raise HTTPException(status_code=403, detail="Guard case access required.")
        try:
            return await service.guard_detective.investigate(case_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except GuardInvestigationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/guard/cases/{case_id}/interview")
    async def interview_guard_agent(case_id: str, request: Request, _: None = Depends(authenticated)):
        service = request.app.state.runtime_service
        if not service.guard_observer.cases.can_read(case_id):
            raise HTTPException(status_code=403, detail="Guard case access required.")
        try:
            return await service.guard_interrogator.ask_next(case_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except GuardInterviewError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/guard/cases/{case_id}/adjudicate")
    async def adjudicate_guard_case(case_id: str, request: Request, _: None = Depends(authenticated)):
        service = request.app.state.runtime_service
        if not service.guard_observer.cases.can_read(case_id):
            raise HTTPException(status_code=403, detail="Guard case access required.")
        try:
            return await service.guard_adjudicator.adjudicate(case_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except GuardInvestigationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/guard/cases/{case_id}/containment")
    async def contain_guard_agent(case_id: str, body: GuardContainmentRequest, request: Request, _: None = Depends(authenticated)):
        service = request.app.state.runtime_service
        if not service.guard_observer.cases.can_read(case_id):
            raise HTTPException(status_code=403, detail="Guard case access required.")
        try:
            return await service.guard_containment.apply(case_id, body.action, body.reason, "local_user", body.capability)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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
