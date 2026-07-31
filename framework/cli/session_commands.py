from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Dict, List, Optional


def session_snapshot_paths(workspace: str = "wf_workspace") -> List[Path]:
    return [Path(p) for p in sorted(glob.glob(f"{workspace}/session_*/session.snapshot.json"), reverse=True)]


def list_sessions(workspace: str = "wf_workspace") -> List[Dict[str, object]]:
    rows = []
    for snap in session_snapshot_paths(workspace):
        stat = snap.stat()
        session_dir = snap.parent
        row: Dict[str, object] = {
            "session_id": session_dir.name,
            "session_dir": str(session_dir),
            "snapshot": str(snap),
            "modified": stat.st_mtime,
            "size_bytes": stat.st_size,
        }
        try:
            data = json.loads(snap.read_text())
            row["workflow_user"] = data.get("WORKFLOW_USER")
            row["workflow_turn"] = data.get("WORKFLOW_TURN")
            row["timestamp"] = data.get("timestamp")
        except Exception as exc:
            row["snapshot_error"] = str(exc)
        rows.append(row)
    return rows


def resolve_session_snapshot(identifier: str, workspace: str = "wf_workspace") -> Optional[Path]:
    if not identifier:
        return None
    ident = identifier.strip()
    lower = ident.lower()
    snapshots = session_snapshot_paths(workspace)
    if lower in {"latest", "last", "recent", "most_recent", "newest"}:
        return snapshots[0] if snapshots else None
    p = Path(ident)
    if p.exists():
        if p.is_dir():
            candidate = p / "session.snapshot.json"
            return candidate if candidate.exists() else None
        return p
    if ident.startswith("session_"):
        candidate = Path(workspace) / ident / "session.snapshot.json"
        return candidate if candidate.exists() else None
    matches = [s for s in snapshots if ident in str(s)]
    return matches[0] if matches else None


def inspect_session(identifier: str, workspace: str = "wf_workspace") -> Dict[str, object]:
    snap = resolve_session_snapshot(identifier, workspace)
    if snap is None:
        raise FileNotFoundError(f"No session snapshot found for: {identifier}")
    data = json.loads(snap.read_text())
    infra = data.get("infrastructure", {}) or {}
    return {
        "session_id": snap.parent.name,
        "session_dir": str(snap.parent),
        "snapshot": str(snap),
        "timestamp": data.get("timestamp"),
        "workflow_user": data.get("WORKFLOW_USER"),
        "workflow_turn": data.get("WORKFLOW_TURN"),
        "wf_rules_file": data.get("wf_rules_file"),
        "wf_agent_behaviour_file": data.get("wf_agent_behaviour_file"),
        "wf_agent_sys_prompt_file": data.get("wf_agent_sys_prompt_file"),
        "history_entries": len(infra.get("FULL_CTX", []) or []),
        "chat_history_entries": len(infra.get("chat_history", []) or []),
        "roles": infra.get("ROLEs", {}),
    }
