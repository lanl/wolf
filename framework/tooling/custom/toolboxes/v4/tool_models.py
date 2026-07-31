from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class StrictModel(BaseModel):
    class Config:
        extra = 'forbid'
        use_enum_values = True


class LifecycleStatus(str, Enum):
    PROPOSED = 'proposed'
    REGISTERED = 'registered'
    INSPECTING = 'inspecting'
    INSPECTION_FAILED = 'inspection_failed'
    BUILDING = 'building'
    BUILD_FAILED = 'build_failed'
    PACKAGED = 'packaged'
    DEPLOYING = 'deploying'
    DEPLOY_FAILED = 'deploy_failed'
    DEPLOYED = 'deployed'
    TESTING = 'testing'
    TEST_FAILED = 'test_failed'
    READY = 'ready'
    DEGRADED = 'degraded'
    DEPRECATED = 'deprecated'
    ARCHIVED = 'archived'
    REJECTED = 'rejected'


class LocalityKind(str, Enum):
    LOCAL = 'local'
    UNIVERSE = 'universe'
    ACTIONBOX = 'actionbox'
    CONTAINER = 'container'
    REMOTE_HOST = 'remote_host'
    CLUSTER = 'cluster'
    OBJECT_STORE = 'object_store'
    VOLUME = 'volume'
    INLINE = 'inline'
    UNKNOWN = 'unknown'


class ArtifactKind(str, Enum):
    FILE = 'file'
    DIRECTORY = 'directory'
    STREAM = 'stream'
    OBJECT = 'object'
    DATABASE = 'database'
    SECRET = 'secret'
    MODEL = 'model'
    VOLUME = 'volume'
    INLINE = 'inline'
    UNKNOWN = 'unknown'


class SourceKind(str, Enum):
    PYTHON_FUNCTION = 'python_function'
    PYTHON_SCRIPT = 'python_script'
    SHELL_SCRIPT = 'shell_script'
    BINARY = 'binary'
    LOCAL_FILE = 'local_file'
    DIRECTORY = 'directory'
    GIT_REPO = 'git_repo'
    NOTEBOOK = 'notebook'
    DOCKERFILE = 'dockerfile'
    OCI_IMAGE = 'oci_image'
    OPENAPI = 'openapi'
    MCP_SERVER = 'mcp_server'
    REMOTE_API = 'remote_api'
    GENERATED_SOURCE = 'generated_source'
    UNKNOWN = 'unknown'


class PackageKind(str, Enum):
    SOURCE = 'source'
    ARCHIVE = 'archive'
    WHEEL = 'wheel'
    VENV = 'venv'
    OCI_IMAGE = 'oci_image'
    DOCKER_IMAGE = 'docker_image'
    PODMAN_IMAGE = 'podman_image'
    MANIFEST = 'manifest'
    UNKNOWN = 'unknown'


class DeploymentKind(str, Enum):
    LOCAL_PROCESS = 'local_process'
    LOCAL_SUBPROCESS = 'local_subprocess'
    LOCAL_VENV = 'local_venv'
    DOCKER_CONTAINER = 'docker_container'
    PODMAN_CONTAINER = 'podman_container'
    UNIVERSE = 'universe'
    ACTIONBOX = 'actionbox'
    REMOTE_HOST = 'remote_host'
    KUBERNETES = 'kubernetes'
    MCP_SERVER = 'mcp_server'
    UNKNOWN = 'unknown'


class EndpointProtocol(str, Enum):
    PYTHON_FUNCTION = 'python_function'
    CLI = 'cli'
    SUBPROCESS = 'subprocess'
    HTTP = 'http'
    GRPC = 'grpc'
    MCP = 'mcp'
    FILESYSTEM = 'filesystem'
    QUEUE = 'queue'
    UNIVERSE_API = 'universe_api'
    UNKNOWN = 'unknown'


class PlacementPolicy(str, Enum):
    COMPUTE_NEAR_DATA = 'compute_near_data'
    DATA_NEAR_COMPUTE = 'data_near_compute'
    CHEAPEST = 'cheapest'
    FASTEST = 'fastest'
    SAFEST = 'safest'
    USER_SELECTED = 'user_selected'
    EXISTING_DEPLOYMENT = 'existing_deployment'


class IsolationPolicy(str, Enum):
    NONE = 'none'
    PROCESS = 'process'
    VENV = 'venv'
    CONTAINER = 'container'
    SANDBOX = 'sandbox'
    REMOTE_UNIVERSE = 'remote_universe'
    CLUSTER = 'cluster'


class RunStatus(str, Enum):
    PLANNED = 'planned'
    RUNNING = 'running'
    SUCCESS = 'success'
    FAILED = 'failed'
    INTERRUPTED = 'interrupted'
    SKIPPED = 'skipped'


