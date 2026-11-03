"""Phase 5 M8: AIP service permissions, grants, revocation, Guard observation."""

from __future__ import annotations

import asyncio
import json

import pytest

from runtime.aip.policy import (
    SENSITIVE_SERVICE_TOOLS,
    SERVICE_PERMISSIONS,
    SERVICE_TOOL_PERMISSION,
    authorize_tool,
)
from runtime.browser.tools import TOOL_SCHEMAS
from runtime.connectors.fake_adapter import FakeMailAdapter
from runtime.guard.evidence import GuardEvidenceService
from runtime.providers import ModelProviderError
from runtime.scheduler import TOOL_NAMES, RuntimeService
from runtime.services.fake_services import FAKE_SERVICE_LABEL, FakeServiceAdapter
from runtime.services.permissions import REVOKE_ACTION, validate_permission
from runtime.services.registry import ServiceRegistry
from runtime.store import RuntimeStore

FAKE_ACCOUNT = "demo.person@example.test"
LONG_BODY = "Fictional body sentence for minimal-evidence tests. " * 20


def _harness(tmp_path):
    registry = ServiceRegistry(store_path=tmp_path / "services" / "accounts.json")
    store = RuntimeStore(db_path=tmp_path / "store" / "runtime.sqlite3")
    adapter = FakeServiceAdapter()
    service = RuntimeService(
        store=store,
        service_registry=registry,
        mail_connectors={"fake-mail": FakeMailAdapter()},
        service_adapter=adapter,
    )
    return service, registry, adapter


def _run_values(**overrides):
    values = {
        "goal_id": "goal-m8",
        "goal_title": "Fictional M8 service test",
        "goal_description": "A made-up goal used only to test service grants.",
        "task": "Exercise service tools against labeled fakes.",
        "source_url": "",
        "step_budget": 4,
        "token_budget": 4000,
    }
    values.update(overrides)
    return values


def _make_run(service, permissions):
    run = service.store.create_run(_run_values(), "meta", "fake-model", TOOL_NAMES, permissions)
    service._pause_gates.setdefault(run["id"], asyncio.Event()).set()
    return service.store.get_run(run["id"])


def _decide(tool, permissions, **extra):
    return authorize_tool(
        agent_id="a", registered_agent_id="a", goal_id="g", registered_goal_id="g",
        permissions=set(permissions), tool_name=tool, arguments={},
        authorized_origin=None, **extra)


