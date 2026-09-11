from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator




CAPTURE_SCOPE_ALIASES = {
    "dashboard": "active_dashboard",
    "active_dashboard_panel": "active_dashboard_panels",
    "active_dashboard_panel_urls": "active_dashboard_panels",
    "dashboard_panels": "active_dashboard_panels",
    "dashboard_panel": "active_dashboard_panels",
    "panels": "active_dashboard_panels",
    "panel": "selected_panels",
    "selected_panel": "selected_panels",
    "selected_dashboard_panels": "selected_panels",
    "annotation": "annotation_regions",
    "annotations": "annotation_regions",
    "annotation_region": "annotation_regions",
    "selected_annotation": "annotation_regions",
    "selected_annotations": "annotation_regions",
    "boxed_region": "annotation_regions",
    "box": "annotation_regions",
    "selection": "annotation_regions",
    "selected_box": "annotation_regions",
    "gui": "full_gui",
    "whole_gui": "full_gui",
    "full_workspace": "workspace",
}


def normalize_capture_scope_alias(value: Any) -> Any:
    text = str(value or "").strip().lower()
    return CAPTURE_SCOPE_ALIASES.get(text, value)


class CaptureViewport(BaseModel):
    width: int = Field(default=1440, ge=100, le=7680)
    height: int = Field(default=900, ge=100, le=4320)
    device_scale_factor: float = Field(default=1.0, ge=0.25, le=4.0)


class CaptureUrlRequest(BaseModel):
    url: str = Field(..., description="URL to capture with the backend browser worker")
    viewport: CaptureViewport = Field(default_factory=CaptureViewport)
    clip: Optional[Dict[str, int]] = Field(default=None, description="Optional Playwright screenshot clip rectangle: x, y, width, height")
    format: Literal["png", "jpeg"] = "png"
    quality: int = Field(default=90, ge=1, le=100)
    full_page: bool = False
    wait_until: Literal["load", "domcontentloaded", "networkidle"] = "domcontentloaded"
    extra_wait_ms: int = Field(default=500, ge=0, le=10000)
    timeout_ms: int = Field(default=30000, ge=1000, le=120000)
    reason: str = Field(default="Agent requested screenshot capture")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CaptureWorkspaceRequest(BaseModel):
    visual_context: Dict[str, Any] = Field(default_factory=dict)
    urls: List[str] = Field(default_factory=list)
    panel_ids: List[str] = Field(default_factory=list)
    capture_scope: Literal[
        "url_list",
        "active_dashboard_panels",
        "selected_panels",
        "active_dashboard",
        "workspace",
        "full_gui",
        "annotation_regions",
    ] = Field(default="active_dashboard_panels", description="Explicit visual capture scope requested by the agent/client")
    include_annotations: bool = Field(default=True, description="Whether annotation overlays/metadata should be included when rendered capture is supported")
    annotation_ids: List[str] = Field(default_factory=list)
    padding_px: int = Field(default=24, ge=0, le=2000)
    include_agent_panel: bool = Field(default=False)
    include_toolbar: bool = Field(default=True)
    fail_if_no_targets: bool = Field(default=True)
    viewport: CaptureViewport = Field(default_factory=CaptureViewport)
    format: Literal["png", "jpeg"] = "png"
    quality: int = Field(default=90, ge=1, le=100)
    full_page: bool = False
    wait_until: Literal["load", "domcontentloaded", "networkidle"] = "domcontentloaded"
    extra_wait_ms: int = Field(default=500, ge=0, le=10000)
    timeout_ms: int = Field(default=30000, ge=1000, le=120000)
    max_panels: int = Field(default=6, ge=1, le=24)
    reason: str = Field(default="Agent requested workspace screenshot capture")
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_capture_scope_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict) and "capture_scope" in data:
            out = dict(data)
            out["capture_scope"] = normalize_capture_scope_alias(out.get("capture_scope"))
            return out
        return data


class CaptureResult(BaseModel):
    ok: bool
    status: Literal["success", "blocked", "timeout", "auth_failed", "error"]
    capture_id: str
    source_url: Optional[str] = None
    panel_id: Optional[str] = None
    image_path: Optional[str] = None
    metadata_path: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    format: str = "png"
    captured_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    error: Optional[str] = None
    policy: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
