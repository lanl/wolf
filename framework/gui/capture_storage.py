from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_segment(value: str, fallback: str = "unknown") -> str:
    text = _SAFE.sub("_", str(value or "").strip())[:120].strip("._-")
    return text or fallback


class CaptureStorage:
    def __init__(self, root: str | Path = "wf_workspace/captures", ttl_seconds: int = 3600):
        self.root = Path(root)
        self.ttl_seconds = int(ttl_seconds or 3600)
        self.root.mkdir(parents=True, exist_ok=True)

    def session_dir(self, session_id: str = "default") -> Path:
        path = self.root / safe_segment(session_id, "default")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def new_capture_id(self) -> str:
        return f"capture_{uuid.uuid4().hex[:16]}"

    def image_path(self, session_id: str, capture_id: str, fmt: str = "png") -> Path:
        fmt = "jpeg" if str(fmt).lower() in {"jpg", "jpeg"} else "png"
        return self.session_dir(session_id) / f"{safe_segment(capture_id, 'capture')}.{fmt}"

    def metadata_path(self, session_id: str, capture_id: str) -> Path:
        return self.session_dir(session_id) / f"{safe_segment(capture_id, 'capture')}.json"

    def write_metadata(self, session_id: str, capture_id: str, metadata: Dict[str, Any]) -> Path:
        path = self.metadata_path(session_id, capture_id)
        path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def cleanup_expired(self) -> int:
        now = time.time()
        removed = 0
        if not self.root.exists():
            return 0
        for p in self.root.rglob("*"):
            if not p.is_file():
                continue
            try:
                if now - p.stat().st_mtime > self.ttl_seconds:
                    p.unlink()
                    removed += 1
            except Exception:
                pass
        return removed
