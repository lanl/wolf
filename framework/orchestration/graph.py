from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Set

from .models import TaskNode, TaskStatus


class TaskGraph:
    def __init__(self) -> None:
        self.tasks: Dict[str, TaskNode] = {}
        self.children: Dict[str, List[str]] = defaultdict(list)
        self.reverse_deps: Dict[str, List[str]] = defaultdict(list)

    def add_task(self, task: TaskNode) -> None:
        if task.id in self.tasks:
            raise ValueError(f'Task already exists: {task.id}')
        self._ensure_no_cycle(task)
        self.tasks[task.id] = task
        if task.spec.parent_id:
            self.children[task.spec.parent_id].append(task.id)
        for dep in task.spec.dependencies:
            self.reverse_deps[dep].append(task.id)

    def _ensure_no_cycle(self, task: TaskNode) -> None:
        if task.id in task.spec.dependencies:
            raise ValueError('Task cannot depend on itself')
        ancestors: Set[str] = set(task.lineage)
        for dep in task.spec.dependencies:
            if dep in ancestors:
                raise ValueError(f'Dependency cycle detected: {task.id} cannot depend on ancestor {dep}')

    def dependencies_satisfied(self, task_id: str) -> bool:
        task = self.tasks[task_id]
        return all(self.tasks[d].status == TaskStatus.COMPLETED for d in task.spec.dependencies)

    def runnable_tasks(self) -> List[TaskNode]:
        runnable: List[TaskNode] = []
        for task in self.tasks.values():
            if task.status in {TaskStatus.PENDING, TaskStatus.READY} and self.dependencies_satisfied(task.id):
                runnable.append(task)
        runnable.sort(key=lambda t: (-t.spec.priority, t.created_at))
        return runnable

    def get_children(self, parent_id: str) -> List[TaskNode]:
        return [self.tasks[cid] for cid in self.children.get(parent_id, [])]

    def subtree_size(self, root_id: str) -> int:
        total = 1
        for child_id in self.children.get(root_id, []):
            total += self.subtree_size(child_id)
        return total
