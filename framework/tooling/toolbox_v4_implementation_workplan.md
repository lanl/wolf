# Toolbox v4 Implementation Workplan

> **Status:** Planning / implementation scaffold  
> **Target location:** `./framework/tooling/custom/toolboxes/v4/`  
> **Purpose:** Track the design and implementation of a locality-aware, lifecycle-managed, MCP-compatible WOLF Toolbox for Agency Level 2 through Level 4 capabilities.  
> **Crash recovery note:** If work is interrupted, read this file first, then inspect `./framework/tooling/custom/toolboxes/v4/` and continue from the Progress Tracker.

---

## 1. Background and Motivation

The current WOLF tooling implementation under `./framework/tooling` was originally created before MCPs became a standard pattern. Its original purpose was to provide a central utility, the **ToolBox**, through which agents could manage tools: search them, call them, document them, diagnose them, benchmark them, create them, delete them, and modify them.

That original vision maps strongly to **Agency Level 2 — Operation / Tool Management**:

- discover tools,
- understand tool documentation,
- execute tools,
- manage tool metadata,
- add or remove tools,
- search for tools semantically,
- append documentation,
- support local scripts/functions/executables.

However, WOLF is now aiming beyond operation into **Agency Level 4 — Environment Mastery**. At Level 4, locality becomes central. A tool, its inputs, its outputs, the agent, the runtime, and the user may no longer live in the same locality.

Previously, even when universes/actionboxes enabled distributed compute, inputs and outputs were often assumed to live in the same environment as the tool invocation. This assumption no longer holds.

The new Toolbox should therefore become a **locality-aware capability lifecycle manager**, not merely a registry of callable local tools.

---

## 1.1 Tooling Customization Reshuffle

The implementation location was updated after the initial workplan was created. Instead of only supporting custom toolbox versions under `custom_toolboxes/`, the framework now has a broader customization namespace:

```text
framework/tooling/custom/toolboxes/
framework/tooling/custom/tools/
```

This is intentional. Toolbox implementations and Tool implementations may evolve independently. Toolbox v4 should therefore live at:

```text
framework/tooling/custom/toolboxes/v4/
```

Future custom tool runtimes, adapters, wrappers, or models may live under:

```text
framework/tooling/custom/tools/
```

This workplan has been updated to reflect the new layout.

---

## 2. Guiding Thesis

> The WOLF Toolbox should manage the full lifecycle of executable capabilities across local, remote, containerized, and clustered environments, while making locality, readiness, provenance, safety, and interoperability explicit.

The Toolbox should not only answer:

> “What tool can I call?”

It should also answer:

> “What capability is needed?”  
> “Where can this tool safely run?”  
> “Where are the inputs and outputs?”  
> “What runtime or deployment is required?”  
> “Has this tool been tested?”  
> “Is it ready?”  
> “How should data locality be resolved?”  
> “Should compute move to data, data move to compute, or should a volume/object reference be passed?”  
> “Can this tool be exposed through MCP?”  
> “Can this MCP server be imported into WOLF?”  
> “What did we learn from previous executions?”

---

## 3. Relationship to WOLF Philosophy

This work implements part of the WOLF philosophy expressed in `./philo.md`:

> Agency is the recursive reduction of impedance to solution search.

The Toolbox v4 specifically targets these impedance classes:

- **Action-space limitation:** missing tools, wrong tools, missing permissions, missing runtime.
- **Knowledge gap:** lack of documentation, examples, schemas, tool usage wisdom.
- **Policy/method failure:** poor tool choice, wrong deployment choice, inefficient data movement.
- **Evaluation failure:** no smoke tests, no readiness checks, no benchmark evidence.
- **Credit assignment failure:** no tool run trace, no diagnostics, no causality of tool failure.
- **Memory failure:** discovered tool behaviors not preserved.
- **Coordination failure:** unclear relationship between agent, toolbox, universe, runtime, data, and outputs.
- **Resource/budget failure:** compute/data movement/cost constraints not made explicit.
- **Safety/governance failure:** unsafe tool execution or uncontrolled environment mutation.
- **Environment non-stationarity:** tools, APIs, MCP servers, containers, and infrastructure may drift.

The Toolbox v4 should help agents move from Level 2 operation toward Level 4 environment mastery by making tools deployable, testable, routable, observable, and evolvable across environments.

---

## 4. Current Implementation Summary

The existing implementation has three primary files:

```text
framework/tooling/tool_models.py
framework/tooling/tools.py
framework/tooling/toolbox.py
```

### 4.1 Existing `tool_models.py`

Defines Pydantic metadata schemas:

- `FuncArg`
- `FuncMeta`
- `ScriptMeta`
- `ExecutableMeta`
- `ToolMeta`
- a Pydantic `ToolCard`

`ToolMeta` describes functions, scripts, and executables with fields such as:

- `name`
- `args`
- `description`
- `body`
- `purpose`
- `path`
- `dependencies`
- `return_type`
- `tool_type`

### 4.2 Existing `tools.py`

Defines runtime primitives:

- dataclass `ToolCard`
- `ToolDoc`, a thin subclass of `KnowledgeBase`
- `Tool`, which combines a `ToolCard`, a documentation KB, and optional `ToolMeta`

Existing `Tool` can:

- store/search documentation,
- save/load its card,
- execute Python functions in-process,
- execute scripts/binaries through subprocess,
- execute dynamic Python from `ToolMeta.body` using `exec`.

### 4.3 Existing `toolbox.py`

Defines:

- `ToolBoxParams`
- `ToolBox`

Existing `ToolBox` has:

- a semantic tool-card index implemented as a `KnowledgeBase`,
- `tool_id` and `tool_name` registry mappings,
- an in-memory `tools: Dict[str, Tool]` registry.

Existing `ToolBox` supports:

- add/remove/replace tools,
- add tool from `ToolMeta`,
- semantic search over tool cards,
- tool info/listing,
- tool documentation append/search/upload,
- sync/async execution,
- recursive filesystem discovery.

### 4.4 Existing Universe Integration

`framework/universes/base_universe.py` hosts ToolBoxes in `self.TBs` and exposes methods such as:

- `add_tb`, `remove_tb`, `get_tb`, `list_tbs`
- `tb_search_tools`
- `tb_execute`
- `tb_tool_info`
- `tb_list_tools`
- `tb_append_docs`
- `tb_upload_docs`
- `tb_search_tool_docs`
- `tb_add_tool_from_meta`
- `atb_recursive_upload_tools`
- `tb_get_stats`
- `get_available_tools`
- `get_toolbox_tools`

The Universe FastAPI layer exposes endpoints such as:

- `GET /tbs`
- `POST /tbs`
- `POST /tbs/{name}/search`
- `GET /tbs/{name}/tools`
- `GET /tbs/{name}/stats`
- `GET /tbs/{name}/tools/{tool}/info`
- `POST /tbs/{name}/tools/{tool}/append_texts`
- `POST /tbs/{name}/tools/{tool}/search_docs`
- `POST /tbs/{name}/add_tool_from_meta`
- `POST /tbs/{name}/recursive_upload_tools`
- `POST /tbs/{name}/execute`

### 4.5 Existing Agent Action Integration

`framework/workflows/agent_actions/universe_tb_interactions.py` defines agent-facing actions such as:

- `create_toolbox`
- `universe_tb_search_tools`
- `universe_tb_execute`
- `universe_tb_tool_info`
- `universe_tb_list_tools`
- `universe_tb_search_docs`
- `universe_tb_stats`
- `universe_tb_append_docs`

---

## 5. Issues in Current Implementation

The current implementation contains useful ideas but mixes old and new assumptions.

Known issues:

1. **Locality is implicit.**
   - Tool caller, runtime, inputs, outputs, and docs are assumed local or same-environment.

2. **Tool readiness is implicit.**
   - A tool can be added and appear usable before dependencies, deployment, smoke tests, or benchmarks are validated.

3. **Tool lifecycle is under-modeled.**
   - There is no clear status such as proposed, building, deployed, testing, ready, degraded, deprecated, archived.

4. **Registry persistence is partial.**
   - `registry_path` persists mappings but not enough information to reconstruct executable `Tool` objects.

5. **Remove/replace may leave stale semantic index entries.**
   - `remove_tool` updates memory/registry but does not clearly delete the vector-indexed ToolCard document.

6. **There are duplicate ToolCard concepts.**
   - `tool_models.py` defines a Pydantic `ToolCard`.
   - `tools.py` defines a dataclass `ToolCard`.

7. **Dynamic `exec` is unsafe without governance.**
   - Agent-created tool bodies should require sandboxing/policy checks.

8. **Filesystem discovery is shallow.**
   - It maps extensions to tool types but does not deeply inspect signatures, docstrings, argparse, dependencies, schemas, or tests.

9. **MCP is absent.**
   - Existing tooling predates MCP and does not import MCP tools or expose WOLF tools via MCP.

10. **Toolbox is monolithic.**
    - Tool registry, docs, indexing, execution, and discovery are mixed into one class.

---

## 6. Desired Toolbox v4 Definition

> The WOLF Toolbox v4 is a locality-aware capability lifecycle manager. It registers, documents, packages, deploys, validates, exposes, executes, benchmarks, diagnoses, modifies, versions, archives, and evolves tools across local, remote, containerized, and clustered environments, while supporting MCP interoperability and preserving operational wisdom from every tool interaction.

MCP standardizes tool access.

WOLF Toolbox v4 should standardize tool lifecycle, locality, deployment, evaluation, learning, and environment-aware execution.

---

## 7. Target Directory Layout

The tooling customization layout has been reshuffled to support both custom ToolBox implementations and custom Tool implementations.

