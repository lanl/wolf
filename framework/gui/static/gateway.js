
(() => {
  "use strict";

  const STORAGE_KEY = "wolfGatewayStateV3";
  const PRESET_STORAGE_KEY = "wolf.gateway.agentPresets.v1";
  const PRESET_SELECTED_KEY = "wolf.gateway.selectedAgentPreset.v1";
  const $ = (id) => document.getElementById(id);

  const els = {
    openButtons: () => Array.from(document.querySelectorAll("#wolfGatewayOpenButton, [data-wolf-gateway-open]")),
    overlay: $("wolfGatewayOverlay"),
    close: $("wolfGatewayCloseButton"),
    form: $("wolfGatewayForm"),
    url: $("wolfGatewayUrl"),
    token: $("wolfGatewayToken"),
    username: $("wolfGatewayUsername"),
    password: $("wolfGatewayPassword"),
    remember: $("wolfGatewayRemember"),
    feedback: $("wolfGatewayFeedback"),
    local: $("wolfGatewayLocalButton"),
    submit: $("wolfGatewaySubmitButton"),
    authenticate: $("wolfGatewayAuthenticate"),
    connectSessionButton: $("wolfGatewayConnectSession"),
    statusDot: $("wolfGatewayStatusDot"),
    statusText: $("wolfGatewayStatusText"),
    authSummary: $("wolfGatewayAuthSummary"),
    sessionStep: $("wolfGatewaySessionStep"),
    sessionSelect: $("wolfGatewaySessionSelect"),
    refreshSessions: $("wolfGatewayRefreshSessions"),
    createSession: $("wolfGatewayCreateSession"),
    showParams: $("wolfGatewayShowParams"),
    saveParams: $("wolfGatewaySaveParams"),
    loadPresets: $("wolfGatewayLoadPresets"),
    applyPreset: $("wolfGatewayApplyPreset"),
    applyCommitPreset: $("wolfGatewayApplyCommitPreset"),
    agentPresetSelect: $("wolfGatewayAgentPresetSelect"),
    agentPresetSummary: $("wolfGatewayPresetSummary"),
    showPolicy: $("wolfGatewayShowPolicy"),
    savePolicy: $("wolfGatewaySavePolicy"),
    resetSession: $("wolfGatewayResetSession"),
    paramsEditor: $("wolfGatewayParamsEditor"),
    policyEditor: $("wolfGatewayPolicyEditor"),
    cfgModel: $("wolfCfgModel"),
    cfgHostAddress: $("wolfCfgHostAddress"),
    cfgHostPort: $("wolfCfgHostPort"),
    cfgApiVersion: $("wolfCfgApiVersion"),
    cfgApiKey: $("wolfCfgApiKey"),
    cfgApiKeyVar: $("wolfCfgApiKeyVar"),
    cfgAgentName: $("wolfCfgAgentName"),
    cfgVerbose: $("wolfCfgVerbose"),
    cfgMode: $("wolfCfgMode"),
    cfgMaxSteps: $("wolfCfgMaxSteps"),
    cfgOrchestrationEnabled: $("wolfCfgOrchestrationEnabled"),
    cfgOrchestrationWorkerCount: $("wolfCfgOrchestrationWorkerCount"),
    cfgOrchestrationMaxActiveTasks: $("wolfCfgOrchestrationMaxActiveTasks"),
    cfgOrchestrationMaxTotalTasks: $("wolfCfgOrchestrationMaxTotalTasks"),
    cfgGuiCommandTimeout: $("wolfCfgGuiCommandTimeout"),
    cfgCtxWindow: $("wolfCfgCtxWindow"),
    cfgActionPolicy: $("wolfCfgActionPolicy"),
    cfgEnableWrite: $("wolfCfgEnableWrite"),
    cfgEnableSyscall: $("wolfCfgEnableSyscall"),
    cfgEnableGuiCapture: $("wolfCfgEnableGuiCapture"),
    cfgSyscallShell: $("wolfCfgSyscallShell"),
    cfgSyscallTimeout: $("wolfCfgSyscallTimeout"),
    cfgCapabilities: $("wolfCfgCapabilities"),
    cfgActionNames: $("wolfCfgActionNames"),
    cfgSyscallAllow: $("wolfCfgSyscallAllow"),
    cfgSysPrompt: $("wolfCfgSysPrompt"),
    messageForm: $("message-form"),
    messageInput: $("message-input"),
    includeVisualContext: $("include-visual-context"),
    allowAgentInspect: $("allow-agent-inspect"),
    allowAgentCapture: $("allow-agent-capture"),
    agentRunStatus: $("agent-run-status"),
    agentPauseRun: $("agent-pause-run"),
    agentResumeRun: $("agent-resume-run"),
    agentStopRun: $("agent-stop-run"),
  };

  if (!els.overlay || !els.form) {
    console.warn("[wolf-gateway-ui] Gateway modal not found; standalone gateway script inactive.");
    return;
  }

  let ws = null;
  let intentionalClose = false;
  const defaultState = {
    phase: "local", // local | authenticated | connecting | connected | error
    gatewayUrl: "http://127.0.0.1:8000",
    token: "",
    accountId: "",
    sessionId: "",
    sessions: [],
    username: "",
    participantId: "gui",
    lastError: "",
    runStatus: "idle",
    runId: "",
    pauseRequested: false,
    stopRequested: false,
    reassessRequested: false,
    pendingUserMessageCount: 0,
    runStep: 0,
  };
  let state = loadState();
  let currentParams = {};
  let currentPolicy = {};

  function loadState() {
    try { return { ...defaultState, ...(JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}) }; }
    catch { return { ...defaultState }; }
  }

  function saveState() {
    const copy = { ...state };
    if (!els.remember?.checked) {
      copy.token = "";
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(copy));
  }

  function normaliseUrl(value) {
    return String(value || "").trim().replace(/\/+$/, "");
  }

  function uuid() {
    if (crypto && crypto.randomUUID) return crypto.randomUUID();
    return `gui_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
  }

  function isAuthed() {
    return Boolean(state.gatewayUrl && state.token && state.accountId);
  }

  function isConnected() {
    return state.phase === "connected" && Boolean(state.sessionId) && ws && ws.readyState === WebSocket.OPEN;
  }

  function setDisabled(el, disabled) {
    if (el) el.disabled = Boolean(disabled);
  }

  function clearAuth(reason = "Gateway authentication expired. Please authenticate again.") {
    intentionalClose = true;
    try { ws?.close(); } catch (_) {}
    ws = null;
    state = { ...state, token: "", accountId: "", sessionId: "", sessions: [], phase: "error", lastError: reason };
    saveState();
    render();
    if (els.feedback) els.feedback.textContent = reason;
  }

  function formControls() {
    return [
      els.cfgModel, els.cfgHostAddress, els.cfgHostPort, els.cfgApiVersion, els.cfgApiKey,
      els.cfgApiKeyVar, els.cfgAgentName, els.cfgVerbose, els.cfgMode, els.cfgMaxSteps,
      els.cfgOrchestrationEnabled, els.cfgOrchestrationWorkerCount,
      els.cfgOrchestrationMaxActiveTasks, els.cfgOrchestrationMaxTotalTasks,
      els.cfgGuiCommandTimeout, els.cfgCtxWindow, els.cfgActionPolicy, els.cfgEnableWrite, els.cfgEnableSyscall,
      els.cfgEnableGuiCapture,
      els.cfgSyscallShell, els.cfgSyscallTimeout, els.cfgCapabilities, els.cfgActionNames,
      els.cfgSyscallAllow, els.cfgSysPrompt,
    ].filter(Boolean);
  }

  function parseCsv(value) {
    const items = String(value || "").split(",").map((x) => x.trim()).filter(Boolean);
    return items.length ? items : null;
  }

  function csv(value) {
    return Array.isArray(value) ? value.join(", ") : (value == null ? "" : String(value));
  }

  function setValue(el, value) {
    if (!el) return;
    if (el.type === "checkbox") el.checked = Boolean(value);
    else el.value = value == null ? "" : String(value);
  }

  function numberOrNull(value) {
    const text = String(value ?? "").trim();
    if (!text) return null;
    const n = Number(text);
    return Number.isFinite(n) ? n : null;
  }

  function isRedactedSecret(key, value) {
    const k = String(key || "").toLowerCase();
    const v = String(value ?? "");
    if (k === "api_key_var") return false;
    return /(api_key|token|password|secret|authorization)/.test(k) && /redacted|\*\*\*/i.test(v);
  }

  function sanitizeRedacted(value) {
    if (Array.isArray(value)) return value.map(sanitizeRedacted);
    if (!value || typeof value !== "object") return value;
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      if (isRedactedSecret(k, v)) continue;
      out[k] = sanitizeRedacted(v);
    }
    return out;
  }

  function readRawParams() {
    try { return sanitizeRedacted(JSON.parse(els.paramsEditor?.value || "{}")); }
    catch { return {}; }
  }

  function applyParamsToForm(params = {}) {
    currentParams = { ...(params || {}) };
    setValue(els.cfgModel, params.model || "");
    setValue(els.cfgHostAddress, params.host_address || "");
    setValue(els.cfgHostPort, params.host_port ?? "");
    setValue(els.cfgApiVersion, params.api_version || "");
    setValue(els.cfgApiKey, "");
    setValue(els.cfgApiKeyVar, params.api_key_var || "");
    setValue(els.cfgAgentName, params.agent_name || "");
    setValue(els.cfgVerbose, params.verbose ?? "");
    setValue(els.cfgMode, params.mode || "single_step");
    setValue(els.cfgMaxSteps, params.max_steps ?? 1);
    setValue(els.cfgOrchestrationEnabled, params.orchestration_enabled || false);
    setValue(els.cfgOrchestrationWorkerCount, params.orchestration_worker_count ?? 1);
    setValue(els.cfgOrchestrationMaxActiveTasks, params.orchestration_max_active_tasks ?? 4);
    setValue(els.cfgOrchestrationMaxTotalTasks, params.orchestration_max_total_tasks ?? 128);
    setValue(els.cfgGuiCommandTimeout, params.gui_command_timeout_seconds ?? 60);
    setValue(els.cfgCtxWindow, params.ctx_window_length ?? "");
    setValue(els.cfgCapabilities, csv(params.capabilities));
    setValue(els.cfgSysPrompt, params.sys_prompt || "");
  }

  const POLICY_PARAM_KEYS = new Set([
    "action_policy",
    "enable_write",
    "enable_syscall",
    "enable_gui_capture",
    "syscall_allow_shell",
    "syscall_max_timeout",
    "action_names",
    "syscall_allowed_commands"
  ]);

  function filterOutPolicyParams(params = {}) {
    const out = {};
    Object.entries(params || {}).forEach(([key, value]) => {
      if (!POLICY_PARAM_KEYS.has(key)) out[key] = value;
    });
    return out;
  }

  function filterPolicyParams(params = {}) {
    const out = {};
    Object.entries(params || {}).forEach(([key, value]) => {
      if (POLICY_PARAM_KEYS.has(key)) out[key] = value;
    });
    return out;
  }

  function extractPolicyParams(policy = {}) {
    const source = { ...(policy.params || {}), ...(policy.policy || {}), ...policy };
    return filterPolicyParams(source);
  }

  function readRawAgentParams() {
    return filterOutPolicyParams(readRawParams());
  }

  function readRawPolicyParams() {
    if (!els.policyEditor || !els.policyEditor.value.trim()) return {};
    try {
      const parsed = JSON.parse(els.policyEditor.value);
      return filterPolicyParams(parsed && typeof parsed === "object" ? parsed : {});
    } catch (error) {
      throw new Error(`Policy JSON is invalid: ${error.message}`);
    }
  }

  function formToParams() {
    const out = {};
    if (els.cfgModel?.value.trim()) out.model = els.cfgModel.value.trim();
    if (els.cfgHostAddress?.value.trim()) out.host_address = els.cfgHostAddress.value.trim();
    out.host_port = numberOrNull(els.cfgHostPort?.value);
    if (els.cfgApiVersion?.value.trim()) out.api_version = els.cfgApiVersion.value.trim();
    if (els.cfgApiKey?.value.trim() && !isRedactedSecret("api_key", els.cfgApiKey.value)) out.api_key = els.cfgApiKey.value;
    if (els.cfgApiKeyVar?.value.trim()) out.api_key_var = els.cfgApiKeyVar.value.trim();
    if (els.cfgAgentName?.value.trim()) out.agent_name = els.cfgAgentName.value.trim();
    const verbose = numberOrNull(els.cfgVerbose?.value);
    if (verbose !== null) out.verbose = verbose;
    out.mode = els.cfgMode?.value || "single_step";
    const maxSteps = numberOrNull(els.cfgMaxSteps?.value);
    if (maxSteps !== null) out.max_steps = maxSteps;
    out.orchestration_enabled = Boolean(els.cfgOrchestrationEnabled?.checked);
    const orchestrationWorkerCount = numberOrNull(els.cfgOrchestrationWorkerCount?.value);
    if (orchestrationWorkerCount !== null) out.orchestration_worker_count = orchestrationWorkerCount;
    const orchestrationMaxActiveTasks = numberOrNull(els.cfgOrchestrationMaxActiveTasks?.value);
    if (orchestrationMaxActiveTasks !== null) out.orchestration_max_active_tasks = orchestrationMaxActiveTasks;
    const orchestrationMaxTotalTasks = numberOrNull(els.cfgOrchestrationMaxTotalTasks?.value);
    if (orchestrationMaxTotalTasks !== null) out.orchestration_max_total_tasks = orchestrationMaxTotalTasks;
    const guiCommandTimeout = numberOrNull(els.cfgGuiCommandTimeout?.value);
    if (guiCommandTimeout !== null) out.gui_command_timeout_seconds = guiCommandTimeout;
    out.ctx_window_length = numberOrNull(els.cfgCtxWindow?.value);
    out.capabilities = parseCsv(els.cfgCapabilities?.value) || [];
    if (els.cfgSysPrompt?.value.trim()) out.sys_prompt = els.cfgSysPrompt.value;
    return sanitizeRedacted(out);
  }

  function policyFormToParams() {
    const out = {};
    out.action_policy = els.cfgActionPolicy?.value || "limited";
    out.enable_write = Boolean(els.cfgEnableWrite?.checked);
    out.enable_syscall = Boolean(els.cfgEnableSyscall?.checked);
    out.enable_gui_capture = Boolean(els.cfgEnableGuiCapture?.checked);
    out.syscall_allow_shell = Boolean(els.cfgSyscallShell?.checked);
    const timeout = numberOrNull(els.cfgSyscallTimeout?.value);
    if (timeout !== null) out.syscall_max_timeout = timeout;
    out.action_names = parseCsv(els.cfgActionNames?.value);
    out.syscall_allowed_commands = parseCsv(els.cfgSyscallAllow?.value);
    return sanitizeRedacted(out);
  }

  function applyPolicyToForm(policy = {}) {
    const params = extractPolicyParams(policy);
    const actionPolicy = params.action_policy || "limited";
    setValue(els.cfgActionPolicy, actionPolicy);
    setValue(els.cfgEnableWrite, params.enable_write || ["write", "dev", "advanced", "master"].includes(actionPolicy));
    setValue(els.cfgEnableSyscall, params.enable_syscall || ["dev", "master"].includes(actionPolicy));
    setValue(els.cfgEnableGuiCapture, params.enable_gui_capture || ["advanced", "master"].includes(actionPolicy));
    setValue(els.cfgSyscallShell, params.syscall_allow_shell || false);
    setValue(els.cfgSyscallTimeout, params.syscall_max_timeout ?? 10);
    setValue(els.cfgActionNames, csv(params.action_names));
    setValue(els.cfgSyscallAllow, csv(params.syscall_allowed_commands));
  }

  function syncPolicyRawFromForm() {
    if (!els.policyEditor) return;
    const merged = sanitizeRedacted({ ...readRawPolicyParams(), ...policyFormToParams() });
    els.policyEditor.value = JSON.stringify(merged, null, 2);
  }

  function markNeedsReconnect(message) {
    const suffix = ws && ws.readyState === WebSocket.OPEN
      ? " Existing websocket conversations may keep the previous agent instance; reconnect the session before sending the next chat to guarantee these agent params are active."
      : " Reconnect the session before chatting to use these agent params.";
    const text = `${message}${suffix}`;
    if (els.feedback) els.feedback.textContent = text;
    addMessage("system", text, { compact: true, gateway_reconnect_hint: true });
  }

  function reconnectAfterConfig(message) {
    if (!isAuthed() || !state.sessionId) {
      markNeedsReconnect(message);
      return;
    }
    const text = `${message} Reconnecting Gateway websocket so subsequent chat uses the committed runtime.`;
    if (els.feedback) els.feedback.textContent = text;
    addMessage("system", text, { compact: true, gateway_reconnect: true });
    state.phase = "connecting";
    saveState();
    render();
    setTimeout(() => {
      try { connectSession(); }
      catch (error) {
        state.phase = "error";
        state.lastError = error.message || "Reconnect failed after committing configuration.";
        saveState();
        render();
        if (els.feedback) els.feedback.textContent = `Reconnect failed: ${state.lastError}`;
        addMessage("system", `Reconnect failed after committing configuration: ${state.lastError}`, { tone: "error" });
      }
    }, 150);
  }

  function syncRawFromForm() {
    if (!els.paramsEditor) return;
    const merged = sanitizeRedacted({ ...currentParams, ...readRawParams(), ...formToParams() });
    els.paramsEditor.value = JSON.stringify(merged, null, 2);
  }

  function shortId(value) {
    const raw = String(value || "");
    if (!raw) return "";
    if (raw.length <= 18) return raw;
    return `${raw.slice(0, 8)}…${raw.slice(-6)}`;
  }

  function setStatus(kind, text) {
    if (els.statusDot) els.statusDot.className = `wolf-gateway-dot ${kind || ""}`.trim();
    if (els.statusText) els.statusText.textContent = text;
    const summary = $("session-summary");
    if (summary) summary.textContent = text;
  }

  function renderSessions() {
    if (!els.sessionSelect) return;
    const sessions = Array.isArray(state.sessions) ? state.sessions : [];
    const options = [];
    if (!isAuthed()) options.push('<option value="">Authenticate first…</option>');
    else options.push('<option value="">Select a session…</option>');
    for (const sess of sessions) {
      const sid = sess.session_id || sess.sessionId || "";
      if (!sid) continue;
      const bits = [sid];
      if (sess.active !== undefined) bits.push(sess.active ? "active" : "inactive");
      if (sess.created_at) bits.push(sess.created_at);
      options.push(`<option value="${sid}">${bits.join(" · ")}</option>`);
    }
    els.sessionSelect.innerHTML = options.join("");
    if (state.sessionId) els.sessionSelect.value = state.sessionId;
  }

  function render() {
    const authed = isAuthed();
    const selected = Boolean(state.sessionId);
    const connected = isConnected();

    if (els.url) els.url.value = state.gatewayUrl || els.url.value || defaultState.gatewayUrl;
    if (els.token) els.token.value = "";
    if (els.username) els.username.value = state.username || els.username.value || "";

    if (els.sessionStep) els.sessionStep.classList.toggle("wolf-gateway-hidden", !authed);
    if (els.authSummary) {
      els.authSummary.textContent = authed
        ? `Authenticated as ${state.accountId}${state.username ? ` (${state.username})` : ""}. Fetch/select/create a session, then connect it.`
        : "Not authenticated. Enter Gateway URL plus username/password, then click Authenticate.";
    }
    renderSessions();
    renderRunControl();

    setDisabled(els.refreshSessions, !authed);
    setDisabled(els.createSession, !authed);
    setDisabled(els.sessionSelect, !authed);
    setDisabled(els.connectSessionButton, !authed || !selected);
    setDisabled(els.showParams, !connected);
    setDisabled(els.saveParams, !connected);
    setDisabled(els.showPolicy, !connected);
    setDisabled(els.savePolicy, !connected);
    setDisabled(els.resetSession, !connected);
    formControls().forEach((el) => setDisabled(el, !connected));
    setDisabled(els.paramsEditor, !connected);
    setDisabled(els.policyEditor, !connected);

    if (els.submit) els.submit.textContent = authed ? "Re-authenticate" : "Authenticate";
    if (els.authenticate) els.authenticate.textContent = authed ? "Re-authenticate" : "Authenticate";

    if (state.phase === "connected") setStatus("connected", `Gateway connected · acct ${shortId(state.accountId)} · session ${shortId(state.sessionId)}`);
    else if (state.phase === "connecting") setStatus("testing", "Gateway connecting…");
    else if (state.phase === "error") setStatus("error", `Gateway error · ${state.lastError || "local workspace active"}`);
    else if (authed) setStatus("", `Authenticated · acct ${shortId(state.accountId)}${state.sessionId ? ` · selected ${shortId(state.sessionId)}` : ""}`);
    else setStatus("", "Gateway disconnected · local workspace active");
  }

  function open() {
    state = loadState();
    if (!state.gatewayUrl) state.gatewayUrl = defaultState.gatewayUrl;
    if (els.feedback) els.feedback.textContent = "";
    els.overlay.classList.remove("wolf-gateway-hidden");
    els.overlay.setAttribute("aria-hidden", "false");
    render();
    setTimeout(() => (isAuthed() ? els.sessionSelect?.focus() : els.url?.focus()), 0);
  }

  function close() {
    els.overlay.classList.add("wolf-gateway-hidden");
    els.overlay.setAttribute("aria-hidden", "true");
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.error || `${response.status} ${response.statusText}`);
    return payload;
  }

  async function fetchSessions() {
    if (!isAuthed()) throw new Error("Authenticate first. Gateway session list requires a valid gateway auth token from /auth/login.");
    const url = `${state.gatewayUrl}/accounts/${encodeURIComponent(state.accountId)}/sessions?token=${encodeURIComponent(state.token)}`;
    const response = await fetch(url, { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401 || response.status === 403) {
      clearAuth("Gateway authorization failed while fetching sessions. Re-authenticate with username/password; do not paste provider API keys into the gateway auth token field.");
      throw new Error(payload.detail || payload.error || `Session fetch unauthorized (${response.status})`);
    }
    if (!response.ok) throw new Error(payload.detail || payload.error || `Session fetch failed (${response.status})`);
    return Array.isArray(payload.sessions) ? payload.sessions : [];
  }

  async function authenticate() {
    const gatewayUrl = normaliseUrl(els.url?.value || state.gatewayUrl || defaultState.gatewayUrl);
    const username = String(els.username?.value || "").trim();
    const password = String(els.password?.value || "");
    const typedToken = String(els.token?.value || "").trim();

    if (!gatewayUrl) throw new Error("Gateway URL is required.");

    let nextState = { ...state, gatewayUrl };
    if (username || password) {
      if (!username || !password) throw new Error("Both username and password are required for gateway login.");
      const auth = await postJson(`${gatewayUrl}/auth/login`, { username, password });
      nextState = {
        ...nextState,
        username,
        token: auth.token,
        accountId: auth.account_id,
        sessionId: "",
        sessions: Array.isArray(auth.sessions) ? auth.sessions : [],
        phase: "authenticated",
        lastError: "",
      };
    } else if (typedToken && state.accountId) {
      nextState = { ...nextState, token: typedToken, phase: "authenticated", lastError: "" };
    } else if (state.token && state.accountId) {
      nextState = { ...nextState, phase: "authenticated", lastError: "" };
    } else {
      throw new Error("Authenticate with username/password first. The advanced token field is for an existing gateway auth token, not a provider API key.");
    }

    state = nextState;
    saveState();
    render();

    if (els.feedback) els.feedback.textContent = "Authenticated. Fetching sessions…";
    state.sessions = await fetchSessions();
    if (state.sessionId && !state.sessions.some((s) => (s.session_id || s.sessionId) === state.sessionId)) {
      state.sessionId = "";
    }
    state.phase = "authenticated";
    state.lastError = "";
    saveState();
    render();
    if (els.feedback) {
      els.feedback.textContent = state.sessions.length
        ? "Authenticated. Select an existing session or create a new one, then click Connect selected session."
        : "Authenticated. No previous sessions found; click Create new session, then Connect selected session.";
    }
  }

  function websocketUrl() {
    const url = new URL(state.gatewayUrl);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = `/ws/${encodeURIComponent(state.accountId)}/${encodeURIComponent(state.sessionId)}`;
    url.search = new URLSearchParams({
      token: state.token,
      participant_id: state.participantId || "gui",
      participant_role: "user",
      client_type: "gui",
    }).toString();
    return url.toString();
  }

  function addMessage(role, content, metadata = {}) {
    if (!content) return;
    const msg = {
      id: `gw_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
      role,
      content,
      created_at: Date.now() / 1000,
      visual_context: {},
      metadata,
    };
    if (window.wolfGuiState && Array.isArray(window.wolfGuiState.messages)) {
      window.wolfGuiState.messages.push(msg);
      try { window.wolfGuiRenderMessages?.(); } catch (_) {}
    } else {
      const list = document.getElementById("messages");
      if (list) {
        const node = document.createElement("article");
        node.className = `message ${role || "system"}`;
        node.textContent = content;
        list.appendChild(node);
        list.scrollTop = list.scrollHeight;
      }
    }
  }


  async function postLocalGui(path, body = {}) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || payload.detail || `${response.status} ${response.statusText}`);
    }
    return payload;
  }

  function agentCaptureAllowedForGateway() {
    return typeof window.wolfGuiAgentCaptureAllowed === "function" ? window.wolfGuiAgentCaptureAllowed() : Boolean(els.allowAgentCapture?.checked);
  }

  function isCapturableHttpUrl(value) {
    const raw = String(value || "").trim();
    if (!raw) return false;
    try {
      const url = new URL(raw, window.location.href);
      return url.protocol === "http:" || url.protocol === "https:";
    } catch (_) {
      return false;
    }
  }

  function normalizeCaptureUrl(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    try {
      const url = new URL(raw, window.location.href);
      return (url.protocol === "http:" || url.protocol === "https:") ? url.href : "";
    } catch (_) {
      return "";
    }
  }

  function extractHttpUrlsFromText(text) {
    const raw = String(text || "");
    if (!raw) return [];
    const found = [];
    const push = (candidate) => {
      const normalized = normalizeCaptureUrl(candidate);
      if (normalized && !found.includes(normalized)) found.push(normalized);
    };
    const attrRe = /(?:src|href)\s*=\s*["']([^"']+)["']/gi;
    let match;
    while ((match = attrRe.exec(raw))) push(match[1]);
    const urlRe = /https?:\/\/[^\s"'<>\)]+/gi;
    while ((match = urlRe.exec(raw))) push(match[0]);
    return found;
  }

  function panelIdentifier(panel) {
    return panel?.id || panel?.panel_id || panel?.key || panel?.title || panel?.name || "unknown_panel";
  }

  function mergeLiveVisualContextForRenderedCapture(body = {}) {
    // Rendered scopes need the full live geometry (annotation_targets,
    // visible_surfaces, viewport, dashboard_panels). Agents sometimes pass a
    // minimized visual_context containing only annotation_pixel_box; preserve
    // those explicit fields but enrich from the current browser GUI context.
    let live = null;
    try {
      live = typeof window.wolfGuiCurrentVisualContext === "function" ? window.wolfGuiCurrentVisualContext() : null;
    } catch (_) {
      live = null;
    }
    const partial = (body && typeof body.visual_context === "object" && body.visual_context) ? body.visual_context : {};
    const merged = { ...(live || {}), ...(partial || {}) };
    if (live?.annotation_targets && !partial.annotation_targets) merged.annotation_targets = live.annotation_targets;
    if (live?.annotations && !partial.annotations) merged.annotations = live.annotations;
    if (live?.visible_surfaces && !partial.visible_surfaces) merged.visible_surfaces = live.visible_surfaces;
    if (live?.viewport && !partial.viewport) merged.viewport = live.viewport;
    if (live?.dashboard_panels && !partial.dashboard_panels) merged.dashboard_panels = live.dashboard_panels;
    if (live?.active_dashboard && !partial.active_dashboard) merged.active_dashboard = live.active_dashboard;
    if (live?.workspace && !partial.workspace) merged.workspace = live.workspace;
    return merged;
  }

  function liveCaptureClip(body = {}, visualContext = {}) {
    const scope = String(body.capture_scope || "full_gui");
    const viewport = visualContext?.viewport || {};
    const surfaces = visualContext?.visible_surfaces || {};
    const vw = Number(viewport.width || window.innerWidth || 1);
    const vh = Number(viewport.height || window.innerHeight || 1);
    const boxToClip = (box) => {
      if (!box || !Number(box.width) || !Number(box.height)) return null;
      return {
        x: Math.max(0, Number(box.x || 0)),
        y: Math.max(0, Number(box.y || 0)),
        width: Math.max(1, Number(box.width || 0)),
        height: Math.max(1, Number(box.height || 0)),
        viewport_width: vw,
        viewport_height: vh,
      };
    };
    if (scope === "full_gui") return null;
    if (scope === "active_dashboard") return boxToClip(surfaces?.dashboard_workspace?.bounding_box) || null;
    if (scope === "workspace") return boxToClip(surfaces?.workspace_frame?.bounding_box || surfaces?.dashboard_workspace?.bounding_box) || null;
    if (scope === "annotation_regions") {
      const target = Array.isArray(visualContext?.annotation_targets) ? visualContext.annotation_targets[0] : null;
      return boxToClip(target?.pixel_box || visualContext?.annotation_pixel_box || visualContext?.pixel_box) || null;
    }
    return null;
  }

  async function dataUrlFromBlob(blob) {
    return await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(reader.error || new Error("Could not read captured image blob"));
      reader.readAsDataURL(blob);
    });
  }

  function requestLiveClientCaptureFromUser(body = {}) {
    // Browsers require navigator.mediaDevices.getDisplayMedia() to be called
    // from a transient user activation (a real click/key gesture). A websocket
    // gui_command is not a user gesture, so we park the command and present a
    // local capture button. The button's click handler then calls
    // captureLiveClientSurface() directly.
    return new Promise((resolve, reject) => {
      const timeoutMs = Math.max(15000, Number(body?.live_capture_timeout_ms || 120000));
      const existing = document.getElementById("wolfLiveCapturePrompt");
      if (existing) existing.remove();

      const prompt = document.createElement("div");
      prompt.id = "wolfLiveCapturePrompt";
      prompt.setAttribute("role", "dialog");
      prompt.setAttribute("aria-live", "assertive");
      prompt.style.cssText = [
        "position:fixed",
        "z-index:2147483647",
        "right:18px",
        "bottom:18px",
        "width:min(420px,calc(100vw - 36px))",
        "padding:14px",
        "border:1px solid rgba(251,191,36,0.75)",
        "border-radius:16px",
        "background:rgba(15,23,42,0.96)",
        "color:#e5f3ff",
        "box-shadow:0 24px 80px rgba(0,0,0,0.45), 0 0 0 4px rgba(251,191,36,0.16)",
        "font:14px/1.4 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif"
      ].join(";");

      const title = document.createElement("div");
      title.textContent = "Agent requests live GUI capture";
      title.style.cssText = "font-weight:750;margin-bottom:6px;color:#fde68a";
      const msg = document.createElement("div");
      msg.textContent = "Click Capture live GUI, then choose this Wolf GUI tab/window in the browser sharing picker. This is required by browser security rules.";
      msg.style.cssText = "margin-bottom:12px;color:#dbeafe";
      const row = document.createElement("div");
      row.style.cssText = "display:flex;gap:8px;justify-content:flex-end;align-items:center";
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.textContent = "Use backend fallback";
      cancel.style.cssText = "padding:8px 10px;border-radius:10px;border:1px solid rgba(148,163,184,0.45);background:rgba(255,255,255,0.06);color:#e5f3ff;cursor:pointer";
      const capture = document.createElement("button");
      capture.type = "button";
      capture.textContent = "Capture live GUI";
      capture.style.cssText = "padding:8px 12px;border-radius:10px;border:1px solid rgba(34,197,94,0.75);background:rgba(34,197,94,0.22);color:#dcfce7;font-weight:700;cursor:pointer";
      row.append(cancel, capture);
      prompt.append(title, msg, row);
      document.body.appendChild(prompt);
      try { capture.focus({ preventScroll: true }); } catch (_) {}

      let settled = false;
      const cleanup = () => {
        try { clearTimeout(timer); } catch (_) {}
        try { prompt.remove(); } catch (_) {}
      };
      const finish = (fn, value) => {
        if (settled) return;
        settled = true;
        cleanup();
        fn(value);
      };
      const timer = setTimeout(() => {
        finish(reject, new Error(`Timed out waiting for user to approve live GUI capture after ${Math.round(timeoutMs / 1000)}s.`));
      }, timeoutMs);

      cancel.addEventListener("click", () => {
        finish(reject, new Error("User chose backend replay fallback instead of live GUI capture."));
      }, { once: true });

      capture.addEventListener("click", () => {
        capture.disabled = true;
        cancel.disabled = true;
        capture.textContent = "Opening browser picker…";
        // Do not await before calling captureLiveClientSurface; it must invoke
        // getDisplayMedia inside this click activation chain.
        captureLiveClientSurface(body).then(
          (result) => finish(resolve, result),
          (error) => finish(reject, error instanceof Error ? error : new Error(String(error)))
        );
      }, { once: true });
    });
  }

  async function captureLiveClientSurface(body = {}) {
    if (!navigator.mediaDevices?.getDisplayMedia) {
      throw new Error("Live browser-surface capture is not supported by this browser.");
    }
    const visualContext = mergeLiveVisualContextForRenderedCapture(body);
    addMessage("system", "Live GUI capture requested. Choose this Wolf GUI tab/window in the browser sharing picker.", { compact: true, live_capture_notice: true });
    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: { cursor: "always", displaySurface: "browser" },
      audio: false,
    });
    try {
      const video = document.createElement("video");
      video.muted = true;
      video.playsInline = true;
      video.srcObject = stream;
      await new Promise((resolve, reject) => {
        video.onloadedmetadata = resolve;
        video.onerror = () => reject(new Error("Could not initialize live capture video stream"));
      });
      await video.play();
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));

      const sourceW = video.videoWidth || window.innerWidth || 1;
      const sourceH = video.videoHeight || window.innerHeight || 1;
      const clip = liveCaptureClip(body, visualContext);
      const scaleX = sourceW / Number(clip?.viewport_width || visualContext?.viewport?.width || window.innerWidth || sourceW);
      const scaleY = sourceH / Number(clip?.viewport_height || visualContext?.viewport?.height || window.innerHeight || sourceH);
      let sx = 0, sy = 0, sw = sourceW, sh = sourceH;
      if (clip) {
        sx = Math.max(0, Math.min(sourceW - 1, Math.round(Number(clip.x || 0) * scaleX)));
        sy = Math.max(0, Math.min(sourceH - 1, Math.round(Number(clip.y || 0) * scaleY)));
        sw = Math.max(1, Math.min(sourceW - sx, Math.round(Number(clip.width || sourceW) * scaleX)));
        sh = Math.max(1, Math.min(sourceH - sy, Math.round(Number(clip.height || sourceH) * scaleY)));
      }
      const canvas = document.createElement("canvas");
      canvas.width = sw;
      canvas.height = sh;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("Canvas 2D context is unavailable for live capture.");
      ctx.drawImage(video, sx, sy, sw, sh, 0, 0, sw, sh);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("Could not encode live capture image.");
      const imageData = await dataUrlFromBlob(blob);
      const upload = await httpJson("POST", `/api/gui/capture/live?session_id=${encodeURIComponent(state.sessionId || "default")}`, {
        image_data: imageData,
        format: "png",
        capture_scope: body.capture_scope || "full_gui",
        source_url: window.location.href,
        width: sw,
        height: sh,
        metadata: {
          ...(body.metadata || {}),
          capture_mode: "live_client_surface",
          live_capture_clip: clip,
          live_capture_source_size: { width: sourceW, height: sourceH },
          live_visual_context: visualContext,
          user_permission: "granted_via_getDisplayMedia",
        },
      });
      return {
        ok: true,
        status: "success",
        capture_mode: "live_client_surface",
        capture_scope: body.capture_scope || "full_gui",
        count: 1,
        results: [upload],
        rendered_gui_url: window.location.href,
        skipped_targets: [],
      };
    } finally {
      try { stream.getTracks().forEach((track) => track.stop()); } catch (_) {}
    }
  }

  function collectWorkspaceCaptureTargets(payload = {}) {
    const requestedPanelIds = new Set((Array.isArray(payload.panel_ids) ? payload.panel_ids : []).map((v) => String(v)));
    const urls = [];
    const skipped_targets = [];
    const addUrl = (candidate, meta = {}) => {
      const normalized = normalizeCaptureUrl(candidate);
      if (normalized) {
        if (!urls.includes(normalized)) urls.push(normalized);
        return true;
      }
      const raw = String(candidate || "").trim();
      if (raw) skipped_targets.push({ ...meta, value: raw, reason: "not_capturable_http_url" });
      return false;
    };

    if (Array.isArray(payload.urls) && payload.urls.length) {
      payload.urls.forEach((url, index) => addUrl(url, { source: "payload.urls", index }));
      return { urls, skipped_targets, visual_context: payload.visual_context || null };
    }

    let vc = payload.visual_context || null;
    try {
      if (!vc && typeof window.wolfGuiCurrentVisualContext === "function") vc = window.wolfGuiCurrentVisualContext();
      const panels = [];
      if (Array.isArray(vc?.dashboard_panels)) panels.push(...vc.dashboard_panels);
      if (Array.isArray(vc?.dashboard?.panels)) panels.push(...vc.dashboard.panels);
      if (Array.isArray(vc?.active_dashboard?.panels)) panels.push(...vc.active_dashboard.panels);
      for (const panel of panels) {
        const panelId = String(panelIdentifier(panel));
        if (requestedPanelIds.size && !requestedPanelIds.has(panelId)) {
          skipped_targets.push({ source: "visual_context.dashboard_panels", panel_id: panelId, reason: "panel_not_requested" });
          continue;
        }
        const before = urls.length;
        addUrl(panel?.url, { source: "panel.url", panel_id: panelId });
        addUrl(panel?.iframe?.src, { source: "panel.iframe.src", panel_id: panelId });
        addUrl(panel?.iframe?.url, { source: "panel.iframe.url", panel_id: panelId });
        extractHttpUrlsFromText(panel?.inline_html_excerpt).forEach((url, index) => addUrl(url, { source: "panel.inline_html_excerpt", panel_id: panelId, index }));
        extractHttpUrlsFromText(panel?.iframe?.src).forEach((url, index) => addUrl(url, { source: "panel.iframe.src_embedded", panel_id: panelId, index }));
        if (urls.length === before) skipped_targets.push({ source: "visual_context.dashboard_panels", panel_id: panelId, reason: "no_capturable_http_url_found" });
        if (urls.length >= Number(payload.max_panels || 6)) break;
      }
    } catch (err) {
      skipped_targets.push({ source: "visual_context", reason: "collection_error", error: String(err?.message || err) });
    }
    return { urls: urls.slice(0, Number(payload.max_panels || 6)), skipped_targets, visual_context: vc };
  }

  async function executeGatewayGuiCommand(event) {
    const commandId = event?.command_id || `guicmd_${Date.now()}`;
    const action = event?.action;
    const payload = event?.payload || {};
    try {
      let result;
      if (action === "gui_notify") {
        result = await postLocalGui("/api/gui/control", { command: "notify", args: payload });
      } else if (action === "gui_create_dashboard") {
        const body = { ...payload };
        const openAfter = Boolean(body.open);
        delete body.open;
        result = await postLocalGui("/api/gui/dashboards/create", body);
        const dashboardId = result?.dashboard?.id;
        if (openAfter && dashboardId) result.opened = await postLocalGui("/api/gui/dashboards/open", { dashboard_id: dashboardId });
      } else if (action === "gui_add_dashboard_panel") {
        const body = { ...payload };
        const openAfter = Boolean(body.open);
        delete body.open;
        result = await postLocalGui("/api/gui/dashboards/add_panel", body);
        const dashboardId = body.dashboard_id || result?.panel?.dashboard_id;
        if (openAfter && dashboardId) result.opened = await postLocalGui("/api/gui/dashboards/open", { dashboard_id: dashboardId });
      } else if (action === "gui_update_dashboard_panel") {
        const body = { ...payload };
        const openAfter = Boolean(body.open);
        delete body.open;
        result = await postLocalGui("/api/gui/dashboards/update_panel", body);
        const dashboardId = result?.panel?.dashboard_id;
        if (openAfter && dashboardId) result.opened = await postLocalGui("/api/gui/dashboards/open", { dashboard_id: dashboardId });
      } else if (action === "gui_open_dashboard") {
        result = await postLocalGui("/api/gui/dashboards/open", payload);
      } else if (action === "gui_publish_dashboard") {
        result = await postLocalGui("/api/gui/dashboards/publish", payload);
      } else if (action === "gui_register_app") {
        const body = { ...payload };
        const openAfter = Boolean(body.open);
        delete body.open;
        result = await postLocalGui("/api/gui/apps/register", body);
        const appId = result?.app?.id;
        if (openAfter && appId) result.opened = await postLocalGui("/api/gui/workspace/open_app", { app_id: appId });
      } else if (action === "gui_open_app") {
        result = await postLocalGui("/api/gui/workspace/open_app", payload);
      } else if (action === "gui_get_visual_context") {
        const allowed = typeof window.wolfGuiAgentInspectAllowed === "function" ? window.wolfGuiAgentInspectAllowed() : Boolean(els.allowAgentInspect?.checked);
        if (!allowed) throw new Error("Agent workspace inspection disabled by user. Turn on 'Allow agent inspect' in the composer to permit gui_get_visual_context.");
        if (typeof window.wolfGuiCurrentVisualContext !== "function") throw new Error("Wolf GUI visual context provider is unavailable.");
        result = window.wolfGuiCurrentVisualContext();
      } else if (action === "gui_capture_url") {
        if (!agentCaptureAllowedForGateway()) throw new Error("Agent screenshot capture disabled by user. Turn on 'Allow agent capture' to permit gui_capture_url.");
        result = await httpJson("POST", `/api/gui/capture/url?session_id=${encodeURIComponent(state.sessionId || "default")}`, payload);
      } else if (action === "gui_capture_workspace") {
        if (!agentCaptureAllowedForGateway()) throw new Error("Agent screenshot capture disabled by user. Turn on 'Allow agent capture' to permit gui_capture_workspace.");
        const body = { ...payload };
        if (!body.visual_context && typeof window.wolfGuiCurrentVisualContext === "function") body.visual_context = window.wolfGuiCurrentVisualContext();
        if (!body.capture_scope) body.capture_scope = Array.isArray(body.urls) && body.urls.length ? "url_list" : (Array.isArray(body.panel_ids) && body.panel_ids.length ? "selected_panels" : "active_dashboard_panels");
        if (["url_list", "active_dashboard_panels", "selected_panels"].includes(body.capture_scope)) {
          const collected = collectWorkspaceCaptureTargets(body);
          body.urls = collected.urls;
          if (!body.visual_context && collected.visual_context) body.visual_context = collected.visual_context;
          body.metadata = { ...(body.metadata || {}), capture_target_collection: { skipped_targets: collected.skipped_targets, resolved_url_count: collected.urls.length } };
          if (!body.urls.length) {
            result = { ok: false, status: "no_targets", capture_scope: body.capture_scope, count: 0, results: [], skipped_targets: collected.skipped_targets, error: "No capturable HTTP(S) URL targets found for requested GUI workspace capture scope." };
          } else {
            result = await httpJson("POST", `/api/gui/capture/workspace?session_id=${encodeURIComponent(state.sessionId || "default")}`, body);
          }
        } else if (["workspace", "active_dashboard", "full_gui", "annotation_regions"].includes(body.capture_scope)) {
          // Prefer a permissioned screenshot of the user's actual live browser
          // surface. This avoids the older backend replay path, which opens a
          // separate browser and may not match the visible GUI tab. If browser
          // permission is denied/unavailable, fall back to backend replay and
          // report that fallback in the result metadata.
          body.visual_context = mergeLiveVisualContextForRenderedCapture(body);
          body.metadata = { ...(body.metadata || {}), rendered_scope_forwarded_by: "browser_gateway", live_visual_context_merged: true, preferred_capture_mode: "live_client_surface" };
          try {
            result = await requestLiveClientCaptureFromUser(body);
          } catch (liveError) {
            const liveMessage = String(liveError?.message || liveError);
            addMessage("system", `Live GUI capture unavailable; falling back to backend replay capture: ${liveMessage}`, { compact: true, tone: "warning", live_capture_fallback: true });
            body.metadata = { ...(body.metadata || {}), live_client_capture_error: liveMessage, fallback_capture_mode: "backend_replay" };
            result = await httpJson("POST", `/api/gui/capture/workspace?session_id=${encodeURIComponent(state.sessionId || "default")}`, body);
            result = { ...(result || {}), live_client_capture_error: liveMessage, capture_mode: result?.capture_mode || "backend_replay" };
          }
        } else {
          result = { ok: false, status: "unsupported_scope", capture_scope: body.capture_scope, count: 0, results: [], skipped_targets: [], error: `Unsupported capture scope '${body.capture_scope}'. Use url_list, active_dashboard_panels, selected_panels, workspace, active_dashboard, full_gui, or annotation_regions.` };
        }
      } else if (action === "gui_get_dom") {
        result = {
          title: document.title,
          url: window.location.href,
          body_text: document.body ? document.body.innerText.slice(0, 20000) : "",
          html: document.documentElement ? document.documentElement.outerHTML.slice(0, 50000) : ""
        };
      } else if (action === "gui_click") {
        const selector = payload.selector || payload.css_selector;
        if (!selector) throw new Error("gui_click requires payload.selector");
        const el = document.querySelector(selector);
        if (!el) throw new Error(`No element matches ${selector}`);
        el.click();
        result = { clicked: true, selector };
      } else if (action === "gui_type") {
        const selector = payload.selector || payload.css_selector;
        if (!selector) throw new Error("gui_type requires payload.selector");
        const el = document.querySelector(selector);
        if (!el) throw new Error(`No element matches ${selector}`);
        el.focus();
        el.value = payload.text || payload.value || "";
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        result = { typed: true, selector, length: String(el.value || "").length };
      } else if (action === "gui_eval") {
        const code = payload.code || payload.script;
        if (!code) throw new Error("gui_eval requires payload.code");
        // eslint-disable-next-line no-new-func
        result = await Promise.resolve(Function(`"use strict"; return (${code});`)());
      } else {
        throw new Error(`Unsupported GUI command action: ${action}`);
      }
      ws?.send(JSON.stringify({ type: "gui_command_result", command_id: commandId, action, ok: true, result, content: `GUI command completed: ${action}` }));
      addMessage("system", `GUI command completed: ${action}`, { gateway_event: event, compact: true });
    } catch (err) {
      ws?.send(JSON.stringify({ type: "gui_command_result", command_id: commandId, action, ok: false, error: String(err?.message || err), content: `GUI command failed: ${action}` }));
      addMessage("system", `GUI command failed: ${action}: ${String(err?.message || err)}`, { gateway_event: event, tone: "error" });
    }
  }

  function handleGatewayEvent(event) {
    const type = event?.type || "message";
    const content = event?.content || "";
    if (type === "ping") { try { ws?.send(JSON.stringify({ type: "pong", timestamp: new Date().toISOString() })); } catch (_) {} return; }
    if (type === "pong" || type === "user_echo") return;
    if (type === "system") return addMessage("system", content || "Connected to gateway.", { gateway_event: event });
    if (type === "error" || type === "workflow_error") return addMessage("system", content || event.error || "Gateway error.", { gateway_event: event, tone: "error" });
    if (type === "agent_response") return addMessage("assistant", content, { gateway_event: event });
    if (type === "run_control_state") {
      applyRunControlState(event);
      renderRunControl();
      return addMessage("system", content || `Run status: ${state.runStatus}`, { gateway_event: event, compact: true });
    }
    if (type === "workflow_control") {
      if (event?.status) state.runStatus = String(event.status);
      renderRunControl();
      return addMessage("system", content || `Workflow control: ${event.status || "update"}`, { gateway_event: event, compact: true });
    }
    if (type === "workflow_status") {
      if (String(event?.status || "") === "done" && String(event?.stop_reason || "").toLowerCase() === "error") {
        state.runStatus = "failed";
        renderRunControl();
      }
      return addMessage("system", `Workflow: ${content || event.status || "status"}`, { gateway_event: event, compact: true });
    }
    if (type === "policy_resolved") return addMessage("system", `Policy resolved: ${event.action_policy || "limited"}`, { gateway_event: event, compact: true });
    if (type === "gui_route_resolved") return addMessage("system", content || `GUI route: ${event.route || "auto"}`, { gateway_event: event, compact: true });
    if (type === "gui_command") { executeGatewayGuiCommand(event); return; }
    if (type === "gui_command_result") return addMessage("system", content || `GUI command result: ${event.ok ? "ok" : "failed"}`, { gateway_event: event, compact: true });
    if (type === "workflow_action") return addMessage("system", content || `Action: ${event.action || event.payload?.action || "action"}`, { gateway_event: event, card: true });
    if (type === "workflow_result") {
      const resultAction = event.action || event.payload?.action || event.normalized?.action || event.result?.action || "action";
      if (resultAction === "send_message") return addMessage("assistant", content || event.payload?.message || event.payload?.content || event.message || "", { gateway_event: event, force_visible: true });
      return addMessage("system", `Result: ${resultAction} — ${content || "completed"}`, { gateway_event: event, card: true });
    }
    if (type === "presence") return addMessage("system", content || "Presence updated.", { gateway_event: event, compact: true });
    if (type === "participant_message") return addMessage("assistant", content, { gateway_event: event });
    return addMessage("system", content || `Gateway event: ${type}`, { gateway_event: event });
  }

  function applyRunControlState(payload = {}) {
    const status = String(payload.status || state.runStatus || "idle").trim() || "idle";
    state.runStatus = status;
    state.runId = String(payload.run_id || state.runId || "");
    state.pauseRequested = Boolean(payload.pause_requested);
    state.stopRequested = Boolean(payload.stop_requested);
    state.reassessRequested = Boolean(payload.reassess_requested);
    const pending = payload.pending_user_messages;
    state.pendingUserMessageCount = Array.isArray(pending) ? pending.length : Number(payload.pending_user_message_count || state.pendingUserMessageCount || 0);
    state.runStep = Number(payload.step ?? state.runStep ?? 0) || 0;
  }

  function renderRunControl() {
    const status = String(state.runStatus || "idle");
    const connected = isConnected();
    const active = new Set(["running", "pause_requested", "paused", "resume_requested", "stop_requested"]);
    const canPause = connected && (status === "running" || status === "resume_requested") && !state.pauseRequested;
    const canResume = connected && status === "paused";
    const canStop = connected && active.has(status) && status !== "stop_requested";

    if (els.agentRunStatus) {
      const pretty = status.replace(/_/g, " ").replace(/(^|\s)(\w)/g, (m) => m.toUpperCase());
      els.agentRunStatus.textContent = pretty || "Idle";
      els.agentRunStatus.className = `agent-run-status status-${status || "idle"}`;
    }
    setDisabled(els.agentPauseRun, !canPause);
    setDisabled(els.agentResumeRun, !canResume);
    setDisabled(els.agentStopRun, !canStop);
  }

  function sendGatewayControl(command, extra = {}) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify({
      type: "agent_control",
      command,
      run_id: state.runId || "current",
      timestamp: new Date().toISOString(),
      session_id: state.sessionId,
      source: "gui",
      authority: "user",
      ...extra,
    }));
    return true;
  }

  function connectSession() {
    if (!isAuthed()) throw new Error("Authenticate first.");
    state.sessionId = String(els.sessionSelect?.value || state.sessionId || "").trim();
    if (!state.sessionId) throw new Error("Select a session or click Create new session.");

    const previousWs = ws;
    if (previousWs && previousWs.readyState <= 1) {
      try { previousWs.close(); } catch (_) {}
    }
    state.phase = "connecting";
    saveState();
    render();
    if (els.feedback) els.feedback.textContent = `Opening websocket for ${state.accountId}/${state.sessionId}…`;

    const socket = new WebSocket(websocketUrl());
    ws = socket;
    window.wolfGatewaySocket = socket;
    socket.addEventListener("open", () => {
      if (ws !== socket) return;
      state.phase = "connected";
      state.lastError = "";
      saveState();
      render();
      if (els.feedback) els.feedback.textContent = "Gateway websocket connected. Fetching agent and policy state…";
      try {
        socket.send(JSON.stringify({
          type: "gui_client_hello",
          requested_route: "auto",
          gui_url: window.location.origin,
          agent_inspect_allowed: typeof window.wolfGuiAgentInspectAllowed === "function" ? window.wolfGuiAgentInspectAllowed() : Boolean(els.allowAgentInspect?.checked),
          agent_capture_allowed: agentCaptureAllowedForGateway(),
          visual_context_capabilities: {
            push_on_user_message: true,
            pull_action: "gui_get_visual_context",
            capture_actions: ["gui_capture_url", "gui_capture_workspace"],
            permission_toggle_id: "allow-agent-inspect",
            cross_origin_iframe_pixels: agentCaptureAllowedForGateway() ? "live_client_surface_or_backend_capture_available" : false,
            live_client_surface_capture: agentCaptureAllowedForGateway() && Boolean(navigator.mediaDevices?.getDisplayMedia),
            same_origin_iframe_dom_excerpt: "best_effort"
          },
          timestamp: new Date().toISOString()
        }));
      } catch (_) {}
      Promise.allSettled([showParams(), showPolicy()]).then(() => {
        try { sendGatewayControl("state_request"); } catch (_) {}
        if (els.feedback) els.feedback.textContent = "Gateway session connected. Agent and policy forms are ready.";
        render();
      });
    });
    socket.addEventListener("message", (ev) => {
      if (ws !== socket) return;
      try { handleGatewayEvent(JSON.parse(ev.data)); }
      catch { addMessage("system", String(ev.data || ""), { raw_gateway_event: true }); }
    });
    socket.addEventListener("close", () => {
      if (ws !== socket) return;
      if (!intentionalClose) {
        state.phase = "error";
        state.lastError = "Websocket disconnected.";
        saveState();
        render();
      }
    });
    socket.addEventListener("error", () => {
      if (ws !== socket) return;
      state.phase = "error";
      state.lastError = "Websocket error.";
      saveState();
      render();
      if (els.feedback) els.feedback.textContent = state.lastError;
    });
  }

  async function handleSubmit(ev) {
    ev.preventDefault();
    ev.stopPropagation();
    if (els.submit) els.submit.disabled = true;
    if (els.authenticate) els.authenticate.disabled = true;
    try {
      await authenticate();
    } catch (error) {
      state.phase = "error";
      state.lastError = error.message || "Gateway authentication failed.";
      saveState();
      render();
      if (els.feedback) els.feedback.textContent = `Authentication failed: ${state.lastError}`;
    } finally {
      if (els.submit) els.submit.disabled = false;
      if (els.authenticate) els.authenticate.disabled = false;
      render();
    }
  }

  async function handleConnectSession(ev) {
    ev?.preventDefault?.();
    ev?.stopPropagation?.();
    try {
      connectSession();
    } catch (error) {
      state.phase = "error";
      state.lastError = error.message || "Gateway session connection failed.";
      saveState();
      render();
      if (els.feedback) els.feedback.textContent = `Connect failed: ${state.lastError}`;
    }
  }

  async function refreshSessions() {
    try {
      if (!isAuthed()) throw new Error("Authenticate first.");
      if (els.feedback) els.feedback.textContent = "Fetching sessions…";
      state.sessions = await fetchSessions();
      saveState();
      render();
      if (els.feedback) els.feedback.textContent = state.sessions.length ? "Sessions fetched." : "No sessions found. Create a new session.";
    } catch (error) {
      if (els.feedback) els.feedback.textContent = `Fetch sessions failed: ${error.message}`;
    }
  }

  function createSession() {
    if (!isAuthed()) {
      if (els.feedback) els.feedback.textContent = "Authenticate first.";
      return;
    }
    state.sessionId = uuid();
    const exists = state.sessions.some((s) => (s.session_id || s.sessionId) === state.sessionId);
    if (!exists) state.sessions.unshift({ session_id: state.sessionId, created_at: new Date().toISOString(), active: false, client_type: "gui-new" });
    saveState();
    render();
    if (els.feedback) els.feedback.textContent = "New session prepared. Click Connect selected session to create/connect it on the gateway.";
  }

  function continueLocal() {
    intentionalClose = true;
    try { ws?.close(); } catch (_) {}
    state.phase = "local";
    state.lastError = "";
    saveState();
    render();
    close();
  }

  async function httpJson(method, endpoint, body = undefined) {
    if (!isAuthed()) throw new Error("Authenticate first.");
    const url = `${state.gatewayUrl}${endpoint}${endpoint.includes("?") ? "&" : "?"}token=${encodeURIComponent(state.token)}`;
    const options = { method, cache: "no-store", headers: { "Content-Type": "application/json" } };
    if (body !== undefined) options.body = JSON.stringify(body);
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.error || `${response.status} ${response.statusText}`);
    return payload;
  }

  let agentPresets = [];

  function loadCachedPresets() {
    try {
      const raw = window.localStorage?.getItem(PRESET_STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (_) {
      return [];
    }
  }

  function cachePresets(presets = []) {
    try { window.localStorage?.setItem(PRESET_STORAGE_KEY, JSON.stringify(presets || [])); } catch (_) {}
  }

  function selectedPresetId() {
    return String(els.agentPresetSelect?.value || window.localStorage?.getItem(PRESET_SELECTED_KEY) || "");
  }

  function renderPresetSelect(presets = agentPresets) {
    agentPresets = Array.isArray(presets) ? presets : [];
    if (!els.agentPresetSelect) return;
    const previous = selectedPresetId();
    els.agentPresetSelect.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = agentPresets.length ? "Select an agent config preset…" : "No presets loaded";
    els.agentPresetSelect.appendChild(placeholder);
    agentPresets.forEach((preset) => {
      const opt = document.createElement("option");
      opt.value = preset.id || `${preset.source_file || "preset"}::${preset.key || preset.model || "unnamed"}`;
      opt.textContent = preset.display_name || opt.value;
      try { opt.title = JSON.stringify(preset.params || preset, null, 2); } catch (_) {}
      els.agentPresetSelect.appendChild(opt);
    });
    if (previous && agentPresets.some((p) => p.id === previous)) els.agentPresetSelect.value = previous;
    if (els.agentPresetSummary) {
      els.agentPresetSummary.textContent = agentPresets.length
        ? `${agentPresets.length} preset(s) loaded from project JSON files.`
        : "Presets are read from ./llms.json, ./sample_llm_config.json, and ./JSONs/*.json.";
    }
  }

  function currentPreset() {
    const id = selectedPresetId();
    return agentPresets.find((preset) => preset.id === id) || null;
  }

  async function loadAgentPresets() {
    const data = await httpJson("GET", "/agent-config-presets");
    agentPresets = Array.isArray(data.presets) ? data.presets : [];
    cachePresets(agentPresets);
    renderPresetSelect(agentPresets);
    if (els.feedback) els.feedback.textContent = `Loaded ${agentPresets.length} agent config preset(s).`;
    const errorNote = Array.isArray(data.errors) && data.errors.length ? ` (${data.errors.length} file warning(s))` : "";
    addMessage("system", `Agent config presets loaded: ${agentPresets.length}${errorNote}.`, { compact: true, gateway_result: data });
    return data;
  }

  function applyAgentPresetToForm(preset = currentPreset()) {
    if (!preset) throw new Error("Select an agent config preset first.");
    const params = sanitizeRedacted({ ...(preset.params || {}) });
    currentParams = { ...currentParams, ...params };
    applyParamsToForm(currentParams);
    if (els.paramsEditor) els.paramsEditor.value = JSON.stringify(currentParams, null, 2);
    try { window.localStorage?.setItem(PRESET_SELECTED_KEY, preset.id || ""); } catch (_) {}
    if (els.feedback) els.feedback.textContent = `Applied preset ${preset.display_name || preset.id} to the form.`;
    addMessage("system", `Applied agent config preset: ${preset.display_name || preset.id}`, { compact: true, gateway_result: preset });
    return params;
  }

  async function applyAndCommitAgentPreset() {
    applyAgentPresetToForm();
    await saveParams();
  }

  async function showParams() {
    if (!state.sessionId) throw new Error("Select/connect a session first.");
    const params = await httpJson("GET", `/sessions/${encodeURIComponent(state.sessionId)}/params`);
    currentParams = sanitizeRedacted(filterOutPolicyParams(params || {}));
    applyParamsToForm(currentParams);
    if (els.paramsEditor) els.paramsEditor.value = JSON.stringify(currentParams, null, 2);
    if (els.feedback) els.feedback.textContent = "Agent params loaded into form.";
    addMessage("system", "Agent params loaded into Gateway → Agent parameters form.", { compact: true });
  }

  async function saveParams() {
    if (!state.sessionId) throw new Error("Select/connect a session first.");
    syncRawFromForm();
    const updates = sanitizeRedacted({ ...readRawAgentParams(), ...formToParams() });
    const result = await httpJson("PATCH", `/sessions/${encodeURIComponent(state.sessionId)}/params`, updates);
    currentParams = { ...currentParams, ...updates };
    applyParamsToForm(currentParams);
    if (els.paramsEditor) els.paramsEditor.value = JSON.stringify(currentParams, null, 2);
    addMessage("system", `Agent params committed: ${JSON.stringify(result.updated_params || updates)}`, { gateway_result: result, compact: true });
    reconnectAfterConfig("Agent params committed.");
  }

  async function showPolicy() {
    if (!state.sessionId) throw new Error("Select/connect a session first.");
    const policy = await httpJson("GET", `/sessions/${encodeURIComponent(state.sessionId)}/policy`);
    currentPolicy = policy || {};
    const policyParams = extractPolicyParams(currentPolicy);
    applyPolicyToForm(policyParams);
    if (els.policyEditor) els.policyEditor.value = JSON.stringify(policyParams, null, 2);
    if (els.feedback) els.feedback.textContent = "Policy params loaded into form.";
    addMessage("system", `Policy params loaded. Resolved actions: ${JSON.stringify(policy.resolved_action_names || [])}`, { gateway_policy: policy, compact: true });
  }

  async function savePolicy() {
    if (!state.sessionId) throw new Error("Select/connect a session first.");
    syncPolicyRawFromForm();
    const updates = sanitizeRedacted({ ...readRawPolicyParams(), ...policyFormToParams() });
    const result = await httpJson("PATCH", `/sessions/${encodeURIComponent(state.sessionId)}/params`, updates);
    try {
      currentPolicy = await httpJson("GET", `/sessions/${encodeURIComponent(state.sessionId)}/policy`);
    } catch {
      currentPolicy = { ...currentPolicy, ...updates };
    }
    const policyParams = extractPolicyParams(currentPolicy);
    applyPolicyToForm(policyParams);
    if (els.policyEditor) els.policyEditor.value = JSON.stringify(policyParams, null, 2);
    const recreated = Boolean(result.runtime_recreated);
    addMessage(
      "system",
      recreated
        ? `Policy params committed; runtime was recreated: ${JSON.stringify(result.updated_params || updates)}`
        : `Policy params committed without resetting chat/context: ${JSON.stringify(result.updated_params || updates)}`,
      { gateway_result: result, gateway_policy: currentPolicy, compact: true }
    );
    if (els.feedback) els.feedback.textContent = recreated
      ? "Policy params committed. Runtime was recreated by the gateway."
      : "Policy params committed. New privileges apply on the next workflow turn without reconnecting.";
    if (recreated) reconnectAfterConfig("Policy params committed.");
    else render();
  }

  async function resetSession() {
    if (!state.sessionId) throw new Error("Select/connect a session first.");
    const result = await httpJson("POST", `/sessions/${encodeURIComponent(state.sessionId)}/reset`, {});
    if (els.feedback) els.feedback.textContent = "Agent context reset.";
    addMessage("system", `Agent context reset for ${result.session_id || state.sessionId}.`, { gateway_result: result, compact: true });
  }

  function sendGatewayChat(content, visualContext = {}) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    if (new Set(["running","pause_requested","paused","resume_requested","stop_requested"]).has(String(state.runStatus || ""))) {
      addMessage("system", "Agent is working. Your message will be applied at the next safe step.", { compact: true });
    }
    ws.send(JSON.stringify({ type: "chat", content, timestamp: new Date().toISOString(), session_id: state.sessionId, visual_context: visualContext, metadata: { visual_context: visualContext, client_type: "gui" } }));
    addMessage("user", content, { gateway_sent: true, visual_context: visualContext });
    return true;
  }

  // Event wiring. Capture phase prevents older experimental form listeners from closing/resetting the modal.
  document.addEventListener("click", (ev) => {
    const opener = ev.target?.closest?.("#wolfGatewayOpenButton, [data-wolf-gateway-open]");
    if (opener) { ev.preventDefault(); ev.stopPropagation(); open(); return; }
    const closer = ev.target?.closest?.("#wolfGatewayCloseButton");
    if (closer) { ev.preventDefault(); ev.stopPropagation(); close(); return; }
    if (ev.target === els.overlay) { ev.preventDefault(); ev.stopPropagation(); close(); }
  }, true);

  renderPresetSelect(loadCachedPresets());

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") close();
    if (ev.ctrlKey && ev.shiftKey && String(ev.key || "").toLowerCase() === "g") { ev.preventDefault(); open(); }
  });

  els.form.addEventListener("submit", handleSubmit, true);
  els.authenticate?.addEventListener("click", handleSubmit, true);
  els.connectSessionButton?.addEventListener("click", handleConnectSession, true);
  els.local?.addEventListener("click", (ev) => { ev.preventDefault(); continueLocal(); }, true);
  els.refreshSessions?.addEventListener("click", (ev) => { ev.preventDefault(); refreshSessions(); }, true);
  els.createSession?.addEventListener("click", (ev) => { ev.preventDefault(); createSession(); }, true);
  els.showParams?.addEventListener("click", async (ev) => { ev.preventDefault(); try { await showParams(); } catch (error) { if (els.feedback) els.feedback.textContent = `Fetch agent params failed: ${error.message}`; } }, true);
  els.saveParams?.addEventListener("click", async (ev) => { ev.preventDefault(); try { await saveParams(); } catch (error) { if (els.feedback) els.feedback.textContent = `Commit agent params failed: ${error.message}`; } }, true);
  els.loadPresets?.addEventListener("click", async (ev) => { ev.preventDefault(); try { await loadAgentPresets(); } catch (error) { if (els.feedback) els.feedback.textContent = `Load presets failed: ${error.message}`; addMessage("system", `Load presets failed: ${error.message}`, { tone: "error" }); } }, true);
  els.applyPreset?.addEventListener("click", (ev) => { ev.preventDefault(); try { applyAgentPresetToForm(); } catch (error) { if (els.feedback) els.feedback.textContent = `Apply preset failed: ${error.message}`; } }, true);
  els.applyCommitPreset?.addEventListener("click", async (ev) => { ev.preventDefault(); try { await applyAndCommitAgentPreset(); } catch (error) { if (els.feedback) els.feedback.textContent = `Apply + Commit preset failed: ${error.message}`; } }, true);
  els.agentPresetSelect?.addEventListener("change", () => { try { window.localStorage?.setItem(PRESET_SELECTED_KEY, els.agentPresetSelect.value || ""); } catch (_) {} });
  els.showPolicy?.addEventListener("click", async (ev) => { ev.preventDefault(); try { await showPolicy(); } catch (error) { if (els.feedback) els.feedback.textContent = `Fetch policy params failed: ${error.message}`; } }, true);
  els.savePolicy?.addEventListener("click", async (ev) => { ev.preventDefault(); try { await savePolicy(); } catch (error) { if (els.feedback) els.feedback.textContent = `Commit policy params failed: ${error.message}`; } }, true);
  els.resetSession?.addEventListener("click", async (ev) => { ev.preventDefault(); try { await resetSession(); } catch (error) { if (els.feedback) els.feedback.textContent = `Reset failed: ${error.message}`; } }, true);
  els.agentPauseRun?.addEventListener("click", (ev) => { ev.preventDefault(); if (!sendGatewayControl("pause_after_step")) addMessage("system", "Gateway websocket is not open. Reconnect the selected session.", { tone: "error" }); }, true);
  els.agentResumeRun?.addEventListener("click", (ev) => { ev.preventDefault(); if (!sendGatewayControl("resume")) addMessage("system", "Gateway websocket is not open. Reconnect the selected session.", { tone: "error" }); }, true);
  els.agentStopRun?.addEventListener("click", (ev) => { ev.preventDefault(); if (!sendGatewayControl("stop_after_step")) addMessage("system", "Gateway websocket is not open. Reconnect the selected session.", { tone: "error" }); }, true);
  formControls().forEach((el) => el.addEventListener("change", syncRawFromForm));
  formControls().forEach((el) => el.addEventListener("input", () => { if (el !== els.cfgApiKey) syncRawFromForm(); }));

  els.sessionSelect?.addEventListener("change", () => {
    const nextSession = els.sessionSelect.value || "";
    if (nextSession !== state.sessionId && ws && ws.readyState <= 1) {
      intentionalClose = true;
      try { ws.close(); } catch (_) {}
      ws = null;
      state.phase = isAuthed() ? "authenticated" : "local";
    }
    state.sessionId = nextSession;
    saveState();
    render();
    if (els.feedback && nextSession) els.feedback.textContent = "Session selected. Click Connect Session.";
  });

  els.messageForm?.addEventListener("submit", (ev) => {
    if (state.phase !== "connected") return;
    const content = String(els.messageInput?.value || "").trim();
    if (!content) return;
    const visualContext = els.includeVisualContext?.checked && typeof window.wolfGuiCurrentVisualContext === "function" ? window.wolfGuiCurrentVisualContext() : {};
    ev.preventDefault();
    ev.stopImmediatePropagation();
    if (sendGatewayChat(content, visualContext)) {
      if (els.messageInput) els.messageInput.value = "";
    } else {
      addMessage("system", "Gateway websocket is not open. Reconnect the selected session.", { tone: "error" });
      state.phase = isAuthed() ? "authenticated" : "local";
      saveState();
      render();
    }
  }, true);

  window.WolfGatewayUI = { open, close, render, authenticate, refreshSessions, connectSession, showParams, saveParams, showPolicy, savePolicy, resetSession, applyParamsToForm, formToParams, syncRawFromForm, state: () => ({ ...state, token: state.token ? "***redacted***" : "" }), sendChat: sendGatewayChat };
  render();
  console.info("[wolf-gateway-ui] standalone TUI-parity gateway client installed");
})();


