#!/usr/bin/env bash
# run_dash.sh — launch the View Files FastAPI dashboard.
#
# USER GUIDE FOR AGENTS
# =====================
#
# Purpose:
#   Start this dashboard webapp with uvicorn using one of three runner modes:
#     1. uv       -> uv run uvicorn ...        default, best for this repo
#     2. python   -> python -m uvicorn ...
#     3. uvicorn  -> uvicorn ...              only if uvicorn is on PATH
#
# Basic usage from this directory:
#   ./run_dash.sh --host 127.0.0.1 --port 8012
#
# Basic usage from anywhere:
#   /path/to/FRAMEs/dashboards/view_files/run_dash.sh --host 127.0.0.1 --port 8012
#
# With reload:
#   ./run_dash.sh --host 127.0.0.1 --port 8012 --reload
#
# Use python runner instead of uv:
#   ./run_dash.sh --runner python --host 127.0.0.1 --port 8012
#
# Use bare uvicorn if available globally:
#   ./run_dash.sh --runner uvicorn --host 127.0.0.1 --port 8012
#
# Defaults:
#   --host 127.0.0.1
#   --port 8000
#   --runner uv
#   --module main:app
#
# This script auto-detects the dashboard app directory from its own location and
# auto-detects the repository root by walking upward until it finds pyproject.toml.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
HOST="127.0.0.1"
PORT="8000"
RUNNER="uv"
MODULE="main:app"
RELOAD="false"
WORKERS=""
UV_BIN="uv"
PYTHON_BIN="python"
UVICORN_BIN="uvicorn"
REPO_ROOT=""
EXTRA_ARGS=()

usage() {
  sed -n '1,45p' "$0" | sed 's/^# \{0,1\}//'
}

find_repo_root() {
  local d="$APP_DIR"
  while [[ "$d" != "/" ]]; do
    if [[ -f "$d/pyproject.toml" ]]; then
      printf '%s\n' "$d"
      return 0
    fi
    d="$(dirname "$d")"
  done
  pwd
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:?--host requires a value}"; shift 2 ;;
    --port) PORT="${2:?--port requires a value}"; shift 2 ;;
    --runner) RUNNER="${2:?--runner requires uv, python, or uvicorn}"; shift 2 ;;
    --module) MODULE="${2:?--module requires a value}"; shift 2 ;;
    --reload) RELOAD="true"; shift ;;
    --workers) WORKERS="${2:?--workers requires a value}"; shift 2 ;;
    --uv) UV_BIN="${2:?--uv requires a value}"; shift 2 ;;
    --python) PYTHON_BIN="${2:?--python requires a value}"; shift 2 ;;
    --uvicorn) UVICORN_BIN="${2:?--uvicorn requires a value}"; shift 2 ;;
    --repo-root) REPO_ROOT="${2:?--repo-root requires a value}"; shift 2 ;;
    --extra-arg) EXTRA_ARGS+=("${2:?--extra-arg requires a value}"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$RELOAD" == "true" && -n "$WORKERS" ]]; then
  echo "ERROR: do not combine --reload and --workers." >&2
  exit 2
fi

if [[ ! -f "$APP_DIR/main.py" ]]; then
  echo "ERROR: expected main.py next to this script, but missing: $APP_DIR/main.py" >&2
  exit 1
fi

if [[ -z "$REPO_ROOT" ]]; then
  REPO_ROOT="$(find_repo_root)"
else
  REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"
fi

COMMON_ARGS=("$MODULE" --app-dir "$APP_DIR" --host "$HOST" --port "$PORT")

if [[ "$RELOAD" == "true" ]]; then
  COMMON_ARGS+=(--reload)
fi

if [[ -n "$WORKERS" ]]; then
  COMMON_ARGS+=(--workers "$WORKERS")
fi

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  COMMON_ARGS+=("${EXTRA_ARGS[@]}")
fi

cd "$REPO_ROOT"

echo "[run_dash] app_dir: $APP_DIR"
echo "[run_dash] repo_root: $REPO_ROOT"
echo "[run_dash] url: http://$HOST:$PORT/"

case "$RUNNER" in
  uv)
    if ! command -v "$UV_BIN" >/dev/null 2>&1; then
      echo "ERROR: uv not found on PATH. Try --runner python or install uv." >&2
      exit 1
    fi
    echo "[run_dash] command: $UV_BIN run uvicorn ${COMMON_ARGS[*]}"
    exec "$UV_BIN" run uvicorn "${COMMON_ARGS[@]}"
    ;;
  python)
    if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
      echo "ERROR: python executable not found: $PYTHON_BIN" >&2
      exit 1
    fi
    echo "[run_dash] command: $PYTHON_BIN -m uvicorn ${COMMON_ARGS[*]}"
    exec "$PYTHON_BIN" -m uvicorn "${COMMON_ARGS[@]}"
    ;;
  uvicorn)
    if ! command -v "$UVICORN_BIN" >/dev/null 2>&1; then
      echo "ERROR: uvicorn not found on PATH. Try --runner uv or --runner python." >&2
      exit 1
    fi
    echo "[run_dash] command: $UVICORN_BIN ${COMMON_ARGS[*]}"
    exec "$UVICORN_BIN" "${COMMON_ARGS[@]}"
    ;;
  *)
    echo "ERROR: --runner must be one of: uv, python, uvicorn" >&2
    exit 2
    ;;
esac