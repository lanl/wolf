# WOLF Application Working Notes

This document is intended to give a new agent enough context to begin working productively in this repository. It should be maintained as our understanding improves and as the application changes.

**Co-living knowledge index:** See [`./wisdom.md`](./wisdom.md) for the registry of focused wisdom nugget (`.nug`) documents. `app.md` remains the broad architectural overview, while `wisdom.md` indexes source-grounded discoveries that are narrower, deeper, or operationally specific.

The core mental model is:

- `runners/interactive.py` is a thin launcher.
- `framework/utils/config_tools.py` builds a session/runtime.
- `framework/infrastructure/base_infrastructure.py` is the local sandbox/system interface and shared runtime state.
- `framework/workflows/custom_workflows/turn_based_workflow.py` is the active interactive CLI orchestration loop.
- `framework/workflows/custom_workflows/gateway_action_workflow.py` is the async websocket-friendly orchestration loop used by the FastAPI gateway.
- Agents do not respond with arbitrary free text; they respond with JSON actions validated by Pydantic models discovered from `framework/workflows/agent_actions/`.
- Universes, also called actionboxes, are external or nested sandbox environments that can host knowledgebases, toolboxes, and executable actions.

---

## 1. Interactive Startup Path

### Primary entry point

**File:** `runners/interactive.py`

This is the main interactive CLI launcher currently used to start a session.

Important behavior:

1. Defines a local `user_name = "user"`.
2. Defines local parameter overrides:
   - `banner_image_width`
   - `banner_image_color`
   - `verbose`
   - optionally `universes`, currently commented out.
3. Imports default LLM configuration:
   - `from config.defaults.inference_engine import LLM`
4. Imports default session inputs:
   - `from config.session.default.params.inputs import session_params`
5. Copies `session_params`, applies local overrides, and injects:
   - `session_inputs['LLMs'] = LLM`
6. Constructs a CLI session:
   - `cli_session = CliSession(session_params=session_inputs, db_client=None)`
7. Creates a new session:
   - `cli_session.create_session(resume_session=None)`
8. Starts the workflow:
   - `cli_session.session['wf'].run(user_name=user_name)`

Typical invocation:

```bash
python runners/interactive.py
```

Resume options are present but commented out in `interactive.py`, e.g.:

```python
# cli_session.create_session(resume_session="last")
# cli_session.create_session(resume_session="20260716_082240")
```

---

## 2. Configuration Inputs

### Session parameters

**File:** `config/session/default/params/inputs.py`

Defines `session_params`, currently a smaller dict than the extended commented template. Current active keys:

```python
session_params = {
    "tiktoken_cache_dir": (Path.cwd() / ".tiktoken_cache").resolve(),
    "banner_image_file": f"{(Path.cwd() / 'config/preferences/banner').resolve()}/WOLF.png".strip(),
    "banner_image_color": "purple",
    "banner_image_width": 100,
    "universes": [],
    "kbs": [],
    "tbs": [],
    "verbose": 0,
}
```

The longer commented template suggests supported/anticipated session inputs include:

- `curl_ca_bundle_file`
- `session_dir`
- `memory_db_persist_sub_dir`
- `summaries_params`
- `traces_params`
- `chat_manager`
- `memory_manager`
- `context_manager`
- `infra`
- `actions`
- `max_ctx_tokens`
- `wf`

### LLM configuration

**File:** `config/defaults/inference_engine.py`

Builds the `LLM` dict consumed by session creation.

Default provider settings:

```python
Provider_params = {
    'provider_type': 'openai',
    'host': 'https://shirty.sandia.gov',
    'port': None,
    'api_key_var': 'LOCAL_API_KEY',
    'api_version': 'api/v1',
    'verbose': 2,
}
```

Default model settings:

```python
Model_params = {
    'model': None,
    'capabilities': [],
}
```

Environment variable overrides loaded from `.env` / `USER_ENV_VARs` include:

- `INFERENCE_HOST_ADDRESS`
- `INFERENCE_HOST_PORT`
- `LOCAL_API_KEY_VAR`
- `API_VERSION`
- `LLM_MODEL`
- `LLM_CAPABILITIES`

The resulting shape is roughly:

```python
LLM = {
    Model_name: {
        'provider_type': ...,
        'host': ...,
        'port': ...,
        'api_key_var': ...,
        'api_version': ...,
        'verbose': ...,
        'model': ...,
        'capabilities': ...,
    }
}
```

---

## 3. Session Construction

### Main session factory

**File:** `framework/utils/config_tools.py`

Important classes/functions:

- `CliSession`
- `BaseSession`
- `setup_cli_session()`
- `load_existing_session()`
- `build_list_agents()`
- `build_list_universes()`
- `create_session_dir()`
- `load_session_certs()`
- `show_banner()`

### `CliSession`

`CliSession` extends a minimal `BaseSession` wrapper and stores the created session dict on `self.session`.

```python
cli_session = CliSession(session_params=session_inputs, db_client=None)
cli_session.create_session(resume_session=None)
```

Internally:

```python
self.session = setup_cli_session(
    session_params=self.session_params,
    resume_session=resume_session,
    db_client=db_client,
)
```

### `setup_cli_session()`

This is the core bootstrapping function for a new or resumed CLI workflow session.

For a new session, it performs the following:

1. Requires `LLMs` in `session_params`.
2. Calls `load_session_certs(session_params)`.
   - Sets `TIKTOKEN_CACHE_DIR`.
   - Ensures the tiktoken cache directory exists.
3. Calls `show_banner(session_params)`.
4. Creates or uses a session directory:
   - default: `wf_workspace/session_YYYYMMDD_HHMMSS`
5. Initializes ChromaDB under:
   - `session_dir/VStore`
6. Builds agents:
   - `build_list_agents(session_params)`
7. Selects the first configured LLM as the main agent.
8. Treats additional configured LLMs as worker agents.
9. Builds configured universes/actionboxes:
   - `build_list_universes(session_params)`
