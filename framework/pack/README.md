# WOLF Pack Gateway (`framework/pack`)

`framework/pack/gateway.py` is the FastAPI REST/websocket gateway for WOLF workflow, VUI, TUI, collaboration, orchestration, action execution, permission routing, and visual-command bridging.

The gateway is intentionally a **transport/session/runtime layer**. It owns authentication, account/session ownership, websocket connections, event fanout, participant tracking, runtime bundle creation, action policy resolution, permission routing, GUI command routing, and capture artifact storage. The workflow/action loop itself is handled by `GatewayActionWorkflow`, and multi-task execution is handled by `GatewayOrchestrationSession`.

---

## What PACK means

**PACK** means **Parallel and Asynchronous Composable Kernels**. It is the orchestration model behind the WOLF PACK.

WOLF orchestration is task-based. A root task can be decomposed into sibling tasks or child tasks when the work exposes useful asynchronous or parallel structure. The goal is to hide latency and increase throughput by letting independent subtasks run concurrently while preserving enough structure for results to be recomposed.

The task metaphor is intentionally close to computational kernels:

- a task has a bounded objective, local state, and a lifecycle;
- child/sibling tasks can run independently when dependencies allow;
- task-local context can be generated, pruned, and summarized locally;
- compact summaries, artifacts, and status updates travel back to parent or sibling tasks instead of flooding every participant with full subtask context;
- orchestration can exploit async execution, parallelism, and vectorization of subtasks;
- tasks remain composable so the gateway can schedule, pause, wake, and join them.

This is why the gateway/orchestration package is the WOLF **PACK**: it provides the Parallel and Asynchronous Composable Kernels layer that lets agents decompose work, coordinate workers, bridge external GUI results, and rejoin summarized outcomes into the parent workflow.

---

## Quick start

```bash
./wolf gateway
```

Common flags:

```bash
./wolf gateway --gateway-host 127.0.0.1 --gateway-port 8000
./wolf gateway --model <model> --host-address <provider-url> --api-key-var LOCAL_API_KEY
./wolf gateway --action-policy dev --enable-syscall
./wolf gateway --orchestration-enabled
./wolf gateway --gui-action-route client_event
```

Launch path:

```text
framework/cli/wolf_app.py      -> parses `wolf gateway` flags
framework/cli/launchers.py     -> launch_gateway(...)
framework/pack/gateway.py      -> WolfGateway(...).run()
```

---

## Important files

```text
framework/pack/gateway.py
  FastAPI app, REST endpoints, websocket endpoint, runtime manager, action policies, GUI bridge, capture routes, permission provider.

framework/pack/orchestration_session.py
  Gateway orchestration runtime: task graph, workers, leases, wait/attach/wake logic, GUI-result attachment.

framework/workflows/custom_workflows/gateway_action_workflow.py
  Async gateway workflow that asks the model for structured WOLF actions, validates actions, guards permissioned actions, defers GUI commands, and emits workflow events.

framework/workflows/agent_actions/gui_actions.py
  GUI action schemas and helpers: gui_get_visual_context, gui_capture_workspace, dashboards, apps, notify, etc.

framework/workflows/agent_actions/system_actions.py
  run_syscall implementation and user-approval request construction.

framework/infrastructure/base_infrastructure.py
  Permission-provider plumbing through BaseInfrastructure.request_permission(...).

framework/permissions/
  Transport-neutral PermissionRequest, PermissionDecision, and PermissionManager.

framework/gui/static/gateway.js
  Browser-side VUI client for gateway auth/session/config/websocket/gui-command/permission/capture handling.

framework/ui/tui_client.py
  Terminal client for gateway sessions.

scripts/gateway_smoke.py
  REST/websocket smoke tester.
```

---

## Runtime model

Each gateway session has a runtime bundle stored by `ConnectionManager`:

```python
{
    "agent": main_agent,
    "wf": GatewayActionWorkflow(...),
    "infra": BaseInfrastructure(...),
    "managers": {
        "chat": chat_manager,
        "memory": memory_manager,
        "context": context_manager,
    },
    "config": agent_config,
    "session_dir": "wf_workspace/gateway/<account>/session_<id>",
    "db_client": db_client,
    "lock": asyncio.Lock(),
    "run_control": {...},
    "pending_gui_commands": {},
    "pending_permission_requests": {},
    "gui_route": {...},
    "orchestration": GatewayOrchestrationSession(...) | None,
}
```