class LocalityRef(StrictModel):
    kind: LocalityKind = Field(default=LocalityKind.LOCAL)
    id: str = Field(default='local')
    uri: Optional[str] = None
    host: Optional[str] = None
    path: Optional[str] = None
    access_modes: List[str] = Field(default_factory=list)
    network_reachable_from: List[str] = Field(default_factory=list)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ArtifactRef(StrictModel):
    uri: str
    kind: ArtifactKind = ArtifactKind.UNKNOWN
    media_type: Optional[str] = None
    locality: LocalityRef = Field(default_factory=LocalityRef)
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None
    permissions: str = 'read_only'
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SafetyPolicy(StrictModel):
    risk_level: str = 'low'
    requires_sandbox: bool = False
    allowed_localities: List[str] = Field(default_factory=list)
    allowed_data_classes: List[str] = Field(default_factory=list)
    network_policy: str = 'deny_by_default'
    filesystem_policy: str = 'mounted_paths_only'
    secret_policy: str = 'explicit_allowlist'
    approval_required: List[str] = Field(default_factory=list)
    max_runtime_seconds: Optional[float] = None
    max_cost: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RuntimeRequirements(StrictModel):
    languages: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    environment: Dict[str, Any] = Field(default_factory=dict)
    hardware: List[str] = Field(default_factory=list)
    min_memory_mb: Optional[int] = None
    min_disk_mb: Optional[int] = None
    needs_gpu: bool = False
    needs_network: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolSource(StrictModel):
    id: str = Field(default_factory=lambda: new_id('src'))
    name: str
    kind: SourceKind = SourceKind.UNKNOWN
    uri: Optional[str] = None
    body: Optional[str] = None
    entrypoint: Optional[str] = None
    description: str = ''
    metadata: Dict[str, Any] = Field(default_factory=dict)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class ToolEndpoint(StrictModel):
    id: str = Field(default_factory=lambda: new_id('endpoint'))
    tool_id: Optional[str] = None
    deployment_id: Optional[str] = None
    protocol: EndpointProtocol = EndpointProtocol.UNKNOWN
    uri: Optional[str] = None
    entrypoint: Optional[str] = None
    invocation: Dict[str, Any] = Field(default_factory=dict)
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status: LifecycleStatus = LifecycleStatus.REGISTERED
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class ToolPackage(StrictModel):
    id: str = Field(default_factory=lambda: new_id('pkg'))
    tool_id: Optional[str] = None
    source_id: Optional[str] = None
    kind: PackageKind = PackageKind.SOURCE
    uri: Optional[str] = None
    digest: Optional[str] = None
    build_backend: Optional[str] = None
    build_log: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status: LifecycleStatus = LifecycleStatus.REGISTERED
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class ToolDeployment(StrictModel):
    id: str = Field(default_factory=lambda: new_id('deploy'))
    tool_id: Optional[str] = None
    package_id: Optional[str] = None
    name: str
    kind: DeploymentKind = DeploymentKind.LOCAL_SUBPROCESS
    locality: LocalityRef = Field(default_factory=LocalityRef)
    endpoint_ids: List[str] = Field(default_factory=list)
    health: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status: LifecycleStatus = LifecycleStatus.REGISTERED
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class SmokeTestExpectation(StrictModel):
    returncode: Optional[int] = 0
    stdout_equals: Optional[str] = None
    stdout_contains: Optional[str] = None
    stderr_equals: Optional[str] = None
    stderr_contains: Optional[str] = None
    result_equals: Any = None
    require_no_error: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SmokeTestCase(StrictModel):
    id: str = Field(default_factory=lambda: new_id('smoke'))
    name: str = 'smoke_test'
    description: str = ''
    args: Optional[List[str]] = None
    fn_args: Optional[List[Any]] = None
    kwargs: Dict[str, Any] = Field(default_factory=dict)
    input_data: Optional[str] = None
    timeout: Optional[float] = None
    expectation: SmokeTestExpectation = Field(default_factory=SmokeTestExpectation)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SmokeTestResult(StrictModel):
    id: str = Field(default_factory=lambda: new_id('smokeresult'))
    test_id: str
    name: str
    passed: bool
    run_id: Optional[str] = None
    message: str = ''
    details: Dict[str, Any] = Field(default_factory=dict)
    checked_at: str = Field(default_factory=utc_now)


