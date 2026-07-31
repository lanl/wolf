from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from framework.gui.capture_models import CaptureUrlRequest, CaptureViewport, CaptureWorkspaceRequest
from framework.gui.capture_worker import capture_url
from framework.workflows.base_agent_action import AgentAction


class GuiCaptureUrlPayload(CaptureUrlRequest):
    session_id: Optional[str] = Field(default=None, description="Gateway/session id used for capture storage grouping")


class GuiCaptureWorkspacePayload(CaptureWorkspaceRequest):
    session_id: Optional[str] = Field(default=None, description="Gateway/session id used for capture storage grouping")


class GuiCaptureUrlAction(AgentAction):
    action: Literal["gui_capture_url"] = "gui_capture_url"
    description: Literal["Capture a pixel screenshot of an allowed URL using the permissioned backend browser capture service"] = "Capture a pixel screenshot of an allowed URL using the permissioned backend browser capture service"
    payload: GuiCaptureUrlPayload
    payload_schema: str = '{"url":"https://example.com","viewport":{"width":1440,"height":900,"device_scale_factor":1},"format":"png","full_page":false,"reason":"why capture is needed"}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        session_id = data.pop("session_id", None) or "direct"
        req = CaptureUrlRequest.model_validate(data)
        result = capture_url(req, session_id=session_id).model_dump(mode="json")
        try:
            infra.append_chat_history(actor="system", content=f"[GUI] gui_capture_url result: {result}", action={"action": "system_info"}, log_console=True)
        except Exception:
            pass
        return result


class GuiCaptureWorkspaceAction(AgentAction):
    action: Literal["gui_capture_workspace"] = "gui_capture_workspace"
    description: Literal["Capture pixel screenshots for selected URL panels from the GUI workspace using the permissioned backend capture service"] = "Capture pixel screenshots for selected URL panels from the GUI workspace using the permissioned backend capture service"
    payload: GuiCaptureWorkspacePayload
    payload_schema: str = '{"urls":["https://example.com"],"max_panels":4,"viewport":{"width":1440,"height":900,"device_scale_factor":1},"reason":"why capture is needed"}'

    def execute(self, infra: Any = None) -> Dict[str, Any]:
        data = self.payload.model_dump(exclude_none=True)
        session_id = data.pop("session_id", None) or "direct"
        req = CaptureWorkspaceRequest.model_validate(data)
        urls = list(req.urls or [])[: int(req.max_panels or 1)]
        results = []
        for url in urls:
            one = CaptureUrlRequest(
                url=url,
                viewport=req.viewport,
                format=req.format,
                quality=req.quality,
                full_page=req.full_page,
                wait_until=req.wait_until,
                extra_wait_ms=req.extra_wait_ms,
                timeout_ms=req.timeout_ms,
                reason=req.reason,
                metadata=req.metadata,
            )
            results.append(capture_url(one, session_id=session_id).model_dump(mode="json"))
        out = {"ok": all(r.get("ok") for r in results) if results else False, "count": len(results), "results": results}
        try:
            infra.append_chat_history(actor="system", content=f"[GUI] gui_capture_workspace result: {out}", action={"action": "system_info"}, log_console=True)
        except Exception:
            pass
        return out
