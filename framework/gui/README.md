# Wolf GUI / VUI (`framework/gui`)

Wolf GUI is WOLF's local **VUI** implementation: a browser-based visual collaboration workspace where a human user and WOLF agents can share visual context, open artifacts, inspect dashboards, request screenshots, exchange messages, and approve permissioned actions.

> **VUI — Virtual User Interface**: a shared human-agent workspace where visual state, workflow events, permission decisions, captured artifacts, and chat all participate in the same operational loop.

The GUI can run standalone as a local visual workspace, or it can connect to a WOLF Gateway for authenticated sessions, orchestration, permission routing, visual-context capture, and agent execution.

---

## Quick start

```bash
./wolf gui
```

Default launch behavior:

- local GUI HTTP server: `http://127.0.0.1:8765/`
- static frontend served from `framework/gui/static/`
- browser opens automatically unless disabled by launch config or `--no-browser`

Useful flags:

```bash
./wolf gui --gui-host 127.0.0.1 --gui-port 8765
./wolf gui --gateway-url http://127.0.0.1:8000
./wolf gui --generate-gui-token
./wolf gui --gui-token <token>
./wolf gui --no-browser
```

CLI launch path:

```text
framework/cli/wolf_app.py     -> parses `wolf gui` flags and env settings
framework/cli/launchers.py    -> launch_gui(...) calls start_gui_server(...)
framework/gui/server.py       -> local GUI HTTP server
```

---

## What the VUI is for

Wolf GUI is designed for workflows where visual context matters:

- generated reports and dashboards
- browser views
- simulation or analysis artifacts
- CAD/mesh/Glance-style visual inspection
- actionbox-hosted mini-apps
- screenshots of the actual visible GUI tab/window
- human approval of permissioned operations

Design principle:

```text
If the user can see it, the agent can request permission to inspect it.
If the agent needs permission to do something, the user should be able to approve, deny,
or approve for the session through the VUI.
```

---

## Important files

```text
framework/gui/server.py              # Local stdlib HTTP server and GUI API routes
framework/gui/runtime.py             # In-memory GUI state, events, apps, dashboards, approvals
framework/gui/static/index.html      # Browser shell and gateway modal markup
framework/gui/static/styles.css      # VUI layout, panels, gateway modal, dashboard styling
framework/gui/static/app.js          # Local workspace state, annotations, dashboards, approvals
framework/gui/static/gateway.js      # Gateway auth/session/config/websocket/client-command bridge
```

Adjacent gateway/orchestration files that directly affect GUI behavior:

```text
framework/pack/gateway.py                              # Gateway REST/websocket server and bridge
framework/pack/orchestration_session.py                # Orchestrated task runtime and GUI-result attachment
framework/workflows/custom_workflows/gateway_action_workflow.py
                                                        # Action workflow, deferred GUI commands, syscall guard
framework/workflows/agent_actions/gui_actions.py        # GUI action schemas/execution helpers
framework/workflows/agent_actions/system_actions.py     # run_syscall action and approval requests
framework/infrastructure/base_infrastructure.py         # PermissionManager integration helpers
framework/permissions/                                 # Permission request/decision models and manager
```

---

## Local server architecture

### `server.py`

`framework/gui/server.py` provides a small stdlib HTTP server based on `ThreadingHTTPServer`.

Key objects and functions:

- `GuiRequestHandler`
  - serves static files from `framework/gui/static/`
  - exposes local JSON API routes under `/api/gui/...`
  - checks `WOLF_GUI_CONTROL_TOKEN` for mutation routes when configured
- `create_gui_server(config, host, port)`
  - creates `GuiRuntime`
  - binds a request-handler class to that runtime
- `start_gui_server(config, host, port, open_browser)`
  - starts the server
  - optionally opens the browser

### `runtime.py`

`framework/gui/runtime.py` owns local in-memory GUI state:

- `WorkspaceState`
- `Annotation`
- `ChatMessage`
- `WorkspaceApp`
- `Dashboard`
- `DashboardPanel`
- local approval request queue
- event log
- `GuiWorkspaceController`
- `GuiControllerClient`

Important `GuiRuntime` methods:

- `bootstrap()` — initial GUI state for the browser.
- `health()` — service health and state counts.
- `open_url()` / `open_glance()` — open browser or Glance-style workspace.
- `register_app()` / `open_app()` / `remove_app()` — app registry.
- `create_dashboard()` / `add_dashboard_panel()` / `update_dashboard_panel()` / `open_dashboard()` — dashboard registry and workspace routing.
- `add_annotation()` / `clear_annotations()` / `pointer_event()` — annotation and pointer state.
- `add_message()` — local placeholder chat path when no gateway is connected.
- `create_approval_request()` / `decide_approval_request()` — local approval queue.
- `events_since(seq)` — event polling for frontend updates.

