from __future__ import annotations

import base64
from typing import Any


def coerce_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    return value


def coerce_int(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit() or (stripped.startswith("-") and stripped[1:].isdigit()):
            return int(stripped)
    return value


def coerce_float(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        try:
            return float(stripped)
        except ValueError:
            return value
    return value


def decode_base64_text(value: str) -> str:
    return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")


def resolve_text_source(
    *,
    text: str | None = None,
    lines: list[str] | None = None,
    base64_text: str | None = None,
    field_label: str = "text",
    required: bool = True,
) -> str | None:
    provided = [text is not None, lines is not None, base64_text is not None]
    count = sum(provided)
    if required and count != 1:
        raise ValueError(f"{field_label} requires exactly one of direct text, lines, or base64 text")
    if not required and count > 1:
        raise ValueError(f"{field_label} accepts at most one of direct text, lines, or base64 text")
    if count == 0:
        return None
    if text is not None:
        return text
    if lines is not None:
        return "\n".join(lines)
    if base64_text is not None:
        try:
            return decode_base64_text(base64_text)
        except Exception as exc:
            raise ValueError(f"{field_label}_base64 must be valid base64-encoded UTF-8 text") from exc
    return None


def resolve_text_list_source(
    *,
    texts: list[str] | None = None,
    text_lines: list[list[str]] | None = None,
    texts_base64: list[str] | None = None,
    field_label: str = "texts",
    required: bool = True,
) -> list[str] | None:
    provided = [texts is not None, text_lines is not None, texts_base64 is not None]
    count = sum(provided)
    if required and count != 1:
        raise ValueError(f"{field_label} requires exactly one of direct strings, line batches, or base64 strings")
    if not required and count > 1:
        raise ValueError(f"{field_label} accepts at most one of direct strings, line batches, or base64 strings")
    if count == 0:
        return None
    if texts is not None:
        return texts
    if text_lines is not None:
        return ["\n".join(lines) for lines in text_lines]
    if texts_base64 is not None:
        try:
            return [decode_base64_text(item) for item in texts_base64]
        except Exception as exc:
            raise ValueError(f"{field_label}_base64 entries must be valid base64-encoded UTF-8 text") from exc
    return None


def normalize_text_payload_dict(
    data: dict[str, Any],
    *,
    target: str,
    lines_key: str | None = None,
    base64_key: str | None = None,
    required: bool = False,
) -> dict[str, Any]:
    lines_key = lines_key or f"{target}_lines"
    base64_key = base64_key or f"{target}_base64"
    resolved = resolve_text_source(
        text=data.get(target),
        lines=data.get(lines_key),
        base64_text=data.get(base64_key),
        field_label=target,
        required=required,
    )
    if resolved is not None:
        data[target] = resolved
    data.pop(lines_key, None)
    data.pop(base64_key, None)
    return data
