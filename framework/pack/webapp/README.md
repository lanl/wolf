# WOLF Gateway Web UI (`framework/pack/webapp`)

This directory contains the static web UI assets served by the WOLF Gateway root/static routes when running:

```bash
./wolf gateway
```

The gateway web UI is intentionally separate from the local Wolf GUI/VUI frontend under `framework/gui/static/`.

- `framework/gui/static/` is the full local VUI workspace: visual workspace, floating agent panel, gateway modal, visual-context/capture bridge, approvals, dashboards, and Kanban.
- `framework/pack/webapp/` is gateway-served static UI space. It is useful for lightweight gateway-root pages or future gateway-native views, but the current primary browser client for VUI/gateway collaboration is `framework/gui/static/gateway.js` loaded by `./wolf gui`.

---

## Relationship to the gateway

`framework.pack.gateway.WolfGateway` can serve static files from a configured directory:

```bash
./wolf gateway --static-dir ./framework/pack/webapp
```

The CLI default is currently:

```text
./framework/pack/webapp
```

Implementation path:

```text
framework/cli/wolf_app.py      -> gateway subcommand parses --static-dir
framework/cli/launchers.py     -> launch_gateway(..., static_dir=...)
framework/pack/gateway.py      -> WolfGateway(host, port, static_dir, ...)
```

The gateway also exposes REST and websocket APIs consumed by richer clients such as the VUI and TUI.

PACK means **Parallel and Asynchronous Composable Kernels**: WOLF's task-based orchestration layer where a root task can decompose into child/sibling tasks, run independent subtasks concurrently, keep subtask context local, and pass compact summaries/artifacts back through the task graph.

---

## Primary browser client today

The current full-featured browser client is the Wolf GUI/VUI:

```text
framework/gui/static/index.html
framework/gui/static/app.js
framework/gui/static/gateway.js
framework/gui/static/styles.css
```

That VUI handles:

- gateway login through `/auth/login`
- account/session listing
- websocket connection to `/ws/{account_id}/{session_id}`
- agent parameter editing
- policy parameter editing
- permission cards
- visual context collection
- live browser-surface screenshot capture
- deferred GUI command execution
- orchestration Kanban state
- workflow/chat event rendering

If you are looking for the implementation of the gateway connection modal shown in `./wolf gui`, start with:

```text
framework/gui/static/gateway.js
```

---

## Gateway API surface relevant to web clients

A gateway-served web UI or external browser client generally talks to:

### Auth/session

```text
POST /auth/login
GET  /accounts/{account_id}/sessions?token=...
```

### Runtime params and policy

```text
GET   /agent-config-presets
GET   /sessions/{session_id}/params?token=...
PATCH /sessions/{session_id}/params?token=...
GET   /sessions/{session_id}/policy?token=...
POST  /sessions/{session_id}/reset?token=...
```

### Websocket

```text
WS /ws/{account_id}/{session_id}?token=...&participant_id=...&participant_role=...&client_type=...
```

Common browser/VUI participant metadata:

```text
participant_id=gui
participant_role=owner
client_type=gui
```

### Orchestration

```text
GET /sessions/{session_id}/orchestration/snapshot?token=...
GET /sessions/{session_id}/orchestration/tasks/{task_id}?token=...
```

### GUI capture/artifacts

```text
POST /api/gui/capture/live
POST /api/gui/capture/url
POST /api/gui/capture/workspace
```

Capture endpoints are used by the VUI after user permission. The gateway stores capture artifacts under `wf_workspace/captures/...` and returns compact image references rather than embedding large blobs in chat history.

---

## Websocket messages expected by gateway web clients

### Client -> gateway

```text
chat
agent_control
gui_client_hello
gui_command_result
permission_decision
participant_message
orchestration_snapshot_request
ping
pong
```

### Gateway -> client

```text
system
error
presence
user_echo
policy_resolved
workflow_status
workflow_action
workflow_result
workflow_error
run_control_state
workflow_control
permission_request
permission_decision_ack
permission_request_timeout
gui_route_resolved
gui_command
gui_command_result
agent_response
participant_message
orchestration_snapshot
orchestration_event
```

A browser client that wants to support visual tasks must implement at least:

- `gui_client_hello` on connect
- handling of `gui_command`
- sending `gui_command_result`
- handling of `permission_request`
- sending `permission_decision`
- optionally handling `orchestration_event` / `orchestration_snapshot`

---

## Deferred GUI command contract

Gateway workflows can defer GUI actions to a browser client. This is essential for remote gateways because the gateway backend cannot see the user's actual browser tab/window.

Flow:

```text
GatewayActionWorkflow emits workflow_result
  result.deferred_to_gui_client = true
  result.gui_command = { action, payload }

WolfGateway converts that result into websocket gui_command
  -> browser client executes locally
  -> browser client sends gui_command_result

WolfGateway receives gui_command_result
  -> attaches result to session workflow history or orchestration task history
  -> schedules continuation/wake when appropriate
```

Browser-side implementations should treat these commands as asynchronous tool calls and return enough structured data for the agent to continue.

Important commands:

```text
gui_get_visual_context
gui_capture_workspace
gui_capture_url
gui_notify
gui_create_dashboard
gui_add_dashboard_panel
gui_update_dashboard_panel
gui_open_dashboard
gui_publish_dashboard
gui_register_app
gui_open_app
```

---

## Permission contract

Permissioned actions may trigger gateway websocket permission requests.

Flow:

```text
action requests permission
  -> gateway emits permission_request
  -> browser displays approval UI
  -> user approves once / approves for session / denies
  -> browser sends permission_decision
  -> gateway unblocks the pending action Future
```

A browser client should preserve the request ID and send decisions as websocket messages rather than posting them to the local VUI HTTP approval queue.

Relevant permissioned operations include:

- `run_syscall` for unknown/elevated commands
- write-capable actions
- GUI capture actions
- future universe/tool/destructive KB actions depending on policy

---

## Syscall policy displayed by clients

Current gateway syscall behavior is allow / deny / ask:

- commands in `syscall_allowed_commands` auto-run only when they are simple non-shell invocations
- commands matching `syscall_deny_patterns` are blocked immediately
- commands neither allowed nor denied ask the user through the permission bridge
- shell/script forms are elevated and ask for approval unless explicitly denied

Browser policy UIs should expose both:

```text
syscall_allowed_commands
syscall_deny_patterns
```

---

## Development notes

- Keep this directory lightweight unless building a gateway-native web UI.
- For VUI behavior changes, edit `framework/gui/static/` instead.
- For gateway REST/websocket behavior, edit `framework/pack/gateway.py`.
- For orchestration behavior, edit `framework/pack/orchestration_session.py`.
- For deferred GUI actions and syscall guard behavior, edit `framework/workflows/custom_workflows/gateway_action_workflow.py`.
- For permission models, edit `framework/permissions/`.
- For syscall approval request details, edit `framework/workflows/agent_actions/system_actions.py`.