Current persistence note: local GUI runtime state is in-memory. Gateway workflow/session state is persisted under `wf_workspace/`.

---

## Static frontend architecture

### `index.html`

Defines the browser UI shell:

- workspace iframe
- dashboard workspace
- annotation layer
- toolbar
- floating/dockable agent panel
- approval panel
- Gateway modal
- Gateway tabs: Connect, Agent, Policy, Kanban

### `app.js`

Owns local browser-side GUI state at `window.wolfGuiState`.

Major responsibilities:

- bootstrap from `/api/gui/bootstrap`
- render workspace iframe or dashboard workspace
- handle dashboard panel floating/resizing/layout persistence
- manage annotations and pointer events
- collect structured visual context
- render local messages
- render approval cards
- poll `/api/gui/events`
- expose hooks consumed by `gateway.js`

Important exported hooks:

```javascript
window.wolfGuiState
window.wolfGuiCurrentVisualContext
window.wolfGuiAgentInspectAllowed
window.wolfGuiAgentCaptureAllowed
window.wolfGuiAddApprovalRequest
window.wolfGuiResolveApprovalRequest
window.wolfGuiRenderApprovals
window.wolfGuiRenderMessages
window.wolfGuiShowToast
window.wolfGuiPulsePermissionPanel
```

### `gateway.js`

Owns browser-side gateway integration:

- gateway authentication
- session listing/creation/selection
- websocket connection
- agent parameter form
- policy parameter form
- permission request display
- gateway chat send path
- run-control buttons
- GUI command execution
- screenshot capture bridge
- orchestration Kanban updates

Gateway client state is persisted in browser localStorage under:

```text
wolfGatewayStateV3
```

Agent preset cache keys:

```text
wolf.gateway.agentPresets.v1
wolf.gateway.selectedAgentPreset.v1
```

---

## Local GUI API reference

### Read/bootstrap routes

```text
GET /api/gui/health
GET /api/gui/bootstrap
GET /api/gui/workflows
GET /api/gui/sessions
GET /api/gui/config
GET /api/gui/apps
GET /api/gui/dashboards
GET /api/gui/annotations
GET /api/gui/messages
GET /api/gui/approvals
GET /api/gui/approvals/{request_id}
GET /api/gui/events?since=<seq>
```

### Workspace/app/dashboard routes

```text
POST /api/gui/workspace/open_url
POST /api/gui/workspace/open_glance
POST /api/gui/workspace/open_app
POST /api/gui/apps/register
POST /api/gui/apps/{app_id}/remove
POST /api/gui/actionbox/publish_app
POST /api/gui/dashboards/create
POST /api/gui/dashboards/add_panel
POST /api/gui/dashboards/update_panel
POST /api/gui/dashboards/open
POST /api/gui/dashboards/publish
```

### Collaboration and approval routes

```text
POST /api/gui/message
POST /api/gui/annotations
POST /api/gui/annotations/clear
POST /api/gui/pointer_event
POST /api/gui/approvals/request
POST /api/gui/approvals/{request_id}/decision
POST /api/gui/control
```

Mutation/control routes require `X-Wolf-Gui-Token` only when `WOLF_GUI_CONTROL_TOKEN` is set.

---

## Visual context model

The GUI exposes three user-facing context controls in the chat composer.

### 1. Attach workspace view

User-push mode. When enabled, each user message sent through the gateway includes a structured `visual_context` packet.

The packet includes:

- schema/version metadata
- GUI viewport geometry
- workspace mode and URL
- active dashboard metadata
- dashboard panel metadata and bounding boxes
- app/dashboard summaries
- annotation list
- annotation pixel geometry
- target-surface association
- same-origin iframe text excerpt where allowed
- inline dashboard HTML excerpt where available
- permission-state flags

Implementation:

- `app.js` — `currentVisualContext()`
- `gateway.js` — `sendGatewayChat(content, visualContext)`

### 2. Allow agent inspect

Agent-pull mode. When enabled, the gateway-connected agent may request current workspace context through:

```text
gui_get_visual_context
```

The request is forwarded by the gateway as a websocket `gui_command`, then executed locally in `gateway.js` by calling `window.wolfGuiCurrentVisualContext()`.

If disabled, the GUI rejects the command and returns a failed `gui_command_result`.

### 3. Allow agent capture

