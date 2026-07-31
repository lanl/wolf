
(() => {
  "use strict";

  const STORAGE_KEY = "wolfGatewayStateV3";
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
      els.cfgCtxWindow, els.cfgActionPolicy, els.cfgEnableWrite, els.cfgEnableSyscall,
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

    if (state.phase === "connected") setStatus("connected", `Gateway connected · ${state.accountId}/${state.sessionId}`);
    else if (state.phase === "connecting") setStatus("testing", "Gateway connecting…");
    else if (state.phase === "error") setStatus("error", `Gateway error · ${state.lastError || "local workspace active"}`);
    else if (authed) setStatus("", `Authenticated · ${state.accountId}${state.sessionId ? ` · selected ${state.sessionId}` : ""}`);
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

  function collectWorkspaceCaptureUrls(payload = {}) {
    const urls = Array.isArray(payload.urls) ? payload.urls.slice() : [];
    if (urls.length) return urls;
    try {
      const vc = typeof window.wolfGuiCurrentVisualContext === "function" ? window.wolfGuiCurrentVisualContext() : {};
      const panels = [];
      if (Array.isArray(vc?.dashboard_panels)) panels.push(...vc.dashboard_panels);
      if (Array.isArray(vc?.dashboard?.panels)) panels.push(...vc.dashboard.panels);
      if (Array.isArray(vc?.active_dashboard?.panels)) panels.push(...vc.active_dashboard.panels);
      for (const panel of panels) {
        const url = panel?.url || panel?.iframe?.src || panel?.iframe?.url;
        if (url && !urls.includes(url)) urls.push(url);
      }
    } catch (_) {}
    return urls;
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
        if (!Array.isArray(body.urls) || !body.urls.length) body.urls = collectWorkspaceCaptureUrls(payload);
        if (!body.visual_context && typeof window.wolfGuiCurrentVisualContext === "function") body.visual_context = window.wolfGuiCurrentVisualContext();
        result = await httpJson("POST", `/api/gui/capture/workspace?session_id=${encodeURIComponent(state.sessionId || "default")}`, body);
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
      ws?.send(JSON.stringify({ type: "gui_command_result", command_id: commandId, ok: true, result, content: `GUI command completed: ${action}` }));
      addMessage("system", `GUI command completed: ${action}`, { gateway_event: event, compact: true });
    } catch (err) {
      ws?.send(JSON.stringify({ type: "gui_command_result", command_id: commandId, ok: false, error: String(err?.message || err), content: `GUI command failed: ${action}` }));
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
    if (type === "workflow_status") return addMessage("system", `Workflow: ${content || event.status || "status"}`, { gateway_event: event, compact: true });
    if (type === "policy_resolved") return addMessage("system", `Policy resolved: ${event.action_policy || "limited"}`, { gateway_event: event, compact: true });
    if (type === "gui_route_resolved") return addMessage("system", content || `GUI route: ${event.route || "auto"}`, { gateway_event: event, compact: true });
    if (type === "gui_command") { executeGatewayGuiCommand(event); return; }
    if (type === "gui_command_result") return addMessage("system", content || `GUI command result: ${event.ok ? "ok" : "failed"}`, { gateway_event: event, compact: true });
    if (type === "workflow_action") return addMessage("system", content || `Action: ${event.action || event.payload?.action || "action"}`, { gateway_event: event, card: true });
    if (type === "workflow_result") {
      if (event.action === "send_message") return addMessage("assistant", content, { gateway_event: event });
      return addMessage("system", `Result: ${event.action || "action"} — ${content || "completed"}`, { gateway_event: event, card: true });
    }
    if (type === "presence") return addMessage("system", content || "Presence updated.", { gateway_event: event, compact: true });
    if (type === "participant_message") return addMessage("assistant", content, { gateway_event: event });
    return addMessage("system", content || `Gateway event: ${type}`, { gateway_event: event });
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
            cross_origin_iframe_pixels: agentCaptureAllowedForGateway() ? "backend_capture_action_available" : false,
            same_origin_iframe_dom_excerpt: "best_effort"
          },
          timestamp: new Date().toISOString()
        }));
      } catch (_) {}
      Promise.allSettled([showParams(), showPolicy()]).then(() => {
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
  els.showPolicy?.addEventListener("click", async (ev) => { ev.preventDefault(); try { await showPolicy(); } catch (error) { if (els.feedback) els.feedback.textContent = `Fetch policy params failed: ${error.message}`; } }, true);
  els.savePolicy?.addEventListener("click", async (ev) => { ev.preventDefault(); try { await savePolicy(); } catch (error) { if (els.feedback) els.feedback.textContent = `Commit policy params failed: ${error.message}`; } }, true);
  els.resetSession?.addEventListener("click", async (ev) => { ev.preventDefault(); try { await resetSession(); } catch (error) { if (els.feedback) els.feedback.textContent = `Reset failed: ${error.message}`; } }, true);
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