Toolbox v4 should now be implemented under:

```text
framework/tooling/custom/toolboxes/v4/
```

Custom tool implementations should eventually live under:

```text
framework/tooling/custom/tools/
```

This gives the framework two independent axes of extensibility:

1. **ToolBox implementations** — different orchestration/lifecycle managers for tools.
2. **Tool implementations** — different runtime tool abstractions, adapters, wrappers, or execution models.

Proposed v4 toolbox layout:

```text
framework/tooling/custom/toolboxes/v4/
  __init__.py
  README.md
  tool_models.py
  toolbox.py
  registry.py
  planner.py
  builder.py
  deployer.py
  runtime.py
  diagnostics.py
  benchmarks.py
  docs.py
  security.py
  storage.py
  mcp/
    __init__.py
    import_mcp.py
    export_mcp_server.py
    schema_translation.py
```

Possible future custom tools layout:

```text
framework/tooling/custom/tools/
  v1/
    __init__.py
    tool.py
    tool_models.py
  v4/
    __init__.py
    tool.py
    tool_models.py
    adapters.py
```

Implementation can start smaller, but should preserve this direction.

---

## 8. Core Object Model

Toolbox v4 should separate capability, source, package, deployment, endpoint, execution, locality, and evidence.

### 8.1 Capability

A high-level description of what can be done.

Example:

```text
Capability: transcribe audio
Capability: analyze CSV
Capability: query filesystem
Capability: run benchmark
```

A capability may have multiple tools, versions, packages, deployments, and endpoints.

### 8.2 ToolSpec

Declarative canonical description of a tool.

Suggested fields:

- `id`
- `name`
- `version`
- `description`
- `capabilities`
- `input_schema`
- `output_schema`
- `side_effects`
- `requirements`
- `safety_policy`
- `status`
- `source_refs`
- `package_refs`
- `deployment_refs`
- `endpoint_refs`
- `documentation_refs`
- `benchmark_refs`
- `provenance`
- `created_at`
- `updated_at`

### 8.3 ToolSource

Where the implementation comes from.

Supported source kinds should eventually include:

- Python function
- Python script
- shell script
- binary
- local file
- directory
- Git repository
- notebook
- Dockerfile
- OCI image
- OpenAPI spec
- MCP server
- remote API
- generated source body

### 8.4 ToolPackage

A reproducible build artifact.

Examples:

- OCI image
- Docker image
- Podman image
- wheel
- archive bundle
- source archive
- lockfile
- deployment manifest

### 8.5 ToolDeployment

A running or deployable instance of a tool in a specific locality.

Examples:

- local process
- local venv
- Docker container
- Podman container
- WOLF universe/actionbox
- remote host
- Kubernetes pod/service/job
- MCP server process

### 8.6 ToolEndpoint

Concrete invocation interface.

Supported endpoint protocols:

- Python function
- CLI
- subprocess
- HTTP
- gRPC
- MCP
- filesystem handoff
- queue
- custom universe API

### 8.7 ArtifactRef

Reference to input/output data without assuming locality.

Suggested fields:

- `uri`
- `kind`: file, directory, stream, object, database, secret, model, volume, inline
- `media_type`
- `locality`
- `size_bytes`
- `checksum`
- `permissions`
- `metadata`

Example:

```yaml
uri: universe://gpu_box/data/big.csv
kind: file
media_type: text/csv
locality:
  kind: universe
  id: gpu_box
permissions: read_only
```

### 8.8 LocalityRef

Describes where a thing lives or can run.

Suggested fields:

- `kind`: local, universe, actionbox, container, remote_host, cluster, object_store, volume
- `id`
- `uri`
- `host`
- `path`
- `access_modes`
- `network_reachable_from`
- `constraints`
- `metadata`

### 8.9 ExecutionPolicy

Describes user/agent/system constraints for an execution.

Suggested fields:

- `placement`: compute_near_data, data_near_compute, cheapest, fastest, safest, user_selected, existing_deployment
- `isolation`: none, process, venv, container, sandbox, remote_universe, cluster
- `persistence`: ephemeral, cached, long_running_service
- `output_location`: same_as_input, caller_local, specified_uri, object_store
- `allow_data_movement`
- `allow_mounts`
- `allow_network`
- `max_cost`
- `timeout`
- `required_hardware`
- `risk_tolerance`

### 8.10 ExecutionPlan

Inspectable plan produced before execution.

Suggested fields:

- selected tool/version
- selected package
- selected deployment
- selected endpoint
- input bindings
- output bindings
- mounts/transfers
- secret bindings
- invocation protocol
- readiness status
- estimated cost/time
- risks/warnings
- plan explanation

### 8.11 ToolRun

Execution record.

Suggested fields:

- run id
- tool id/version
- deployment id
- execution plan id
- inputs
- outputs
- logs
- metrics
- start/end timestamps
- status
- error
- diagnostics
- benchmark/evaluation links

