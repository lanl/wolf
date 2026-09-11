(() => {
  'use strict';

  const INFO_TYPES = new Set(['system', 'presence', 'agent_pool_update', 'workflow_status', 'policy_resolved', 'gui_route_resolved', 'gui_command_result', 'workflow_action', 'orchestration_event', 'orchestration_snapshot', 'raw']);
  const IMPORTANT_TYPES = new Set(['error', 'workflow_error', 'warning']);
  const BUNDLE_ID = 'systemNoticeBundle';

  function $(id) { return document.getElementById(id); }

  function escHtml(value) {
    return String(value ?? '').replace(/[&<'"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[ch] || ch));
  }

  function textOf(node) {
    return String(node?.textContent || '').toLowerCase();
  }

  function toneFor(node) {
    const text = textOf(node);
    const classes = Array.from(node.classList || []).join(' ').toLowerCase();
    const dataTone = String(node?.dataset?.messageTone || '').toLowerCase();
    if (dataTone === 'error' || IMPORTANT_TYPES.has(classes) || classes.includes('error') || text.includes('error') || text.includes('failed') || text.includes('exception') || text.includes('traceback')) return 'error';
    if (dataTone === 'warning' || classes.includes('warn') || text.includes('warning') || text.includes('warn') || text.includes('timeout') || text.includes('blocked') || text.includes('retry') || text.includes('waiting')) return 'warning';
    return 'info';
  }

  function isSystemNotice(node) {
    if (!node || !node.classList || !node.classList.contains('message')) return false;
    if (node.id === BUNDLE_ID || node.classList.contains('system-notice-group')) return false;
    if (node.classList.contains('user') || node.classList.contains('assistant') || node.classList.contains('agent')) return false;
    if (node.classList.contains('system') || node.classList.contains('warning') || node.classList.contains('error')) return true;
    const type = String(node.dataset?.messageType || '').toLowerCase();
    if (INFO_TYPES.has(type) || IMPORTANT_TYPES.has(type)) return true;
    for (const cls of node.classList) if (INFO_TYPES.has(cls) || IMPORTANT_TYPES.has(cls)) return true;
    return false;
  }

  function noticeLabel(node) {
    const type = node?.dataset?.messageType || Array.from(node?.classList || []).find((c) => INFO_TYPES.has(c) || IMPORTANT_TYPES.has(c)) || 'notice';
    const text = String(node?.querySelector?.('.message-content')?.textContent || node?.textContent || '').trim().replace(/\s+/g, ' ');
    return `${type}${text ? ` · ${text.slice(0, 96)}` : ''}`;
  }

  function install() {
    const messages = $('messages');
    const toggle = $('toggleSystemNotices');
    const summary = $('systemNoticeSummary');
    if (!messages || !toggle || !summary) return;

    let showInfo = localStorage.getItem('wolf.gateway.showSystemNotices') === 'true';

    function ensureBundle() {
      let bundle = $(BUNDLE_ID);
      if (!bundle) {
        bundle = document.createElement('button');
        bundle.id = BUNDLE_ID;
        bundle.type = 'button';
        bundle.className = 'message system-notice-group compact';
        bundle.addEventListener('click', () => toggle.click());
        messages.insertBefore(bundle, messages.firstChild || null);
      }
      return bundle;
    }

    function refresh() {
      const notices = Array.from(messages.querySelectorAll('.message')).filter(isSystemNotice);
      let info = 0, warning = 0, error = 0;
      const hiddenInfoLabels = [];
      for (const node of notices) {
        const tone = toneFor(node);
        node.classList.add('system-notice', `tone-${tone}`);
        node.classList.toggle('is-hidden', tone === 'info' && !showInfo);
        if (tone === 'error') error += 1;
        else if (tone === 'warning') warning += 1;
        else {
          info += 1;
          if (hiddenInfoLabels.length < 3) hiddenInfoLabels.push(noticeLabel(node));
        }
      }
      const hidden = showInfo ? 0 : info;
      summary.textContent = notices.length ? `${notices.length} system notices · ${hidden} hidden · ${warning} warnings · ${error} errors` : 'No system notices.';
      toggle.textContent = showInfo ? 'System notices shown' : 'System notices hidden';
      toggle.setAttribute('aria-pressed', showInfo ? 'true' : 'false');
      toggle.classList.toggle('is-active', showInfo);
      toggle.classList.toggle('has-warning', warning > 0);
      toggle.classList.toggle('has-error', error > 0);

      const bundle = $(BUNDLE_ID);
      if (bundle) bundle.remove();
    }

    toggle.addEventListener('click', () => {
      showInfo = !showInfo;
      localStorage.setItem('wolf.gateway.showSystemNotices', String(showInfo));
      refresh();
    });

    new MutationObserver(refresh).observe(messages, { childList: true, subtree: false });
    refresh();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
})();

// Static class markers used by regression tests: tone-warning tone-error system-notice-group has-warning has-error summary-bar-only summary-bar-only
