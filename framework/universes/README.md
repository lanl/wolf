# Universes Module (ActionBoxes)

## Overview

The `framework.universes` package implements WOLF **Universes**: sandboxable, deployable execution environments also referred to as **ActionBoxes**. A Universe hosts one or more KnowledgeBases (KBs) and ToolBoxes (TBs), exposes them through a uniform Python API and FastAPI HTTP API, and can be launched locally, over SSH, in containers, in Kubernetes, in terminal multiplexers, on Ray/Slurm, or through prototype sandbox/VM backends.

A Universe is intended to be the boundary around resources an agent may use for a task: knowledge stores, tools, execution state, and deployment/runtime metadata. The local WOLF system interacts with Universes through registered endpoints and agent actions.

## Core files

- `base_universe.py` — `BaseUniverse`, KB/TB proxy methods, FastAPI app factory, default app builder, and runtime launcher.
- `data_models.py` — `BaseUniverseModel` and `BaseUniverseParams` configuration models.
- `run_universe.py` — CLI entry point used by deployment backends.
- `universe_tools.py` — helpers for endpoint discovery and converting `/info` responses back into `BaseUniverseParams`.
- `endpoint_resolver.py` — endpoint repair/verification helper used by agent actions.
- `status_files.py` — atomic status-file read/write helpers and endpoint extraction.
- `deployment_backends/` — pluggable runtime backends and shared deployment handle/spec/status models.
- `backends/` — backend-specific documentation.

## Data model

### `BaseUniverseModel`

`BaseUniverseModel` describes the externally visible Universe node:

- `name` — Universe name.
- `host` — bind or connect host. Defaults to `127.0.0.1`.
- `port` — port. `0` means auto-assigned when supported.
- `description` — human-readable description.
- `api_version` — optional URL path component appended by `get_base_url()`.
- `api_token` — optional API token metadata.
- `ssh_config` — optional remote deployment settings such as `user`, `key_path`, `remote_python_path`, and `remote_work_dir`.

Important helpers:

- `get_base_url()` returns an HTTP base URL from host/port/api version.
- `is_remote()` identifies non-local hosts.
- `validate_remote_config()` requires SSH config for remote hosts.

### `BaseUniverseParams`

`BaseUniverseParams` contains the resources and metadata needed to instantiate a Universe:

- `kbs` — mapping of KB name to KB object/params.
- `tbs` — mapping of TB name to ToolBox object/params.
- `info` — optional `BaseUniverseModel`.

## `BaseUniverse`

`BaseUniverse` is the in-process registry and proxy for KBs and TBs. It stores:

- `KBs: Dict[str, KnowledgeBase | MultimodalKnowledgeBase]`
- `TBs: Dict[str, ToolBox]`
- `info: BaseUniverseModel | None`
- `name`
- a ChromaDB client for Universe-level vector storage support

### Discovery and registry operations

The Universe exposes discovery helpers:

- `allowed_actions()` — names supported Universe actions.
- `get_info()` — node info, KB names, TB names, allowed actions.
- `get_stats()` — aggregate KB/TB statistics.
- `list_kbs()`, `add_kb()`, `remove_kb()`, `get_kb()`.
- `list_tbs()`, `add_tb()`, `remove_tb()`, `get_tb()`.

### KnowledgeBase operations

`BaseUniverse` proxies KB operations by KB name:

- `kb_search(name, query, k=5, context_window=1)` / `akb_search(...)`
- `kb_append_texts(name, texts, doc_source="universe")` / `akb_append_texts(...)`
- `kb_upload_dir(name, dir_path, target_ext=None)` / `akb_upload_dir(...)`
- `kb_add_url(name, url)` / `akb_add_url(...)`
- `kb_add_urls(name, urls)` / `akb_add_urls(...)`
- `kb_add_document(name, content, metadata=None, modality="text")` / `akb_add_document(...)` for multimodal KBs
- `kb_stats(name)`
- `kb_sources(name)`
- `kb_purge(name)` / `akb_purge(...)`
- `kb_get_document_by_id(name, document_id)`

### ToolBox operations

`BaseUniverse` proxies ToolBox operations by TB name:

- `tb_search_tools(name, query, k=5)` / async variant where implemented
- `tb_execute(name, tool_name, kwargs=None)`
- `tb_tool_info(name, tool_name)`
- `tb_list_tools(name)`
- `tb_append_docs(name, tool_name, texts, doc_source="universe")`
- `tb_upload_docs(name, tool_name, dir_path, target_ext=None)`
- `tb_search_tool_docs(name, tool_name, query, k=5)`
- `tb_add_tool_from_meta(name, meta)`
- `tb_recursive_upload_tools(name, root_dir, ...)`
- `tb_get_stats(name)`
- `get_available_tools()`
- `get_toolbox_tools(name)`

