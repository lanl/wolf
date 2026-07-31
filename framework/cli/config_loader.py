from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Optional


LAUNCH_KEYS = {"mode", "workflow", "resume_session", "user_name", "run"}


def deep_merge(base: Any, override: Any) -> Any:
    """Recursively merge *override* into *base* and return a new object."""
    if isinstance(base, dict) and isinstance(override, dict):
        merged = copy.deepcopy(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = deep_merge(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged
    return copy.deepcopy(override)


def load_config_file(config_path: str | Path | None) -> Dict[str, Any]:
    """Load a JSON/YAML launch config file. YAML requires optional PyYAML."""
    if not config_path:
        return {}
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    suffix = path.suffix.lower()
    text = path.read_text()
    if suffix in {".json", ".jsonc"}:
        return json.loads(text)
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError("YAML config support requires PyYAML. Use JSON or install pyyaml.") from exc
        data = yaml.safe_load(text)
        return data or {}
    raise ValueError(f"Unsupported config file extension '{suffix}'. Use .json, .yaml, or .yml")


def default_launch_config() -> Dict[str, Any]:
    """Build the default launch config from repository defaults."""
    from config.defaults.inference_engine import LLM
    from config.session.default.params.inputs import session_params

    return {
        "mode": "cli",
        "workflow": "TurnBasedWorkflow",
        "resume_session": None,
        "user_name": "user",
        "session": copy.deepcopy(session_params),
        "llms": copy.deepcopy(LLM),
        "run": {},
    }


def normalize_user_config(user_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize accepted config aliases into the launch-config shape."""
    cfg = copy.deepcopy(user_cfg or {})
    if "LLMs" in cfg and "llms" not in cfg:
        cfg["llms"] = cfg.pop("LLMs")
    if "session_params" in cfg and "session" not in cfg:
        cfg["session"] = cfg.pop("session_params")
    return cfg


def build_launch_config(config_path: str | Path | None = None, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build final launch config: defaults < file < CLI overrides."""
    cfg = default_launch_config()
    file_cfg = normalize_user_config(load_config_file(config_path))
    cfg = deep_merge(cfg, file_cfg)
    if overrides:
        cfg = deep_merge(cfg, normalize_user_config(overrides))

    session = copy.deepcopy(cfg.get("session", {}))
    session["LLMs"] = copy.deepcopy(cfg.get("llms", {}))
    cfg["session"] = session
    return cfg


def to_jsonable(value: Any, redact_secrets: bool = True) -> Any:
    """Convert runtime config objects to JSON-safe values for printing."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if redact_secrets and str(k).lower() in {"api_key", "token", "secret", "password"} and v:
                out[k] = "***REDACTED***"
            else:
                out[k] = to_jsonable(v, redact_secrets=redact_secrets)
        return out
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v, redact_secrets=redact_secrets) for v in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump(), redact_secrets=redact_secrets)
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def print_launch_config(config: Dict[str, Any], redact_secrets: bool = True) -> None:
    print(json.dumps(to_jsonable(config, redact_secrets=redact_secrets), indent=2, sort_keys=True))
