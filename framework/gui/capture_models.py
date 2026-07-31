from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class CaptureViewport(BaseModel):
    width: int = Field(default=1440, ge=100, le=7680)
    height: int = Field(default=900, ge=100, le=4320)
    device_scale_factor: float = Field(default=1.0, ge=0.25, le=4.0)


class CaptureUrlRequest(BaseModel):
    url: str = Field(..., description="URL to capture with the backend browser worker")
    viewport: CaptureViewport = Field(default_factory=CaptureViewport)
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