// gateway tabbed console controller
(function () {
  function ready(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn, { once: true });
    else fn();
  }
  ready(function () {
    const tabs = Array.from(document.querySelectorAll('[data-gateway-tab]'));
    const pages = Array.from(document.querySelectorAll('[data-gateway-panel]'));
    const sessionStep = document.getElementById('wolfGatewaySessionStep');
    const feedback = document.getElementById('wolfGatewayFeedback');
    if (!tabs.length || !pages.length) return;
    let active = 'connect';
    function authed() {
      return !!sessionStep && !sessionStep.classList.contains('wolf-gateway-hidden');
    }
    function setTab(name, opts) {
      opts = opts || {};
      if (name !== 'connect' && !authed()) {
        if (!opts.silent && feedback) feedback.textContent = 'Authenticate with the gateway before editing agent or policy parameters.';
        name = 'connect';
      }
      active = name;
      tabs.forEach(function (tab) {
        const on = tab.dataset.gatewayTab === name;
        tab.classList.toggle('is-active', on);
        tab.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      pages.forEach(function (page) {
        const on = page.dataset.gatewayPanel === name;
        page.hidden = !on;
        page.classList.toggle('is-active', on);
      });
    }
    function refreshLocks() {
      const ok = authed();
      tabs.forEach(function (tab) {
        if (tab.dataset.gatewayTab !== 'connect') tab.disabled = !ok;
      });
      if (!ok && active !== 'connect') setTab('connect', { silent: true });
    }
    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        setTab(tab.dataset.gatewayTab || 'connect');
        refreshLocks();
      });
    });
    if (sessionStep && window.MutationObserver) {
      new MutationObserver(refreshLocks).observe(sessionStep, { attributes: true, attributeFilter: ['class'] });
    }
    refreshLocks();
    setTab(active, { silent: true });
  });
})();


