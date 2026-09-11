from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional
import copy
import os
import re

from framework.agentic.agents import OpenAIAgent
from framework.orchestration.default_agents import EchoAgent


@dataclass(slots=True)
class AgentProfile:
    """Resource/capability profile for orchestration workers.

    Profiles describe interchangeable compute resources. They are not fixed
    semantic roles; a task objective still supplies the role at scheduling time.
    """

    profile_id: str
    display_name: Optional[str] = None
    agent_kind: str = "echo"  # echo | openai
    model: Optional[str] = None
    host_address: Optional[str] = None
    host_port: Optional[int] = None
    api_key: Optional[str] = None
    api_key_var: Optional[str] = None
    api_version: Optional[str] = None
    sys_prompt: Optional[str] = None
    capabilities: List[str] = field(default_factory=lambda: ["structured_output"])
    ctx_window_length: Optional[int] = None
    reasoning_level: Optional[str] = None
    model_size_class: Optional[str] = None
    cost_weight: Optional[float] = None
    latency_weight: Optional[float] = None
    max_instances: Optional[int] = None
    default_action_policy: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, fallback_id: Optional[str] = None) -> "AgentProfile":
        data = dict(data or {})
        profile_id = str(data.pop("profile_id", None) or data.pop("id", None) or data.pop("name", None) or fallback_id or "default")
        caps = data.get("capabilities")
        if isinstance(caps, str):
            data["capabilities"] = [x.strip() for x in caps.split(",") if x.strip()]
        elif caps is None:
            data["capabilities"] = ["structured_output"]
        elif not isinstance(caps, list):
            data["capabilities"] = [str(caps)] if str(caps or "").strip() else ["structured_output"]
        else:
            data["capabilities"] = [str(x).strip() for x in caps if str(x).strip()]
        data["agent_kind"] = _safe_agent_kind(data.get("agent_kind"))
        for key in ["host_port", "ctx_window_length", "max_instances"]:
            if key in data:
                data[key] = _optional_positive_int(data.get(key))
        for key in ["cost_weight", "latency_weight"]:
            if key in data:
                data[key] = _optional_float(data.get(key))
        metadata = data.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            data["metadata"] = {"value": str(metadata)}
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in data.items() if k in allowed and k != "profile_id"}
        return cls(profile_id=_safe_profile_id(profile_id), **kwargs)

    def to_dict(self, *, redact: bool = True) -> Dict[str, Any]:
        out = asdict(self)
        if redact and out.get("api_key"):
            out["api_key"] = "***redacted***"
        return out


def _safe_profile_id(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "profile").strip()).strip("-._")
    return text or "profile"


def _safe_agent_kind(value: Any) -> str:
    kind = str(value or "echo").strip().lower()
    return kind if kind in {"echo", "openai"} else "echo"


def _optional_positive_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        n = int(value)
    except Exception:
        return None
    return n if n > 0 else None


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _dedupe_profile_ids(profiles: List[AgentProfile]) -> List[AgentProfile]:
    """Ensure profile IDs are unique after user/UI normalization.

    Duplicate profile IDs make mix reconciliation ambiguous.  Preserve the first
    requested ID and suffix later duplicates deterministically so snapshots and
    PATCH responses are stable and user-correctable.
    """
    seen: Dict[str, int] = {}
    for profile in profiles:
        base = _safe_profile_id(profile.profile_id)
        count = seen.get(base, 0) + 1
        seen[base] = count
        profile.profile_id = base if count == 1 else f"{base}-{count}"
    return profiles


def _profile_force_echo(profile: AgentProfile) -> bool:
    metadata = profile.metadata if isinstance(profile.metadata, dict) else {}
    return bool(metadata.get("force_echo") or metadata.get("demo_echo") or metadata.get("force_agent_kind") == "echo")


def _promote_model_backed_profiles(profiles: List[AgentProfile], base_config: Optional[Dict[str, Any]] = None) -> List[AgentProfile]:
    base = dict(base_config or {})
    for profile in profiles:
        kind = str(profile.agent_kind or "echo").strip().lower()
        model = profile.model or base.get("model")
        if kind == "echo" and model and not _profile_force_echo(profile):
            profile.agent_kind = "openai"
            if not profile.model:
                profile.model = model
    return profiles


