const state = {
  workspace: { mode: 'browser', url: 'about:blank' },
  dockMode: 'float',
  annotations: [],
  messages: [],
  approvals: [],
  showSystemMessages: true,
  chatScale: 1.0,
  hiddenSystemAlertSignature: '',
  hiddenSystemAlertInitialized: false,
  workflows: {},
  sessions: [],
  apps: [],
  dashboards: [],
  dashboardActivity: {},
  annotateMode: null,
  dashboardFloat: false,
  latestSeq: 0,
};
window.wolfGuiState = state;


const $ = (id) => document.getElementById(id);

const els = {
  frame: $('workspace-frame'),
  dashboard: $('dashboard-workspace'),
  blank: $('blank-workspace'),
  layer: $('annotation-layer'),
  preview: $('draw-preview'),
  toolbarMode: $('workspace-mode'),
  urlInput: $('workspace-url'),
  openUrlForm: $('open-url-form'),
  workspaceBack: $('workspace-back'),
  workspaceForward: $('workspace-forward'),
  workspaceRefresh: $('workspace-refresh'),
  workspaceOpenExternal: $('workspace-open-external'),
  toggleAnnotate: $('toggle-annotate'),
  toggleRect: $('toggle-rect'),
  dockModeToolbar: $('dock-mode'),
  dockModePanel: $('dock-mode-panel'),
  clearAnnotations: $('clear-annotations'),
  dashboardFloatToggle: $('dashboard-float-toggle'),
  dashboardSnapGrid: $('dashboard-snap-grid'),
  dashboardSwitcher: $('dashboard-switcher'),
  healthDot: $('gui-health-dot'),
  panel: $('agent-panel'),
  panelHeader: $('agent-panel-header'),
  panelBody: $('agent-panel-body'),
  panelTab: $('panel-tab'),
  panelResize: $('agent-panel-resize'),
  panelResizeHandles: Array.from(document.querySelectorAll('.agent-panel-resize')),
  topToolbar: $('top-toolbar'),
  toolbarToggle: $('toolbar-toggle'),
  toolbarControls: $('toolbar-controls'),
  collapsePanel: $('collapse-panel'),
  refreshState: $('refresh-state'),
  messages: $('messages'),
  toggleSystemMessages: $('toggle-system-messages'),
  chatScaleDown: $('chat-scale-down'),
  chatScaleUp: $('chat-scale-up'),
  chatScaleLabel: $('chat-scale-label'),
  chatScaleReset: $('chat-scale-reset'),
  messageForm: $('message-form'),
  messageInput: $('message-input'),
  includeVisualContext: $('include-visual-context'),
  allowAgentInspect: $('allow-agent-inspect'),
  allowAgentCapture: $('allow-agent-capture'),
  workspaceSummary: $('workspace-summary'),
  workflowCount: $('workflow-count'),
  sessionCount: $('session-count'),
  toast: $('toast'),
  approvalPanel: $('approval-panel'),
  approvalList: $('approval-list'),
  approvalEmpty: $('approval-empty'),
  approvalCount: $('approval-count'),
  approvalRefresh: $('approval-refresh'),
};

const UI_PREFS_KEY = 'wolf.gui.uiPrefs.v2';

function loadUiPrefs() {
  try {
    const raw = window.localStorage?.getItem(UI_PREFS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (_) {
    return {};
  }
}

function saveUiPrefs() {
  try {
    window.localStorage?.setItem(UI_PREFS_KEY, JSON.stringify(state.uiPrefs || {}));
  } catch (_) {
    // Best-effort preference persistence only.
  }
}

function updateUiPrefs(patch = {}) {
  state.uiPrefs = { ...(state.uiPrefs || {}), ...(patch || {}) };
  saveUiPrefs();
}

state.uiPrefs = loadUiPrefs();
state.toolbarSize = state.uiPrefs.toolbarSize || 'toolbar-mid';
state.dockMode = state.uiPrefs.dockMode || state.dockMode || 'float';
state.dashboardLayouts = state.uiPrefs.dashboardLayouts || {};
state.dashboardFloatById = state.uiPrefs.dashboardFloatById || {};
state.dashboardActivity = state.uiPrefs.dashboardActivity || {};
state.showSystemMessages = state.uiPrefs.showSystemMessages !== false;
state.chatScale = Number.isFinite(Number(state.uiPrefs.chatScale)) ? Number(state.uiPrefs.chatScale) : 1.0;

state.attachWorkspaceView = state.uiPrefs.attachWorkspaceView !== undefined ? Boolean(state.uiPrefs.attachWorkspaceView) : true;
state.allowAgentInspect = Boolean(state.uiPrefs.allowAgentInspect);
state.allowAgentCapture = Boolean(state.uiPrefs.allowAgentCapture);


function clampChatScale(value) {
  const scale = Number(value);
  if (!Number.isFinite(scale)) return 1.0;
  return Math.max(0.75, Math.min(1.6, Math.round(scale * 1000) / 1000));
}

function chatScaleLabel(scale = state.chatScale) {
  return `${Math.round(clampChatScale(scale) * 100)}%`;
}

function applyChatScale(scale = state.chatScale, options = {}) {
  const next = clampChatScale(scale);
  state.chatScale = next;
  const target = els.panel || document.documentElement;
  const setPx = (name, value) => target.style.setProperty(name, `${Math.round(value * 10) / 10}px`);
  target.style.setProperty('--chat-scale', String(next));
  setPx('--chat-font-size', 13 * next);
  setPx('--chat-compact-font-size', 12 * next);
  setPx('--chat-meta-font-size', 11 * next);
  setPx('--chat-detail-font-size', 11 * next);
  setPx('--chat-bubble-pad-y', 10 * next);
  setPx('--chat-bubble-pad-x', 12 * next);
  setPx('--chat-compact-pad-y', 7 * next);
  setPx('--chat-compact-pad-x', 10 * next);
  setPx('--chat-message-gap', 10 * next);
  setPx('--chat-message-padding-y', 14 * next);
  setPx('--chat-message-padding-x', 12 * next);
  setPx('--chat-composer-font-size', 13 * next);
  setPx('--chat-composer-padding', 10 * next);
  setPx('--chat-toolbar-font-size', 11 * next);
  if (els.chatScaleLabel) {
    els.chatScaleLabel.textContent = chatScaleLabel(next);
    els.chatScaleLabel.title = `Chat content size ${chatScaleLabel(next)} — click to reset`;
  }
  if (options.persist !== false) updateUiPrefs({ chatScale: next });
  return next;
}

async function setChatScale(scale, options = {}) {
  const next = clampChatScale(scale);
  if (options.localOnly) {
    applyChatScale(next);
    return { chat_scale: next };
  }
  const data = await api('/api/gui/chat_scale', {
    method: 'POST',
    body: JSON.stringify({ scale: next, source: options.source || 'user' }),
  });
  applyChatScale(data?.chat_scale ?? data?.result?.chat_scale ?? next);
  showToast(`Chat size ${chatScaleLabel()}`, 1500);
  return data;
}

async function adjustChatScale(delta, options = {}) {
  const amount = Number.isFinite(Number(delta)) ? Number(delta) : 0.1;
  try {
    const data = await api('/api/gui/chat_scale_delta', {
      method: 'POST',
      body: JSON.stringify({ delta: amount, source: options.source || 'user' }),
    });
    applyChatScale(data?.chat_scale ?? data?.result?.chat_scale ?? (state.chatScale + amount));
    showToast(`Chat size ${chatScaleLabel()}`, 1500);
    return data;
  } catch (error) {
    applyChatScale(state.chatScale + amount);
    showToast(`Chat size ${chatScaleLabel()} (local)`, 1500);
    return { ok: false, error: error.message, chat_scale: state.chatScale };
  }
}

async function resetChatScale(options = {}) {
  try {
    const data = await api('/api/gui/chat_scale_reset', {
      method: 'POST',
      body: JSON.stringify({ source: options.source || 'user' }),
    });
    applyChatScale(data?.chat_scale ?? data?.result?.chat_scale ?? 1.0);
    showToast('Chat size reset to 100%', 1500);
    return data;
  } catch (error) {
    applyChatScale(1.0);
    showToast('Chat size reset locally', 1500);
    return { ok: false, error: error.message, chat_scale: state.chatScale };
  }
}

window.wolfGuiSetChatScale = (scale) => setChatScale(scale, { source: 'script' });
window.wolfGuiAdjustChatScale = (delta) => adjustChatScale(delta, { source: 'script' });
window.wolfGuiResetChatScale = () => resetChatScale({ source: 'script' });

function syncComposerContextControls(options = {}) {
  const attach = Boolean(state.attachWorkspaceView);
  const inspect = Boolean(state.allowAgentInspect);
  const capture = Boolean(state.allowAgentCapture);
  if (els.includeVisualContext) {
    els.includeVisualContext.checked = attach;
    els.includeVisualContext.closest('.composer-toggle')?.classList.toggle('is-active', attach);
  }
  if (els.allowAgentInspect) {
    els.allowAgentInspect.checked = inspect;
    els.allowAgentInspect.closest('.composer-toggle')?.classList.toggle('is-active', inspect);
  }
  if (els.allowAgentCapture) {
    els.allowAgentCapture.checked = capture;
    els.allowAgentCapture.closest('.composer-toggle')?.classList.toggle('is-active', capture);
  }
  if (options.toast) {
    const bits = [];
    bits.push(inspect ? 'inspection allowed' : 'inspection disabled');
    bits.push(capture ? 'capture allowed' : 'capture disabled');
    showToast(`Agent ${bits.join('; ')}`, 1700);
  }
}

function agentInspectAllowed() {
  return Boolean(els.allowAgentInspect?.checked);
}

function agentCaptureAllowed() {
  return Boolean(els.allowAgentCapture?.checked);
}

window.wolfGuiAgentInspectAllowed = agentInspectAllowed;
window.wolfGuiAgentCaptureAllowed = agentCaptureAllowed;

function dashboardIdForPrefs(dashboard = activeDashboard()) {
  return String(dashboard?.id || dashboard?.name || 'default_dashboard');
}

function dashboardPanelKey(panel, index) {
  return String(panel?.id || panel?.panel_id || panel?.name || panel?.title || index);
}

function getDashboardFloat(dashboard = activeDashboard()) {
  const id = dashboardIdForPrefs(dashboard);
  return Boolean((state.dashboardFloatById || {})[id]);
}

function setDashboardFloat(dashboard, enabled) {
  const id = dashboardIdForPrefs(dashboard);
  state.dashboardFloatById = { ...(state.dashboardFloatById || {}), [id]: Boolean(enabled) };
  state.dashboardFloat = Boolean(enabled);
  updateUiPrefs({ dashboardFloatById: state.dashboardFloatById });
}

function getDashboardPanelLayout(dashboard, panel, index) {
  const id = dashboardIdForPrefs(dashboard);
  const key = dashboardPanelKey(panel, index);
  return ((state.dashboardLayouts || {})[id] || {})[key] || null;
}

function saveDashboardPanelLayout(card) {
  const dashboard = activeDashboard();
  if (!dashboard || !card) return;
  const index = Number(card.dataset.panelIndex || 0);
  const panel = (dashboard.panels || [])[index] || {};
  const id = dashboardIdForPrefs(dashboard);
  const key = dashboardPanelKey(panel, index);
  const layout = {
    left: card.style.left || `${card.offsetLeft}px`,
    top: card.style.top || `${card.offsetTop}px`,
    width: card.style.width || `${card.offsetWidth}px`,
    height: card.style.height || `${card.offsetHeight}px`,
    zIndex: card.style.zIndex || '',
  };
  state.dashboardLayouts = {
    ...(state.dashboardLayouts || {}),
    [id]: { ...((state.dashboardLayouts || {})[id] || {}), [key]: layout },
  };
  updateUiPrefs({ dashboardLayouts: state.dashboardLayouts });
}

function snapDashboardPanelsToGrid() {
  const dashboard = activeDashboard();
  const grid = els.dashboard?.querySelector('.dashboard-grid.is-floating');
  if (!dashboard || !grid) return;
  const gridWidth = Math.max(320, grid.clientWidth || window.innerWidth);
  const cols = Math.max(1, Math.min(3, Math.floor(gridWidth / 360)));
  const gap = 14;
  const width = Math.max(280, Math.floor((gridWidth - gap * (cols + 1)) / cols));
  const height = Math.max(240, Math.min(420, Math.floor(window.innerHeight * 0.36)));
  Array.from(grid.querySelectorAll('.dashboard-panel')).forEach((card, index) => {
    const col = index % cols;
    const row = Math.floor(index / cols);
    card.style.left = `${gap + col * (width + gap)}px`;
    card.style.top = `${gap + row * (height + gap)}px`;
    card.style.width = `${width}px`;
    card.style.height = `${height}px`;
    card.style.zIndex = String(20 + index);
    saveDashboardPanelLayout(card);
  });
  updateDashboardToolbar();
  showToast('Dashboard panels snapped to grid', 1800);
}

function persistAgentPanelPrefs() {
  if (!els.panel) return;
  const prefs = {
    dockMode: state.dockMode || 'float',
    left: els.panel.style.left || '',
    top: els.panel.style.top || '',
    right: els.panel.style.right || '',
    bottom: els.panel.style.bottom || '',
    width: els.panel.style.width || '',
    height: els.panel.style.height || '',
    dockSideSize: document.documentElement.style.getPropertyValue('--agent-dock-side-size') || els.panel.style.getPropertyValue('--agent-dock-side-size') || '',
    dockBlockSize: document.documentElement.style.getPropertyValue('--agent-dock-block-size') || els.panel.style.getPropertyValue('--agent-dock-block-size') || '',
  };
  updateUiPrefs({ dockMode: prefs.dockMode, agentPanel: prefs });
}

function restoreAgentPanelPrefs() {
  const prefs = (state.uiPrefs || {}).agentPanel || {};
  if (prefs.dockSideSize) {
    document.documentElement.style.setProperty('--agent-dock-side-size', prefs.dockSideSize);
    els.panel?.style.setProperty('--agent-dock-side-size', prefs.dockSideSize);
  }
  if (prefs.dockBlockSize) {
    document.documentElement.style.setProperty('--agent-dock-block-size', prefs.dockBlockSize);
    els.panel?.style.setProperty('--agent-dock-block-size', prefs.dockBlockSize);
  }
  applyDockMode((state.uiPrefs || {}).dockMode || prefs.dockMode || 'float', { persist: false });
  if ((state.dockMode || 'float') === 'float' && els.panel) {
    ['left', 'top', 'right', 'bottom', 'width', 'height'].forEach((key) => {
      if (prefs[key]) els.panel.style[key] = prefs[key];
    });
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(data?.error || `HTTP ${response.status}`);
  }
  return data;
}

function showToast(message, ms = 2200) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    els.toast.hidden = true;
  }, ms);
}


