from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol

from .models import EngineConfig, TaskNode, TaskSpec


class WorkflowPolicy(Protocol):
    workflow_type: str
    def allowed_action_names(self, task: TaskNode) -> List[str] | None: ...
    def build_agent_prompt(self, task: TaskNode, context: str, schema_text: str) -> str: ...
    def choose_workflow_type(self, parent: TaskNode, child_spec: TaskSpec) -> str: ...


@dataclass
class GenericWorkflowPolicy:
    workflow_type: str = 'generic'
    def allowed_action_names(self, task: TaskNode) -> List[str] | None:
        return task.spec.allowed_actions
    def build_agent_prompt(self, task: TaskNode, context: str, schema_text: str) -> str:
        return (
            f'You are handling task {task.id} ({task.spec.name}).\n'
            f'Objective: {task.spec.objective}\n\n'
            f'Context:\n{context}\n\n'
            'Return either one orchestration action or one discovered domain action.\n'
            f'Allowed action schema:\n{schema_text}\n'
        )
    def choose_workflow_type(self, parent: TaskNode, child_spec: TaskSpec) -> str:
        return child_spec.workflow_type or parent.spec.workflow_type or 'generic'


class BudgetGuard:
    def __init__(self, config: EngineConfig, graph) -> None:
        self.config = config
        self.graph = graph
    def validate_new_child(self, parent: TaskNode, nchildren: int) -> None:
        budgets = self.config.budgets
        if parent.depth + 1 > budgets.max_depth:
            raise RuntimeError('Maximum task depth exceeded')
        if nchildren > budgets.max_children_per_task:
            raise RuntimeError('Maximum children per task exceeded')
        if self.graph.subtree_size(parent.id) + nchildren > budgets.max_subtree_tasks:
            raise RuntimeError('Maximum subtree task count exceeded')


@dataclass
class ChatWorkflowPolicy(GenericWorkflowPolicy):
    workflow_type: str = 'chat'
    def build_agent_prompt(self, task: TaskNode, context: str, schema_text: str) -> str:
        return (
            f'You are participating in the task workspace for task {task.id} ({task.spec.name}).\n'
            f'Objective: {task.spec.objective}\n\n'
            'Read the task thread and decide the single best next action. '
            'If you want to speak to the user, use a messaging-style action such as send_message, or return plain text if messaging actions are unavailable. '
            'If work is complete, complete the task. If you need more information, request user input. '
            'If you need to use tools, choose one allowed domain action.\n\n'
            f'Context:\n{context}\n\n'
            f'Allowed action schema:\n{schema_text}\n'
        )
