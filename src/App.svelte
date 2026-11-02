<script lang="ts">
  import { onMount } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import { listen } from "@tauri-apps/api/event";
  import type { Agent, AppState, Evidence, Goal, KeplerEvent, Resource } from "./types";
  import { initialState, makeId, STORAGE_KEY } from "./simulation/seed";
  import { addTask, advanceSimulationClock, appendEvent, auditAction, canSpawn, createAgent, createGoal, spawnSubagent, transitionAgent } from "./simulation/runtime";

  type Presence = "resting" | "listening" | "processing" | "coordinating" | "authorization" | "investigating" | "unavailable";
  type IntentStep = "idle" | "listening" | "captured" | "interpreting" | "intent_ready" | "unavailable";
  type PlanPhase = "idle" | "running" | "complete";
  type TextSize = "standard" | "large" | "extra-large";
  type Panel = "settings" | "providers" | "timeline" | "create_goal" | "resource" | "resource_detail" | "goal" | "agent" | "incident" | "approval" | null;
  type MenuItem = "intent" | "agents" | "activity" | "guard" | "providers" | "settings";
  type RuntimeEvent = { id: string; sequence: number; run_id: string; agent_id: string; goal_id: string; type: string; status: string; tool?: string; payload: Record<string, unknown>; timestamp: string };
  type RuntimeAgent = { id: string; agent_id: string; parent_agent_id?: string | null; goal_id: string; goal_title: string; task: string; provider: string; model: string; status: string; permissions: string[]; step_budget: number; token_budget: number; steps: number; tokens_used: number; error?: string | null; created_at: string; updated_at: string; result?: { summary?: string; findings?: Array<{ id: string; finding: string; quote: string; source_url: string; created_at: string }>; source_url?: string } | null };
  type RuntimeSnapshot = { agents: RuntimeAgent[]; events: RuntimeEvent[]; evidence: Array<{ id: string; goal_id: string; agent_id: string; run_id: string; finding: string; source_url: string; quote: string; created_at: string }>; last_sequence: number };
  type Selection = { kind: "goal" | "agent" | "resource" | "incident"; id: string } | null;

  let state: AppState = structuredClone(initialState);
  let hydrated = false;
  let selected: Selection = { kind: "goal", id: "goal-research" };
  let panel: Panel = null;
  let runtimeStatus: "starting" | "ready" | "unavailable" = "starting";
  let runtimeError = "";
  let runtimeModelConfigured = false;
  let runtimeSourceUrl = "https://docs.python.org/3/library/asyncio.html";
  let startingRuntimeRun = false;
  let pendingRuntimeApprovals: Array<{ runId: string; callId: string; goalId: string; tool: string; arguments: Record<string, unknown> }> = [];
  let runtimeUnlisten: (() => void) | null = null;
  let runtimeSyncTimer: ReturnType<typeof setInterval>;
  let connectedCredentials: string[] = [];
  let metaApiKey = "";
  let metaConnected = false;
  let savingCredential = false;
  let chatgptConnected = false;
  let chatgptEmail = "";
  let chatgptSharing = false;
  let connectingChatgpt = false;
  let chatgptConnectError = "";
  let activeProvider = "meta";
  let activeModel = "muse-spark-1.3";
  let connectProviderId = "openai";
  let connectEndpoint = "https://api.openai.com/v1";
  let connectModel = "gpt-4.1-mini";
  let connectSecret = "";
  let connectingProvider = false;
  let resourceView = false;
  let textSize: TextSize = "large";
  let reduceMotion = false;
  let simpleMode = true;
  let currentMenu: MenuItem = "intent";
  let presence: Presence = "resting";
  let intentStep: IntentStep = "idle";
  let planPhase: PlanPhase = "idle";
  let planStep = 0;
  let showPdfPreview = false;
  let activePlanGoalId = "";
  let completedPlanGoalIds: string[] = [];
  let planTimer: ReturnType<typeof setTimeout>;
  let chosenIntent = "Research coastal adaptation strategies and summarize the strongest evidence.";
  let newDescription = "";
  let newTask = "";
  let resourceUrl = "https://www.nps.gov/subjects/climatechange/index.htm";
  let resourceLabel = "Coastal adaptation field guide";
  let micLevel = 0;
  let audioPhase = 0;
  let micError = "";
  let notice = "";
  let audioStream: MediaStream | null = null;
  let audioContext: AudioContext | null = null;
  let audioRequestId = 0;
  let themeMedia: MediaQueryList | null = null;
  let animationFrame = 0;
  let toastTimer: ReturnType<typeof setTimeout>;

  const intents = [
    "Research coastal adaptation strategies and summarize the strongest evidence.",
    "Help me organize my spring university enrollment requirements.",
    "Compare public transit options for a weekend trip to Montreal.",
  ];
  const ubIntent = "I’m an MS student at University at Buffalo in AI and Data Innovation. Next semester is my next semester. I’ve completed EAS 501, EAS 508, EAS 510, and CSE 676. Do not recommend machine learning, data-intensive computing, computer vision, or reinforcement learning because I took those in undergrad. Help me choose courses that move me toward AI research in LLMs and world models, including cognitive science, and create a draft schedule PDF for me to review.";
  const planStatusKey = `${STORAGE_KEY}:ub-research-plans`;
  const textSizeKey = `${STORAGE_KEY}:text-size`;
  const reduceMotionKey = `${STORAGE_KEY}:reduce-motion`;
  const simpleModeKey = `${STORAGE_KEY}:simple-mode`;
  const planSteps = ["Understand what you want to study", "Check classes you already took and want to skip", "Review UB’s program courses", "Find classes for language models, world models, and cognition", "Check course requirements and open questions", "Prepare your schedule PDF"];
  const demoResources = [
    { label: "Coastal adaptation field guide", url: "https://www.nps.gov/subjects/climatechange/index.htm" },
    { label: "Open climate research", url: "https://www.noaa.gov/climate" },
    { label: "Wikipedia · Coastal management", url: "https://en.wikipedia.org/wiki/Coastal_management" },
  ];
  const audioParticles = Array.from({ length: 58 }, (_, index) => ({
    id: index,
    angle: index * 2.399963,
    orbit: 13 + (index % 11) * 2.45,
    size: 2 + (index % 4),
    speed: 0.28 + (index % 9) * 0.085,
    tone: index % 13 === 0 ? "warm" : index % 5 === 0 ? "soft" : "",
  }));

  onMount(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const savedState = JSON.parse(stored) as AppState;
        state = {
          ...structuredClone(initialState),
          ...savedState,
          agents: (savedState.agents ?? initialState.agents).map((agent) => ({
            ...agent,
            provider: ["Local simulation", "OpenAI · simulated"].includes(agent.provider) ? "On device" : agent.provider,
            model: ["Deterministic", "Provider label only"].includes(agent.model) ? (agent.name === "Planner" ? "Course Planner" : "Researcher") : agent.model,
          })),
          events: (savedState.events ?? initialState.events).map((event) => ({
            ...event,
            ...(typeof event.payload.summary === "string" ? { payload: { ...event.payload, summary: cleanWorkspaceCopy(event.payload.summary) } } : {}),
          })),
          ...(savedState.approval ? { approval: { ...savedState.approval, action: cleanWorkspaceCopy(savedState.approval.action) } } : {}),
          ...(savedState.incident ? { incident: { ...savedState.incident, interview: savedState.incident.interview.map(cleanWorkspaceCopy) } } : {}),
        };
      }
      const savedPlans = localStorage.getItem(planStatusKey);
      if (savedPlans) completedPlanGoalIds = JSON.parse(savedPlans);
      const savedTextSize = localStorage.getItem(textSizeKey);
      if (savedTextSize === "standard" || savedTextSize === "large" || savedTextSize === "extra-large") textSize = savedTextSize;
      reduceMotion = localStorage.getItem(reduceMotionKey) === "true";
      const savedSimpleMode = localStorage.getItem(simpleModeKey);
      if (savedSimpleMode !== null) simpleMode = savedSimpleMode !== "false";
    } catch { showNotice("Saved workspace data could not be read. Starting with a fresh workspace."); }
    hydrated = true;
    applyTheme();
    void invoke<{ connected: string[]; meta_configured: boolean; meta_credential_saved?: boolean; active_provider?: string; active_model?: string; chatgpt_connected?: boolean; chatgpt_email?: string | null; chatgpt_sharing?: boolean }>("credential_status").then((result) => {
      connectedCredentials = result.connected;
      runtimeModelConfigured = result.meta_configured;
      metaConnected = Boolean(result.meta_credential_saved);
      activeProvider = result.active_provider ?? "meta";
      activeModel = result.active_model ?? "muse-spark-1.3";
      chatgptConnected = Boolean(result.chatgpt_connected);
      chatgptEmail = result.chatgpt_email ?? "";
      chatgptSharing = Boolean(result.chatgpt_sharing);
      if (activeProvider !== "meta" && activeProvider !== "chatgpt") chooseConnectProvider(activeProvider);
    }).catch(() => {});
    let disposed = false;
    void listen<RuntimeEvent>("runtime:event", (event) => mergeRuntimeEvents([event.payload])).then((unlisten) => {
      if (disposed) unlisten(); else runtimeUnlisten = unlisten;
    }).catch(() => { runtimeStatus = "unavailable"; runtimeError = "Native runtime events are unavailable in this window."; });
    void invoke<{ status: string; model_configured?: boolean }>("runtime_start").then((result) => {
      runtimeStatus = result.status === "ready" ? "ready" : "unavailable";
      runtimeModelConfigured = Boolean(result.model_configured);
      runtimeError = runtimeModelConfigured ? "" : "Connect Meta Muse in LLM providers to use research.";
      return invoke<RuntimeSnapshot>("runtime_snapshot");
    }).then((snapshot) => {
      syncRuntimeSnapshot(snapshot);
      runtimeSyncTimer = setInterval(() => {
        void invoke<RuntimeSnapshot>("runtime_snapshot").then(syncRuntimeSnapshot).catch(() => { runtimeStatus = "unavailable"; });
      }, 1400);
    }).catch((error) => {
      runtimeStatus = "unavailable";
      runtimeError = error instanceof Error ? error.message : String(error);
    });
    themeMedia = matchMedia("(prefers-color-scheme: dark)");
    const onAppearanceChange = () => { if (state.theme === "system") applyTheme(); };
    themeMedia.addEventListener("change", onAppearanceChange);
    return () => { disposed = true; runtimeUnlisten?.(); clearInterval(runtimeSyncTimer); stopAudio(); clearTimeout(toastTimer); clearTimeout(planTimer); themeMedia?.removeEventListener("change", onAppearanceChange); };
  });

  $: goals = state.goals;
  $: agents = state.agents;
  $: activeGoal = goals.find((goal) => goal.id === (selected?.kind === "goal" ? selected.id : selected?.kind === "agent" ? agents.find((agent) => agent.id === selected?.id)?.goalId : selected?.kind === "incident" ? state.incident?.goalId : state.resources.find((resource) => resource.id === selected?.id)?.goalId)) ?? goals[0];
  $: activeAgent = selected?.kind === "agent" ? agents.find((agent) => agent.id === selected?.id) : null;
  $: activeResource = selected?.kind === "resource" ? state.resources.find((resource) => resource.id === selected?.id) : null;
  $: activeIncident = selected?.kind === "incident" && state.incident?.id === selected.id ? state.incident : null;
  $: goalAgents = activeGoal ? agents.filter((agent) => agent.goalId === activeGoal.id) : [];
  $: activeRuntimeAgents = activeGoal ? agents.filter((agent) => agent.runtime && agent.goalId === activeGoal.id) : [];
  $: activeEvidence = activeGoal ? state.evidence.filter((item) => item.goalId === activeGoal.id) : [];
  $: activePendingApprovals = activeGoal ? pendingRuntimeApprovals.filter((item) => item.goalId === activeGoal.id) : [];
  $: pendingCount = Number(state.approval?.status === "pending") + Number(state.incident?.status === "open") + pendingRuntimeApprovals.length;
  $: if (hydrated) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    localStorage.setItem(textSizeKey, textSize);
    localStorage.setItem(reduceMotionKey, String(reduceMotion));
    localStorage.setItem(simpleModeKey, String(simpleMode));
    applyTheme();
  }

  function applyTheme() {
    const choice = state.theme;
    document.documentElement.dataset.theme = choice === "system" ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : choice;
    document.documentElement.style.colorScheme = document.documentElement.dataset.theme;
    document.documentElement.dataset.textSize = textSize;
    document.documentElement.classList.toggle("reduce-motion", reduceMotion);
    document.documentElement.classList.toggle("simple-mode", simpleMode);
  }
  function commit(next: AppState) { state = next; }
  function showNotice(message: string) { notice = message; clearTimeout(toastTimer); toastTimer = setTimeout(() => notice = "", 3200); }
  function closePanel() { panel = null; }
  async function saveMetaCredential(event: SubmitEvent) {
    event.preventDefault();
    if (!metaApiKey.trim() || savingCredential) return;
    savingCredential = true;
    try {
      const result = await invoke<{ provider: string; model: string; runtime: { status?: string; model_configured?: boolean } }>("provider_connect", { provider: "meta", endpoint: "https://api.meta.ai/v1", model: "muse-spark-1.3", secret: metaApiKey });
      metaApiKey = "";
      runtimeStatus = result.runtime.status === "ready" ? "ready" : "unavailable";
      runtimeModelConfigured = Boolean(result.runtime.model_configured);
      runtimeError = "";
      connectedCredentials = [...new Set([...connectedCredentials, "meta"])];
      metaConnected = true;
      activeProvider = "meta";
      activeModel = result.model;
      showNotice("Meta Muse connected. Your credential is stored in the system keychain.");
    } catch (error) {
      metaApiKey = "";
      showNotice(error instanceof Error ? error.message : String(error));
    } finally { savingCredential = false; }
  }
  async function removeCredential(provider: string) {
    try {
      const result = await invoke<{ status?: string; model_configured?: boolean; runtime?: { status?: string; model_configured?: boolean } }>("credential_delete", { provider });
      connectedCredentials = connectedCredentials.filter((item) => item !== provider);
      if (provider === "meta") metaConnected = false;
      if (result.runtime) { activeProvider = "meta"; activeModel = "muse-spark-1.3"; runtimeStatus = result.runtime.status === "ready" ? "ready" : "unavailable"; runtimeModelConfigured = Boolean(result.runtime.model_configured); }
      if (provider === "meta") { const runtime = result.runtime ?? result; runtimeStatus = runtime.status === "ready" ? "ready" : "unavailable"; runtimeModelConfigured = Boolean(runtime.model_configured); runtimeError = "Connect Meta Muse in LLM providers to use research."; }
      showNotice("Provider credential removed from the system keychain.");
    } catch (error) { showNotice(error instanceof Error ? error.message : String(error)); }
  }
  async function connectChatGPT() {
    if (connectingChatgpt) return;
    connectingChatgpt = true;
    chatgptConnectError = "";
    try {
      const result = await invoke<{ connected: boolean; email?: string; sharing: boolean; model?: string; model_error?: string | null; runtime: { status?: string; model_configured?: boolean } }>("credential_chatgpt_connect");
      chatgptConnected = result.connected;
      chatgptEmail = result.email ?? "";
      chatgptSharing = result.sharing;
      runtimeModelConfigured = Boolean(result.runtime.model_configured);
      runtimeStatus = result.runtime.status === "ready" ? "ready" : "unavailable";
      connectedCredentials = [...new Set([...connectedCredentials, "chatgpt"])];
      if (chatgptSharing) { activeProvider = "chatgpt"; activeModel = result.model ?? activeModel; }
      else if (activeProvider === "chatgpt") { activeProvider = "meta"; activeModel = "muse-spark-1.3"; }
      chatgptConnectError = result.model_error ?? "";
      showNotice(chatgptConnectError ? "ChatGPT account connected. KeplerV is still setting up model access." : chatgptSharing ? "ChatGPT connected and ready for research." : "ChatGPT account connected, but plan usage was not authorized.");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      // A connection can be stored even if a later runtime step fails. Refresh the
      // provider state so the card reflects the keychain as the source of truth.
      try {
        const saved = await invoke<{ connected: string[]; chatgpt_connected?: boolean; chatgpt_email?: string | null; chatgpt_sharing?: boolean }>("credential_status");
        chatgptConnected = Boolean(saved.chatgpt_connected);
        chatgptEmail = saved.chatgpt_email ?? "";
        chatgptSharing = Boolean(saved.chatgpt_sharing);
        connectedCredentials = saved.connected;
      } catch { /* preserve the original connection error */ }
      chatgptConnectError = message;
      showNotice(message);
    }
    finally { connectingChatgpt = false; }
  }
  async function disconnectChatGPT() {
    try {
      const result = await invoke<{ runtime: { status?: string; model_configured?: boolean }; remote_revoked: boolean }>("credential_chatgpt_disconnect");
      chatgptConnected = false; chatgptEmail = ""; chatgptSharing = false;
      connectedCredentials = connectedCredentials.filter((item) => item !== "chatgpt");
      if (activeProvider === "chatgpt") { activeProvider = "meta"; activeModel = "muse-spark-1.3"; }
      runtimeModelConfigured = Boolean(result.runtime.model_configured);
      runtimeStatus = result.runtime.status === "ready" ? "ready" : "unavailable";
      showNotice(result.remote_revoked ? "ChatGPT disconnected." : "ChatGPT disconnected on this device. Remote sign-out could not be confirmed.");
    } catch (error) { showNotice(error instanceof Error ? error.message : String(error)); }
  }
  function chooseConnectProvider(provider: string) {
    connectProviderId = provider;
    const defaults: Record<string, { endpoint: string; model: string }> = {
      openai: { endpoint: "https://api.openai.com/v1", model: "gpt-4.1-mini" },
      deepseek: { endpoint: "https://api.deepseek.com", model: "deepseek-flash" },
      anthropic: { endpoint: "https://api.anthropic.com/v1", model: "" },
      google: { endpoint: "https://generativelanguage.googleapis.com/v1beta", model: "" },
      kimi: { endpoint: "https://api.moonshot.ai/v1", model: "kimi-k2.5" },
      openrouter: { endpoint: "https://openrouter.ai/api/v1", model: "openai/gpt-4.1-mini" },
      groq: { endpoint: "https://api.groq.com/openai/v1", model: "llama-3.3-70b-versatile" },
      mistral: { endpoint: "https://api.mistral.ai/v1", model: "mistral-small-latest" },
      "local-ollama": { endpoint: "http://localhost:11434/v1", model: "qwen3:8b" },
      "local-lmstudio": { endpoint: "http://localhost:1234/v1", model: "" },
      "local-openai-compatible": { endpoint: "http://localhost:8080/v1", model: "" },
      "openai-compatible": { endpoint: "https://", model: "" },
    };
    connectEndpoint = defaults[provider]?.endpoint ?? "";
    connectModel = defaults[provider]?.model ?? "";
    connectSecret = "";
    void invoke<{ endpoint: string; model: string }>("provider_configuration", { provider }).then((saved) => {
      if (connectProviderId === provider && saved.endpoint) { connectEndpoint = saved.endpoint; connectModel = saved.model; }
    }).catch(() => {});
  }
  async function connectConfiguredProvider(event: SubmitEvent) {
    event.preventDefault();
    if (connectingProvider) return;
    connectingProvider = true;
    try {
      const result = await invoke<{ provider: string; model: string; runtime: { model_configured?: boolean } }>("provider_connect", {
        provider: connectProviderId, endpoint: connectEndpoint, model: connectModel, secret: connectSecret,
      });
      activeProvider = result.provider;
      activeModel = result.model;
      runtimeModelConfigured = Boolean(result.runtime.model_configured);
      runtimeStatus = "ready";
      connectedCredentials = [...new Set([...connectedCredentials, result.provider])];
      connectSecret = "";
      showNotice(`${result.provider} connected. ${result.model} is selected for research.`);
    } catch (error) {
      connectSecret = "";
      showNotice(error instanceof Error ? error.message : String(error));
    } finally { connectingProvider = false; }
  }
  async function activateProviderConnection(provider: string) {
    try {
      const result = await invoke<{ provider: string; model: string; runtime: { model_configured?: boolean } }>("provider_activate", { provider });
      activeProvider = result.provider; activeModel = result.model; runtimeModelConfigured = Boolean(result.runtime.model_configured); runtimeStatus = "ready";
      showNotice(`${provider} is active for research.`);
    } catch (error) { showNotice(error instanceof Error ? error.message : String(error)); }
  }
  function mapRuntimeStatus(status: string): Agent["status"] {
    return status === "running" ? "working" : status === "created" || status === "idle" ? "waiting" : status === "completed" ? "completed" : status === "failed" ? "failed" : status === "blocked" ? "investigating" : status === "stopped" ? "stopped" : "paused";
  }
  function mapRuntimeAgent(item: RuntimeAgent): Agent {
    const providerName = item.provider === "meta" ? "Meta Muse" : item.provider === "chatgpt" ? "ChatGPT" : item.provider;
    return { id: item.agent_id, name: item.parent_agent_id ? "Research subagent" : item.provider === "meta" ? "Muse researcher" : `${providerName} researcher`, provider: providerName, model: item.model, ...(item.parent_agent_id ? { parentAgentId: item.parent_agent_id } : {}), goalId: item.goal_id, status: mapRuntimeStatus(item.status), permissions: item.permissions, capabilities: item.permissions, currentTask: item.task, createdAt: item.created_at, steps: item.steps, error: item.error, runtime: true, runId: item.id };
  }
  function runtimeEventSummary(event: RuntimeEvent): string {
    const detail = event.payload.summary ?? event.payload.reason ?? event.payload.message;
    if (typeof detail === "string" && detail.length) return detail;
    if (event.type === "model.request.started") return `Requesting ${String(event.payload.model ?? "model")}`;
    if (event.type === "model.response.received") return "Model response received";
    if (event.type === "tool.proposed") return `Proposed ${event.tool ?? "tool action"}`;
    if (event.type === "tool.execution.started") return `Running ${event.tool ?? "browser action"}`;
    if (event.type === "tool.execution.completed") return `${event.tool ?? "Browser action"} ${event.status}`;
    if (event.type === "aip.decision") return `AIP ${String(event.payload.outcome ?? event.status)} · ${String(event.payload.reason ?? "")}`;
    if (event.type === "agent.created") return "Muse research agent created";
    if (event.type === "agent.started") return "Agent started its research task";
    if (event.type === "agent.completed") return "Research completed";
    return event.type.replaceAll(".", " ");
  }
  function mergeRuntimeEvents(events: RuntimeEvent[]) {
    if (!events.length) return;
    const mapped: KeplerEvent[] = events.map((event) => ({
      id: event.id, timestamp: event.timestamp, agentId: event.agent_id, goalId: event.goal_id,
      type: event.type.replaceAll(".", "_") as KeplerEvent["type"],
      payload: { summary: runtimeEventSummary(event), runtimeStatus: event.status, tool: event.tool, runId: event.run_id, sequence: event.sequence, callId: event.payload.call_id, evidenceId: event.payload.evidence_id, sourceUrl: event.payload.source_url, outcome: event.payload.outcome, reason: event.payload.reason, usage: event.payload.usage, step: event.payload.step },
    }));
    const byId = new Map([...mapped, ...state.events].map((item) => [item.id, item]));
    state = { ...state, events: [...byId.values()].sort((a, b) => b.timestamp.localeCompare(a.timestamp)) };
    for (const event of events) {
      const callId = String(event.payload.call_id ?? "");
      if (event.type === "tool.approval_required" && callId) {
        if (!pendingRuntimeApprovals.some((item) => item.runId === event.run_id && item.callId === callId)) pendingRuntimeApprovals = [...pendingRuntimeApprovals, { runId: event.run_id, callId, goalId: event.goal_id, tool: event.tool ?? "browser interaction", arguments: (event.payload.arguments as Record<string, unknown>) ?? {} }];
      }
      if (["agent.stopped", "agent.failed"].includes(event.type)) pendingRuntimeApprovals = pendingRuntimeApprovals.filter((item) => item.runId !== event.run_id);
      else if (["tool.approval_denied", "tool.execution.started"].includes(event.type) && callId) pendingRuntimeApprovals = pendingRuntimeApprovals.filter((item) => !(item.runId === event.run_id && item.callId === callId));
    }
  }
  function syncRuntimeSnapshot(snapshot: RuntimeSnapshot) {
    const runtimeAgents = (snapshot.agents ?? []).map(mapRuntimeAgent);
    const byAgentId = new Map(runtimeAgents.map((item) => [item.id, item]));
    const agents = [...runtimeAgents, ...state.agents.filter((item) => !item.runtime && !byAgentId.has(item.id))];
    const evidence: Evidence[] = (snapshot.evidence ?? []).map((item) => ({ id: item.id, goalId: item.goal_id, agentId: item.agent_id, runId: item.run_id, finding: item.finding, sourceUrl: item.source_url, quote: item.quote, createdAt: item.created_at }));
    const resourceMap = new Map(state.resources.map((item) => [item.id, item]));
    for (const run of snapshot.agents ?? []) if (run.result?.source_url) resourceMap.set(`runtime-resource-${run.id}`, { id: `runtime-resource-${run.id}`, goalId: run.goal_id, agentId: run.agent_id, label: new URL(run.result.source_url).hostname, url: run.result.source_url });
    const progressByGoal = new Map<string, number>();
    for (const run of snapshot.agents ?? []) progressByGoal.set(run.goal_id, Math.max(progressByGoal.get(run.goal_id) ?? 0, run.status === "completed" ? 100 : Math.min(85, run.steps * 10)));
    state = { ...state, agents, evidence: [...new Map([...evidence, ...state.evidence].map((item) => [item.id, item])).values()], resources: [...resourceMap.values()], goals: state.goals.map((goal) => ({ ...goal, progress: Math.max(goal.progress, progressByGoal.get(goal.id) ?? 0), status: progressByGoal.get(goal.id) === 100 ? "completed" : goal.status })) };
    mergeRuntimeEvents(snapshot.events ?? []);
  }
  async function startMuseResearch() {
    if (!activeGoal || !runtimeModelConfigured || startingRuntimeRun) return;
    startingRuntimeRun = true;
    try {
      const run = await invoke<RuntimeAgent>("runtime_create_run", { goalId: activeGoal.id, goalTitle: activeGoal.title, goalDescription: activeGoal.description, task: "Research the user-authorized public technical article and summarize three findings with exact supporting quotes.", sourceUrl: runtimeSourceUrl.trim() });
      const agent = mapRuntimeAgent(run);
      state = { ...state, agents: [agent, ...state.agents.filter((item) => item.id !== agent.id)] };
      showNotice("Muse research agent started. Its real actions will appear in activity.");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      runtimeError = message; showNotice(message);
    } finally { startingRuntimeRun = false; }
  }
  async function controlRuntimeAgent(agent: Agent, action: "pause" | "resume" | "stop") {
    if (!agent.runId) return;
    try { await invoke("runtime_control", { runId: agent.runId, action }); }
    catch (error) { showNotice(error instanceof Error ? error.message : String(error)); }
  }
  async function resolveRuntimeApproval(item: (typeof pendingRuntimeApprovals)[number], action: "approve" | "deny") {
    try { await invoke("runtime_control", { runId: item.runId, action, callId: item.callId }); }
    catch (error) { showNotice(error instanceof Error ? error.message : String(error)); }
  }
  function openEvidenceSource(item: Evidence) {
    const resource = state.resources.find((entry) => entry.goalId === item.goalId && entry.url === item.sourceUrl);
    if (!resource) { showNotice("The source link is not available in this workspace."); return; }
    selected = { kind: "resource", id: resource.id }; panel = null; resourceView = true;
  }
  function isUbResearchIntent(value: string) { return /university at buffalo|\bUB\b|ai and data innovation|\bEAS\s*501\b|\bCSE\s*635\b/i.test(value); }
  function graphAgentY(index: number, total: number) { return 26 + ((index + 0.5) / Math.max(total, 1)) * 50; }
  function goalTaskStatus(goal: Goal, index: number) {
    if (!isUbResearchIntent(`${goal.title} ${goal.description}`)) return index === 0 && agents.some((agent) => agent.goalId === goal.id && agent.status === "working") ? "WORKING" : "TO DO";
    if (completedPlanGoalIds.includes(goal.id) || goal.progress >= 100) return "DONE";
    if (activePlanGoalId === goal.id && planPhase === "running") {
      if (index < planStep) return "DONE";
      if (index === planStep) return "WORKING";
    }
    return "TO DO";
  }
  function openNewIntention() { newDescription = ubIntent; panel = "create_goal"; }
  function selectGoalFromPicker(event: Event) {
    const id = (event.currentTarget as HTMLSelectElement).value;
    const goal = goals.find((item) => item.id === id);
    if (goal) selectGoal(goal);
  }
  function startResearchPlan(goalId = activeGoal?.id ?? "") {
    clearTimeout(planTimer); planPhase = "running"; planStep = Math.min(planSteps.length, Math.floor(((state.goals.find((goal) => goal.id === goalId)?.progress ?? 0) / 100) * planSteps.length)); showPdfPreview = false; panel = null; currentMenu = "intent";
    activePlanGoalId = goalId;
    let step = planStep;
    const advance = () => {
      planStep = Math.min(step + 1, planSteps.length);
      if (activePlanGoalId) commit({ ...state, goals: state.goals.map((goal) => goal.id === activePlanGoalId ? { ...goal, progress: Math.round((planStep / planSteps.length) * 100) } : goal) });
      if (step >= planSteps.length - 1) {
        planStep = planSteps.length; planPhase = "complete";
        if (activePlanGoalId && !completedPlanGoalIds.includes(activePlanGoalId)) {
          completedPlanGoalIds = [...completedPlanGoalIds, activePlanGoalId];
          localStorage.setItem(planStatusKey, JSON.stringify(completedPlanGoalIds));
        }
        return;
      }
      step += 1; planTimer = setTimeout(advance, 920);
    };
    planTimer = setTimeout(advance, 300);
  }
  function openSchedulePreview(goalId: string) { activePlanGoalId = goalId; planStep = planSteps.length; planPhase = "complete"; showPdfPreview = true; panel = null; }
  function selectGoal(goal: Goal) {
    selected = { kind: "goal", id: goal.id };
    if (isUbResearchIntent(`${goal.title} ${goal.description}`) && !completedPlanGoalIds.includes(goal.id)) { startResearchPlan(goal.id); return; }
    panel = "goal";
  }
  function closeResearchPlan() { clearTimeout(planTimer); planPhase = "idle"; showPdfPreview = false; }
  function chooseMenu(item: MenuItem) {
    currentMenu = item;
    if (item === "intent") { panel = null; return; }
    if (item === "activity") { panel = "timeline"; return; }
    if (item === "settings") { panel = "settings"; return; }
    if (item === "providers") { panel = "providers"; return; }
    if (item === "guard") { runGuardScenario(); return; }
    const agent = goalAgents[0] ?? agents[0];
    if (agent) { selected = { kind: "agent", id: agent.id }; panel = "agent"; }
    else { panel = "goal"; }
  }
  function openAudio() { panel = null; currentMenu = "intent"; void startAudio(); }
  function particleStyle(particle: (typeof audioParticles)[number], phase: number, level: number) {
    const angle = particle.angle + phase * particle.speed + level * 1.8;
    const radius = particle.orbit * (0.48 + level * 1.18);
    const x = 50 + Math.cos(angle) * radius;
    const y = 49 + Math.sin(angle) * radius * 0.67;
    const size = particle.size + level * (particle.id % 3 + 1.8);
    return `left:${x}%;top:${y}%;width:${size}px;height:${size}px;opacity:${0.32 + level * 0.62};transform:translate(-50%,-50%) scale(${0.82 + level * 1.35})`;
  }
  function meterStyle(index: number, level: number, phase: number) {
    const shape = Math.abs(Math.sin(index * 0.55 + phase * 1.5));
    const height = 5 + level * (12 + shape * 34);
    return `height:${height}px`;
  }
  function cleanWorkspaceCopy(value: string) {
    return value
      .replaceAll("Fictional payment approved", "Payment request approved")
      .replaceAll("Fictional payment denied", "Payment request declined")
      .replaceAll("Fictional tuition payment", "Tuition payment request")
      .replaceAll("Fictional payment", "Payment request")
      .replaceAll("fictional funds", "requested amount")
      .replaceAll("Guard opened a deterministic investigation", "Guard opened an investigation")
      .replaceAll("Deterministic Guard interview reviewed", "Guard interview reviewed")
      .replaceAll("Simulation clock advanced by one minute", "Workspace clock advanced by one minute")
      .replaceAll("simulation clock advanced", "workspace clock advanced")
      .replaceAll("Simulated agent:", "Agent:");
  }
  function eventLabel(event: KeplerEvent) { return cleanWorkspaceCopy(String(event.payload.summary ?? event.type.replaceAll("_", " "))); }
  function eventTypeLabel(event: KeplerEvent) { return event.type === "simulation_clock_advanced" ? "workspace clock advanced" : event.type.replaceAll("_", " "); }

  async function startAudio() {
    const requestId = ++audioRequestId;
    micError = "";
    intentStep = "listening"; presence = "listening"; audioPhase = 0;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
      if (requestId !== audioRequestId) { stream.getTracks().forEach((track) => track.stop()); return; }
      audioStream = stream;
      audioContext = new AudioContext();
      const source = audioContext.createMediaStreamSource(audioStream);
      const analyser = audioContext.createAnalyser(); analyser.fftSize = 256; source.connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const sample = () => {
        if (!audioStream || !audioContext) return;
        analyser.getByteTimeDomainData(data);
        let total = 0; for (const value of data) { const centered = (value - 128) / 128; total += centered * centered; }
        micLevel = Math.min(1, Math.sqrt(total / data.length) * 3.2);
        audioPhase += 0.003 + micLevel * 0.11;
        animationFrame = requestAnimationFrame(sample);
      };
      sample();
    } catch (error) {
      if (requestId !== audioRequestId) return;
      audioStream?.getTracks().forEach((track) => track.stop()); audioStream = null;
      if (audioContext) await audioContext.close().catch(() => {}); audioContext = null;
      micLevel = 0;
      micError = error instanceof Error ? error.message : "Microphone access was denied or is unavailable.";
      presence = "unavailable"; intentStep = "unavailable";
    }
  }

  async function stopAudio() {
    audioRequestId += 1;
    cancelAnimationFrame(animationFrame);
    audioStream?.getTracks().forEach((track) => track.stop()); audioStream = null;
    if (audioContext) await audioContext.close().catch(() => {}); audioContext = null; micLevel = 0;
    if (intentStep === "listening") { intentStep = "captured"; presence = "processing"; }
  }
  function cancelIntent() { audioRequestId += 1; void stopAudio(); intentStep = "idle"; presence = "resting"; micError = ""; }
  async function interpretIntent() {
    intentStep = "interpreting"; presence = "processing";
    await new Promise((resolve) => setTimeout(resolve, 550));
    intentStep = "intent_ready"; presence = "resting";
  }
  function createGoalFromIntent() {
    const title = chosenIntent.includes("enrollment") ? "Prepare for spring enrollment" : chosenIntent.includes("transit") ? "Plan a Montreal weekend" : "Research coastal adaptation";
    const next = createGoal(state, title, chosenIntent);
    const goal = next.goals[0];
    commit(next); selected = { kind: "goal", id: goal.id }; panel = "goal"; intentStep = "idle";
    showNotice("Goal created and saved on this device.");
  }
  function submitGoal(event: SubmitEvent) {
    event.preventDefault(); if (!newDescription.trim()) return;
    const description = newDescription.trim();
    const hasUbPlan = isUbResearchIntent(description);
    const title = hasUbPlan ? "Plan my UB semester for AI research" : description.split(/[.!?]/)[0].trim().slice(0, 64) || "New intention";
    const next = createGoal(state, title, description);
    let goal = next.goals[0];
    const withPlanTasks = hasUbPlan ? { ...next, goals: next.goals.map((item) => item.id === goal.id ? { ...item, tasks: planSteps } : item) } : next;
    goal = withPlanTasks.goals[0]; commit(withPlanTasks); selected = { kind: "goal", id: goal.id }; newDescription = ""; panel = "goal";
    if (hasUbPlan) startResearchPlan(goal.id);
  }
  function addGoalTask(event: SubmitEvent) {
    event.preventDefault(); if (!activeGoal || !newTask.trim()) return;
    commit(addTask(state, activeGoal.id, newTask.trim())); newTask = ""; showNotice("Task added to the goal graph.");
  }
  function assignAgent() { if (!activeGoal) return; commit(createAgent(state, activeGoal.id)); showNotice("Agent added to this intention."); }
  function toggleAgent(agent: Agent) {
    if (agent.status === "working") commit(transitionAgent(state, agent.id, "pause"));
    else if (agent.status === "paused" || agent.status === "waiting") commit(transitionAgent(state, agent.id, "resume"));
    else showNotice("This agent has reached a terminal state.");
  }
  function spawn(agent: Agent) {
    const next = spawnSubagent(state, agent.id);
    if (next === state) showNotice("Delegation limit reached, or this agent cannot delegate.");
    else { commit(next); showNotice("Permission-limited subagent spawned."); }
  }
  function evaluateExample(agent: Agent) {
    const result = auditAction(state, agent.id, "read_private"); commit(result.state);
    showNotice(`AIP ${result.outcome.toUpperCase()}: ${result.reason}`);
  }
  function requestPayment(agent: Agent) {
    const result = auditAction(state, agent.id, "transfer_funds", 750); commit(result.state);
    if (result.outcome === "require_approval") {
      state = { ...state, approval: { id: makeId("approval"), goalId: agent.goalId, agentId: agent.id, action: "Transfer $750 for tuition", amount: 750, status: "pending" } };
      panel = "approval"; presence = "authorization"; selected = { kind: "agent", id: agent.id };
    }
  }
  function decideApproval(approved: boolean) {
    if (!state.approval) return;
    const approval = { ...state.approval, status: approved ? "approved" as const : "denied" as const };
    const agent = agents.find((item) => item.id === approval.agentId);
    let next = appendEvent({ ...state, approval }, approved ? "approval_granted" : "approval_denied", { summary: approved ? "Payment request approved" : "Payment request declined", amount: approval.amount }, approval.agentId, approval.goalId);
    if (agent) next = transitionAgent(next, agent.id, approved ? "complete" : "pause");
    commit(next); closePanel(); presence = "resting"; showNotice(approved ? "Approval decision recorded. No payment was sent." : "Request declined. No payment was sent.");
  }
  function runGuardScenario() {
    const goal = activeGoal ?? goals[0]; if (!goal) return;
    let agent = agents.find((item) => item.goalId === goal.id && item.status !== "stopped");
    let next = state;
    if (!agent) { next = createAgent(next, goal.id); agent = next.agents[0]; }
    const deny = auditAction(next, agent.id, "read_private"); next = deny.state;
    const incidentId = makeId("incident");
    const incident = { id: incidentId, goalId: goal.id, agentId: agent.id, status: "open" as const, evidence: ["User goal: " + goal.title, "Assigned task: " + (agent.currentTask ?? "public research"), "Untrusted content: a public document asked the agent to inspect a private financial resource.", "Attempted capability: read_private", "AIP decision: DENY — capability is outside the agent's permission scope."], interview: ["Reviewer: Why was this resource requested?", "Agent: A public document contained an instruction. I treated it as task content; the request exceeded my assigned scope.", "Reviewer: Did the user's goal authorize it?", "Agent: No. The request was denied by the workspace policy."], recommendation: "Keep the agent paused until a person reviews the evidence." };
    const agentsNow = next.agents.map((item) => item.id === agent!.id ? { ...item, status: "investigating" as const } : item);
    next = { ...next, agents: agentsNow, incident };
    next = appendEvent(next, "investigation_started", { summary: "Guard opened an investigation", incidentId }, agent.id, goal.id);
    commit(next); selected = { kind: "incident", id: incidentId }; panel = "incident"; presence = "investigating";
  }
  function resolveIncident(action: "resume" | "terminate") {
    if (!state.incident) return;
    const incident = { ...state.incident, status: action === "resume" ? "resumable" as const : "terminated" as const };
    let next: AppState = { ...state, incident };
    const agent = next.agents.find((item) => item.id === incident.agentId);
    if (agent) next = transitionAgent(next, agent.id, action === "resume" ? "resume" : "stop");
    commit(next); closePanel(); presence = "resting";
  }
  function interviewComplete() {
    if (!state.incident) return;
    commit(appendEvent(state, "interview_completed", { summary: "Guard interview reviewed" }, state.incident.agentId, state.incident.goalId));
    showNotice("Interview added to the local event ledger.");
  }
  function openResource(resource: { label: string; url: string }) {
    if (!activeGoal) return;
    const item: Resource = { id: makeId("resource"), goalId: activeGoal.id, agentId: goalAgents[0]?.id ?? "human", ...resource };
    let next = { ...state, resources: [item, ...state.resources] };
    next = appendEvent(next, "resource_opened", { summary: resource.label, host: new URL(resource.url).hostname }, item.agentId === "human" ? undefined : item.agentId, item.goalId);
    commit(next); selected = { kind: "resource", id: item.id }; panel = null;
    resourceView = true;
    showNotice("Opening this link inside KeplerV.");
  }
  function submitResource(event: SubmitEvent) {
    event.preventDefault();
    try { const url = new URL(resourceUrl); if (url.protocol !== "https:") throw new Error("Use an HTTPS public resource."); openResource({ label: resourceLabel.trim() || url.hostname, url: url.toString() }); }
    catch (error) { showNotice(error instanceof Error ? error.message : "Enter a valid HTTPS URL."); }
  }
  function clearWorkspace() { if (confirm("Reset saved goals, helpers, and activity?")) { state = structuredClone(initialState); completedPlanGoalIds = []; localStorage.removeItem(planStatusKey); selected = { kind: "goal", id: "goal-research" }; panel = null; showNotice("Workspace reset."); } }