function requestDisplayValue(request, key) {
  const value = request?.[key];
  if (value === undefined || value === null || value === '') return '';
  if (typeof value === 'object') {
    try { return JSON.stringify(value, null, 2); } catch (_) { return String(value); }
  }
  return String(value);
}


function addApprovalRequest(request = {}) {
  const req = { ...(request || {}) };
  const requestId = String(req.id || req.request_id || '');
  if (!requestId) return null;
  req.id = requestId;
  req.request_id = requestId;
  req.status = req.status || 'pending';
  state.approvals = (state.approvals || []).filter((r) => String(r.id || r.request_id) !== requestId);
  state.approvals.unshift(req);
  renderApprovals();
  return req;
}

function resolveApprovalRequest(requestId, patch = {}) {
  const id = String(requestId || '');
  if (!id) return;
  state.approvals = (state.approvals || []).filter((r) => String(r.id || r.request_id) !== id);
  renderApprovals();
}

function renderApprovals() {
  const panel = els.approvalPanel;
  const list = els.approvalList;
  if (!panel || !list) return;
  const pending = (state.approvals || []).filter((r) => String(r.status || 'pending') === 'pending');
  panel.hidden = pending.length === 0;
  list.innerHTML = '';
  if (els.approvalCount) els.approvalCount.textContent = `${pending.length} pending`;
  if (els.approvalEmpty) els.approvalEmpty.hidden = pending.length !== 0;

  pending.forEach((request) => {
    const card = document.createElement('article');
    card.className = 'approval-card';
    const title = document.createElement('div');
    title.className = 'approval-card-title';
    title.textContent = `${request.kind || request.action || 'permission'} · ${request.id || request.request_id || ''}`;
    const details = document.createElement('pre');
    details.className = 'approval-card-details';
    const summary = {
      action: request.action,
      kind: request.kind,
      command: request.command_display,
      target_path: request.target_path,
      operation: request.operation,
      shell: request.shell,
      timeout: request.timeout,
      cwd: request.cwd,
      purpose: request.purpose,
      expectations: request.expectations,
      risk_hints: request.risk_hints,
      payload_summary: request.payload_summary,
      payload: request.payload,
    };
    try { details.textContent = JSON.stringify(summary, null, 2); }
    catch (_) { details.textContent = String(summary); }

    const reason = document.createElement('input');
    reason.type = 'text';
    reason.className = 'approval-reason';
    reason.placeholder = 'Optional reason/feedback';

    const actions = document.createElement('div');
    actions.className = 'approval-card-actions';
    const once = document.createElement('button');
    once.type = 'button';
    once.textContent = 'Approve once';
    const session = document.createElement('button');
    session.type = 'button';
    session.textContent = 'Approve session';
    const deny = document.createElement('button');
    deny.type = 'button';
    deny.className = 'danger';
    deny.textContent = 'Deny';
    const requestId = String(request.id || request.request_id || '');
    once.addEventListener('click', () => decideApproval(requestId, true, false, reason.value || 'approved once from Wolf GUI'));
    session.addEventListener('click', () => decideApproval(requestId, true, true, reason.value || 'approved for session from Wolf GUI'));
    deny.addEventListener('click', () => decideApproval(requestId, false, false, reason.value || 'denied from Wolf GUI'));
    actions.appendChild(once);
    actions.appendChild(session);
    actions.appendChild(deny);

    card.appendChild(title);
    card.appendChild(details);
    card.appendChild(reason);
    card.appendChild(actions);
    list.appendChild(card);
  });
}

async function refreshApprovals() {
  try {
    const data = await api('/api/gui/approvals');
    const serverRequests = Array.isArray(data?.requests) ? data.requests : [];

    // Local WOLF GUI can also receive Gateway websocket permission requests.
    // Those requests are not owned by the local /api/gui/approvals HTTP queue,
    // so a periodic HTTP refresh must not replace/drop them.  Dropping them
    // made the permission card flash briefly, disappear, and leave the agent
    // blocked waiting for a decision that the user could no longer submit.
    const gatewayPending = (state.approvals || []).filter((r) => {
      const status = String(r?.status || 'pending');
      return Boolean(r?.gateway_permission) && status === 'pending';
    });
    const seen = new Set(serverRequests.map((r) => String(r?.id || r?.request_id || '')));
    const mergedGateway = gatewayPending.filter((r) => !seen.has(String(r?.id || r?.request_id || '')));
    state.approvals = [...mergedGateway, ...serverRequests];
    renderApprovals();
  } catch (_) {
    // Older/minimal GUI runtimes may not expose approval endpoints.
  }
}

async function decideApproval(requestId, approved, approveForSession = false, reason = '') {
  if (!requestId) return;
  const id = String(requestId);
  const existing = (state.approvals || []).find((r) => String(r.id || r.request_id) === id) || {};

  // Gateway websocket permission requests are not owned by the local GUI HTTP
  // approval queue. Send the decision back over the gateway websocket so the
  // worker-thread PermissionManager future can unblock immediately.
  if (existing.gateway_permission) {
    const sent = Boolean(window.WolfGatewayUI?.sendPermissionDecision?.({
      request_id: id,
      kind: existing.kind,
      action: existing.action,
      approved: Boolean(approved),
      approve_for_session: Boolean(approveForSession),
      reason,
      feedback: reason,
    }));
    if (sent) {
      resolveApprovalRequest(id);
      showToast(`Gateway permission ${approved ? 'approved' : 'denied'}`, 1800);
    } else {
      showToast('Gateway websocket is not open; reconnect the selected session.', 4200);
    }
    return;
  }

  try {
    const data = await api(`/api/gui/approvals/${encodeURIComponent(id)}/decision`, {
      method: 'POST',
      body: JSON.stringify({ approved: Boolean(approved), approve_for_session: Boolean(approveForSession), reason, feedback: reason }),
    });
    const request = data?.request;
    if (request?.id) {
      state.approvals = (state.approvals || []).filter((r) => String(r.id || r.request_id) !== String(request.id || request.request_id));
    }
    renderApprovals();
    showToast(`Permission ${approved ? 'approved' : 'denied'}`, 1800);
  } catch (error) {
    showToast(error.message || 'Failed to decide permission request', 4200);
  }
}

function pulseHiddenSystemNotice(severity = 'warning') {
  const level = severity === 'error' ? 'error' : 'warning';
  const className = `system-notice-pulse-${level}`;
  const targets = [
    document.querySelector('.agent-panel'),
    document.querySelector('.agent-panel-header'),
    document.querySelector('.agent-panel-orb'),
  ].filter(Boolean);
  targets.forEach((target) => {
    target.classList.remove('system-notice-pulse-warning', 'system-notice-pulse-error');
    // Force animation restart when repeated alerts arrive close together.
    void target.offsetWidth;
    target.classList.add(className);
  });
  clearTimeout(pulseHiddenSystemNotice.timer);
  pulseHiddenSystemNotice.timer = setTimeout(() => {
    targets.forEach((target) => target.classList.remove(className));
  }, 1500);
}