// Gateway orchestration Kanban controller
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const STORAGE_KEY = "wolfGatewayStateV3";
  const cols = [
    { id: "pending", title: "Pending", statuses: ["pending", "ready", "blocked"] },
    { id: "active", title: "Active", statuses: ["running", "waiting", "paused"] },
    { id: "done", title: "Done", statuses: ["completed"] },
    { id: "failed", title: "Failed", statuses: ["failed", "cancelled"] }
  ];
  let snapshot = null;
  let selectedTaskId = "";
  let taskDetails = {};
  let attachedSocket = null;

  function state() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); }
    catch (_) { return {}; }
  }
  function connected() {
    const ws = window.wolfGatewaySocket;
    return Boolean(ws && ws.readyState === WebSocket.OPEN && state().sessionId);
  }
  function esc(v) {
    return String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
  }
  function shortId(v) {
    const s = String(v || "");
    return s.length > 18 ? `${s.slice(0, 8)}…${s.slice(-6)}` : s;
  }
  function taskId(task) { return String(task?.id || task?.task_id || task?.node_id || ""); }
  function statusOf(task) { return String(task?.status || task?.state || "pending").toLowerCase() || "pending"; }
  function taskName(task) { return String(task?.spec?.name || task?.name || task?.title || task?.task_name || taskId(task) || "Task"); }
  function taskObjective(task) { return String(task?.spec?.objective || task?.objective || task?.description || task?._last_event_content || ""); }
  function tasks() { return Array.isArray(snapshot?.tasks) ? snapshot.tasks : []; }
  function agents() { return Array.isArray(snapshot?.agent_pool) ? snapshot.agent_pool : (Array.isArray(snapshot?.agents) ? snapshot.agents : []); }
  function setText(id, value) { const el = $(id); if (el) el.textContent = String(value ?? ""); }
  function setNotice(text, tone) {
    const el = $("wolfKanbanNotice");
    if (!el) return;
    el.textContent = text || "";
    el.className = `wolf-kanban-notice ${tone || ""}`.trim();
  }
  function sessionEndpoint(path) {
    const st = state();
    if (!st.gatewayUrl || !st.token || !st.sessionId) throw new Error("Connect a gateway session first.");
    const base = String(st.gatewayUrl).replace(/\/+$/, "");
    return `${base}/sessions/${encodeURIComponent(st.sessionId)}${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(st.token)}`;
  }
  async function taskHttp(method, taskIdValue, suffix = "", body = undefined) {
    const path = `/orchestration/tasks/${encodeURIComponent(taskIdValue)}${suffix}`;
    const options = { method, cache: "no-store", headers: { "Content-Type": "application/json" } };
    if (body !== undefined) options.body = JSON.stringify(body || {});
    const res = await fetch(sessionEndpoint(path), options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.error || `${res.status} ${res.statusText}`);
    return data;
  }
  function selectedTask() { return tasks().find((t) => taskId(t) === selectedTaskId) || null; }
  function selectedDetail() {
    const cached = taskDetails[selectedTaskId] || null;
    const detailId = cached?.task?.id || cached?.task_id;
    return detailId && detailId !== selectedTaskId ? null : cached;
  }
  function taskTextBlob(task, detail) { try { return JSON.stringify({ task, detail }).toLowerCase(); } catch (_) { return String(task || "").toLowerCase(); } }
  function taskHasGuiTimeout(task, detail) { return taskTextBlob(task, detail).includes("gui_command_timeout"); }
  function taskWaitingForGui(task, detail) {
    const blob = taskTextBlob(task, detail);
    return ["waiting", "blocked", "needs_user"].includes(statusOf(task)) && (blob.includes("waiting_for_gui_command_result") || blob.includes("gui_command_result"));
  }
  function taskHasFailedChildSummary(task, detail) {
    const summaries = detail?.child_summaries || task?.child_summaries || {};
    const blob = taskTextBlob(summaries, null);
    return blob.includes("task failed:") || blob.includes("task_failure") || blob.includes("failed_child") || blob.includes("blockers");
  }
  function taskBadges(task, detail) {
    const badges = [];
    if (taskWaitingForGui(task, detail)) badges.push(["waiting-gui", "Waiting for GUI"]);
    if (taskHasGuiTimeout(task, detail)) badges.push(["gui-timeout", "GUI timeout"]);
    if (taskHasFailedChildSummary(task, detail)) badges.push(["failed-child", "Failed child"]);
    if (task?.error || detail?.error) badges.push(["task-error-badge", "Error"]);
    return badges;
  }
  function renderTaskBadges(task, detail) {
    const badges = taskBadges(task, detail);
    return badges.length ? `<span class="wolf-task-badges">${badges.map(([cls, label]) => `<span class="wolf-task-badge ${esc(cls)}">${esc(label)}</span>`).join("")}</span>` : "";
  }
  function taskActionAllowed(action, task) {
    const s = statusOf(task);
    const terminal = ["completed", "done", "succeeded", "success", "failed", "cancelled", "canceled"].includes(s);
    if (!task || !selectedTaskId) return false;
    if (action === "pause") return ["queued", "ready", "pending", "created", "running", "active", "in_progress"].includes(s);
    if (action === "resume") return ["waiting", "paused", "blocked", "needs_user"].includes(s);
    if (action === "retry") return terminal;
    if (action === "cancel") return !terminal && s !== "unknown";
    if (action === "cancel_subtree") return s !== "unknown" && !["completed", "done", "succeeded", "success"].includes(s);
    if (action === "retry_subtree") return s !== "unknown";
    if (action === "replan") return s !== "unknown" && !["cancelled", "canceled"].includes(s);
    return true;
  }
  function updateTaskActionStates() {
    const task = selectedTask() || selectedDetail()?.task;
    document.querySelectorAll(".wolf-kanban-task-action").forEach((button) => {
      const action = button.getAttribute("data-wolf-task-action") || "";
      const allowed = taskActionAllowed(action, task);
      button.disabled = !allowed;
      button.classList.toggle("wolf-task-action-invalid", !allowed);
      button.title = allowed ? "" : (task ? `Action ${action} is not valid while task is ${statusOf(task)}.` : "Select a task first.");
    });
    const refreshDetail = $("wolfKanbanRefreshDetail");
    if (refreshDetail) refreshDetail.disabled = !selectedTaskId;
  }
  function renderMapSection(title, value) {
    const entries = value && typeof value === "object" ? Object.entries(value) : [];
    if (!entries.length) return "";
    return `<section class="wolf-detail-section"><h5>${esc(title)}</h5>${entries.map(([k, v]) => `<article><div class="wolf-summary-row-head"><strong>${esc(shortId(k))}</strong><button class="wolf-gateway-ghost wolf-detail-task-link" type="button" data-open-task-detail="${esc(k)}">Open task</button></div><p>${esc(typeof v === "string" ? v : JSON.stringify(v, null, 2))}</p></article>`).join("")}</section>`;
  }
  function renderListSection(title, value, limit = 6) {
    const items = Array.isArray(value) ? value.slice(-limit) : [];
    if (!items.length) return "";
    return `<section class="wolf-detail-section"><h5>${esc(title)}</h5>${items.map((v, i) => `<article><strong>${esc(String(v?.role || v?.kind || v?.type || `item ${i + 1}`))}</strong><p>${esc(typeof v === "string" ? v : JSON.stringify(v, null, 2))}</p></article>`).join("")}</section>`;
  }
  async function fetchTaskDetail(taskIdValue = selectedTaskId) {
    if (!taskIdValue) return null;
    const detail = await taskHttp("GET", taskIdValue);
    taskDetails[taskIdValue] = detail;
    selectedTaskId = taskIdValue;
    render();
    return detail;
  }
  function openTaskDetail(taskIdValue) {
    if (!taskIdValue) return;
    selectedTaskId = String(taskIdValue);
    render();
    fetchTaskDetail(selectedTaskId).catch((err) => setNotice(`Task detail failed: ${err.message || err}`, "error"));
  }
  async function taskAction(action) {
    const task = selectedTask() || selectedDetail()?.task;
    if (!selectedTaskId || !task) return setNotice("Select a task first.", "warning");
    if (!taskActionAllowed(action, task)) return setNotice(`Action ${action} is not valid while task is ${statusOf(task)}.`, "warning");
    const body = { reason: `${action} from WOLF GUI Kanban` };
    if (action === "replan") body.prompt = "Please reassess and replan this task using current child summaries, failures, blockers, artifacts, and remaining objective. Choose whether to create new subtasks, retry failed work, continue with caveats, ask the user, or complete.";
    if (action === "retry_subtree") body.include_completed = false;
    if (action === "cancel_subtree") body.include_root = true;
    await taskHttp("POST", selectedTaskId, `/${action}`, body);
    setNotice(`${action} requested for ${shortId(selectedTaskId)}.`);
    await refreshSnapshot({ quiet: true }).catch(() => {});
    await fetchTaskDetail(selectedTaskId).catch(() => {});
  }
  function graphPayload() {
    if (snapshot?.task_graph) return snapshot.task_graph;
    const list = tasks();
    const edges = [];
    list.forEach((task) => {
      const id = taskId(task);
      const spec = task.spec || {};
      if (spec.parent_id || task.parent_id) edges.push({ from: spec.parent_id || task.parent_id, to: id, kind: "parent_child" });
      (spec.dependencies || task.dependencies || []).forEach((dep) => edges.push({ from: dep, to: id, kind: "dependency" }));
      (task.waiting_on || []).forEach((child) => edges.push({ from: id, to: child, kind: "waiting_on" }));
    });
    return { nodes: list.map((task) => ({ id: taskId(task), name: taskName(task), status: statusOf(task), parent_id: task.spec?.parent_id || task.parent_id, depth: task.depth || 0, waiting_on: task.waiting_on || [] })), edges, roots: list.filter((task) => !(task.spec?.parent_id || task.parent_id)).map(taskId) };
  }
  function renderTaskGraph() {
    const el = $("wolfKanbanGraph");
    if (!el) return;
    const graph = graphPayload();
    const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
    const edges = Array.isArray(graph.edges) ? graph.edges : [];
    if (!nodes.length) { el.className = "wolf-kanban-graph wolf-kanban-empty"; el.innerHTML = "No graph loaded."; return; }
    el.className = "wolf-kanban-graph";
    const nodeHtml = nodes.map((node) => {
      const id = String(node.id || node.task_id || "");
      const task = tasks().find((t) => taskId(t) === id) || node;
      const depth = Math.min(8, Math.max(0, Number(node.depth || task.depth || 0)));
      const children = edges.filter((e) => e.from === id && e.kind !== "dependency").length;
      const deps = edges.filter((e) => e.to === id && e.kind === "dependency").length;
      return `<button type="button" class="wolf-graph-node status-${esc(String(node.status || statusOf(task)))}${id === selectedTaskId ? " is-selected" : ""}" style="--depth:${depth}" data-open-task-detail="${esc(id)}"><span><strong>${esc(node.name || taskName(task))}</strong><em>${esc(shortId(id))}</em></span><span class="wolf-kanban-status">${esc(String(node.status || statusOf(task)))}</span>${renderTaskBadges(task, taskDetails[id])}<small>${children} children · ${deps} deps</small></button>`;
    }).join("");
    const edgeHtml = edges.length ? `<div class="wolf-graph-edges"><h5>Edges</h5>${edges.slice(0, 80).map((e) => `<div class="wolf-graph-edge ${esc(e.kind || "edge")}"><span>${esc(shortId(e.from))} → ${esc(shortId(e.to))}</span><em>${esc(e.kind || "edge")}</em></div>`).join("")}${edges.length > 80 ? `<p>${edges.length - 80} more edges hidden.</p>` : ""}</div>` : '<div class="wolf-kanban-empty">No edges</div>';
    el.innerHTML = `<div class="wolf-graph-nodes">${nodeHtml}</div>${edgeHtml}`;
    el.querySelectorAll("[data-open-task-detail]").forEach((button) => button.addEventListener("click", () => openTaskDetail(button.getAttribute("data-open-task-detail") || "")));
  }
  function inferStatus(event) {
    const t = String(event?.event_type || event?.status || "").toLowerCase();
    if (t.includes("completed")) return "completed";
    if (t.includes("failed")) return "failed";
    if (t.includes("cancel")) return "cancelled";
    if (t.includes("started")) return "running";
    if (t.includes("waiting") || t.includes("input_requested")) return "waiting";
    if (t.includes("paused")) return "paused";
    if (t.includes("ready") || t.includes("resumed")) return "ready";
    if (t.includes("registered")) return "pending";
    return "";
  }
  function applySnapshot(next) {
    snapshot = { ...(next || {}), tasks: Array.isArray(next?.tasks) ? next.tasks : [] };
    if (!selectedTaskId && snapshot.last_task_id) selectedTaskId = String(snapshot.last_task_id);
    if (selectedTaskId && !tasks().some((t) => taskId(t) === selectedTaskId)) selectedTaskId = "";
    render();
  }
  function applyEvent(event) {
    const taskId = String(event?.task_id || "");
    if (!snapshot) snapshot = { type: "orchestration_snapshot", enabled: true, started: true, tasks: [], agent_pool: [], timestamp: new Date().toISOString() };
    snapshot.timestamp = event?.timestamp || event?.event_ts || new Date().toISOString();
    snapshot.last_event = event;
    if (!taskId) return render();
    const list = snapshot.tasks = Array.isArray(snapshot.tasks) ? snapshot.tasks : [];
    let task = list.find((t) => String(t.id || t.task_id || t.node_id || "") === taskId);
    if (!task) {
      task = { id: taskId, status: "pending", spec: { name: `Task ${shortId(taskId)}`, objective: "" }, created_at: snapshot.timestamp };
      list.push(task);
    }
    const inferred = inferStatus(event);
    const eventType = String(event?.event_type || event?.type || "");
    const shouldRefreshSnapshot = ["task_completed", "task_failed", "task_cancelled", "agent_released", "parent_resumed"].includes(eventType);
    if (!task && !inferred) {
      if (shouldRefreshSnapshot) requestLive({ quiet: true });
      return render();
    }
    if (inferred) task.status = inferred;
    if (shouldRefreshSnapshot) setTimeout(() => requestLive({ quiet: true }), 80);
    if (event?.actor) task.owner_agent = event.actor;
    if (event?.payload?.name) task.spec = { ...(task.spec || {}), name: event.payload.name };
    task.updated_at = snapshot.timestamp;
    task._last_event_type = event?.event_type || event?.type || "orchestration_event";
    task._last_event_content = event?.content || task._last_event_content || "";
    if (!selectedTaskId) selectedTaskId = taskId;
    render();
  }
  async function refreshSnapshot(options = {}) {
    const st = state();
    if (!st.gatewayUrl || !st.token || !st.sessionId) throw new Error("Connect a gateway session first.");
    const base = String(st.gatewayUrl).replace(/\/+$/, "");
    const url = `${base}/sessions/${encodeURIComponent(st.sessionId)}/orchestration/snapshot?token=${encodeURIComponent(st.token)}`;
    const res = await fetch(url, { cache: "no-store" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.error || `${res.status} ${res.statusText}`);
    applySnapshot(data);
    if (!options.quiet) setNotice("Snapshot refreshed.");
    return data;
  }
  function requestLive(options = {}) {
    const ws = window.wolfGatewaySocket;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify({ type: "orchestration_snapshot_request", timestamp: new Date().toISOString() }));
    if (!options.quiet) setNotice("Requested live orchestration snapshot.");
    return true;
  }
  function renderDetail(list) {
    const snap = list.find((t) => taskId(t) === selectedTaskId);
    const detail = selectedDetail();
    const task = detail?.task || snap;
    const body = $("wolfKanbanDetailBody");
    setText("wolfKanbanDetailTitle", task ? `${taskName(task)} · ${shortId(taskId(task) || selectedTaskId)}` : "No task selected");
    if (!body) return;
    if (!task) { body.textContent = list.length ? "Select a card to inspect its current snapshot fields." : "No orchestration tasks are available yet."; updateTaskActionStates(); return; }
    const id = taskId(task) || selectedTaskId;
    const spec = task.spec || {};
    const parentId = spec.parent_id || task.parent_id || task.parent_task_id || "";
    const parent = parentId ? `<section class="wolf-detail-section"><h5>Parent task</h5><div class="wolf-summary-row-head"><strong>${esc(shortId(parentId))}</strong><button class="wolf-gateway-ghost wolf-detail-task-link" type="button" data-open-task-detail="${esc(parentId)}">Open parent</button></div></section>` : "";
    const rawDetail = detail ? `<details><summary>Full task detail JSON</summary><pre>${esc(JSON.stringify(detail, null, 2))}</pre></details>` : '<p class="wolf-kanban-muted">Live detail has not been fetched yet. Click Refresh detail.</p>';
    const rawSnapshot = snap ? `<details><summary>Snapshot JSON</summary><pre>${esc(JSON.stringify(snap, null, 2))}</pre></details>` : "";
    body.innerHTML = `<div class="wolf-task-detail-head"><span class="wolf-kanban-status">${esc(statusOf(task))}</span><span>${esc(shortId(id))}</span><span>${esc(task.owner_agent_name || task.owner_agent || "unassigned")}</span>${renderTaskBadges(task, detail)}</div>${task.error || detail?.error ? `<div class="wolf-task-error">${esc(task.error || detail?.error)}</div>` : ""}${parent}<section class="wolf-detail-section"><h5>Objective</h5><p>${esc(spec.objective || taskObjective(task) || "No objective")}</p></section>${renderMapSection("Child summaries", detail?.child_summaries || task.child_summaries)}${renderMapSection("Dependency summaries", detail?.dependency_summaries)}${renderListSection("Artifacts", detail?.artifacts)}${renderListSection("Recent local messages", detail?.local_messages, 5)}${renderListSection("Recent events", detail?.events, 5)}${renderListSection("Compressed history", detail?.compressed_history, 5)}<div class="wolf-detail-note">Use Replan, Retry subtree, or Cancel subtree after reviewing failures, child summaries, artifacts, and graph relationships.</div>${rawDetail}${rawSnapshot}`;
    body.querySelectorAll("[data-open-task-detail]").forEach((button) => button.addEventListener("click", () => openTaskDetail(button.getAttribute("data-open-task-detail") || "")));
    updateTaskActionStates();
  }
  function render() {
    const list = tasks();
    const pool = agents();
    const active = new Set(["ready", "running", "waiting", "paused", "blocked"]);
    setText("wolfKanbanMetricTasks", list.length);
    setText("wolfKanbanMetricActive", list.filter((t) => active.has(statusOf(t))).length);
    setText("wolfKanbanMetricDone", list.filter((t) => statusOf(t) === "completed").length);
    setText("wolfKanbanMetricAgents", pool.length);
    setText("wolfKanbanSubtitle", !connected() ? "Connect a gateway session to load orchestration state." : (!snapshot ? "Connected. Refresh or request a live orchestration snapshot." : `${snapshot.enabled ? "Enabled" : "Disabled"} · ${snapshot.started ? "runtime started" : "runtime stopped"} · adapter ${snapshot.adapter || "unknown"}`));
    const refresh = $("wolfKanbanRefresh"), live = $("wolfKanbanRequestLive");
    if (refresh) refresh.disabled = !connected();
    if (live) live.disabled = !connected();
    if (!snapshot) setNotice(connected() ? "No orchestration snapshot loaded yet." : "Connect a session to inspect orchestration tasks.");
    else if (!snapshot.enabled) setNotice("Orchestration is disabled for this session. Enable it in Agent parameters and reconnect.", "warning");
    else setNotice(`Last update: ${snapshot.timestamp || "unknown"}`);
    const board = $("wolfKanbanBoard");
    if (!board) return renderDetail(list);
    board.innerHTML = cols.map((col) => {
      const colTasks = list.filter((t) => col.statuses.includes(statusOf(t)) || (col.id === "pending" && !cols.some((c) => c.statuses.includes(statusOf(t)))));
      const cards = colTasks.map((task) => {
        const id = taskId(task);
        const status = statusOf(task);
        const owner = task.owner_agent || task.owner_agent_name || task.leased_agent_name || "unassigned";
        return `<button type="button" class="wolf-kanban-card status-${esc(status)}${id === selectedTaskId ? " is-selected" : ""}" data-task-id="${esc(id)}"><span class="wolf-kanban-card-head"><strong>${esc(taskName(task))}</strong><em>${esc(shortId(id))}</em></span><span class="wolf-kanban-card-objective">${esc(taskObjective(task)).slice(0, 260) || "No objective in snapshot."}</span><span class="wolf-kanban-card-foot"><span class="wolf-kanban-status">${esc(status)}</span><span>${esc(owner)}</span></span>${renderTaskBadges(task, taskDetails[id])}</button>`;
      }).join("");
      return `<section class="wolf-kanban-column" data-column="${esc(col.id)}"><header><span>${esc(col.title)}</span><strong>${colTasks.length}</strong></header><div class="wolf-kanban-column-cards">${cards || '<div class="wolf-kanban-empty">No tasks</div>'}</div></section>`;
    }).join("");
    board.querySelectorAll(".wolf-kanban-card[data-task-id]").forEach((card) => card.addEventListener("click", () => openTaskDetail(String(card.getAttribute("data-task-id") || ""))));
    renderTaskGraph();
    renderDetail(list);
    updateTaskActionStates();
  }
  function attachSocket() {
    const ws = window.wolfGatewaySocket;
    if (!ws || ws === attachedSocket) return;
    attachedSocket = ws;
    ws.addEventListener("message", (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg?.type === "orchestration_snapshot") applySnapshot(msg);
        else if (msg?.type === "orchestration_event") applyEvent(msg);
      } catch (_) {}
    });
  }
  function wire() {
    $("wolfKanbanRefresh")?.addEventListener("click", async (ev) => { ev.preventDefault(); try { await refreshSnapshot(); } catch (err) { setNotice(`Snapshot refresh failed: ${err.message || err}`, "error"); } }, true);
    $("wolfKanbanRequestLive")?.addEventListener("click", (ev) => { ev.preventDefault(); if (!requestLive()) setNotice("Gateway websocket is not open. Reconnect the selected session.", "error"); }, true);
    $("wolfKanbanRefreshDetail")?.addEventListener("click", async (ev) => { ev.preventDefault(); try { await fetchTaskDetail(); } catch (err) { setNotice(`Task detail refresh failed: ${err.message || err}`, "error"); } }, true);
    document.querySelectorAll(".wolf-kanban-task-action").forEach((button) => button.addEventListener("click", async (ev) => { ev.preventDefault(); try { await taskAction(button.getAttribute("data-wolf-task-action") || ""); } catch (err) { setNotice(`Task action failed: ${err.message || err}`, "error"); } }, true));
    const ui = window.WolfGatewayUI;
    if (ui && typeof ui.connectSession === "function" && !ui.__kanbanWrapped) {
      const original = ui.connectSession;
      ui.connectSession = function () { const out = original.apply(this, arguments); setTimeout(() => { attachSocket(); requestLive({ quiet: true }); }, 350); return out; };
      ui.__kanbanWrapped = true;
      ui.refreshOrchestrationSnapshot = refreshSnapshot;
      ui.requestOrchestrationSnapshot = requestLive;
      ui.orchestrationKanbanSnapshot = () => snapshot;
    }
    attachSocket();
    render();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire, { once: true }); else wire();
  setInterval(() => { attachSocket(); render(); }, 2500);
  window.WolfGatewayKanban = { refreshSnapshot, requestLive, snapshot: () => snapshot, render };
})();