## HTTP API

`create_app(universe, cors_origins=None)` builds a FastAPI app exposing the Universe. Common endpoints include:

### Discovery

- `GET /health`
- `GET /actions`
- `GET /info`
- `GET /stats`
- `GET /tools`

### KB endpoints

- `GET /kbs`
- `POST /kbs`
- `DELETE /kbs/{name}`
- `POST /kbs/{name}/search`
- `GET /kbs/{name}/stats`
- `GET /kbs/{name}/sources`
- `POST /kbs/{name}/append_texts`
- `POST /kbs/{name}/upload_dir`
- `POST /kbs/{name}/add_url`
- `POST /kbs/{name}/add_urls`
- `POST /kbs/{name}/add_document`
- `POST /kbs/{name}/purge`
- `GET /kbs/{name}/document/{document_id}`

### TB endpoints

- `GET /tbs`
- `POST /tbs`
- `DELETE /tbs/{name}`
- `POST /tbs/{name}/search`
- `GET /tbs/{name}/tools`
- `GET /tbs/{name}/stats`
- `GET /tbs/{name}/tools/{tool}/info`
- `POST /tbs/{name}/tools/{tool}/append_texts`
- `POST /tbs/{name}/tools/{tool}/search_docs`
- `POST /tbs/{name}/add_tool_from_meta`
- `POST /tbs/{name}/recursive_upload_tools`
- `POST /tbs/{name}/execute`

## Running a Universe

### Python

```python
from framework.universes.base_universe import BaseUniverse, create_app, run_app
from framework.universes.data_models import BaseUniverseModel, BaseUniverseParams

params = BaseUniverseParams(
    kbs={},
    tbs={},
    info=BaseUniverseModel(name="example_universe", host="127.0.0.1", port=0),
)
universe = BaseUniverse(params)
app = create_app(universe, cors_origins=["*"])
```

### CLI runtime contract

Deployment backends run the module entry point:

```bash
python -m framework.universes.run_universe \
  --params-file /path/to/params.json \
  --status-file /path/to/status.json \
  --host 127.0.0.1 \
  --port 0 \
  --cors '*'
```

The runtime writes a status file containing endpoint metadata. Backends use this to update `UniverseHandle.endpoint` and the registered Universe model.

## Deployment backends

Backends implement the `UniverseBackend` interface in `deployment_backends/base.py` and return `UniverseHandle` objects from `deployment_backends/models.py`.

Shared concepts:

- `UniverseRuntimeSpec` — name, `BaseUniverseParams`, backend name, and backend-specific configuration.
- `UniverseEndpoint` — scheme, host, port, optional base URL and metadata.
- `UniverseStatus` — lifecycle/status metadata.
- `UniverseHandle` — backend, deployment id, endpoint, status, logs, metadata, and backend-native references.
- Registry helpers: `register_universe_backend()`, `get_universe_backend()`, `list_universe_backends()`.

Implemented backend names include:

- `local_process` — local subprocess default.
- `ssh_process` — remote process via SSH for remote hosts.
- `podman` — Podman container.
- `docker` — Docker container.
- `kubernetes` — Kubernetes resources through `kubectl`.
- `screen` / `tmux` — detached terminal multiplexer sessions.
- `ray` — Ray actor.
- `slurm` — Slurm batch job.
- `sandbox_bubblewrap` / `sandbox_nsjail` — local OS sandbox wrappers.
- `vm_qemu` / `microvm_firecracker` — VM/microVM prototype launchers.

Backend-specific documentation lives in [`backends/`](./backends/):

- [`podman_universe_backend.md`](./backends/podman_universe_backend.md)
- [`docker_universe_backend.md`](./backends/docker_universe_backend.md)
- [`kubernetes_universe_backend.md`](./backends/kubernetes_universe_backend.md)
- [`tmux_screen_universe_backend.md`](./backends/tmux_screen_universe_backend.md)
- [`ray_universe_backend.md`](./backends/ray_universe_backend.md)
- [`slurm_universe_backend.md`](./backends/slurm_universe_backend.md)
- [`sandbox_universe_backend.md`](./backends/sandbox_universe_backend.md)
- [`vm_microvm_universe_backend.md`](./backends/vm_microvm_universe_backend.md)

## Backend selection guide

