const state = {
  workspace: { mode: 'browser', url: 'about:blank' },
  dockMode: 'float',
  annotations: [],
  messages: [],
  workflows: {},
  sessions: [],
  apps: [],
  dashboards: [],
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
  messageForm: $('message-form'),
  messageInput: $('message-input'),
  includeVisualContext: $('include-visual-context'),
  allowAgentInspect: $('allow-agent-inspect'),
  allowAgentCapture: $('allow-agent-capture'),
  workspaceSummary: $('workspace-summary'),
  workflowCount: $('workflow-count'),
  sessionCount: $('session-count'),
  toast: $('toast'),
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

state.attachWorkspaceView = state.uiPrefs.attachWorkspaceView !== undefined ? Boolean(state.uiPrefs.attachWorkspaceView) : true;
state.allowAgentInspect = Boolean(state.uiPrefs.allowAgentInspect);
state.allowAgentCapture = Boolean(state.uiPrefs.allowAgentCapture);

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

function panelSource(panel) {
  if (panel.content_html) {
    return `data:text/html;charset=utf-8,${encodeURIComponent(panel.content_html)}`;
  }
  return panel.url || 'about:blank';
}

function updateDashboardToolbar() {
  const root = $('app');
  const dashboard = activeDashboard();
  const inDashboard = Boolean((state.workspace || {}).mode === 'dashboard' && dashboard);
  root?.classList.toggle('dashboard-active', inDashboard);
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
    const empty = document.createElement('div');
    empty.className = 'dashboard-empty glass';
    empty.textContent = 'Dashboard mode is active, but no dashboard panels are available yet.';
    els.dashboard.appendChild(empty);
    updateDashboardToolbar();
    return;
  }

  state.dashboardFloat = getDashboardFloat(dashboard);
  updateDashboardToolbar();
  const panels = dashboard.panels || [];
  const header = document.createElement('div');
  header.className = 'dashboard-header glass dashboard-header-compact';
  const title = document.createElement('div');
  title.innerHTML = `<strong>${dashboard.name || 'Agent Dashboard'}</strong><span>${dashboard.description || `${panels.length} panel(s)`}</span>`;
  header.appendChild(title);
  els.dashboard.appendChild(header);

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

    const frame = document.createElement('iframe');
    frame.className = 'dashboard-panel-frame';
    frame.title = panel.title || panel.name || 'Dashboard panel';
    frame.sandbox = 'allow-same-origin allow-scripts allow-forms allow-popups allow-downloads';
    frame.src = panelSource(panel);

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

    bar.appendChild(titleGroup);
    bar.appendChild(controls);
    card.appendChild(bar);
    card.appendChild(frame);
    if (state.dashboardFloat) {
      const resize = document.createElement('div');
      resize.className = 'dashboard-panel-resize';
      resize.title = 'Resize dashboard panel';
      card.appendChild(resize);
    }
    grid.appendChild(card);
  });
  els.dashboard.appendChild(grid);
}

function upsertDashboard(dashboard) {
  if (!dashboard || !dashboard.id) return;
  const idx = state.dashboards.findIndex((d) => d.id === dashboard.id);
  if (idx >= 0) state.dashboards[idx] = dashboard;
  else state.dashboards.unshift(dashboard);
}