10. Builds vector stores:
    - summaries vector store
    - traces vector store
11. Creates managers:
    - `BaseChatManager`
    - `MemoryManager`
    - `ContextManager`
12. Creates `BaseInfrastructure`.
13. Creates `TurnBasedWorkflow`.
14. Returns a session dict:

```python
{
    'agents': {'main': main_agent, 'workers': workers},
    'objects': {'universes': UNIVs, 'kbs': KBs, 'tbs': TBs},
    'managers': {'chat': chat_manager, 'memory': memory_manager, 'context': context_manager},
    'session_dir': session_dir,
    'db_client': db_client,
    'wf': WF,
}
```

### `build_list_agents()`

Constructs `OpenAIAgent` instances from the configured `LLMs` dict.

Important dependencies:

- `framework.agentic.agents.OpenAIAgent`
- `set_llm_api_key()`
- `framework.utils.multimodal_input.normalize_capabilities()`

Each LLM entry is converted into:

```python
OpenAIAgent(
    model=llm['model'],
    host_address=llm['host'],
    host_port=llm['port'],
    api_version=llm['api_version'],
    api_key=llm['api_key'],
    verbose=llm['verbose'],
    capabilities=list(normalize_capabilities(llm.get('capabilities', []))),
    ctx_window_length=max_ctx,
)
```

### `build_list_universes()`

Builds remote universe/actionbox parameters from `session_params['universes']`.

Expected universe config shape:

```python
{"host": "0.0.0.0", "port": 8115, "scheme": "http"}
```

The function uses:

```python
get_base_universe_params(host=univ['host'], port=univ['port'], verbose=session_params['verbose'])
```

and appends valid `BaseUniverseParams` objects to the runtime object list.

---

## 4. Workflow Architecture

### Active workflow

**File:** `framework/workflows/custom_workflows/turn_based_workflow.py`

Class:

```python
class TurnBasedWorkflow(BaseWorkflow):
```

This is the main interactive turn-based workflow used by `setup_cli_session()`.

It extends:

**File:** `framework/workflows/base_workflow.py`

Class:

```python
class BaseWorkflow:
```

### `TurnBasedWorkflow.run()`

The loop routes turns between:

- user
- main agent
- worker agents
- fallback/reset to user

Important parameters:

```python
def run(
    self,
    user_name: str = "user",
    action_names: Optional[List[str]] = None,
    wolf_commands = ['show', 'clear', 'quit', 'exit', 'bye', 'cls'],
    wf_first_turn = "user",
    log_console: bool = True,
):
```

Key behavior:

1. Sets the action space using `self.set_wf_action_space(action_names)`.
   - If `action_names` is provided, restricts allowed actions to a subset.
   - Otherwise uses the full dynamic action union.
2. Sets `self.WF_USER`.
3. Registers the user role in `infra.ROLEs`.
4. Sets `self.WORKFLOW_TURN`.
5. Loops forever until user exit/break.

### User turn

When the turn is `user` or `self.WF_USER`, the workflow:

1. Shows updated history:
   - `self.infra.show_updated_history()`
2. Reads input:
   - `interactive_input_line_wrapped()`
3. Processes commands:
   - `self.infra.process_user_input(user_prompt)`
4. If regular input:
   - prepares multimodal-safe input:
     - `self.infra.prepare_user_input_for_agent(WF_PROMPT, agent=target_actor)`
   - appends compact text to history:
     - `self.update_history(...)`
   - transfers turn to the target actor/interlocutor.

### Agent/worker turn

Handled by:

```python
def _handle_actor_turn(self, actor, name: str):
```

Behavior:

1. Shows updated history.
2. Gets compacted context:
   - `self.context_manager.get_compacted_context()`
3. Builds a large agent prompt containing:
   - agent role prompt
   - compacted context
   - allowed action schema
   - infrastructure description
   - best practices / agent behavior
   - workflow rules
4. Consumes pending rich multimodal content:
   - `self.infra.consume_pending_agent_content()`
5. Calls the actor:
   - if actor has `structured_output` capability, uses `actor.get_structured_output(...)`
   - otherwise uses JSON formatting fallback.
6. Validates the response against the Pydantic action union.
7. Appends the action to history.
8. Executes the action:
   - `action_obj.execute(infra=self.infra)`
9. Determines next turn:
   - `yield_motion_to`, if present
   - else `receiver`, if present
   - else `system`

---

### Gateway workflow

**File:** `framework/workflows/custom_workflows/gateway_action_workflow.py`

Class:

```python
class GatewayActionWorkflow(BaseWorkflow):
```

This workflow adapts the `TurnBasedWorkflow` actor-turn semantics for websocket sessions. It is intentionally not a transport layer; it owns workflow orchestration while `framework/pack/gateway.py` owns websocket/session concerns.

Primary API:

```python
async def process_user_message(
    user_text: str,
    user_name: str = "user",
    action_names: Optional[list[str]] = None,
    mode: str = "single_step",
    max_steps: int = 1,
) -> list[dict]
```

Key behavior:

1. Sets the allowed action subset with `set_wf_action_space(action_names)`.
2. Appends the user message to durable workflow history through `infra.prepare_user_input_for_agent(...)` and `update_history(...)`.
3. Builds the same style of prompt as `TurnBasedWorkflow`, including compact context, schema, infrastructure description, behavior guidance, and workflow rules.
4. Calls `actor.get_structured_output_async(...)` when structured-output capability is available.
5. Uses `asyncio.to_thread(actor.format_agent_response, ...)` for the synchronous JSON-format fallback path.
6. Normalizes and validates with `BaseWorkflow.normalize_and_validate_agent_response(...)`.
7. Executes the concrete action via `action_obj.execute(infra=self.infra)`.
8. Emits transport-safe events for the gateway/TUI.

Supported modes:

- `single_step` — one validated actor action per websocket user message.
- `wolf_loop` — continue while turn routing stays with an assistant/system actor, stopping on `send_message`, yield to user, max steps, or error.

Initial safe gateway action allowlist:

- `send_message`
- `read_file`
- `check_context_utilization`
- `list_memory_categories`

## 5. Base Workflow Responsibilities

**File:** `framework/workflows/base_workflow.py`

Important responsibilities:

- Session loading and state transfer.
- Workflow prompt/rules/behavior loading.
- Action-space selection.
- Agent response normalization and validation.
- History updates.
- Automatic session snapshot saving.
- Resume helpers.

Important functions/methods:

### `normalize_payload(payload, actor)`

Normalizes action payloads before Pydantic validation.

Notable behavior:

- Removes keys with `None` values.
- Removes empty/falsy `yield_motion_to`.
- Adds default `sender` and `receiver` fields to `send_message` payloads if absent.

### `BaseWorkflow.__init__()`

If `session is None`, creates a workflow session data object from:

- `infra`
- `actions_union`
- workflow rules file
- workflow behavior file
- workflow system prompt file
- workflow user/turn metadata
- full schema string

Then calls:

```python
self.load_session_state()
```

### `load_session_state()`

Loads state from a session object into the workflow instance.

It extracts:

- infrastructure
- main agent
- workers
- objects
- roles
- chat manager
- memory manager
- context manager
- actions union
- full schema string
- workflow prompts/rules/behavior

It loads these files by default:

- `config/preferences/rules/workflow/basewf.md`
- `config/preferences/behaviour/workflow/basewf.md`
- `config/preferences/prompts/workflow/basewf_default_assistant_sys_prompt.md`

### `update_history()`

Central method for adding workflow events to chat/history.

Behavior:

1. Calls `infra.append_chat_history(...)`.
2. Sends new entries to memory manager:
   - `self.memory_manager.process_new_entries(new_entries)`
3. Saves a session snapshot after every update:
   - `self.save_session_state()`

### `save_session_state()`

Writes JSON snapshot:

```text
{session_dir}/session.snapshot.json
```

### `set_wf_action_space(action_names)`

If `action_names` is provided, calls:

```python
get_actions_subset(action_names)
```

Otherwise restores the full `Actions` union and schema string.

Note: The current `get_actions_subset()` implementation in `workflow_models.py` should be reviewed. It appears intended to build a subset union but currently assigns `SubsetUnion = Union` rather than `Union[tuple(matching_classes)]`.

---

## 6. Action Model

### Base action class

**File:** `framework/workflows/base_agent_action.py`

Class:

```python
class AgentAction(BaseModel):
```

Fields:

- `action`: discriminator string.
- `description`: human-readable description.
- `payload`: action parameters.
- `payload_schema`: schema shown to the LLM.
- `yield_motion_to`: optional turn-routing target.

Default behavior:

```python
def execute(self, infra: Any = None) -> Any:
    return None
```

Concrete actions override `execute()`.

### Dynamic action discovery

**File:** `framework/workflows/workflow_models.py`

This file dynamically discovers all concrete action classes by:

1. Importing all modules under:
   - `framework.workflows.agent_actions`
2. Recursively walking subclasses of `AgentAction`.
3. Sorting action classes by their `action` field default.
4. Building a Pydantic discriminated union:

```python
Actions = Annotated[_ActionsUnion, Field(discriminator="action")]
```

It also builds:

- `ACTIONS`: dict of action name to action class.
- `ACTION_NAMES`: list of action names.
- `ACTION_SPACE_PROMPT`: short name/description listing.
- `SCHEMA_STRING`: full natural-language schema prompt.
- `AGENT_ROLE_PROMPT`: default assistant role/action-format prompt.
- `SYS_PROMPT`: role prompt plus schema string.

### Adding a new action

To add a new workflow action:

1. Add or edit a module in `framework/workflows/agent_actions/`.
2. Define a class inheriting from `AgentAction`.
3. Give it a unique literal/default `action` discriminator.
4. Define a Pydantic payload model if needed.
5. Override `execute(self, infra)` if the action should have side effects.
6. Because `workflow_models.py` imports all action modules dynamically, the new action should be auto-discovered when the app starts.

---

## 7. Current Action Modules

Directory:

```text
framework/workflows/agent_actions/
```

Observed modules:

### `io_actions.py`

Defines file IO actions.

Key actions:

- `read_file`
- `write_file`

Uses utilities from:

- `framework.utils.io_tools.read_file`
- `framework.utils.io_tools.write_file`

Important schema note:

- `write_file` expects `file_path`, not `filename`.

### `system_actions.py`

Defines syscall/terminal execution actions.

Key action:

- `run_syscall`

### `messaging_actions.py`

Defines message-sending action.

Key action:

- `send_message`

Supports optional references imported from `referencing_actions.py`:

- audio references
- image references
- video references
- file references

### `referencing_actions.py`

Defines reference models for files/media:

- `AudioReference`
- `ImageReference`
- `VideoReference`
- `FileReference`

### `ctx_mem_management_actions.py`

Defines memory and context-management actions.

Examples:

- `create_memory_fragment`
- `recall_memory`
- `forget_memory`
- `clear_memory_category`
- `list_memory_categories`
- `batch_forget_memory`
- `rename_memory_category`
- `check_context_utilization`
- `optimize_context_window`
- `set_context_monitoring`
- `summarize_context`
- `semantic_recall`

### `ctx_window_advanced_actions.py`

Defines more advanced context-window operations.

Examples:

- `truncate_context_window`
- `filter_context_window`
- `force_context_rebuild`
- `selective_context_summarization`

### `base_universe_interactions.py`

Defines discovery and status actions for universes/actionboxes.

Examples:

- `get_list_known_universes`
- `universe_info`
- `universe_health`
- `universe_stats`
- `universe_list_tools`

### `deployment_actions.py`

Defines actions for creating/managing deployments.

Examples:

- `create_universe`
- `list_deployments`
- `terminate_deployment`

### `universe_kb_interactions.py`

Defines knowledgebase interactions inside universes.

Examples:

- `create_kb`
- `universe_kb_search`
- `universe_kb_append_texts`
- `universe_kb_add_url`
- `universe_kb_add_urls`
- `universe_kb_add_document`
- `universe_kb_stats`
- `universe_kb_sources`
- `universe_kb_purge`
- `universe_kb_get_document`

### `universe_tb_interactions.py`

Defines toolbox/tool interactions inside universes.

Examples:

- `create_toolbox`
- `universe_tb_search_tools`
- `universe_tb_execute`
- `universe_tb_tool_info`
- `universe_tb_list_tools`
- `universe_tb_search_docs`
- `universe_tb_stats`
- `universe_tb_append_docs`

### `playbook_actions.py`

Defines playbook/workplan deployment tracking actions.

Examples:

- `run_playbook`
- `validate_workplan`
- `itemize_workplan`
- `modify_task`
- `run_task`
- `end_task_run`
- `conclude_workplan_deployment`

---

## 8. Infrastructure Layer

### Main infrastructure class

**File:** `framework/infrastructure/base_infrastructure.py`

Class:

```python
class BaseInfrastructure:
```

The infrastructure is the runtime/sandbox interface exposed to the workflow and actions. It owns agents, objects, managers, context state, and local command parsing.

### Constructor inputs

Important parameters:

- `agent`: main agent.
- `workers`: worker agents.
- `objects`: universes, KBs, TBs, etc.
- `max_ctx_tokens`
- `wf_log_dir`
- `session_dir`
- `chat_block_divider`
- `schema_string`
- `chat_manager`
- `memory_manager`
- `context_manager`
- `traces_vector_store`
- `summaries_vector_store`
- `db_client`
- `infra_description_file`
- `input_processor`
- `input_processor_config`

Default infrastructure description file:

```text
framework/infrastructure/config/base_infra_description.md
```

### Runtime object registries

`BaseInfrastructure` classifies passed `objects` into:

- `self.UNIVs`: universes/actionboxes.
- `self.KBs`: knowledgebases.
- `self.TBs`: toolboxes.

It supports object types including:

- `BaseUniverse`
- `BaseUniverseParams`
- `KnowledgeBase`
- `KnowledgeBaseParams`
- `MultimodalKnowledgeBase`
- `MultimodalKnowledgeBaseParams`
- `ToolBox`

### Roles and workflow members

Initial roles include:

```python
self.ROLEs = {
    "system": "system",
    "sys": "system",
    self.agent.name: "assistant",
}
```

Members:

- `self.WF_MEMBERS`
- `self.WF_ASSISTANTS`
- `self.workers`
- `self.workers_names`
- `self.NON_SYS_ROLES`

### Context/history state

Important fields:

- `FULL_CTX`: complete structured context entries.
- `FULL_CTX_TOKENS`: token count estimate.
- `chat_history`: chat-formatted history used in prompts/display.
- `CTX`: printable context string.
- `HEADER`: initial context header.
- `HEADER_IDX`: index after header.
- `CONSOLE_HEAD`: tracks what has already been displayed.

### `append_chat_history()`

Central infrastructure method for appending entries.

Behavior:

1. Computes actor role and alias.
2. Appends to `FULL_CTX`.
3. Updates token count.
4. Appends formatted entry to `chat_history`.
5. Updates printable `CTX`.
6. Persists entry into `chat_manager.CHAT_HISTORY`.
7. Updates `context_manager` incrementally.
8. Triggers context rebuild if utilization threshold is exceeded.
9. Logs to console if requested.

### `process_user_input()`

Parses special CLI commands before sending user input to agents.

Supported command categories:

#### Exit commands

- `exit`
- `quit`
- `/bye`
- `/exit`
- `/quit`

#### Clear commands

- `clear`
- `cls`

#### WOLF commands prefixed by `\>`

Examples:

```text
\>show chat
\>show history
\>show context
\>show ctx
```

#### Terminal commands prefixed by `!>`

Example:

```text
!> ls -la
```

Runs via `subprocess.run(..., shell=True, timeout=30)`.

#### Old-style slash commands

Examples:

```text
/show chat
/quit
/clear
```

#### Interlocutor routing with `@`

Example:

```text
@worker_name do something
```

Routes the next turn to a named workflow member if present in `WF_MEMBERS`.

### Multimodal input handling

Important methods:

```python
def prepare_user_input_for_agent(self, user_prompt: str, agent: Any = None)
def consume_pending_agent_content(self)
```

Behavior:

- Parses inline input/file tags via `MultimodalInputProcessor`.
- Stores compact history-safe text in chat history.
- Keeps rich content such as base64 images only in pending in-memory state for the next agent call.
- Clears pending content after consumption.

---

## 9. Managers

The session creates three important managers.

### Chat manager

**File:** `framework/infrastructure/base_chat_manager.py`

Used by infrastructure and workflow to:

- store `CHAT_HISTORY`
- log console output
- snapshot/restore chat state

### Memory manager

**File:** `framework/infrastructure/base_memory_manager.py`

Used to:

- process new chat entries
- store and recall memory fragments
- interact with traces and summaries vector stores
- snapshot/restore memory state

Created in `setup_cli_session()` with:

```python
MemoryManager(
    memory_path=os.path.join(session_dir, "memory.json"),
    traces_vector_store=traces_vs,
    summaries_vector_store=summaries_vs,
)
```

### Context manager

**File:** `framework/infrastructure/base_context_manager.py`

Used to:

- maintain compacted context
- track token utilization
- decide when to rebuild
- rebuild context from recent chat, memory, and traces
- snapshot/restore context state

Created in `setup_cli_session()` with defaults similar to:

```python
ContextManager(
    max_ctx_tokens=200000,
    recent_chat_ratio=0.50,
    memory_ratio=0.30,
    trace_ratio=0.20,
    traces_vector_store=traces_vs,
)
```

---

## 10. Data Store / Knowledge / Tools / Universes

The infrastructure description summarizes the stack as a hierarchy:

### Vector Store

**File:** `framework/data_store/vstore.py`

