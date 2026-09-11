#!/usr/bin/env python3
"""
frame_dashboard.py — WOLF frame dashboard run/deploy utilities.

USER GUIDE FOR AGENTS
=====================

This utility backs the Wolf CLI commands:

  ./wolf frame run [APP_DIR] --host 127.0.0.1 --port 8012
  ./wolf frame deploy SOURCE [--copy DEST] --host 127.0.0.1 --port 8013 [--sname dash2]

Run mode:
  Starts a dashboard inline using uvicorn. It blocks until stopped.

  Examples:
    ./wolf frame run
    ./wolf frame run ./FRAMEs/dashboards/view_files --host 127.0.0.1 --port 8012
    ./wolf frame run --reload --port 8012

Deploy mode:
  Optionally copies a dashboard template, then launches it.

  If --sname is provided:
    Launch in a detached GNU screen session.

  If --sname is omitted:
    Launch inline, blocking the terminal like run mode.

  Examples:
    ./wolf frame deploy ./FRAMEs/dashboards/view_files --host 127.0.0.1 --port 8012

    ./wolf frame deploy ./FRAMEs/dashboards/view_files \
      --copy ./FRAMEs/dashboards/dashboard2 \
      --host 127.0.0.1 --port 8013 --sname dash2

Default launch command:
  uv run uvicorn main:app --app-dir <APP_DIR> --host <HOST> --port <PORT>

Runner modes:
  --runner uv       -> uv run uvicorn ...      default
  --runner python   -> python -m uvicorn ...
  --runner uvicorn  -> uvicorn ...
  --runner custom --cmd '...' with placeholders:
       {app_dir}, {host}, {port}, {module}, {sname}, {log}
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_APP_DIR = "FRAMEs/dashboards/view_files"

IGNORE_DIRS = {
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
}

IGNORE_FILE_SUFFIXES = {".pyc", ".pyo"}


def fail(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def require_executable(name: str, display_name: str | None = None) -> str:
    path = shutil.which(name)
    if not path:
        fail(f"{display_name or name!r} is not installed or is not on PATH.")
    return path


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd().resolve()


def resolve_app_dir(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        fail(f"Dashboard directory does not exist: {path}")
    if not path.is_dir():
        fail(f"Dashboard path is not a directory: {path}")
    if not (path / "main.py").is_file():
        fail(f"Dashboard directory is missing main.py: {path}")
    return path


def copy_ignore(_dir: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        path = Path(name)
        if name in IGNORE_DIRS:
            ignored.add(name)
        elif path.suffix in IGNORE_FILE_SUFFIXES:
            ignored.add(name)
        elif name.startswith("deploy_") and name.endswith(".log"):
            ignored.add(name)
    return ignored


def copy_dashboard(source: Path, destination_value: str, *, force_copy: bool = False) -> Path:
    destination = Path(destination_value).expanduser().resolve()
    if destination == source:
        fail("--copy destination cannot be the same as source.")
    if destination.exists():
        if not force_copy:
            fail(f"Copy destination already exists: {destination}. Use --force-copy to replace it.")
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, ignore=copy_ignore)
    return destination


def session_exists(sname: str) -> bool:
    result = subprocess.run(
        ["screen", "-S", sname, "-Q", "select", "."],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        return True
    ls = subprocess.run(
        ["screen", "-ls"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return f".{sname}" in ls.stdout or f"\t{sname}\t" in ls.stdout


def quit_session(sname: str) -> None:
    subprocess.run(["screen", "-S", sname, "-X", "quit"], check=False)
    for _ in range(20):
        if not session_exists(sname):
            return
        time.sleep(0.1)


def build_uvicorn_args(args: argparse.Namespace, app_dir: Path) -> list[str]:
    if getattr(args, "reload", False) and getattr(args, "workers", None):
        fail("Do not combine --reload and --workers; uvicorn does not support that combination.")

    uvicorn_args = [
        getattr(args, "module", "main:app"),
        "--app-dir",
        str(app_dir),
        "--host",
        getattr(args, "host", "127.0.0.1"),
        "--port",
        str(getattr(args, "port", 8000)),
    ]
    if getattr(args, "reload", False):
        uvicorn_args.append("--reload")
    if getattr(args, "workers", None):
        uvicorn_args.extend(["--workers", str(args.workers)])
    for extra in getattr(args, "extra_arg", []) or []:
        uvicorn_args.append(extra)
    return uvicorn_args


def build_command(args: argparse.Namespace, app_dir: Path, log_file: Path | None = None) -> list[str] | str:
    runner = getattr(args, "runner", "uv")
    uvicorn_args = build_uvicorn_args(args, app_dir)

    if runner == "uv":
        uv_bin = getattr(args, "uv", "uv")
        require_executable(uv_bin, "uv")
        return [uv_bin, "run", "uvicorn", *uvicorn_args]
    if runner == "python":
        return [getattr(args, "python", sys.executable), "-m", "uvicorn", *uvicorn_args]
    if runner == "uvicorn":
        uvicorn_bin = getattr(args, "uvicorn", "uvicorn")
        require_executable(uvicorn_bin, "uvicorn")
        return [uvicorn_bin, *uvicorn_args]
    if runner == "custom":
        cmd = getattr(args, "cmd", None)
        if not cmd:
            fail("--runner custom requires --cmd.")
        return cmd.format(
            app_dir=str(app_dir),
            host=getattr(args, "host", "127.0.0.1"),
            port=getattr(args, "port", 8000),
            module=getattr(args, "module", "main:app"),
            sname=getattr(args, "sname", "") or "",
            log=str(log_file or ""),
        )
    fail(f"Unknown runner: {runner}")


def command_to_shell(command: list[str] | str) -> str:
    if isinstance(command, str):
        return command
    return " ".join(shlex.quote(str(part)) for part in command)


def run_inline(command: list[str] | str, *, cwd: Path) -> int:
    print(f"[wolf frame] cwd: {cwd}")
    print(f"[wolf frame] command: {command_to_shell(command)}")
    if isinstance(command, str):
        return subprocess.run(command, cwd=str(cwd), shell=True, check=False).returncode
    return subprocess.run(command, cwd=str(cwd), check=False).returncode


def start_screen_session(sname: str, command: list[str] | str, *, cwd: Path, log_file: Path, replace: bool = False) -> None:
    require_executable("screen", "GNU screen")
    if session_exists(sname):
        if replace:
            quit_session(sname)
        else:
            fail(f"A screen session named {sname!r} already exists. Use --replace to quit it first.")

    log_file.parent.mkdir(parents=True, exist_ok=True)
    command_shell = command_to_shell(command)
    shell_script = " && ".join(
        [
            f"cd {shlex.quote(str(cwd))}",
            f"echo '[wolf frame deploy] started at '$(date) >> {shlex.quote(str(log_file))}",
            f"echo '[wolf frame deploy] cwd: {shlex.quote(str(cwd))}' >> {shlex.quote(str(log_file))}",
            f"echo '[wolf frame deploy] command: {command_shell}' >> {shlex.quote(str(log_file))}",
            f"exec {command_shell} >> {shlex.quote(str(log_file))} 2>&1",
        ]
    )
    result = subprocess.run(
        ["screen", "-dmS", sname, "bash", "-lc", shell_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        fail(f"Failed to start screen session.\nstdout: {result.stdout}\nstderr: {result.stderr}")


def add_common_frame_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1", help="Host/interface for uvicorn. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="Port for uvicorn. Default: 8000")
    parser.add_argument("--module", default="main:app", help="ASGI module target for uvicorn. Default: main:app")
    parser.add_argument("--runner", choices=["uv", "python", "uvicorn", "custom"], default="uv", help="How to launch uvicorn. Default: uv")
    parser.add_argument("--cmd", help="Custom shell command template for --runner custom.")
    parser.add_argument("--python", default=sys.executable, help=f"Python executable for --runner python. Default: {sys.executable}")
    parser.add_argument("--uv", default="uv", help="uv executable for --runner uv. Default: uv")
    parser.add_argument("--uvicorn", default="uvicorn", help="uvicorn executable for --runner uvicorn. Default: uvicorn")
    parser.add_argument("--reload", action="store_true", help="Pass --reload to uvicorn.")
    parser.add_argument("--workers", type=int, help="Optional uvicorn worker count. Do not combine with --reload.")
    parser.add_argument("--repo-root", default=None, help="Working directory for the launch command. Default: auto-detected repo root.")
    parser.add_argument("--extra-arg", action="append", default=[], help="Extra uvicorn argument. Can be repeated.")


def add_run_parser(sub: argparse._SubParsersAction[Any]) -> argparse.ArgumentParser:
    p = sub.add_parser("run", help="Run a dashboard inline")
    p.add_argument("app_dir", nargs="?", default=DEFAULT_APP_DIR, help=f"Dashboard app directory. Default: {DEFAULT_APP_DIR}")
    add_common_frame_args(p)
    p.set_defaults(func=command_frame_run)
    return p


def add_deploy_parser(sub: argparse._SubParsersAction[Any]) -> argparse.ArgumentParser:
    p = sub.add_parser("deploy", help="Copy and/or launch a dashboard; screen only when --sname is provided")
    p.add_argument("source", help="Source dashboard directory to run or copy from.")
    p.add_argument("--copy", dest="copy_to", help="Optional destination directory. If set, copy source there and launch the copy.")
    p.add_argument("--force-copy", action="store_true", help="If --copy destination exists, delete it before copying.")
    p.add_argument("--sname", help="Optional GNU screen session name. If omitted, run inline.")
    p.add_argument("--replace", action="store_true", help="If --sname exists, quit it before starting the new one.")
    p.add_argument("--log", help="Log file path for screen mode. Default: <app_dir>/deploy_<sname>.log")
    add_common_frame_args(p)
    p.set_defaults(func=command_frame_deploy)
    return p


def add_frame_parser(sub: argparse._SubParsersAction[Any]) -> argparse.ArgumentParser:
    p_frame = sub.add_parser("frame", aliases=["frames"], help="Run/deploy FRAME dashboard webapps")
    frame_sub = p_frame.add_subparsers(dest="frame_command")
    add_run_parser(frame_sub)
    add_deploy_parser(frame_sub)
    p_frame.set_defaults(func=command_frame_help, parser=p_frame)
    return p_frame


def command_frame_help(args: argparse.Namespace) -> int:
    parser = getattr(args, "parser", None)
    if parser is not None:
        parser.print_help()
    return 0


def command_frame_run(args: argparse.Namespace) -> int:
    app_dir = resolve_app_dir(args.app_dir)
    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else find_repo_root(app_dir)
    command = build_command(args, app_dir)
    print(f"[wolf frame run] app_dir: {app_dir}")
    print(f"[wolf frame run] url: http://{args.host}:{args.port}/")
    return run_inline(command, cwd=repo_root)


def command_frame_deploy(args: argparse.Namespace) -> int:
    source = resolve_app_dir(args.source)
    app_dir = copy_dashboard(source, args.copy_to, force_copy=args.force_copy) if args.copy_to else source
    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else find_repo_root(app_dir)
    log_file = Path(args.log).expanduser().resolve() if args.log else app_dir / f"deploy_{args.sname or 'inline'}.log"
    command = build_command(args, app_dir, log_file)

    print(f"[wolf frame deploy] source:  {source}")
    if args.copy_to:
        print(f"[wolf frame deploy] copy:    {app_dir}")
    print(f"[wolf frame deploy] app_dir: {app_dir}")
    print(f"[wolf frame deploy] url:     http://{args.host}:{args.port}/")

    if args.sname:
        start_screen_session(args.sname, command, cwd=repo_root, log_file=log_file, replace=args.replace)
        time.sleep(0.4)
        running = session_exists(args.sname)
        print("Dashboard deployed successfully in screen." if running else "Screen command issued, but session is not visible yet.")
        print(f"  session: {args.sname}")
        print(f"  log:     {log_file}")
        print(f"  command: {command_to_shell(command)}")
        print(f"Attach: screen -r {shlex.quote(args.sname)}")
        print("Detach: Ctrl-a then d")
        print(f"Stop:   screen -S {shlex.quote(args.sname)} -X quit")
        return 0 if running else 1

    print("[wolf frame deploy] --sname not provided; running inline.")
    return run_inline(command, cwd=repo_root)


def build_standalone_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="frame_dashboard.py", description="Run/deploy WOLF frame dashboards.")
    sub = parser.add_subparsers(dest="command")
    add_run_parser(sub)
    add_deploy_parser(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_standalone_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None):
        return int(args.func(args))
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())