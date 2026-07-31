# Toolbox v4

Toolbox v4 is a locality-aware, lifecycle-managed WOLF Toolbox implementation.

It is designed to evolve from a tool registry/executor into a **capability lifecycle manager** that can register, package, deploy, test, plan, execute, diagnose, benchmark, archive, and expose tools across local, universe/actionbox, container, and clustered environments.

The main design shift is:

```text
Legacy ToolBox:
  "What tools do I have, and how do I call them locally?"

Toolbox v4:
  "What capability is needed, where can it safely run, where are the inputs/outputs,
   has it been validated, and how should execution be planned across localities?"
```

---

## 1. Current Status

Toolbox v4 is an MVP/foundation layer. It is importable and has working local execution, persistent registries, dynamic loading, readiness smoke tests, locality planning, and dry-run Docker/Podman build/deploy planning.

It is intentionally isolated from the legacy implementation:

```text
framework/tooling/toolbox.py              # existing/legacy implementation
framework/tooling/tools.py                # existing/legacy implementation
framework/tooling/custom/toolboxes/v4/    # new Toolbox v4 implementation
framework/tooling/custom/tools/v4/        # new v4 tool runtime adapters
```

Active Universe routes and current agent actions have **not** been replaced. Integration is prepared through adapters and loaders.

---

## 2. Directory Layout

```text
framework/tooling/
  base_tool.py
  base_toolbox.py
  loader.py
  universe_toolbox_adapter.py

  custom/
    toolboxes/
      v4/
        __init__.py
        README.md
        tool_models.py
        registry.py
        toolbox.py
        planner.py
        runtime.py
        diagnostics.py
        builder.py
        deployer.py
        locality.py
        security.py
        storage.py
        mcp/
          __init__.py
          import_mcp.py
          export_mcp_server.py
          schema_translation.py

    tools/
      v4/
        __init__.py
        README.md
        tool_models.py
        tool.py
```

### Separation of responsibilities

```text
custom/toolboxes/v4
  Owns lifecycle, registry, planning, readiness, packaging, deployment planning,
  locality reasoning, run recording, and orchestration.

custom/tools/v4
  Owns concrete endpoint invocation mechanics: CLI, Python functions, MCP, HTTP, etc.
```

---

## 3. Main Capabilities

The current implementation supports:

- Pydantic models for:
  - tools,
  - sources,
  - packages,
  - deployments,
  - endpoints,
  - artifacts,
  - localities,
  - mounts,
  - transfers,
  - execution policies,
  - execution plans,
  - runs,
  - readiness reports,
  - smoke tests,
  - build plans,
  - deploy plans.
- JSON-backed persistent registries.
- A `ToolBoxV4` facade.
- Dynamic loading through `framework.tooling.loader`.
- Custom v4 tool runtime adapters.
- Registration of sources/specs/packages/deployments/endpoints.
- Convenience registration of local CLI tools.
- Basic text search over registered tools.
- Locality-aware execution planning.
- Typed mount/transfer placeholders.
- Local CLI/subprocess execution.
- Python `module:function` execution.
- Tool run recording.
- Structural readiness reports.
- Executable smoke-test readiness gating.
- Docker/Podman dry-run build plans.
- Docker/Podman dry-run deploy plans.
- MCP import/export scaffolding with graceful missing-dependency behavior.
- Non-invasive Universe ToolBox adapter support.

---

## 4. Core Concepts

### 4.1 `ToolSpec`

Canonical description of a tool/capability.

A `ToolSpec` describes:

- name,
- version,
- description,
- capabilities,
- input/output schemas,
- lifecycle status,
- source references,
- package references,
- deployment references,
- endpoint references,
- smoke tests,
- metadata and provenance.

### 4.2 `ToolSource`

Where a tool comes from.

Examples:

- local file,
- Python script,
- shell script,
- generated source,
- Git repo,
- Dockerfile,
- MCP server,
- remote API.

### 4.3 `ToolPackage`

A build/package artifact.

Examples:

- source package,
- wheel,
- archive,
- Docker image,
- Podman image,
- OCI image.

Current Docker/Podman support is dry-run planning only.

### 4.4 `ToolDeployment`

Where and how a tool is deployed or deployable.

Examples:

