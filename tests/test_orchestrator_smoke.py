from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from framework.orchestration.actions import CompleteTaskAction, CreateSubtasksAction
from framework.orchestration.agent_pool import AgentPool
from framework.orchestration.models import EngineConfig, ResourceBudget, TaskSpec, WaitPolicy, TaskStatus
from framework.orchestration.runtime import AsyncWorkflowRuntime
from framework.orchestration.task_infra import SharedResources, TaskInfrastructureFactory


class PermissiveActionAdapter:
    """Minimal adapter for smoke tests.

    Avoids importing the production dynamic action registry while still exposing
    the same interface expected by AgentRunner/AsyncWorkflowRuntime.
    """

    class Bundle:
        def __init__(self) -> None:
            self.validator = None
            self.schema_text = "Return an orchestration action object or JSON payload."
            self.source = "tests.PermissiveActionAdapter"

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
        # Root task: fan out on first execution, synthesize on resume.
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
            return CompleteTaskAction(
                summary=f"root synthesized by {self.name}",
                important_findings=["saw both child summaries"],
                facts=[{"kind": "synthesis", "agent": self.name}],
            )

        # One child fans out again to validate grandchildren.
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
            return CompleteTaskAction(
                summary=f"child-1 synthesized by {self.name}",
                facts=[{"kind": "child-summary", "agent": self.name}],
            )

        # Other leaves complete directly.
        return CompleteTaskAction(
            summary=f"leaf completed by {self.name}",
            important_findings=[f"handled by {self.name}"],
            facts=[{"kind": "leaf", "agent": self.name}],
        )


async def _run_demo() -> tuple[AsyncWorkflowRuntime, str]:
    pool = AgentPool([DemoAgent(f"agent-{i}") for i in range(4)])
    factory = TaskInfrastructureFactory(
        shared_resources=SharedResources(
            toolboxes={"demo_tb": object()},
            knowledge_bases={"demo_kb": object()},
            universes={"demo_univ": object()},
        ),
        session_root=str(ROOT / ".test_sessions"),
    )
    runtime = AsyncWorkflowRuntime(
        agent_pool=pool,
        infra_factory=factory,
        config=EngineConfig(
            budgets=ResourceBudget(
                max_active_tasks=4,
                max_total_tasks=16,
                max_subtree_tasks=16,
                max_children_per_task=4,
                max_depth=4,
            )
        ),
        action_adapter=PermissiveActionAdapter(),
    )

    events = []

    async def collector(event):
        events.append((event.type, event.task_id, event.actor, dict(event.payload)))

    runtime.event_bus.subscribe(collector)

    root_id = await runtime.submit_root_task(TaskSpec(name="root-demo", objective="Test the orchestrator end-to-end"))
    root = await runtime.run_until_complete(root_id)

    runtime._test_events = events  # attach for inspection in test
    return runtime, root_id


def test_orchestrator_smoke():
    runtime, root_id = asyncio.run(_run_demo())
    root = asyncio.run(runtime.repository.get(root_id))
    assert root is not None
    assert root.status == TaskStatus.COMPLETED
    assert root.result is not None
    assert "root synthesized" in root.result.summary

    children = runtime.graph.get_children(root_id)
    assert len(children) == 2
    child_names = sorted(c.spec.name for c in children)
    assert child_names == ["child-1", "child-2"]

    child_1 = [c for c in children if c.spec.name == "child-1"][0]
    grandchildren = runtime.graph.get_children(child_1.id)
    assert len(grandchildren) == 2
    assert sorted(g.spec.name for g in grandchildren) == ["grandchild-1a", "grandchild-1b"]

    # Verify task-local infrastructures are independent and created per task.
    all_task_ids = [root_id] + [c.id for c in children] + [g.id for g in grandchildren]
    assert all(tid in runtime.task_infras for tid in all_task_ids)
    session_dirs = {runtime.task_infras[tid].local.session_dir for tid in all_task_ids}
    assert len(session_dirs) == len(all_task_ids)

    # Root should have been resumed after waiting on children.
    event_types = [e[0] for e in runtime._test_events]
    assert "subtasks_created" in event_types
    assert "parent_resumed" in event_types
    assert event_types.count("task_completed") >= 5  # root + 2 children + 2 grandchildren

    # Agent pool should be fully released when runtime quiesces.
    stats = asyncio.run(runtime.agent_pool.stats())
    assert all(not row["busy"] for row in stats)
