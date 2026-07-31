# Cross-Origin Pixel Capture Workplan

## Goal

Add full pixel screenshot capture for WOLF GUI workspace panels, including pages and iframe content that browser-side JavaScript cannot inspect because of cross-origin restrictions.

The durable solution is a trusted server-side browser capture backend, most likely using Playwright or Chromium DevTools Protocol. Browser-side `gui_get_visual_context` can continue to provide structured metadata, DOM excerpts where allowed, and same-origin text, but it cannot capture cross-origin iframe pixels from inside the user browser sandbox.

## Recent Context

WOLF GUI visual-context work added two user-facing controls:

- `Attach workspace view`
- `Allow agent inspect`

The browser-side visual context can now report workspace metadata, annotations, panels, dashboard state, viewport geometry, iframe URLs/titles, inline dashboard excerpts, and same-origin iframe text where possible.

Remaining caveat: browser JavaScript cannot read cross-origin iframe pixels/DOM. Full pixel screenshots of cross-origin iframes need a Playwright/server-side capture backend.

## Problem

Browser security prevents JavaScript running in one origin from inspecting or screenshotting cross-origin iframe DOM/pixels directly. This is correct and should not be bypassed in the browser.

A trusted backend capture worker can render URLs in an isolated browser process and capture the resulting pixels, subject to explicit user permission, URL safety policy, authentication controls, rate limits, audit logs, and retention rules.

## Target Architecture

```text
Agent / workflow
  -> gui_capture_workspace or gui_capture_url action
  -> gateway capture API
  -> capture policy/security checks
  -> Playwright capture worker
  -> temporary image storage
  -> image references + metadata returned to agent/chat
```

Core principle: cross-origin pixel capture must be a permissioned backend operation, not an in-browser DOM hack.

## Components

### 1. Capture Actions

Add one or both actions:

- `gui_capture_workspace`
- `gui_capture_url`

Suggested payload fields:

- `capture_mode`: `workspace`, `panel`, or `url`
- `workspace_id`: optional session/workspace id
- `panel_ids`: optional selected panels
- `urls`: optional explicit URLs
- `viewport`: width, height, device scale factor
- `format`: png/jpeg/webp
- `quality`: jpeg/webp quality
- `full_page`: boolean
- `wait_until`: load/domcontentloaded/networkidle
- `extra_wait_ms`: post-load delay
- `max_panels`: limit batch size
- `reason`: user-visible reason for capture

Output should include per-capture metadata:

- capture id
- source URL
- panel id when applicable
- image path or image URL
- dimensions
- format
- timestamp
- status: success, blocked, timeout, auth_failed, error
- structured error when failed

### 2. Backend Capture Worker

Implement a trusted worker using Playwright.

Suggested module:

```text
framework/gui/capture_worker.py
```

Responsibilities:

1. Launch isolated Chromium contexts.
2. Navigate to allowed URLs.
3. Apply viewport/device scale factor.
4. Wait for page readiness.
5. Capture viewport or full-page screenshots.
6. Store images in temporary storage.
7. Return references and metadata.
8. Clean up browser contexts.

### 3. Gateway API

Add internal API routes in the gateway layer:

```text
POST /api/gui/capture/url
POST /api/gui/capture/workspace
GET  /api/gui/capture/{capture_id}
```

The gateway should enforce policy, resolve workspace panel URLs, forward safe capture jobs to the worker, return image references, and emit diagnostic/audit events.

## Permission Model

Existing GUI controls remain:

- `Attach workspace view`: user pushes visual metadata with messages.
- `Allow agent inspect`: user permits agent-initiated metadata inspection.

Add a distinct permission:

- `Allow agent capture screenshots`

Recommended UX:

- Default OFF.
- Clear explanation that capture may include visible page contents.
- Optional per-request confirmation for sensitive captures.
- Visible indicator while capture is running.
- Audit trail visible to user.

## Security Controls

Required controls:

1. URL allowlist / denylist.
2. Block dangerous schemes unless explicitly approved: `file://`, `ftp://`, `data:`, `javascript:`.
3. SSRF protection: block metadata endpoints, localhost, and private networks by default.
4. Timeout limits.
5. Max image size.
6. Max panels per request.
7. Rate limits.
8. Concurrency limits.
9. Audit logs.
10. Temporary storage TTL.
11. No persistent credential dumps.
12. Sanitized returned paths/URLs.

