# Wolf GUI / VUI (`framework/gui`)

Wolf GUI is the first implementation of WOLF's emerging **VUI** concept:

> **VUI — Virtual User Interface**: a shared visual and operational workspace where a human and an agent can see, inspect, manipulate, capture, and reason over the same live environment.

Instead of putting chat at the center, Wolf GUI treats the screen as a shared work surface:
- browser pages,
- CAD/mesh views,
- generated dashboards/reports,
- actionbox-hosted mini-apps,
- permissioned live screenshots of the user's actual visible workspace.

A floating/dockable agent panel sits above that workspace so users and agents can collaborate in context.

Design principle:

> If the user can see it, the agent should be able to request permission to see it. If the user can touch it, the agent should be able to request a safe, auditable way to touch it too.

Positioning note: we should avoid an unqualified public claim that this is "the first in the world" unless supported by an external prior-art survey. What is accurate and defensible is that WOLF's VUI combines capabilities that are rarely, if ever, present together in current agent harnesses: shared GUI workspace state, agent-created dashboards, permissioned live browser-surface capture, artifact feedback into the model loop, gateway workflow continuation, and policy-controlled user consent.

---

## 1) What this GUI is for

Wolf GUI is designed for workflows where visual context matters (CAD/mesh review, simulation dashboards, browser tasking, report QA).

Core model:
- **Workspace is primary** (what both user and agent are looking at)
- **Agent panel is control plane** (chat, status, actions)
- **Agents can open visual surfaces quietly** when relevant
- **Agents can request live visual grounding** through explicit user permission
- **User remains informed and in control** via status events, permission toggles, and browser security prompts

---

## 2) How it fits WOLF infrastructure

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

## 4) VUI: Virtual User Interface

In WOLF terminology, the GUI is more than a graphical shell. It is the current concrete implementation of a **Virtual User Interface (VUI)**: a virtual shared workspace for human-agent collaboration.

The VUI differs from ordinary CLI/TUI/GUI modes:

- **CLI**: command-line interaction.
- **TUI**: terminal interaction.
- **GUI**: graphical workspace interaction.
- **VUI**: shared human-agent workspace interaction, where visual state, user intent, agent actions, dashboards, captures, artifacts, and workflow events are all first-class participants in the loop.

The VUI goal is co-presence:

- The user can look at a workspace.
- The agent can be granted access to structured visual context.
- The agent can create or update dashboards and app surfaces.
- The agent can request a permissioned live screenshot of the user's actual visible GUI tab/window.
- The resulting image artifact is fed back into the workflow so the agent reasons over the same pixels the user sees.

This is the distinction from ordinary browser automation or ordinary chat-with-tools: WOLF is not merely letting an agent call tools; it is building a shared operational world where user, agent, tools, screens, universes, captures, and memory are connected.

---

## 5) Major features

### 4.1 Visual workspace shell
- Full-screen workspace iframe
- Workspace modes:
  - Browser
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
- Cross-origin iframe DOM remains unavailable from normal browser JavaScript.
- Rendered pixels can be captured in two ways:
  1. **Preferred**: permissioned live client capture using the browser Screen Capture API (`getDisplayMedia`) after a real user click.
  2. **Fallback**: backend replay capture using the trusted Playwright-based capture worker.

---

## 6) Gateway integration: how it works now

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

## 7) Auth → Sessions → Agent/Policy setup (step-by-step)

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

## 8) API quick reference (GUI server)

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

## 9) Gateway endpoints used by GUI modal

- `POST /auth/login`
- `GET /accounts/{account_id}/sessions?token=...`
- `GET /sessions/{session_id}/params?token=...`
- `PATCH /sessions/{session_id}/params?token=...`
- `GET /sessions/{session_id}/policy?token=...`
- `POST /sessions/{session_id}/reset?token=...`
- `WS /ws/{account_id}/{session_id}?token=...&participant_id=...&participant_role=user&client_type=gui`

---

## 10) Security model

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

## 11) Run / launch

### Start GUI
```bash
./wolf gui
```

Expected default:
- local GUI server on `127.0.0.1:8765`
- browser opens automatically (depending on launch config)

### Typical usage flow
1. Open/prepare workspace (URL/dashboard)
2. Optionally annotate
3. Open Gateway modal and connect (auth → session → connect)
4. Fetch/adjust agent & policy params if needed
5. Chat in agent panel with visual context

---

## 12) Known limitations

- GUI runtime state is currently in-memory (not full durable replay yet)
- Gateway/session UX is improved but can be further refined
- URL allowlist/policy hardening can be expanded for non-local deployments

---

## 13) Files of interest

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

The composer separates three visual-context permissions:

1. **👁 Attach workspace view** pushes structured visual metadata with user messages.
2. **🔭 Allow agent inspect** allows the agent to request live metadata via `gui_get_visual_context`.
3. **📸 Allow agent capture** allows the agent to request screenshot artifacts via `gui_capture_url` / `gui_capture_workspace`.

Screenshot capture is default-off and must be explicitly enabled by the user.

### Live client capture: seeing what the user sees

For rendered workspace scopes such as `full_gui`, `workspace`, `active_dashboard`, and `annotation_regions`, WOLF now prefers **live client-surface capture**:

1. The agent emits a deferred `gui_capture_workspace` command.
2. The GUI shows a local prompt: **Agent requests live GUI capture**.
3. The user clicks **Capture live GUI**.
4. The browser opens its normal tab/window/screen sharing picker through `navigator.mediaDevices.getDisplayMedia()`.
5. The user chooses the Wolf GUI tab/window.
6. The GUI captures a PNG from the live rendered browser surface.
7. The image is uploaded to the gateway endpoint `/api/gui/capture/live`.
8. The artifact is stored under `wf_workspace/captures/...` and appended back into the workflow as an image reference for agent continuation.

This is the key VUI loop: the agent can reason over the same pixels the user is looking at, without bypassing browser security or silently taking screenshots.

### Backend replay fallback

This **backend replay fallback** preserves the previous Playwright capture path when live client capture is not possible.


If live client capture is unavailable, denied, or times out, WOLF falls back to the backend Playwright capture path. Backend replay is useful, but may differ from the visible GUI tab because it renders in a separate browser context. Live client capture is therefore preferred whenever exact visual agreement matters.

The backend still applies URL/SSRF policy for URL captures and stores captures as temporary image references under `wf_workspace/captures/...`; large base64 blobs are not embedded in chat history.
