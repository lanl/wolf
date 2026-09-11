# Docker Universe backend

The `docker` Universe deployment backend starts a Universe runtime in a Docker container through the Docker CLI.

## Runtime contract

The configured image must be able to run:

```bash
python -m framework.universes.run_universe \
  --params-file /wolf/runtime/params.json \
  --status-file /wolf/runtime/status.json \
  --host 0.0.0.0 \
  --port 8000
```

For each launch, the backend creates a unique host work directory, writes `params.json` there, and mounts only that directory at `/wolf/runtime`. The container writes `status.json` in the same directory. Docker publishes the container port, and the backend discovers the host endpoint with `docker port`.

## Common configuration

- `backend="docker"`
- `image` or `WOLF_UNIVERSE_DOCKER_IMAGE`
- `docker_bin` or `WOLF_DOCKER_BIN`
- `runtime_dir`
- `container_port` / `port`
- `host_port` / `publish_port` (`0` means Docker chooses a free host port)
- `publish_host` / `host` (defaults to `127.0.0.1`)
- resource controls: `cpus`, `memory`, `pids_limit`
- confinement controls: `read_only`, `user`, `userns`, `network`
- additional `env` and `mounts`

## Testing

Unit tests mock all Docker subprocess calls. The real Docker integration skeleton is opt-in:

```bash
WOLF_RUN_DOCKER_INTEGRATION=1 \
WOLF_UNIVERSE_DOCKER_IMAGE=wolf-universe:latest \
uv run pytest tests/test_universe_deployment_backends.py -q -k docker_backend_real_launch_opt_in_integration
```