### 8.12 ReadinessReport

Evidence that a tool can be used.

Suggested checks:

- dependencies resolved
- package built
- deployment succeeded
- endpoint reachable
- schema valid
- smoke test passed
- sample input/output test passed
- benchmark run complete
- safety policy accepted
- docs available

---

## 9. Tool Lifecycle States

Toolbox v4 should use explicit lifecycle states.

Suggested states:

```text
proposed
registered
inspecting
inspection_failed
building
build_failed
packaged
deploying
deploy_failed
deployed
testing
test_failed
ready
degraded
deprecated
archived
rejected
```

A tool should be marked `ready` only after readiness checks pass.

---

## 10. Key Subsystems

### 10.1 ToolBox Facade

`toolbox.py`

Primary user/agent-facing orchestrator.

Responsibilities:

- expose high-level methods,
- coordinate registries, planner, builder, deployer, runtime, diagnostics,
- maintain compatibility with WOLF Universe hosting,
- provide a clean API for agent actions.

Potential methods:

```python
register_source(...)
inspect_source(...)
build_tool(...)
deploy_tool(...)
test_tool(...)
mark_ready(...)
search_tools(...)
plan_execution(...)
execute_tool(...)
get_run_status(...)
get_artifact(...)
benchmark_tool(...)
diagnose_tool(...)
modify_tool(...)
version_tool(...)
deprecate_tool(...)
archive_tool(...)
import_mcp_server(...)
serve_mcp(...)
```

### 10.2 Registries

`registry.py`

Persistent stores:

- `ToolRegistry`
- `PackageRegistry`
- `DeploymentRegistry`
- `RunRegistry`
- optional `BenchmarkRegistry`

Initial implementation can use JSON files / SQLite.

Important requirement: registry must be rehydratable after restart.

### 10.3 Execution Planner

`planner.py`

Responsible for locality-aware planning.

Inputs:

- tool requirements,
- input artifact localities,
- output target localities,
- available deployments,
- available universes/actionboxes,
- container runtime availability,
- hardware constraints,
- permissions,
- resource/cost constraints,
- safety policy.

Outputs:

- `ExecutionPlan`
- explanation of locality decisions
- warnings if data movement or deployment is required

### 10.4 Builder

`builder.py`

Responsible for converting sources into reproducible packages.

Initial backends:

- no-op/local source backend,
- Python venv backend,
- Docker backend placeholder,
- Podman backend placeholder.

Future backends:

- Kubernetes build job,
- Nix/Conda/uv packager,
- MCP server packager,
- repo-to-image builder.

### 10.5 Deployer

`deployer.py`

Responsible for deploying packages into localities.

Initial backends:

- local process,
- local subprocess,
- WOLF universe/actionbox placeholder,
- Docker/Podman placeholder.

Future backends:

- Kubernetes job/service,
- remote host via SSH,
- cluster scheduler.

### 10.6 Runtime Executor

`runtime.py`

Responsible for invoking endpoints and recording tool runs.

Should support:

- Python callable endpoints,
- CLI endpoints,
- HTTP endpoints,
- MCP endpoints,
- local subprocess,
- remote universe API.

### 10.7 Diagnostics

`diagnostics.py`

Responsible for:

- dependency checks,
- endpoint health checks,
- smoke tests,
- readiness checks,
- failure classification,
- diagnostic reports.

### 10.8 Benchmarks

`benchmarks.py`

Responsible for:

- correctness tests,
- latency metrics,
- resource metrics,
- cost metrics,
- repeated evaluation,
- benchmark history.

### 10.9 Docs

`docs.py`

Responsible for:

- generating searchable tool descriptions,
- indexing docs,
- examples,
- known issues,
- usage notes,
- wisdom artifacts.

Can integrate later with WOLF KB/VStore.

### 10.10 Security and Governance

`security.py`

Responsible for:

- risk levels,
- sandbox policy,
- permission checks,
- network policy,
- filesystem policy,
- secret policy,
- approval gates,
- audit trail support.

### 10.11 Storage and Locality

`storage.py`

Responsible for:

- `ArtifactRef`, `LocalityRef` helpers,
- URI parsing,
- mount planning,
- transfer planning,
- volume/PVC/object-store abstractions.

### 10.12 MCP Integration

`mcp/`

Bidirectional support:

1. Import MCP servers into WOLF Toolbox.
2. Expose WOLF Toolbox as MCP server.

MCP should be an interoperability layer, not the whole architecture.

---

## 11. MCP Integration Design

### 11.1 Import MCP Tools into WOLF

Given MCP server/client details, Toolbox v4 should eventually:

1. start/connect to MCP server,
2. list exposed tools/resources/prompts,
3. translate MCP schemas into WOLF `ToolSpec`s,
4. register MCP tools in the Toolbox,
5. wrap them with locality/execution metadata,
6. benchmark and document them,
7. expose them to WOLF agents as native tools.