def default_agent_profiles(base_config: Optional[Dict[str, Any]] = None) -> List[AgentProfile]:
    base = dict(base_config or {})
    return [
        AgentProfile(
            profile_id="fast-general",
            display_name="Fast general",
            agent_kind="openai" if base.get("model") else "echo",
            model=base.get("model"),
            host_address=base.get("host_address"),
            host_port=base.get("host_port"),
            api_key_var=base.get("api_key_var"),
            api_version=base.get("api_version"),
            sys_prompt=base.get("sys_prompt") or "You are a helpful WOLF worker.",
            capabilities=list(base.get("capabilities") or ["structured_output", "text"]),
            ctx_window_length=base.get("ctx_window_length"),
            reasoning_level="standard",
            model_size_class="medium",
            max_instances=32,
        )
    ]


def normalize_profiles(value: Any, base_config: Optional[Dict[str, Any]] = None) -> List[AgentProfile]:
    if not value:
        return _promote_model_backed_profiles(_dedupe_profile_ids(default_agent_profiles(base_config)), base_config)
    if isinstance(value, dict):
        raw = []
        for key, entry in value.items():
            if isinstance(entry, dict):
                raw.append(AgentProfile.from_dict(entry, fallback_id=str(key)))
    elif isinstance(value, list):
        raw = [AgentProfile.from_dict(x, fallback_id=f"profile-{i+1}") for i, x in enumerate(value) if isinstance(x, dict)]
    else:
        raw = []
    return _promote_model_backed_profiles(_dedupe_profile_ids(raw or default_agent_profiles(base_config)), base_config)


def normalize_mix(value: Any, profiles: List[AgentProfile], fallback_count: int = 1) -> Dict[str, int]:
    ids = [p.profile_id for p in profiles]
    mix: Dict[str, int] = {}
    if isinstance(value, dict):
        for key, raw in value.items():
            try:
                n = max(0, int(raw or 0))
            except Exception:
                n = 0
            if n:
                mix[_safe_profile_id(str(key))] = n
    if not mix and ids:
        mix[ids[0]] = max(1, int(fallback_count or 1))
    return {pid: max(0, int(mix.get(pid, 0))) for pid in ids if int(mix.get(pid, 0) or 0) > 0}


class AgentFactory:
    """Build orchestration worker agents from profile plus base gateway config."""

    def __init__(self, base_config: Optional[Dict[str, Any]] = None) -> None:
        self.base_config = dict(base_config or {})

    def build(self, profile: AgentProfile, *, instance_index: int, session_id: str) -> Any:
        name = f"{session_id[:8]}-{profile.profile_id}-{instance_index}"
        kind = (profile.agent_kind or "echo").strip().lower()
        if kind == "openai":
            cfg = self._merged_openai_config(profile)
            agent = OpenAIAgent(
                model=cfg.get("model") or "unknown-model",
                host_address=cfg.get("host_address") or "http://localhost",
                host_port=cfg.get("host_port"),
                api_key=cfg.get("api_key"),
                api_version=cfg.get("api_version"),
                sys_prompt=cfg.get("sys_prompt") or "You are a helpful WOLF worker.",
                agent_name=name,
                verbose=int(cfg.get("verbose") or 0),
                capabilities=list(cfg.get("capabilities") or []),
                ctx_window_length=cfg.get("ctx_window_length"),
            )
            agent.profile_id = profile.profile_id
            agent.agent_kind = kind
            agent.api_key_var = cfg.get("api_key_var")
            agent.metadata = {"profile_id": profile.profile_id, "agent_kind": kind}
            return agent
        agent = EchoAgent(name=name, capabilities=list(profile.capabilities or ["structured_output"]))
        # Attach descriptors used by AgentPool.from_agent.
        agent.model = profile.model or self.base_config.get("model") or "echo"
        agent.max_ctx_tokens = profile.ctx_window_length or self.base_config.get("ctx_window_length")
        agent.profile_id = profile.profile_id
        agent.agent_kind = kind
        return agent

    def _merged_openai_config(self, profile: AgentProfile) -> Dict[str, Any]:
        cfg = copy.deepcopy(self.base_config)
        for key in ["model", "host_address", "host_port", "api_key", "api_key_var", "api_version", "sys_prompt", "capabilities", "ctx_window_length"]:
            value = getattr(profile, key, None)
            if value not in (None, "", []):
                cfg[key] = value
        if not cfg.get("api_key") and cfg.get("api_key_var"):
            cfg["api_key"] = os.environ.get(str(cfg.get("api_key_var")), None)
        return cfg