Chroma-backed vector storage for embeddings, retrieval, and ingestion.

Default vector store params are imported in `config_tools.py` from:

**File:** `framework/data_store/default/params/vstore_params.py`

Names:

- `Default_summaries_vs_params`
- `Default_traces_vs_params`

### Knowledgebase

Files:

- `framework/knowledgebase/knowledge_base.py`
- `framework/knowledgebase/base_multimodal_knowledgebase.py`
- `framework/knowledgebase/data_models.py`

Knowledgebases combine vector storage with inventory/metadata for traceable document management.

### Tools and ToolBoxes

Files:

- `framework/tooling/tools.py`
- `framework/tooling/toolbox.py`
- `framework/tooling/tool_models.py`

A `Tool` describes and executes language-agnostic tools. A `ToolBox` indexes/manages multiple tools and enables discovery, docs search, and execution.

### Universes / ActionBoxes

Files:

- `framework/universes/base_universe.py`
- `framework/universes/data_models.py`
- `framework/universes/universe_tools.py`
- `framework/universes/run_universe.py`
- `framework/universes/remote_deployment.py`

Universes/actionboxes are sandbox environments that can host KBs, TBs, and actions. They may be local, containerized, remote, or otherwise isolated.

Important helper used during interactive setup:

```python
get_base_universe_params(host=..., port=..., verbose=...)
```

---

## 11. Persistence and Resume

### Session directory

New sessions are created under:

```text
wf_workspace/session_YYYYMMDD_HHMMSS
```

### Snapshot file

Workflow state is saved to:

```text
{session_dir}/session.snapshot.json
```

`BaseWorkflow.update_history()` saves state after every history update.

### Snapshot contents

`BaseWorkflow.create_session_snapshot()` includes:

- infrastructure snapshot
- workflow user/turn state
- workflow config file paths
- role prompt and schema string
- rules and behavior text
- timestamp
- session directory
- minimal agent/worker/object info

`BaseInfrastructure.snapshot()` includes:

- chat manager snapshot
- context manager snapshot
- memory manager snapshot
- `FULL_CTX`
- `FULL_CTX_TOKENS`
- `chat_history`
- `CTX`
- `HEADER`
- `HEADER_IDX`
- `CONSOLE_HEAD`
- roles and members
- session/log configuration
- infrastructure description file path

### Resume entry points

Two resume-related paths exist:

1. `load_existing_session()` in `framework/utils/config_tools.py`
2. `_resolve_session_path()` / `_load_session_snapshot()` in `framework/workflows/base_workflow.py`

`load_existing_session()` reconstructs:

- agents
- universes
- Chroma client
- summaries/traces vector stores
- chat manager
- memory manager
- context manager
- infrastructure
- workflow

Supported resume identifiers include:

- `last`
- `latest`
- `recent`
- full snapshot path
- `wf_workspace/...` path
- `session_YYYYMMDD_HHMMSS`
- date-like identifiers
- partial matching session strings

---

## 12. Prompt / Rules / Behavior Files

Default workflow preference files loaded by `BaseWorkflow`:

### Workflow rules

```text
config/preferences/rules/workflow/basewf.md
```

Loaded by:

```python
update_workflow_rules()
```

### Agent behavior / best practices

```text
config/preferences/behaviour/workflow/basewf.md
```

Loaded by:

```python
update_agent_behaviour()
```

### Workflow agent system prompt

```text
config/preferences/prompts/workflow/basewf_default_assistant_sys_prompt.md
```

Loaded by:

```python
update_workflow_agent_sys_prompt()
```

### Infrastructure description

```text
framework/infrastructure/config/base_infra_description.md
```

Loaded by:

```python
BaseInfrastructure.update_infra_description()
```

These files are embedded into the runtime prompt sent to agents during `_handle_actor_turn()`.

---

## 13. Important Runners and Adjacent Systems

Directory:

```text
runners/
```

Observed runner files:

- `interactive.py` — current primary interactive launcher.
- `interactive_instructor.py`
- `interactive_old.py`
- `interactive_v1.py`
- `interactive_v2.py`
- `async_api_server.py`
- `run_gateway_demo.py`
- `run_gateway_tui.py`
- `run_orchestrator_demo.py`

Other notable app areas:

### Gateway

Important files and directories:

```text
framework/pack/gateway.py        # FastAPI gateway used for websocket workflow sessions
framework/gateway/               # Adjacent gateway/client/server support modules
framework/ui/tui_client.py       # Terminal client for the gateway
```

`framework/pack/gateway.py` now creates one WOLF runtime bundle per gateway session instead of storing only an `OpenAIAgent`. Runtime creation reuses:

```python
setup_cli_session(..., workflow_cls=GatewayActionWorkflow)
```

Each runtime bundle contains the main agent, `GatewayActionWorkflow`, infrastructure, managers, config, session directory, Chroma client, and a per-session `asyncio.Lock`.

For websocket `chat` messages, the gateway:

1. emits `user_echo` for UI responsiveness;
2. acquires the session lock;
3. calls `wf.process_user_message(...)` with the configured action allowlist and mode;
4. emits returned workflow events;
5. releases the lock.

The lock is required because workflow state, agent context, context/memory/chat managers, and snapshots are mutable.

### UI

Directory:

```text
framework/ui/
```

Contains GUI/TUI client implementations. `framework/ui/tui_client.py` handles the legacy gateway event types:

- `system`
- `user_echo`
- `agent_response`
- `error`
- `ping` / `pong`

It also renders the workflow event types emitted by `GatewayActionWorkflow` through the gateway:

- `workflow_status`
- `workflow_action`
- `workflow_result`
- `workflow_error`

`send_message` workflow results are rendered as assistant chat bubbles. Non-chat actions and results are rendered as action/result panels.

### Orchestration

Directory:

```text
framework/orchestration/
```

Contains a larger/possibly newer orchestration runtime, actions, agents, events, repository, monitoring, policies, and websocket server. This appears adjacent to, or an evolution of, the workflow system but has not yet been traced in detail.

