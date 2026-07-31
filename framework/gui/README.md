# Wolf GUI (`framework/gui`)

Wolf GUI is a **visual-workspace-first** interface for Cerberus/WOLF.

Instead of putting chat at the center, Wolf GUI treats the screen as a shared work surface:
- browser pages,
- Glance/CAD/mesh views,
- generated dashboards/reports,
- actionbox-hosted mini-apps.

A floating/dockable agent panel sits above that workspace so users and agents can collaborate in context.

---

## 1) What this GUI is for

Wolf GUI is designed for workflows where visual context matters (CAD/mesh review, simulation dashboards, browser tasking, report QA).

Core model:
- **Workspace is primary** (what both user and agent are looking at)
- **Agent panel is control plane** (chat, status, actions)
- **Agents can open visual surfaces quietly** when relevant
- **User remains informed** via subtle status events

---

## 2) How it fits Cerberus/WOLF infrastructure

WOLF layers (Workflows, Tools, KnowledgeBases, Universes/ActionBoxes) produce computation and artifacts.
Wolf GUI is the **human-facing visual collaboration surface** on top of those layers.

### Integration modes

1. **In-process (preferred for local workflows)**
   - Workflow/agent code can call runtime controller directly:
   - `GuiWorkspaceController.register_app/open_app/annotate/notify/...`

2. **External/actionbox integration**
   - Actionbox hosts app/dashboard/viewer and exposes URL
   - Posts metadata to GUI API (e.g. `publish_app`)
- Can request visual workspace context through the gateway `gui_get_visual_context` command when the user enables **Allow agent inspect**
   - GUI registers + opens app + emits events

3. **Gateway-connected chat orchestration**
   - GUI authenticates with gateway
   - GUI joins a selected gateway session over websocket
   - Chat is routed through gateway workflow/runtime events

---

## 3) Architecture (current)

### Backend
- `framework/gui/server.py`
  - stdlib HTTP server
  - static file serving
  - JSON API endpoints
  - mutation-route control token enforcement

- `framework/gui/runtime.py`
  - in-memory GUI runtime state
  - workspace state, annotations, chat messages
  - app/dashboard registries
  - event stream (`events_since`)
  - runtime-native controller (`GuiWorkspaceController`)

### Frontend
- `framework/gui/static/index.html`
- `framework/gui/static/styles.css`
- `framework/gui/static/app.js`
- `framework/gui/static/gateway.js` (gateway auth/session/config/chat state machine)

---

## 4) Major features

### 4.1 Visual workspace shell
- Full-screen workspace iframe
- Workspace modes:
  - Browser
  - Glance (URL-based)
  - Dashboard mode (multi-panel)

### 4.2 Floating + dockable + resizable agent panel
- Float mode + drag-to-move
- Dock Left / Right / Top / Bottom
- Collapse/minimize behavior
- **Resizable**:
  - Float: corner drag resize
  - Dock left/right: horizontal edge resize
  - Dock top/bottom: vertical edge resize

### 4.3 Annotation collaboration
- Point markers
- Rectangle selections
- Clear annotations
- Pointer event capture

### 4.4 Agent-managed app/dashboard registry
- Register/list/open/remove apps
- Dashboard create/add-panel/update/open/publish
- Provenance fields (source/universe/workflow/session/etc.)

### 4.5 Gateway-connected chat + workflow events
- Auth + session-aware websocket transport
- Workflow events rendered in GUI
- `send_message` workflow results rendered as assistant bubbles
- Non-chat workflow events rendered as system entries

### 4.6 Cleaner chat UX for system metadata
- Extra gateway/system payload is now hidden by default
- Messages with metadata get an **info (`i`) button**
- Clicking `i` expands/collapses detailed payload JSON

### 4.7 Visual context controls
Wolf GUI exposes two explicit workspace-context modes in the chat composer:

1. **👁 Attach workspace view**
   - User-driven push mode.
   - When enabled, every user message includes a structured `visual_context` packet describing the current Wolf GUI workspace.
   - The packet includes workspace mode/URL, viewport geometry, annotations, app/dashboard summaries, active dashboard metadata, dashboard panel bounds, iframe URLs/titles, inline dashboard HTML excerpts, and same-origin iframe text excerpts where browser security permits.

2. **🔭 Allow agent inspect**
   - Agent-driven pull mode.
   - When enabled, a gateway-connected agent may request the current workspace context on demand via `gui_get_visual_context`.
   - When disabled, the GUI rejects that action with a user-permission error.

Browser security note:
- The GUI can always provide structured workspace/panel metadata.
- Same-origin or inline dashboard content can be inspected on a best-effort basis.
- Cross-origin iframe DOM and full rendered pixels are not available from normal browser JavaScript; those require a separate capture backend such as Playwright.

---

## 5) Gateway integration: how it works now

The GUI gateway modal is now a stricter state machine:

1. **Authenticate**
2. **Fetch sessions**
3. **Select existing session OR create new session**
4. **Connect selected session** (websocket)
5. **Fetch/edit/commit agent params and policy params**

Important distinction:
- **Gateway auth token** = token returned by `/auth/login`, used for gateway account/session APIs.
- **Provider API key** (LLM key) = belongs in **Agent parameters** section, not gateway-auth stage.