- local subprocess,
- local process,
- Docker container,
- Podman container,
- Universe/actionbox,
- Kubernetes,
- MCP server.

### 4.5 `ToolEndpoint`

Concrete invocation interface.

Supported/planned protocols:

- `cli`,
- `subprocess`,
- `python_function`,
- `mcp`,
- `http`,
- `grpc`,
- `universe_api`.

### 4.6 `ArtifactRef` and `LocalityRef`

Toolbox v4 does not assume that caller, tool, input, output, and runtime share the same filesystem.

Inputs and outputs can be represented as artifact references:

```python
ArtifactRef(
    uri="universe://gpu_box/data/input.csv",
    kind="file",
    locality=LocalityRef(kind="universe", id="gpu_box"),
)
```

### 4.7 `ExecutionPlan`

An inspectable plan created before tool execution.

It includes:

- selected tool,
- selected deployment,
- selected endpoint,
- input/output bindings,
- mount placeholders,
- transfer placeholders,
- warnings,
- risks,
- explanation.

### 4.8 Readiness and smoke tests

A tool should not be marked `ready` simply because it exists in the registry.

Toolbox v4 supports smoke tests through:

- `SmokeTestCase`,
- `SmokeTestExpectation`,
- `SmokeTestResult`.

`mark_ready(...)` runs readiness checks and any attached smoke tests. If smoke tests fail, the tool is not marked ready.

---

## 5. Basic Usage

### 5.1 Direct construction

```python
from framework.tooling.custom.toolboxes.v4 import ToolBoxV4, ToolboxV4Params

box = ToolBoxV4(ToolboxV4Params(
    name="demo_toolbox",
    root_dir="./wf_workspace/demo_toolbox_v4",
))
```

### 5.2 Dynamic construction through loader

```python
from framework.tooling.loader import create_toolbox

box = create_toolbox("v4", {
    "name": "demo_toolbox",
    "root_dir": "./wf_workspace/demo_toolbox_v4",
})
```

Aliases currently supported:

```text
v4
toolbox_v4
```

---

## 6. Register and Execute a Local CLI Tool

```python
from framework.tooling.custom.toolboxes.v4 import ToolBoxV4, ToolboxV4Params

box = ToolBoxV4(ToolboxV4Params(root_dir="./tmp/toolbox_v4_demo"))

spec = box.register_local_cli_tool(
    name="echo",
    entrypoint="/bin/echo",
    description="Echo text using /bin/echo",
    mark_ready=True,
)

plan = box.plan_execution("echo")
print(plan.explanation)

run = box.execute_tool("echo", args=["hello", "toolbox", "v4"])
print(run.status)
print(run.stdout)
```

Expected output:

```text
hello toolbox v4
```

---

## 7. Smoke-Test Readiness

Smoke tests can be attached when registering a tool.

```python
from framework.tooling.custom.toolboxes.v4 import ToolBoxV4, ToolboxV4Params
from framework.tooling.custom.toolboxes.v4.tool_models import SmokeTestCase, SmokeTestExpectation

box = ToolBoxV4(ToolboxV4Params(root_dir="./tmp/toolbox_v4_smoke_demo"))

box.register_local_cli_tool(
    name="echo_ready",
    entrypoint="/bin/echo",
    description="Echo tool with readiness smoke test",
    smoke_tests=[
        SmokeTestCase(
            name="echo_outputs_expected_text",
            args=["smoke", "ready"],
            expectation=SmokeTestExpectation(
                returncode=0,
                stdout_equals="smoke ready\n",
            ),
        )
    ],
)

report = box.mark_ready("echo_ready")
print(report.passed)
print(report.status)
```

If the smoke test passes, the tool is marked `ready`.

If the smoke test fails, the tool remains in its previous lifecycle state and the readiness report records the failed check.

---

## 8. Locality-Aware Planning

### 8.1 Classify artifact URIs

```python
local_ref = box.classify_artifact_uri("/tmp/input.csv", kind="file")
volume_ref = box.classify_artifact_uri("volume://shared-data/input.csv")
universe_ref = box.classify_artifact_uri("universe://gpu_box/data/input.csv")
```

Current supported URI patterns include:

```text
local paths
universe://...
actionbox://...
container://...
volume://...
s3://..., gs://..., az://..., object://...
inline://...
```