### Pack

Directory:

```text
framework/pack/
```

Contains gateway packaging variants and requirements.

---

## 14. Practical Development Notes

### Running the interactive app

```bash
python runners/interactive.py
```

Make sure `.env` contains valid inference settings, especially:

```text
INFERENCE_HOST_ADDRESS=...
INFERENCE_HOST_PORT=...
LOCAL_API_KEY_VAR=...
API_VERSION=...
LLM_MODEL=...
LLM_CAPABILITIES=...
```

The actual API key variable referenced by `LOCAL_API_KEY_VAR` should also be present.

### Adding or changing actions

Action changes usually belong under:

```text
framework/workflows/agent_actions/
```

Remember:

- The JSON schema shown to agents is generated at import/startup time.
- The action discriminator must be unique.
- The payload schema must match what the agent will emit.
- `execute(self, infra)` receives the active infrastructure instance.

### Modifying files safely

The workflow’s best-practice prompt says to ask before modifying user files and to make `.wfbk` backups where appropriate. If the user explicitly asks for a new file or an edit, proceed but consider backing up existing files first.

### Debugging context/history

Useful CLI commands during interactive sessions:

```text
\>show chat
\>show history
\>show context
\>show ctx
```

Useful shell escape:

```text
!> pwd
!> ls -la
```

### Routing to a worker/interlocutor

If workers are configured, the user can route input with:

```text
@worker_name message
```

The target must be listed in `infra.WF_MEMBERS`.

---

## 15. Known Areas for Further Investigation

This document should be expanded as we inspect more of the repository. Important next areas:

1. `framework/agentic/agents.py`
   - Understand `OpenAIAgent`, structured output, and fallback formatting.
2. `framework/utils/io_tools.py`
   - Understand file IO, syscall implementation, env loading, and console behavior.
3. `framework/infrastructure/base_chat_manager.py`
   - Understand logging format and snapshot details.
4. `framework/infrastructure/base_memory_manager.py`
   - Understand memory categories, summaries, traces, and vector-store integration.
5. `framework/infrastructure/base_context_manager.py`
   - Understand context compaction/rebuild algorithms.
6. `framework/utils/multimodal_input.py`
   - Understand `<input> ... <input/>` syntax and provider-specific multimodal formatting.
7. `framework/universes/base_universe.py`
   - Understand how universes expose API actions and host KBs/TBs.
8. `framework/tooling/toolbox.py` and `framework/tooling/tools.py`
   - Understand tool discovery and execution.
9. `framework/knowledgebase/*`
   - Understand document ingestion, inventory, search, and multimodal KB behavior.
10. `framework/orchestration/*`
   - Determine whether this is a newer orchestration architecture, experimental subsystem, or production path.
11. `framework/workflows/workflow_models.py:get_actions_subset()`
   - Review and likely fix subset union construction.
12. Resume/session loading path
   - Compare `config_tools.load_existing_session()` with `BaseWorkflow._load_session_snapshot()` and decide whether both are needed.

---

## 16. Current High-Level Call Graph

```text
runners/interactive.py
    -> copy config/session/default/params/inputs.py:session_params
    -> import config/defaults/inference_engine.py:LLM
    -> CliSession(session_params=session_inputs)
        -> CliSession.create_session(resume_session=None)
            -> setup_cli_session(...)
                -> load_session_certs(...)
                -> show_banner(...)
                -> create_session_dir()
                -> chromadb.Client(...)
                -> build_list_agents(...)
                    -> OpenAIAgent(...)
                -> build_list_universes(...)
                    -> get_base_universe_params(...)
                -> VectorStore(...) for summaries
                -> VectorStore(...) for traces
                -> BaseChatManager(...)
                -> MemoryManager(...)
                -> ContextManager(...)
                -> BaseInfrastructure(...)
                -> TurnBasedWorkflow(...)
                    -> BaseWorkflow.__init__(...)
                        -> BaseWorkflow.load_session_state()
    -> cli_session.session['wf'].run(user_name='user')
        -> TurnBasedWorkflow.run(...)
            -> user turn
                -> interactive_input_line_wrapped(...)
                -> infra.process_user_input(...)
                -> infra.prepare_user_input_for_agent(...)
                -> update_history(...)
            -> agent turn
                -> _handle_actor_turn(...)
                    -> context_manager.get_compacted_context()
                    -> build prompt with schema/rules/behavior/infra description
                    -> actor.get_structured_output(...) or fallback JSON formatting
                    -> normalize_and_validate_agent_response(...)
                    -> action_obj.execute(infra=infra)
                    -> route next turn
```

---

## 17. Glossary

### Agent

An LLM-backed actor, usually `OpenAIAgent`, that receives context and emits JSON actions.

### Worker

A secondary agent configured from additional LLM entries. Workers participate in the same workflow and can be targeted by turn routing.

### System

The local sandbox/interface through which the agent performs actions.

### Infrastructure

The runtime object that exposes local state, managers, universes, KBs, TBs, tools, history, context, and command parsing.

### Action

A Pydantic model subclassing `AgentAction`. Agents must respond with one JSON action object matching one of these schemas.

### Universe / ActionBox

A sandboxed environment connected to the system. It can host knowledgebases, toolboxes, and executable actions. It may be local or remote.

### KnowledgeBase / KB

A searchable document/memory store backed by vector storage and metadata inventory.

### ToolBox / TB

A collection of discoverable and executable tools, with documentation search.

### Context Manager

Maintains and compacts the prompt context sent to agents.

### Memory Manager

Stores, recalls, summarizes, and semantically indexes memory/context fragments.

### Chat Manager

Stores chat history and console logs, and supports snapshot/restore.


---

## 18. Memory and Context Management Improvements

The memory/context system now follows a stronger separation between durable history and active LLM context.

### Design principle

- Full chat/session history remains durable and authoritative.
- Active context is a curated working view and may be compacted.
- Compression removes information from active context only; it should not delete durable memory/history.
- Forgetting/deletion remains explicit and confirmation-gated where actions expose it.