## Authentication / Session Propagation

Some panels require login.

MVP recommendation: do not propagate arbitrary browser cookies. Capture only publicly accessible URLs or explicitly configured safe internal URLs. Auth-required pages should return `auth_failed`.

Later options:

1. Explicit token/cookie exchange scoped to one origin and short TTL.
2. Browser-exported Playwright `storage_state`, only after explicit user consent.
3. Dedicated service credentials for known internal apps.

## Storage and Retention

Store screenshots outside chat payloads and return references.

Suggested local dev path:

```text
wf_workspace/captures/<session_id>/<capture_id>.png
wf_workspace/captures/<session_id>/<capture_id>.json
```

Retention:

- default short TTL, e.g. one hour or one session,
- user-configurable retention for debugging,
- cleanup job removes expired captures.

## Integration with Visual Context

`gui_get_visual_context` should remain metadata-first. The new capture action can use visual context as input:

1. Browser reports workspace/panel layout and URLs.
2. Backend capture action selects panel URLs.
3. Worker captures pixels for selected panels.
4. Agent receives both metadata and image references.

## MVP Phases

### Phase 1: Capture one URL

- Add Playwright dependency.
- Implement `capture_url` worker.
- Capture public URL to PNG.
- Save to local temp directory.
- Return image reference and metadata.

Acceptance: captures `https://example.com`; timeout and blocked URL errors are structured; image exists and dimensions are recorded.

### Phase 2: Action and Gateway Route

- Add action model.
- Add gateway API route.
- Add policy gate.
- Return image references to workflow/chat.

Acceptance: agent can request capture only when policy allows; disallowed request returns clear policy error.

### Phase 3: Workspace/Panel Capture

- Use GUI workspace metadata to identify panels/URLs.
- Capture selected panels.
- Return per-panel result objects.

Acceptance: multiple panel URLs can be captured; partial failures are reported without failing the whole batch.

### Phase 4: UX Toggle

- Add `Allow agent capture screenshots` control.
- Display capture-in-progress indicator.
- Display audit log entry.

Acceptance: user can disable/enable capture; browser/gateway reject capture if disabled.

### Phase 5: Hardening

- Add allowlist/denylist.
- Add SSRF protections.
- Add rate limits/concurrency limits.
- Add cleanup job.
- Add audit logs.

Acceptance: private network targets are blocked by default; expired captures are deleted; every capture has an audit event.

## Suggested Files

New files:

```text
framework/gui/capture_worker.py
framework/gui/capture_models.py
framework/gui/capture_storage.py
framework/gui/capture_policy.py
framework/workflows/agent_actions/gui_capture_actions.py
tests/gui/test_capture_policy.py
tests/gui/test_capture_storage.py
tests/gui/test_capture_worker.py
tests/workflows/test_gui_capture_actions.py
```

Modified files:

```text
framework/pack/gateway.py
framework/workflows/custom_workflows/gateway_action_workflow.py
framework/gui/static/index.html
framework/gui/static/app.js
framework/gui/static/gateway.js
framework/gui/static/styles.css
framework/gui/README.md
```

## Testing Plan

Unit tests:

- URL policy allow/block behavior.
- Capture request model validation.
- Metadata serialization.
- Storage path safety.
- TTL cleanup.

Integration tests:

- Capture public URL.
- Capture timeout page.
- Capture invalid URL.
- Capture blocked private URL.
- Capture multiple workspace panels.
- Verify permission toggle blocks request.

Security tests:

- Block `file://`.
- Block localhost/private IP by default.
- Block metadata IPs.
- Verify max image size.
- Verify rate limiting.

## Final Acceptance Criteria

The feature is complete when:

1. User can explicitly permit or deny screenshot capture.
2. Agent can request capture through a structured action.
3. Backend captures full pixels of allowed cross-origin panel URLs.
4. Captures return image references, not large base64 blobs.
5. Per-panel success/failure metadata is returned.
6. Dangerous URLs are blocked by default.
7. Captures are audited and expire automatically.
8. Existing visual-context metadata behavior is not regressed.

## Summary

Browser-side visual context is good for metadata and same-origin inspection. Full cross-origin pixel capture requires a trusted backend browser worker. Build this as a permissioned, audited, policy-gated Playwright capture service that returns temporary image references to the agent.
