# Podman Universe backend

The `podman` Universe deployment backend starts a Universe runtime in a Podman container through the Podman CLI.

## Runtime contract

The configured image must be able to run:

```bash
python -m framework.universes.run_universe \
  --params-file /wolf/runtime/params.json \
  --status-file /wolf/runtime/status.json \
  --host 0.0.0.0 \
  --port 8000
```

For every launch, the backend creates a unique host work directory under `runtime_dir`, writes `params.json` there, and mounts only that work directory at `/wolf/runtime`. The container writes `status.json` in the same mounted directory. This avoids collisions between concurrent launches and makes cleanup safe.

## Common configuration

- `backend="podman"`
- `image` or `WOLF_UNIVERSE_PODMAN_IMAGE`
- `podman_bin` or `WOLF_PODMAN_BIN`
- `runtime_dir`
- `container_port` / `port`
- `host_port` / `publish_port` (`0` means Podman chooses a free host port)
- `publish_host` / `host` (defaults to `127.0.0.1`)
- resource controls: `cpus`, `memory`, `pids_limit`
- additional `env` and `mounts`

## Testing

Unit tests mock all Podman subprocess calls and do not require a daemon. The real Podman integration skeleton is opt-in:

```bash
WOLF_RUN_PODMAN_INTEGRATION=1 \
WOLF_UNIVERSE_PODMAN_IMAGE=wolf-universe:latest \
uv run pytest tests/test_universe_deployment_backends.py -q -k podman_backend_real_launch_opt_in_integration
```
