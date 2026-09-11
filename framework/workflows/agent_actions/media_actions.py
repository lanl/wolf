from __future__ import annotations

import shlex
from collections import Counter
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ConfigDict, model_validator

from framework.utils.multimodal_input import UserInputBundle
from framework.workflows.base_agent_action import AgentAction


class LoadMediaContextActionArgs(BaseModel):
    """Payload for load_media_context.

    This action is intentionally workflow-independent.  It prepares existing
    local media files as provider-ready multimodal content using the shared
    MultimodalInputProcessor and stages that content on the infrastructure for
    the next agent turn.  Workflows that already consume
    infra.consume_pending_agent_content() can pass the staged content to the
    model without this action depending on any one workflow implementation.
    """

    model_config = ConfigDict(populate_by_name=True)

    file_paths: list[str] = Field(
        ...,
        description="One or more local file paths to load into the next agent turn as multimodal context.",
        min_length=1,
    )
    prompt: str = Field(
        default="Inspect the attached media and summarize the relevant visual/audio/document context for the current task.",
        description="Instruction/question to pair with the media when it is staged for the next agent turn.",
    )
    target_agent: Optional[str] = Field(
        default=None,
        description="Optional target agent/worker name whose capabilities should be used for media preparation. Defaults to infra.agent.",
    )
    clear_existing: bool = Field(
        default=True,
        description="Replace any pending staged multimodal context if true; append to it if false.",
    )
    force_inline_images: bool = Field(
        default=True,
        description=(
            "If true, prepare image files as provider-ready image_url content blocks even when "
            "the target agent configuration does not explicitly list the 'vision' capability."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def accept_single_file_path_aliases(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            if "file_paths" not in data:
                if "file_path" in data:
                    data["file_paths"] = [data.pop("file_path")]
                elif "path" in data:
                    data["file_paths"] = [data.pop("path")]
            elif isinstance(data.get("file_paths"), str):
                data["file_paths"] = [data["file_paths"]]
        return data


class LoadMediaContextAction(AgentAction):
    action: Literal["load_media_context"] = "load_media_context"
    description: Literal[
        "Stage local images, PDFs, text, audio, or video metadata as multimodal context for the next agent turn"
    ] = "Stage local images, PDFs, text, audio, or video metadata as multimodal context for the next agent turn"
    payload: LoadMediaContextActionArgs
    payload_schema: str = """
    {"file_paths": [<string>: "/path/to/media_or_document"],
     "prompt": <string>: "Question/instruction to pair with the media for the next agent turn",
     "target_agent": <string|null>: "Optional agent/worker name whose capabilities should be used",
     "clear_existing": <bool>: true,
     "force_inline_images": <bool>: true
     }
     """


    class _CapabilityView:
        """Delegate to an agent while exposing adjusted capabilities for preprocessing."""

        def __init__(self, agent: Any, capabilities: list):
            self._agent = agent
            self.capabilities = capabilities

        def __getattr__(self, name: str) -> Any:
            return getattr(self._agent, name)

    def _resolve_target_agent(self, infra: Any):
        target_name = self.payload.target_agent
        if not target_name:
            return getattr(infra, "agent", None)

        if getattr(getattr(infra, "agent", None), "name", None) == target_name:
            return infra.agent

        workers = getattr(infra, "workers", None)
        if isinstance(workers, dict) and target_name in workers:
            return workers[target_name]

        # Some infrastructure versions expose WORKERS / assistants differently;
        # keep the action tolerant and fall back to the main agent.
        return getattr(infra, "agent", None)

    @staticmethod
    def _bundle_summary(bundle: UserInputBundle) -> str:
        lines: list = []
        lines.append(" staged multimodal context for next agent turn")
        if bundle.clean_text:
            lines.append(f"prompt: {bundle.clean_text}")
        lines.append(f"attachments: {len(bundle.attachments)}")

        block_counts = Counter(str(block.get("type", "unknown")) for block in bundle.agent_content)
        image_url_blocks = block_counts.get("image_url", 0)

        for att in bundle.attachments:
            status = "error" if att.error else "ok"
            lines.append(
                f"- {att.name} | modality={att.modality} | mime={att.mime_type} | "
                f"size={att.size_bytes} | path={att.path} | status={status}"
            )
            if att.error:
                lines.append(f"  error: {att.error}")
            elif att.modality == "image":
                if image_url_blocks:
                    lines.append("  inline_status: inlined as provider image_url content for the next agent call")
                else:
                    lines.append("  inline_status: NOT inlined; next agent call will only receive text metadata")

        if bundle.agent_content:
            lines.append("agent_content_blocks:")
            for block_type, count in sorted(block_counts.items()):
                lines.append(f"- {block_type}: {count}")
        else:
            lines.append("agent_content_blocks: none")

        if bundle.errors:
            lines.append("errors:")
            lines.extend(f"- {err}" for err in bundle.errors)
        lines.append(
            "Note: heavy media payloads are staged in-memory only; persistent history contains metadata, not base64 blobs."
        )
        return "\n".join(lines)

    def execute(self, infra: Any = None) -> None:
        if infra is None:
            return None

        try:
            processor = getattr(infra, "input_processor", None)
            if processor is None:
                raise RuntimeError("infra.input_processor is not available")

            target_agent = self._resolve_target_agent(infra)
            processing_agent = target_agent
            if self.payload.force_inline_images and target_agent is not None:
                caps = list(getattr(target_agent, "capabilities", None) or [])
                if "vision" not in {str(c).strip().lower() for c in caps}:
                    caps.append("vision")
                    processing_agent = self._CapabilityView(target_agent, caps)

            quoted_paths = " ".join(shlex.quote(str(Path(p).expanduser())) for p in self.payload.file_paths)
            raw_text = f"{self.payload.prompt}\n<input> {quoted_paths} <input/>"
            bundle = processor.process(raw_text, agent=processing_agent)

            pending = bundle.agent_content if bundle.has_attachments else []
            if self.payload.clear_existing or not getattr(infra, "pending_agent_content", None):
                infra.pending_agent_content = pending
            else:
                existing = getattr(infra, "pending_agent_content", None) or []
                infra.pending_agent_content = [*existing, *pending]
            infra.pending_user_input_bundle = bundle

            ctx_msg = self._bundle_summary(bundle)
        except Exception as action_err:
            ctx_msg = f"[load_media_context][error]: {action_err}"

        if hasattr(infra, "append_chat_history"):
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
        return None


class MediaMetadataActionArgs(BaseModel):
    """Payload for media_metadata.

    Produces durable, text-only metadata for local media files.  This is useful
    when the model lacks native vision/audio/video support or when persistent
    context should contain lightweight facts such as dimensions, MIME type, and
    basic image statistics.
    """

    model_config = ConfigDict(populate_by_name=True)

    file_paths: list[str] = Field(..., description="One or more local media/document paths.", min_length=1)
    include_image_stats: bool = Field(default=True, description="Include image dimensions/mode and simple channel statistics when Pillow is available.")

    @model_validator(mode="before")
    @classmethod
    def accept_single_file_path_aliases(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            if "file_paths" not in data:
                if "file_path" in data:
                    data["file_paths"] = [data.pop("file_path")]
                elif "path" in data:
                    data["file_paths"] = [data.pop("path")]
            elif isinstance(data.get("file_paths"), str):
                data["file_paths"] = [data["file_paths"]]
        return data


class MediaMetadataAction(AgentAction):
    action: Literal["media_metadata"] = "media_metadata"
    description: Literal["Extract durable text-only metadata for local media/document files"] = "Extract durable text-only metadata for local media/document files"
    payload: MediaMetadataActionArgs
    payload_schema: str = """
    {"file_paths": [<string>: "/path/to/media_or_document"],
     "include_image_stats": <bool>: true
     }
     """

    @staticmethod
    def _basic_file_metadata(path: Path) -> dict[str, Any]:
        import mimetypes

        exists = path.exists() and path.is_file()
        return {
            "path": str(path),
            "name": path.name,
            "suffix": path.suffix.lower(),
            "exists": exists,
            "size_bytes": path.stat().st_size if exists else None,
            "mime_type": mimetypes.guess_type(str(path))[0] or "application/octet-stream",
        }

    @staticmethod
    def _image_metadata(path: Path) -> dict[str, Any]:
        try:
            from PIL import Image, ImageStat

            with Image.open(path) as im:
                info: dict[str, Any] = {
                    "image_size": list(im.size),
                    "image_mode": im.mode,
                }
                try:
                    stat = ImageStat.Stat(im.convert("RGBA"))
                    info["rgba_mean"] = [round(float(x), 3) for x in stat.mean]
                    info["rgba_extrema"] = [list(x) for x in stat.extrema]
                except Exception as exc:
                    info["image_stats_error"] = str(exc)
                return info
        except Exception as exc:
            return {"image_metadata_error": str(exc)}

    def execute(self, infra: Any = None) -> None:
        import json

        records: list[dict[str, Any]] = []
        for raw in self.payload.file_paths:
            path = Path(raw).expanduser().resolve()
            rec = self._basic_file_metadata(path)
            if rec.get("exists") and self.payload.include_image_stats:
                suffix = str(rec.get("suffix") or "").lower()
                mime = str(rec.get("mime_type") or "")
                if mime.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}:
                    rec.update(self._image_metadata(path))
            records.append(rec)

        ctx_msg = "[media_metadata]\n" + json.dumps(records, indent=2, sort_keys=True)
        if infra is not None and hasattr(infra, "append_chat_history"):
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
        return None