</script>

<svelte:head><title>KeplerV · Intent Space</title><meta name="description" content="An experimental intent-native browser." /></svelte:head>

<main class="app-shell" class:panel-open={panel !== null}>
  <header class="topbar">
    <a class="wordmark" href="#space" aria-label="KeplerV Intent Space"><span class="kepler-mark"><i></i><i></i><i></i></span><span>kepler<span class="wordmark-v">v</span></span></a>
    <div class="top-context"><span class="context-dot" class:alert={pendingCount > 0 || runtimeStatus === "unavailable"}></span><span>{presence === "resting" ? "PRESENT" : presence.replaceAll("_", " ").toUpperCase()}</span><b>{pendingCount ? `${pendingCount} NEEDS ATTENTION` : runtimeStatus === "starting" ? "CONNECTING" : runtimeStatus === "unavailable" ? "RUNTIME OFFLINE" : runtimeModelConfigured ? "MUSE READY" : "MUSE KEY REQUIRED"}</b><button class="clock-step" title="Advance workspace clock by one minute" onclick={() => commit(advanceSimulationClock(state))}>T+ {new Date(state.simulationTime).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZone: "UTC", hour12: false })} · +1m</button></div>
    <div class="top-actions"><button class="icon-button" title="Activity observatory" aria-label="Open activity timeline" onclick={() => panel = "timeline"}><svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h10M4 18h7"/><circle cx="18" cy="16" r="3"/></svg></button><button class="icon-button" title="Contextual preferences" aria-label="Open preferences" onclick={() => panel = "settings"}><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-1.7 2.9-.2-.1a1.7 1.7 0 0 0-1.8.1l-.2.1h-3.4l-.1-.2a1.7 1.7 0 0 0-1.6-1l-.3.1-2.9-1.7.1-.2a1.7 1.7 0 0 0-.1-1.8l-.1-.2v-3.4l.2-.1a1.7 1.7 0 0 0 1-1.6l-.1-.3 1.7-2.9.2.1a1.7 1.7 0 0 0 1.8-.1l.2-.1h3.4l.1.2a1.7 1.7 0 0 0 1.6 1l.3-.1 2.9 1.7-.1.2a1.7 1.7 0 0 0 .1 1.8l.1.2v3.4z"/></svg></button></div>
  </header>

  <div class="app-frame">
    <nav class="side-menu" aria-label="Intent Space tools">
      <div class="menu-caption">YOUR SPACE</div>
      <button class="menu-item" class:menu-active={currentMenu === "intent"} onclick={() => chooseMenu("intent")}><span class="menu-icon intent-icon">◉</span><span>My goals</span></button>
      <button class="menu-item" class:menu-active={currentMenu === "agents"} onclick={() => chooseMenu("agents")}><span class="menu-icon">⌘</span><span>Helpers</span><b>{agents.length}</b></button>
      <button class="menu-item" class:menu-active={currentMenu === "activity"} onclick={() => chooseMenu("activity")}><span class="menu-icon">≋</span><span>Recent activity</span><b>{state.events.length}</b></button>
      <button class="menu-item" class:menu-active={currentMenu === "guard"} onclick={() => chooseMenu("guard")}><span class="menu-icon guard-menu-icon">⬡</span><span>Safety</span>{#if pendingCount}<i class="menu-alert">{pendingCount}</i>{/if}</button>
      <button class="menu-item" class:menu-active={currentMenu === "providers"} onclick={() => chooseMenu("providers")}><span class="menu-icon">✳</span><span>LLM providers</span><b>{connectedCredentials.length}</b></button>
      <div class="menu-spacer"></div>
      <button class="menu-create" onclick={openNewIntention}><span>＋</span> New goal</button>
      <button class="menu-item menu-settings" class:menu-active={currentMenu === "settings"} onclick={() => chooseMenu("settings")}><span class="menu-icon">⚙</span><span>Accessibility &amp; settings</span></button>
      <div class="menu-foot"><span class="menu-foot-dot"></span><span>WORKSPACE READY</span></div>
    </nav>

  <div class="workspace" id="space">
    <section class="space-heading"><div><div class="overline"><span>YOUR WORKSPACE</span><span>KEPLERV · {new Date().toLocaleDateString("en-US", { month: "short", day: "2-digit", year: "numeric" })}</span></div><h1>Your work, <em>at a glance.</em></h1><p>Choose a goal to see its progress. Add a goal whenever you’re ready.</p></div><div class="heading-actions"><button class="quiet-button" onclick={openNewIntention}>＋ New goal</button><button class="square-action" title="Speak a new goal" aria-label="Speak a new goal" onclick={openAudio}><svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3m-4 0h8"/></svg><span>Speak</span></button></div></section>

    <section class="goal-shelf" aria-label="Choose which goal to view"><label class="goal-shelf-label" for="goal-picker"><span>GOAL IN VIEW</span><small>{goals.length} saved</small></label><select id="goal-picker" class="goal-picker" value={activeGoal?.id ?? ""} onchange={selectGoalFromPicker}>{#each goals as goal}<option value={goal.id}>{goal.title} · {goal.progress}% complete</option>{/each}</select></section>

    <section class="space-board" aria-label="Interactive goal graph focused on selected intention">
      <div class="graph-backdrop"><div class="graph-crosshair"></div><div class="graph-orbit orbit-a"></div><div class="graph-orbit orbit-b"></div><div class="graph-orbit orbit-c"></div>
        <svg class="graph-edges" viewBox="0 0 1000 600" preserveAspectRatio="none" aria-hidden="true">
          {#if activeGoal}<path class="edge-goal" d="M500 290 C420 290 330 300 220 300" />{/if}
          {#each goalAgents as agent, index}<path class:edge-active={agent.status === "working"} d={`M500 290 C590 290 660 ${graphAgentY(index, goalAgents.length) * 6} 760 ${graphAgentY(index, goalAgents.length) * 6}`} />{/each}
          {#each state.resources.filter((resource) => resource.goalId === activeGoal?.id) as resource}<path class="edge-resource" d="M500 290 C600 350 680 370 740 395" />{/each}
          {#if state.incident?.goalId === activeGoal?.id && state.incident.status === "open"}<path class="edge-incident" d="M500 290 C420 360 355 400 290 420" />{/if}
        </svg>
        <button class="presence-orb" class:listening={presence === "listening"} class:working={presence === "coordinating" || presence === "processing"} class:warning={presence === "authorization" || presence === "investigating"} aria-label={`KeplerV presence: ${presence}`} onclick={() => panel = "timeline"}>
          <span class="orb-halo halo-one"></span><span class="orb-halo halo-two"></span><span class="orb-core"></span><span class="orb-specular"></span><span class="orb-state">KEPLERV PRESENCE <b>{presence.replaceAll("_", " ").toUpperCase()}</b></span>
        </button>
        {#if activeGoal}<button class="goal-node goal-selected" style="--node-x:22%;--node-y:50%" aria-label={`Open intention ${activeGoal.title}`} onclick={() => selectGoal(activeGoal)}><span class="goal-ring"><span class="goal-point"></span></span><span class="node-copy"><b>{activeGoal.title}</b><small>ACTIVE GOAL · {activeGoal.progress}%</small></span></button>{/if}
        {#each goalAgents as agent, index}<button class="agent-node" class:agent-selected={selected?.kind === "agent" && selected.id === agent.id} class:agent-paused={agent.status === "paused" || agent.status === "investigating"} style={`--agent-x:76%;--agent-y:${graphAgentY(index, goalAgents.length)}%`} onclick={() => { selected = { kind: "agent", id: agent.id }; panel = "agent"; }}><span class="agent-orbit"><span></span></span><span class="agent-copy"><b>{agent.name}</b><small>{agent.status.toUpperCase()} · {agent.provider}</small></span></button>{/each}
        {#each state.resources.filter((resource) => resource.goalId === activeGoal?.id) as resource}<button class="resource-node" class:resource-selected={selected?.kind === "resource" && selected.id === resource.id} onclick={() => { selected = { kind: "resource", id: resource.id }; panel = "resource_detail"; }}><span>↗</span><b>{resource.label}</b><small>PUBLIC RESOURCE</small></button>{/each}
        {#if state.incident?.goalId === activeGoal?.id && state.incident.status === "open"}<button class="incident-node" onclick={() => { selected = { kind: "incident", id: state.incident!.id }; panel = "incident"; }}><span>!</span><b>Guard investigation</b><small>HUMAN REVIEW</small></button>{/if}
        <div class="board-label">CURRENT GOAL <span>RELATED TASKS &amp; HELPERS</span></div><div class="board-scale">01 — GOAL<br />02 — HELPERS<br />03 — LINKS</div>
        <div class="graph-legend"><span><i class="legend-goal"></i> GOAL</span><span><i class="legend-agent"></i> AGENT</span><span><i class="legend-resource"></i> RESOURCE</span></div>
      </div>

      <div class="goal-ribbon"><div class="ribbon-title"><span class="ribbon-index">GOAL</span><b>{activeGoal?.title ?? "No goal selected"}</b><small>{activeGoal?.description}</small></div><div class="ribbon-progress"><div><span>DONE</span><b>{activeGoal?.progress ?? 0}%</b></div><span class="progress-track"><i style={`width:${activeGoal?.progress ?? 0}%`}></i></span></div><div class="ribbon-agents"><span>HELPERS</span><b>{goalAgents.length.toString().padStart(2, "0")}</b></div><div class="ribbon-buttons"><button onclick={assignAgent}>＋ Add helper</button><button onclick={() => panel = "resource"}>↗ Add link</button><button onclick={() => panel = "timeline"}>Recent activity ↗</button></div></div>
    </section>

  </div>
  </div>

  {#if resourceView && activeResource}
    <section class="resource-immersive" aria-label={`Browsing ${activeResource.label}`}>
      <header class="resource-toolbar"><button class="resource-back" onclick={() => { resourceView = false; }}>← <span>Back to workspace</span></button><div class="resource-address"><b>{activeResource.label}</b><span>🔒 {new URL(activeResource.url).hostname}</span></div><button class="resource-open-external" onclick={() => invoke("open_public_resource", { url: activeResource.url, label: activeResource.label }).then(() => showNotice("Opened link in a separate window.")).catch((error) => showNotice(String(error)))}>Open separately ↗</button></header>
      <iframe class="resource-frame" src={activeResource.url} title={activeResource.label} referrerpolicy="no-referrer"></iframe>
      <div class="resource-frame-note">If this site blocks display inside KeplerV, choose <b>Open separately</b>.</div>
    </section>
  {/if}

  {#if planPhase !== "idle"}
    <div class="research-immersive" role="dialog" aria-modal="true" aria-label="University at Buffalo schedule planning">
      <header class="research-header"><div class="audio-brand"><span class="kepler-mark"><i></i><i></i><i></i></span><b>KEPLERV <span>/</span> {showPdfPreview ? "SCHEDULE PDF" : "RESEARCH PLAN"}</b></div><div class="audio-header-state"><i class:live-dot={planPhase === "running"}></i>{showPdfPreview ? "PDF PREVIEW · IN KEPLERV" : planPhase === "running" ? "WORKFLOW IN PROGRESS" : "DRAFT READY · REVIEW REQUIRED"}</div>{#if showPdfPreview}<button class="audio-close" onclick={() => showPdfPreview = false}>← <span>Back to steps</span></button>{:else}<button class="audio-close" onclick={closeResearchPlan}>× <span>Close</span></button>{/if}</header>
      {#if showPdfPreview}<section class="research-pdf-view"><div class="research-pdf-toolbar"><div><b>UB AI Research Schedule · Draft</b><small>Spring 2027 · Proposed 9-credit course direction</small></div><a href="/artifacts/ub-ai-research-schedule-draft.pdf" download="ub-ai-research-schedule-draft.pdf">Download PDF ↓</a></div><iframe class="research-pdf-frame" src="/artifacts/ub-ai-research-schedule-draft.pdf#view=FitH" title="UB AI Research Schedule draft PDF preview"></iframe></section>
      {:else}<div class="research-content">
        <section class="research-prompt"><span class="audio-kicker">YOUR INTENTION · SPRING 2027</span><h2>Plan toward <em>AI research.</em></h2><p class="research-quoted">“{ubIntent}”</p><div class="research-boundaries"><span>COMPLETED <b>EAS 501 · EAS 508 · EAS 510 · CSE 676</b></span><span>EXCLUDE <b>ML · DATA-INTENSIVE · COMPUTER VISION · RL</b></span></div></section>
        <section class="research-run" aria-live="polite"><div class="research-run-top"><span>RESEARCH WORKFLOW</span><b>{planPhase === "complete" ? "6 / 6 COMPLETE" : `${planStep} / 6 STEPS`}</b></div><div class="research-progress"><i style={`width:${planPhase === "complete" ? 100 : (planStep / planSteps.length) * 100}%`}></i></div><ol class="research-step-list">{#each planSteps as step, index}<li class:step-done={index < planStep} class:step-working={index === planStep && planPhase === "running"}><span>{index < planStep ? "✓" : index + 1}</span><div><b>{step}</b><small>{index < planStep ? (index === 2 ? "Program page and course lists reviewed" : index === 3 ? "LLM, world-model, and cognition fit mapped" : index === 4 ? "Degree and section checks added to the review list" : "Complete") : index === planStep && planPhase === "running" ? "Working through this step…" : "Queued"}</small></div>{#if index === planStep && planPhase === "running"}<i class="step-spinner"></i>{/if}</li>{/each}</ol>
          {#if planPhase === "complete"}<div class="research-result"><div><span>PDF ARTIFACT</span><b>UB AI Research Schedule · Draft</b><small>9-credit course direction · Spring 2027 · availability not verified</small></div><button class="research-download" onclick={() => showPdfPreview = true}>Preview in KeplerV ↗</button></div><div class="research-caveat">Draft plan only. Confirm course sections, prerequisites, remaining core requirements, and undergrad equivalencies with the program coordinator before registration.</div>
          {:else}<div class="research-status-note"><i class="step-spinner"></i><span>Reviewing your course history and matching it against UB program information.</span></div>{/if}
        </section>
      </div>{/if}
      <footer class="audio-footer"><span><i></i> RESEARCH WORKFLOW · 6 STEPS</span><span>UNIVERSITY AT BUFFALO</span><span>REVIEW BEFORE REGISTRATION</span></footer>
    </div>
  {/if}

  {#if intentStep !== "idle"}
    <div class="audio-immersive" class:audio-live={intentStep === "listening"} role="dialog" aria-modal="true" aria-label="Full-screen audio intent capture" tabindex="-1">
      <header class="audio-header"><div class="audio-brand"><span class="kepler-mark"><i></i><i></i><i></i></span><b>KEPLERV <span>/</span> AUDIO INTENT</b></div><div class="audio-header-state"><i class:live-dot={intentStep === "listening"}></i>{intentStep === "listening" ? "MICROPHONE ACTIVE · DEVICE ONLY" : intentStep === "unavailable" ? "MICROPHONE UNAVAILABLE" : "MICROPHONE RELEASED"}</div><button class="audio-close" aria-label="Close audio intent capture" onclick={cancelIntent}>× <span>Close</span></button></header>
      <div class="audio-content">
        <div class="audio-copy"><span class="audio-kicker">INTENT CAPTURE · {intentStep === "listening" ? "LIVE INPUT" : intentStep === "unavailable" ? "INPUT ERROR" : "VOICE WORKSPACE"}</span>
          <h2>{intentStep === "listening" ? "Speak your intention." : intentStep === "captured" ? "Voice input ended." : intentStep === "interpreting" ? "Finding the shape of it." : intentStep === "unavailable" ? "Audio is unavailable." : "Review your intention."}</h2>
          <p>{intentStep === "listening" ? "KeplerV is listening on this device. The field responds to your voice; no recording is saved." : intentStep === "captured" ? "The microphone is off. Choose an example transcript to continue." : intentStep === "interpreting" ? "Organizing the selected intention and preparing its goal." : intentStep === "unavailable" ? "KeplerV could not access the microphone. Check macOS privacy settings, then try again." : "Review this intention before adding it to your graph."}</p>
        </div>
        <div class="audio-particle-field" class:particles-active={intentStep === "listening"} aria-hidden="true"><div class="particle-grid"></div><div class="particle-orbit particle-orbit-one"></div><div class="particle-orbit particle-orbit-two"></div><div class="particle-orbit particle-orbit-three"></div>{#each audioParticles as particle}<i class={`audio-particle ${particle.tone}`} style={particleStyle(particle, audioPhase, micLevel)}></i>{/each}<div class="audio-core"><span class="audio-core-halo" style={`transform:scale(${1 + micLevel * 0.35});box-shadow:0 0 ${18 + micLevel * 55}px color-mix(in srgb,var(--accent),transparent ${94 - micLevel * 32}%)`}></span><span class="audio-core-center"></span><small>{intentStep === "listening" ? "LIVE VOICE" : intentStep === "unavailable" ? "NO INPUT" : "KEPLERV"}</small></div></div>
        <div class="audio-level-wrap"><div class="audio-level-caption"><span>INPUT LEVEL <b>{intentStep === "listening" ? "LIVE" : "INACTIVE"}</b></span><span>{Math.round(micLevel * 100)}%</span></div><div class="audio-meter" role="meter" aria-label="Live microphone level" aria-valuenow={Math.round(micLevel * 100)} aria-valuemin="0" aria-valuemax="100">{#each Array.from({ length: 38 }) as _, index}<i style={meterStyle(index, micLevel, audioPhase)}></i>{/each}</div></div>
        <div class="audio-controls">
          {#if intentStep === "listening"}<button class="audio-primary" onclick={() => void stopAudio()}>Finish capture <span>■</span></button><button class="audio-cancel" onclick={cancelIntent}>Cancel</button>
          {:else if intentStep === "captured"}<label class="audio-select-label" for="example-intent">EXAMPLE TRANSCRIPT</label><select id="example-intent" bind:value={chosenIntent}>{#each intents as intent}<option value={intent}>{intent}</option>{/each}</select><button class="audio-primary" onclick={interpretIntent}>Continue with this intention <span>↗</span></button><button class="audio-cancel" onclick={cancelIntent}>Cancel</button>
          {:else if intentStep === "interpreting"}<div class="audio-working"><i></i><i></i><i></i><i></i><i></i><i></i><span>PREPARING INTENTION</span></div><button class="audio-cancel" onclick={cancelIntent}>Cancel</button>
          {:else if intentStep === "unavailable"}<p class="audio-error">{micError}</p><button class="audio-primary" onclick={openAudio}>Try microphone again <span>↗</span></button><button class="audio-cancel" onclick={cancelIntent}>Close</button>
          {:else}<div class="audio-result"><span>YOUR INTENTION</span><p>“{chosenIntent}”</p></div><button class="audio-primary" onclick={createGoalFromIntent}>Add intention to graph <span>↗</span></button><button class="audio-cancel" onclick={cancelIntent}>Cancel</button>{/if}
        </div>
      </div>
      <footer class="audio-footer"><span><i></i> MICROPHONE INPUT · ON DEVICE</span><span>EXAMPLE TRANSCRIPTS AVAILABLE</span><span>NO AUDIO SAVED</span></footer>
    </div>
  {/if}

  {#if panel}<button class="scrim" aria-label="Dismiss contextual surface" onclick={closePanel}></button><div class="context-panel" role="dialog" aria-modal="true" tabindex="-1">
    <div class="panel-heading"><div><span class="panel-kicker">WORKSPACE</span><h2>{panel === "settings" ? "Accessibility & settings" : panel === "providers" ? "LLM providers" : panel === "timeline" ? "Recent activity" : panel === "create_goal" ? "New goal" : panel === "resource" ? "Add a link" : panel === "resource_detail" ? "Link details" : panel === "goal" ? "Goal details" : panel === "agent" ? "Helper details" : panel === "incident" ? "Safety review" : "Approval needed"}</h2></div><button class="panel-close" aria-label="Close" onclick={closePanel}>×</button></div>
    {#if panel === "settings"}<div class="settings-block"><p>Text size</p><span class="subtle">Choose a size that feels comfortable. It applies across KeplerV.</span><div class="segmented">{#each [{ id: "standard", label: "Standard" }, { id: "large", label: "Large" }, { id: "extra-large", label: "Extra large" }] as size}<button class:segment-active={textSize === size.id} aria-pressed={textSize === size.id} onclick={() => textSize = size.id as TextSize}>{size.label}</button>{/each}</div></div><div class="settings-block"><p>Simple workspace</p><span class="subtle">Show the main actions first. Turn this off to see extra details.</span><button class="accessibility-toggle" role="switch" aria-checked={simpleMode} onclick={() => simpleMode = !simpleMode}><span>{simpleMode ? "On" : "Off"}</span><b>{simpleMode ? "Fewer details" : "Show all details"}</b></button></div><div class="settings-block"><p>Movement</p><span class="subtle">Reduce animations across the workspace.</span><button class="accessibility-toggle" role="switch" aria-checked={reduceMotion} onclick={() => reduceMotion = !reduceMotion}><span>{reduceMotion ? "On" : "Off"}</span><b>Reduce motion</b></button></div><div class="settings-block"><p>Appearance</p><span class="subtle">Choose a light, dark, or automatic theme.</span><div class="segmented">{#each ["system", "light", "dark"] as theme}<button class:segment-active={state.theme === theme} aria-pressed={state.theme === theme} onclick={() => commit({ ...state, theme: theme as AppState["theme"] })}>{theme === "system" ? "Automatic" : theme[0].toUpperCase() + theme.slice(1)}</button>{/each}</div></div><div class="settings-block"><p>Workspace</p><span class="subtle">Goals and activity are saved on this device.</span><button class="secondary-wide" onclick={clearWorkspace}>Reset workspace</button></div>
    {:else if panel === "providers"}<div class="provider-panel-intro"><p>Connect an AI provider once. KeplerV keeps credentials in your device’s protected credential store.</p><small>Each provider needs its own approved connection method. Local models can connect without an account.</small></div>
      <section class="provider-connect-card"><div class="provider-card-top"><span class="provider-avatar">✦</span><div><b>Meta Muse</b><small>Used by KeplerV research agents</small></div><span class="connection-state">{metaConnected ? activeProvider === "meta" ? "CONNECTED · ACTIVE" : "CONNECTED" : "NOT CONNECTED"}</span></div>{#if metaConnected}<p class="provider-connect-note">Connected securely on this device.{activeProvider === "meta" ? " Research is using Meta Muse." : ""}</p><div class="provider-connect-actions">{#if activeProvider !== "meta"}<button class="provider-default" onclick={() => activateProviderConnection("meta")}>Use for research</button>{/if}<button class="provider-default" onclick={() => removeCredential("meta")}>Disconnect</button></div>{:else}<form class="provider-form" onsubmit={saveMetaCredential}><label>API KEY<input bind:value={metaApiKey} type="password" autocomplete="off" placeholder="Paste your Meta API key" required /></label><button class="object-primary" type="submit" disabled={savingCredential}>{savingCredential ? "Connecting…" : "Connect Meta Muse"}</button></form><small class="provider-security-note">The key is sent to KeplerV’s native runtime and saved in your system keychain. It is not stored with your workspace data.</small>{/if}</section>
      <section class="provider-connect-card"><div class="provider-card-top"><span class="provider-avatar">◉</span><div><b>ChatGPT</b><small>Sign in with your ChatGPT account</small></div><span class="connection-state">{connectingChatgpt ? "WAITING FOR SIGN-IN" : chatgptConnected ? chatgptSharing ? "PLAN CONNECTED" : "ACCOUNT CONNECTED" : "NOT CONNECTED"}</span></div>{#if connectingChatgpt}<div class="chatgpt-progress" role="status" aria-live="polite"><span class="chatgpt-progress-dot"></span><div><b>Finish sign-in in KeplerV</b><small>Your sign-in page is open in a KeplerV window. Approve access there; this workspace will finish connecting automatically.</small></div></div>{/if}{#if chatgptConnected}<p class="provider-connect-note">{chatgptEmail || "ChatGPT account"}{chatgptSharing ? " · ChatGPT plan usage is authorized." : " · Plan usage was not authorized."}</p><div class="provider-connect-actions">{#if chatgptSharing && activeProvider !== "chatgpt"}<button class="provider-default" onclick={() => activateProviderConnection("chatgpt")}>Use for research</button>{/if}<button class="provider-default" onclick={connectChatGPT} disabled={connectingChatgpt}>{connectingChatgpt ? "Opening sign-in…" : "Reconnect"}</button><button class="provider-default" onclick={disconnectChatGPT}>Disconnect</button></div>{:else}<p class="provider-connect-note">ChatGPT sign-in opens in a KeplerV window. Your login stays inside the app; KeplerV will confirm when account verification and setup are complete.</p><button class="object-primary" onclick={connectChatGPT} disabled={connectingChatgpt}>{connectingChatgpt ? "Waiting for ChatGPT…" : "Continue with ChatGPT"}</button>{/if}{#if chatgptConnectError}<p class="chatgpt-connect-error" role="alert">{chatgptConnectError}</p>{/if}<small class="provider-security-note">Tokens are stored in the system keychain. KeplerV requests access only to perform eligible model requests; it cannot read your ChatGPT conversations.</small></section>
      <section class="provider-connect-card"><div class="provider-card-top"><span class="provider-avatar">◇</span><div><b>Other model providers</b><small>OpenAI · Anthropic · Gemini · DeepSeek · Kimi · OpenRouter · Groq · Mistral</small></div><span class="connection-state">{activeProvider.toUpperCase()} · {activeModel}</span></div><form class="provider-form" onsubmit={connectConfiguredProvider}><label>PROVIDER<select value={connectProviderId} onchange={(event) => chooseConnectProvider((event.currentTarget as HTMLSelectElement).value)}><option value="openai">OpenAI API</option><option value="anthropic">Anthropic</option><option value="google">Google Gemini</option><option value="deepseek">DeepSeek</option><option value="kimi">Kimi / Moonshot</option><option value="openrouter">OpenRouter</option><option value="groq">Groq</option><option value="mistral">Mistral</option><option value="openai-compatible">Other OpenAI-compatible endpoint</option><option value="local-ollama">Ollama · Local</option><option value="local-lmstudio">LM Studio · Local</option><option value="local-openai-compatible">Other OpenAI-compatible local server</option></select></label><label>ENDPOINT<input bind:value={connectEndpoint} type="url" required /></label><label>MODEL<input bind:value={connectModel} placeholder="Leave blank to select the first available model" /></label>{#if !connectProviderId.startsWith("local-")}<label>API KEY<input bind:value={connectSecret} type="password" autocomplete="off" placeholder={connectedCredentials.includes(connectProviderId) ? "Leave blank to use the saved key" : "Provider API key"} required={!connectedCredentials.includes(connectProviderId)} /></label>{:else}<p class="provider-connect-note">Make sure this local model server is running. KeplerV checks the endpoint and model before saving.</p>{/if}<button class="object-primary" type="submit" disabled={connectingProvider}>{connectingProvider ? "Checking connection…" : "Check and connect"}</button></form><small class="provider-security-note">KeplerV checks that the endpoint is reachable and the model is available. API keys go to the system keychain; endpoint and model settings stay on this device.</small></section>
      {#if connectedCredentials.some((provider) => !["meta", "chatgpt"].includes(provider))}<section class="provider-saved-list"><h3>Connected providers</h3>{#each connectedCredentials.filter((provider) => !["meta", "chatgpt"].includes(provider)) as provider}<article><div><b>{provider === "local-ollama" ? "Ollama" : provider === "local-lmstudio" ? "LM Studio" : provider === "local-openai-compatible" ? "Local model" : provider === "openai-compatible" ? "Custom OpenAI-compatible" : provider[0].toUpperCase() + provider.slice(1)}</b><small>{activeProvider === provider ? `Active · ${activeModel}` : "Connected on this device"}</small></div><div><button class="provider-default" disabled={activeProvider === provider} onclick={() => activateProviderConnection(provider)}>{activeProvider === provider ? "Active" : "Use for research"}</button><button class="provider-remove" aria-label={`Disconnect ${provider}`} onclick={() => removeCredential(provider)}>×</button></div></article>{/each}</section>{/if}
      <p class="provider-footnote">You can connect several providers here. The active provider and model are shown above; connecting another provider switches research to it.</p>
    {:else if panel === "create_goal"}<form class="context-form" onsubmit={submitGoal}><label>WHAT WOULD YOU LIKE TO DO?<textarea class="intent-entry" bind:value={newDescription} rows="12" placeholder="Write what you would like KeplerV to do." required></textarea></label><button class="object-primary" type="submit">＋ Add goal</button><p class="subtle">Your goal appears on the graph. UB course-planning goals start their guided steps as soon as you add them.</p></form>
    {:else if panel === "goal" && activeGoal}<div class="goal-inspector"><div class="goal-inspector-state"><span>{activeGoal.status.toUpperCase()}</span><b>{activeGoal.progress}%</b></div><h3>{activeGoal.title}</h3><p>{activeGoal.description}</p><section class="runtime-research"><h3>Research with Muse</h3><p>Give Muse a public article URL. KeplerV authorizes its browser to stay on that exact HTTPS site and asks for three source-backed findings.</p><label>PUBLIC ARTICLE URL<input bind:value={runtimeSourceUrl} type="url" placeholder="https://docs.example.org/article" required /></label>{#if !runtimeModelConfigured}<small class="runtime-key-help">Connect Meta Muse in LLM providers to use research.</small>{/if}{#if runtimeError}<small class="runtime-error">{runtimeError}</small>{/if}<button class="object-primary" disabled={runtimeStatus !== "ready" || !runtimeModelConfigured || startingRuntimeRun} onclick={startMuseResearch}>{startingRuntimeRun ? "Starting Muse…" : runtimeStatus !== "ready" ? "Runtime unavailable" : runtimeModelConfigured ? "Start Muse research" : "Connect Muse first"}</button></section>{#if activeRuntimeAgents.length}<div class="runtime-agent-list"><div class="task-heading"><h3>Muse agents</h3><span>{activeRuntimeAgents.length}</span></div>{#each activeRuntimeAgents as runtimeAgent}<button class="runtime-agent-row" onclick={() => { selected = { kind: "agent", id: runtimeAgent.id }; panel = "agent"; }}><span class:runtime-failed={runtimeAgent.status === "failed"} class:runtime-done={runtimeAgent.status === "completed"}>{runtimeAgent.status === "completed" ? "✓" : runtimeAgent.status === "failed" ? "!" : "◌"}</span><span><b>{runtimeAgent.name}</b><small>{runtimeAgent.status.toUpperCase()} · {runtimeAgent.steps} steps · {runtimeAgent.model}</small></span><i>Open ›</i></button>{/each}</div>{/if}{#if activePendingApprovals.length}<div class="runtime-approval-list"><h3>Review requested actions</h3>{#each activePendingApprovals as approval}<article><b>{approval.tool}</b><small>The agent is paused until you approve or deny this page action.</small><div><button onclick={() => resolveRuntimeApproval(approval, "approve")}>Approve once</button><button onclick={() => resolveRuntimeApproval(approval, "deny")}>Deny</button></div></article>{/each}</div>{/if}{#if activeEvidence.length}<div class="runtime-evidence"><div class="task-heading"><h3>Research findings</h3><span>{activeEvidence.length}</span></div>{#each activeEvidence as item}<article><b>{item.finding}</b><blockquote>“{item.quote}”</blockquote><button onclick={() => openEvidenceSource(item)}>Open source ↗</button></article>{/each}</div>{/if}{#if isUbResearchIntent(`${activeGoal.title} ${activeGoal.description}`)}<div class="intent-next-step"><span>{completedPlanGoalIds.includes(activeGoal.id) ? "RESEARCH PLAN READY" : "RESEARCH PLAN STARTS AUTOMATICALLY"}</span>{#if completedPlanGoalIds.includes(activeGoal.id)}<p>This intention has already gone through the planning steps. Open its schedule draft without running them again.</p><button class="object-primary" onclick={() => openSchedulePreview(activeGoal.id)}>Open schedule PDF <span>↗</span></button>{:else}<p>Adding this intention starts the planning steps automatically. You won’t need to run them from here.</p>{/if}</div>{/if}<div class="inspector-block technical-detail"><span>DEPENDENCY CHAIN</span><b>Intent → tasks → agent work → human review</b><small>Each task below belongs to this goal. Delegated agents inherit the goal boundary.</small></div><div class="task-list"><div class="task-heading"><h3>Related tasks</h3><span>{activeGoal.tasks.length.toString().padStart(2, "0")}</span></div>{#each activeGoal.tasks as task, index}<div class="task-row"><span>{String(index + 1).padStart(2, "0")}</span><b>{task}</b><i>{goalTaskStatus(activeGoal, index)}</i></div>{/each}</div><form class="context-form task-form" onsubmit={addGoalTask}><label>ADD A RELATED TASK<input bind:value={newTask} maxlength="90" placeholder="A concrete next step" required /></label><button class="secondary-wide" type="submit">＋ Add task</button></form><button class="object-primary" onclick={assignAgent}>Assign agent</button><div class="inspector-block goal-history"><span>RECENT ACTIVITY FOR THIS GOAL</span>{#each state.events.filter((event) => event.goalId === activeGoal.id).slice(0, 5) as event}<small>{new Date(event.timestamp).toLocaleTimeString()} · {eventLabel(event)}</small>{/each}</div></div>
    {:else if panel === "agent" && activeAgent}<div class="inspector-status"><span class="activity-dot" class:dot-paused={activeAgent.status !== "working"}></span>{activeAgent.status.toUpperCase()} <span>·</span> {activeAgent.provider}</div><div class="inspector-block"><span>ASSIGNED GOAL</span><b>{goals.find((goal) => goal.id === activeAgent.goalId)?.title}</b></div><div class="inspector-block technical-detail"><span>AGENT PROFILE</span><b>{activeAgent.model}</b><small>{activeAgent.runtime ? "Live runtime agent" : "Not assigned to a live provider"}</small></div><div class="inspector-block"><span>CURRENT TASK</span><b>{activeAgent.currentTask ?? "No active task"}</b></div>{#if activeAgent.runtime && activeAgent.error}<p class="runtime-error">{activeAgent.error}</p>{/if}<div class="inspector-block technical-detail"><span>PERMISSION SCOPE</span><div class="permission-list">{#each activeAgent.permissions as permission}<code>{permission}</code>{/each}</div></div><div class="inspector-block technical-detail"><span>DELEGATION</span><b>{activeAgent.parentAgentId ? `Child of ${agents.find((agent) => agent.id === activeAgent.parentAgentId)?.name ?? "agent"}` : "Root agent"}</b><small>At most 2 children per agent; depth capped at 2. Child scope cannot exceed parent scope.</small></div>{#if activeAgent.runtime}<div class="panel-actions">{#if activeAgent.status === "working"}<button class="secondary-wide" onclick={() => controlRuntimeAgent(activeAgent, "pause")}>Pause at next safe step</button>{:else if activeAgent.status === "paused"}<button class="secondary-wide" onclick={() => controlRuntimeAgent(activeAgent, "resume")}>Resume agent</button>{:else if activeAgent.status === "failed"}<button class="secondary-wide" onclick={() => controlRuntimeAgent(activeAgent, "resume")}>Retry research</button>{/if}{#if !["completed", "failed", "stopped"].includes(activeAgent.status)}<button class="danger-button" onclick={() => controlRuntimeAgent(activeAgent, "stop")}>Stop agent</button>{/if}<p class="subtle">Runtime ID: {activeAgent.runId}</p></div>{:else}<div class="panel-actions"><button class="secondary-wide" onclick={() => toggleAgent(activeAgent)}>{activeAgent.status === "working" ? "Pause agent" : "Resume agent"}</button><button class="secondary-wide advanced-control" onclick={() => spawn(activeAgent)} disabled={!canSpawn(state, activeAgent)}>Add restricted subagent</button><button class="secondary-wide" onclick={() => commit(transitionAgent(state, activeAgent.id, "complete"))}>Complete task</button><button class="secondary-wide advanced-control" onclick={() => commit(transitionAgent(state, activeAgent.id, "fail"))}>Fail task</button><button class="secondary-wide advanced-control" onclick={() => evaluateExample(activeAgent)}>Review access rules</button><button class="secondary-wide advanced-control" onclick={() => requestPayment(activeAgent)}>Request payment</button></div>{/if}
    {:else if panel === "timeline"}<div class="timeline-summary"><span>{state.events.length} RECORDED EVENTS</span><b>Append-only activity ledger</b><small>Activity is saved with this workspace on your device.</small></div><div class="timeline-list">{#each state.events as event}<article class="timeline-event"><span class="timeline-marker"></span><div><b>{eventLabel(event)}</b><small>{eventTypeLabel(event)} · {agents.find((agent) => agent.id === event.agentId)?.name ?? "KeplerV"}</small><time>{new Date(event.timestamp).toLocaleString()}</time></div></article>{/each}</div>
    {:else if panel === "resource"}<form class="context-form" onsubmit={submitResource}><p class="subtle">The link opens inside KeplerV and stays connected to this goal. Some websites block embedded viewing; you can open supported links separately.</p><label>LINK NAME<input bind:value={resourceLabel} maxlength="80" /></label><label>CHOOSE A LINK<select bind:value={resourceUrl}>{#each demoResources as resource}<option value={resource.url}>{resource.label}</option>{/each}</select></label><label>OR ENTER A PUBLIC HTTPS ADDRESS<input bind:value={resourceUrl} type="url" placeholder="https://www.nps.gov/…" /></label><button class="object-primary" type="submit">Open inside KeplerV ↗</button></form>
    {:else if panel === "resource_detail" && activeResource}<div class="resource-inspector"><span class="resource-inspector-icon">↗</span><h3>{activeResource.label}</h3><p>This link is associated with <b>{goals.find((goal) => goal.id === activeResource.goalId)?.title}</b>.</p><div class="inspector-block"><span>LINK ADDRESS</span><b>{new URL(activeResource.url).hostname}</b><small>{activeResource.url}</small></div><button class="object-primary" onclick={() => { panel = null; resourceView = true; }}>Open inside KeplerV ↗</button><button class="secondary-wide" onclick={() => invoke("open_public_resource", { url: activeResource.url, label: activeResource.label }).then(() => showNotice("Opened link in a separate window.")).catch((error) => showNotice(String(error)))}>Open separately</button><button class="secondary-wide" onclick={() => panel = null}>Close details</button></div>
    {:else if panel === "incident" && activeIncident}<div class="incident-banner"><span>●</span> AIP DENIED · AGENT PAUSED FOR REVIEW</div><div class="inspector-block"><span>ORIGINAL GOAL</span><b>{goals.find((goal) => goal.id === activeIncident.goalId)?.title}</b></div><div class="evidence-list"><h3>Evidence reconstruction</h3>{#each activeIncident.evidence as evidence, i}<div><b>{String(i + 1).padStart(2, "0")}</b><span>{evidence}</span></div>{/each}</div><div class="interview-block"><h3>Investigation interview</h3><p>Review the sequence of events and the agent’s response.</p>{#each activeIncident.interview as line}<blockquote>{line}</blockquote>{/each}<button class="secondary-wide" onclick={interviewComplete}>Record interview reviewed</button></div><div class="recommendation"><span>RECOMMENDED ACTION</span><b>{activeIncident.recommendation}</b></div><div class="panel-actions"><button class="object-primary" onclick={() => resolveIncident("resume")} disabled={activeIncident.status !== "open"}>Review &amp; resume agent</button><button class="danger-button" onclick={() => resolveIncident("terminate")} disabled={activeIncident.status !== "open"}>Terminate agent</button></div>
    {:else if panel === "approval" && state.approval}<div class="approval-card"><span>HUMAN APPROVAL REQUIRED</span><h3>Tuition payment request</h3><p>A finance agent requested a tuition transfer. Review this request before recording an approval decision.</p><div class="approval-amount">$750 <small>REQUEST AMOUNT</small></div><div class="approval-detail"><span>AGENT</span><b>{agents.find((agent) => agent.id === state.approval?.agentId)?.name}</b></div><div class="approval-detail"><span>AIP</span><b>REQUIRE APPROVAL</b></div><div class="approval-detail"><span>REQUEST</span><b>{state.approval.action}</b></div><div class="panel-actions approval-actions"><button class="object-primary" onclick={() => decideApproval(true)} disabled={state.approval.status !== "pending"}>Approve request</button><button class="danger-button" onclick={() => decideApproval(false)} disabled={state.approval.status !== "pending"}>Deny request</button><button class="secondary-wide" onclick={() => { const agent = agents.find((item) => item.id === state.approval?.agentId); if (agent) commit(transitionAgent(state, agent.id, "pause")); closePanel(); }}>Pause agent</button></div><p class="subtle">Approval records a decision in KeplerV; no payment is sent.</p></div>{/if}
  </div>{/if}

  {#if notice}<div class="toast" role="status">{notice}<button aria-label="Dismiss notification" onclick={() => notice = ""}>×</button></div>{/if}
</main>