Runtime creation reuses normal WOLF session construction:

```python
setup_cli_session(..., workflow_cls=GatewayActionWorkflow)
```

The per-session lock is mandatory for non-orchestrated turns because workflow history, managers, snapshots, and agent context are mutable. Orchestration uses task-local worker state plus coordination primitives in `GatewayOrchestrationSession`.

---

## Main classes and responsibilities

### `AgentConfig`

`AgentConfig` is the gateway's session/runtime configuration model. Important fields include:

- provider/model fields: `model`, `host_address`, `host_port`, `api_key`, `api_key_var`, `api_version`
- workflow behavior: `mode`, `max_steps`, `ctx_window_length`, `sys_prompt`, `capabilities`
- orchestration: `orchestration_enabled`, worker counts, max active/total tasks
- action policy: `action_policy`, `action_names`, `enable_write`, `enable_syscall`, `enable_gui_capture`
- syscall policy: `syscall_allowed_commands`, `syscall_deny_patterns`, `syscall_max_timeout`, `syscall_allow_shell`
- GUI routing: `gui_url`, `gui_action_route`, `gui_command_timeout_seconds`

### `ConnectionManager`

Tracks:

- accounts and sessions
- runtime bundles
- websocket connections
- participant metadata and permissions
- collaboration invites/join requests
- active session ownership
- message fanout

### `WolfGateway`

Defines the FastAPI app, REST routes, websocket handler, runtime creation, policy resolution, GUI command bridge, permission bridge, capture endpoints, and orchestration integration.

Important methods:

- `_resolve_action_names(config)`
- `_resolve_execution_policy(config)`
- `_get_or_create_runtime(...)`
- `_get_orchestration_session(session_id)`
- `_gui_command_from_workflow_event(event)`
- `_gui_command_continuation_prompt(...)`
- `install_gateway_permission_provider(...)`
- `_make_gateway_permission_provider(...)`
- websocket branch for `gui_command_result`
- websocket branch for `permission_decision`

---

## REST endpoints used by VUI/TUI

### Auth and sessions

```text
POST /auth/login
GET  /accounts/{account_id}/sessions?token=...
```

### Agent/config/policy

```text
GET   /agent-config-presets
GET   /sessions/{session_id}/params?token=...
PATCH /sessions/{session_id}/params?token=...
GET   /sessions/{session_id}/policy?token=...
POST  /sessions/{session_id}/reset?token=...
```

### Participants/collaboration

```text
GET    /sessions/{session_id}/participants?token=...
POST   /sessions/{session_id}/invites?token=...
GET    /sessions/{session_id}/invites?token=...
DELETE /sessions/{session_id}/invites/{invite_id}?token=...
GET    /sessions/{session_id}/join-requests?token=...
POST   /sessions/{session_id}/join-requests/{request_id}/approve?token=...
```

### Orchestration

```text
GET  /sessions/{session_id}/orchestration/snapshot?token=...
GET  /sessions/{session_id}/orchestration/tasks/{task_id}?token=...
POST /sessions/{session_id}/orchestration/tasks/{task_id}/control?token=...
```

### GUI/capture

```text
POST /api/gui/capture/live
POST /api/gui/capture/url
POST /api/gui/capture/workspace
```

Exact capture route availability depends on the current `gateway.py` implementation and action policy. GUI capture must be enabled by policy and by the user's VUI toggle before an agent can obtain screenshots.

---

## Websocket endpoint

```text
WS /ws/{account_id}/{session_id}?token=...&participant_id=...&participant_role=...&client_type=...
```

For the VUI, typical query fields are:

```text
participant_id=gui
participant_role=owner
client_type=gui
```

The websocket handler authenticates the account/session/token, registers participant metadata, broadcasts presence, sends an initial `system` event, then receives client messages in a loop.

---

## Websocket message types

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

---

## Basic non-orchestrated chat flow