### 8.2 Plan execution with non-local input

```python
from framework.tooling.custom.toolboxes.v4.tool_models import ExecutionPolicy, PlacementPolicy

volume_ref = box.classify_artifact_uri("volume://shared-data/input.csv")

plan = box.plan_execution(
    "echo",
    inputs={"csv": volume_ref},
    policy=ExecutionPolicy(
        placement=PlacementPolicy.EXISTING_DEPLOYMENT,
        allow_mounts=True,
    ),
)

print(plan.mounts)
print(plan.transfers)
print(plan.warnings)
```

If the input locality differs from the selected deployment locality, Toolbox v4 can produce a typed `MountSpec` or `TransferSpec` placeholder.

This is not yet real Docker/Kubernetes mount execution. It is an inspectable planning layer.

---

## 9. Docker/Podman Dry-Run Build and Deploy Plans

Toolbox v4 includes Docker/Podman backend skeletons.

Real builds and container launches are not executed yet. Instead, the system produces dry-run plans and warnings.

### 9.1 Build plan

```python
info = box.tool_info("echo")
source_id = info["tool"]["source_refs"][0]

plan = box.plan_build(
    source_id=source_id,
    tool_id=info["tool"]["id"],
    backend="docker",
    image_tag="wolf/echo:test",
    context_dir=".",
)

print(plan.command)
print(plan.warnings)
```

If Docker is not installed, the plan includes a warning such as:

```text
docker command not found on PATH; plan is not executable in current environment.
```

### 9.2 Package record from dry-run build

```python
pkg = box.build_tool_package(
    source_id=source_id,
    tool_id=info["tool"]["id"],
    backend="docker",
    dry_run=True,
    image_tag="wolf/echo:test",
    context_dir=".",
)
```

### 9.3 Deployment plan

```python
deploy_plan = box.plan_deployment(
    package_id=pkg.id,
    tool_id=info["tool"]["id"],
    backend="docker",
    image_tag="wolf/echo:test",
    container_name="wolf-echo-test",
)

print(deploy_plan.command)
print(deploy_plan.warnings)
```

---

## 10. Custom Tools v4 Runtime Adapters

Toolbox v4 delegates concrete endpoint execution to `framework.tooling.custom.tools.v4`.

Current adapters:

- `CliToolAdapter`
- `PythonFunctionToolAdapter`
- `McpToolAdapter`
- `HttpToolAdapter` placeholder

The adapter registry can be loaded directly:

```python
from framework.tooling.custom.tools.v4 import ToolAdapterRegistry

registry = ToolAdapterRegistry()
cli_adapter = registry.get("cli")
```

The Toolbox runtime uses this registry internally.

---

## 11. Dynamic Loading

Dynamic loading is implemented in:

```text
framework/tooling/loader.py
```

Example:

```python
from framework.tooling.loader import load_toolbox_class, create_toolbox, load_tool_class, create_tool

loaded = load_toolbox_class("v4")
box = create_toolbox("v4", {"root_dir": "./tmp/dynamic_tb"})

loaded_tools = load_tool_class("v4")
adapters = create_tool("v4")
```

This prepares the framework to support multiple toolbox/tool versions side by side.

---

## 12. Universe Integration Adapter

A non-invasive adapter exists at:

```text
framework/tooling/universe_toolbox_adapter.py
```

It provides:

- `UniverseToolBoxAdapter`
- `UniverseToolBoxRegistryAdapter`

Purpose:

- wrap either legacy `ToolBox` or `ToolBoxV4`,
- normalize common operations,
- avoid modifying active Universe routes yet.

Example:

```python
from framework.tooling.universe_toolbox_adapter import UniverseToolBoxAdapter

adapter = UniverseToolBoxAdapter("my_tb", box)
print(adapter.info())
print(adapter.list_tools())
print(adapter.search_tools("echo"))
run = adapter.execute_tool("echo", args=["hello"])
```

---

## 13. MCP Integration

Toolbox v4 includes first-pass MCP support.

Files:

```text
framework/tooling/custom/toolboxes/v4/mcp/import_mcp.py
framework/tooling/custom/toolboxes/v4/mcp/export_mcp_server.py
framework/tooling/custom/toolboxes/v4/mcp/schema_translation.py
framework/tooling/custom/tools/v4/tool.py
```

