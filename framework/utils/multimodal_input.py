from __future__ import annotations

import base64
import mimetypes
import os
import re
import shlex
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


INPUT_TAG_RE = re.compile(
    r"<input(?:\s+[^>]*)?>\s*(.*?)\s*(?:</input>|<input\s*/>)",
    flags=re.IGNORECASE | re.DOTALL,
)


@dataclass
class MultimodalInputConfig:
    """Configuration for reusable multimodal user-input processing.

    The processor intentionally keeps heavy payloads out of history. Large or
    unsupported files are represented as metadata in the prompt/history rather
    than inlined as base64.
    """

    root_dir: Optional[str] = None
    max_file_bytes: int = 25 * 1024 * 1024
    max_text_chars: int = 60_000
    max_pdf_chars: int = 60_000
    inline_images: bool = True
    extract_text_files: bool = True
    extract_pdf_text: bool = True
    transcribe_audio: bool = False
    allow_missing_files: bool = False


@dataclass
class InputAttachment:
    original_reference: str
    path: str
    name: str
    exists: bool
    size_bytes: Optional[int] = None
    mime_type: str = "application/octet-stream"
    modality: str = "binary"
    suffix: str = ""
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class UserInputBundle:
    raw_text: str
    clean_text: str
    history_text: str
    attachments: List[InputAttachment] = field(default_factory=list)
    agent_content: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def has_attachments(self) -> bool:
        return bool(self.attachments)

    @property
    def has_rich_agent_content(self) -> bool:
        return bool(self.agent_content)

    def to_dict(self, include_agent_content: bool = False) -> Dict[str, Any]:
        data = {
            "raw_text": self.raw_text,
            "clean_text": self.clean_text,
            "history_text": self.history_text,
            "attachments": [a.to_dict() for a in self.attachments],
            "errors": list(self.errors),
        }
        if include_agent_content:
            data["agent_content"] = self.agent_content
        return data