function normalizedPointFromEvent(event) {
  return {
    x: event.clientX / Math.max(1, window.innerWidth),
    y: event.clientY / Math.max(1, window.innerHeight),
    screen_x: event.clientX,
    screen_y: event.clientY,
  };
}

function denormalize(annotation) {
  return {
    left: `${Number(annotation.x || 0) * 100}%`,
    top: `${Number(annotation.y || 0) * 100}%`,
    width: `${Number(annotation.w || 0) * 100}%`,
    height: `${Number(annotation.h || 0) * 100}%`,
  };
}



function navigateFrame(frame, action, fallbackUrl = null) {
  if (!frame) return;
  try {
    if (action === 'back') {
      frame.contentWindow?.history?.back();
      return;
    }
    if (action === 'forward') {
      frame.contentWindow?.history?.forward();
      return;
    }
    if (action === 'refresh') {
      frame.contentWindow?.location?.reload();
      return;
    }
  } catch (_) {
    // Cross-origin iframe access can be blocked; fall back below where possible.
  }

  if (action === 'refresh') {
    const src = frame.getAttribute('src') || fallbackUrl;
    if (src) frame.setAttribute('src', src);
  }
}

function openExternalUrl(url) {
  const target = (url || '').trim();
  if (!target || target === 'about:blank' || target.startsWith('about:dashboard/')) {
    showToast('No external URL available for this view', 2600);
    return;
  }
  window.open(target, '_blank', 'noopener,noreferrer');
}

function workspaceFrameUrl() {
  const workspace = state.workspace || {};
  if (workspace.mode === 'dashboard') return null;
  return workspace.url && workspace.url !== 'about:blank' ? workspace.url : null;
}

function activeDashboard() {
  const activeId = state.workspace?.metadata?.active_dashboard_id;
  if (activeId) {
    const found = state.dashboards.find((d) => d.id === activeId);
    if (found) return found;
  }
  return Array.isArray(state.dashboards) && state.dashboards.length ? state.dashboards[0] : null;
}

function dashboardLabel(dashboard) {
  if (!dashboard) return 'Dashboard';
  const panels = Array.isArray(dashboard.panels) ? dashboard.panels.length : 0;
  return `${dashboard.name || dashboard.id || 'Dashboard'}${panels ? ` (${panels})` : ''}`;
}

function markDashboardActivity(dashboardId) {
  if (!dashboardId) return;
  const activeId = activeDashboard()?.id;
  const inDashboard = state.workspace?.mode === 'dashboard';
  if (inDashboard && activeId === dashboardId) return;
  state.dashboardActivity = { ...(state.dashboardActivity || {}), [dashboardId]: Date.now() };
  updateUiPrefs({ dashboardActivity: state.dashboardActivity });
  renderDashboardSwitcher();
}

function clearDashboardActivity(dashboardId) {
  if (!dashboardId || !state.dashboardActivity?.[dashboardId]) return;
  const next = { ...(state.dashboardActivity || {}) };
  delete next[dashboardId];
  state.dashboardActivity = next;
  updateUiPrefs({ dashboardActivity: state.dashboardActivity });
}

function renderDashboardSwitcher() {
  const select = els.dashboardSwitcher;
  if (!select) return;
  const dashboards = Array.isArray(state.dashboards) ? state.dashboards : [];
  const active = activeDashboard();
  const activeId = active?.id || '';
  select.innerHTML = '';
  select.disabled = dashboards.length === 0;
  select.classList.toggle('has-hidden-activity', dashboards.some((d) => d.id && state.dashboardActivity?.[d.id] && d.id !== activeId));

  if (!dashboards.length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'No dashboards';
    select.appendChild(option);
    select.title = 'No dashboards are available yet';
    return;
  }

  dashboards.forEach((dashboard) => {
    const option = document.createElement('option');
    option.value = dashboard.id || '';
    const hasActivity = Boolean(dashboard.id && state.dashboardActivity?.[dashboard.id] && dashboard.id !== activeId);
    option.textContent = `${hasActivity ? '• ' : ''}${dashboardLabel(dashboard)}`;
    if (hasActivity) option.dataset.activity = 'true';
    select.appendChild(option);
  });
  select.value = activeId || dashboards[0]?.id || '';
  select.title = select.classList.contains('has-hidden-activity') ? 'Switch dashboard — bullet indicates hidden dashboard activity' : 'Switch dashboard';
}

async function openDashboard(dashboardId) {
  const id = (dashboardId || '').trim();
  if (!id) return;
  clearDashboardActivity(id);
  const local = state.dashboards.find((d) => d.id === id);
  state.workspace = {
    ...(state.workspace || {}),
    mode: 'dashboard',
    url: `about:dashboard/${encodeURIComponent(id)}`,
    title: local?.name || 'Dashboard',
    metadata: { ...((state.workspace || {}).metadata || {}), active_dashboard_id: id },
  };
  renderWorkspace();

  try {
    const data = await api('/api/gui/dashboards/open', {
      method: 'POST',
      body: JSON.stringify({ dashboard_id: id }),
    });
    if (data?.dashboard) upsertDashboard(data.dashboard);
    if (data?.workspace) state.workspace = data.workspace;
    clearDashboardActivity(id);
    renderWorkspace();
    renderDiscovery();
  } catch (error) {
    showToast(error.message || 'Dashboard switched locally', 2600);
  }
}

function panelSource(panel) {
  if (panel.content_html) {
    return `data:text/html;charset=utf-8,${encodeURIComponent(panel.content_html)}`;
  }
  return panel.url || 'about:blank';
}


function panelZoom(panel) {
  const value = Number(panel?.zoom);
  const min = Number(panel?.min_zoom || 0.25);
  const max = Number(panel?.max_zoom || 3.0);
  const zoom = Number.isFinite(value) && value > 0 ? value : 1.0;
  return Math.max(min, Math.min(max, zoom));
}

function panelZoomLabel(panel) {
  return `${Math.round(panelZoom(panel) * 100)}%`;
}

function upsertDashboardFromPayload(payload) {
  if (payload?.dashboard) upsertDashboard(payload.dashboard);
  if (payload?.workspace) state.workspace = payload.workspace;
  renderWorkspace();
  renderDiscovery();
}

async function adjustDashboardPanelZoom(panel, delta) {
  const data = await api('/api/gui/dashboards/panel_zoom_delta', {
    method: 'POST',
    body: JSON.stringify({ panel_id: panel.id, delta, source: 'user' }),
  });
  upsertDashboardFromPayload(data);
  showToast(`Panel zoom ${Math.round((data.zoom || data.panel?.zoom || 1) * 100)}%`, 1500);
}

async function resetDashboardPanelZoom(panel) {
  const data = await api('/api/gui/dashboards/panel_zoom_reset', {
    method: 'POST',
    body: JSON.stringify({ panel_id: panel.id, source: 'user' }),
  });
  upsertDashboardFromPayload(data);
  showToast('Panel zoom reset to 100%', 1500);
}

async function closeDashboardPanel(panel) {
  if (!panel?.id) return;
  if (!window.confirm(`Close panel "${panel.title || panel.name || panel.id}"?`)) return;
  const data = await api('/api/gui/dashboards/remove_panel', {
    method: 'POST',
    body: JSON.stringify({ panel_id: panel.id, source: 'user' }),
  });
  upsertDashboardFromPayload(data);
  showToast('Dashboard panel closed', 1800);
}

async function closeDashboard(dashboard) {
  const target = dashboard || activeDashboard();
  if (!target?.id) return;
  if (!window.confirm(`Close dashboard "${target.name || target.id}"?`)) return;
  const data = await api('/api/gui/dashboards/remove', {
    method: 'POST',
    body: JSON.stringify({ dashboard_id: target.id, source: 'user' }),
  });
  if (data?.dashboard_id) {
    state.dashboards = (state.dashboards || []).filter((d) => d.id !== data.dashboard_id);
  }
  if (data?.next_dashboard) upsertDashboard(data.next_dashboard);
  if (data?.workspace) state.workspace = data.workspace;
  renderWorkspace();
  renderDiscovery();
  showToast('Dashboard closed', 1800);
}

function updateDashboardToolbar() {
  const root = $('app');
  const dashboard = activeDashboard();
  const inDashboard = Boolean((state.workspace || {}).mode === 'dashboard' && dashboard);
  root?.classList.toggle('dashboard-active', inDashboard);
  renderDashboardSwitcher();
  if (!els.dashboardFloatToggle || !els.dashboardSnapGrid) return;

  els.dashboardFloatToggle.disabled = !inDashboard;
  els.dashboardSnapGrid.disabled = !inDashboard || !getDashboardFloat(dashboard);
  if (!inDashboard) {
    els.dashboardFloatToggle.textContent = 'Float';
    els.dashboardFloatToggle.title = 'Dashboard controls are available when a dashboard is open';
    els.dashboardSnapGrid.title = 'Open a dashboard and enable floating panels first';
    return;
  }

  const floating = getDashboardFloat(dashboard);
  els.dashboardFloatToggle.textContent = floating ? 'Grid' : 'Float';
  els.dashboardFloatToggle.title = floating ? 'Return dashboard panels to CSS grid layout' : 'Float dashboard panels so they can be moved and resized';
  els.dashboardSnapGrid.title = floating ? 'Arrange floating panels into a clean grid' : 'Enable floating panels before snapping';
}

