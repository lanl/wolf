from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class HeavyFieldSpec(BaseModel):
    """Description of one heavy/plain-text field collected outside JSON.

    Heavy fields are deliberately generated as plain text so large Markdown,
    code, file content, or shell snippets do not have to be JSON-escaped by the
    model. The workflow/action builder later inserts the text into the final
    payload at ``payload_path``.
    """

    field_name: str = Field(description="Human/action-facing field name, e.g. message or content")
    payload_path: list[str] = Field(description="Path inside final action payload, e.g. ['payload', 'message']")
    content_type: str = Field(default="text", description="text, markdown, code, shell_command, shell_script, etc.")
    stream_to_cli: bool = Field(default=True, description="Whether deltas may be shown through a non-durable CLI stream")
    required: bool = Field(default=True, description="Whether the field must be collected")
    prompt_hint: Optional[str] = Field(default=None, description="Action-specific instruction for generating this field")
    stop_hint: Optional[str] = Field(default=None, description="Optional instruction describing how to terminate generation")


class ActionBuildSpec(BaseModel):
    """Action-specific staged payload construction metadata."""

    action_name: str
    mode: Literal["small_json", "json_plus_heavy_text"] = "small_json"
    small_fields: list[str] = Field(default_factory=list)
    heavy_fields: list[HeavyFieldSpec] = Field(default_factory=list)
    selection_description: Optional[str] = None
    risk_level: str = "normal"
    tags: list[str] = Field(default_factory=list)
    json_prompt_hint: Optional[str] = None
    heavy_prompt_hint: Optional[str] = None
    supports_one_shot: bool = True

    @property
    def has_heavy_fields(self) -> bool:
        return bool(self.heavy_fields)


class StagedActionBuildResult(BaseModel):
    """Result produced by staged action assembly before execution."""

    ok: bool
    action_name: Optional[str] = None
    action_dict: Optional[dict[str, Any]] = None
    action_obj: Any = None
    normalized: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    stage: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True


class StagedActionError(BaseModel):
    stage: str
    message: str
    action_name: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)