<script lang="ts">
  import { onMount } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import { listen } from "@tauri-apps/api/event";
  import type { Agent, AppState, ConnectedAccount, EmailDraft, Evidence, Goal, KeplerEvent, MailMessage, MediaFocusState, MediaSurfaceId, Resource, ServiceProviderId } from "./types";
  import { accountKey, parseAccountKey, resolveAccount } from "./experience/accountResolver";
  import { applyMediaCommand, createSurfaceState, duckForSpeech, LOCAL_DEMO_VIDEO_URL, NETFLIX_FALLBACK_LABEL, NETFLIX_URL, probeYouTubeEmbed, resolveMediaTarget, surfaceLabel, youtubeEmbedUrl } from "./experience/surfaceManager";
  import { initialState, makeId, STORAGE_KEY } from "./simulation/seed";
  import { addTask, advanceSimulationClock, appendEvent, auditAction, canSpawn, createAgent, createGoal, modifyGoal, setOfflineMode, spawnSubagent, transitionAgent } from "./simulation/runtime";
  import { VoiceRecorder } from "./voice/recorder";
  import { VoiceSpeech } from "./voice/speech";
  import { arrayBufferToBase64 } from "./voice/wav";

  type Presence = "resting" | "listening" | "processing" | "speaking" | "coordinating" | "authorization" | "investigating" | "unavailable";
  type IntentStep = "idle" | "listening" | "captured" | "interpreting" | "intent_ready" | "unavailable";
  type PlanPhase = "idle" | "running" | "complete";
  type TextSize = "standard" | "large" | "extra-large";
  type Panel = "settings" | "providers" | "timeline" | "create_goal" | "resource" | "resource_detail" | "goal" | "agent" | "approval" | "guard" | null;
  type MenuItem = "intent" | "agents" | "activity" | "guard" | "providers" | "settings";
  type RuntimeEvent = { id: string; sequence: number; run_id: string; agent_id: string; goal_id: string; type: string; status: string; tool?: string; payload: Record<string, unknown>; timestamp: string };
  type RuntimeDetectiveReport = { summary: string; assessment: string; findings: Array<{ claim: string; classification: string; evidence_ids: string[]; explanation: string }>; uncertainties: string[]; recommended_next_step: string };
  type RuntimeGuardCase = { id: string; trigger_event_id: string; run_id: string; goal_id: string; goal_title: string; agent_id: string; provider: string; model: string; status: string; severity: string; signal: string; summary: string; created_at: string; updated_at: string; evidence: Array<{ id: string; source_type: string; source_id: string; timestamp: string; payload: Record<string, unknown>; previous_hash: string; integrity_hash: string }>; transitions: Array<{ from_status: string; to_status: string; reason: string; actor: string; timestamp: string }> };
  type RuntimeAgent = { id: string; agent_id: string; parent_agent_id?: string | null; goal_id: string; goal_title: string; task: string; provider: string; model: string; status: string; permissions: string[]; step_budget: number; token_budget: number; steps: number; tokens_used: number; browser_isolated?: boolean; error?: string | null; created_at: string; updated_at: string; result?: { summary?: string; findings?: Array<{ id: string; finding: string; quote: string; source_url: string; created_at: string }>; source_url?: string } | null };
  type RuntimeSnapshot = { agents: RuntimeAgent[]; events: RuntimeEvent[]; evidence: Array<{ id: string; goal_id: string; agent_id: string; run_id: string; finding: string; source_url: string; quote: string; created_at: string }>; guard_cases: RuntimeGuardCase[]; last_sequence: number };
  type Selection = { kind: "goal" | "agent" | "resource"; id: string } | null;

  let state: AppState = structuredClone(initialState);
  let realActivityEvents: KeplerEvent[] = [];
  let hydrated = false;
  let selected: Selection = { kind: "goal", id: "goal-research" };
  let panel: Panel = null;
  let runtimeStatus: "starting" | "ready" | "unavailable" = "starting";
  let runtimeError = "";
  let runtimeModelConfigured = false;
  let startingRuntimeRun = false;
  let runtimeTokenBudget = 50000;
  let runtimeSourceUrl = "";
  let runtimeBaseUrl = "";
  let pendingRuntimeApprovals: Array<{ runId: string; callId: string; goalId: string; tool: string; arguments: Record<string, unknown> }> = [];
  let runtimeGuardCases: RuntimeGuardCase[] = [];
  let selectedGuardCaseId = "";
  let investigatingCaseId = "";
  let interviewingCaseId = "";
  let adjudicatingCaseId = "";
  let guardInvestigationError = "";
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
  let connectedAccounts: ConnectedAccount[] = [];
  let connectingService: ServiceProviderId | null = null;
  let serviceError = "";
  let mailAccountKey = "";
  let mailMessages: MailMessage[] = [];
  let mailQuery = "";
  let mailSelected: MailMessage | null = null;
  let mailLoading = false;
  let mailError = "";
  let emailDraft: EmailDraft | null = null;
  let pendingEmailSend: EmailDraft | null = null;
  let resourceView = false;
  let mediaState: MediaFocusState = createSurfaceState();
  let mediaView = false;
  let mediaSurface: MediaSurfaceId | null = null;
  let youtubeVideoId = "";
  let youtubeSrc = "";
  let localVideoEl: HTMLVideoElement | null = null;
  let youtubeFrameEl: HTMLIFrameElement | null = null;
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
  let voiceRecorder: VoiceRecorder | null = null;
  let voiceTranscript = "";
  let voiceResult = "";
  let voiceConversation: Array<{ role: string; text: string }> = [];
  let speech: VoiceSpeech | null = null;
  let speechEnabled = true;
  const announcedCompletions = new Set<string>();
  const announcedCases = new Set<string>();
  const announcedApprovals = new Set<string>();
  let snapshotPrimed = false;
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
  const tokenBudgetKey = `${STORAGE_KEY}:runtime-token-budget`;
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
        delete (savedState as AppState & { incident?: unknown }).incident;
        state = {
          ...structuredClone(initialState),
          ...savedState,
          agents: (savedState.agents ?? initialState.agents).map((agent) => ({
            ...agent,
            provider: ["Local simulation", "OpenAI · simulated"].includes(agent.provider) ? "On device" : agent.provider,
            model: ["Deterministic", "Provider label only"].includes(agent.model) ? (agent.name === "Planner" ? "Course Planner" : "Researcher") : agent.model,
          })),
          goals: (savedState.goals ?? initialState.goals).map((goal) => ({
            ...goal,
            tasks: goal.tasks ?? [],
            criteria: goal.criteria ?? ["Outcome reviewed against the request"],
            constraints: goal.constraints ?? [],
          })),
          events: (savedState.events ?? []).filter((event) => typeof event.payload?.runId === "string").map((event) => ({
            ...event,
            ...(typeof event.payload.summary === "string" ? { payload: { ...event.payload, summary: cleanWorkspaceCopy(event.payload.summary) } } : {}),
          })),
          ...(savedState.approval ? { approval: { ...savedState.approval, action: cleanWorkspaceCopy(savedState.approval.action) } } : {}),
        };
      }
      const savedPlans = localStorage.getItem(planStatusKey);
      if (savedPlans) completedPlanGoalIds = JSON.parse(savedPlans);
      const savedTokenBudget = Number(localStorage.getItem(tokenBudgetKey));
      if (Number.isInteger(savedTokenBudget) && savedTokenBudget >= 1000 && savedTokenBudget <= 50000) runtimeTokenBudget = savedTokenBudget;
      const savedTextSize = localStorage.getItem(textSizeKey);
      if (savedTextSize === "standard" || savedTextSize === "large" || savedTextSize === "extra-large") textSize = savedTextSize;
      reduceMotion = localStorage.getItem(reduceMotionKey) === "true";
      const savedSimpleMode = localStorage.getItem(simpleModeKey);
      if (savedSimpleMode !== null) simpleMode = savedSimpleMode !== "false";
    } catch { showNotice("Saved workspace data could not be read. Starting with a fresh workspace."); }
    hydrated = true;
    speechEnabled = voice().isEnabled;
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
    void refreshServiceAccounts();
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
      void invoke<{ base_url: string }>("runtime_info").then((info) => { runtimeBaseUrl = info.base_url; }).catch(() => {});
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
  $: realActivityEvents = state.events.filter((event) => typeof event.payload.runId === "string");
  $: activeGoal = goals.find((goal) => goal.id === (selected?.kind === "goal" ? selected.id : selected?.kind === "agent" ? agents.find((agent) => agent.id === selected?.id)?.goalId : state.resources.find((resource) => resource.id === selected?.id)?.goalId)) ?? goals[0];
  $: activeAgent = selected?.kind === "agent" ? agents.find((agent) => agent.id === selected?.id) : null;
  $: activeResource = selected?.kind === "resource" ? state.resources.find((resource) => resource.id === selected?.id) : null;
  $: activeGuardCase = runtimeGuardCases.find((item) => item.id === selectedGuardCaseId) ?? runtimeGuardCases[0] ?? null;
  $: activeGoalGuardCase = runtimeGuardCases.find((item) => item.goal_id === activeGoal?.id) ?? null;
  $: activeGuardAgent = activeGuardCase ? agents.find((item) => item.id === activeGuardCase.agent_id && item.runtime) ?? null : null;
  $: goalAgents = activeGoal ? agents.filter((agent) => agent.goalId === activeGoal.id) : [];
  $: activeRuntimeAgents = activeGoal ? agents.filter((agent) => agent.runtime && agent.goalId === activeGoal.id) : [];
  $: activeEvidence = activeGoal ? state.evidence.filter((item) => item.goalId === activeGoal.id) : [];
  $: activePendingApprovals = activeGoal ? pendingRuntimeApprovals.filter((item) => item.goalId === activeGoal.id) : [];
  $: pendingCount = Number(state.approval?.status === "pending") + runtimeGuardCases.filter((item) => !["closed", "inconclusive"].includes(item.status)).length + pendingRuntimeApprovals.length;
  $: if (hydrated) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    localStorage.setItem(tokenBudgetKey, String(runtimeTokenBudget));
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
  async function refreshServiceAccounts() {
    try {
      const result = await invoke<{ accounts: ConnectedAccount[] }>("account_status");
      connectedAccounts = result.accounts;
    } catch { connectedAccounts = []; }
  }
  async function connectService(provider: ServiceProviderId) {
    if (connectingService) return;
    connectingService = provider;
    serviceError = "";
    try {
      const result = await invoke<{ connected: boolean; account: ConnectedAccount }>("oauth_connect", provider === "fake-mail" ? { provider, email: "demo.person@example.test" } : { provider });
      connectedAccounts = [...connectedAccounts.filter((item) => !(item.provider === result.account.provider && item.email === result.account.email)), result.account];
      void say(provider === "fake-mail" ? "Fake test mailbox connected." : `${provider} account connected.`);
      showNotice(provider === "fake-mail" ? "Fake test mailbox connected. Fixtures are labeled and never live mail." : `${result.account.email} connected. Tokens are in the system keychain.`);
    } catch (error) {
      serviceError = error instanceof Error ? error.message : String(error);
      showNotice(serviceError);
    } finally { connectingService = null; }
  }
  async function disconnectService(account: ConnectedAccount) {
    if (connectingService) return;
    connectingService = account.provider;
    try {
      await invoke<{ disconnected: boolean; remote_revoked: boolean }>("oauth_disconnect", { provider: account.provider, email: account.email });
      connectedAccounts = connectedAccounts.filter((item) => !(item.provider === account.provider && item.email === account.email));
      void say(`${account.provider} account disconnected.`);
      showNotice(`${account.email} disconnected. Its credentials were deleted from the system keychain.`);
    } catch (error) { showNotice(error instanceof Error ? error.message : String(error)); }
    finally { connectingService = null; }
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
    return { id: item.agent_id, name: item.parent_agent_id ? "Research subagent" : item.provider === "meta" ? "Muse researcher" : `${providerName} researcher`, provider: providerName, model: item.model, ...(item.parent_agent_id ? { parentAgentId: item.parent_agent_id } : {}), goalId: item.goal_id, status: mapRuntimeStatus(item.status), permissions: item.permissions, capabilities: item.permissions, currentTask: item.task, createdAt: item.created_at, steps: item.steps, error: item.error, runtime: true, runId: item.id, browserIsolated: Boolean(item.browser_isolated) };
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
        if (!pendingRuntimeApprovals.some((item) => item.runId === event.run_id && item.callId === callId)) {
          pendingRuntimeApprovals = [...pendingRuntimeApprovals, { runId: event.run_id, callId, goalId: event.goal_id, tool: event.tool ?? "browser interaction", arguments: (event.payload.arguments as Record<string, unknown>) ?? {} }];
          const approvalKey = `${event.run_id}:${callId}`;
          if (!announcedApprovals.has(approvalKey)) {
            announcedApprovals.add(approvalKey);
            const goalTitle = goals.find((goal) => goal.id === event.goal_id)?.title ?? "your goal";
            say(`The research agent on ${goalTitle} wants to run ${event.tool ?? "a page action"}. Your decision is needed in the goal panel.`);
          }
        }
      }
      if (["agent.stopped", "agent.failed"].includes(event.type)) pendingRuntimeApprovals = pendingRuntimeApprovals.filter((item) => item.runId !== event.run_id);
      else if (["tool.approval_denied", "tool.execution.started"].includes(event.type) && callId) pendingRuntimeApprovals = pendingRuntimeApprovals.filter((item) => !(item.runId === event.run_id && item.callId === callId));
    }
  }
  function syncRuntimeSnapshot(snapshot: RuntimeSnapshot) {
    if (!snapshotPrimed) {
      snapshotPrimed = true;
      for (const guardCase of snapshot.guard_cases ?? []) announcedCases.add(guardCase.id);
      for (const agent of snapshot.agents ?? []) if (agent.status === "completed") announcedCompletions.add(agent.agent_id);
    }
    for (const guardCase of snapshot.guard_cases ?? []) {
      if (!announcedCases.has(guardCase.id) && guardCase.status !== "closed") {
        announcedCases.add(guardCase.id);
        say(`Guard opened an investigation on ${guardCase.goal_title}. Ask me why if you want the evidence.`);
      }
    }
    runtimeGuardCases = snapshot.guard_cases ?? [];
    if (selectedGuardCaseId && !runtimeGuardCases.some((item) => item.id === selectedGuardCaseId)) selectedGuardCaseId = "";
    const previous = new Map(state.agents.filter((item) => item.runtime).map((item) => [item.id, item.status]));
    const runtimeAgents = (snapshot.agents ?? []).map(mapRuntimeAgent);
    for (const agent of runtimeAgents) {
      if (agent.status === "completed" && previous.has(agent.id) && previous.get(agent.id) !== "completed" && !announcedCompletions.has(agent.id)) {
        announcedCompletions.add(agent.id);
        const goalTitle = goals.find((goal) => goal.id === agent.goalId)?.title ?? "your goal";
        const findings = (snapshot.evidence ?? []).filter((item) => item.run_id === agent.runId).length;
        say(`Research for ${goalTitle} is complete with ${findings} findings. ${goalTitle} is ready to review.`);
      }
    }
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
    if (!Number.isInteger(runtimeTokenBudget) || runtimeTokenBudget < 1000 || runtimeTokenBudget > 50000) {
      runtimeError = "Choose a token budget from 1,000 to 50,000.";
      return;
    }
    startingRuntimeRun = true;
    try {
      const run = await invoke<RuntimeAgent>("runtime_create_run", { goalId: activeGoal.id, goalTitle: activeGoal.title, goalDescription: activeGoal.description, task: "Search the public web for relevant sources, inspect the most useful pages, and summarize three findings with exact supporting quotes and source links.", sourceUrl: runtimeSourceUrl.trim() || null, tokenBudget: runtimeTokenBudget });
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
  async function startGuardInvestigation(caseId: string) {
    if (investigatingCaseId) return;
    investigatingCaseId = caseId; guardInvestigationError = "";
    try {
      const result = await invoke<{ status: string; provider: string; model: string }>("runtime_guard_investigate", { caseId });
      showNotice(`Detective report ready for review · ${result.provider} · ${result.model}`);
      const snapshot = await invoke<RuntimeSnapshot>("runtime_snapshot");
      syncRuntimeSnapshot(snapshot);
    } catch (error) {
      guardInvestigationError = error instanceof Error ? error.message : String(error);
      showNotice(guardInvestigationError);
      void invoke<RuntimeSnapshot>("runtime_snapshot").then(syncRuntimeSnapshot).catch(() => {});
    } finally { investigatingCaseId = ""; }
  }
  async function startGuardInterview(caseId: string) {
    if (interviewingCaseId || investigatingCaseId) return;
    interviewingCaseId = caseId; guardInvestigationError = "";
    try {
      const result = await invoke<{ status: string; complete: boolean; turn: number; provider?: string; model?: string }>("runtime_guard_interview", { caseId });
      showNotice(result.complete ? "Interview complete · review the case record" : `Interview question ${result.turn} answered · response saved for review`);
      const snapshot = await invoke<RuntimeSnapshot>("runtime_snapshot");
      syncRuntimeSnapshot(snapshot);
    } catch (error) {
      guardInvestigationError = error instanceof Error ? error.message : String(error);
      showNotice(guardInvestigationError);
      void invoke<RuntimeSnapshot>("runtime_snapshot").then(syncRuntimeSnapshot).catch(() => {});
    } finally { interviewingCaseId = ""; }
  }
  async function startGuardAdjudication(caseId: string) {
    if (adjudicatingCaseId || interviewingCaseId || investigatingCaseId) return;
    adjudicatingCaseId = caseId; guardInvestigationError = "";
    try {
      const result = await invoke<{ status: string; provider: string; model: string; report: { disposition: string } }>("runtime_guard_adjudicate", { caseId });
      showNotice(`Independent review ready · ${result.report.disposition.replaceAll("_", " ")}`);
      const snapshot = await invoke<RuntimeSnapshot>("runtime_snapshot");
      syncRuntimeSnapshot(snapshot);
    } catch (error) {
      guardInvestigationError = error instanceof Error ? error.message : String(error);
      showNotice(guardInvestigationError);
      void invoke<RuntimeSnapshot>("runtime_snapshot").then(syncRuntimeSnapshot).catch(() => {});
    } finally { adjudicatingCaseId = ""; }
  }
  type ContainmentAction = "pause_agent" | "resume_agent" | "deny_pending" | "revoke_capability" | "isolate_browser" | "restore_browser";
  async function applyGuardContainment(caseId: string, action: ContainmentAction, capability?: string) {
    const prompts: Record<ContainmentAction, string> = {
      pause_agent: "Pause the affected agent at its next safe step? KeplerV will record this user-authorized action in the case.",
      resume_agent: "Resume the affected agent? KeplerV will record this user-authorized action in the case.",
      deny_pending: "Deny every pending page action for this agent and pause it? KeplerV will record this user-authorized action in the case.",
      revoke_capability: `Revoke the ${capability} capability from this agent? Later actions needing it will be denied. KeplerV will record this user-authorized action in the case.`,
      isolate_browser: "Isolate this agent's browser session? Open pages close and later browser actions are denied until restored. KeplerV will record this user-authorized action in the case.",
      restore_browser: "Restore this agent's browser session? KeplerV will record this user-authorized action in the case.",
    };
    if (!window.confirm(prompts[action])) return;
    try {
      const result = await invoke<{ agent_status: string }>("runtime_guard_containment", {
        caseId, action, capability: capability ?? null,
        reason: `User approved ${action}${capability ? ` (${capability})` : ""} after reviewing this Guard case.`,
      });
      showNotice(`Agent ${result.agent_status} · action recorded in Guard case`);
      const snapshot = await invoke<RuntimeSnapshot>("runtime_snapshot");
      syncRuntimeSnapshot(snapshot);
    } catch (error) { showNotice(error instanceof Error ? error.message : String(error)); }
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
  function openNewIntention() { newDescription = ""; panel = "create_goal"; }
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
    if (item === "guard") { panel = "guard"; selectedGuardCaseId = runtimeGuardCases[0]?.id ?? ""; return; }
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

  function voice(): VoiceSpeech {
    if (!speech) speech = new VoiceSpeech({ onState: (speaking) => {
      if (speaking === "speaking") presence = "speaking";
      else if (presence === "speaking") presence = "resting";
    } });
    return speech;
  }
  function say(text: string): void { duckMediaForSpeech(); voice().announce(text); }
  function duckMediaForSpeech(): void {
    const { state, ducked } = duckForSpeech(mediaState);
    mediaState = state;
    if (ducked.includes("local-video")) localVideoEl?.pause();
    if (ducked.includes("youtube")) pauseYoutubeFrame();
  }
  function pauseYoutubeFrame(): void {
    try {
      youtubeFrameEl?.contentWindow?.postMessage(JSON.stringify({ event: "command", func: "pauseVideo", args: [] }), "https://www.youtube-nocookie.com");
    } catch {
      /* cross-origin postMessage is best-effort; focus state is the record */
    }
  }
  function playYoutubeFrame(): void {
    try {
      youtubeFrameEl?.contentWindow?.postMessage(JSON.stringify({ event: "command", func: "playVideo", args: [] }), "https://www.youtube-nocookie.com");
    } catch {
      /* cross-origin postMessage is best-effort; focus state is the record */
    }
  }
  function pauseInactiveElements(except: MediaSurfaceId): void {
    if (except !== "local-video") localVideoEl?.pause();
    if (except !== "youtube") pauseYoutubeFrame();
  }
  function openMediaSurface(target: MediaSurfaceId, opts: { videoId?: string } = {}): string {
    if (target === "youtube") {
      const probe = probeYouTubeEmbed({ iframeAllowed: typeof document !== "undefined" });
      const candidate = (opts.videoId ?? youtubeVideoId).trim();
      const src = youtubeEmbedUrl(candidate);
      if (!probe.embeddable || !src) {
        youtubeVideoId = candidate;
        const message = candidate && !src
          ? "That YouTube video id looks invalid. Use an 11-character video id."
          : "Enter an 11-character YouTube video id to embed it with the official player.";
        showNotice(message);
        mediaSurface = "youtube"; mediaView = true; panel = null;
        return message;
      }
      youtubeVideoId = candidate;
      youtubeSrc = src;
    }
    const result = applyMediaCommand(mediaState, "OPEN", target);
    mediaState = result.state;
    if (result.ok) {
      pauseInactiveElements(target);
      mediaSurface = target; mediaView = true; panel = null;
    }
    showNotice(result.message);
    return result.message;
  }
  function pauseMedia(target?: MediaSurfaceId | null): string {
    const result = applyMediaCommand(mediaState, "PAUSE", target ?? mediaSurface);
    mediaState = result.state;
    if (result.ok) { localVideoEl?.pause(); pauseYoutubeFrame(); }
    showNotice(result.message);
    return result.message;
  }
  function resumeMedia(target?: MediaSurfaceId | null): string {
    const id = target ?? mediaSurface;
    const result = applyMediaCommand(mediaState, "RESUME", id);
    mediaState = result.state;
    if (result.ok && id) {
      pauseInactiveElements(id);
      if (id === "local-video") void localVideoEl?.play().catch(() => {});
      if (id === "youtube") playYoutubeFrame();
      mediaSurface = id; mediaView = true;
    }
    showNotice(result.message);
    return result.message;
  }
  function closeMedia(target?: MediaSurfaceId | null): string {
    const result = applyMediaCommand(mediaState, "CLOSE_MEDIA", target ?? mediaSurface);
    mediaState = result.state;
    localVideoEl?.pause(); pauseYoutubeFrame();
    if (!target || target === mediaSurface) { mediaView = false; mediaSurface = null; }
    showNotice(result.message);
    return result.message;
  }
  function openNetflixExternally(): void {
    void invoke("open_public_resource", { url: NETFLIX_URL, label: "Netflix" })
      .then(() => showNotice("Netflix opened in the system browser. Embedded playback is not supported."))
      .catch((error) => showNotice(String(error)));
  }
  function pushVoiceTurn(role: string, text: string): void {
    voiceConversation = [...voiceConversation.slice(-7), { role, text: text.slice(0, 500) }];
  }

  async function startAudio() {
    const requestId = ++audioRequestId;
    micError = ""; voiceTranscript = ""; voiceResult = "";
    voice().interrupt();
    duckMediaForSpeech();
    intentStep = "listening"; presence = "listening"; audioPhase = 0; micLevel = 0;
    try {
      const recorder = new VoiceRecorder({ onLevel: (level) => { micLevel = level; audioPhase += 0.003 + level * 0.11; } });
      voiceRecorder = recorder;
      const done = recorder.run();
      audioStream = await recorder.start();
      if (requestId !== audioRequestId) { recorder.cancel(); return; }
      const wav = await done;
      voiceRecorder = null; audioStream = null; micLevel = 0;
      if (requestId !== audioRequestId || !wav) return;
      await processVoiceRecording(wav, requestId);
    } catch (error) {
      if (requestId !== audioRequestId) return;
      voiceRecorder = null; audioStream = null; micLevel = 0;
      micError = error instanceof Error ? error.message : "Microphone access was denied or is unavailable.";
      presence = "unavailable"; intentStep = "unavailable";
    }
  }

  async function stopAudio() {
    audioRequestId += 1;
    cancelAnimationFrame(animationFrame);
    voiceRecorder?.cancel(); voiceRecorder = null;
    audioStream = null; micLevel = 0;
  }
  function finishCapture() { voiceRecorder?.stop(); }
  function cancelIntent() { void stopAudio(); intentStep = "idle"; presence = "resting"; micError = ""; }

  async function processVoiceRecording(wav: ArrayBuffer, requestId: number) {
    intentStep = "interpreting"; presence = "processing";
    try {
      const result = await invoke<{ transcript: string; duration_ms: number }>("transcribe_audio", { audioB64: arrayBufferToBase64(wav) });
      if (requestId !== audioRequestId) return;
      voiceTranscript = result.transcript; chosenIntent = result.transcript;
      pushVoiceTurn("user", result.transcript);
      await dispatchVoiceCommand(result.transcript, requestId);
    } catch (error) {
      if (requestId !== audioRequestId) return;
      micError = error instanceof Error ? error.message : String(error);
      presence = "unavailable"; intentStep = "unavailable";
    }
  }

  async function interpretIntent() {
    if (!chosenIntent.trim()) return;
    intentStep = "interpreting"; presence = "processing";
    pushVoiceTurn("user", chosenIntent);
    await dispatchVoiceCommand(chosenIntent, audioRequestId);
  }

  type VoiceIntent = { type: string; target_id?: string | null; args: Record<string, unknown>; ambiguous: boolean; needs_clarification: boolean; clarification_question?: string | null; permission: string; summary_for_speech: string };

  function buildVoiceContext() {
    return {
      goals: goals.slice(0, 40).map((goal) => ({ id: goal.id, title: goal.title, description: goal.description })),
      agents: agents.slice(0, 80).map((agent) => ({ id: agent.id, name: agent.name, goalId: agent.goalId, status: agent.status })),
      evidence: state.evidence.slice(0, 120).map((item, index) => ({ id: item.id, goalId: item.goalId, finding: item.finding, position: index + 1 })),
      resources: state.resources.slice(0, 120).map((item) => ({ id: item.id, goalId: item.goalId, label: item.label, url: item.url })),
      approvals: [...pendingRuntimeApprovals.map((item) => ({ id: `${item.runId}:${item.callId}`, goalId: item.goalId, action: item.tool })), ...(state.approval && state.approval.status === "pending" ? [{ id: state.approval.id, goalId: state.approval.goalId, action: state.approval.action }] : [])].slice(0, 20),
      cases: runtimeGuardCases.slice(0, 40).map((item) => ({ id: item.id, goal_title: item.goal_title, status: item.status, signal: item.signal })),
      accounts: connectedAccounts.slice(0, 20).map((item) => ({ id: `${item.provider}:${item.email}`, provider: item.provider, email: item.email, status: item.status })),
      messages: [...(mailSelected ? [mailSelected] : []), ...mailMessages].slice(0, 40).map((item) => ({ id: item.id, subject: item.subject, from: item.from ?? "" })),
      selection: { ...(selected ? { kind: selected.kind, id: selected.id } : {}), panel },
      recent_events: state.events.slice(0, 8).map(eventLabel),
      conversation: voiceConversation.slice(-8),
    };
  }

  async function dispatchVoiceCommand(transcript: string, requestId: number) {
    try {
      const intent = await invoke<VoiceIntent>("interpret_voice", { transcript, context: buildVoiceContext() });
      if (requestId !== audioRequestId) return;
      const spoken = await executeVoiceIntent(intent, transcript);
      pushVoiceTurn("kepler", spoken);
      say(spoken);
      voiceResult = spoken;
      intentStep = "intent_ready"; presence = "resting";
    } catch (error) {
      if (requestId !== audioRequestId) return;
      const message = error instanceof Error ? error.message : String(error);
      voiceResult = message; intentStep = "intent_ready"; presence = "resting";
      showNotice(message);
    }
  }

  function findVoiceTarget(intent: VoiceIntent) {
    const id = intent.target_id ?? undefined;
    return {
      goal: id ? goals.find((item) => item.id === id) : undefined,
      agent: id ? agents.find((item) => item.id === id) : undefined,
      evidence: id ? state.evidence.find((item) => item.id === id) : undefined,
      resource: id ? state.resources.find((item) => item.id === id) : undefined,
      approval: id ? pendingRuntimeApprovals.find((item) => `${item.runId}:${item.callId}` === id) : undefined,
      localApproval: id && state.approval?.id === id && state.approval.status === "pending" ? state.approval : undefined,
      guardCase: id ? runtimeGuardCases.find((item) => item.id === id) : undefined,
      account: id ? connectedAccounts.find((item) => item.email === id || `${item.provider}:${item.email}` === id) : undefined,
      message: id ? [...(mailSelected ? [mailSelected] : []), ...mailMessages].find((item) => item.id === id) : undefined,
    };
  }

  function resolveVoiceAccount(intent: VoiceIntent) {
    const provider = typeof intent.args.provider === "string" ? intent.args.provider : undefined;
    const email = typeof intent.args.email === "string" ? intent.args.email : undefined;
    return resolveAccount(connectedAccounts, { provider, email });
  }

  function selectedMailAccount(): ConnectedAccount | undefined {
    if (mailAccountKey) {
      const ref = parseAccountKey(mailAccountKey);
      const hit = resolveAccount(connectedAccounts, ref);
      if (hit.kind === "resolved") return hit.account;
    }
    if (connectedAccounts.length === 1) return connectedAccounts[0];
    return undefined;
  }

  async function loadMail(account: ConnectedAccount | undefined, query?: string): Promise<void> {
    mailError = "";
    if (!account) {
      mailMessages = []; mailSelected = null;
      mailError = connectedAccounts.length ? "Pick one account above to read its mail." : "No service account is connected. Nothing is shown without a connection.";
      return;
    }
    mailLoading = true;
    try {
      const result = await invoke<{ messages: MailMessage[] }>("runtime_mail_list", {
        provider: account.provider, email: account.email,
        ...(query?.trim() ? { query: query.trim() } : {}), limit: 10,
      });
      mailMessages = result.messages;
      mailSelected = null;
      mailAccountKey = accountKey(account);
    } catch (error) {
      mailMessages = []; mailSelected = null;
      mailError = error instanceof Error ? error.message : String(error);
    } finally { mailLoading = false; }
  }

  async function readMailMessage(account: ConnectedAccount, messageId: string): Promise<void> {
    mailError = "";
    mailLoading = true;
    try {
      mailSelected = await invoke<MailMessage>("runtime_mail_get", { messageId, provider: account.provider, email: account.email });
      mailAccountKey = accountKey(account);
    } catch (error) {
      mailError = error instanceof Error ? error.message : String(error);
    } finally { mailLoading = false; }
  }

  function decideEmailSend(approved: boolean) {
    const draft = pendingEmailSend;
    pendingEmailSend = null;
    closePanel(); presence = "resting";
    if (approved && draft) {
      showNotice(`Approval recorded for the draft to ${draft.to}. Nothing was sent: the mail connector is read-only.`);
    } else {
      showNotice("Email send declined. No draft was sent.");
    }
  }

  function describeGoalStatus(goal: Goal): string {
    const helpers = agents.filter((agent) => agent.goalId === goal.id);
    const findings = state.evidence.filter((item) => item.goalId === goal.id).length;
    const pending = pendingRuntimeApprovals.filter((item) => item.goalId === goal.id).length;
    const openCases = runtimeGuardCases.filter((item) => item.goal_id === goal.id && item.status !== "closed").length;
    return `${goal.title} is ${goal.progress}% complete with ${helpers.length} agents and ${findings} findings.`
      + (pending ? ` ${pending} actions await your decision.` : "") + (openCases ? ` ${openCases} Guard investigations are open.` : "");
  }

  function explainGuardCase(guardCase: RuntimeGuardCase): string {
    const report = guardCase.evidence.find((item) => item.source_type === "guard_adjudication")?.payload as { report?: { summary?: string; disposition?: string } } | undefined;
    const detective = guardCase.evidence.find((item) => item.source_type === "guard_report")?.payload as { report?: { summary?: string } } | undefined;
    const turns = guardCase.evidence.filter((item) => item.source_type === "guard_interview_turn").length;
    const summary = report?.report?.summary ?? detective?.report?.summary ?? guardCase.summary;
    return `Guard case on ${guardCase.goal_title}: ${guardCase.signal.replaceAll("_", " ")}, status ${guardCase.status.replaceAll("_", " ")}.`
      + ` ${summary} ${turns} interview questions asked.`
      + (report?.report?.disposition ? ` Disposition: ${report.report.disposition.replaceAll("_", " ")}.` : " No independent disposition yet.");
  }

  async function executeVoiceIntent(intent: VoiceIntent, transcript: string): Promise<string> {
    if (intent.needs_clarification) return intent.clarification_question || intent.summary_for_speech;
    const target = findVoiceTarget(intent);
    const argText = (name: string): string | undefined => {
      const value = intent.args[name];
      return typeof value === "string" && value.trim() ? value.slice(0, 500) : undefined;
    };
    switch (intent.type) {
      case "CREATE_GOAL": {
        const title = (argText("title") ?? transcript.split(/[.!?]/)[0].trim().slice(0, 64)) || "New intention";
        const description = argText("description") ?? transcript;
        const next = createGoal(state, title, description);
        const goal = next.goals[0];
        commit(next); selected = { kind: "goal", id: goal.id }; panel = "goal";
        if (intent.args.start_research === true) {
          if (!runtimeModelConfigured) return `I created ${title}, but Muse is not connected, so research cannot start yet.`;
          await startMuseResearch();
          return `I created ${title} and started a Muse research agent. I will speak up when findings arrive.`;
        }
        return `I created the goal ${title}.`;
      }
      case "MODIFY_GOAL": {
        const goal = target.goal ?? activeGoal;
        if (!goal) return "Which goal should I change?";
        const patch = { title: argText("title"), description: argText("description"), addTask: argText("add_task"), addCriterion: argText("add_criterion"), addConstraint: argText("add_constraint") };
        if (!patch.title && !patch.description && !patch.addTask && !patch.addCriterion && !patch.addConstraint) return `What should change about ${goal.title}?`;
        commit(modifyGoal(state, goal.id, patch));
        selected = { kind: "goal", id: goal.id }; panel = "goal";
        return `Updated ${goal.title}.`;
      }
      case "START_AGENT": {
        const goal = target.goal ?? activeGoal;
        if (!goal) return "Which goal should I research?";
        if (!runtimeModelConfigured) return "Muse is not connected, so I cannot start research yet.";
        selected = { kind: "goal", id: goal.id }; panel = "goal";
        await startMuseResearch();
        return `Starting a research agent for ${goal.title}.`;
      }
      case "PAUSE_AGENT":
      case "RESUME_AGENT":
      case "STOP_AGENT": {
        const action = intent.type === "PAUSE_AGENT" ? "pause" : intent.type === "RESUME_AGENT" ? "resume" : "stop";
        const agent = target.agent ?? (activeAgent?.status === "working" || activeAgent?.status === "paused" ? activeAgent : agents.find((item) => item.status === "working"));
        if (!agent) return "I could not find a running agent to control.";
        if (agent.runtime && agent.runId) await controlRuntimeAgent(agent, action);
        else commit(transitionAgent(state, agent.id, action));
        return `${agent.name} ${action === "pause" ? "paused" : action === "resume" ? "resumed" : "stopped"}.`;
      }
      case "GET_STATUS": {
        if (target.agent) {
          selected = { kind: "agent", id: target.agent.id }; panel = "agent";
          return `${target.agent.name} is ${target.agent.status}. ${target.agent.currentTask ?? "No active task."}`;
        }
        const goal = target.goal ?? activeGoal;
        if (goal) {
          selected = { kind: "goal", id: goal.id }; panel = "goal";
          return describeGoalStatus(goal);
        }
        return `${goals.length} goals, ${agents.filter((item) => item.status === "working").length} working agents, ${state.evidence.length} findings.`;
      }
      case "INSPECT_RESOURCE": {
        if (!target.resource) return "Which link should I open?";
        selected = { kind: "resource", id: target.resource.id }; panel = null; resourceView = true;
        return `Opening ${target.resource.label}.`;
      }
      case "SHOW_EVIDENCE": {
        const item = target.evidence ?? (activeEvidence.length === 1 ? activeEvidence[0] : undefined);
        if (!item) return activeEvidence.length ? "Which finding should I show?" : "There are no findings yet.";
        const resource = state.resources.find((entry) => entry.goalId === item.goalId && entry.url === item.sourceUrl);
        if (resource) { selected = { kind: "resource", id: resource.id }; panel = null; resourceView = true; }
        return `Showing: ${item.finding}`;
      }
      case "OPEN_INVESTIGATION": {
        const guardCase = target.guardCase ?? (runtimeGuardCases.length === 1 ? runtimeGuardCases[0] : undefined);
        if (!guardCase) return runtimeGuardCases.length ? "Which investigation should I open?" : "No Guard investigations are open.";
        panel = "guard"; currentMenu = "guard"; selectedGuardCaseId = guardCase.id;
        return `Opening the investigation on ${guardCase.goal_title}.`;
      }
      case "REQUEST_EXPLANATION": {
        const guardCase = target.guardCase ?? (runtimeGuardCases.length === 1 ? runtimeGuardCases[0] : undefined);
        if (guardCase) {
          panel = "guard"; currentMenu = "guard"; selectedGuardCaseId = guardCase.id;
          return explainGuardCase(guardCase);
        }
        const agent = target.agent;
        if (agent?.error) return `${agent.name} reported: ${agent.error}`;
        if (agent) return `${agent.name} is ${agent.status} on ${goals.find((goal) => goal.id === agent.goalId)?.title ?? "its goal"}.`;
        return "I found no investigation or agent problem to explain.";
      }
      case "REQUEST_APPROVAL_DETAILS": {
        if (pendingEmailSend) { panel = "approval"; return `An email to ${pendingEmailSend.to} is waiting for your approval. I opened the request — confirm it in the panel.`; }
        const pending = target.approval ?? (pendingRuntimeApprovals.length === 1 ? pendingRuntimeApprovals[0] : undefined);
        if (pending) {
          const goal = goals.find((item) => item.id === pending.goalId);
          if (goal) { selected = { kind: "goal", id: goal.id }; panel = "goal"; }
          return `The research agent wants to run ${pending.tool}. I opened the request in the goal panel. Approve or deny it there.`;
        }
        if (target.localApproval ?? (state.approval?.status === "pending" ? state.approval : undefined)) { panel = "approval"; return "I opened the pending approval. Confirm your decision in the panel."; }
        return "No actions are waiting for approval.";
      }
      case "APPROVE_ACTION":
      case "DENY_ACTION": {
        const pending = target.approval ?? (pendingRuntimeApprovals.length === 1 ? pendingRuntimeApprovals[0] : undefined);
        const verb = intent.type === "APPROVE_ACTION" ? "approve" : "deny";
        if (pendingEmailSend) { panel = "approval"; return `I opened the email send request. To stay safe I never ${verb} by voice alone, so confirm it in the panel.`; }
        if (pending) {
          const goal = goals.find((item) => item.id === pending.goalId);
          if (goal) { selected = { kind: "goal", id: goal.id }; panel = "goal"; }
          return `I opened the ${pending.tool} request. To stay safe I never ${verb} by voice alone, so confirm it in the goal panel.`;
        }
        if (state.approval?.status === "pending") { panel = "approval"; return `I opened the payment approval. Confirm ${verb} in the panel.`; }
        return "Nothing is waiting for approval.";
      }
      case "OPEN_MEDIA": {
        const target = resolveMediaTarget(argText("target"));
        if (!target) return "Which media should I open: local video, YouTube, or Netflix?";
        if (target === "youtube") {
          const videoId = argText("video_id");
          if (videoId) youtubeVideoId = videoId;
          if (!youtubeVideoId.trim()) return "Which YouTube video? Give me its 11-character video id.";
        }
        return openMediaSurface(target, { videoId: youtubeVideoId });
      }
      case "PAUSE_MEDIA":
        return pauseMedia(resolveMediaTarget(argText("target")));
      case "RESUME_MEDIA":
        return resumeMedia(resolveMediaTarget(argText("target")));
      case "CLOSE_MEDIA":
        return closeMedia(resolveMediaTarget(argText("target")));
      case "CONNECT_ACCOUNT": {
        const provider = argText("provider")?.toLowerCase();
        if (provider !== "gmail" && provider !== "outlook" && provider !== "fake-mail") {
          panel = "providers"; currentMenu = "providers";
          return "Which service should I connect: Gmail, Outlook, or the fake test mailbox? I opened the services panel.";
        }
        if (provider === "fake-mail") {
          await connectService("fake-mail");
          panel = "providers"; currentMenu = "providers";
          return "Fake test mailbox connected. Its fixtures are labeled and never live mail.";
        }
        panel = "providers"; currentMenu = "providers";
        return `To stay safe I never sign in by voice alone, so I opened the services panel. Choose Connect ${provider === "gmail" ? "Gmail" : "Outlook"} there.`;
      }
      case "DISCONNECT_ACCOUNT":
      case "REVOKE_AGENT_SERVICE_ACCESS": {
        const named = target.account;
        const resolved = named ? { kind: "resolved" as const, account: named } : resolveVoiceAccount(intent);
        if (resolved.kind === "ambiguous") {
          panel = "providers"; currentMenu = "providers";
          return `That matches ${resolved.candidates.length} accounts. I opened the services panel — pick the one to disconnect.`;
        }
        const account = resolved.kind === "resolved" ? resolved.account : (connectedAccounts.length === 1 ? connectedAccounts[0] : undefined);
        if (!account) {
          panel = "providers"; currentMenu = "providers";
          return connectedAccounts.length ? "Which account should I disconnect?" : "No service account is connected.";
        }
        await disconnectService(account);
        panel = "providers"; currentMenu = "providers";
        return intent.type === "REVOKE_AGENT_SERVICE_ACCESS"
          ? `${account.email} is disconnected. Agents can no longer read it, and its credentials were deleted from the system keychain.`
          : `${account.email} disconnected.`;
      }
      case "LIST_CONNECTED_ACCOUNTS":
      case "OPEN_SERVICE":
      case "SHOW_SERVICE_PERMISSIONS": {
        panel = "providers"; currentMenu = "providers";
        if (!connectedAccounts.length) return "No service account is connected. I opened the services panel to connect one.";
        const detail = connectedAccounts.map((item) => `${item.email} (${item.provider}, ${item.status})`).join("; ");
        if (intent.type === "SHOW_SERVICE_PERMISSIONS") {
          const scopes = connectedAccounts.map((item) => `${item.email}: ${item.scopes.join(", ")}`).join("; ");
          return `Connected accounts: ${detail}. Granted scopes: ${scopes}. Tokens stay in the system keychain.`;
        }
        return `Connected accounts: ${detail}. I opened the services panel.`;
      }
      case "SEARCH_EMAIL": {
        const query = argText("query");
        if (!query) return "What should I search your mail for?";
        const named = target.account;
        const resolved = named ? { kind: "resolved" as const, account: named } : resolveVoiceAccount(intent);
        if (resolved.kind === "ambiguous") {
          panel = "providers"; currentMenu = "providers";
          return "That matches several accounts. I opened the services panel — pick one, then search again.";
        }
        const account = resolved.kind === "resolved" ? resolved.account : (connectedAccounts.length === 1 ? connectedAccounts[0] : undefined);
        if (!account) {
          panel = "providers"; currentMenu = "providers";
          return connectedAccounts.length ? "Which account should I search?" : "No service account is connected, so there is nothing to search.";
        }
        mailQuery = query;
        await loadMail(account, query);
        panel = "providers"; currentMenu = "providers";
        if (mailError) return mailError;
        return mailMessages.length ? `Found ${mailMessages.length} message${mailMessages.length === 1 ? "" : "s"} in ${account.email}. I opened them in the services panel.` : `No messages matched in ${account.email}.`;
      }
      case "READ_EMAIL": {
        const named = target.account;
        const resolved = named ? { kind: "resolved" as const, account: named } : resolveVoiceAccount(intent);
        if (resolved.kind === "ambiguous") {
          panel = "providers"; currentMenu = "providers";
          return "That matches several accounts. I opened the services panel — pick one, then read again.";
        }
        const account = resolved.kind === "resolved" ? resolved.account : selectedMailAccount();
        if (!account) {
          panel = "providers"; currentMenu = "providers";
          return connectedAccounts.length ? "Which account should I read?" : "No service account is connected, so there is nothing to read.";
        }
        const messageId = target.message?.id ?? argText("message_id");
        if (messageId) {
          await readMailMessage(account, messageId);
          panel = "providers"; currentMenu = "providers";
          if (mailError) return mailError;
          return mailSelected ? `Opened: ${mailSelected.subject}. ${mailSelected.snippet ?? ""}` : "I could not open that message.";
        }
        await loadMail(account);
        panel = "providers"; currentMenu = "providers";
        if (mailError) return mailError;
        return mailMessages.length ? `Showing ${mailMessages.length} message${mailMessages.length === 1 ? "" : "s"} in ${account.email}.` : `No messages in ${account.email}.`;
      }
      case "PREPARE_EMAIL_DRAFT": {
        const to = argText("to");
        if (!to) return "Who should the draft be addressed to?";
        emailDraft = { to, subject: argText("subject") ?? "", body: argText("body") ?? "", accountEmail: selectedMailAccount()?.email };
        panel = "providers"; currentMenu = "providers";
        return `Draft prepared for ${to}. I did not send it — review it in the services panel.`;
      }
      case "REQUEST_SEND_EMAIL": {
        const to = argText("to") ?? emailDraft?.to;
        if (!to) return "There is no draft to send. Tell me who it is for first.";
        pendingEmailSend = { to, subject: argText("subject") ?? emailDraft?.subject ?? "", body: argText("body") ?? emailDraft?.body ?? "", accountEmail: emailDraft?.accountEmail ?? selectedMailAccount()?.email };
        emailDraft = pendingEmailSend;
        panel = "approval";
        return `I opened the send request for your review. To stay safe I never send by voice alone, so confirm it in the panel.`;
      }
      case "CHANGE_APPEARANCE": {
        const theme = argText("theme");
        if (theme !== "light" && theme !== "dark" && theme !== "system") return "Which appearance: light, dark, or automatic?";
        commit({ ...state, theme }); applyTheme();
        return `Appearance set to ${theme === "system" ? "automatic" : theme}.`;
      }
      default:
        return intent.summary_for_speech;
    }
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
<svelte:window onkeydown={(event) => { if (event.key === "Escape") { if (panel) closePanel(); else if (mediaView) closeMedia(); } }} />

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
      <button class="menu-item" class:menu-active={currentMenu === "activity"} onclick={() => chooseMenu("activity")}><span class="menu-icon">≋</span><span>Recent activity</span><b>{realActivityEvents.length}</b></button>
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
          {#if activeGoalGuardCase}<path class="edge-incident" d="M500 290 C420 360 355 400 290 420" />{/if}
        </svg>
        <button class="presence-orb" class:listening={presence === "listening"} class:working={presence === "coordinating" || presence === "processing"} class:warning={presence === "authorization" || presence === "investigating"} aria-label={`KeplerV presence: ${presence}`} onclick={() => panel = "timeline"}>
          <span class="orb-halo halo-one"></span><span class="orb-halo halo-two"></span><span class="orb-core"></span><span class="orb-specular"></span><span class="orb-state">KEPLERV PRESENCE <b>{presence.replaceAll("_", " ").toUpperCase()}</b></span>
        </button>
        {#if activeGoal}<button class="goal-node goal-selected" style="--node-x:22%;--node-y:50%" aria-label={`Open intention ${activeGoal.title}`} onclick={() => selectGoal(activeGoal)}><span class="goal-ring"><span class="goal-point"></span></span><span class="node-copy"><b>{activeGoal.title}</b><small>ACTIVE GOAL · {activeGoal.progress}%</small></span></button>{/if}
        {#each goalAgents as agent, index}<button class="agent-node" class:agent-selected={selected?.kind === "agent" && selected.id === agent.id} class:agent-paused={agent.status === "paused" || agent.status === "investigating"} style={`--agent-x:76%;--agent-y:${graphAgentY(index, goalAgents.length)}%`} onclick={() => { selected = { kind: "agent", id: agent.id }; panel = "agent"; }}><span class="agent-orbit"><span></span></span><span class="agent-copy"><b>{agent.name}</b><small>{agent.status.toUpperCase()} · {agent.provider}</small></span></button>{/each}
        {#each state.resources.filter((resource) => resource.goalId === activeGoal?.id) as resource}<button class="resource-node" class:resource-selected={selected?.kind === "resource" && selected.id === resource.id} onclick={() => { selected = { kind: "resource", id: resource.id }; panel = "resource_detail"; }}><span>↗</span><b>{resource.label}</b><small>PUBLIC RESOURCE</small></button>{/each}
        {#if activeGoalGuardCase}<button class="incident-node" onclick={() => { selectedGuardCaseId = activeGoalGuardCase!.id; panel = "guard"; currentMenu = "guard"; }}><span>!</span><b>Guard case</b><small>{activeGoalGuardCase.status.replaceAll("_", " ").toUpperCase()}</small></button>{/if}
        <div class="board-label">CURRENT GOAL <span>RELATED TASKS &amp; HELPERS</span></div><div class="board-scale">01 — GOAL<br />02 — HELPERS<br />03 — LINKS</div>
        <div class="graph-legend"><span><i class="legend-goal"></i> GOAL</span><span><i class="legend-agent"></i> AGENT</span><span><i class="legend-resource"></i> RESOURCE</span></div>
      </div>

      <div class="goal-ribbon"><div class="ribbon-title"><span class="ribbon-index">GOAL</span><b>{activeGoal?.title ?? "No goal selected"}</b><small>{activeGoal?.description}</small></div><div class="ribbon-progress"><div><span>DONE</span><b>{activeGoal?.progress ?? 0}%</b></div><span class="progress-track"><i style={`width:${activeGoal?.progress ?? 0}%`}></i></span></div><div class="ribbon-agents"><span>HELPERS</span><b>{goalAgents.length.toString().padStart(2, "0")}</b></div><div class="ribbon-buttons"><button onclick={assignAgent}>＋ Add helper</button><button onclick={() => panel = "resource"}>↗ Add link</button><button onclick={() => openMediaSurface("local-video")}>▶ Media</button><button onclick={() => panel = "timeline"}>Recent activity ↗</button></div></div>
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

  {#if mediaView && mediaSurface}
    <section class="media-immersive" aria-label={`Media surface: ${surfaceLabel(mediaSurface)}`}>
      <header class="media-toolbar"><button class="media-back" onclick={() => closeMedia()}>← <span>Back to workspace</span></button><div class="media-address"><b>{surfaceLabel(mediaSurface)}</b><span>ONE AUDIBLE SURFACE · VOICE DUCKS MEDIA</span></div><div class="media-switch"><button class:media-on={mediaSurface === "local-video"} onclick={() => openMediaSurface("local-video")}>Local</button><button class:media-on={mediaSurface === "youtube"} onclick={() => openMediaSurface("youtube")}>YouTube</button><button class:media-on={mediaSurface === "netflix-fallback"} onclick={() => openMediaSurface("netflix-fallback")}>Netflix</button></div></header>
      {#if mediaSurface === "local-video"}
        <video class="media-video" bind:this={localVideoEl} src={LOCAL_DEMO_VIDEO_URL} controls playsinline preload="metadata" title="KeplerV local demo fixture"><track kind="captions" src="/media/keplerv-demo.en.vtt" srclang="en" label="English" /></video>
        <div class="media-frame-note"><span>LOCAL FIXTURE · generated test pattern, no licensed content</span><span class="media-transport"><button onclick={() => pauseMedia()}>Pause</button><button onclick={() => resumeMedia()}>Resume</button><button onclick={() => closeMedia()}>Close</button></span></div>
      {:else if mediaSurface === "youtube"}
        {#if youtubeSrc}<iframe class="media-frame" bind:this={youtubeFrameEl} src={youtubeSrc} title="YouTube embed" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>
        {:else}<div class="media-prompt"><form onsubmit={(event) => { event.preventDefault(); openMediaSurface("youtube"); }}><label>YOUTUBE VIDEO ID<input bind:value={youtubeVideoId} maxlength="11" placeholder="11-character id from the video URL" /></label><button class="object-primary" type="submit">Embed with the official player</button></form><small>Official youtube-nocookie iframe player only. KeplerV never fetches or rehosts video.</small></div>{/if}
        <div class="media-frame-note"><span>YOUTUBE EMBED · official player · capability-checked</span><span class="media-transport"><button onclick={() => pauseMedia()}>Pause</button><button onclick={() => resumeMedia()}>Resume</button><button onclick={() => closeMedia()}>Close</button></span></div>
      {:else}
        <div class="media-prompt"><p><b>Embedded Netflix is not supported in KeplerV.</b></p><p>Netflix uses DRM that cannot play inside this app. No workaround is attempted. Use the supported option below.</p><button class="object-primary" onclick={openNetflixExternally}>{NETFLIX_FALLBACK_LABEL} ↗</button></div>
        <div class="media-frame-note"><span>NETFLIX · external browser only · embedded playback never claimed</span><span class="media-transport"><button onclick={() => closeMedia()}>Close</button></span></div>
      {/if}
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
        <div class="audio-particle-field" class:particles-active={intentStep === "listening"} aria-hidden="true"><div class="particle-grid"></div><div class="particle-orbit particle-orbit-one"></div><div class="particle-orbit particle-orbit-two"></div><div class="particle-orbit particle-orbit-three"></div>{#each audioParticles as particle}<i class={`audio-particle ${particle.tone}`} style={particleStyle(particle, audioPhase, micLevel)}></i>{/each}<div class="audio-core"><span class="audio-core-halo" style={`transform:scale(${1 + micLevel * 0.35});box-shadow:0 0 ${18 + micLevel * 55}px color-mix(in srgb,var(--accent),transparent ${94 - micLevel * 32}%)`}></span><span class="audio-core-center"></span><small>{intentStep === "listening" ? "LIVE VOICE" : intentStep === "unavailable" ? "NO INPUT" : presence === "speaking" ? "SPEAKING" : intentStep === "interpreting" ? "THINKING" : "KEPLERV"}</small></div></div>
        <div class="audio-level-wrap"><div class="audio-level-caption"><span>INPUT LEVEL <b>{intentStep === "listening" ? "LIVE" : "INACTIVE"}</b></span><span>{Math.round(micLevel * 100)}%</span></div><div class="audio-meter" role="meter" aria-label="Live microphone level" aria-valuenow={Math.round(micLevel * 100)} aria-valuemin="0" aria-valuemax="100">{#each Array.from({ length: 38 }) as _, index}<i style={meterStyle(index, micLevel, audioPhase)}></i>{/each}</div></div>
        <div class="audio-controls">
          {#if intentStep === "listening"}<button class="audio-primary" onclick={finishCapture}>Finish capture <span>■</span></button><button class="audio-cancel" onclick={cancelIntent}>Cancel</button>
          {:else if intentStep === "captured"}<label class="audio-select-label" for="example-intent">EXAMPLE TRANSCRIPT · FALLBACK WHEN LIVE TRANSCRIPTION IS UNAVAILABLE</label><select id="example-intent" bind:value={chosenIntent}>{#each intents as intent}<option value={intent}>{intent}</option>{/each}</select><button class="audio-primary" onclick={interpretIntent}>Continue with this intention <span>↗</span></button><button class="audio-cancel" onclick={cancelIntent}>Cancel</button>
          {:else if intentStep === "interpreting"}<div class="audio-working"><i></i><i></i><i></i><i></i><i></i><i></i><span>TRANSCRIBING · UNDERSTANDING REQUEST</span></div><button class="audio-cancel" onclick={cancelIntent}>Cancel</button>
          {:else if intentStep === "unavailable"}<p class="audio-error">{micError}</p><button class="audio-primary" onclick={openAudio}>Try microphone again <span>↗</span></button><button class="audio-primary" onclick={() => { intentStep = "captured"; presence = "resting"; }}>Use an example transcript instead</button><button class="audio-cancel" onclick={cancelIntent}>Close</button>
          {:else}<div class="audio-result"><span>YOUR INTENTION</span><p>“{voiceTranscript || chosenIntent}”</p><span>KEPLERV RESPONSE</span><p>{voiceResult || "Ready."}</p></div><button class="audio-primary" onclick={cancelIntent}>Done <span>↗</span></button><button class="audio-cancel" onclick={openAudio}>Speak again</button>{/if}
        </div>
      </div>
      <footer class="audio-footer"><span><i></i> MICROPHONE INPUT · LIVE META TRANSCRIPTION</span><span>EXAMPLES ARE A FALLBACK ONLY</span><span>NO AUDIO SAVED</span></footer>
    </div>
  {/if}

  {#if panel}<button class="scrim" aria-label="Dismiss contextual surface" onclick={closePanel}></button><div class="context-panel" role="dialog" aria-modal="true" tabindex="-1">
    <div class="panel-heading"><div><span class="panel-kicker">WORKSPACE</span><h2>{panel === "settings" ? "Accessibility & settings" : panel === "providers" ? "LLM providers" : panel === "timeline" ? "Recent activity" : panel === "create_goal" ? "New goal" : panel === "resource" ? "Add a link" : panel === "resource_detail" ? "Link details" : panel === "goal" ? "Goal details" : panel === "agent" ? "Helper details" : panel === "guard" ? "Guard cases" : "Approval needed"}</h2></div>{#if panel === "agent"}<button class="panel-back" onclick={closePanel}>← <span>Back to workspace</span></button>{:else}<button class="panel-close" aria-label="Close" onclick={closePanel}>×</button>{/if}</div>
    {#if panel === "settings"}<div class="settings-block"><p>Text size</p><span class="subtle">Choose a size that feels comfortable. It applies across KeplerV.</span><div class="segmented">{#each [{ id: "standard", label: "Standard" }, { id: "large", label: "Large" }, { id: "extra-large", label: "Extra large" }] as size}<button class:segment-active={textSize === size.id} aria-pressed={textSize === size.id} onclick={() => textSize = size.id as TextSize}>{size.label}</button>{/each}</div></div><div class="settings-block"><p>Simple workspace</p><span class="subtle">Show the main actions first. Turn this off to see extra details.</span><button class="accessibility-toggle" role="switch" aria-checked={simpleMode} onclick={() => simpleMode = !simpleMode}><span>{simpleMode ? "On" : "Off"}</span><b>{simpleMode ? "Fewer details" : "Show all details"}</b></button></div><div class="settings-block"><p>Movement</p><span class="subtle">Reduce animations across the workspace.</span><button class="accessibility-toggle" role="switch" aria-checked={reduceMotion} onclick={() => reduceMotion = !reduceMotion}><span>{reduceMotion ? "On" : "Off"}</span><b>Reduce motion</b></button></div><div class="settings-block"><p>Appearance</p><span class="subtle">Choose a light, dark, or automatic theme.</span><div class="segmented">{#each ["system", "light", "dark"] as theme}<button class:segment-active={state.theme === theme} aria-pressed={state.theme === theme} onclick={() => commit({ ...state, theme: theme as AppState["theme"] })}>{theme === "system" ? "Automatic" : theme[0].toUpperCase() + theme.slice(1)}</button>{/each}</div></div><div class="settings-block"><p>Spoken responses</p><span class="subtle">KeplerV speaks confirmations, approvals, and completion summaries using system voices.</span><button class="accessibility-toggle" role="switch" aria-checked={speechEnabled} onclick={() => { speechEnabled = !speechEnabled; voice().setEnabled(speechEnabled); }}><span>{speechEnabled ? "On" : "Off"}</span><b>Speak responses</b></button></div><div class="settings-block"><p>Workspace</p><span class="subtle">Goals and activity are saved on this device.</span><button class="secondary-wide" onclick={clearWorkspace}>Reset workspace</button></div>
    {:else if panel === "providers"}<div class="provider-panel-intro"><p>Connect an AI provider once. KeplerV keeps credentials in your device’s protected credential store.</p><small>Each provider needs its own approved connection method. Local models can connect without an account.</small></div>
      <section class="provider-connect-card"><div class="provider-card-top"><span class="provider-avatar">✦</span><div><b>Meta Muse</b><small>Used by KeplerV research agents</small></div><span class="connection-state">{metaConnected ? activeProvider === "meta" ? "CONNECTED · ACTIVE" : "CONNECTED" : "NOT CONNECTED"}</span></div>{#if metaConnected}<p class="provider-connect-note">Connected securely on this device.{activeProvider === "meta" ? " Research is using Meta Muse." : ""}</p><div class="provider-connect-actions">{#if activeProvider !== "meta"}<button class="provider-default" onclick={() => activateProviderConnection("meta")}>Use for research</button>{/if}<button class="provider-default" onclick={() => removeCredential("meta")}>Disconnect</button></div>{:else}<form class="provider-form" onsubmit={saveMetaCredential}><label>API KEY<input bind:value={metaApiKey} type="password" autocomplete="off" placeholder="Paste your Meta API key" required /></label><button class="object-primary" type="submit" disabled={savingCredential}>{savingCredential ? "Connecting…" : "Connect Meta Muse"}</button></form><small class="provider-security-note">The key is sent to KeplerV’s native runtime and saved in your system keychain. It is not stored with your workspace data.</small>{/if}</section>
      <section class="provider-connect-card"><div class="provider-card-top"><span class="provider-avatar">◉</span><div><b>ChatGPT</b><small>Sign in with your ChatGPT account</small></div><span class="connection-state">{connectingChatgpt ? "WAITING FOR SIGN-IN" : chatgptConnected ? chatgptSharing ? "PLAN CONNECTED" : "ACCOUNT CONNECTED" : "NOT CONNECTED"}</span></div>{#if connectingChatgpt}<div class="chatgpt-progress" role="status" aria-live="polite"><span class="chatgpt-progress-dot"></span><div><b>Finish sign-in in KeplerV</b><small>Your sign-in page is open in a KeplerV window. Approve access there; this workspace will finish connecting automatically.</small></div></div>{/if}{#if chatgptConnected}<p class="provider-connect-note">{chatgptEmail || "ChatGPT account"}{chatgptSharing ? " · ChatGPT plan usage is authorized." : " · Plan usage was not authorized."}</p><div class="provider-connect-actions">{#if chatgptSharing && activeProvider !== "chatgpt"}<button class="provider-default" onclick={() => activateProviderConnection("chatgpt")}>Use for research</button>{/if}<button class="provider-default" onclick={connectChatGPT} disabled={connectingChatgpt}>{connectingChatgpt ? "Opening sign-in…" : "Reconnect"}</button><button class="provider-default" onclick={disconnectChatGPT}>Disconnect</button></div>{:else}<p class="provider-connect-note">ChatGPT sign-in opens in a KeplerV window. Your login stays inside the app; KeplerV will confirm when account verification and setup are complete.</p><button class="object-primary" onclick={connectChatGPT} disabled={connectingChatgpt}>{connectingChatgpt ? "Waiting for ChatGPT…" : "Continue with ChatGPT"}</button>{/if}{#if chatgptConnectError}<p class="chatgpt-connect-error" role="alert">{chatgptConnectError}</p>{/if}<small class="provider-security-note">Tokens are stored in the system keychain. KeplerV requests access only to perform eligible model requests; it cannot read your ChatGPT conversations.</small></section>
      <section class="provider-connect-card"><div class="provider-card-top"><span class="provider-avatar">◇</span><div><b>Other model providers</b><small>OpenAI · Anthropic · Gemini · DeepSeek · Kimi · OpenRouter · Groq · Mistral</small></div><span class="connection-state">{activeProvider.toUpperCase()} · {activeModel}</span></div><form class="provider-form" onsubmit={connectConfiguredProvider}><label>PROVIDER<select value={connectProviderId} onchange={(event) => chooseConnectProvider((event.currentTarget as HTMLSelectElement).value)}><option value="openai">OpenAI API</option><option value="anthropic">Anthropic</option><option value="google">Google Gemini</option><option value="deepseek">DeepSeek</option><option value="kimi">Kimi / Moonshot</option><option value="openrouter">OpenRouter</option><option value="groq">Groq</option><option value="mistral">Mistral</option><option value="openai-compatible">Other OpenAI-compatible endpoint</option><option value="local-ollama">Ollama · Local</option><option value="local-lmstudio">LM Studio · Local</option><option value="local-openai-compatible">Other OpenAI-compatible local server</option></select></label><label>ENDPOINT<input bind:value={connectEndpoint} type="url" required /></label><label>MODEL<input bind:value={connectModel} placeholder="Leave blank to select the first available model" /></label>{#if !connectProviderId.startsWith("local-")}<label>API KEY<input bind:value={connectSecret} type="password" autocomplete="off" placeholder={connectedCredentials.includes(connectProviderId) ? "Leave blank to use the saved key" : "Provider API key"} required={!connectedCredentials.includes(connectProviderId)} /></label>{:else}<p class="provider-connect-note">Make sure this local model server is running. KeplerV checks the endpoint and model before saving.</p>{/if}<button class="object-primary" type="submit" disabled={connectingProvider}>{connectingProvider ? "Checking connection…" : "Check and connect"}</button></form><small class="provider-security-note">KeplerV checks that the endpoint is reachable and the model is available. API keys go to the system keychain; endpoint and model settings stay on this device.</small></section>
      <section class="provider-connect-card"><div class="provider-card-top"><span class="provider-avatar">✉</span><div><b>Connected services</b><small>Gmail · Outlook · Fake Mail test fixture</small></div><span class="connection-state">{connectedAccounts.length ? `${connectedAccounts.length} CONNECTED` : "NONE CONNECTED"}</span></div>
        {#if connectedAccounts.length}<div class="provider-saved-list">{#each connectedAccounts as account}<article><div><b>{account.email}</b><small>{account.provider} · {account.status.toUpperCase()} · {account.scopes.length} scope{account.scopes.length === 1 ? "" : "s"}</small></div><div><button class="provider-default" disabled={connectingService !== null} onclick={() => disconnectService(account)}>Disconnect</button></div></article>{/each}</div>
        {:else}<p class="provider-connect-note">No service accounts connected. Connect one to let research read mail with your approval.</p>{/if}
        <div class="provider-connect-actions"><button class="provider-default" disabled={connectingService !== null} onclick={() => connectService("gmail")}>{connectingService === "gmail" ? "Waiting for sign-in…" : "Connect Gmail"}</button><button class="provider-default" disabled={connectingService !== null} onclick={() => connectService("outlook")}>{connectingService === "outlook" ? "Waiting for sign-in…" : "Connect Outlook"}</button><button class="provider-default" disabled={connectingService !== null} onclick={() => connectService("fake-mail")}>{connectingService === "fake-mail" ? "Connecting…" : "Connect Fake Mail (test fixture)"}</button></div>
        {#if serviceError}<p class="chatgpt-connect-error" role="alert">{serviceError}</p>{/if}
        <small class="provider-security-note">OAuth tokens are stored in the system keychain, separate from model API keys. Fake Mail is a clearly-labeled local fixture — never live mail.</small></section>
      <section class="provider-connect-card"><div class="provider-card-top"><span class="provider-avatar">✉</span><div><b>Mail</b><small>Read connected accounts · sending needs approval</small></div><span class="connection-state">{mailLoading ? "LOADING" : mailMessages.length ? `${mailMessages.length} SHOWN` : "NOTHING LOADED"}</span></div>
        {#if connectedAccounts.length}
          <form class="provider-form" onsubmit={(event) => { event.preventDefault(); void loadMail(selectedMailAccount(), mailQuery); }}><label>ACCOUNT<select bind:value={mailAccountKey}><option value="">Pick an account…</option>{#each connectedAccounts as account}<option value={accountKey(account)}>{account.email} ({account.provider})</option>{/each}</select></label><label>SEARCH MAIL<input bind:value={mailQuery} placeholder="enrollment, atlas…" /></label><button class="object-primary" type="submit" disabled={mailLoading}>{mailLoading ? "Loading…" : "Load mail"}</button></form>
          {#if mailError}<p class="chatgpt-connect-error" role="alert">{mailError}</p>{/if}
          {#if mailMessages.length}<div class="provider-saved-list">{#each mailMessages as message}<article><div><b>{message.subject}</b><small>{message.from ?? ""}{message.date ? ` · ${message.date}` : ""}{message.fake ? " · FAKE FIXTURE" : ""}</small></div><div><button class="provider-default" onclick={() => { const account = selectedMailAccount(); if (account) void readMailMessage(account, message.id); }}>Read</button></div></article>{/each}</div>{/if}
          {#if mailSelected}<article class="mail-reading"><div class="inspector-block"><span>{mailSelected.fake ? (mailSelected.label ?? "FAKE FIXTURE") : `FROM ${mailSelected.from ?? "unknown"}`}</span><b>{mailSelected.subject}</b><small>{mailSelected.date ?? ""}</small><p>{mailSelected.body || mailSelected.snippet || "No preview available."}</p></div><button class="secondary-wide" onclick={() => mailSelected = null}>Close message</button></article>{/if}
        {:else}<p class="provider-connect-note">Connect an account above first. Nothing is shown without a connection.</p>{/if}
        {#if emailDraft}<article class="mail-draft"><div class="inspector-block"><span>UNSENT DRAFT</span><b>To: {emailDraft.to}</b><small>{emailDraft.subject || "(no subject)"}{emailDraft.accountEmail ? ` · via ${emailDraft.accountEmail}` : ""}</small><p>{emailDraft.body || "(empty body)"}</p></div><div class="provider-connect-actions"><button class="provider-default" onclick={() => { pendingEmailSend = emailDraft; panel = "approval"; }}>Request approval to send</button><button class="provider-default" onclick={() => emailDraft = null}>Discard draft</button></div><small class="provider-security-note">Drafts are never sent automatically. Sending needs your explicit approval, and the mail connector is read-only.</small></article>{/if}
        <small class="provider-security-note">Agent browsers run in isolated sessions with no shared cookies; your sign-in stays in the app window.</small></section>
      {#if connectedCredentials.some((provider) => !["meta", "chatgpt"].includes(provider))}<section class="provider-saved-list"><h3>Connected providers</h3>{#each connectedCredentials.filter((provider) => !["meta", "chatgpt"].includes(provider)) as provider}<article><div><b>{provider === "local-ollama" ? "Ollama" : provider === "local-lmstudio" ? "LM Studio" : provider === "local-openai-compatible" ? "Local model" : provider === "openai-compatible" ? "Custom OpenAI-compatible" : provider[0].toUpperCase() + provider.slice(1)}</b><small>{activeProvider === provider ? `Active · ${activeModel}` : "Connected on this device"}</small></div><div><button class="provider-default" disabled={activeProvider === provider} onclick={() => activateProviderConnection(provider)}>{activeProvider === provider ? "Active" : "Use for research"}</button><button class="provider-remove" aria-label={`Disconnect ${provider}`} onclick={() => removeCredential(provider)}>×</button></div></article>{/each}</section>{/if}
      <p class="provider-footnote">You can connect several providers here. The active provider and model are shown above; connecting another provider switches research to it.</p>
    {:else if panel === "create_goal"}<form class="context-form" onsubmit={submitGoal}><label>WHAT WOULD YOU LIKE TO DO?<textarea class="intent-entry" bind:value={newDescription} rows="12" placeholder="Describe what you want KeplerV to help with." required></textarea></label><button class="object-primary" type="submit">＋ Add goal</button><p class="subtle">Your goal appears on the graph. Open it to start web research with Muse.</p></form>
    {:else if panel === "goal" && activeGoal}<div class="goal-inspector"><div class="goal-inspector-state"><span>{activeGoal.status.toUpperCase()}</span><b>{activeGoal.progress}%</b></div><h3>{activeGoal.title}</h3><p>{activeGoal.description}</p><section class="runtime-research"><h3>Research with Muse</h3><p>KeplerV searches the public web from this goal, opens relevant pages in its research browser, and returns source-backed findings for you to review.</p><label for="runtime-token-budget">MAXIMUM TOKEN BUDGET<input id="runtime-token-budget" bind:value={runtimeTokenBudget} type="number" min="1000" max="50000" step="1000" /></label><small class="subtle">1,000–50,000 tokens per run. Higher limits can increase model usage and provider charges.</small><label for="runtime-source-url">START PAGE (OPTIONAL)<input id="runtime-source-url" bind:value={runtimeSourceUrl} type="url" placeholder="https://… or loopback demo fixture" /></label><small class="subtle">Leave blank to start from web search. Loopback fixture pages work only with demo mode enabled.</small>{#if runtimeBaseUrl}<button class="secondary-wide" onclick={() => { runtimeSourceUrl = `${runtimeBaseUrl}/demo/university/catalog`; showNotice("Start page set to the fictional university catalog."); }}>Use fictional demo fixtures</button>{/if}{#if !runtimeModelConfigured}{#if state.offlineMode}<small class="subtle">Offline mode — goals, tasks, and local helpers stay available. Research resumes when Muse connects.</small><button class="secondary-wide" onclick={() => { commit(setOfflineMode(state, false)); showNotice("Online research re-enabled."); }}>Exit offline mode</button>{:else}<small class="runtime-key-help">Connect Meta Muse in LLM providers to use research.</small><button class="secondary-wide" onclick={() => { commit(setOfflineMode(state, true)); showNotice("Offline workspace active. Nothing here will be presented as live research."); }}>Continue offline</button>{/if}{/if}{#if runtimeError}<small class="runtime-error">{runtimeError}</small>{/if}<button class="object-primary" disabled={runtimeStatus !== "ready" || !runtimeModelConfigured || startingRuntimeRun || !Number.isInteger(runtimeTokenBudget) || runtimeTokenBudget < 1000 || runtimeTokenBudget > 50000} onclick={startMuseResearch}>{startingRuntimeRun ? "Starting Muse…" : runtimeStatus !== "ready" ? "Runtime unavailable" : runtimeModelConfigured ? "Search the web with Muse" : "Connect Muse first"}</button></section>{#if activeRuntimeAgents.length}<div class="runtime-agent-list"><div class="task-heading"><h3>Muse agents</h3><span>{activeRuntimeAgents.length}</span></div>{#each activeRuntimeAgents as runtimeAgent}<button class="runtime-agent-row" onclick={() => { selected = { kind: "agent", id: runtimeAgent.id }; panel = "agent"; }}><span class:runtime-failed={runtimeAgent.status === "failed"} class:runtime-done={runtimeAgent.status === "completed"}>{runtimeAgent.status === "completed" ? "✓" : runtimeAgent.status === "failed" ? "!" : "◌"}</span><span><b>{runtimeAgent.name}</b><small>{runtimeAgent.status.toUpperCase()} · {runtimeAgent.steps} steps · {runtimeAgent.model}</small></span><i>Open ›</i></button>{/each}</div>{/if}{#if activePendingApprovals.length}<div class="runtime-approval-list"><h3>Review requested actions</h3>{#each activePendingApprovals as approval}<article><b>{approval.tool}</b><small>The agent is paused until you approve or deny this page action.</small><div><button onclick={() => resolveRuntimeApproval(approval, "approve")}>Approve once</button><button onclick={() => resolveRuntimeApproval(approval, "deny")}>Deny</button></div></article>{/each}</div>{/if}{#if activeEvidence.length}<div class="runtime-evidence"><div class="task-heading"><h3>Research findings</h3><span>{activeEvidence.length}</span></div>{#each activeEvidence as item}<article><b>{item.finding}</b><blockquote>“{item.quote}”</blockquote><button onclick={() => openEvidenceSource(item)}>Open source ↗</button></article>{/each}</div>{/if}{#if isUbResearchIntent(`${activeGoal.title} ${activeGoal.description}`)}<div class="intent-next-step"><span>{completedPlanGoalIds.includes(activeGoal.id) ? "RESEARCH PLAN READY" : "RESEARCH PLAN STARTS AUTOMATICALLY"}</span>{#if completedPlanGoalIds.includes(activeGoal.id)}<p>This intention has already gone through the planning steps. Open its schedule draft without running them again.</p><button class="object-primary" onclick={() => openSchedulePreview(activeGoal.id)}>Open schedule PDF <span>↗</span></button>{:else}<p>Adding this intention starts the planning steps automatically. You won’t need to run them from here.</p>{/if}</div>{/if}<div class="inspector-block technical-detail"><span>DEPENDENCY CHAIN</span><b>Intent → tasks → agent work → human review</b><small>Each task below belongs to this goal. Delegated agents inherit the goal boundary.</small></div><div class="task-list"><div class="task-heading"><h3>Related tasks</h3><span>{activeGoal.tasks.length.toString().padStart(2, "0")}</span></div>{#each activeGoal.tasks as task, index}<div class="task-row"><span>{String(index + 1).padStart(2, "0")}</span><b>{task}</b><i>{goalTaskStatus(activeGoal, index)}</i></div>{/each}</div>{#if activeGoal.criteria?.length}<div class="task-list"><div class="task-heading"><h3>Completion criteria</h3><span>{activeGoal.criteria.length.toString().padStart(2, "0")}</span></div>{#each activeGoal.criteria as criterion}<div class="task-row"><span>✓</span><b>{criterion}</b></div>{/each}</div>{/if}{#if activeGoal.constraints?.length}<div class="task-list"><div class="task-heading"><h3>Constraints</h3><span>{activeGoal.constraints.length.toString().padStart(2, "0")}</span></div>{#each activeGoal.constraints as constraint}<div class="task-row"><span>!</span><b>{constraint}</b></div>{/each}</div>{/if}<form class="context-form task-form" onsubmit={addGoalTask}><label>ADD A RELATED TASK<input bind:value={newTask} maxlength="90" placeholder="A concrete next step" required /></label><button class="secondary-wide" type="submit">＋ Add task</button></form><button class="object-primary" onclick={assignAgent}>Assign agent</button><div class="inspector-block goal-history"><span>RECENT ACTIVITY FOR THIS GOAL</span>{#each state.events.filter((event) => event.goalId === activeGoal.id).slice(0, 5) as event}<small>{new Date(event.timestamp).toLocaleTimeString()} · {eventLabel(event)}</small>{/each}</div></div>
    {:else if panel === "agent" && activeAgent}<div class="inspector-status"><span class="activity-dot" class:dot-paused={activeAgent.status !== "working"}></span>{activeAgent.status.toUpperCase()} <span>·</span> {activeAgent.provider}</div><div class="inspector-block"><span>ASSIGNED GOAL</span><b>{goals.find((goal) => goal.id === activeAgent.goalId)?.title}</b></div><div class="inspector-block technical-detail"><span>AGENT PROFILE</span><b>{activeAgent.model}</b><small>{activeAgent.runtime ? "Live runtime agent" : "Not assigned to a live provider"}</small></div><div class="inspector-block"><span>CURRENT TASK</span><b>{activeAgent.currentTask ?? "No active task"}</b></div>{#if activeAgent.runtime && activeAgent.error}<p class="runtime-error">{activeAgent.error}</p>{/if}<div class="inspector-block technical-detail"><span>PERMISSION SCOPE</span><div class="permission-list">{#each activeAgent.permissions as permission}<code>{permission}</code>{/each}</div></div><div class="inspector-block technical-detail"><span>DELEGATION</span><b>{activeAgent.parentAgentId ? `Child of ${agents.find((agent) => agent.id === activeAgent.parentAgentId)?.name ?? "agent"}` : "Root agent"}</b><small>At most 2 children per agent; depth capped at 2. Child scope cannot exceed parent scope.</small></div>{#if activeAgent.runtime}<div class="panel-actions">{#if activeAgent.status === "working"}<button class="secondary-wide" onclick={() => controlRuntimeAgent(activeAgent, "pause")}>Pause at next safe step</button>{:else if activeAgent.status === "paused"}<button class="secondary-wide" onclick={() => controlRuntimeAgent(activeAgent, "resume")}>Resume agent</button>{:else if activeAgent.status === "failed"}<button class="secondary-wide" onclick={startMuseResearch}>Start a fresh research run</button>{/if}{#if !["completed", "failed", "stopped"].includes(activeAgent.status)}<button class="danger-button" onclick={() => controlRuntimeAgent(activeAgent, "stop")}>Stop agent</button>{/if}<p class="subtle">Runtime ID: {activeAgent.runId}</p></div>{:else}<div class="panel-actions"><button class="secondary-wide" onclick={() => toggleAgent(activeAgent)}>{activeAgent.status === "working" ? "Pause agent" : "Resume agent"}</button><button class="secondary-wide advanced-control" onclick={() => spawn(activeAgent)} disabled={!canSpawn(state, activeAgent)}>Add restricted subagent</button><button class="secondary-wide" onclick={() => commit(transitionAgent(state, activeAgent.id, "complete"))}>Complete task</button><button class="secondary-wide advanced-control" onclick={() => commit(transitionAgent(state, activeAgent.id, "fail"))}>Fail task</button><button class="secondary-wide advanced-control" onclick={() => evaluateExample(activeAgent)}>Review access rules</button><button class="secondary-wide advanced-control" onclick={() => requestPayment(activeAgent)}>Request payment</button></div>{/if}
    {:else if panel === "guard"}
      <div class="inspector-block"><span>RUNTIME INVESTIGATIONS</span><b>Cases are created when AIP blocks an agent action.</b><p>Evidence, model identity, and status are retained with the runtime workspace.</p></div>
      {#if runtimeGuardCases.length}
        <div class="guard-case-list">{#each runtimeGuardCases as guardCase}<button class="guard-case-row" class:guard-case-selected={activeGuardCase?.id === guardCase.id} onclick={() => selectedGuardCaseId = guardCase.id}><span class="guard-case-severity">{guardCase.severity.toUpperCase()}</span><b>{guardCase.goal_title}</b><small>{guardCase.signal.replaceAll("_", " ")} · {guardCase.status.replaceAll("_", " ")}</small><p>{guardCase.summary}</p></button>{/each}</div>
        {#if activeGuardCase}
          <div class="inspector-block"><span>INVESTIGATION SOURCE</span><b>{activeGuardCase.provider} · {activeGuardCase.model}</b><p>Agent {activeGuardCase.agent_id} · run {activeGuardCase.run_id}</p><p>Evidence integrity: {activeGuardCase.evidence.length ? activeGuardCase.evidence[activeGuardCase.evidence.length - 1].integrity_hash.slice(0, 18) : "No evidence recorded"}…</p></div>
          <div class="guard-investigation-action"><button class="object-primary" onclick={() => startGuardInvestigation(activeGuardCase!.id)} disabled={Boolean(investigatingCaseId || interviewingCaseId || adjudicatingCaseId) || activeGuardCase.status === "closed"}>{investigatingCaseId === activeGuardCase.id ? "Detective reviewing evidence…" : activeGuardCase.status === "awaiting_human_review" ? "Run another Detective review" : "Start Detective investigation"}</button><small>Uses {activeGuardCase.provider === "meta" ? "Muse Wonder" : activeGuardCase.provider} and may incur provider charges. The report will remain evidence-cited and subject to your review.</small>
          {#if activeGuardCase.evidence.filter((item) => item.source_type === "guard_interview_turn").length < 5 && activeGuardCase.status !== "closed" && activeGuardCase.status !== "inconclusive"}<button class="secondary-wide" onclick={() => startGuardInterview(activeGuardCase!.id)} disabled={Boolean(interviewingCaseId || investigatingCaseId || adjudicatingCaseId)}>{interviewingCaseId === activeGuardCase.id ? "Wonder is choosing a question; the agent is responding…" : `Ask the agent an adaptive question · ${activeGuardCase.evidence.filter((item) => item.source_type === "guard_interview_turn").length}/5`}</button>{/if}
          <small>Each turn uses Wonder to choose a question and the original agent model to answer. Agent answers are self-reports, not proof; important claims must match the independent event record. Up to five questions per case.</small>{#if guardInvestigationError && selectedGuardCaseId === activeGuardCase.id}<p class="runtime-error">{guardInvestigationError}</p>{/if}</div>
          {#if activeGuardCase.status !== "closed" && activeGuardCase.status !== "inconclusive" && activeGuardCase.status !== "containment_requested"}<button class="secondary-wide" onclick={() => startGuardAdjudication(activeGuardCase!.id)} disabled={Boolean(interviewingCaseId || investigatingCaseId || adjudicatingCaseId)}>{adjudicatingCaseId === activeGuardCase.id ? "Independent Adjudicator reviewing primary evidence…" : "Request independent Adjudicator review"}</button><small>Uses {activeGuardCase.provider === "meta" ? "Muse Wonder" : activeGuardCase.provider}; prior model reports are excluded. Its assessment is advisory and cannot change access policy.</small>{/if}
          {#if activeGuardCase.status === "awaiting_human_review" && activeGuardAgent && !["completed", "failed", "stopped"].includes(activeGuardAgent.status)}<div class="inspector-block"><span>USER-AUTHORIZED CONTAINMENT</span><b>AIP policy stays unchanged. Containment only narrows this agent’s run.</b><p>Containment is applied by KeplerV’s trusted runtime only after you confirm below.</p>{#if activeGuardAgent.status === "paused"}<button class="secondary-wide" onclick={() => applyGuardContainment(activeGuardCase!.id, "resume_agent")}>Resume affected agent</button>{:else}<button class="danger-button" onclick={() => applyGuardContainment(activeGuardCase!.id, "pause_agent")}>Pause affected agent</button>{/if}{#if pendingRuntimeApprovals.some((item) => item.runId === activeGuardAgent.runId)}<button class="danger-button" onclick={() => applyGuardContainment(activeGuardCase!.id, "deny_pending")}>Deny pending actions</button>{/if}{#if activeGuardAgent.browserIsolated}<button class="secondary-wide" onclick={() => applyGuardContainment(activeGuardCase!.id, "restore_browser")}>Restore browser session</button>{:else}<button class="secondary-wide" onclick={() => applyGuardContainment(activeGuardCase!.id, "isolate_browser")}>Isolate browser session</button>{/if}{#if activeGuardAgent.permissions.includes("browser.interact")}<button class="secondary-wide" onclick={() => applyGuardContainment(activeGuardCase!.id, "revoke_capability", "browser.interact")}>Revoke page interaction</button>{/if}{#if activeGuardAgent.permissions.includes("delegate")}<button class="secondary-wide" onclick={() => applyGuardContainment(activeGuardCase!.id, "revoke_capability", "delegate")}>Revoke delegation</button>{/if}</div>{/if}
          <div class="evidence-list"><h3>Case record</h3>{#each activeGuardCase.evidence as evidence, i}<article><b>{String(i + 1).padStart(2, "0")} · {evidence.source_type.replaceAll("_", " ")}</b>{#if evidence.source_type === "guard_screening"}<p><strong>Muse Spark · qualitative risk: {String((evidence.payload.screening as Record<string, unknown>)?.risk_level ?? "not available")}</strong></p><p>{String((evidence.payload.screening as Record<string, unknown>)?.summary ?? "")}</p><p>{String((evidence.payload.screening as Record<string, unknown>)?.rationale ?? "")}</p><small>Sources: {((evidence.payload.screening as Record<string, unknown>)?.evidence_ids as string[] ?? []).join(", ")} · Not a probability or enforcement decision</small>{#each (((evidence.payload.screening as Record<string, unknown>)?.uncertainties as string[]) ?? []) as uncertainty}<p>Uncertainty: {uncertainty}</p>{/each}{:else if evidence.source_type === "guard_screening_error"}<p>Muse Spark screening error: {String(evidence.payload.error ?? "The model request failed")}</p>{:else if evidence.payload.report}<p>{(evidence.payload.report as RuntimeDetectiveReport).summary}</p><small>Assessment: {(evidence.payload.report as RuntimeDetectiveReport).assessment.replaceAll("_", " ")} · {(evidence.payload.provider as string)} · {(evidence.payload.model as string)}</small>{#each (evidence.payload.report as RuntimeDetectiveReport).findings as finding}<p><strong>{finding.classification.replaceAll("_", " ")}</strong> · {finding.claim}</p><small>Sources: {finding.evidence_ids.join(", ")}</small>{/each}{#if (evidence.payload.report as RuntimeDetectiveReport).uncertainties.length}<p>Uncertainty: {(evidence.payload.report as RuntimeDetectiveReport).uncertainties.join(" · ")}</p>{/if}<p>Next step: {(evidence.payload.report as RuntimeDetectiveReport).recommended_next_step}</p>{:else if evidence.source_type === "guard_adjudication"}<p><strong>Disposition: {String((evidence.payload.report as Record<string, unknown>)?.disposition ?? "review unavailable").replaceAll("_", " ")}</strong></p><p>{String((evidence.payload.report as Record<string, unknown>)?.summary ?? "")}</p>{#each (((evidence.payload.report as Record<string, unknown>)?.findings as Array<Record<string, unknown>>) ?? []) as finding}<p>{String(finding.claim ?? "")}</p><small>Evidence: {((finding.evidence_ids as string[]) ?? []).join(", ")}</small>{/each}{#each (((evidence.payload.report as Record<string, unknown>)?.contradictions as Array<Record<string, unknown>>) ?? []) as contradiction}<p><strong>Possible contradiction</strong></p><p>Agent statement: {String(contradiction.agent_statement ?? "")}</p><p>Recorded event: {String(contradiction.observed_record ?? "")}</p><small>Evidence: {((contradiction.evidence_ids as string[]) ?? []).join(", ")}</small>{/each}<p>{String((evidence.payload.report as Record<string, unknown>)?.recommended_response ?? "")}</p><small>Advisory only · {String(evidence.payload.provider)} · {String(evidence.payload.model)} · Prior model reports excluded</small>{:else if evidence.source_type === "guard_interview_turn"}<p><strong>Question {String(evidence.payload.turn)}:</strong> {String(evidence.payload.question ?? "Question pending")}</p><small>{String(evidence.payload.interrogator_provider)} · {String(evidence.payload.interrogator_model)} · Cites: {((evidence.payload.evidence_ids as string[]) ?? []).join(", ")}</small><p>{String(evidence.payload.rationale ?? "")}</p>{:else if evidence.source_type === "guard_interview_answer"}<p><strong>Agent response:</strong> {String(evidence.payload.answer ?? "No answer recorded")}</p><small>{String(evidence.payload.suspect_provider)} · {String(evidence.payload.suspect_model)} · Self-report, independently unverified</small>{:else if evidence.source_type === "guard_containment_action"}<p><strong>{String(evidence.payload.action).replaceAll("_", " ")}</strong> · agent status: {String(evidence.payload.run_status)}</p><p>{String(evidence.payload.reason)}</p><small>Authorized by {String(evidence.payload.authorized_by)} · AIP policy unchanged</small>{:else if evidence.source_type === "guard_interview_error"}<p>Interview error: {String(evidence.payload.error ?? "The model request failed")}</p>{:else}<p>{String(evidence.payload.reason ?? evidence.payload.summary ?? evidence.payload.action ?? evidence.payload.type ?? "Runtime policy event")}</p>{/if}<small>Evidence ID {evidence.id} · {new Date(evidence.timestamp).toLocaleString()}</small></article>{/each}</div>
        {/if}
      {:else}<div class="empty-state"><span>✓</span><h3>No Guard cases</h3><p>When AIP blocks an agent action, its evidence will appear here for review.</p></div>{/if}
    {:else if panel === "timeline"}<div class="timeline-summary"><span>{realActivityEvents.length} RECORDED EVENTS</span><b>Agent activity</b><small>Events are recorded by the local runtime as agents work.</small></div>{#if realActivityEvents.length}<div class="timeline-list">{#each realActivityEvents as event}<article class="timeline-event"><span class="timeline-marker"></span><div><b>{eventLabel(event)}</b><small>{eventTypeLabel(event)} · {agents.find((agent) => agent.id === event.agentId)?.name ?? "KeplerV"}</small><time>{new Date(event.timestamp).toLocaleString()}</time></div></article>{/each}</div>{:else}<p class="subtle">No agent activity yet. Start research from a goal to see its events here.</p>{/if}
    {:else if panel === "resource"}<form class="context-form" onsubmit={submitResource}><p class="subtle">The link opens inside KeplerV and stays connected to this goal. Some websites block embedded viewing; you can open supported links separately.</p><label>LINK NAME<input bind:value={resourceLabel} maxlength="80" /></label><label>CHOOSE A LINK<select bind:value={resourceUrl}>{#each demoResources as resource}<option value={resource.url}>{resource.label}</option>{/each}</select></label><label>OR ENTER A PUBLIC HTTPS ADDRESS<input bind:value={resourceUrl} type="url" placeholder="https://www.nps.gov/…" /></label><button class="object-primary" type="submit">Open inside KeplerV ↗</button></form>
    {:else if panel === "resource_detail" && activeResource}<div class="resource-inspector"><span class="resource-inspector-icon">↗</span><h3>{activeResource.label}</h3><p>This link is associated with <b>{goals.find((goal) => goal.id === activeResource.goalId)?.title}</b>.</p><div class="inspector-block"><span>LINK ADDRESS</span><b>{new URL(activeResource.url).hostname}</b><small>{activeResource.url}</small></div><button class="object-primary" onclick={() => { panel = null; resourceView = true; }}>Open inside KeplerV ↗</button><button class="secondary-wide" onclick={() => invoke("open_public_resource", { url: activeResource.url, label: activeResource.label }).then(() => showNotice("Opened link in a separate window.")).catch((error) => showNotice(String(error)))}>Open separately</button><button class="secondary-wide" onclick={() => panel = null}>Close details</button></div>
    {:else if panel === "approval" && (pendingEmailSend || state.approval)}{#if pendingEmailSend}<div class="approval-card"><span>HUMAN APPROVAL REQUIRED</span><h3>Email send request</h3><p>KeplerV prepared an email draft. Review it before recording an approval decision. Nothing is sent automatically.</p><div class="approval-detail"><span>TO</span><b>{pendingEmailSend.to}</b></div><div class="approval-detail"><span>SUBJECT</span><b>{pendingEmailSend.subject || "(no subject)"}</b></div><div class="approval-detail"><span>VIA</span><b>{pendingEmailSend.accountEmail ?? "default account"}</b></div><div class="approval-detail"><span>BODY</span><b>{pendingEmailSend.body.slice(0, 280) || "(empty body)"}</b></div><div class="approval-detail"><span>AIP</span><b>REQUIRE APPROVAL</b></div><div class="panel-actions approval-actions"><button class="object-primary" onclick={() => decideEmailSend(true)}>Approve request</button><button class="danger-button" onclick={() => decideEmailSend(false)}>Deny request</button></div><p class="subtle">Approval records a decision in KeplerV; the mail connector is read-only, so no email is transmitted.</p></div>{:else if state.approval}<div class="approval-card"><span>HUMAN APPROVAL REQUIRED</span><h3>Tuition payment request</h3><p>A finance agent requested a tuition transfer. Review this request before recording an approval decision.</p><div class="approval-amount">$750 <small>REQUEST AMOUNT</small></div><div class="approval-detail"><span>AGENT</span><b>{agents.find((agent) => agent.id === state.approval?.agentId)?.name}</b></div><div class="approval-detail"><span>AIP</span><b>REQUIRE APPROVAL</b></div><div class="approval-detail"><span>REQUEST</span><b>{state.approval.action}</b></div><div class="panel-actions approval-actions"><button class="object-primary" onclick={() => decideApproval(true)} disabled={state.approval.status !== "pending"}>Approve request</button><button class="danger-button" onclick={() => decideApproval(false)} disabled={state.approval.status !== "pending"}>Deny request</button><button class="secondary-wide" onclick={() => { const agent = agents.find((item) => item.id === state.approval?.agentId); if (agent) commit(transitionAgent(state, agent.id, "pause")); closePanel(); }}>Pause agent</button></div><p class="subtle">Approval records a decision in KeplerV; no payment is sent.</p></div>{/if}{/if}
  </div>{/if}

  {#if notice}<div class="toast" role="status">{notice}<button aria-label="Dismiss notification" onclick={() => notice = ""}>×</button></div>{/if}
</main>