function renderDashboard() {
  if (!els.dashboard) return;
  const dashboard = activeDashboard();
  els.dashboard.innerHTML = '';
  if (!dashboard) {
    renderDashboardSwitcher();
    const empty = document.createElement('div');
    empty.className = 'dashboard-empty glass';
    empty.textContent = 'Dashboard mode is active, but no dashboard panels are available yet.';
    els.dashboard.appendChild(empty);
    updateDashboardToolbar();
    return;
  }

  state.dashboardFloat = getDashboardFloat(dashboard);
  clearDashboardActivity(dashboard.id);
  renderDashboardSwitcher();
  updateDashboardToolbar();
  const header = document.createElement('header');
  header.className = 'dashboard-header glass dashboard-header-compact';
  const headerText = document.createElement('div');
  const headerTitle = document.createElement('strong');
  headerTitle.textContent = dashboard.name || 'Dashboard';
  const headerMeta = document.createElement('span');
  headerMeta.textContent = `${dashboard.panels?.length || 0} panel${(dashboard.panels?.length || 0) === 1 ? '' : 's'} · ${dashboard.layout || 'grid'}`;
  headerText.appendChild(headerTitle);
  headerText.appendChild(headerMeta);
  const headerActions = document.createElement('div');
  headerActions.className = 'dashboard-actions';
  const closeDashboardButton = document.createElement('button');
  closeDashboardButton.type = 'button';
  closeDashboardButton.className = 'dashboard-close-button';
  closeDashboardButton.textContent = 'Close dashboard';
  closeDashboardButton.title = 'Close this dashboard and notify the agent';
  closeDashboardButton.addEventListener('click', (event) => {
    event.stopPropagation();
    closeDashboard(dashboard).catch((error) => showToast(error.message || 'Failed to close dashboard', 3000));
  });
  headerActions.appendChild(closeDashboardButton);
  header.appendChild(headerText);
  header.appendChild(headerActions);
  els.dashboard.appendChild(header);

  const panels = dashboard.panels || [];
  const grid = document.createElement('div');
  grid.className = `dashboard-grid layout-${dashboard.layout || 'grid'}${state.dashboardFloat ? ' is-floating' : ''}`;
  panels.forEach((panel, index) => {
    const card = document.createElement('article');
    card.className = `dashboard-panel kind-${panel.kind || 'html'}`;
    card.dataset.panelIndex = String(index);
    card.dataset.panelKey = dashboardPanelKey(panel, index);
    const layout = panel.layout || {};
    const savedLayout = getDashboardPanelLayout(dashboard, panel, index);
    if (state.dashboardFloat) {
      const col = index % 3;
      const row = Math.floor(index / 3);
      card.style.left = savedLayout?.left || layout.left || `${18 + col * 30}%`;
      card.style.top = savedLayout?.top || layout.top || `${12 + row * 320}px`;
      card.style.width = savedLayout?.width || layout.width || 'min(560px, 42vw)';
      card.style.height = savedLayout?.height || layout.height || 'min(440px, 46vh)';
      card.style.zIndex = savedLayout?.zIndex || String(10 + index);
    } else {
      if (layout.grid_column) card.style.gridColumn = layout.grid_column;
      if (layout.grid_row) card.style.gridRow = layout.grid_row;
      if (layout.min_height) card.style.minHeight = layout.min_height;
    }

    const bar = document.createElement('div');
    bar.className = 'dashboard-panel-bar';
    bar.title = state.dashboardFloat ? 'Drag to move panel' : '';
    const titleGroup = document.createElement('div');
    titleGroup.className = 'dashboard-panel-title';
    const name = document.createElement('span');
    name.textContent = panel.title || panel.name || 'Panel';
    const meta = document.createElement('small');
    meta.textContent = `${panel.kind || 'html'} · ${panel.status || 'ready'}`;
    titleGroup.appendChild(name);
    titleGroup.appendChild(meta);

    const zoom = panelZoom(panel);
    const viewport = document.createElement('div');
    viewport.className = 'dashboard-panel-viewport';
    viewport.dataset.zoom = String(zoom);
    const zoomSurface = document.createElement('div');
    zoomSurface.className = 'dashboard-panel-zoom-surface';
    zoomSurface.style.transform = `scale(${zoom})`;
    zoomSurface.style.transformOrigin = 'top left';

    const frame = document.createElement('iframe');
    frame.className = 'dashboard-panel-frame';
    frame.title = panel.title || panel.name || 'Dashboard panel';
    frame.sandbox = 'allow-same-origin allow-scripts allow-forms allow-popups allow-downloads';
    frame.src = panelSource(panel);
    zoomSurface.appendChild(frame);
    viewport.appendChild(zoomSurface);

    const controls = document.createElement('div');
    controls.className = 'dashboard-panel-nav';
    const navItems = [
      ['back', '←', 'Back in panel'],
      ['forward', '→', 'Forward in panel'],
      ['refresh', '↻', 'Refresh panel'],
      ['external', '↗', 'Open panel externally'],
    ];
    navItems.forEach(([action, label, title]) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.title = title;
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        if (action === 'external') openExternalUrl(panel.url);
        else navigateFrame(frame, action, panelSource(panel));
      });
      controls.appendChild(button);
    });

    const zoomControls = document.createElement('div');
    zoomControls.className = 'dashboard-panel-zoom-controls';
    const zoomOut = document.createElement('button');
    zoomOut.type = 'button';
    zoomOut.textContent = '−';
    zoomOut.title = 'Zoom panel out';
    zoomOut.addEventListener('click', (event) => {
      event.stopPropagation();
      adjustDashboardPanelZoom(panel, -(Number(panel.zoom_step || 0.1))).catch((error) => showToast(error.message || 'Failed to zoom panel', 3000));
    });
    const zoomLabel = document.createElement('button');
    zoomLabel.type = 'button';
    zoomLabel.className = 'dashboard-panel-zoom-label';
    zoomLabel.textContent = panelZoomLabel(panel);
    zoomLabel.title = 'Reset panel zoom to 100%';
    zoomLabel.addEventListener('click', (event) => {
      event.stopPropagation();
      resetDashboardPanelZoom(panel).catch((error) => showToast(error.message || 'Failed to reset zoom', 3000));
    });
    const zoomIn = document.createElement('button');
    zoomIn.type = 'button';
    zoomIn.textContent = '+';
    zoomIn.title = 'Zoom panel in';
    zoomIn.addEventListener('click', (event) => {
      event.stopPropagation();
      adjustDashboardPanelZoom(panel, Number(panel.zoom_step || 0.1)).catch((error) => showToast(error.message || 'Failed to zoom panel', 3000));
    });
    const zoomReset = document.createElement('button');
    zoomReset.type = 'button';
    zoomReset.textContent = '100';
    zoomReset.title = 'Reset panel zoom';
    zoomReset.addEventListener('click', (event) => {
      event.stopPropagation();
      resetDashboardPanelZoom(panel).catch((error) => showToast(error.message || 'Failed to reset zoom', 3000));
    });
    zoomControls.appendChild(zoomOut);
    zoomControls.appendChild(zoomLabel);
    zoomControls.appendChild(zoomIn);
    zoomControls.appendChild(zoomReset);
    controls.appendChild(zoomControls);

    const closePanelButton = document.createElement('button');
    closePanelButton.type = 'button';
    closePanelButton.className = 'dashboard-panel-close';
    closePanelButton.textContent = '×';
    closePanelButton.title = 'Close this panel and notify the agent';
    closePanelButton.addEventListener('click', (event) => {
      event.stopPropagation();
      closeDashboardPanel(panel).catch((error) => showToast(error.message || 'Failed to close panel', 3000));
    });
    controls.appendChild(closePanelButton);

    bar.appendChild(titleGroup);
    bar.appendChild(controls);
    card.appendChild(bar);
    card.appendChild(viewport);
    const resize = document.createElement('div');
    resize.className = 'dashboard-panel-resize';
    resize.title = state.dashboardFloat ? 'Resize floating dashboard panel' : 'Resize dashboard panel';
    card.appendChild(resize);
    grid.appendChild(card);
  });
  els.dashboard.appendChild(grid);
}

function upsertDashboard(dashboard) {
  if (!dashboard || !dashboard.id) return;
  const idx = state.dashboards.findIndex((d) => d.id === dashboard.id);
  if (idx >= 0) state.dashboards[idx] = dashboard;
  else state.dashboards.unshift(dashboard);
  renderDashboardSwitcher();
}

function renderWorkspace() {
  const workspace = state.workspace || {};
  els.toolbarMode.value = workspace.mode || 'browser';
  els.urlInput.value = workspace.url && workspace.url !== 'about:blank' && workspace.mode !== 'dashboard' ? workspace.url : '';
  els.workspaceSummary.textContent = `${workspace.mode || 'browser'} · ${workspace.title || workspace.url || 'blank'}`;
  renderDashboardSwitcher();

  $('app')?.classList.toggle('dashboard-active', workspace.mode === 'dashboard');
  updateDashboardToolbar();

  if (workspace.mode === 'dashboard') {
    els.frame.removeAttribute('src');
    els.frame.hidden = true;
    els.blank.style.display = 'none';
    if (els.dashboard) {
      els.dashboard.hidden = false;
      renderDashboard();
    }
    return;
  }

  if (els.dashboard) {
    els.dashboard.hidden = true;
    els.dashboard.innerHTML = '';
  }
  els.frame.hidden = false;
  if (!workspace.url || workspace.url === 'about:blank') {
    els.frame.removeAttribute('src');
    els.blank.style.display = 'grid';
    return;
  }
  els.blank.style.display = 'none';
  if (els.frame.src !== workspace.url) {
    els.frame.src = workspace.url;
  }
}

function renderAnnotations() {
  els.layer.innerHTML = '';
  state.annotations.forEach((annotation) => {
    const pos = denormalize(annotation);
    if (annotation.kind === 'rect') {
      const rect = document.createElement('div');
      rect.className = 'annotation-rect';
      rect.style.left = pos.left;
      rect.style.top = pos.top;
      rect.style.width = pos.width;
      rect.style.height = pos.height;
      rect.style.setProperty('--annotation-color', annotation.color || '#7dd3fc');
      rect.title = annotation.label || annotation.id;
      if (annotation.label) {
        const label = document.createElement('div');
        label.className = 'annotation-label';
        label.textContent = annotation.label;
        rect.appendChild(label);
      }
      els.layer.appendChild(rect);
      return;
    }

    const point = document.createElement('div');
    point.className = 'annotation annotation-point';
    point.style.left = pos.left;
    point.style.top = pos.top;
    point.style.setProperty('--annotation-color', annotation.color || '#7dd3fc');
    point.title = annotation.label || annotation.id;
    if (annotation.label) {
      const label = document.createElement('div');
      label.className = 'annotation-label';
      label.textContent = annotation.label;
      point.appendChild(label);
    }
    els.layer.appendChild(point);
  });
}