Possible MCP endpoint model:

```yaml
protocol: mcp
transport: stdio | sse | http
server_command: npx some-mcp-server
server_args: []
tool_name: search_files
```

### 11.2 Expose WOLF Toolbox as MCP Server

Traditional MCP clients should eventually be able to:

- list WOLF tools,
- call WOLF tools,
- retrieve tool docs,
- request readiness/status,
- access output artifact references.

This makes WOLF interoperable with MCP clients while retaining WOLF’s richer lifecycle/locality model.

---

## 12. Locality and Volume Strategy

Data locality should be solved with explicit references and planning rather than implicit path assumptions.

Supported locality mechanisms should eventually include:

- local path,
- remote path,
- universe/actionbox path,
- named Docker/Podman volume,
- bind mount,
- Kubernetes PVC,
- object store URI,
- database connection,
- stream endpoint,
- artifact store,
- secret store.

Example mount model:

```yaml
mounts:
  - type: bind
    source: /host/data
    target: /mnt/data
    mode: ro
  - type: volume
    source: wolf-results-volume
    target: /mnt/results
    mode: rw
```

Example Kubernetes storage model:

```yaml
storage:
  kind: pvc
  claim_name: shared-dataset-pvc
  mount_path: /mnt/data
```

The agent should express intent and constraints; the Toolbox should plan the concrete data movement or mounting strategy.

---

## 13. Agent-Facing Actions to Support Later

Future workflow actions should include:

```text
toolbox_register_source
toolbox_inspect_source
toolbox_build_tool
toolbox_deploy_tool
toolbox_test_tool
toolbox_mark_ready
toolbox_search_tools
toolbox_plan_execution
toolbox_execute_tool
toolbox_get_run_status
toolbox_get_artifact
toolbox_benchmark_tool
toolbox_diagnose_tool
toolbox_modify_tool
toolbox_version_tool
toolbox_deprecate_tool
toolbox_archive_tool
toolbox_import_mcp_server
toolbox_export_mcp_server
toolbox_explain_locality_plan
```

Most important early addition:

```text
toolbox_plan_execution
```

Agents should be able to ask:

> “Given this tool and these inputs, where and how should this run?”

before execution.

---

## 14. Incremental Implementation Plan

### Phase 0 — Workplan and Scaffold

Goal: create the implementation plan and v4 directory scaffold.

Tasks:

- [x] Create `./framework/tooling/custom/toolboxes/v4/`.
- [x] Create this workplan file.
- [x] Create v4 `__init__.py`.
- [x] Create v4 `README.md`.

### Phase 1 — Core Models

Goal: define stable Pydantic models for v4.

Tasks:

- [x] Implement `tool_models.py`.
- [x] Define enums/literals for lifecycle states, locality kinds, source kinds, endpoint protocols.
- [x] Define `LocalityRef`.
- [x] Define `ArtifactRef`.
- [x] Define `ToolSource`.
- [x] Define `ToolSpec`.
- [x] Define `ToolPackage`.
- [x] Define `ToolDeployment`.
- [x] Define `ToolEndpoint`.
- [x] Define `ExecutionPolicy`.
- [x] Define `ExecutionPlan`.
- [x] Define `ToolRun`.
- [x] Define `ReadinessReport`.
- [x] Define `ToolboxV4Params`.
- [x] Add model serialization helpers if needed.

### Phase 2 — Persistent Registries

Goal: implement rehydratable JSON/SQLite registries.

Tasks:

- [x] Implement `registry.py`.
- [x] Create base JSON registry abstraction.
- [x] Implement `ToolRegistry`.
- [x] Implement `PackageRegistry`.
- [x] Implement `DeploymentRegistry`.
- [x] Implement `RunRegistry`.
- [x] Add CRUD operations.
- [x] Add list/search/filter operations.
- [x] Add version/status update helpers.
- [x] Add basic tests or smoke checks.

### Phase 3 — Toolbox Facade MVP

Goal: create a usable `ToolBoxV4` facade with registry-backed operations.

Tasks:

- [x] Implement `toolbox.py`.
- [x] Initialize registries from params.
- [x] Implement `register_source`.
- [x] Implement `register_tool_spec`.
- [x] Implement `get_tool_spec`.
- [x] Implement `list_tools`.
- [x] Implement simple text/metadata search.
- [x] Implement status updates.
- [x] Implement `get_stats`.
- [x] Provide compatibility alias/class name if useful.

### Phase 4 — Locality-Aware Planning MVP

Goal: implement first version of execution planning.

Tasks:

- [x] Implement `planner.py`.
- [x] Implement same-locality preference.
- [x] Implement explicit failure when no compatible deployment exists.
- [x] Implement simple `compute_near_data` policy.
- [x] Implement simple `data_near_compute` policy placeholder.
- [x] Implement mount/transfer plan placeholders.
- [x] Implement `toolbox.plan_execution(...)`.
- [x] Return inspectable `ExecutionPlan` with explanation.