def normalize_capabilities(capabilities: Any) -> set[str]:
    """Normalize agent/model capability declarations to lowercase tokens.

    CLI/session configuration may provide capabilities as a Python list,
    a comma/space separated string (e.g. ``"vision,tools"``), or a nested
    collection. Treating a string with ``set("vision")`` creates a set of
    characters and breaks membership checks, so all multimodal gating should
    go through this helper.
    """
    if capabilities is None:
        return set()

    tokens: list[str] = []

    def add(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return
            # Accept env-friendly forms: "vision", "vision,tools",
            # "['vision', 'tools']", and whitespace separated values.
            stripped = stripped.strip("[](){}")
            for part in re.split(r"[,\s]+", stripped):
                token = part.strip().strip("'\"").lower()
                if token:
                    tokens.append(token)
            return
        if isinstance(value, dict):
            for key, enabled in value.items():
                if enabled:
                    add(key)
            return
        try:
            iterator = iter(value)
        except TypeError:
            token = str(value).strip().lower()
            if token:
                tokens.append(token)
            return
        for item in iterator:
            add(item)

    add(capabilities)
    return set(tokens)


class MultimodalInputProcessor:
    """Workflow-independent parser/classifier/adapter for tagged inputs.

    Supported tag forms:
      - <input> ./path/to/file.png <input/>
      - <input> ./path/to/file.png </input>

    The processor returns two representations:
      - history_text: compact, persistent, no heavy base64 blobs
      - agent_content: provider-ready OpenAI-style content blocks for the next
        immediate agent call when possible
    """

    IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
    AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".opus"}
    VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
    TEXT_EXTS = {
        ".txt", ".md", ".rst", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
        ".toml", ".csv", ".tsv", ".xml", ".html", ".css", ".sh", ".bash",
        ".c", ".cpp", ".h", ".hpp", ".java", ".go", ".rs", ".sql", ".log",
    }
    PDF_EXTS = {".pdf"}

    def __init__(self, config: Optional[MultimodalInputConfig] = None):
        self.config = config or MultimodalInputConfig()

    def process(self, raw_text: str, agent: Any = None) -> UserInputBundle:
        clean_text, references = self._extract_references(raw_text)
        attachments: List[InputAttachment] = []
        errors: List[str] = []
        agent_content: List[Dict[str, Any]] = []

        caps = normalize_capabilities(getattr(agent, "capabilities", None))
        if clean_text.strip():
            agent_content.append({"type": "text", "text": clean_text.strip()})

        for ref in references:
            attachment = self._build_attachment(ref)
            attachments.append(attachment)
            if attachment.error:
                errors.append(attachment.error)

            blocks = self._attachment_to_openai_blocks(attachment, caps=caps, agent=agent)
            agent_content.extend(blocks)

        history_text = self._build_history_text(clean_text, attachments, errors)
        if not attachments:
            # Preserve old behavior as much as possible: callers can ignore
            # agent_content and pass the original/clean text as a string.
            agent_content = []

        return UserInputBundle(
            raw_text=raw_text,
            clean_text=clean_text.strip(),
            history_text=history_text,
            attachments=attachments,
            agent_content=agent_content,
            errors=errors,
        )

    def _extract_references(self, raw_text: str) -> tuple[str, List[str]]:
        references: List[str] = []

        def repl(match: re.Match) -> str:
            inner = match.group(1).strip()
            if inner:
                references.extend(self._split_reference_text(inner))
            return " "

        clean_text = INPUT_TAG_RE.sub(repl, raw_text)
        clean_text = re.sub(r"[ \t]+", " ", clean_text)
        clean_text = re.sub(r"\n\s*\n\s*\n+", "\n\n", clean_text).strip()
        return clean_text, references

    def _split_reference_text(self, text: str) -> List[str]:
        try:
            parts = shlex.split(text)
        except ValueError:
            parts = text.split()
        return parts if parts else [text]

    def _resolve_path(self, ref: str) -> Path:
        p = Path(os.path.expandvars(os.path.expanduser(ref)))
        if not p.is_absolute() and self.config.root_dir:
            p = Path(self.config.root_dir) / p
        return p.resolve()

    def _build_attachment(self, ref: str) -> InputAttachment:
        p = self._resolve_path(ref)
        suffix = p.suffix.lower()
        mime_type = mimetypes.guess_type(str(p))[0] or self._mime_from_ext(suffix)
        modality = self._modality_from_mime_ext(mime_type, suffix)
        exists = p.exists() and p.is_file()
        size = p.stat().st_size if exists else None
        error = None
        if not exists and not self.config.allow_missing_files:
            error = f"Attachment not found or not a file: {ref}"
        elif size is not None and size > self.config.max_file_bytes:
            error = f"Attachment exceeds max_file_bytes ({self.config.max_file_bytes}): {p} ({size} bytes)"
        return InputAttachment(
            original_reference=ref,
            path=str(p),
            name=p.name,
            exists=exists,
            size_bytes=size,
            mime_type=mime_type,
            modality=modality,
            suffix=suffix,
            error=error,
        )

    def _mime_from_ext(self, suffix: str) -> str:
        if suffix in self.IMAGE_EXTS:
            return f"image/{'jpeg' if suffix in {'.jpg', '.jpeg'} else suffix.lstrip('.')}"
        if suffix in self.AUDIO_EXTS:
            return f"audio/{suffix.lstrip('.')}"
        if suffix in self.VIDEO_EXTS:
            return f"video/{suffix.lstrip('.')}"
        if suffix in self.TEXT_EXTS:
            return "text/plain"
        if suffix in self.PDF_EXTS:
            return "application/pdf"
        return "application/octet-stream"

    def _modality_from_mime_ext(self, mime_type: str, suffix: str) -> str:
        if mime_type.startswith("image/") or suffix in self.IMAGE_EXTS:
            return "image"
        if mime_type.startswith("audio/") or suffix in self.AUDIO_EXTS:
            return "audio"
        if mime_type.startswith("video/") or suffix in self.VIDEO_EXTS:
            return "video"
        if mime_type == "application/pdf" or suffix in self.PDF_EXTS:
            return "pdf"
        if mime_type.startswith("text/") or suffix in self.TEXT_EXTS:
            return "text"
        return "binary"

    def _attachment_to_openai_blocks(
        self,
        attachment: InputAttachment,
        caps: Sequence[str] | set[str],
        agent: Any = None,
    ) -> List[Dict[str, Any]]:
        if attachment.error:
            return [{"type": "text", "text": f"[Attachment error] {attachment.error}"}]

        metadata_text = self._attachment_metadata_text(attachment)

        if attachment.modality == "image":
            if self.config.inline_images and "vision" in caps:
                try:
                    data_uri = self._file_to_data_uri(attachment.path, attachment.mime_type)
                    return [
                        {"type": "text", "text": metadata_text},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ]
                except Exception as exc:
                    return [{"type": "text", "text": f"{metadata_text}\n[Image inline error] {exc}"}]
            return [{"type": "text", "text": f"{metadata_text}\n[Image not inlined: agent lacks 'vision' capability]"}]

        if attachment.modality == "text" and self.config.extract_text_files:
            return [{"type": "text", "text": self._read_text_attachment(attachment, self.config.max_text_chars)}]

        if attachment.modality == "pdf" and self.config.extract_pdf_text:
            return [{"type": "text", "text": self._read_pdf_attachment(attachment)}]

        if attachment.modality == "audio":
            if self.config.transcribe_audio and agent is not None and hasattr(agent, "get_audio_transcription"):
                try:
                    transcript = agent.get_audio_transcription(attachment.path)
                    return [{"type": "text", "text": f"{metadata_text}\n[Audio transcript]\n{transcript}"}]
                except Exception as exc:
                    return [{"type": "text", "text": f"{metadata_text}\n[Audio transcription failed] {exc}"}]
            return [{"type": "text", "text": f"{metadata_text}\n[Audio attached as metadata; transcription/native audio disabled]"}]

        if attachment.modality == "video":
            return [{"type": "text", "text": f"{metadata_text}\n[Video attached as metadata; transcript/keyframe extraction not enabled in this processor path]"}]

        return [{"type": "text", "text": metadata_text}]

    def _attachment_metadata_text(self, attachment: InputAttachment) -> str:
        return (
            "[User attachment]\n"
            f"name: {attachment.name}\n"
            f"path: {attachment.path}\n"
            f"modality: {attachment.modality}\n"
            f"mime_type: {attachment.mime_type}\n"
            f"size_bytes: {attachment.size_bytes}"
        )

    def _file_to_data_uri(self, path: str, mime_type: str) -> str:
        with open(path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _read_text_attachment(self, attachment: InputAttachment, max_chars: int) -> str:
        header = self._attachment_metadata_text(attachment)
        try:
            with open(attachment.path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(max_chars + 1)
            truncated = len(text) > max_chars
            text = text[:max_chars]
            tail = "\n[TRUNCATED]" if truncated else ""
            return f"{header}\n[Text content start]\n{text}\n[Text content end]{tail}"
        except Exception as exc:
            return f"{header}\n[Text extraction failed] {exc}"

    def _read_pdf_attachment(self, attachment: InputAttachment) -> str:
        header = self._attachment_metadata_text(attachment)
        try:
            import pdfplumber  # type: ignore
        except Exception as exc:
            return f"{header}\n[PDF text extraction unavailable: pdfplumber import failed] {exc}"

        chunks: List[str] = []
        remaining = self.config.max_pdf_chars
        try:
            with pdfplumber.open(attachment.path) as pdf:
                for idx, page in enumerate(pdf.pages, start=1):
                    if remaining <= 0:
                        break
                    page_text = page.extract_text() or ""
                    if not page_text:
                        continue
                    piece = f"\n--- PDF page {idx} ---\n{page_text}"
                    chunks.append(piece[:remaining])
                    remaining -= len(piece)
            body = "".join(chunks).strip()
            if not body:
                body = "[No extractable PDF text found]"
            truncated = remaining <= 0
            tail = "\n[TRUNCATED]" if truncated else ""
            return f"{header}\n[PDF text start]\n{body}\n[PDF text end]{tail}"
        except Exception as exc:
            return f"{header}\n[PDF extraction failed] {exc}"

    def _build_history_text(
        self,
        clean_text: str,
        attachments: List[InputAttachment],
        errors: List[str],
    ) -> str:
        parts: List[str] = []
        if clean_text.strip():
            parts.append(clean_text.strip())
        if attachments:
            parts.append("[attachments]")
            for att in attachments:
                status = "error" if att.error else "ok"
                parts.append(
                    f"- {att.name} | modality={att.modality} | mime={att.mime_type} | "
                    f"size={att.size_bytes} | path={att.path} | status={status}"
                )
        if errors:
            parts.append("[attachment_errors]")
            parts.extend(f"- {err}" for err in errors)
        return "\n".join(parts).strip()


def append_text_block(prompt: Any, text: str) -> Any:
    """Append text to either a string prompt or a multimodal content-block list."""
    if isinstance(prompt, list):
        return [*prompt, {"type": "text", "text": text}]
    return f"{prompt}\n{text}"


def combine_prompt_with_user_content(prompt: str, user_content: Optional[List[Dict[str, Any]]]) -> Any:
    """Combine workflow/system prompt text with pending user multimodal content.

    The workflow prompt remains the first text block; pending user content follows
    so models can inspect attached content while still receiving the action schema
    and workflow rules.
    """
    if not user_content:
        return prompt
    return [{"type": "text", "text": prompt}, *user_content]