function renderMessages() {
  els.messages.innerHTML = '';
  const showSystemMessages = state.showSystemMessages !== false;
  let hiddenSystemCount = 0;
  const hiddenSystemSeverityCounts = { neutral: 0, warning: 0, error: 0 };
  const hiddenSystemAlertItems = [];
  let pendingSystem = [];

  const messageTime = (message) => {
    if (!message?.created_at) return null;
    const date = new Date(message.created_at * 1000);
    return Number.isFinite(date.getTime()) ? date : null;
  };

  const roleLabelFor = (role) => ({ user: 'You', assistant: 'Agent', system: 'System' }[role] || String(role || 'Message').replace(/(^|[-_\s])([a-z])/g, (_, sep, ch) => `${sep}${ch.toUpperCase()}`));

  const isSystemNotice = (message) => {
    // Only actual system-role messages should be grouped/hidden.
    // Assistant/user messages from the gateway often carry gateway_event metadata;
    // those must remain visible as normal chat bubbles.  Also honor a
    // force_visible escape hatch for gateway messages that should never be
    // folded into a notice bundle.
    if (message?.metadata?.force_visible) return false;
    return (message?.role || 'assistant') === 'system';
  };

  const systemNoticeSeverity = (message) => {
    const metadata = message?.metadata || {};


    const tokens = [
      metadata.severity, metadata.level, metadata.tone, metadata.status, metadata.type,
      metadata.gateway_event?.severity, metadata.gateway_event?.level, metadata.gateway_event?.status, metadata.gateway_event?.type,
      metadata.system_event?.severity, metadata.system_event?.level, metadata.system_event?.status, metadata.system_event?.type,
      message?.content,
    ].filter(Boolean).map((value) => String(value).toLowerCase()).join(' ');

    if (/\b(error|failed|failure|fatal|exception|denied|blocked|unauthorized|forbidden|offline|unhealthy)\b/.test(tokens)) return 'error';
    if (/\b(warn|warning|caution|degraded|retry|timeout|paused|pending|rate.?limit)\b/.test(tokens)) return 'warning';
    return 'neutral';
  };

  const systemNoticeSeverityRank = { neutral: 0, warning: 1, error: 2 };
  const strongestSystemNoticeSeverity = (counts) => (counts.error ? 'error' : counts.warning ? 'warning' : 'neutral');
  const systemNoticeCountSummary = (counts) => {
    const parts = [];
    if (counts.error) parts.push(`${counts.error} error${counts.error === 1 ? '' : 's'}`);
    if (counts.warning) parts.push(`${counts.warning} warning${counts.warning === 1 ? '' : 's'}`);
    if (!parts.length && counts.neutral) parts.push(`${counts.neutral} neutral`);
    return parts.join(', ');
  };

  const systemNoticeIdentity = (message, index = 0) => {
    const metadata = message?.metadata || {};
    return String(metadata.id || metadata.event_id || metadata.gateway_event?.id || metadata.system_event?.id || `${message?.created_at || ''}:${index}:${message?.content || ''}`);
  };

  const detailPayloadFor = (message) => {
    const metadata = message?.metadata || {};
    return metadata.gateway_event || metadata.system_event || metadata.gateway_result || metadata.gateway_policy || null;
  };

  const appendDetailsButton = (meta, details, title = 'Show details') => {
    const info = document.createElement('button');
    info.type = 'button';
    info.className = 'message-info';
    info.title = title;
    info.setAttribute('aria-label', title);
    info.setAttribute('aria-expanded', 'false');
    info.textContent = 'i';
    info.addEventListener('click', () => {
      const open = details.hidden;
      details.hidden = !open;
      info.classList.toggle('is-open', open);
      info.setAttribute('aria-expanded', open ? 'true' : 'false');
      info.title = open ? 'Hide details' : title;
    });
    meta.appendChild(info);
  };

  const appendMessage = (message) => {
    const metadata = message.metadata || {};
    const roleName = message.role || 'assistant';
    const classes = ['message', roleName];
    if (metadata.compact) classes.push('compact');
    if (metadata.card) classes.push('card');
    if (metadata.tone) classes.push(`tone-${String(metadata.tone).replace(/[^a-z0-9_-]/gi, '')}`);

    const node = document.createElement('article');
    node.className = classes.join(' ');

    const meta = document.createElement('div');
    meta.className = 'message-meta';
    const label = document.createElement('span');
    const date = messageTime(message);
    const roleLabel = roleLabelFor(roleName);
    label.textContent = date ? `${roleLabel} · ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : roleLabel;
    if (date) label.title = date.toLocaleString();
    meta.appendChild(label);

    const body = document.createElement('div');
    body.className = 'message-content';
    body.textContent = message.content || '';

    const detailPayload = detailPayloadFor(message);
    let details = null;
    if (detailPayload) {
      details = document.createElement('pre');
      details.className = 'message-details';
      details.hidden = true;
      try {
        details.textContent = JSON.stringify(detailPayload, null, 2);
      } catch (_) {
        details.textContent = String(detailPayload);
      }
      appendDetailsButton(meta, details, 'Show system details');
    }

    node.appendChild(meta);
    node.appendChild(body);
    if (details) node.appendChild(details);
    els.messages.appendChild(node);
  };

  const flushSystemGroup = () => {
    if (!pendingSystem.length) return;
    if (!showSystemMessages) {
      pendingSystem.forEach((msg, index) => {
        const severity = systemNoticeSeverity(msg);
        hiddenSystemSeverityCounts[severity] += 1;
        if (severity !== 'neutral') hiddenSystemAlertItems.push(`${severity}:${systemNoticeIdentity(msg, index)}`);
      });
      hiddenSystemCount += pendingSystem.length;
      pendingSystem = [];
      return;
    }

    const groupCounts = { neutral: 0, warning: 0, error: 0 };
    pendingSystem.forEach((msg) => { groupCounts[systemNoticeSeverity(msg)] += 1; });
    const groupSeverity = strongestSystemNoticeSeverity(groupCounts);

    const group = document.createElement('article');
    group.className = `message system system-group compact severity-${groupSeverity}`;

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'system-group-toggle';
    button.setAttribute('aria-expanded', 'false');
    const firstTime = messageTime(pendingSystem[0]);
    const groupSummary = systemNoticeCountSummary(groupCounts);
    button.classList.add(`severity-${groupSeverity}`);
    button.textContent = `${pendingSystem.length} system notice${pendingSystem.length === 1 ? '' : 's'}${groupSummary ? ` · ${groupSummary}` : ''}${firstTime ? ` · ${firstTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}`;

    const details = document.createElement('div');
    details.className = 'system-group-details';
    details.hidden = true;
    pendingSystem.forEach((msg) => {
      const item = document.createElement('div');
      const itemSeverity = systemNoticeSeverity(msg);
      item.className = `system-group-item severity-${itemSeverity}`;
      const t = messageTime(msg);
      item.textContent = `${t ? `${t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} · ` : ''}${msg.content || 'System event'}`;
      const payload = detailPayloadFor(msg);
      if (payload) {
        const pre = document.createElement('pre');
        pre.className = 'message-details system-group-payload';
        try { pre.textContent = JSON.stringify(payload, null, 2); }
        catch (_) { pre.textContent = String(payload); }
        item.appendChild(pre);
      }
      details.appendChild(item);
    });

    button.addEventListener('click', () => {
      const open = details.hidden;
      details.hidden = !open;
      button.classList.toggle('is-open', open);
      button.setAttribute('aria-expanded', open ? 'true' : 'false');
    });

    group.appendChild(button);
    group.appendChild(details);
    els.messages.appendChild(group);
    pendingSystem = [];
  };

  state.messages.forEach((message) => {
    if (isSystemNotice(message)) {
      pendingSystem.push(message);
      return;
    }
    flushSystemGroup();
    appendMessage(message);
  });
  flushSystemGroup();

  const hiddenSeverity = strongestSystemNoticeSeverity(hiddenSystemSeverityCounts);
  const hiddenSeveritySummary = systemNoticeCountSummary(hiddenSystemSeverityCounts);
  if (!showSystemMessages && hiddenSystemCount) {
    const summary = document.createElement('article');
    summary.className = `message system compact system-summary severity-${hiddenSeverity}`;
    summary.textContent = `${hiddenSystemCount} system notice${hiddenSystemCount === 1 ? '' : 's'} hidden${hiddenSeveritySummary ? ` · ${hiddenSeveritySummary}` : ''}`;
    els.messages.prepend(summary);
  }
  const hiddenAlertSignature = hiddenSystemAlertItems.join('|');
  if (!showSystemMessages) {
    // Treat the first hidden render as the baseline, then pulse only when a
    // new hidden warning/error appears. This avoids alarming on page load or
    // immediately when the user intentionally hides existing notices.
    if (hiddenAlertSignature && state.hiddenSystemAlertInitialized && state.hiddenSystemAlertSignature !== hiddenAlertSignature) {
      pulseHiddenSystemNotice(hiddenSeverity);
    }
    state.hiddenSystemAlertSignature = hiddenAlertSignature;
    state.hiddenSystemAlertInitialized = true;
  } else {
    state.hiddenSystemAlertSignature = '';
    state.hiddenSystemAlertInitialized = false;
  }
  if (els.toggleSystemMessages) {
    els.toggleSystemMessages.classList.toggle('is-active', showSystemMessages);
    els.toggleSystemMessages.classList.remove('severity-neutral', 'severity-warning', 'severity-error');
    if (!showSystemMessages) els.toggleSystemMessages.classList.add(`severity-${hiddenSeverity}`);
    els.toggleSystemMessages.setAttribute('aria-pressed', showSystemMessages ? 'true' : 'false');
    els.toggleSystemMessages.textContent = showSystemMessages ? 'System notices on' : `System notices hidden${hiddenSeveritySummary && hiddenSeverity !== 'neutral' ? ` · ${hiddenSeveritySummary}` : ''}`;
  }
  els.messages.scrollTop = els.messages.scrollHeight;
}

window.wolfGuiRenderMessages = renderMessages;
window.wolfGuiAddApprovalRequest = addApprovalRequest;
window.wolfGuiResolveApprovalRequest = resolveApprovalRequest;
window.wolfGuiRenderApprovals = renderApprovals;
window.wolfGuiShowToast = showToast;
window.wolfGuiPulsePermissionPanel = pulseHiddenSystemNotice;

function renderDiscovery() {
  const workflowCount = state.workflows && !state.workflows.error ? Object.keys(state.workflows).length : 0;
  const appCount = Array.isArray(state.apps) ? state.apps.length : 0;
  const dashboardCount = Array.isArray(state.dashboards) ? state.dashboards.length : 0;
  els.workflowCount.textContent = String(workflowCount);
  els.sessionCount.textContent = `${Array.isArray(state.sessions) ? state.sessions.length : 0} · Apps ${appCount} · Dash ${dashboardCount}`;
}


function applyDockMode(mode, options = {}) {
  const root = $('app');
  const allowed = new Set(['float', 'left', 'right', 'top', 'bottom']);
  const next = allowed.has(mode) ? mode : 'float';
  state.dockMode = next;

  root.classList.remove('dock-left', 'dock-right', 'dock-top', 'dock-bottom');
  if (next !== 'float') {
    root.classList.add(`dock-${next}`);
    els.panel.style.left = '';
    els.panel.style.right = '';
    els.panel.style.top = '';
    els.panel.style.bottom = '';
    els.panel.style.width = '';
    els.panel.style.height = '';
  }

  if (els.dockModeToolbar) els.dockModeToolbar.value = next;
  if (els.dockModePanel) els.dockModePanel.value = next;
  document.querySelectorAll('[data-dock-mode]').forEach((button) => {
    button.classList.toggle('is-active', button.getAttribute('data-dock-mode') === next);
  });
  if (options.persist !== false) persistAgentPanelPrefs();
}


function syncActiveAppFromWorkspace() {
  const activeAppId = state.workspace?.metadata?.active_app_id;
  if (!activeAppId) return;
  const found = state.apps.find((a) => a.id === activeAppId);
  if (found) {
    showToast(`Agent opened app: ${found.name}`, 1800);
  }
}

function renderAll() {
  renderWorkspace();
  renderAnnotations();
  renderMessages();
  renderApprovals();
  renderDiscovery();
  applyChatScale(state.chatScale, { persist: false });
}

function setAnnotationMode(mode) {
  state.annotateMode = state.annotateMode === mode ? null : mode;
  els.toggleAnnotate.classList.toggle('is-active', state.annotateMode === 'point');
  els.toggleRect.classList.toggle('is-active', state.annotateMode === 'rect');
  els.layer.classList.toggle('capture', !!state.annotateMode);
  showToast(state.annotateMode ? `${state.annotateMode} annotation mode` : 'annotation mode off');
}

async function bootstrap() {
  const data = await api('/api/gui/bootstrap');
  state.workspace = data.workspace || state.workspace;
  state.annotations = data.annotations || [];
  state.messages = data.messages || [];
  state.approvals = data.approvals || state.approvals || [];
  state.apps = data.apps || [];
  state.dashboards = data.dashboards || [];
  state.workflows = data.workflows || {};
  state.sessions = data.sessions || [];
  renderAll();
  syncActiveAppFromWorkspace();
  await refreshHealth();
}

async function refreshHealth() {
  const dot = els.healthDot;
  if (!dot) return;
  dot.classList.remove('is-healthy', 'is-unhealthy', 'is-offline', 'is-checking');
  dot.classList.add('is-checking');
  dot.title = 'Gateway health: checking';
  try {
    const data = await api('/api/gui/health');
    dot.classList.remove('is-checking');
    dot.classList.add(data.ok ? 'is-healthy' : 'is-unhealthy');
    dot.title = `Gateway health: ${data.ok ? 'healthy' : 'unhealthy'}`;
  } catch (error) {
    dot.classList.remove('is-checking');
    dot.classList.add('is-offline');
    dot.title = 'Gateway health: offline';
  }
}

async function openUrl(url) {
  const data = await api('/api/gui/workspace/open_url', {
    method: 'POST',
    body: JSON.stringify({ url }),
  });
  state.workspace = data.workspace;
  renderWorkspace();
}

async function addAnnotation(annotation) {
  const data = await api('/api/gui/annotations', {
    method: 'POST',
    body: JSON.stringify(annotation),
  });
  state.annotations.push(data.annotation);
  renderAnnotations();
}

function currentVisualContext() {
  const now = Date.now() / 1000;
  const workspace = state.workspace || {};
  const dashboard = workspace.mode === 'dashboard' ? activeDashboard() : null;
  const frameRect = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.left),
      y: Math.round(r.top),
      width: Math.round(r.width),
      height: Math.round(r.height),
      visible: r.width > 0 && r.height > 0,
    };
  };
  const iframeAccess = (frame) => {
    const info = {
      src: frame?.getAttribute?.('src') || null,
      title: frame?.getAttribute?.('title') || null,
      same_origin_accessible: false,
      document_title: null,
      body_text_excerpt: null,
      limitation: null,
    };
    if (!frame) return info;
    try {
      const doc = frame.contentDocument || frame.contentWindow?.document;
      if (!doc) throw new Error('iframe document unavailable');
      info.same_origin_accessible = true;
      info.document_title = doc.title || null;
      const text = doc.body?.innerText || '';
      info.body_text_excerpt = text ? text.slice(0, 12000) : null;
    } catch (error) {
      info.limitation = 'Browser security blocks DOM/pixel inspection for cross-origin or restricted iframe content.';
    }
    return info;
  };
  const panelNodes = Array.from(document.querySelectorAll('#dashboard-workspace .dashboard-panel'));
  const panels = dashboard ? (dashboard.panels || []).map((panel, index) => {
    const node = panelNodes[index] || null;
    const frame = node?.querySelector?.('iframe') || null;
    return {
      index,
      id: panel.id || panel.panel_id || null,
      key: dashboardPanelKey(panel, index),
      title: panel.title || panel.name || 'Panel',
      kind: panel.kind || 'html',
      status: panel.status || 'ready',
      url: panel.url || null,
      source: panel.source || null,
      host_status: panel.host_status || null,
      layout: panel.layout || {},
      bounding_box: frameRect(node),
      iframe: iframeAccess(frame),
      has_inline_html: Boolean(panel.content_html),
      inline_html_excerpt: panel.content_html ? String(panel.content_html).slice(0, 12000) : null,
    };
  }) : [];

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const rectsIntersect = (a, b) => {
    if (!a || !b || !a.visible || !b.visible) return false;
    return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
  };
  const pointInRect = (point, rect) => {
    if (!point || !rect || !rect.visible) return false;
    return point.x >= rect.x && point.x <= rect.x + rect.width && point.y >= rect.y && point.y <= rect.y + rect.height;
  };
  const annotationPixelGeometry = (annotation) => {
    const x = clamp(Number(annotation.x || 0), 0, 1) * window.innerWidth;
    const y = clamp(Number(annotation.y || 0), 0, 1) * window.innerHeight;
    if (annotation.kind === 'rect') {
      const w = Math.max(0, Number(annotation.w || 0) * window.innerWidth);
      const h = Math.max(0, Number(annotation.h || 0) * window.innerHeight);
      return {
        point: { x: Math.round(x + w / 2), y: Math.round(y + h / 2) },
        box: {
          x: Math.round(x),
          y: Math.round(y),
          width: Math.round(w),
          height: Math.round(h),
          visible: w > 0 && h > 0,
        },
      };
    }
    return {
      point: { x: Math.round(x), y: Math.round(y) },
      box: {
        x: Math.round(Math.max(0, x - 12)),
        y: Math.round(Math.max(0, y - 12)),
        width: 24,
        height: 24,
        visible: true,
      },
    };
  };
  const annotationTargets = (state.annotations || []).map((annotation, index) => {
    const geometry = annotationPixelGeometry(annotation);
    const matchedPanels = panels.filter((panel) => {
      const box = panel.bounding_box;
      return annotation.kind === 'rect' ? rectsIntersect(geometry.box, box) : pointInRect(geometry.point, box);
    }).map((panel) => ({
      panel_id: panel.id,
      panel_key: panel.key,
      panel_title: panel.title,
      panel_index: panel.index,
      bounding_box: panel.bounding_box,
    }));
    const surface = matchedPanels.length ? 'dashboard_panel' : (dashboard ? 'dashboard_workspace' : 'workspace_frame');
    return {
      ...annotation,
      index,
      pixel_point: geometry.point,
      pixel_box: geometry.box,
      target_surface: surface,
      target_panels: matchedPanels,
      capture_hint: annotation.kind === 'rect' ? 'crop_pixel_box' : 'crop_around_pixel_point',
    };
  });

  return {
    schema_version: 'wolf_gui_visual_context.v1',
    kind: 'wolf_gui_visual_context',
    source: 'wolf_gui_browser_client',
    captured_at: now,
    permissions: {
      attach_workspace_view: Boolean(els.includeVisualContext?.checked),
      agent_inspect_allowed: agentInspectAllowed(),
      agent_capture_allowed: agentCaptureAllowed(),
    },
    capture_capabilities: {
      workspace_state: true,
      viewport_geometry: true,
      annotations: true,
      annotation_pixel_geometry: true,
      annotation_panel_association: true,
      dashboard_panel_metadata: true,
      dashboard_inline_html_excerpt: true,
      same_origin_iframe_dom_excerpt: 'best_effort',
      full_gui_pixel_screenshot: agentCaptureAllowed() ? "live_client_surface_or_backend_capture_available" : false,
      cross_origin_iframe_dom: false,
      cross_origin_iframe_pixels: agentCaptureAllowed() ? "requires_permissioned_live_client_or_backend_capture" : false,
      backend_capture_action: agentCaptureAllowed() ? 'gui_capture_url/gui_capture_workspace permitted by user toggle; live client surface capture may prompt for browser sharing permission' : 'disabled_by_user_toggle',
      limitation_note: 'The browser can describe the Wolf GUI workspace and same-origin/inline dashboard content. Exact live rendered pixels require the Allow agent capture toggle and browser screen/tab sharing permission; backend Playwright replay remains a fallback.'
    },
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      device_pixel_ratio: window.devicePixelRatio || 1,
      location: window.location.href,
      document_title: document.title,
    },
    workspace,
    workspace_mode: workspace.mode || 'browser',
    url: workspace.url || null,
    visible_surfaces: {
      workspace_frame: workspace.mode === 'dashboard' ? null : {
        bounding_box: frameRect(els.frame),
        iframe: iframeAccess(els.frame),
      },
      dashboard_workspace: dashboard ? {
        bounding_box: frameRect(els.dashboard),
        floating_panels: Boolean(state.dashboardFloat),
      } : null,
      agent_panel: frameRect(els.panel),
    },
    active_dashboard: dashboard ? {
      id: dashboard.id || null,
      name: dashboard.name || null,
      description: dashboard.description || null,
      layout: dashboard.layout || null,
      panel_count: (dashboard.panels || []).length,
    } : null,
    dashboard_panels: panels,
    dashboards_summary: (state.dashboards || []).map((d) => ({ id: d.id, name: d.name, panel_count: (d.panels || []).length })),
    apps_summary: (state.apps || []).map((a) => ({ id: a.id, name: a.name, url: a.url, kind: a.kind, status: a.host_status || a.status })),
    annotations: state.annotations,
    annotation_targets: annotationTargets,
    annotation_count: state.annotations.length,
  };
}