### Phase 5 — Runtime Execution MVP

Goal: run simple local tools through v4.

Tasks:

- [x] Implement `runtime.py`.
- [x] Support CLI/subprocess endpoint.
- [x] Support Python callable endpoint if straightforward.
- [x] Support HTTP endpoint placeholder.
- [x] Record `ToolRun` entries.
- [x] Capture stdout/stderr/result/status/duration.
- [x] Implement `toolbox.execute_tool(...)` using planner + runtime.

### Phase 6 — Diagnostics and Readiness

Goal: tools must be tested before being marked ready.

Tasks:

- [x] Implement `diagnostics.py`.
- [x] Implement dependency check placeholder.
- [x] Implement endpoint health check placeholder.
- [ ] Implement smoke test model.
- [x] Implement readiness report.
- [x] Implement `toolbox.test_tool(...)`.
- [x] Implement `toolbox.mark_ready(...)` gated by readiness report.

### Phase 7 — Builder and Deployer Placeholders

Goal: create extension points for environment mastery.

Tasks:

- [x] Implement `builder.py`.
- [ ] Define `BuildBackend` interface. MVP placeholder exists via `ToolBuilder`.
- [x] Add no-op/local source backend.
- [ ] Add Docker/Podman backend placeholders.
- [x] Implement `deployer.py`.
- [ ] Define `DeploymentBackend` interface. MVP placeholder exists via `ToolDeployer`.
- [x] Add local process backend placeholder.
- [ ] Add Universe/actionbox backend placeholder.
- [ ] Add Docker/Podman backend placeholders.

### Phase 8 — MCP Import/Export Stubs

Goal: establish MCP extension points.

Tasks:

- [x] Create `mcp/__init__.py`.
- [x] Create `mcp/schema_translation.py`.
- [x] Create `mcp/import_mcp.py`.
- [x] Create `mcp/export_mcp_server.py`.
- [x] Define model translation boundaries.
- [x] Add placeholders that fail gracefully if MCP dependencies are absent.

### Phase 9 — Documentation

Goal: document how v4 works and how to continue development.

Tasks:

- [x] Write `custom/toolboxes/v4/README.md`.
- [x] Document model concepts.
- [ ] Document lifecycle states in more detail.
- [x] Document locality-aware execution.
- [x] Document future MCP integration.
- [x] Document minimal example.

### Phase 10 — Integration Preparation

Goal: prepare for dynamic import by future base abstractions/loaders.

Expected future layout:

```text
framework/tooling/base_toolbox.py
framework/tooling/base_tool.py
framework/tooling/custom/toolboxes/<version_or_name>/
framework/tooling/custom/tools/<version_or_name>/
```

Tasks:

- [x] Ensure stable exports from `custom/toolboxes/v4/__init__.py`.
- [x] Keep constructor clean and params-driven.
- [x] Avoid modifying current v1 implementation.
- [x] Avoid changing active Universe/action code until user approves.
- [ ] Provide compatibility notes for later adapter work.
- [ ] Prepare for a future dynamic toolbox loader.
- [ ] Prepare for a future dynamic tool loader.

---

## 15. Minimal MVP Scope

The first useful v4 MVP should include:

1. Pydantic models for locality, artifacts, specs, deployments, endpoints, plans, runs.
2. JSON-backed registries.
3. `ToolBoxV4` facade.
4. Register local script/source as a `ToolSpec`.
5. Register a local CLI endpoint/deployment.
6. Plan execution with explicit locality handling.
7. Execute simple CLI tool locally.
8. Record `ToolRun`.
9. Mark readiness only after a smoke test.
10. Provide clear extension points for Docker/Podman/Kubernetes/MCP.

---

## 16. Non-Goals for First Pass

Do not implement everything immediately.

Out of scope for first pass unless explicitly requested:

- full Kubernetes integration,
- full MCP server implementation,
- robust Docker image builder,
- secure sandbox enforcement,
- production-grade secret management,
- distributed artifact store,
- semantic vector indexing,
- automatic docstring/argparse extraction,
- full benchmark suite,
- active integration into current Universe FastAPI routes,
- migration of v1 implementation.

The first pass should create a clean, importable foundation that can be extended.

---

## 17. Safety and Governance Requirements

Because v4 will eventually build, deploy, and execute code across environments, safety must be designed from the start.

Required concepts:

- risk level per tool,
- allowed localities,
- allowed data classes,
- approval requirements,
- network access policy,
- filesystem access policy,
- secret access policy,
- max runtime/cost,
- sandbox requirement,
- audit logs,
- rollback support,
- package provenance.

Initial implementation can model these fields without enforcing all of them.

---

## 18. Suggested Initial APIs

Potential Python API shape:

```python
from framework.tooling.custom_toolboxes.v4 import ToolBoxV4, ToolboxV4Params

params = ToolboxV4Params(
    name="toolbox_v4",
    root_dir="./wf_workspace/toolboxes/toolbox_v4",
)

tb = ToolBoxV4(params)

source = tb.register_source(...)
spec = tb.register_tool_spec(...)
deployment = tb.register_deployment(...)
plan = tb.plan_execution(tool_id=spec.id, inputs={...})
run = tb.execute_plan(plan.id)
```

---

## 19. Crash Recovery Instructions

If interrupted:

1. Read this file.
2. Check `./framework/tooling/custom/toolboxes/v4/`.
3. Inspect files already created.
4. Run import smoke tests for created modules.
5. Continue from the Progress Tracker below.
6. Do not modify existing v1/current tooling unless explicitly approved.
7. Keep all v4 work isolated under `custom_toolboxes/v4`.

Useful commands:

```bash
find ./framework/tooling/custom_toolboxes/v4 -maxdepth 3 -type f -print | sort
python -m compileall ./framework/tooling/custom_toolboxes/v4
python - <<'PY'
import importlib
mods = [
    'framework.tooling.custom.toolboxes.v4',
    'framework.tooling.custom.toolboxes.v4.tool_models',
    'framework.tooling.custom.toolboxes.v4.toolbox',
]
for m in mods:
    try:
        importlib.import_module(m)
        print('OK', m)
    except Exception as e:
        print('ERR', m, type(e).__name__, e)
PY
```

---

## 20. Progress Tracker

### Completed