```text
websocket chat
  -> gateway extracts visual_context from message/metadata
  -> gateway resolves action policy
  -> gateway emits user_echo and policy_resolved
  -> gateway acquires session runtime lock
  -> GatewayActionWorkflow.process_user_message(...)
      -> appends user input to workflow history
      -> builds prompt with compact context and effective action schema
      -> model emits structured action JSON
      -> validate_action_response(...)
      -> _guard_action_execution(...) for permissioned actions
      -> execute action or defer GUI action
      -> update history and snapshot
      -> return workflow events
  -> gateway fans out workflow events
  -> gateway releases runtime lock
```

If a GUI action is deferred, the normal flow pauses and the GUI command bridge takes over.

---

## GatewayActionWorkflow

`GatewayActionWorkflow` is the gateway-facing workflow implementation. It is responsible for:

- presenting the effective action schema to the model
- parsing structured model output
- validating action payloads
- guarding permissioned actions
- deferring GUI actions when they must run in the connected VUI client
- appending workflow history
- saving session snapshots
- emitting transport-friendly workflow events

Important behavior:

- `send_message` produces user-visible assistant chat output.
- `read_file`, `write_file`, `run_syscall`, GUI actions, memory/context actions, etc. are normal WOLF actions gated by policy.
- GUI actions may be direct or deferred depending on GUI route resolution.
- Deferred GUI results are compact in initial `workflow_result` events and must be completed by later `gui_command_result` bridge messages.

---

## Action policy resolution

Gateway action exposure is policy-driven.

Common policy modes:

| Policy | General meaning |
| --- | --- |
| `safe` | Chat/read/context-safe operations only. |
| `limited` | Default limited action set plus selected safe GUI/context actions. |
| `write` | Adds write-capable actions where enabled. |
| `dev` | Enables developer actions such as guarded `run_syscall`. |
| `advanced` | Adds GUI capture and advanced workspace actions where enabled. |
| `master` | Broadest built-in policy; still subject to guardrails. |
| `custom` | Uses explicit `action_names`. |

Important functions:

```text
WolfGateway._resolve_action_names(config)
WolfGateway._resolve_execution_policy(config)
GatewayActionWorkflow._guard_action_execution(...)
```

The gateway emits `policy_resolved` so clients can inspect the effective action names and execution guardrails.

---

## Current syscall policy: allow / deny / ask

`run_syscall` is guarded even when exposed in the model schema.

Policy fields:

```text
enable_syscall
syscall_allowed_commands
syscall_deny_patterns
syscall_max_timeout
syscall_allow_shell
syscall_request_approval_for_shell
syscall_credential_exfiltration_patterns
```

Current behavior:

1. `enable_syscall=false`
   - `run_syscall` is not exposed or is blocked by policy.

2. Command base or token matches `syscall_deny_patterns`
   - hard block immediately.

3. Command base is in `syscall_allowed_commands` and the call is a simple non-shell command
   - auto-run without asking the user.

4. Command is neither explicitly allowed nor denied
   - route to the permission bridge and ask the user in VUI/TUI/terminal if a provider is available.

5. Shell/script forms
   - treated as elevated.
   - ask for user approval unless explicitly denied.
   - do not auto-run merely because the base command is allowlisted.

6. argv-form `shell=false` arguments
   - semicolons/newlines inside ordinary argv arguments are treated as data for the executable, not shell composition.
   - this supports safe cases such as `python3 -c "...\n...; ..."` when `python3` is explicitly allowed.

Suggested VUI policy values:

```text
Allowed syscalls / commands:
pwd, ls, cat, head, tail, grep, find, wc, echo, git, python, python3, du

Denied syscalls / commands:
rm, sudo, su, chmod, chown, mkfs, dd, shutdown, reboot, kill, pkill, curl, wget, ssh, scp, nc, pip, uv
```

Implementation path:

```text
VUI Policy tab
  -> gateway.js policyFormToParams()
  -> PATCH /sessions/{session_id}/params
  -> gateway.py _resolve_execution_policy(...)
  -> GatewayActionWorkflow._guard_action_execution(...)
  -> SysCallAction.execute(...)
  -> BaseInfrastructure.request_syscall_approval(...) if approval is required
  -> PermissionManager + gateway websocket permission provider
```

---

## Permission bridge

The gateway installs a transport-specific permission provider on the workflow infrastructure:

```text
WolfGateway.install_gateway_permission_provider(...)
  -> BaseInfrastructure.set_permission_providers([...])
  -> BaseInfrastructure.request_permission(...)
  -> PermissionManager.request_permission(...)
  -> WolfGateway._make_gateway_permission_provider(...)
```

