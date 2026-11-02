from runtime.aip.policy import authorize_tool


ORIGIN = ("https", "docs.python.org", 443)


def evaluate(tool, arguments=None, **extra):
    options = {"public_host_check": lambda _: True}
    options.update(extra)
    return authorize_tool(
        agent_id="agent-a", registered_agent_id="agent-a",
        goal_id="goal-a", registered_goal_id="goal-a",
        permissions={"browser.read", "browser.interact", "delegate"},
        tool_name=tool, arguments=arguments or {}, authorized_origin=ORIGIN,
        **options,
    )


def test_authorizes_user_approved_origin():
    decision = evaluate("browser.navigate", {"url": "https://docs.python.org/3/library/asyncio.html"})
    assert decision.outcome == "allow"


def test_denies_unauthorized_origin_before_tool_execution():
    decision = evaluate("browser.navigate", {"url": "https://attacker.example/collect"})
    assert decision.outcome == "deny"
    assert "authorized" in decision.reason


def test_denies_private_origin_and_non_https():
    private = evaluate("browser.navigate", {"url": "https://docs.python.org/"}, public_host_check=lambda _: False)
    plain_http = evaluate("browser.navigate", {"url": "http://docs.python.org/"})
    assert private.outcome == "deny"
    assert plain_http.outcome == "deny"


def test_page_interaction_requires_explicit_approval():
    pending = evaluate("browser.click", {"selector": "#continue"}, current_url="https://docs.python.org/3/")
    approved = evaluate("browser.click", {"selector": "#continue"}, current_url="https://docs.python.org/3/", user_approved=True)
    assert pending.outcome == "require_approval"
    assert approved.outcome == "allow"


def test_every_page_action_is_bound_to_current_authorized_origin():
    closed = evaluate("browser.inspect")
    wrong_origin = evaluate("browser.screenshot", current_url="https://attacker.example/page")
    assert closed.outcome == wrong_origin.outcome == "deny"


def test_unregistered_tool_is_denied():
    assert evaluate("browser.execute_javascript").outcome == "deny"


def test_identity_and_goal_mismatch_are_denied():
    identity = authorize_tool(agent_id="agent-b", registered_agent_id="agent-a", goal_id="goal-a",
        registered_goal_id="goal-a", permissions={"browser.read"}, tool_name="browser.inspect",
        arguments={}, authorized_origin=ORIGIN)
    goal = authorize_tool(agent_id="agent-a", registered_agent_id="agent-a", goal_id="goal-b",
        registered_goal_id="goal-a", permissions={"browser.read"}, tool_name="browser.inspect",
        arguments={}, authorized_origin=ORIGIN)
    assert identity.outcome == goal.outcome == "deny"


def test_child_scope_cannot_interact_or_delegate_without_grant():
    decision = authorize_tool(agent_id="child", registered_agent_id="child", goal_id="goal-a",
        registered_goal_id="goal-a", permissions={"browser.read"}, tool_name="browser.click",
        arguments={"selector":"#submit"}, authorized_origin=ORIGIN)
    delegation = authorize_tool(agent_id="child", registered_agent_id="child", goal_id="goal-a",
        registered_goal_id="goal-a", permissions={"browser.read"}, tool_name="agent.delegate",
        arguments={"task":"Find an article"}, authorized_origin=ORIGIN, delegation_allowed=True)
    assert decision.outcome == delegation.outcome == "deny"