| Backend | Best fit | External dependencies | Isolation strength | Persistence/lifecycle notes | Networking notes |
| --- | --- | --- | --- | --- | --- |
| `local_process` | Fast local development and tests | Python/uvicorn environment | Low; same host/user boundary | Parent process tracks child PID and status file | Direct localhost TCP; supports port `0` discovery |
| `ssh_process` | Simple remote host execution | SSH, remote Python, shared copied files | Low to medium; depends on remote account isolation | Remote process metadata is tracked locally | Requires reachable host/port or SSH tunnel/site routing |
| `podman` | Local containerized runtime, rootless-friendly hosts | Podman CLI and image | Medium; depends on rootless/rootful configuration, mounts, capabilities, user namespace | Container id and per-launch runtime directory are recorded | Host port publishing; `0` can request runtime-assigned host port |
| `docker` | Common local/server container runtime | Docker CLI/daemon and image | Medium; depends on daemon policy, image, user/capability/mount settings | Container id and per-launch runtime directory are recorded | Host port publishing discovered through `docker port` |
| `kubernetes` | Local kind clusters or managed clusters | `kubectl`, context, image accessible to cluster | Medium to high depending on cluster policy | Deployment/Service/ConfigMap resources plus port-forward process | Local endpoint is normally a `kubectl port-forward` |
| `screen` / `tmux` | Local long-running debug sessions | GNU screen or tmux | Low; convenience orchestration only | Detached session survives some parent-process exits | Direct host TCP; inspect multiplexer session for debugging |
| `ray` | Distributed Python actor deployments | Optional `ray` package and Ray cluster | Low to medium; Ray cluster trust boundary | Detached actor can outlive caller depending on Ray config | Endpoint comes from actor-written status file; cluster routing must allow access |
| `slurm` | HPC batch environments | Slurm CLI and shared filesystem | Depends on cluster account/isolation policy | Slurm job id is recorded; status/logs come from job files and Slurm commands | Compute-node endpoints may need `connect_host`, SSH tunnels, or site routing |
| `sandbox_bubblewrap` / `sandbox_nsjail` | Local sandbox experiments | bubblewrap or nsjail and Linux kernel support | Potentially medium/high, but currently prototype policy | Wrapper process and runtime files are recorded | Network namespaces can make HTTP endpoint unreachable without extra routing |
| `vm_qemu` / `microvm_firecracker` | Research toward stronger VM/microVM boundaries | QEMU/Firecracker and prepared guest images | Potentially high, but current implementation is prototype | Process/config/status files are recorded; no image lifecycle management yet | Requires guest-to-host status and explicit port/vsock/TAP/SSH strategy |

Use the least complex backend that gives the required isolation and lifecycle behavior. For ordinary local development, start with `local_process`, `tmux`, or `podman`. For multi-host infrastructure, prefer `kubernetes`, `ray`, or `slurm` only when their operational model already exists. Treat sandbox and VM/microVM backends as experimental until site-specific policies and image preparation are complete.

## Diagnostics, repair, and lifecycle operations

When a Universe is created with port `0`, the requested endpoint is intentionally incomplete until the runtime binds a real port. The runtime writes `status.json`; deployment backends and interaction actions use that status file to repair stale registry entries.

Recommended debugging flow:

1. Inspect deployment records with the deployment listing action or by checking `infra.managed_deployments` in a debug session.
2. Locate the backend runtime directory and read `status.json`, `stdout.log`, and `stderr.log`.
3. Confirm that the status payload contains a ready/running status and endpoint host/port.
4. If `infra.UNIVs` still has port `0` or a stale host, run a Universe interaction action (`universe_health`, `universe_info`, `universe_stats`, or a KB/TB action). These actions invoke endpoint resolution before making HTTP requests.
5. For non-local backends, verify host reachability separately: container port publishing, Kubernetes port-forward process, SSH tunnel, Slurm compute-node routing, Ray worker routing, or sandbox/VM network setup.
6. If a runtime is gone but metadata remains, terminate/cleanup the deployment record and recreate the Universe.

Common failure modes:

- status file never appears: runtime failed before FastAPI startup; inspect stderr/stdout or backend-native logs;
- status has ready endpoint but HTTP fails: stale port-forward, firewall/routing issue, sandbox network isolation, or process already exited;
- backend binary missing: install the runtime or select another backend; several prototype backends return failed handles with diagnostic metadata instead of raising raw executable errors;
- image not found or import failure inside container/cluster: rebuild/load image and verify `python -m framework.universes.run_universe` works inside the image;
- Slurm job submitted but no endpoint: check shared filesystem visibility, job state, module/setup lines, and compute-node reachability.