class ToolSpec(StrictModel):
    id: str = Field(default_factory=lambda: new_id('tool'))
    name: str
    version: str = '0.1.0'
    description: str = ''
    capabilities: List[str] = Field(default_factory=list)
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    side_effects: List[str] = Field(default_factory=list)
    requirements: RuntimeRequirements = Field(default_factory=RuntimeRequirements)
    safety_policy: SafetyPolicy = Field(default_factory=SafetyPolicy)
    status: LifecycleStatus = LifecycleStatus.PROPOSED
    source_refs: List[str] = Field(default_factory=list)
    package_refs: List[str] = Field(default_factory=list)
    deployment_refs: List[str] = Field(default_factory=list)
    endpoint_refs: List[str] = Field(default_factory=list)
    documentation_refs: List[str] = Field(default_factory=list)
    benchmark_refs: List[str] = Field(default_factory=list)
    smoke_tests: List[SmokeTestCase] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class ExecutionPolicy(StrictModel):
    placement: PlacementPolicy = PlacementPolicy.EXISTING_DEPLOYMENT
    isolation: IsolationPolicy = IsolationPolicy.PROCESS
    persistence: str = 'ephemeral'
    output_location: str = 'same_as_input'
    allow_data_movement: bool = False
    allow_mounts: bool = True
    allow_network: bool = False
    max_cost: Optional[float] = None
    timeout: Optional[float] = None
    required_hardware: List[str] = Field(default_factory=list)
    risk_tolerance: str = 'low'
    preferred_deployment_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MountSpec(StrictModel):
    id: str = Field(default_factory=lambda: new_id('mount'))
    type: str = 'bind'
    source: str
    target: str
    mode: str = 'ro'
    reason: str = ''
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TransferSpec(StrictModel):
    id: str = Field(default_factory=lambda: new_id('transfer'))
    source_uri: str
    target_uri: Optional[str] = None
    source_locality: Optional[LocalityRef] = None
    target_locality: Optional[LocalityRef] = None
    strategy: str = 'copy'
    reason: str = ''
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BuildPlan(StrictModel):
    id: str = Field(default_factory=lambda: new_id('buildplan'))
    source_id: Optional[str] = None
    tool_id: Optional[str] = None
    backend: str = 'noop'
    package_kind: PackageKind = PackageKind.SOURCE
    dockerfile_path: Optional[str] = None
    context_dir: Optional[str] = None
    image_tag: Optional[str] = None
    command: Optional[List[str]] = None
    dry_run: bool = True
    warnings: List[str] = Field(default_factory=list)
    explanation: str = ''
    created_at: str = Field(default_factory=utc_now)


class DeployPlan(StrictModel):
    id: str = Field(default_factory=lambda: new_id('deployplan'))
    tool_id: Optional[str] = None
    package_id: Optional[str] = None
    backend: str = 'local'
    deployment_kind: DeploymentKind = DeploymentKind.LOCAL_SUBPROCESS
    image_tag: Optional[str] = None
    command: Optional[List[str]] = None
    mounts: List[MountSpec] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    dry_run: bool = True
    warnings: List[str] = Field(default_factory=list)
    explanation: str = ''
    created_at: str = Field(default_factory=utc_now)


class ExecutionPlan(StrictModel):
    id: str = Field(default_factory=lambda: new_id('plan'))
    tool_id: str
    tool_name: Optional[str] = None
    tool_version: Optional[str] = None
    selected_package_id: Optional[str] = None
    selected_deployment_id: Optional[str] = None
    selected_endpoint_id: Optional[str] = None
    input_bindings: Dict[str, ArtifactRef] = Field(default_factory=dict)
    output_bindings: Dict[str, ArtifactRef] = Field(default_factory=dict)
    mounts: List[MountSpec] = Field(default_factory=list)
    transfers: List[TransferSpec] = Field(default_factory=list)
    secret_bindings: Dict[str, Any] = Field(default_factory=dict)
    invocation: Dict[str, Any] = Field(default_factory=dict)
    readiness_status: LifecycleStatus = LifecycleStatus.REGISTERED
    estimated_cost: Optional[float] = None
    estimated_duration_seconds: Optional[float] = None
    risks: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    explanation: str = ''
    policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    status: RunStatus = RunStatus.PLANNED
    created_at: str = Field(default_factory=utc_now)


class ToolRun(StrictModel):
    id: str = Field(default_factory=lambda: new_id('run'))
    plan_id: Optional[str] = None
    tool_id: str
    deployment_id: Optional[str] = None
    endpoint_id: Optional[str] = None
    inputs: Dict[str, ArtifactRef] = Field(default_factory=dict)
    outputs: Dict[str, ArtifactRef] = Field(default_factory=dict)
    status: RunStatus = RunStatus.PLANNED
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    returncode: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    result: Any = None
    logs: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)


class ReadinessCheck(StrictModel):
    name: str
    passed: bool
    message: str = ''
    details: Dict[str, Any] = Field(default_factory=dict)
    checked_at: str = Field(default_factory=utc_now)


class ReadinessReport(StrictModel):
    id: str = Field(default_factory=lambda: new_id('ready'))
    tool_id: str
    deployment_id: Optional[str] = None
    endpoint_id: Optional[str] = None
    checks: List[ReadinessCheck] = Field(default_factory=list)
    passed: bool = False
    status: LifecycleStatus = LifecycleStatus.TESTING
    summary: str = ''
    created_at: str = Field(default_factory=utc_now)


class ToolboxV4Params(StrictModel):
    name: str = 'toolbox_v4'
    root_dir: str = './wf_workspace/toolboxes/toolbox_v4'
    registry_dir: Optional[str] = None
    default_locality: LocalityRef = Field(default_factory=LocalityRef)
    enable_mcp: bool = False
    enable_semantic_index: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)