For a permission request:

```text
permissioned action requests permission
  -> PermissionRequest created
  -> gateway stores runtime.pending_permission_requests[request_id]
  -> gateway broadcasts websocket permission_request
  -> VUI displays approval card
  -> user chooses approve once / approve session / deny
  -> VUI sends websocket permission_decision
  -> gateway resolves stored Future
  -> PermissionManager records PermissionDecision
  -> action continues or returns denied result
```

Notes:

- Gateway websocket permission requests are not owned by the local GUI HTTP approval queue; the VUI displays them in the same approval panel but sends decisions back over websocket.
- `approve_for_session` is currently tracked by `PermissionManager` at permission-kind level.
- Routing modes such as `session_owner`, `self_approved`, and placeholders for delegation are normalized by `_normalize_permission_routing_mode(...)`.

---

## GUI/VUI routing

The gateway supports GUI actions through route resolution.

Routes:

| Route | Meaning |
| --- | --- |
| `direct` | Gateway calls a reachable GUI HTTP API directly. Useful when gateway and GUI are local and mutually reachable. |
| `client_event` | Gateway sends a websocket `gui_command` to the connected browser client. Required/preferred for remote gateways and live browser-surface capture. |
| `auto` | Gateway probes direct route and falls back/resolves according to availability. |

The VUI sends `gui_client_hello` after websocket connect. The gateway uses it to resolve GUI route and emits `gui_route_resolved`.

---

## Deferred GUI command bridge

Many GUI actions must be executed in the browser because only the browser has access to:

- the live visible GUI tab/window
- user toggles for inspect/capture
- browser Screen Capture API
- same-origin iframe DOM where available
- local dashboard layout and annotation geometry

Bridge flow:

```text
agent emits gui_get_visual_context / gui_capture_workspace / dashboard action
  -> GatewayActionWorkflow decides action should defer to GUI client
  -> workflow_result contains:
       result.deferred_to_gui_client = true
       result.route = client_event
       result.gui_command = { action, payload }
  -> WolfGateway._gui_command_from_workflow_event(...) creates websocket gui_command
  -> gateway stores runtime.pending_gui_commands[command_id]
  -> VUI gateway.js executeGatewayGuiCommand(...) executes locally
  -> VUI sends websocket gui_command_result
  -> gateway attaches result to workflow or orchestration task
  -> agent continuation is scheduled when appropriate
```

Important backend pieces:

```text
WolfGateway._gui_command_from_workflow_event(event)
WolfGateway._gui_command_continuation_prompt(...)
runtime.pending_gui_commands
websocket branch: msg_type == "gui_command_result"
```

Important frontend pieces:

```text
framework/gui/static/gateway.js
  executeGatewayGuiCommand(event)
  sendGuiCommandResult(...)
  capture workspace/live/upload helpers
```

---

## Visual context and screenshot capture

GUI actions relevant to visual context:

```text
gui_get_visual_context
gui_capture_url
gui_capture_workspace
```

### `gui_get_visual_context`

Usually deferred to the VUI client. The browser returns structured metadata from `window.wolfGuiCurrentVisualContext()`:

- viewport size
- workspace mode/URL
- active dashboard
- panels and bounding boxes
- annotations and annotation target mapping
- app/dashboard summaries
- same-origin iframe excerpts where possible
- capture permission flags

### `gui_capture_workspace`

Can capture rendered GUI scopes or dashboard/panel URL scopes.

Live client capture flow:

```text
agent requests gui_capture_workspace
  -> deferred websocket gui_command
  -> VUI checks Allow agent capture
  -> user clicks capture prompt
  -> browser getDisplayMedia picker opens
  -> user selects Wolf GUI tab/window
  -> VUI captures PNG from live browser surface
  -> VUI uploads to /api/gui/capture/live
  -> gateway stores artifact under wf_workspace/captures/...
  -> VUI returns gui_command_result with image reference
  -> gateway attaches image reference to workflow/orchestration context
```

Backend replay capture remains available for URL/panel captures but may differ from the actual visible GUI tab.

---

## Orchestration layer

When `orchestration_enabled=true`, gateway chat is routed into `GatewayOrchestrationSession` rather than a single synchronous `GatewayActionWorkflow` loop.