### 13.1 Dependency status

The project `environment.yml` lists:

```text
mcp==1.13.1
```

However, the active interpreter used during implementation did not have the `mcp` package installed. Therefore MCP code paths currently fail gracefully with structured `missing_dependency` results when the SDK is unavailable.

### 13.2 Import MCP server tools

Intended API:

```python
result = box.import_mcp_server(
    name="filesystem",
    command="npx",
    args=["some-mcp-server"],
    transport="stdio",
    dry_run=False,
)
```

When `mcp` is installed, the intended flow is:

1. start/connect to stdio MCP server,
2. initialize MCP client session,
3. list MCP tools,
4. translate MCP schemas into WOLF `ToolSpec`s,
5. register MCP source/deployment/endpoint records.

### 13.3 Expose Toolbox v4 as MCP

Intended API:

```python
result = box.serve_mcp(run=False)
```

This attempts to build a `FastMCP` server exposing Toolbox v4 operations such as:

- list tools,
- search tools,
- get tool info,
- execute tool.

---

## 14. Persistent Registries

Toolbox v4 persists state under the configured `root_dir`:

```text
<root_dir>/registries/
  tools.json
  sources.json
  packages.json
  deployments.json
  endpoints.json
  runs.json
  readiness.json
```

This allows a Toolbox v4 instance to be rehydrated after restart.

Example:

```python
box1 = ToolBoxV4(ToolboxV4Params(root_dir="./tmp/rehydrate_demo"))
box1.register_local_cli_tool("echo", "/bin/echo", mark_ready=True)

box2 = ToolBoxV4(ToolboxV4Params(root_dir="./tmp/rehydrate_demo"))
print(box2.list_tools())
```

---

## 15. Lifecycle States

Toolbox v4 models lifecycle explicitly.

Common statuses include:

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

A tool should only become `ready` after passing required structural checks and provided smoke tests.

---

## 16. Validation Performed

During implementation, the following checks were run successfully:

- compile validation for Toolbox v4 modules,
- compile validation for custom tools v4 modules,
- local CLI execution with `/bin/echo`,
- persistent registry rehydration,
- dynamic loader smoke test,
- custom tool adapter delegation smoke test,
- Universe adapter smoke test,
- passing and failing smoke-test readiness cases,
- Docker dry-run build plan generation,
- Docker dry-run deploy plan generation,
- local/volume/universe artifact classification,
- mount placeholder creation for locality mismatch,
- MCP missing-dependency graceful responses.

---

## 17. Current Limitations

- Active Universe routes do not yet use Toolbox v4.
- Existing v1/current tooling remains active for production paths.
- Docker/Podman backends generate dry-run plans only.
- Real container builds/runs are not implemented yet.
- Locality resolver is early and conservative.
- Mount/transfer planning is not yet backend-specific enough for real Docker/Kubernetes execution.
- MCP requires the official `mcp` SDK to be installed before real validation.
- HTTP endpoint execution is a placeholder.
- Semantic vector indexing for v4 tools is not yet implemented.
- Benchmark records are not yet separated from smoke tests.
- Security/governance enforcement is minimal.

---

## 18. Recommended Next Steps

Suggested next implementation steps:

1. Add benchmark/evaluation records separate from smoke-test readiness.
2. Improve Docker/Podman mount planning using `MountSpec`.
3. Add policy-gated real Docker/Podman build and run support.
4. Add non-breaking Universe API routes or a v4-specific Universe adapter endpoint.
5. Validate MCP paths in an environment where `mcp==1.13.1` is installed.
6. Add semantic tool indexing using the WOLF KB/VStore stack.
7. Add stronger governance:
   - approval gates,
   - sandbox enforcement,
   - network policy,
   - filesystem policy,
   - secret policy,
   - audit trails.
8. Add migration/compatibility documentation for v1 and v4 coexistence.

---

## 19. Design Mantra

```text
MCP gives agents a standard way to call tools.
WOLF Toolbox v4 gives agents a standard way to create, deploy, validate,
route, call, observe, improve, and retire tools across environments.
```

Toolbox v4 is not only a tool caller. It is the operational memory and lifecycle manager for executable capabilities.
