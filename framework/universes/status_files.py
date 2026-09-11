from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

READY_STATES = {"ready", "running"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_status_atomic(path: str | Path, data: Dict[str, Any]) -> Path:
    """Atomically write a Universe status JSON file.

    The child Universe process uses this as the explicit parent-readable
    handshake.  Atomic replacement prevents the parent from observing partial
    JSON while polling.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data or {})
    payload.setdefault("schema_version", 1)
    payload["updated_at"] = utc_now_iso()

    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except Exception:
            pass
    return target


def read_status_file(path: str | Path | None) -> Dict[str, Any] | None:
    if not path:
        return None
    target = Path(path)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def endpoint_from_status(status: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not isinstance(status, dict):
        return None
    state = str(status.get("status") or "").strip().lower()
    if state not in READY_STATES:
        return None
    try:
        port = int(status.get("port"))
    except Exception:
        return None
    if port <= 0:
        return None
    host = str(status.get("host") or "127.0.0.1").strip() or "127.0.0.1"
    scheme = str(status.get("scheme") or "http").strip() or "http"
    url = status.get("url") or f"{scheme}://{host}:{port}"
    return {"scheme": scheme, "host": host, "port": port, "url": str(url)}


def apply_status_to_infra(infra: Any, name: str, status: Dict[str, Any] | None) -> bool:
    """Repair infra.UNIVs and managed deployment metadata from status JSON."""
    endpoint = endpoint_from_status(status)
    if not endpoint:
        return False
    try:
        univ = getattr(infra, "UNIVs", {}).get(name)
        if univ is None or getattr(univ, "info", None) is None:
            return False
        univ.info.host = endpoint["host"]
        univ.info.port = endpoint["port"]
        deployments = getattr(infra, "managed_deployments", {}) or {}
        entry = deployments.get(name)
        if isinstance(entry, dict):
            meta = entry.setdefault("meta_data", {})
            meta.update({
                "status": "ready",
                "host": endpoint["host"],
                "port": endpoint["port"],
                "url": endpoint["url"],
                "last_endpoint_repair": utc_now_iso(),
            })
            entry["endpoint"] = endpoint
        return True
    except Exception:
        return False