Screenshot mode. When enabled, the agent may request:

```text
gui_capture_url
gui_capture_workspace
```

If disabled, the GUI rejects capture commands and returns a failed `gui_command_result`.

---

## Permissioned screenshot capture

Screenshot capture is default-off and requires user consent.

### Workspace capture flow

```text
agent action: gui_capture_workspace
  -> GatewayActionWorkflow returns deferred GUI command
  -> gateway emits websocket gui_command
  -> gateway.js checks Allow agent capture
  -> for rendered GUI scopes, asks user for live browser-surface capture
  -> browser getDisplayMedia picker opens
  -> user selects Wolf GUI tab/window
  -> gateway.js captures PNG from the live surface
  -> gateway.js uploads to gateway /api/gui/capture/live
  -> gateway stores image under wf_workspace/captures/...
  -> gateway.js returns gui_command_result over websocket
  -> gateway attaches result to workflow/orchestration task
  -> agent continues with image reference/context
```

Rendered GUI capture scopes prefer live client capture:

- `full_gui`
- `workspace`
- `active_dashboard`
- `annotation_regions`

Panel/URL capture scopes can use backend replay capture:

- `url_list`
- `active_dashboard_panels`
- `selected_panels`

Backend replay uses the gateway capture service and may not perfectly match the user's visible browser tab because it renders in a separate browser context. Live client capture is preferred when exact visual agreement matters.

---

## Gateway connection flow

The Gateway modal is a state machine:

```text
1. Authenticate
2. Fetch sessions
3. Select existing session or create new session
4. Connect selected session over websocket
5. Fetch/edit/commit Agent params
6. Fetch/edit/commit Policy params
7. Chat / approve / inspect / capture / orchestrate
```

Important distinction:

- Gateway auth token: returned by `/auth/login`; used for gateway REST and websocket auth.
- Provider/LLM API key: belongs in Agent parameters, not in gateway-auth fields.

Gateway routes used by the GUI:

```text
POST /auth/login
GET /accounts/{account_id}/sessions
GET /agent-config-presets
GET /sessions/{session_id}/params
PATCH /sessions/{session_id}/params
GET /sessions/{session_id}/policy
POST /sessions/{session_id}/reset
GET /sessions/{session_id}/orchestration/snapshot
GET /sessions/{session_id}/orchestration/tasks/{task_id}
WS  /ws/{account_id}/{session_id}
```

The websocket URL includes:

```text
token=<gateway-auth-token>
participant_id=gui
participant_role=owner
client_type=gui
```

On connect, the GUI sends `gui_client_hello` so the gateway can resolve GUI routing mode.

---

## Deferred GUI command bridge

GUI actions requested by agents can be executed in two ways:

1. **Direct route** — gateway calls a reachable local GUI HTTP endpoint.
2. **Client-event route** — gateway forwards a websocket `gui_command` to the connected browser client.

For a remote/pre-deployed gateway, `client_event` is usually the correct route because the browser owns the real visual surface and user permissions.

Bridge flow:

```text
GatewayActionWorkflow emits deferred result
  result.deferred_to_gui_client = true
  result.gui_command = { action, payload }

WolfGateway._gui_command_from_workflow_event(...)
  -> creates websocket event type=gui_command
  -> stores runtime.pending_gui_commands[command_id]
  -> includes orchestration metadata when present

framework/gui/static/gateway.js executeGatewayGuiCommand(...)
  -> executes local GUI command
  -> sends type=gui_command_result back over websocket

WolfGateway websocket handler
  -> receives gui_command_result
  -> finds pending command or uses task/source metadata fallback
  -> appends result to workflow or orchestration worker history
  -> emits gui_command_result and/or orchestration attachment events
```

Important frontend functions:

- `executeGatewayGuiCommand(event)`
- `sendGuiCommandResult(event, commandId, action, fields)`
- `handleGatewayEvent(event)`

Important backend functions:

- `WolfGateway._gui_command_from_workflow_event(event)`
- `WolfGateway._gui_command_continuation_prompt(...)`
- websocket `gui_command_result` branch in `framework/pack/gateway.py`
- `GatewayOrchestrationSession.handle_gui_command_result(...)`

---

## Orchestration and GUI bridge

When gateway orchestration is enabled, chat messages become orchestration root tasks or task messages rather than a single synchronous workflow turn. This is the VUI-facing part of WOLF **PACK**: **Parallel and Asynchronous Composable Kernels**, where tasks act like composable kernels that can run concurrently, keep subtask context local, and send summaries/artifacts back to parent or sibling tasks.

