from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from framework.orchestration.actions import CompleteTaskAction, CreateSubtasksAction
from framework.orchestration.agent_pool import AgentPool
from framework.orchestration.models import EngineConfig, ResourceBudget, TaskSpec, WaitPolicy
from framework.orchestration.runtime import AsyncWorkflowRuntime
from framework.orchestration.task_infra import SharedResources, TaskInfrastructureFactory


class PermissiveActionAdapter:
    class Bundle:
        def __init__(self) -> None:
            self.validator = None
            self.schema_text = "Return an orchestration action object or JSON payload."
            self.source = "scripts.PermissiveActionAdapter"

    def build(self, allowed_action_names):
        return self.Bundle()

    def parse_json(self, raw):
        return raw

    def validate(self, payload, validator):
        return payload


class DemoAgent:
    def __init__(self, name: str):
        self.name = name
        self.capabilities = ["structured_output", "planning"]
        self.model = "demo-model"
        self.max_ctx_tokens = 32000

    async def get_json_structured_output_async(self, user_prompt, output_format=None):
        if "Task Name: root-demo" in user_prompt:
            if "Child Summaries:\n<none>" in user_prompt:
                return CreateSubtasksAction(
                    subtasks=[
                        TaskSpec(name="child-1", objective="Analyze branch 1"),
                        TaskSpec(name="child-2", objective="Analyze branch 2"),
                    ],
                    wait_policy=WaitPolicy.ALL,
                    summary=f"{self.name} delegated root task",
                )
            return CompleteTaskAction(summary=f"root synthesized by {self.name}")

        if "Task Name: child-1" in user_prompt:
            if "Child Summaries:\n<none>" in user_prompt:
                return CreateSubtasksAction(
                    subtasks=[
                        TaskSpec(name="grandchild-1a", objective="Inspect sub-branch A"),
                        TaskSpec(name="grandchild-1b", objective="Inspect sub-branch B"),
                    ],
                    wait_policy=WaitPolicy.ALL,
                    summary=f"{self.name} delegated child-1",
                )
            return CompleteTaskAction(summary=f"child-1 synthesized by {self.name}")

        return CompleteTaskAction(summary=f"leaf completed by {self.name}")


async def main() -> None:
    pool = AgentPool([DemoAgent(f"agent-{i}") for i in range(4)])
    factory = TaskInfrastructureFactory(
        shared_resources=SharedResources(
            toolboxes={"demo_tb": object()},
            knowledge_bases={"demo_kb": object()},
            universes={"demo_univ": object()},
        ),
        session_root=str(ROOT / ".demo_sessions"),
    )
    runtime = AsyncWorkflowRuntime(
        agent_pool=pool,
        infra_factory=factory,
        config=EngineConfig(
            budgets=ResourceBudget(max_active_tasks=4, max_total_tasks=16, max_subtree_tasks=16, max_children_per_task=4, max_depth=4)
        ),
        action_adapter=PermissiveActionAdapter(),
    )

    async def printer(event):
        print(f"[{event.type}] task={event.task_id[:8]} actor={event.actor} payload={event.payload}")

    runtime.event_bus.subscribe(printer)

    root_id = await runtime.submit_root_task(TaskSpec(name="root-demo", objective="Test the orchestrator end-to-end"))
    root = await runtime.run_until_complete(root_id)

    print("\n=== FINAL RESULT ===")
    print(f"root_id={root.id}")
    print(f"status={root.status.value}")
    print(f"summary={root.result.summary if root.result else root.error}")

    print("\n=== TASK GRAPH ===")
    for task in runtime.graph.tasks.values():
        print(f"- {task.spec.name:16s} status={task.status.value:10s} parent={task.spec.parent_id} owner={task.owner_agent_name} leased={task.leased_agent_name}")

    print("\n=== AGENT POOL ===")
    for row in await runtime.agent_pool.stats():
        print(row)


if __name__ == "__main__":
    asyncio.run(main())
