from __future__ import annotations

import json
from typing import Any, Dict, Literal, Optional

import requests
from pydantic import BaseModel, Field, AliasChoices

from framework.workflows.base_agent_action import AgentAction
from framework.universes.endpoint_resolver import get_universe_base_url_or_error


DEFAULT_TIMEOUT = 30


def _append_result(infra: Any, action: str, universe: str, base_url: str, result: Any) -> None:
    try:
        infra.append_chat_history(
            actor="system",
            content=f"[Universe App Templates][{action}] universe={universe} url={base_url}:\n{json.dumps(result, indent=2, sort_keys=True)}",
            action={"action": action},
            log_console=True,
        )
    except Exception:
        pass


def _base_url(infra: Any, universe: str) -> tuple[str, Optional[str]]:
    return get_universe_base_url_or_error(infra, universe)[:2]


class UniverseAppTemplateBaseArgs(BaseModel):
    system: str = Field(default="local", description="System where the Universe is registered")
    universe: str = Field(..., description="Name of the Universe")


class UniverseAppTemplateGetArgs(UniverseAppTemplateBaseArgs):
    template_id: str = Field(..., description="Template id, e.g. static_report")


class UniverseAppTemplateInstantiateArgs(UniverseAppTemplateGetArgs):
    app_id: Optional[str] = Field(default=None, description="Stable generated app id")
    title: Optional[str] = Field(default=None, description="Optional generated app title")
    output_dir: str = Field(..., description="Target directory for generated app files")
    context: Dict[str, Any] = Field(default_factory=dict, description="Template variable values")
    auto_register: bool = Field(default=True, validation_alias=AliasChoices("auto_register", "register"), description="Register the rendered app with the Universe")
    overwrite: bool = Field(default=False, description="Allow replacing existing files")


class UniverseAppTemplateListAction(AgentAction):
    action: Literal["universe_app_template_list"] = "universe_app_template_list"
    description: Literal["List reusable Universe app templates"] = "List reusable Universe app templates"
    payload: UniverseAppTemplateBaseArgs
    payload_schema: str = '{"system": "local", "universe": "u"}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        base, err = _base_url(infra, self.payload.universe)
        if err:
            result = {"ok": False, "error": err}
        else:
            try:
                r = requests.get(f"{base}/app-templates", timeout=DEFAULT_TIMEOUT)
                r.raise_for_status()
                result = r.json()
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _append_result(infra, self.action, self.payload.universe, base or "<unresolved>", result)
        return result


class UniverseAppTemplateGetAction(AgentAction):
    action: Literal["universe_app_template_get"] = "universe_app_template_get"
    description: Literal["Get metadata and parameters for one reusable Universe app template"] = "Get metadata and parameters for one reusable Universe app template"
    payload: UniverseAppTemplateGetArgs
    payload_schema: str = '{"system": "local", "universe": "u", "template_id": "static_report"}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        base, err = _base_url(infra, self.payload.universe)
        if err:
            result = {"ok": False, "error": err}
        else:
            try:
                r = requests.get(f"{base}/app-templates/{self.payload.template_id}", timeout=DEFAULT_TIMEOUT)
                r.raise_for_status()
                result = r.json()
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _append_result(infra, self.action, self.payload.universe, base or "<unresolved>", result)
        return result


class UniverseAppTemplateInstantiateAction(AgentAction):
    action: Literal["universe_app_template_instantiate"] = "universe_app_template_instantiate"
    description: Literal["Create/register a Universe app from a reusable template"] = "Create/register a Universe app from a reusable template"
    payload: UniverseAppTemplateInstantiateArgs
    payload_schema: str = '{"system": "local", "universe": "u", "template_id": "static_report", "app_id": "report", "output_dir": "scratch/report", "context": {"title": "Report"}, "auto_register": true}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        base, err = _base_url(infra, self.payload.universe)
        if err:
            result = {"ok": False, "error": err}
        else:
            try:
                data = self.payload.model_dump(exclude_none=True)
                data.pop("system", None)
                data.pop("universe", None)
                template_id = data.pop("template_id")
                r = requests.post(f"{base}/app-templates/{template_id}/instantiate", json=data, timeout=DEFAULT_TIMEOUT)
                r.raise_for_status()
                result = r.json()
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _append_result(infra, self.action, self.payload.universe, base or "<unresolved>", result)
        return result