window.wolfGuiCurrentVisualContext = currentVisualContext;

function installToolbarGroupHandlers() {
  const groups = Array.from(document.querySelectorAll('.toolbar-group'));
  if (!groups.length) return;
  let openGroup = null;

  const setExpanded = (group, expanded) => {
    const trigger = group?.querySelector?.('.toolbar-group-trigger');
    if (trigger) trigger.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  };

  const closeGroup = (group) => {
    if (!group) return;
    clearTimeout(group._wolfCloseTimer);
    group.classList.remove('is-open', 'is-hovering');
    setExpanded(group, false);
    if (openGroup === group) openGroup = null;
  };

  const closeOtherGroups = (keep = null) => {
    groups.forEach((group) => {
      if (group !== keep) closeGroup(group);
    });
  };

  groups.forEach((group) => {
    const trigger = group.querySelector('.toolbar-group-trigger');
    if (trigger) {
      trigger.setAttribute('aria-haspopup', 'true');
      trigger.setAttribute('aria-expanded', 'false');
    }

    group.addEventListener('pointerenter', () => {
      clearTimeout(group._wolfCloseTimer);
      closeOtherGroups(group);
      group.classList.add('is-hovering');
      setExpanded(group, true);
    });

    group.addEventListener('pointerleave', () => {
      clearTimeout(group._wolfCloseTimer);
      group._wolfCloseTimer = setTimeout(() => {
        if (!group.classList.contains('is-open')) {
          group.classList.remove('is-hovering');
          setExpanded(group, false);
        }
      }, 260);
    });

    trigger?.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      const willOpen = !group.classList.contains('is-open');
      closeOtherGroups(group);
      group.classList.toggle('is-open', willOpen);
      group.classList.toggle('is-hovering', willOpen);
      setExpanded(group, willOpen);
      openGroup = willOpen ? group : null;
    });

    group.addEventListener('click', (event) => {
      // Let toolbar item buttons run normally, then close only click-pinned groups.
      if (!event.target.closest('.toolbar-group-items button')) return;
      if (group.classList.contains('is-open')) {
        setTimeout(() => closeGroup(group), 0);
      }
    });
  });

  document.addEventListener('click', (event) => {
    if (event.target.closest('.toolbar-group')) return;
    closeOtherGroups(null);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    closeOtherGroups(null);
  });
}

