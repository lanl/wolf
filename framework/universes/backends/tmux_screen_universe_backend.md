# tmux/screen Universe backends

The `screen` and `tmux` Universe deployment backends start a Universe runtime inside a detached terminal multiplexer session. They are useful for local long-running debug sessions where a container or Kubernetes cluster is unnecessary.

## Runtime contract

The backend writes `params.json`, then starts a detached session running:

```bash
python -m framework.universes.run_universe \
  --params-file <params.json> \
  --status-file <status.json> \
  --cors '*'
```

Stdout and stderr are redirected to per-launch log files. The Universe writes endpoint information to the status file; the backend polls that file and records the resulting endpoint.

## Backends

- `backend="screen"` uses GNU screen:
  - launch: `screen -dmS <session> bash -lc <command>`
  - status: `screen -ls`
  - terminate: `screen -S <session> -X quit`

- `backend="tmux"` uses tmux:
  - launch: `tmux new-session -d -s <session> bash -lc <command>`
  - status: `tmux has-session -t <session>`
  - terminate: `tmux kill-session -t <session>`

## Common configuration

- `session_name`
- `runtime_dir`
- `python_bin`
- `screen_bin` / `WOLF_SCREEN_BIN`
- `tmux_bin` / `WOLF_TMUX_BIN`
- `host`
- `port`
- `startup_timeout_s`
- `poll_interval_s`

## Testing

Unit tests mock screen/tmux subprocess calls. A real screen integration skeleton is opt-in:

```bash
WOLF_RUN_SCREEN_INTEGRATION=1 \
uv run pytest tests/test_universe_deployment_backends.py -q -k screen_backend_real_launch_opt_in_integration
```
