# WOLF Pack Gateway

`framework/pack/gateway.py` is the FastAPI websocket gateway for WOLF workflow/action sessions.

The gateway is intentionally a **transport/session layer**. It owns authentication, account/session ownership, websocket connections, event fanout, runtime-bundle registry, participant tracking, and per-session locks. It does **not** duplicate WOLF action orchestration; that lives in `GatewayActionWorkflow`.

## Main files

```text
framework/pack/gateway.py                                  # FastAPI gateway
framework/workflows/custom_workflows/gateway_action_workflow.py  # Async gateway action workflow
framework/ui/tui_client.py                                 # Gateway TUI client
scripts/gateway_smoke.py                                   # REST/websocket smoke tester
```

## Runtime model

Each gateway session has a runtime bundle:

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
}
```

Runtime creation reuses the normal session construction path:

```python
setup_cli_session(..., workflow_cls=GatewayActionWorkflow)
```

The per-session lock is mandatory because the workflow, infrastructure, managers, snapshots, and agent context are mutable.

## Websocket flow

For a TUI `chat` message:

```text
websocket chat
  -> gateway emits user_echo
  -> gateway resolves action policy and emits policy_resolved
  -> gateway acquires runtime lock
  -> GatewayActionWorkflow.process_user_message(...)
      -> append user input to workflow history
      -> build prompt with compact context + effective action schema
      -> get structured action or fallback JSON action
      -> validate Pydantic action
      -> guard risky execution if needed
      -> execute action through infrastructure
      -> update history/snapshot
      -> return workflow events
  -> gateway fans out workflow events
  -> gateway releases runtime lock
```

## Event types

Legacy/basic events:

- `system`
- `user_echo`
- `error`
- `ping`
- `pong`

Workflow events:

- `policy_resolved` — effective policy/action allowlist/guardrails.
- `workflow_status` — lifecycle state such as `received`, `thinking`, and `done`.
- `workflow_action` — normalized action payload selected by the model.
- `workflow_result` — execution result summary and history delta.
- `workflow_error` — validation/execution/guardrail failure.

Participant events:

- `presence` — participant joined/left-style notification.
- `participant_message` — message broadcast from an attached participant.

## Action policies

Gateway action exposure is policy-driven.

| Policy | Actions |
| --- | --- |
| `safe` | `send_message`, `read_file`, `check_context_utilization`, `list_memory_categories` |
| `write` | safe actions plus `write_file` |
| `dev` | write policy plus guarded `run_syscall` |

TUI examples:

```text
/config agent params action_policy='safe'
/config agent params action_policy='write'
/config agent params action_policy='dev'
/config agent params enable_syscall=true syscall_max_timeout=5
```

## `run_syscall` guardrails

`run_syscall` is guarded even when exposed in the model schema.

Current guardrails:

- disabled unless policy enables it;
- `shell=True` blocked by default;
- shell composition/metacharacters blocked, including `;`, `&&`, `||`, `|`, backticks, `$`, redirects, and newlines;
- timeout capped by `syscall_max_timeout`;
- default allowed commands:
  - `pwd`
  - `ls`
  - `cat`
  - `head`
  - `tail`
  - `grep`
  - `find`
  - `wc`
  - `echo`

This supports local diagnostics like `pwd` without exposing unrestricted command execution.

## Policy and participant endpoints

```text
GET /sessions/{session_id}/policy
GET /sessions/{session_id}/participants
```

`/policy` returns redacted configured params, resolved action names, and resolved execution guardrails.

`/participants` returns active/known participant metadata for the session.

## Secret redaction

The gateway redacts sensitive values in config/params responses. The TUI also redacts sensitive values in `/show agent params` and `/config agent params ...` confirmations.

Redacted key patterns include:

- `api_key`
- `token`
- `password`
- `secret`
- `authorization`

## Join-session scaffold

The gateway can track multiple websocket participants per session. A first CLI bridge exists:

```bash
./wolf join-session   --gateway http://127.0.0.1:8000   --account-id <account_id>   --session-id <session_id>   --token <token>   --participant-id sad_chaplygin_clone
```

Current scope: message-level participation. Joined entities can observe events and send `participant_message` events. A later milestone should add autonomous joined-agent loops, invite tokens, scoped action delegation, context transfer, and clone-test-promote workflows.

## Smoke testing

Use:

```bash
python scripts/gateway_smoke.py   --username max   --password ''   --policy dev   --host-address https://example-llm-host   --api-key "$LOCAL_API_KEY"   --model gpt-5.4-nano   --api-version v1   --message "What is the current working directory? Use run_syscall with command pwd, shell false, timeout 5."
```

The smoke script logs in, configures a session, checks `/policy`, opens a websocket, sends a message, and asserts gateway workflow events.

## Operational notes

- Restart the gateway after code changes: `bash runners/run_gateway.sh`.
- Restart/reconnect the TUI after client changes.
- If an action appears unavailable, check the `policy_resolved` event first.
- For dev-mode `run_syscall`, prompt for `shell: false` and an allowed command.
- Do not expose unrestricted `run_syscall` by default.