PACK orchestration responsibilities:

- create root tasks from user messages
- decompose root tasks into child tasks or sibling tasks when independent async work is available
- lease worker agents
- maintain task graph, dependency, parent, and sibling relationships
- run worker-local `GatewayActionWorkflow` turns
- keep subtask context local to the worker/task where possible
- summarize subtask outcomes before sending them back to parent or sibling tasks
- stream `orchestration_event` and snapshots
- pause tasks waiting for external GUI results
- attach GUI results into task-local worker history
- wake tasks after dependencies or external results arrive
- recombine summaries/artifacts into the parent workflow so the root task can continue

Typical orchestrated GUI-capture flow:

```text
VUI websocket chat
  -> gateway _handle_orchestration_chat_message(...)
  -> GatewayOrchestrationSession creates/updates task
  -> worker runs GatewayActionWorkflow
  -> worker emits deferred gui_capture_workspace
  -> task state becomes waiting_for_gui_command_result
  -> gateway sends browser gui_command
  -> VUI performs capture and returns gui_command_result
  -> gateway calls GatewayOrchestrationSession.handle_gui_command_result(...)
  -> result is attached to task-local worker history
  -> task is marked ready/woken
  -> worker continues with actual visual result
```

Important orchestration files/functions:

```text
framework/pack/orchestration_session.py
  GatewayOrchestrationSession
  handle_gui_command_result(...)
  pending_gui_commands
  waiting_for_gui_command_result
  snapshot()
```

Important events:

```text
orchestration_event
orchestration_snapshot
workflow_action
workflow_result
gui_command
gui_command_result
gui_command_result_attached
workflow_status
```

The VUI Kanban tab consumes orchestration snapshots/events to show task lifecycle state.

---

## Gateway event UX in VUI

The GUI renders gateway events in three broad ways:

1. User/assistant chat bubbles
   - `user_echo`, `agent_response`, `send_message` workflow results.

2. Compact system notices
   - policy, workflow status, route resolution, errors, permission status.

3. Inspectable metadata
   - events with structured payloads get an info/expand affordance in the message panel.

This keeps the chat readable while preserving low-level debug details for inspection.

---

## Auth, stale tokens, and websocket 403

Gateway auth tokens come from:

```text
POST /auth/login
```

They are not provider/LLM API keys.

Common websocket failure:

```text
WebSocket ... 403
```

Likely causes:

- stale gateway token in browser localStorage
- session belongs to another account
- gateway restarted and auth/session state changed
- selected session ID is stale
- wrong gateway URL
- websocket participant lacks required permissions

Recommended recovery:

1. Re-authenticate in the VUI Gateway modal.
2. Fetch sessions again.
3. Select/create a session.
4. Connect selected session.
5. Commit params/policy only after websocket connection is healthy.
6. If needed, clear browser localStorage key `wolfGatewayStateV3`.

---

## Secret redaction

Gateway redacts sensitive values in config and params responses. Redacted key patterns include:

- `api_key`
- `token`
- `password`
- `secret`
- `authorization`

The VUI also avoids re-sending redacted secrets from forms.

---

## Smoke testing

Example smoke test:

```bash
python scripts/gateway_smoke.py \
  --username max \
  --password '' \
  --policy dev \
  --host-address https://example-llm-host \
  --api-key "$LOCAL_API_KEY" \
  --model gpt-5.4-nano \
  --api-version v1 \
  --message "What is the current working directory? Use run_syscall with command pwd, shell false, timeout 5."
```

The smoke script logs in, configures a session, checks `/policy`, opens a websocket, sends a message, and asserts expected gateway workflow events.

---

## Operational notes

- Restart gateway after backend changes.
- Refresh/reopen VUI after frontend changes.
- Reconnect selected session after runtime recreation or policy/agent config changes.
- If an action appears unavailable, inspect `policy_resolved`.
- If a GUI command appears to complete without useful content, inspect `gui_command`, `gui_command_result`, and orchestration attachment events.
- If an orchestrated visual task loops saying the GUI result was deferred but not returned, check the VUI websocket connection and `pending_gui_commands`/`handle_gui_command_result` bridge.
- Do not expose unrestricted `run_syscall` by default.
- Prefer `client_event` GUI route for remote/pre-deployed gateways connected to a browser VUI.
