# framework.orchestration

Async task-DAG orchestration runtime refactored around:

- **AgentPool**: agents are leased compute resources rather than fixed main/worker roles.
- **Task DAG**: lineage is tracked by `parent_id`, scheduling by `dependencies`.
- **TaskInfrastructureFactory**: creates task-local runtime state while allowing shared resources.
- **WorkflowPolicy**: task/workflow-specific orchestration policy and prompt building.
- **DynamicActionAdapter**: bridges to discoverable actions from `framework.workflows.workflow_models`.
- **Backward compatibility**: `yield_motion_to` is intentionally ignored by orchestration; legacy actions with `execute(infra=...)` remain usable.

## Main entry point

```python
from framework.orchestration import (
    AgentPool,
    AsyncWorkflowRuntime,
    EngineConfig,
    TaskInfrastructureFactory,
    TaskSpec,
)

agent_pool = AgentPool([...])
runtime = AsyncWorkflowRuntime(
    agent_pool=agent_pool,
    infra_factory=TaskInfrastructureFactory(),
    config=EngineConfig(),
)
root_id = await runtime.submit_root_task(
    TaskSpec(name="root", objective="Do the work", workflow_type="generic")
)
root = await runtime.run_until_complete(root_id)
```

## Design notes

### 1. Pool-based agents
Agents are not permanently attached to tasks. A paused or waiting task can be resumed by another compatible agent later.

### 2. Shared vs local infrastructure
`SharedResources` holds reusable backing resources such as KB/tool/universe handles.
`TaskInfrastructureFactory` creates task-local state for chat/memory/context and can optionally build a compatibility infrastructure object.

### 3. Discoverable actions
`DynamicActionAdapter` lazily imports `framework.workflows.workflow_models`, uses its `get_actions_subset(...)` when available, and validates agent outputs against the dynamically discovered union.

### 4. Domain actions
If an action object has `execute(infra=...)`, the runtime treats it as a domain action and executes it against the task-local infra (or compatibility infra if provided).

### 5. Orchestration actions
Built-in orchestration actions are:
- `create_subtasks`
- `complete_task`
- `publish_progress`
- `wait_for_tasks`
- `request_user_input`
- `pause_task`
- `fail_task`

## Files

- `models.py`: task/task-result/context models
- `events.py`: event bus
- `actions.py`: orchestration actions
- `agent_pool.py`: leasing and capability filtering
- `task_infra.py`: shared resources + task infrastructure factory
- `action_adapter.py`: bridge to discoverable workflow actions
- `agent_runner.py`: calls existing agent APIs
- `runtime.py`: scheduler + execution engine
- `policies.py`: workflow policy interfaces
- `default_agents.py`: smoke-test agents

## Best-effort integration
This package is designed to integrate with the project structure you shared, but it avoids hard-coding assumptions about undisclosed modules. For deeper integration with the full framework, the next most useful files would be the concrete `framework/workflows/agent_actions/*` actions and any real infrastructure builders used in production.