function installWorkspaceHandlers() {
  els.openUrlForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await openUrl(els.urlInput.value.trim());
    } catch (error) {
      showToast(error.message, 4000);
    }
  });

  const toolbarSizes = ['toolbar-compact', 'toolbar-mid', 'toolbar-full'];
  const toolbarLabels = {
    'toolbar-compact': '◐',
    'toolbar-mid': '◑',
    'toolbar-full': '●',
  };
  const toolbarTitles = {
    'toolbar-compact': 'Toolbar: Mini',
    'toolbar-mid': 'Toolbar: Mid',
    'toolbar-full': 'Toolbar: Full',
  };
  const setToolbarSize = (size, options = {}) => {
    if (!els.topToolbar || !els.toolbarToggle) return;
    const next = toolbarSizes.includes(size) ? size : 'toolbar-mid';
    toolbarSizes.forEach((className) => els.topToolbar.classList.toggle(className, className === next));
    const expanded = next !== 'toolbar-compact';
    state.toolbarSize = next;
    els.toolbarToggle.textContent = toolbarLabels[next] || '◑';
    els.toolbarToggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    els.toolbarToggle.title = `${toolbarTitles[next] || 'Toolbar'} — click to cycle`;
    els.toolbarToggle.setAttribute('aria-label', els.toolbarToggle.title);
    if (options.persist !== false) updateUiPrefs({ toolbarSize: next });
  };
  setToolbarSize(state.toolbarSize || 'toolbar-mid', { persist: false });
  els.toolbarToggle?.addEventListener('click', () => {
    const current = toolbarSizes.findIndex((className) => els.topToolbar?.classList.contains(className));
    setToolbarSize(toolbarSizes[(current + 1) % toolbarSizes.length]);
  });

  els.dashboardFloatToggle?.addEventListener('click', () => {
    const dashboard = activeDashboard();
    if (!dashboard) return;
    setDashboardFloat(dashboard, !getDashboardFloat(dashboard));
    renderDashboard();
  });
  els.dashboardSnapGrid?.addEventListener('click', () => {
    if (!getDashboardFloat(activeDashboard())) return;
    snapDashboardPanelsToGrid();
    updateDashboardToolbar();
  });
  els.dashboardSwitcher?.addEventListener('change', (event) => {
    const dashboardId = event.target.value;
    if (!dashboardId) return;
    openDashboard(dashboardId);
  });

  els.workspaceBack?.addEventListener('click', () => {
    navigateFrame(els.frame, 'back', workspaceFrameUrl());
  });
  els.workspaceForward?.addEventListener('click', () => {
    navigateFrame(els.frame, 'forward', workspaceFrameUrl());
  });
  els.workspaceRefresh?.addEventListener('click', () => {
    navigateFrame(els.frame, 'refresh', workspaceFrameUrl());
  });
  els.workspaceOpenExternal?.addEventListener('click', () => {
    openExternalUrl(workspaceFrameUrl());
  });

  els.toggleAnnotate.addEventListener('click', () => setAnnotationMode('point'));
  els.dockModeToolbar?.addEventListener('change', (e) => applyDockMode(e.target.value));
  els.dockModePanel?.addEventListener('change', (e) => applyDockMode(e.target.value));
  document.querySelectorAll('[data-dock-mode]').forEach((button) => {
    button.addEventListener('click', () => {
      const mode = button.getAttribute('data-dock-mode') || 'float';
      if (els.dockModePanel) els.dockModePanel.value = mode;
      applyDockMode(mode);
    });
  });
  els.toggleSystemMessages?.addEventListener('click', () => {
    state.showSystemMessages = state.showSystemMessages === false;
    updateUiPrefs({ showSystemMessages: state.showSystemMessages });
    renderMessages();
  });
  els.chatScaleDown?.addEventListener('click', () => adjustChatScale(-0.1));
  els.chatScaleUp?.addEventListener('click', () => adjustChatScale(0.1));
  els.chatScaleLabel?.addEventListener('click', () => resetChatScale());
  els.chatScaleReset?.addEventListener('click', () => resetChatScale());
  els.toggleRect.addEventListener('click', () => setAnnotationMode('rect'));

  els.clearAnnotations.addEventListener('click', async () => {
    await api('/api/gui/annotations/clear', { method: 'POST', body: JSON.stringify({}) });
    state.annotations = [];
    renderAnnotations();
  });

  let rectStart = null;
  els.layer.addEventListener('click', async (event) => {
    if (state.annotateMode !== 'point') return;
    const p = normalizedPointFromEvent(event);
    await addAnnotation({ kind: 'point', x: p.x, y: p.y, label: 'user marker', metadata: p });
    await api('/api/gui/pointer_event', { method: 'POST', body: JSON.stringify({ x: p.x, y: p.y, label: 'point annotation', metadata: p }) });
  });

  els.layer.addEventListener('pointerdown', (event) => {
    if (state.annotateMode !== 'rect') return;
    rectStart = { x: event.clientX, y: event.clientY };
    els.preview.hidden = false;
    els.preview.style.left = `${rectStart.x}px`;
    els.preview.style.top = `${rectStart.y}px`;
    els.preview.style.width = '0px';
    els.preview.style.height = '0px';
    event.preventDefault();
  });

  window.addEventListener('pointermove', (event) => {
    if (!rectStart) return;
    const left = Math.min(rectStart.x, event.clientX);
    const top = Math.min(rectStart.y, event.clientY);
    const width = Math.abs(event.clientX - rectStart.x);
    const height = Math.abs(event.clientY - rectStart.y);
    els.preview.style.left = `${left}px`;
    els.preview.style.top = `${top}px`;
    els.preview.style.width = `${width}px`;
    els.preview.style.height = `${height}px`;
  });

  window.addEventListener('pointerup', async (event) => {
    if (!rectStart) return;
    const left = Math.min(rectStart.x, event.clientX);
    const top = Math.min(rectStart.y, event.clientY);
    const width = Math.abs(event.clientX - rectStart.x);
    const height = Math.abs(event.clientY - rectStart.y);
    rectStart = null;
    els.preview.hidden = true;
    if (width < 8 || height < 8) return;
    await addAnnotation({
      kind: 'rect',
      x: left / window.innerWidth,
      y: top / window.innerHeight,
      w: width / window.innerWidth,
      h: height / window.innerHeight,
      label: 'selected region',
      metadata: { screen_x: left, screen_y: top, screen_w: width, screen_h: height },
    });
  });
}

function installPanelHandlers() {
  let drag = null;
  let resize = null;
  let panelChanged = false;
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  els.panelHeader.addEventListener('pointerdown', (event) => {
    if (state.dockMode !== 'float' || resize) return;
    if (event.target.closest('button') || event.target.closest('select')) return;
    const rect = els.panel.getBoundingClientRect();
    drag = { dx: event.clientX - rect.left, dy: event.clientY - rect.top };
    panelChanged = false;
    els.panel.classList.add('dragging');
    els.panelHeader.setPointerCapture?.(event.pointerId);
  });

  (els.panelResizeHandles.length ? els.panelResizeHandles : [els.panelResize]).filter(Boolean).forEach((handle) => {
    handle.addEventListener('pointerdown', (event) => {
      const rect = els.panel.getBoundingClientRect();
      resize = {
        mode: state.dockMode || 'float',
        corner: handle.dataset.resizeCorner || 'se',
        startX: event.clientX,
        startY: event.clientY,
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
      };
      panelChanged = false;
      drag = null;
      els.panel.classList.add('resizing');
      handle.setPointerCapture?.(event.pointerId);
      event.preventDefault();
      event.stopPropagation();
    });
  });

  window.addEventListener('pointermove', (event) => {
    if (resize) {
      panelChanged = true;
      const mode = resize.mode;
      if (mode === 'left' || mode === 'right') {
        const size = mode === 'left' ? event.clientX : window.innerWidth - event.clientX;
        const value = `${clamp(size, 300, Math.max(320, window.innerWidth * 0.75))}px`;
        document.documentElement.style.setProperty('--agent-dock-side-size', value);
        els.panel.style.setProperty('--agent-dock-side-size', value);
        return;
      }
      if (mode === 'top' || mode === 'bottom') {
        const size = mode === 'top' ? event.clientY : window.innerHeight - event.clientY;
        const value = `${clamp(size, 240, Math.max(260, window.innerHeight * 0.72))}px`;
        document.documentElement.style.setProperty('--agent-dock-block-size', value);
        els.panel.style.setProperty('--agent-dock-block-size', value);
        return;
      }

      const minWidth = 300;
      const minHeight = 280;
      const maxWidth = Math.max(minWidth, window.innerWidth - 16);
      const maxHeight = Math.max(minHeight, window.innerHeight - 16);
      let left = resize.left;
      let top = resize.top;
      let width = resize.width;
      let height = resize.height;
      const corner = resize.corner || 'se';

      if (corner.includes('w')) {
        width = clamp(resize.right - event.clientX, minWidth, Math.min(maxWidth, resize.right - 8));
        left = resize.right - width;
      } else {
        width = clamp(event.clientX - resize.left, minWidth, Math.min(maxWidth, window.innerWidth - resize.left - 8));
      }

      if (corner.includes('n')) {
        height = clamp(resize.bottom - event.clientY, minHeight, Math.min(maxHeight, resize.bottom - 8));
        top = resize.bottom - height;
      } else {
        height = clamp(event.clientY - resize.top, minHeight, Math.min(maxHeight, window.innerHeight - resize.top - 8));
      }

      els.panel.style.width = `${width}px`;
      els.panel.style.height = `${height}px`;
      els.panel.style.left = `${clamp(left, 8, window.innerWidth - width - 8)}px`;
      els.panel.style.top = `${clamp(top, 70, window.innerHeight - height - 8)}px`;
      els.panel.style.right = 'auto';
      els.panel.style.bottom = 'auto';
      return;
    }

    if (!drag) return;
    panelChanged = true;
    const maxLeft = window.innerWidth - els.panel.offsetWidth - 8;
    const maxTop = window.innerHeight - els.panel.offsetHeight - 8;
    const left = Math.max(8, Math.min(maxLeft, event.clientX - drag.dx));
    const top = Math.max(70, Math.min(maxTop, event.clientY - drag.dy));
    els.panel.style.left = `${left}px`;
    els.panel.style.top = `${top}px`;
    els.panel.style.right = 'auto';
    els.panel.style.bottom = 'auto';
  });

  window.addEventListener('pointerup', () => {
    const shouldPersist = panelChanged && (drag || resize);
    drag = null;
    resize = null;
    panelChanged = false;
    els.panel.classList.remove('dragging', 'resizing');
    if (shouldPersist) persistAgentPanelPrefs();
  });

  els.collapsePanel.addEventListener('click', () => {
    els.panel.hidden = true;
    els.panelTab.hidden = false;
  });
  els.panelTab.addEventListener('click', () => {
    els.panel.hidden = false;
    els.panelTab.hidden = true;
  });
  els.refreshState.addEventListener('click', bootstrap);
  els.approvalRefresh?.addEventListener('click', refreshApprovals);
  els.includeVisualContext?.addEventListener('change', () => {
    state.attachWorkspaceView = Boolean(els.includeVisualContext.checked);
    updateUiPrefs({ attachWorkspaceView: state.attachWorkspaceView });
    syncComposerContextControls();
  });
  els.allowAgentInspect?.addEventListener('change', () => {
    state.allowAgentInspect = Boolean(els.allowAgentInspect.checked);
    updateUiPrefs({ allowAgentInspect: state.allowAgentInspect });
    syncComposerContextControls({ toast: true });
  });
  els.allowAgentCapture?.addEventListener('change', () => {
    state.allowAgentCapture = Boolean(els.allowAgentCapture.checked);
    updateUiPrefs({ allowAgentCapture: state.allowAgentCapture });
    syncComposerContextControls({ toast: true });
  });

  els.messageInput?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || !event.shiftKey || event.altKey || event.ctrlKey || event.metaKey || event.isComposing) return;
    event.preventDefault();
    if (typeof els.messageForm.requestSubmit === 'function') els.messageForm.requestSubmit();
    else els.messageForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
  });

  els.messageForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const content = els.messageInput.value.trim();
    if (!content) return;
    els.messageInput.value = '';
    const visualContext = els.includeVisualContext?.checked ? currentVisualContext() : {};
    try {
      const data = await api('/api/gui/message', {
        method: 'POST',
        body: JSON.stringify({ content, visual_context: visualContext }),
      });
      state.messages.push(...(data.messages || []));
      renderMessages();
    } catch (error) {
      showToast(error.message, 4000);
    }
  });
}


