from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional
import importlib
import json


@dataclass(slots=True)
class ActionSchemaBundle:
    validator: Any
    schema_text: str
    source: str


class DynamicActionAdapter:
    def __init__(self, workflow_models_module: str = 'framework.workflows.workflow_models') -> None:
        self.workflow_models_module = workflow_models_module
        self._module = None

    def _load(self):
        if self._module is None:
            self._module = importlib.import_module(self.workflow_models_module)
        return self._module

    def build(self, allowed_action_names: Optional[List[str]]) -> ActionSchemaBundle:
        try:
            mod = self._load()
            if allowed_action_names and hasattr(mod, 'get_actions_subset'):
                validator, schema_text = mod.get_actions_subset(allowed_action_names)
                return ActionSchemaBundle(validator=validator, schema_text=schema_text, source=self.workflow_models_module)
            return ActionSchemaBundle(validator=getattr(mod, 'Actions', None), schema_text=getattr(mod, 'SCHEMA_STRING', ''), source=self.workflow_models_module)
        except Exception:
            schema = 'Orchestration actions only: create_subtasks, complete_task, publish_progress, wait_for_tasks, request_user_input, pause_task, fail_task'
            return ActionSchemaBundle(validator=None, schema_text=schema, source='fallback')

    def parse_json(self, raw: Any) -> Any:
        if isinstance(raw, (dict, list)):
            return raw
        if hasattr(raw, 'model_dump'):
            return raw.model_dump()
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return raw
        return raw