Orchestrated visual-task flow:

```text
VUI sends websocket chat
  -> gateway submits orchestration task
  -> GatewayOrchestrationSession leases worker
  -> worker GatewayActionWorkflow runs
  -> worker emits gui_get_visual_context or gui_capture_workspace
  -> deferred GUI command is sent to browser
  -> worker stops with wait_reason=waiting_for_gui_command_result
  -> VUI returns gui_command_result
  -> gateway attaches result to task-local worker history
  -> orchestration marks task READY
  -> worker continues using the actual visual result
```

Deferred GUI commands must not be treated as completed synchronous tool calls. They require the wait/attach/wake bridge so the agent does not continue before the browser returns the screenshot/context result.

Relevant events:

- `workflow_action`
- `workflow_result`
- `gui_command`
- `gui_command_result`
- `workflow_status` with `wait_reason=waiting_for_gui_command_result`
- `orchestration_event` with `task_ready`
- `gui_command_result_attached`
- `orchestration_attach`

---

## Approvals and permissions in the VUI

The GUI displays two kinds of approval requests:

1. Local GUI approval queue from `/api/gui/approvals`.
2. Gateway websocket permission requests from `permission_request` events.

Gateway permission requests are not owned by the local GUI HTTP queue. The frontend keeps them in the same approval panel for UX, but decisions are sent back over websocket as:

```text
permission_decision
```

Decision options:

- approve once
- approve for session
- deny

Important frontend functions:

- `app.js` — `refreshApprovals()`, `renderApprovals()`, `decideApproval(...)`
- `gateway.js` — `handlePermissionRequest(event)`, `sendPermissionDecision(decision)`

Important backend path:

- `WolfGateway._make_gateway_permission_provider(...)`
- `BaseInfrastructure.request_permission(...)`
- `PermissionManager.request_permission(...)`

---

## Policy tab and syscalls

The Policy tab controls what the gateway exposes and how permissioned actions are handled.

Current syscall policy model:

- `enable_syscall` exposes `run_syscall` to the model.
- `syscall_allowed_commands` lists commands that can auto-run when used as simple non-shell commands.
- `syscall_deny_patterns` lists commands/patterns that are always blocked.
- Commands neither explicitly allowed nor denied are routed to the VUI approval panel.
- Shell/script forms are elevated and ask for approval unless explicitly denied; they do not auto-run merely because the base command is allowlisted.

Examples:

```text
Allowed syscalls / commands:
pwd, ls, cat, head, tail, grep, find, wc, echo, git, python, python3, du

Denied syscalls / commands:
rm, sudo, su, chmod, chown, mkfs, dd, shutdown, reboot, kill, pkill, curl, wget, ssh, scp, nc, pip, uv
```

This behavior is implemented across:

- `index.html` — policy fields.
- `gateway.js` — `policyFormToParams()` and `applyPolicyToForm()`.
- `gateway.py` — `_resolve_execution_policy(...)`.
- `gateway_action_workflow.py` — `_guard_action_execution(...)`.
- `system_actions.py` — `SysCallAction.execute(...)` approval/bypass behavior.

---

## Security notes

### Local GUI control token

Set a control token to protect mutation/control routes:

```bash
export WOLF_GUI_CONTROL_TOKEN="your-secret-token"
```

Mutation requests must include:

```text
X-Wolf-Gui-Token: your-secret-token
```

The CLI can write/generate this token through:

```bash
./wolf gui --gui-token <token>
./wolf gui --generate-gui-token
```

### Browser security

- Cross-origin iframe DOM is not readable by normal browser JavaScript.
- Pixel capture of the user's visible GUI requires explicit browser Screen Capture API consent.
- Backend replay capture renders in a separate browser and may not match the visible tab exactly.

### Gateway permissions

- Gateway auth is separate from provider/LLM API keys.
- Gateway permission requests route to connected participants that can approve permissions.
- Approve-for-session is handled by `PermissionManager` and currently applies at permission-kind level.

---

## Developer onboarding map

Start here:

1. `framework/gui/static/app.js` — local workspace, visual context, approvals.
2. `framework/gui/static/gateway.js` — gateway modal, websocket, GUI command execution.
3. `framework/gui/runtime.py` — GUI state and server-side events.
4. `framework/gui/server.py` — local GUI HTTP routes.
5. `framework/pack/gateway.py` — websocket bridge and permission routing.
6. `framework/pack/orchestration_session.py` — orchestration wait/attach/wake flow.
7. `framework/workflows/custom_workflows/gateway_action_workflow.py` — deferred GUI action and syscall guard behavior.