function renderWorkspace() {
  const workspace = state.workspace || {};
  els.toolbarMode.value = workspace.mode || 'browser';
  els.urlInput.value = workspace.url && workspace.url !== 'about:blank' && workspace.mode !== 'dashboard' ? workspace.url : '';
  els.workspaceSummary.textContent = `${workspace.mode || 'browser'} · ${workspace.title || workspace.url || 'blank'}`;

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
  state.messages.forEach((message) => {
    const metadata = message.metadata || {};
    const classes = ['message', message.role || 'assistant'];
    if (metadata.compact) classes.push('compact');
    if (metadata.card) classes.push('card');
    if (metadata.tone) classes.push(`tone-${String(metadata.tone).replace(/[^a-z0-9_-]/gi, '')}`);

    const node = document.createElement('article');
    node.className = classes.join(' ');

    const meta = document.createElement('div');
    meta.className = 'message-meta';
    const label = document.createElement('span');
    const date = message.created_at ? new Date(message.created_at * 1000).toLocaleTimeString() : '';
    label.textContent = `${message.role || 'message'} ${date}`;
    meta.appendChild(label);

    const body = document.createElement('div');
    body.className = 'message-content';
    body.textContent = message.content || '';

    const detailPayload = metadata.gateway_event || metadata.system_event || null;
    let details = null;
    if (detailPayload) {
      const info = document.createElement('button');
      info.type = 'button';
      info.className = 'message-info';
      info.title = 'Show system details';
      info.setAttribute('aria-label', 'Show system details');
      info.setAttribute('aria-expanded', 'false');
      info.textContent = 'i';

      details = document.createElement('pre');
      details.className = 'message-details';
      details.hidden = true;
      try {
        details.textContent = JSON.stringify(detailPayload, null, 2);
      } catch (_) {
        details.textContent = String(detailPayload);
      }

      info.addEventListener('click', () => {
        const open = details.hidden;
        details.hidden = !open;
        info.classList.toggle('is-open', open);
        info.setAttribute('aria-expanded', open ? 'true' : 'false');
        info.title = open ? 'Hide system details' : 'Show system details';
      });
      meta.appendChild(info);
    }

    node.appendChild(meta);
    node.appendChild(body);
    if (details) node.appendChild(details);
    els.messages.appendChild(node);
  });
  els.messages.scrollTop = els.messages.scrollHeight;
}

window.wolfGuiRenderMessages = renderMessages;

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
  renderDiscovery();
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
      dashboard_panel_metadata: true,
      dashboard_inline_html_excerpt: true,
      same_origin_iframe_dom_excerpt: 'best_effort',
      full_gui_pixel_screenshot: agentCaptureAllowed() ? "backend_capture_action_available" : false,
      cross_origin_iframe_dom: false,
      cross_origin_iframe_pixels: agentCaptureAllowed() ? "requires_gui_capture_url_or_workspace" : false,
      backend_capture_action: agentCaptureAllowed() ? 'gui_capture_url/gui_capture_workspace permitted by user toggle' : 'disabled_by_user_toggle',
      limitation_note: 'The browser can describe the Wolf GUI workspace and same-origin/inline dashboard content. Cross-origin iframe DOM or rendered pixels require the permissioned backend Playwright capture action and the Allow agent capture toggle.'
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
    annotation_count: state.annotations.length,
  };
}

window.wolfGuiCurrentVisualContext = currentVisualContext;

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
    if (!state.dashboardFloat) return;
    const card = event.target.closest('.dashboard-panel');
    if (!card) return;
    const grid = event.target.closest('.dashboard-grid.is-floating');
    const gridRect = grid?.getBoundingClientRect();
    const rect = card.getBoundingClientRect();
    card.style.zIndex = String(Date.now() % 100000);

    const resizeHandle = event.target.closest('.dashboard-panel-resize');
    if (resizeHandle) {
      sizing = { card, gridRect, left: rect.left, top: rect.top, width: rect.width, height: rect.height };
      changedCard = card;
      resizeHandle.setPointerCapture?.(event.pointerId);
      event.preventDefault();
      event.stopPropagation();
      return;
    }

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
      const width = clamp(event.clientX - sizing.left, 280, gridRight - sizing.left - 12);
      const height = clamp(event.clientY - sizing.top, 220, window.innerHeight - sizing.top - 12);
      sizing.card.style.width = `${width}px`;
      sizing.card.style.height = `${height}px`;
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
    renderDiscovery();
    if (state.workspace?.mode === 'dashboard') renderWorkspace();
    return;
  }

  if (t === 'dashboard_panel_added' || t === 'dashboard_panel_updated') {
    if (payload?.dashboard) upsertDashboard(payload.dashboard);
    renderDiscovery();
    if (state.workspace?.mode === 'dashboard') renderWorkspace();
    return;
  }

  if (t === 'dashboard_opened') {
    if (payload?.dashboard) upsertDashboard(payload.dashboard);
    if (payload?.workspace) state.workspace = payload.workspace;
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

  if (t === 'agent_status') {
    const msg = payload?.message || 'Agent updated workspace';
    showToast(msg, 2200);
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
installPanelHandlers();
installDashboardPanelHandlers();
restoreAgentPanelPrefs();
syncComposerContextControls();
bootstrap().catch((error) => showToast(error.message, 5000));
setInterval(refreshHealth, 5000);
pollEvents();