async def _cancel_screening(service):
    """Cancel background Guard screening; these tests assert the sync rule case only."""
    tasks = list(service._guard_screening_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# -- AIP grant matrix ------------------------------------------------------


def test_every_service_tool_requires_its_exact_grant():
    assert set(SERVICE_TOOL_PERMISSION) >= {
        "gmail.read", "gmail.search", "gmail.send",
        "calendar.read", "calendar.create", "calendar.delete",
        "drive.read", "drive.share", "github.read", "github.publish",
        "media.open", "media.control",
    }
    for tool, grant in SERVICE_TOOL_PERMISSION.items():
        denied = _decide(tool, {"browser.read"})
        assert denied.outcome == "deny", tool
        assert grant in denied.reason, tool
        if tool in SENSITIVE_SERVICE_TOOLS:
            pending = _decide(tool, {grant})
            assert pending.outcome == "require_approval", tool
            approved = _decide(tool, {grant}, user_approved=True)
            assert approved.outcome == "allow", tool
        else:
            assert _decide(tool, {grant}).outcome == "allow", tool


def test_wildcards_are_not_grantable_and_unknown_services_deny():
    with pytest.raises(ValueError, match="Unknown service permission"):
        validate_permission("calendar.*")
    with pytest.raises(ValueError, match="Unknown service permission"):
        validate_permission("gmail.delete")
    assert _decide("gmail.delete", {"gmail.read"}).outcome == "deny"
    # A wildcard string in permissions grants nothing.
    assert _decide("calendar.read", {"calendar.*"}).outcome == "deny"


def test_legacy_mail_send_alias_stays_gated():
    assert _decide("mail.send", {"mail.read"}).outcome == "deny"
    assert _decide("mail.send", {"gmail.send"}).outcome == "require_approval"
    assert _decide("mail.send", {"gmail.send"}, user_approved=True).outcome == "allow"


def test_all_service_tools_have_wire_schemas():
    names = {item["function"]["name"] for item in TOOL_SCHEMAS}
    for wire in ("gmail_read", "gmail_search", "gmail_send", "calendar_read",
                 "calendar_create", "calendar_delete", "drive_read", "drive_share",
                 "github_read", "github_publish", "media_open", "media_control"):
        assert wire in names


# -- grants, revocation, security events ------------------------------------


def test_grant_and_revoke_emit_security_events(tmp_path):
    async def _body():
        service, _, _ = _harness(tmp_path)
        run = _make_run(service, ["browser.read"])
        service.grant_service_access(run["id"], "gmail.read")
        view = service.revoke_agent_service_access(run["id"], "gmail.read")
        assert "gmail.read" not in view["permissions"]
        events = service.store.list_run_events(run["id"])
        connected = [e for e in events if e["type"] == "service.connected"]
        revoked = [e for e in events if e["type"] == "service.revoked"]
        assert len(connected) == 1 and connected[0]["payload"]["permission"] == "gmail.read"
        assert len(revoked) == 1 and revoked[0]["payload"]["action"] == REVOKE_ACTION
        assert revoked[0]["payload"]["removed"] == ["gmail.read"]

    asyncio.run(_body())


def test_revoke_all_clears_every_service_grant(tmp_path):
    async def _body():
        service, _, _ = _harness(tmp_path)
        run = _make_run(service, ["browser.read", "browser.interact"])
        service.grant_service_access(run["id"], "gmail.read")
        service.grant_service_access(run["id"], "drive.read")
        view = service.revoke_agent_service_access(run["id"])
        assert view["permissions"] == ["browser.read", "browser.interact"]

    asyncio.run(_body())


def test_revocation_takes_effect_immediately(tmp_path):
    async def _body():
        service, registry, _ = _harness(tmp_path)
        registry.connect("fake-mail", FAKE_ACCOUNT)
        run = _make_run(service, ["browser.read"])
        service.grant_service_access(run["id"], "gmail.read")
        first = await service._execute_tool(run, None, "gmail.read", {"limit": 5}, "call-1")
        assert first["ok"] is True
        service.revoke_agent_service_access(run["id"], "gmail.read")
        second = await service._execute_tool(run, None, "gmail.read", {"limit": 5}, "call-2")
        assert second["ok"] is False
        assert "gmail.read" in second["error"]
        denied = [e for e in service.store.list_run_events(run["id"])
                  if e["type"] == "service.access_denied"]
        assert denied, "revoked access must record a denied-access security event"
        await _cancel_screening(service)

    asyncio.run(_body())


def test_no_inheritance_by_default_through_real_delegation(tmp_path):
    class _FailingAdapter:
        async def generate(self, model, messages, tools):
            raise ModelProviderError("stop immediately for tests")

    class _FailingProviders:
        active_provider = "meta"
        active_model = "stop-test"

        def get(self, provider_id):
            return _FailingAdapter()

    class _DummyBrowser:
        async def close(self):
            return None

    async def _body():
        service, _, _ = _harness(tmp_path)
        service.providers = _FailingProviders()
        service.browser_factory = lambda run_id, origin, path: _DummyBrowser()
        run = _make_run(service, ["browser.read", "browser.interact", "delegate"])
        service.grant_service_access(run["id"], "gmail.send")
        service.grant_service_access(run["id"], "drive.read")
        result = await service._execute_tool(
            run, None, "agent.delegate", {"task": "A bounded fictional subtask."}, "call-1")
        assert result["ok"] is False  # child stops immediately by design
        child_id = result["child_agent_id"]
        child = next(r for r in service.store.list_runs() if r["agent_id"] == child_id)
        assert set(child["permissions"]) & set(SERVICE_PERMISSIONS) == set()
        assert _decide("gmail.send", child["permissions"]).outcome == "deny"
        parent = service.store.get_run(run["id"])
        assert "gmail.send" in parent["permissions"] and "drive.read" in parent["permissions"]

    asyncio.run(_body())


# -- approval for sensitive ops ---------------------------------------------


def test_sensitive_send_requires_approval_then_sends_to_fake_outbox(tmp_path):
    async def _body():
        service, registry, adapter = _harness(tmp_path)
        registry.connect("fake-mail", FAKE_ACCOUNT)
        run = _make_run(service, ["browser.read"])
        service.grant_service_access(run["id"], "gmail.send")
        args = {"to": "friend@example.test", "subject": "Fictional hello",
                "body": "A made-up message for approval tests."}

        denied_task = asyncio.create_task(
            service._execute_tool(run, None, "gmail.send", args, "call-deny"))
        for _ in range(100):
            if (run["id"], "call-deny") in service._pending_approvals:
                break
            await asyncio.sleep(0.02)
        assert (run["id"], "call-deny") in service._pending_approvals
        await service.control(run["id"], "deny", "call-deny")
        assert (await denied_task)["ok"] is False
        assert adapter.sent == []

        approved_task = asyncio.create_task(
            service._execute_tool(run, None, "gmail.send", args, "call-allow"))
        for _ in range(100):
            if (run["id"], "call-allow") in service._pending_approvals:
                break
            await asyncio.sleep(0.02)
        await service.control(run["id"], "approve", "call-allow")
        result = await approved_task
        assert result["ok"] is True
        assert result["fake"] is True and result["label"] == FAKE_SERVICE_LABEL
        assert len(adapter.sent) == 1
        assert adapter.sent[0]["to"] == "friend@example.test"
        approvals = [e for e in service.store.list_run_events(run["id"])
                     if e["type"] == "tool.approval_required" and e["tool"] == "gmail.send"]
        assert len(approvals) == 2

    asyncio.run(_body())


# -- Scenario C --------------------------------------------------------------


def test_scenario_c_unauthorized_send_denied_guard_case_nothing_sent(tmp_path):
    async def _body():
        service, registry, adapter = _harness(tmp_path)
        registry.connect("fake-mail", FAKE_ACCOUNT)
        run = _make_run(service, ["browser.read"])
        result = await service._execute_tool(run, None, "gmail.send", {
            "to": "friend@example.test", "subject": "Fictional attempt",
            "body": "This must never be sent without a grant."}, "call-c")
        assert result["ok"] is False
        assert "gmail.send" in result["error"]
        assert adapter.sent == [], "denied send must leave the outbox empty"

        events = service.store.list_run_events(run["id"])
        decisions = [e for e in events if e["type"] == "aip.decision"
                     and e.get("tool") == "gmail.send"]
        assert decisions and all(e["payload"]["outcome"] == "deny" for e in decisions)
        access_denied = [e for e in events if e["type"] == "service.access_denied"]
        assert len(access_denied) == 1
        assert access_denied[0].get("tool") == "gmail.send"
        assert not [e for e in events if e["type"] == "tool.execution.completed"
                    and e.get("tool") == "gmail.send" and e["status"] == "success"]

        cases = service.guard_observer.cases.list_cases()
        assert len(cases) == 1, "unauthorized send must open exactly one Guard case"
        assert cases[0]["signal"] == "service_access_denied"
        assert cases[0]["severity"] == "high"
        assert service.store.verify_guard_case_integrity(cases[0]["id"])
        await _cancel_screening(service)

    asyncio.run(_body())


def test_guard_minimal_evidence_withholds_full_bodies(tmp_path):
    async def _body():
        service, registry, _ = _harness(tmp_path)
        registry.connect("fake-mail", FAKE_ACCOUNT)
        run = _make_run(service, ["browser.read"])
        # Mirror the real loop: tool.proposed records the full arguments, so
        # the Guard bundle must truncate the body itself.
        service._event(run["id"], "tool.proposed", "pending", {
            "call_id": "call-body",
            "arguments": {"to": "friend@example.test", "subject": "Fictional attempt",
                          "body": LONG_BODY}}, "gmail.send")
        await service._execute_tool(run, None, "gmail.send", {
            "to": "friend@example.test", "subject": "Fictional attempt", "body": LONG_BODY},
            "call-body")
        cases = service.guard_observer.cases.list_cases()
        assert len(cases) == 1
        bundle = GuardEvidenceService(service.store).reconstruct_case(cases[0]["id"])
        dumped = json.dumps(bundle, ensure_ascii=False)
        assert LONG_BODY not in dumped, "full email bodies must not reach Guard bundles"
        assert "minimal-evidence" in dumped
        assert LONG_BODY[:100] in dumped, "a snippet stays for context"
        assert "Full message bodies are withheld by default" in dumped
        await _cancel_screening(service)

    asyncio.run(_body())


# -- fake-labeled reads + loud failures --------------------------------------


def test_service_reads_return_fake_labeled_fixtures(tmp_path):
    async def _body():
        service, _, _ = _harness(tmp_path)
        run = _make_run(service, ["browser.read"])
        for permission in ("calendar.read", "drive.read", "github.read", "media.open",
                           "media.control"):
            service.grant_service_access(run["id"], permission)
        reads = [
            ("calendar.read", {}),
            ("drive.read", {}),
            ("github.read", {}),
            ("media.open", {"target": "local-video"}),
            ("media.control", {"action": "pause"}),
        ]
        for tool, args in reads:
            result = await service._execute_tool(run, None, tool, args, f"call-{tool}")
            assert result["ok"] is True, tool
            assert result["fake"] is True and result["label"] == FAKE_SERVICE_LABEL, tool

    asyncio.run(_body())


def test_revoked_account_blocks_send_even_with_grant(tmp_path):
    async def _body():
        service, registry, adapter = _harness(tmp_path)
        registry.connect("fake-mail", FAKE_ACCOUNT)
        registry.revoke("fake-mail", FAKE_ACCOUNT)
        run = _make_run(service, ["browser.read"])
        service.grant_service_access(run["id"], "gmail.send")
        args = {"to": "friend@example.test", "subject": "Fictional", "body": "Made-up."}
        task = asyncio.create_task(
            service._execute_tool(run, None, "gmail.send", args, "call-revoked"))
        for _ in range(100):
            if (run["id"], "call-revoked") in service._pending_approvals:
                break
            await asyncio.sleep(0.02)
        await service.control(run["id"], "approve", "call-revoked")
        result = await task
        assert result["ok"] is False
        assert "revoked" in result["error"]
        assert adapter.sent == []

    asyncio.run(_body())


def test_live_provider_send_fails_loudly_without_substitution(tmp_path):
    async def _body():
        service, registry, adapter = _harness(tmp_path)
        registry.connect("gmail", "ada@example.test")
        run = _make_run(service, ["browser.read"])
        service.grant_service_access(run["id"], "gmail.send")
        args = {"provider": "gmail", "email": "ada@example.test",
                "to": "friend@example.test", "subject": "Fictional", "body": "Made-up."}
        task = asyncio.create_task(
            service._execute_tool(run, None, "gmail.send", args, "call-live"))
        for _ in range(100):
            if (run["id"], "call-live") in service._pending_approvals:
                break
            await asyncio.sleep(0.02)
        await service.control(run["id"], "approve", "call-live")
        result = await task
        assert result["ok"] is False
        assert "Nothing was sent" in result["error"]
        assert adapter.sent == []

    asyncio.run(_body())
