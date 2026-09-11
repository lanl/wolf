# Kubernetes Universe backend

The `kubernetes` Universe deployment backend starts a Universe runtime as Kubernetes resources using the `kubectl` CLI. It is designed to work with local `kind` clusters as well as ordinary Kubernetes contexts.

## Runtime contract

The configured image must be able to run:

```bash
python -m framework.universes.run_universe \
  --params-file /wolf/config/params.json \
  --status-file /wolf/runtime/status.json \
  --host 0.0.0.0 \
  --port 8000
```

The backend creates a manifest containing:

- a `ConfigMap` containing `params.json`
- a `Deployment` running the Universe image
- a `Service` targeting the Universe HTTP port

`params.json` is mounted read-only at `/wolf/config/params.json`; an `emptyDir` is mounted at `/wolf/runtime` for the status file. For local access, the backend starts `kubectl port-forward svc/<service> <local_port>:<container_port>` and records a local `http://127.0.0.1:<local_port>` endpoint.

## Common configuration

- `backend="kubernetes"`
- `image` or `WOLF_UNIVERSE_KUBERNETES_IMAGE`
- `kubectl_bin` or `WOLF_KUBECTL_BIN`
- `context` or `WOLF_K8S_CONTEXT`
- `namespace` or `WOLF_K8S_NAMESPACE`
- `container_port` / `port`
- `local_port` / `host_port` (`0` means choose a free local port)
- `local_host` (defaults to `127.0.0.1`)
- `replicas`
- `image_pull_policy`
- `resources`
- `env`
- `service_type`
- `create_namespace`

## Local kind testing

Normal unit tests mock `kubectl` and do not require a cluster. The real kind/Kubernetes integration skeleton is opt-in:

```bash
WOLF_RUN_K8S_INTEGRATION=1 \
WOLF_K8S_CONTEXT=kind-podman-cluster \
WOLF_UNIVERSE_KUBERNETES_IMAGE=wolf-universe:latest \
uv run pytest tests/test_universe_deployment_backends.py -q -k kubernetes_backend_real_kind_launch_opt_in_integration
```

For kind, make sure the image is available inside the cluster, e.g. via `kind load docker-image` or an accessible registry, depending on your runtime setup.