The UI now enforces this flow by disabling controls until prerequisites are met.

---

## 6) Auth → Sessions → Agent/Policy setup (step-by-step)

1. Open GUI and click **Gateway**.
2. Enter:
   - Gateway URL
   - Username
   - Password
3. Click **Authenticate**.
4. Click **Fetch sessions** (if needed).
5. Select a session from dropdown, or click **Create new session**.
6. Click **Connect selected session**.
7. After websocket connects, GUI auto-fetches:
   - Agent params (`/sessions/{id}/params`)
   - Policy params (`/sessions/{id}/policy`)
8. Use explicit buttons to:
   - **Fetch agent params** / **Commit agent params**
   - **Fetch policy params** / **Commit policy params**
9. Chat from the main panel; messages route through gateway workflow.

Notes:
- On auth/session 401/403, GUI clears stale auth state and asks for re-auth.
- After commit of agent/policy params, GUI reconnects websocket so chat uses updated runtime.

---

## 7) API quick reference (GUI server)

### Read/bootstrap
- `GET /api/gui/health`
- `GET /api/gui/bootstrap`
- `GET /api/gui/workflows`
- `GET /api/gui/sessions`
- `GET /api/gui/config`
- `GET /api/gui/messages`
- `GET /api/gui/annotations`
- `GET /api/gui/events?since=<seq>`
- `GET /api/gui/apps`
- `GET /api/gui/dashboards`

### Workspace/app/dashboard control
- `POST /api/gui/workspace/open_url`
- `POST /api/gui/workspace/open_glance`
- `POST /api/gui/workspace/open_app`
- `POST /api/gui/apps/register`
- `POST /api/gui/apps/{app_id}/remove`
- `POST /api/gui/actionbox/publish_app`
- `POST /api/gui/dashboards/create`
- `POST /api/gui/dashboards/add_panel`
- `POST /api/gui/dashboards/update_panel`
- `POST /api/gui/dashboards/open`
- `POST /api/gui/dashboards/publish`

### Collaboration input
- `POST /api/gui/message`
- `POST /api/gui/annotations`
- `POST /api/gui/annotations/clear`
- `POST /api/gui/pointer_event`

### Runtime-control bridge
- `POST /api/gui/control`

---

## 8) Gateway endpoints used by GUI modal

- `POST /auth/login`
- `GET /accounts/{account_id}/sessions?token=...`
- `GET /sessions/{session_id}/params?token=...`
- `PATCH /sessions/{session_id}/params?token=...`
- `GET /sessions/{session_id}/policy?token=...`
- `POST /sessions/{session_id}/reset?token=...`
- `WS /ws/{account_id}/{session_id}?token=...&participant_id=...&participant_role=user&client_type=gui`

---

## 9) Security model

### GUI mutation token (GUI server)
Set:
```bash
export WOLF_GUI_CONTROL_TOKEN="your-secret-token"
```
Use header on mutation/control routes:
```text
X-Wolf-Gui-Token: your-secret-token
```

### Gateway auth
- Obtained from gateway `/auth/login`
- Used for gateway account/session REST + websocket auth

---

## 10) Run / launch

### Start GUI
```bash
./wolf gui
```

Expected default:
- local GUI server on `127.0.0.1:8765`
- browser opens automatically (depending on launch config)

### Typical usage flow
1. Open/prepare workspace (URL/Glance/dashboard)
2. Optionally annotate
3. Open Gateway modal and connect (auth → session → connect)
4. Fetch/adjust agent & policy params if needed
5. Chat in agent panel with visual context

---

## 11) Known limitations

- GUI runtime state is currently in-memory (not full durable replay yet)
- Gateway/session UX is improved but can be further refined
- URL allowlist/policy hardening can be expanded for non-local deployments

---

## 12) Files of interest

- `framework/gui/__init__.py`
- `framework/gui/server.py`
- `framework/gui/runtime.py`
- `framework/gui/static/index.html`
- `framework/gui/static/styles.css`
- `framework/gui/static/app.js`
- `framework/gui/static/gateway.js`
- `gui_implementation.md`

---

If onboarding as a developer, start with:
1. `gui_implementation.md`
2. `framework/gui/runtime.py`
3. `framework/gui/server.py`
4. `framework/gui/static/gateway.js`
5. `framework/gui/static/app.js`


## Permissioned screenshot capture

The composer now separates three visual-context permissions:

1. **👁 Attach workspace view** pushes structured visual metadata with user messages.
2. **🔭 Allow agent inspect** allows the agent to request live metadata via `gui_get_visual_context`.
3. **📸 Allow agent capture** allows the browser client to forward `gui_capture_url` / `gui_capture_workspace` requests to the gateway backend screenshot-capture service.

Screenshot capture is default-off and must be explicitly enabled by the user. The backend still applies URL/SSRF policy and stores captures as temporary image references under `wf_workspace/captures/...`; it does not embed large base64 blobs in chat. Cross-origin iframe pixels are captured by the trusted backend browser worker, not by trying to bypass browser same-origin rules in page JavaScript.
