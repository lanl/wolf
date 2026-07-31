from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from framework.cli.config_loader import build_launch_config, print_launch_config, to_jsonable
from framework.cli.discovery import get_actions, get_workflows
from framework.cli.launchers import launch_api, launch_cli, launch_gateway, launch_gui, launch_tui, launch_join_session
from framework.cli.session_commands import inspect_session, list_sessions


MODES = {"cli", "tui", "gui", "api", "gateway"}


GUI_TOKEN_ENV_KEY = "WOLF_GUI_CONTROL_TOKEN"


def _strip_env_value(value: str) -> str:
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _quote_env_value(value: str) -> str:
    escaped = str(value).replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def _read_env_file(env_file: str | Path) -> Dict[str, str]:
    """Read simple KEY=VALUE pairs from an env file without mutating os.environ."""
    path = Path(env_file).expanduser()
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].strip()
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if not key:
            continue
        values[key] = _strip_env_value(value)
    return values


def _load_env_file(env_file: str | Path, *, override: bool = False) -> Dict[str, str]:
    """Load simple KEY=VALUE pairs into os.environ.

    Shell/inline environment values win by default. CLI flags that explicitly set
    a value can still override os.environ after this helper runs.
    """
    values = _read_env_file(env_file)
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return values


def _upsert_env_file(env_file: str | Path, key: str, value: str) -> Path:
    """Create/update a KEY=VALUE entry while preserving unrelated .env lines."""
    path = Path(env_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    out = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        candidate = stripped[len('export '):].strip() if stripped.startswith('export ') else stripped
        if candidate.startswith(f'{key}='):
            prefix = 'export ' if stripped.startswith('export ') else ''
            out.append(f'{prefix}{key}={_quote_env_value(value)}')
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append('')
        out.append(f'{key}={_quote_env_value(value)}')
    path.write_text('\n'.join(out) + '\n')
    return path


def _mask_secret(value: str, *, keep: int = 4) -> str:
    text = str(value or '')
    if not text:
        return '<unset>'
    if len(text) <= keep * 2:
        return '***REDACTED***'
    return f'{text[:keep]}...{text[-keep:]}'


def _prepare_process_env(args: argparse.Namespace, mode: str) -> None:
    """Load env-file values and handle GUI control-token generation/override."""
    env_file = getattr(args, 'env_file', None) or '.env'
    _load_env_file(env_file, override=False)

    if mode != 'gui':
        return

    gui_token = getattr(args, 'gui_token', None)
    generate = bool(getattr(args, 'generate_gui_token', False))
    dry_run = bool(getattr(args, 'dry_run', False))
    if gui_token and generate:
        raise SystemExit('--gui-token and --generate-gui-token are mutually exclusive')

    if gui_token:
        token = str(gui_token)
        if dry_run:
            print(f'[wolf gui] dry-run: would set {GUI_TOKEN_ENV_KEY} in {Path(env_file).expanduser()} ({_mask_secret(token)})')
            return
        path = _upsert_env_file(env_file, GUI_TOKEN_ENV_KEY, token)
        os.environ[GUI_TOKEN_ENV_KEY] = token
        print(f'[wolf gui] {GUI_TOKEN_ENV_KEY} set from --gui-token and written to {path} ({_mask_secret(token)})')
        return

    if generate:
        token = secrets.token_urlsafe(32)
        if dry_run:
            print(f'[wolf gui] dry-run: would generate {GUI_TOKEN_ENV_KEY} and write it to {Path(env_file).expanduser()} ({_mask_secret(token)})')
            return
        path = _upsert_env_file(env_file, GUI_TOKEN_ENV_KEY, token)
        os.environ[GUI_TOKEN_ENV_KEY] = token
        print(f'[wolf gui] Generated {GUI_TOKEN_ENV_KEY} and written to {path} ({_mask_secret(token)})')
        return

    # No inline token. Use shell env if present; otherwise use .env if present.
    env_values = _read_env_file(env_file)
    file_token = env_values.get(GUI_TOKEN_ENV_KEY)
    if GUI_TOKEN_ENV_KEY not in os.environ and file_token:
        os.environ[GUI_TOKEN_ENV_KEY] = file_token

def _add_common_launch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", help="JSON/YAML launch config file")
    parser.add_argument("--workflow", "-w", help="Workflow class name or WF_TAG to run")
    parser.add_argument("--resume", dest="resume_session", help="Resume session identifier: last/latest/session_.../path/date/partial")
    parser.add_argument("--user", dest="user_name", help="Workflow user name")
    parser.add_argument("--session-dir", help="Explicit session directory for new sessions")
    parser.add_argument("--verbose", "-v", type=int, help="Session verbosity")
    parser.add_argument("--universe", action="append", default=None, help="Universe endpoint as host:port or scheme://host:port; can be repeated")
    parser.add_argument("--env-file", default=".env", help="Env file to load before launch (default: .env). Shell/inline env values take precedence unless an explicit CLI setter is used.")
    parser.add_argument("--dry-run", action="store_true", help="Build/explain launch plan without starting runtime")
    parser.add_argument("--explain", action="store_true", help="Print launch plan before starting runtime")


def _parse_universe(value: str) -> Dict[str, Any]:
    scheme = "http"
    rest = value.strip()
    if "://" in rest:
        scheme, rest = rest.split("://", 1)
    if ":" not in rest:
        raise argparse.ArgumentTypeError(f"Universe must include host:port: {value}")
    host, port_s = rest.rsplit(":", 1)
    try:
        port = int(port_s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Universe port must be an integer: {value}") from exc
    return {"host": host, "port": port, "scheme": scheme}


def _csv_values(value: Optional[str]) -> Optional[list[str]]:
    if value is None:
        return None
    items = [part.strip() for part in str(value).split(",") if part.strip()]
    return items or None


def _gateway_default_agent_config(args: argparse.Namespace) -> Dict[str, Any]:
    """Build gateway default AgentConfig overrides from CLI flags."""
    cfg: Dict[str, Any] = {}
    mapping = {
        "model": "model",
        "host_address": "host_address",
        "host_port": "host_port",
        "api_key_var": "api_key_var",
        "api_version": "api_version",
        "agent_name": "agent_name",
        "verbose": "gateway_verbose",
        "ctx_window_length": "ctx_window_length",
        "mode": "gateway_mode",
        "max_steps": "max_steps",
        "action_policy": "action_policy",
        "gui_url": "gui_url",
        "gui_action_route": "gui_action_route",
        "syscall_max_timeout": "syscall_max_timeout",
    }
    for key, attr in mapping.items():
        value = getattr(args, attr, None)
        if value is not None:
            cfg[key] = value
    for key, attr in {"capabilities": "capabilities", "action_names": "action_names", "syscall_allowed_commands": "syscall_allowed_commands"}.items():
        values = _csv_values(getattr(args, attr, None))
        if values is not None:
            cfg[key] = values
    for key, attr in {"enable_write": "enable_write", "enable_syscall": "enable_syscall", "syscall_allow_shell": "syscall_allow_shell"}.items():
        if getattr(args, attr, False):
            cfg[key] = True
    return cfg


def _launch_overrides(args: argparse.Namespace, mode: str) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {"mode": mode}
    session: Dict[str, Any] = {}
    if getattr(args, "workflow", None):
        overrides["workflow"] = args.workflow
    if getattr(args, "resume_session", None):
        overrides["resume_session"] = args.resume_session
    if getattr(args, "user_name", None):
        overrides["user_name"] = args.user_name
    if getattr(args, "session_dir", None):
        session["session_dir"] = args.session_dir
    if getattr(args, "verbose", None) is not None:
        session["verbose"] = args.verbose
    if getattr(args, "universe", None):
        session["universes"] = [_parse_universe(v) for v in args.universe]
    if session:
        overrides["session"] = session
    gui: Dict[str, Any] = {}
    if getattr(args, "gui_host", None):
        gui["host"] = args.gui_host
    if getattr(args, "gui_port", None) is not None:
        gui["port"] = args.gui_port
    if getattr(args, "gui_open_browser", None) is not None:
        gui["open_browser"] = args.gui_open_browser
    if getattr(args, "gateway_url", None):
        gui["gateway_url"] = args.gateway_url
    if getattr(args, "auto_connect_gateway", None) is not None:
        gui["auto_connect_gateway"] = args.auto_connect_gateway
    if getattr(args, "gui_action_route", None):
        gui["gui_action_route"] = args.gui_action_route
    if gui:
        overrides["gui"] = gui
    return overrides


def command_join_session(args: argparse.Namespace) -> int:
    _prepare_process_env(args, "cli")
    cfg = build_launch_config(getattr(args, "config", None), _launch_overrides(args, "cli"))
    return launch_join_session(
        cfg,
        gateway=args.gateway,
        account_id=args.account_id,
        session_id=args.session_id,
        token=args.token,
        participant_id=args.participant_id,
        dry_run=args.dry_run,
        explain=args.explain,
    )


def command_gateway(args: argparse.Namespace) -> int:
    _prepare_process_env(args, "gateway")
    cfg = build_launch_config(getattr(args, "config", None), _launch_overrides(args, "gateway"))
    default_agent_config = _gateway_default_agent_config(args)
    return launch_gateway(
        cfg,
        host=args.gateway_host,
        port=args.gateway_port,
        static_dir=args.static_dir,
        default_agent_config=default_agent_config or None,
        dry_run=args.dry_run,
        explain=args.explain,
    )


def command_launch(args: argparse.Namespace, mode: str) -> int:
    _prepare_process_env(args, mode)
    cfg = build_launch_config(getattr(args, "config", None), _launch_overrides(args, mode))
    if mode == "cli":
        return launch_cli(cfg, dry_run=args.dry_run, explain=args.explain)
    if mode == "api":
        return launch_api(cfg, host=args.host, port=args.port, dry_run=args.dry_run, explain=args.explain)
    if mode == "tui":
        return launch_tui(cfg, gateway_url=getattr(args, "gateway", "http://127.0.0.1:8000"), session_id=getattr(args, "session_id", None), dry_run=args.dry_run, explain=args.explain)
    if mode == "gui":
        return launch_gui(cfg, dry_run=args.dry_run, explain=args.explain)
    raise ValueError(f"Unsupported mode: {mode}")


def command_workflows_list(args: argparse.Namespace) -> int:
    workflows = get_workflows()
    if args.json:
        print(json.dumps(workflows, indent=2, sort_keys=True))
        return 0
    print("Discovered workflows:")
    for name, info in workflows.items():
        tag = info.get("wf_tag") or ""
        doc = info.get("doc") or ""
        suffix = f" [WF_TAG={tag}]" if tag else ""
        print(f"  - {name}{suffix} :: {info.get('module')}")
        if doc:
            print(f"      {doc}")
    return 0


def command_actions_list(args: argparse.Namespace) -> int:
    rows = get_actions()
    if args.limit:
        rows = rows[: args.limit]
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0
    print("Discovered actions:")
    for row in rows:
        desc = row.get("description") or ""
        print(f"  - {row['action']} :: {row['module']}.{row['class_name']}")
        if desc:
            print(f"      {desc}")
    return 0


def command_sessions_list(args: argparse.Namespace) -> int:
    rows = list_sessions(workspace=args.workspace)
    if args.json:
        print(json.dumps(to_jsonable(rows), indent=2, sort_keys=True))
        return 0
    if not rows:
        print(f"No sessions found under {args.workspace}/session_*/session.snapshot.json")
        return 0
    print("Sessions:")
    for row in rows:
        turn = row.get("workflow_turn") or "?"
        ts = row.get("timestamp") or "?"
        print(f"  - {row['session_id']}  turn={turn}  timestamp={ts}  path={row['session_dir']}")
    return 0


def command_sessions_inspect(args: argparse.Namespace) -> int:
    data = inspect_session(args.identifier, workspace=args.workspace)
    print(json.dumps(to_jsonable(data), indent=2, sort_keys=True))
    return 0


def command_config_print(args: argparse.Namespace) -> int:
    _prepare_process_env(args, args.mode or "cli")
    cfg = build_launch_config(args.config, _launch_overrides(args, args.mode or "cli"))
    print_launch_config(cfg, redact_secrets=not args.show_secrets)
    return 0


def command_config_validate(args: argparse.Namespace) -> int:
    _prepare_process_env(args, args.mode or "cli")
    cfg = build_launch_config(args.config, _launch_overrides(args, args.mode or "cli"))
    errors = []
    warnings = []
    if cfg.get("mode") not in MODES:
        errors.append(f"Unsupported mode: {cfg.get('mode')}")
    try:
        from framework.workflows.workflow_space import get_workflow_class
        get_workflow_class(cfg.get("workflow") or "TurnBasedWorkflow")
    except Exception as exc:
        errors.append(f"Workflow not discoverable: {exc}")
    llms = cfg.get("session", {}).get("LLMs", {})
    if not llms:
        errors.append("No LLMs configured")
    for name, llm in llms.items():
        if not llm.get("model"):
            warnings.append(f"LLM '{name}' has no model set")
        key_var = llm.get("api_key_var")
        if key_var and key_var not in os.environ:
            warnings.append(f"LLM '{name}' api_key_var '{key_var}' is not present in process environment")
    result = {"ok": not errors, "errors": errors, "warnings": warnings, "mode": cfg.get("mode"), "workflow": cfg.get("workflow")}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 1


def command_doctor(args: argparse.Namespace) -> int:
    _prepare_process_env(args, "cli")
    checks = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    try:
        import framework  # noqa: F401
        check("framework import", True)
    except Exception as exc:
        check("framework import", False, str(exc))
    try:
        workflows = get_workflows()
        check("workflow discovery", bool(workflows), f"{len(workflows)} workflows")
    except Exception as exc:
        check("workflow discovery", False, str(exc))
    try:
        actions = get_actions()
        check("action discovery", bool(actions), f"{len(actions)} actions")
    except Exception as exc:
        check("action discovery", False, str(exc))
    try:
        cfg = build_launch_config(args.config, {})
        check("default/config load", True, f"mode={cfg.get('mode')} workflow={cfg.get('workflow')}")
        llms = cfg.get("session", {}).get("LLMs", {})
        check("LLM config present", bool(llms), f"{len(llms)} LLM entries")
    except Exception as exc:
        check("default/config load", False, str(exc))
    try:
        Path("wf_workspace").mkdir(exist_ok=True)
        test_file = Path("wf_workspace") / ".wolf_doctor_write_test"
        test_file.write_text("ok")
        test_file.unlink()
        check("wf_workspace writable", True)
    except Exception as exc:
        check("wf_workspace writable", False, str(exc))

    ok = all(c["ok"] for c in checks)
    if args.json:
        print(json.dumps({"ok": ok, "checks": checks}, indent=2, sort_keys=True))
    else:
        print("Wolf doctor:")
        for c in checks:
            mark = "OK" if c["ok"] else "FAIL"
            detail = f" - {c['detail']}" if c.get("detail") else ""
            print(f"  [{mark}] {c['name']}{detail}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wolf", description="WOLF application launcher")
    parser.add_argument("--version", action="version", version="wolf launcher prototype")
    sub = parser.add_subparsers(dest="command")

    for mode in ["cli", "tui", "gui"]:
        p = sub.add_parser(mode, help=f"Launch {mode.upper()} mode")
        _add_common_launch_args(p)
        if mode == "tui":
            p.add_argument("--gateway", default="http://127.0.0.1:8000", help="Gateway URL for the TUI client")
            p.add_argument("--session-id", help="Gateway session id to select/create")
        if mode == "gui":
            p.add_argument("--gui-host", help="GUI server bind host (default from config or 127.0.0.1)")
            p.add_argument("--gui-port", type=int, help="GUI server port (default from config or 8765)")
            p.add_argument("--gateway-url", help="Default gateway URL shown by the GUI gateway panel")
            p.add_argument("--auto-connect-gateway", action="store_true", default=None, help="Ask the GUI to auto-open/prepare gateway connection state when possible")
            p.add_argument("--gui-action-route", choices=["auto", "direct", "client_event"], help="Preferred route for gateway GUI actions")
            browser = p.add_mutually_exclusive_group()
            browser.add_argument("--open-browser", dest="gui_open_browser", action="store_true", default=None, help="Open browser after starting GUI")
            browser.add_argument("--no-browser", dest="gui_open_browser", action="store_false", help="Do not open browser after starting GUI")
            token_group = p.add_mutually_exclusive_group()
            token_group.add_argument("--gui-token", help="Set WOLF_GUI_CONTROL_TOKEN for this launch and write/update it in the env file")
            token_group.add_argument("--generate-gui-token", action="store_true", help="Generate a secure WOLF_GUI_CONTROL_TOKEN and write/update it in the env file")
        p.set_defaults(func=lambda args, mode=mode: command_launch(args, mode))

    p_gateway = sub.add_parser("gateway", help="Launch WOLF gateway server")
    _add_common_launch_args(p_gateway)
    p_gateway.add_argument("--gateway-host", "--host", default="127.0.0.1", help="Gateway bind host (default: 127.0.0.1)")
    p_gateway.add_argument("--gateway-port", "--port", dest="gateway_port", type=int, default=8000, help="Gateway bind port (default: 8000)")
    p_gateway.add_argument("--static-dir", default="./framework/ui/webapp", help="Static web UI directory for gateway root/static routes")
    p_gateway.add_argument("--model", help="Default gateway agent model")
    p_gateway.add_argument("--host-address", help="Default inference provider base URL")
    p_gateway.add_argument("--host-port", type=int, help="Default inference provider port")
    p_gateway.add_argument("--api-key-var", help="Environment variable containing the provider API key")
    p_gateway.add_argument("--api-version", help="Provider API version/path")
    p_gateway.add_argument("--agent-name", help="Default gateway agent name")
    p_gateway.add_argument("--gateway-verbose", type=int, help="Gateway runtime/session verbosity")
    p_gateway.add_argument("--capabilities", help="Comma-separated default agent capabilities")
    p_gateway.add_argument("--ctx-window-length", type=int, help="Default context window length")
    p_gateway.add_argument("--gateway-mode", choices=["single_step", "wolf_loop"], help="Gateway workflow turn mode")
    p_gateway.add_argument("--max-steps", type=int, help="Max gateway workflow steps per user message")
    p_gateway.add_argument("--action-policy", "--policy", choices=["safe", "limited", "write", "dev", "advanced", "master", "custom"], help="Gateway action policy")
    p_gateway.add_argument("--action-names", help="Comma-separated explicit allowed action names for custom policy")
    p_gateway.add_argument("--enable-write", action="store_true", help="Enable write-capable gateway actions where policy allows")
    p_gateway.add_argument("--enable-syscall", action="store_true", help="Enable syscall action where policy allows")
    p_gateway.add_argument("--syscall-allowed-commands", help="Comma-separated allowed syscall command prefixes")
    p_gateway.add_argument("--syscall-max-timeout", type=int, help="Maximum syscall timeout in seconds")
    p_gateway.add_argument("--syscall-allow-shell", action="store_true", help="Allow shell=True syscalls when syscall policy permits")
    p_gateway.add_argument("--gui-url", help="GUI base URL for direct gateway->GUI action routing")
    p_gateway.add_argument("--gui-action-route", choices=["auto", "direct", "client_event"], help="Gateway GUI action route preference")
    p_gateway.set_defaults(func=command_gateway)

    p_api = sub.add_parser("api", help="Launch API/server mode")
    _add_common_launch_args(p_api)
    p_api.add_argument("--host", default="0.0.0.0")
    p_api.add_argument("--port", type=int, default=8000)
    p_api.set_defaults(func=lambda args: command_launch(args, "api"))

    p_workflows = sub.add_parser("workflows", help="Workflow discovery commands")
    wf_sub = p_workflows.add_subparsers(dest="workflow_command")
    p_wf_list = wf_sub.add_parser("list", help="List discovered workflows")
    p_wf_list.add_argument("--json", action="store_true")
    p_wf_list.set_defaults(func=command_workflows_list)

    p_actions = sub.add_parser("actions", help="Action discovery commands")
    ac_sub = p_actions.add_subparsers(dest="action_command")
    p_ac_list = ac_sub.add_parser("list", help="List discovered workflow actions")
    p_ac_list.add_argument("--json", action="store_true")
    p_ac_list.add_argument("--limit", type=int)
    p_ac_list.set_defaults(func=command_actions_list)

    p_sessions = sub.add_parser("sessions", help="Session management commands")
    sess_sub = p_sessions.add_subparsers(dest="session_command")
    p_sess_list = sess_sub.add_parser("list", help="List saved sessions")
    p_sess_list.add_argument("--workspace", default="wf_workspace")
    p_sess_list.add_argument("--json", action="store_true")
    p_sess_list.set_defaults(func=command_sessions_list)
    p_sess_inspect = sess_sub.add_parser("inspect", help="Inspect one saved session")
    p_sess_inspect.add_argument("identifier")
    p_sess_inspect.add_argument("--workspace", default="wf_workspace")
    p_sess_inspect.set_defaults(func=command_sessions_inspect)

    p_config = sub.add_parser("config", help="Launch config commands")
    cfg_sub = p_config.add_subparsers(dest="config_command")
    p_cfg_print = cfg_sub.add_parser("print", help="Print merged launch config")
    _add_common_launch_args(p_cfg_print)
    p_cfg_print.add_argument("--mode", choices=sorted(MODES), default="cli")
    p_cfg_print.add_argument("--show-secrets", action="store_true")
    p_cfg_print.set_defaults(func=command_config_print)
    p_cfg_validate = cfg_sub.add_parser("validate", help="Validate launch config")
    _add_common_launch_args(p_cfg_validate)
    p_cfg_validate.add_argument("--mode", choices=sorted(MODES), default="cli")
    p_cfg_validate.set_defaults(func=command_config_validate)

    p_join = sub.add_parser("join-session", help="Join an active gateway session as a message-level participant")
    _add_common_launch_args(p_join)
    p_join.add_argument("--gateway", required=True, help="Gateway URL, e.g. http://127.0.0.1:8000")
    p_join.add_argument("--account-id", required=True, help="Gateway account id")
    p_join.add_argument("--session-id", required=True, help="Gateway session id to join")
    p_join.add_argument("--token", required=True, help="Gateway auth token")
    p_join.add_argument("--participant-id", default="wolf_cli_agent", help="Name/id for this joined entity")
    p_join.set_defaults(func=command_join_session)

    p_doctor = sub.add_parser("doctor", help="Run startup/environment diagnostics")
    p_doctor.add_argument("--config", "-c")
    p_doctor.add_argument("--json", action="store_true")
    p_doctor.set_defaults(func=command_doctor)

    # Backward-compatible top-level launch flags: `./wolf --resume last`.
    _add_common_launch_args(parser)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None):
        return int(args.func(args))
    # No subcommand: preserve old behavior and launch CLI mode.
    return command_launch(args, "cli")


if __name__ == "__main__":
    raise SystemExit(main())