- [x] Added Docker/Podman backend skeletons and locality resolver/mount planning.
- [x] Added `MountSpec`, `TransferSpec`, `BuildPlan`, and `DeployPlan` models to `custom/toolboxes/v4/tool_models.py`.
- [x] Added `custom/toolboxes/v4/locality.py` with `LocalityResolver` for URI classification and mount/transfer planning.
- [x] Updated `planner.py` to use `LocalityResolver` and typed mount/transfer plan objects.
- [x] Reworked `builder.py` with `BuildBackend`, `SourceBuildBackend`, `DockerBuildBackend`, `PodmanBuildBackend`, and dry-run build plans.
- [x] Reworked `deployer.py` with `DeploymentBackend`, `LocalDeploymentBackend`, `DockerDeploymentBackend`, and `PodmanDeploymentBackend` dry-run deploy plans.
- [x] Added Toolbox v4 facade methods: `classify_artifact_uri`, `plan_build`, `build_tool_package`, and `plan_deployment`.
- [x] Ran compile validation for Toolbox v4 after build/deploy/locality changes.
- [x] Ran smoke validation: registered `/bin/echo`, generated source and Docker dry-run build plans, generated Docker dry-run deploy plan, classified local/volume/universe artifact refs, and verified execution planning creates mount placeholders for volume-locality mismatch.
- [x] Added executable smoke-test readiness gating for Toolbox v4.
- [x] Added `SmokeTestExpectation`, `SmokeTestCase`, and `SmokeTestResult` models to `custom/toolboxes/v4/tool_models.py`.
- [x] Added `smoke_tests` field to `ToolSpec`.
- [x] Extended `register_tool_spec(...)` and `register_local_cli_tool(...)` to accept smoke tests.
- [x] Extended `test_tool(...)` to optionally execute smoke tests and append smoke checks to `ReadinessReport`.
- [x] Added `run_smoke_tests(...)` and expectation evaluation helpers to `ToolBoxV4`.
- [x] Updated `mark_ready(...)` so tools are marked ready only when structural readiness and provided smoke tests pass.
- [x] Ran smoke validation: passing `/bin/echo` smoke test marks tool ready; failing smoke test prevents ready status and records failed readiness check.
- [x] Created non-invasive Universe ToolBox adapter layer.
- [x] Added `framework/tooling/universe_toolbox_adapter.py`.
- [x] Added `UniverseToolBoxAdapter` to wrap legacy ToolBox or ToolBoxV4 objects through common operations.
- [x] Added `UniverseToolBoxRegistryAdapter` to wrap Universe-like objects exposing a `TBs` registry or `get_tb/list_tbs` methods.
- [x] Adapter supports normalized list/search/info/stats/execute operations and v4-only plan/readiness fallbacks.
- [x] Confirmed adapter can wrap dynamically created ToolBox v4 instance without changing active Universe routes.
- [x] Ran smoke test: created ToolBox v4 through loader, registered `/bin/echo`, wrapped with adapter, listed/searched/executed tool, and wrapped fake Universe registry successfully.
- [x] Created base tooling interfaces and dynamic loader support.
- [x] Added `framework/tooling/base_tool.py` with `BaseTool`, `BaseToolAdapter`, `BaseToolExecutionRequest`, and `BaseToolExecutionResult`.
- [x] Added `framework/tooling/base_toolbox.py` with `BaseToolBox` and `BaseToolBoxParams`.
- [x] Added `framework/tooling/loader.py` with `load_toolbox_class`, `create_toolbox`, `load_tool_class`, and `create_tool`.
- [x] Updated `framework/tooling/__init__.py` to expose base interfaces/loaders while remaining tolerant of partial environments.
- [x] Verified dynamic loading of `framework.tooling.custom.toolboxes.v4.ToolBoxV4` by version name `v4`.
- [x] Verified dynamic loading of `framework.tooling.custom.tools.v4.ToolAdapterRegistry` by version name `v4`.
- [x] Ran smoke test: created Toolbox v4 through loader, registered `/bin/echo`, executed it, and confirmed adapter delegation.
- [x] Created `framework/tooling/custom/tools/v4/` runtime adapter layer.
- [x] Added `ToolExecutionRequest` and `ToolExecutionResult` models for endpoint-level execution delegation.
- [x] Added `ToolAdapter` base class and protocol adapters: `CliToolAdapter`, `PythonFunctionToolAdapter`, `HttpToolAdapter` placeholder, and `McpToolAdapter`.
- [x] Added `ToolAdapterRegistry` for protocol-to-adapter resolution.
- [x] Refactored Toolbox v4 runtime to delegate endpoint invocation to `custom.tools.v4` adapters.
- [x] Added `custom/tools/v4/README.md` documenting the separation between toolbox lifecycle orchestration and tool endpoint execution.
- [x] Ran compile validation for `custom/tools/v4` and the refactored Toolbox v4 runtime.
- [x] Ran smoke test: registered `/bin/echo`, marked ready, executed through Toolbox v4, verified execution delegated to `CliToolAdapter`, and confirmed stdout/result recording.
- [x] Prioritized MCP integration above later phases.
- [x] Added first real MCP integration layer under `framework/tooling/custom/toolboxes/v4/mcp/`.
- [x] Implemented MCP dependency detection for the official `mcp` Python SDK.
- [x] Implemented stdio MCP tool listing/import path when `mcp` is installed.
- [x] Implemented MCP ToolSpec/ToolSource/ToolDeployment/ToolEndpoint registration flow.
- [x] Implemented MCP endpoint execution path in `runtime.py` for stdio MCP servers.
- [x] Implemented FastMCP export/server builder exposing Toolbox v4 operations.
- [x] Added graceful `missing_dependency` behavior because current active interpreter does not have `mcp` installed, although `environment.yml` lists `mcp==1.13.1`.
- [x] Ran compile validation after MCP changes.
- [x] Ran MCP smoke checks confirming graceful missing-dependency responses.
- [x] Implemented Toolbox v4 MVP under `framework/tooling/custom/toolboxes/v4/`.
- [x] Added Pydantic model layer, JSON registries, planner, runtime, diagnostics, builder/deployer placeholders, security/storage helpers, MCP stubs, package exports, and README.
- [x] Ran `python -m compileall ./framework/tooling/custom/toolboxes/v4` successfully.
- [x] Ran smoke test: registered `/bin/echo` as local CLI tool, marked ready, planned execution, executed successfully, recorded run, and rehydrated registries from disk.
- [x] Discussed Toolbox v4 concept and goals.
- [x] Identified need for locality-aware tool execution.
- [x] Identified need for deployment/readiness lifecycle.
- [x] Identified bidirectional MCP integration requirement.
- [x] Created `./framework/tooling/custom/toolboxes/v4/`.
- [x] Created this workplan.

### In Progress

- [ ] Decide next target after Docker/Podman/locality skeletons: richer benchmark records, real container execution gated by policy, Universe-hosted v4 API integration, documentation refresh, or MCP validation when environment supports it.

### Next Recommended Step

With Docker/Podman backend skeletons and locality resolver/mount planning now implemented, the recommended next step is to refresh documentation and then choose between integration hardening paths.

Recommended next actions:

1. Update `custom/toolboxes/v4/README.md` with the new smoke-test readiness, dynamic loader, Universe adapter, Docker/Podman dry-run planning, and locality resolver examples.
2. Add richer benchmark/evaluation records so smoke tests and performance tests are tracked separately.
3. Add policy-gated real container execution/build support only after review.
4. Add a v4 Universe FastAPI adapter or routes under a non-breaking namespace.
5. Add stronger volume/mount resolution for Docker/Podman and future Kubernetes PVC support.
6. Validate MCP paths once the runtime environment has the official `mcp` SDK installed.

Current implementation remains isolated and non-breaking: active legacy ToolBox, Universe routes, and agent actions have not been replaced.

---

## 21. Design Mantra

> MCP gives agents a standard way to call tools.  
> WOLF Toolbox v4 should give agents a standard way to create, deploy, validate, route, call, observe, improve, and retire tools across environments.

The Toolbox is not only a tool caller. It is the operational memory and lifecycle manager for executable capabilities.