function installDashboardPanelHandlers() {
  if (!els.dashboard) return;
  let moving = null;
  let sizing = null;
  let changedCard = null;
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  els.dashboard.addEventListener('pointerdown', (event) => {
    const card = event.target.closest('.dashboard-panel');
    if (!card) return;
    const grid = event.target.closest('.dashboard-grid');
    const floating = Boolean(grid?.classList.contains('is-floating'));
    const gridRect = grid?.getBoundingClientRect();
    const rect = card.getBoundingClientRect();
    card.style.zIndex = String(Date.now() % 100000);

    const resizeHandle = event.target.closest('.dashboard-panel-resize');
    if (resizeHandle) {
      sizing = { card, gridRect, floating, left: rect.left, top: rect.top, width: rect.width, height: rect.height };
      changedCard = card;
      resizeHandle.setPointerCapture?.(event.pointerId);
      event.preventDefault();
      event.stopPropagation();
      return;
    }

    if (!floating) return;
    const bar = event.target.closest('.dashboard-panel-bar');
    if (!bar || event.target.closest('button')) return;
    moving = { card, gridRect, dx: event.clientX - rect.left, dy: event.clientY - rect.top };
    changedCard = card;
    bar.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  });

  window.addEventListener('pointermove', (event) => {
    if (sizing) {
      const gridRight = sizing.gridRect ? sizing.gridRect.right : window.innerWidth;
      const width = clamp(event.clientX - sizing.left, 280, Math.max(280, gridRight - sizing.left - 12));
      const height = clamp(event.clientY - sizing.top, 220, Math.max(220, window.innerHeight - sizing.top - 12));
      sizing.card.style.height = `${height}px`;
      sizing.card.style.minHeight = `${height}px`;
      if (sizing.floating) {
        sizing.card.style.width = `${width}px`;
      } else {
        sizing.card.style.setProperty('--dashboard-panel-custom-width', `${width}px`);
      }
      return;
    }
    if (!moving) return;
    const gridRect = moving.gridRect;
    const baseLeft = gridRect ? gridRect.left : 0;
    const baseTop = gridRect ? gridRect.top : 0;
    const maxLeft = Math.max(8, (gridRect?.width || window.innerWidth) - moving.card.offsetWidth - 8);
    const left = clamp(event.clientX - moving.dx - baseLeft, 8, maxLeft);
    const top = Math.max(8, event.clientY - moving.dy - baseTop);
    moving.card.style.left = `${left}px`;
    moving.card.style.top = `${top}px`;
  });

  window.addEventListener('pointerup', () => {
    if (changedCard) saveDashboardPanelLayout(changedCard);
    moving = null;
    sizing = null;
    changedCard = null;
  });
}

function upsertApp(app) {
  if (!app || !app.id) return;
  const idx = state.apps.findIndex((a) => a.id === app.id);
  if (idx >= 0) state.apps[idx] = app;
  else state.apps.unshift(app);
}

function applyEvent(event) {
  const t = event?.type;
  const payload = event?.payload || {};

  if (t === 'app_registered') {
    upsertApp(payload);
    renderDiscovery();
    return;
  }

  if (t === 'app_removed') {
    const appId = payload.app_id;
    if (appId) state.apps = state.apps.filter((a) => a.id !== appId);
    renderDiscovery();
    return;
  }


  if (t === 'dashboard_created') {
    upsertDashboard(payload);
    markDashboardActivity(payload?.id);
    renderDiscovery();
    if (state.workspace?.mode === 'dashboard') renderWorkspace();
    return;
  }

  if (t === 'dashboard_panel_added' || t === 'dashboard_panel_updated' || t === 'dashboard_panel_zoom_changed') {
    if (payload?.dashboard) {
      upsertDashboard(payload.dashboard);
      markDashboardActivity(payload.dashboard.id);
    }
    renderDiscovery();
    if (state.workspace?.mode === 'dashboard') renderWorkspace();
    return;
  }

  if (t === 'dashboard_panel_removed') {
    if (payload?.dashboard) upsertDashboard(payload.dashboard);
    renderDiscovery();
    if (state.workspace?.mode === 'dashboard') renderWorkspace();
    showToast(`Dashboard panel closed: ${payload?.panel?.title || payload?.panel?.name || 'panel'}`, 2200);
    return;
  }

  if (t === 'dashboard_removed') {
    if (payload?.dashboard_id) state.dashboards = (state.dashboards || []).filter((d) => d.id !== payload.dashboard_id);
    if (payload?.next_dashboard) upsertDashboard(payload.next_dashboard);
    if (payload?.workspace) state.workspace = payload.workspace;
    renderWorkspace();
    renderDiscovery();
    showToast(`Dashboard closed: ${payload?.dashboard?.name || 'dashboard'}`, 2200);
    return;
  }

  if (t === 'dashboard_opened') {
    if (payload?.dashboard) upsertDashboard(payload.dashboard);
    if (payload?.workspace) state.workspace = payload.workspace;
    clearDashboardActivity(payload?.dashboard?.id || payload?.workspace?.metadata?.active_dashboard_id);
    renderWorkspace();
    renderDiscovery();
    showToast(`Agent opened dashboard: ${payload?.dashboard?.name || 'dashboard'}`, 2200);
    return;
  }

  if (t === 'workspace_opened') {
    if (payload?.url) {
      state.workspace = {
        ...(state.workspace || {}),
        mode: payload.mode || state.workspace?.mode || 'browser',
        url: payload.url,
        title: payload.name || payload.url,
      };
      renderWorkspace();
    }
    return;
  }

  if (t === 'workspace_app_opened') {
    if (payload?.app) upsertApp(payload.app);
    if (payload?.workspace) {
      state.workspace = payload.workspace;
      renderWorkspace();
      syncActiveAppFromWorkspace();
    }
    return;
  }

  if (t === 'annotation_created') {
    if (payload?.id) {
      const exists = state.annotations.some((a) => a.id === payload.id);
      if (!exists) state.annotations.push(payload);
      renderAnnotations();
    }
    return;
  }

  if (t === 'annotations_cleared') {
    state.annotations = [];
    renderAnnotations();
    return;
  }

  if (t === 'chat_scale_changed') {
    applyChatScale(payload?.chat_scale ?? state.chatScale);
    showToast(`Chat size ${chatScaleLabel()}`, 1500);
    return;
  }

  if (t === 'agent_status') {
    const msg = payload?.message || 'Agent updated workspace';
    showToast(msg, 2200);
    return;
  }

  if (t === 'approval_requested') {
    const request = payload?.request || payload;
    if (request?.id) {
      state.approvals = (state.approvals || []).filter((r) => String(r.id || r.request_id) !== String(request.id || request.request_id));
      state.approvals.unshift(request);
      renderApprovals();
      showToast(`Permission requested: ${request.kind || request.action || 'agent action'}`, 4000);
    }
    return;
  }

  if (t === 'approval_decided') {
    const request = payload?.request || {};
    const id = request.id || request.request_id || payload?.request_id;
    if (id) {
      state.approvals = (state.approvals || []).filter((r) => String(r.id || r.request_id) !== String(id));
      renderApprovals();
    }
    return;
  }

  if (t === 'message_created') {
    if (payload?.id) {
      const exists = state.messages.some((m) => m.id === payload.id);
      if (!exists) state.messages.push(payload);
      renderMessages();
    }
  }
}

async function pollEvents() {
  try {
    const data = await api(`/api/gui/events?since=${state.latestSeq || 0}`);
    const events = Array.isArray(data?.events) ? data.events : [];
    for (const event of events) applyEvent(event);
    state.latestSeq = data.latest_seq || state.latestSeq || 0;
  } catch (_) {
    // Polling is opportunistic for milestone 1.
  } finally {
    setTimeout(pollEvents, 1200);
  }
}

installWorkspaceHandlers();
installToolbarGroupHandlers();
installPanelHandlers();
installDashboardPanelHandlers();
restoreAgentPanelPrefs();
applyChatScale(state.chatScale, { persist: false });
syncComposerContextControls();
renderDashboardSwitcher();
bootstrap().catch((error) => showToast(error.message, 5000));
setInterval(refreshHealth, 5000);
setInterval(() => refreshApprovals().catch(() => {}), 2500);
pollEvents();