## Agent and developer entry points

Future agents working on Universes should start with these files and commands.

### Read first

- `framework/universes/README.md` — conceptual and operational overview.
- `framework/universes/backends/*.md` — backend-specific contracts and configuration.
- `IMPROVEMENTS/better_universe_backend_support.md` — implementation history, remaining work, and open questions.

### Runtime entry points

- `framework.universes.run_universe:main()` — module CLI used by backends.
- `framework.universes.base_universe:run_app()` — builds and serves a Universe FastAPI app.
- `framework.universes.base_universe:create_app()` — HTTP route factory.
- `framework.workflows.agent_actions.deployment_actions:CreateUniverseAction.execute()` — agent-driven launch path.
- `framework.universes.deployment_backends.registry:get_universe_backend()` — backend selection.

### Diagnosis and repair entry points

- `framework.universes.status_files.read_status_file()` — inspect backend status payloads.
- `framework.universes.status_files.endpoint_from_status()` — extract usable endpoint from status.
- `framework.universes.status_files.apply_status_to_infra()` — repair `infra.UNIVs` and deployment metadata from status.
- `framework.universes.endpoint_resolver.resolve_universe_endpoint()` — resolve/repair endpoint before HTTP calls.
- `framework.universes.endpoint_resolver.get_universe_base_url_or_error()` — action-friendly helper returning base URL or error.
- Agent actions: `universe_health`, `universe_info`, `universe_stats`, `universe_list_tools` are useful first probes.
- Deployment actions: `list_deployments` and `terminate_deployment` are the current lifecycle controls.

### Test entry points

Recommended focused test commands:

```bash
uv run pytest tests/test_universe_endpoint_resolver.py -q
uv run pytest tests/test_universe_deployment_backends.py -q
uv run pytest tests/test_universe_endpoint_resolver.py tests/test_universe_deployment_backends.py -q
```

Broader related checks:

```bash
uv run pytest   tests/test_universe_endpoint_resolver.py   tests/test_universe_deployment_backends.py   tests/test_all_action_examples_validate.py   tests/test_action_validation_refactor.py   tests/test_risky_action_permission_gates.py   -q
```

When adding a backend, update all of the following together:

1. Backend implementation under `framework/universes/deployment_backends/`.
2. Registry wiring in `framework/universes/deployment_backends/registry.py`.
3. `create_universe` risk/approval wording if the backend changes risk profile.
4. Mocked tests in `tests/test_universe_deployment_backends.py`.
5. Backend documentation under `framework/universes/backends/`.
6. This README backend list/selection guide.
7. The backend-support workplan if the change affects roadmap or open questions.

## Agent actions

Workflow actions in `framework/workflows/agent_actions/` let agents create, discover, inspect, and interact with Universes.

Deployment actions:

- `create_universe`
- `list_deployments`
- `terminate_deployment`

Base Universe interaction actions:

- find known Universes
- inspect `/info`
- check `/health`
- read `/stats`
- list available tools

KB interaction actions cover KB creation, search, append, URL ingestion, multimodal document addition, stats, sources, purge, and document lookup.

TB interaction actions cover ToolBox creation, tool search, tool execution, tool info, tool listing, documentation search/append, and stats.

## Endpoint and status-file behavior

Deployment backends typically write `params.json`, launch `python -m framework.universes.run_universe`, then poll `status.json`. Utilities in `status_files.py` support:

- atomic status writes;
- defensive status reads;
- endpoint extraction from status payloads;
- applying discovered status back to registered infra models.

`endpoint_resolver.py` can repair stale endpoint metadata using deployment status files and optionally verify HTTP reachability.

## Testing notes

Most deployment backend tests mock external runtimes (`docker`, `podman`, `kubectl`, `screen`, `tmux`, `ray`, `sbatch`, `qemu`, `firecracker`, `bwrap`, `nsjail`) so unit tests can run without those systems installed. Real integrations are opt-in through environment variables documented in backend-specific docs.

## Security notes

Universe deployment expands the agent execution boundary. Treat each backend according to its isolation strength:

- local process, tmux/screen, Ray, and Slurm are orchestration mechanisms, not strong security sandboxes;
- Docker/Podman/Kubernetes isolation depends on runtime configuration, images, mounts, users, capabilities, network policy, and cluster policy;
- bubblewrap/nsjail and VM/microVM backends are prototypes and require careful host-specific policy before being considered strong isolation;
- secrets, writable mounts, host networking, and privileged containers should be avoided unless explicitly required.