### Important files

- `framework/infrastructure/base_context_manager.py`
  - Maintains active context buffer.
  - Tracks context utilization and cognitive load state.
  - Maintains context manifest/ledger.
  - Supports pinned context entries.
  - Supports the working-memory packet.
  - Supports context policy metadata.
  - Provides context snapshot/restore.

- `framework/infrastructure/base_memory_manager.py`
  - Maintains structured durable memory categories.
  - Provides category/key memory operations.
  - Supports compatibility upgrades for older sessions/snapshots.

- `framework/infrastructure/base_infrastructure.py`
  - Appends chat history and now stores `action` plus `history_index` in `chat_manager.CHAT_HISTORY` entries.
  - Calls `context_manager.should_rebuild()` as the automatic safety brake.

- `framework/workflows/agent_actions/ctx_window_advanced_actions.py`
  - Defines agent actions for manifest, pinning, promotion, audit, policy, and working-memory operations.

### Context manifest / ledger

`ContextManager.context_manifest` describes the current active context as a machine-readable view over durable state. It includes:

- raw active entries
- summarized ranges
- pinned entries
- memory references
- omitted ranges
- retrieval hints
- working-memory packet
- compression provenance
- current context policy

Useful methods:

- `build_context_manifest(chat_history=None)`
- `list_context_manifest()`
- `audit_context_integrity()`

### Pinned context

Pinned entries are active-context entries that rebuilds should preserve when possible.

Useful methods:

- `pin_context_entry(entry_id=None, history_index=None, reason=None, label=None)`
- `unpin_context_entry(entry_id)`
- `list_pinned_entries()`

### Working-memory packet

The working-memory packet is a compact always-relevant state summary. It can include:

- current objective
- current plan
- current step
- active files
- modified files
- open tasks/questions
- decisions
- known bugs/warnings
- last successful action
- next recommended action

Useful methods:

- `update_working_memory_packet(**fields)`
- `get_working_memory_packet()`

When present and policy allows it, the packet is inserted into active context and preserved through rebuilds.

### Context policy

`ContextManager.context_policy` currently includes:

- `profile`
- `auto_rebuild_enabled`
- `rebuild_threshold`
- `target_utilization`
- `preserve_pinned`
- `preserve_working_memory`
- optional `retrieval_hints`

Important behavior:

- `should_rebuild()` obeys `auto_rebuild_enabled`.
- Manual forced rebuilds still work even if automatic rebuild is disabled.
- `rebuild_threshold` and `target_utilization` are validated to be in `(0.0, 1.0]`.

### Cognitive-load diagnostics

`get_context_diagnostics()` includes `cognitive_load_state`:

- `sober`: 0–50%
- `caution`: 50–70%
- `impaired`: 70–80%
- `high_risk`: 80–85%
- `emergency_brake`: >85%

### New or refined actions

New context-management actions include:

- `pin_context_entry`
- `unpin_context_entry`
- `promote_context_to_memory`
- `build_context_manifest`
- `list_context_manifest`
- `audit_context_integrity`
- `set_context_policy`
- `update_working_memory_packet`
- `recall_by_memory_key`
- `recall_compressed_range`

Existing memory actions now have backing MemoryManager methods for:

- `list_memory_categories`
- `batch_forget_memory`
- `rename_memory_category`

### Validation

The first-pass implementation was smoke-tested for import/syntax validity, action discovery, memory category compatibility, context manifest generation, working-memory packet behavior, pinning, policy behavior, promotion to memory, rebuild preservation, and snapshot restore without session-file mutation.


---

## 19. `./wolf` Application Launcher

The root `./wolf` executable has been promoted from a hardcoded wrapper into a real CLI application entrypoint.

Current wrapper:

```bash
PYTHONPATH=./ uv run python -m framework.cli.wolf_app "$@"
```

Primary implementation files:

- `framework/cli/wolf_app.py` — argparse command tree and command dispatch.
- `framework/cli/config_loader.py` — default/file/CLI config merge, JSON/YAML loading, config printing.
- `framework/cli/launchers.py` — launch functions for CLI/API/TUI/GUI modes.
- `framework/cli/discovery.py` — workflow/action discovery helpers.
- `framework/cli/session_commands.py` — session list/inspect helpers.
- `sessions/example_cli_session.json` — first example launch config.

Useful commands:

```bash
./wolf --help
./wolf cli --dry-run --explain
./wolf cli --workflow TurnBasedWorkflow
./wolf cli --workflow FastTurnBasedWorkflow
./wolf cli --resume last
./wolf cli --config sessions/example_cli_session.json
./wolf workflows list
./wolf actions list --limit 10
./wolf sessions list
./wolf sessions inspect last
./wolf config print --config sessions/example_cli_session.json
./wolf config validate --config sessions/example_cli_session.json
./wolf doctor
```

Plain `./wolf` still preserves the old behavior by launching CLI mode with `TurnBasedWorkflow`.

Session construction now accepts workflow-class injection through `setup_cli_session(..., workflow_cls=...)`, `load_existing_session(..., workflow_cls=...)`, and `CliSession.create_session(..., workflow_cls=...)`. This lets the launcher select discovered workflows without editing `runners/interactive.py`.

See also the wisdom nugget [`./wisdom_nuggets/wolf_cli_app_entrypoint.nug`](./wisdom_nuggets/wolf_cli_app_entrypoint.nug).
---

## 20. Gateway Workflow Runtime

The gateway runtime now brings TurnBasedWorkflow-style structured-action semantics to websocket sessions.

### Authoritative split

`framework/pack/gateway.py` is the transport/session layer. It owns:

- authentication;
- account/session ownership checks;
- websocket connection management;
- fanout of UI events;
- runtime bundle registry;
- per-session `asyncio.Lock` concurrency control.

`framework/workflows/custom_workflows/gateway_action_workflow.py` is the orchestration layer. It owns:

- action-space selection and schema prompt construction;
- compact context retrieval;
- prompt assembly with rules, behavior, and infrastructure description;
- async structured-output calls or sync fallback through `asyncio.to_thread`;
- response normalization and Pydantic validation;
- execution of concrete `AgentAction` objects through infrastructure;
- workflow history and snapshot updates;
- `single_step` and `wolf_loop` turn policies.

### Runtime bundle shape

A gateway session is backed by a runtime bundle similar to:

```python
{
    "agent": main_agent,
    "wf": GatewayActionWorkflow(...),
    "infra": BaseInfrastructure(...),
    "managers": {
        "chat": chat_manager,
        "memory": memory_manager,
        "context": context_manager,
    },
    "config": agent_config,
    "session_dir": "wf_workspace/gateway/<account>/session_<id>",
    "db_client": db_client,
    "lock": asyncio.Lock(),
}
```

Runtime bundles are created once per gateway session and reused across websocket messages. Rebuilding a runtime for every chat message would lose context and risk corrupting persistence.

### Websocket chat flow

```text
TUI/user message
  -> websocket {type: "chat", content: ...}
  -> gateway emits user_echo
  -> gateway acquires session lock
  -> GatewayActionWorkflow.process_user_message(...)
      -> append user input to workflow history
      -> build agent prompt
      -> get structured action
      -> normalize/validate action
      -> append action to history
      -> execute action via infra
      -> save/update snapshot
      -> return workflow events
  -> gateway emits workflow events
  -> gateway releases session lock
```

### Workflow event types

Gateway workflow calls return transport-safe events:

| Event | Meaning |
| --- | --- |
| `workflow_status` | Lifecycle state such as received, thinking, done, or error. |
| `workflow_action` | Normalized action payload selected by the agent before execution. |
| `workflow_result` | Execution result summary, action name, elapsed time, next turn, and optional history delta. |
| `workflow_error` | Validation, formatting, or execution failure safe for UI transport. |

The gateway still supports legacy `system`, `user_echo`, `agent_response`, `error`, `ping`, and `pong` messages for compatibility.

### Join-session participant scaffold

The gateway now supports multiple websocket participants on one session. Internally, `ConnectionManager.account_sessions` maps account/session/participant to websocket connections, and `session_participants` tracks participant metadata.

Participant connection query parameters:

- `participant_id`
- `participant_role`
- `client_type`

Supported new websocket events:

- `presence` — participant joined/left style notifications.
- `participant_message` — non-orchestrating message broadcast from an attached participant.

The CLI exposes a first bridge command:

```bash
./wolf join-session   --gateway http://127.0.0.1:8000   --account-id <account_id>   --session-id <session_id>   --token <token>   --participant-id sad_chaplygin_clone
```

This first version is message-level and does not yet run an autonomous local WOLF workflow loop. It is the groundwork for a later joined-agent workflow where a clone can observe a session, receive instructions, test changes, report results, and eventually participate in clone-test-promote context transfer.


### Latest gateway hardening notes

The gateway workflow path has several additional hardening features:

#### Policy visibility

Before each workflow turn, `framework/pack/gateway.py` emits a `policy_resolved` event containing:

- configured `action_policy`;
- resolved action names shown to the model;
- resolved execution guardrails.

`framework/ui/tui_client.py` renders this compactly so operators can immediately see whether a session is running `safe`, `write`, or `dev` policy.

#### REST policy and participant introspection

New/updated REST surfaces:

```text
GET /sessions/{session_id}/policy
GET /sessions/{session_id}/participants
```

`/policy` reports configured policy fields, resolved action names, and execution guardrails. `/participants` reports connected/known participant metadata such as participant id, role, client type, active state, and connection time.

#### Secret redaction

Gateway and TUI outputs redact sensitive values before display or response. Redacted keys include:

- `api_key`
- `token`
- `password`
- `secret`
- `authorization`

This applies to session params, policy/config responses, configure/patch responses, `/show agent params`, and `/config agent params ...` update confirmations.

#### Fallback schema hygiene

`GatewayActionWorkflow` now avoids the old fallback pattern:

```python
actor.format_agent_response(prompt, schema)
```

because the gateway workflow prompt already includes the selected action schema. Appending a schema again risks prompt bloat and can obscure whether the model is seeing the effective restricted action space. The new helper is:

```python
GatewayActionWorkflow._format_actor_response_no_duplicate(...)
```

It calls the model with the existing workflow-built prompt and only uses the effective restricted `self.schema_to_use` in a JSON-repair prompt if needed.

#### Smoke test helper

A lightweight test utility exists at:

```text
scripts/gateway_smoke.py
```

It can:

1. log in;
2. create/configure a session;
3. inspect effective policy;
4. connect over websocket;
5. send a message;
6. assert `policy_resolved` and workflow events.

This should be used for regression checks after gateway changes.

### Turn policy

WOLF remains turn-based: the previous action decides who can act next through `yield_motion_to` or receiver routing.

For gateway MVP:

- `single_step` stops after one validated action per user message.
- `wolf_loop` may continue only while the next turn routes to an assistant/system actor and stops on `send_message`, yield to user, max steps, or error.

### Safety rollout and action policies

The websocket action allowlist is now policy-driven:

| Policy | Actions |
| --- | --- |
| `safe` | `send_message`, `read_file`, `check_context_utilization`, `list_memory_categories` |
| `write` | safe policy plus `write_file` |
| `dev` | write policy plus guarded `run_syscall` |

The gateway resolves both an action-schema allowlist and an execution policy. This means risky actions must pass two gates:

1. the model must be allowed to emit the action at schema/validation time;
2. `GatewayActionWorkflow._guard_action_execution(...)` must allow the action at execution time.

Current `run_syscall` guardrails:

- disabled unless policy explicitly enables it;
- `shell=True` blocked by default;
- shell composition/metacharacters blocked;
- timeout capped;
- command allowlist defaults to simple diagnostics/read-only commands such as `pwd`, `ls`, `cat`, `head`, `tail`, `grep`, `find`, `wc`, and `echo`.


