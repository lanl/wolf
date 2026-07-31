# WOLF

Historically, WOLF has meant the **Workflow Orchestration Language Framework**. As the project evolves, WOLF also names the **Workflow Orchestration Learning Framework**: a philosophy and runtime direction for agents that learn from workflows, tools, users, environments, and their own operational traces.

WOLF is an agentic AI framework for building interactive, turn-based workflows where users, LLM-backed agents, worker agents, tools, knowledgebases, and sandboxed execution environments collaborate through structured actions.

Much of the repository still uses **WOLF** terminology in code, prompts, configuration, commands, and documentation. In practice, this repository should be understood as the active development branch of the WOLF-style runtime.

---

## Table of Contents

1. [What is WOLF?](#what-is-wolf)
2. [WOLF Philosophy](#wolf-philosophy)
3. [Core Mental Model](#core-mental-model)
4. [Repository Highlights](#repository-highlights)
5. [Quick Start](#quick-start)
6. [Running the Application](#running-the-application)
7. [Interactive CLI Usage](#interactive-cli-usage)
8. [Gateway / TUI Workflow Runtime](#gateway--tui-workflow-runtime)
9. [Architecture Overview](#architecture-overview)
10. [Structured Actions](#structured-actions)
11. [Infrastructure Layer](#infrastructure-layer)
12. [Memory and Context Management](#memory-and-context-management)
13. [Universes / ActionBoxes](#universes--actionboxes)
14. [Knowledgebases, Toolboxes, and Vector Stores](#knowledgebases-toolboxes-and-vector-stores)
15. [Sessions, Persistence, and Resume](#sessions-persistence-and-resume)
16. [Configuration](#configuration)
17. [Prompt, Rules, and Behavior Files](#prompt-rules-and-behavior-files)
18. [Developer Notes](#developer-notes)
19. [Living Documentation](#living-documentation)
20. [License](#license)

---

## What is WOLF?

WOLF provides a composable runtime for agentic workflows. Instead of allowing agents to respond with arbitrary free text, the active workflow expects agents to emit validated JSON actions. Those actions are discovered dynamically from the framework, validated with Pydantic models, executed through the local infrastructure layer, and routed back to the user, system, another agent, or an external sandbox.

The framework supports:

- **Interactive human-agent workflows** through a CLI application.
- **Structured agent actions** validated against dynamically discovered schemas.
- **Session persistence and resume** with workflow snapshots.
- **Memory and context management** for long-running sessions.
- **Knowledgebases** backed by vector stores and metadata inventory.
- **Toolboxes** for discoverable and executable tools.
- **Universes / ActionBoxes** as external or nested sandbox environments.
- **Workflow selection** through the root `./wolf` application launcher.
- **Agent-to-agent routing** when worker agents are configured.


---

## WOLF Philosophy

Beyond the runtime described in this README, WOLF is also a philosophy for building self-evolving agentic systems.

Current agent frameworks often focus on connecting LLMs to tools, prompts, skills, and workflow glue. WOLF aims to go further: it asks how agents can learn from the environments in which they act, preserve reusable operational wisdom, diagnose their own failure modes, improve their policies, practice through self-play, and co-evolve with their infrastructure.

The guiding thesis is:

> Agency is the recursive reduction of impedance to solution search.

In this view, an agent is not only a task executor. It is a participant in the improvement of the workflows, tools, memories, evaluations, and environments that make future solutions easier to find.

The public living philosophy document is here:

- [philo.md](./philo.md) — WOLF as an open invitation to build self-evolving agentic systems.

We welcome revisions, critiques, missing failure modes, new capability levels, safety concerns, implementation experiments, and alternative framings.

---

## Core Mental Model

At runtime, the system is organized around a few key concepts:

| Concept | Meaning |
| --- | --- |
| **Agent** | An LLM-backed actor, usually an `OpenAIAgent`, that receives workflow context and emits JSON actions. |
| **User** | The human participant in the workflow. |
| **System** | The local sandbox/interface through which actions are executed. |
| **Infrastructure** | The runtime object that exposes agents, objects, managers, history, context, memory, universes, KBs, and TBs. |
| **Action** | A Pydantic model subclassing `AgentAction`. Agents must respond with one valid action object. |
| **Workflow** | The orchestration loop that routes turns between users, agents, workers, and the system. |
| **Universe / ActionBox** | A sandboxed environment that can host knowledgebases, toolboxes, and executable actions. |
| **KnowledgeBase / KB** | A searchable document or memory store, typically backed by vector storage. |
| **ToolBox / TB** | A collection of discoverable and executable tools. |
| **Memory Manager** | Stores and recalls durable memory fragments and summaries. |
| **Context Manager** | Maintains the compact active context sent to agents. |
| **Chat Manager** | Stores chat history, console output, and snapshot state. |

A useful high-level flow is:

```text
User input
  -> TurnBasedWorkflow
  -> BaseInfrastructure
  -> ContextManager / MemoryManager / ChatManager
  -> Agent prompt with allowed action schema
  -> Agent emits JSON action
  -> Action is validated
  -> Action executes through infrastructure
  -> Workflow routes the next turn
```

---

## Repository Highlights

```text
./wolf                                      # Main CLI application wrapper
README.md                                  # This document
philo.md                                   # Public living philosophy for WOLF self-evolving agents
app.md                                     # Broad living architecture notes
wisdom.md                                  # Index of focused wisdom nuggets
wisdom_nuggets/                            # Source-grounded development notes
runners/interactive.py                     # Traditional interactive launcher
config/defaults/inference_engine.py        # Default LLM/provider configuration
config/session/default/params/inputs.py    # Default session parameters
framework/cli/                             # Real ./wolf CLI application implementation
framework/utils/config_tools.py            # Session construction and resume helpers
framework/infrastructure/                  # Runtime infrastructure, chat, memory, context
framework/workflows/                       # Workflow base classes, action models, active workflows
framework/workflows/custom_workflows/gateway_action_workflow.py  # Async websocket workflow/action runtime
framework/workflows/agent_actions/         # Dynamically discovered workflow actions
framework/agentic/                         # Agent implementations
framework/data_store/                      # Vector store layer
framework/knowledgebase/                   # KnowledgeBase implementations
framework/tooling/                         # Tools and ToolBox implementations
framework/universes/                       # Universe / ActionBox support
framework/gateway/                         # Gateway client/server/TUI support
framework/ui/                              # UI-related clients
framework/orchestration/                   # Adjacent or evolving orchestration subsystem
sessions/                                  # Example launch/session configs
wf_workspace/                              # Runtime session directories, snapshots, stores
```

---

## Quick Start

### 1. Clone the repository

```bash
git clone ssh://git@re-git.lanl.gov:10022/mada/wolf.git
cd wolf
```


---

### 2. Install the environment
WOLF can be installed using several Python environment workflows. The older Conda-only setup is still supported, but the repository now includes `pyproject.toml`, so `uv`, `pip`, and other PEP 517/518-compatible tools can be used as well.

Python 3.13 or newer is recommended unless your branch or deployment environment specifies otherwise.

#### 2.1 Option A: Using `uv` recommended for local development

If you have `uv` installed, this is usually the fastest way to create and manage a local development environment:

```bash
uv sync
```

Then run commands through `uv`:

```bash
uv run ./wolf --help
uv run ./wolf doctor
uv run ./wolf
```

The root `./wolf` wrapper may already invoke `uv` internally depending on the current checkout, so in many cases this is enough:

```bash
./wolf
```

If you need to include optional dependency groups, use the project/team convention for this repository, for example:

```bash
uv sync --all-extras
```

#### 2.2 Option B: Using `pip` with a virtual environment

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install the project from `pyproject.toml`:

```bash
pip install -e .
```

If the project defines optional extras, install the ones you need, for example:

```bash
pip install -e '.[dev]'
```

Then launch the app:

```bash
./wolf
```

or:

```bash
python -m framework.cli.wolf_app --help
```

#### 2.3 Option C: Using Conda / Anaconda

The historical setup used Conda and is still a valid option, especially on shared systems where Conda is the standard environment manager.

```bash
conda create -n wolf python=3.13
conda activate wolf
```

Then install the project:

```bash
pip install -e .
```

If you are working from an older checkout or need to manually install the legacy dependency set, use:

```bash
pip install dotenv searxng_wrapper rich openai funkybob tiktoken pdfplumber nbformat alive_progress prompt_toolkit chromadb fastapi dill
```

If the repository-provided `environment.yml` is the preferred team workflow for your branch, you can instead create the environment from it:

```bash
conda env create -f environment.yml
conda activate wolf
```

#### 2.4 Option D: Existing managed environment

On managed systems, shared development machines, or containerized deployments, you may already have a compatible Python environment. In that case, activate the environment according to local site instructions, then install the project if needed:

```bash
pip install -e .
```

Verify the CLI is available:

```bash
./wolf --help
./wolf doctor
```

---

### 3. Configure environment variables

Create a local `.env` file from the sample if available:

```bash
cp sample.env .env
```

Configure inference settings for one default LLM such as:

```text
INFERENCE_HOST_ADDRESS=...
INFERENCE_HOST_PORT=...
LOCAL_API_KEY_VAR=...
API_VERSION=...
LLM_MODEL=...
LLM_CAPABILITIES=...
```

The default loader also supports multiple LLMs from `.env`. Use indexed `LLM_N_*` variables to create one agent per entry. The first entry becomes the main agent and later entries become worker agents. Missing per-entry provider fields inherit the single/default provider settings above.

```text
LLM_1_NAME=main
LLM_1_MODEL=model-a
LLM_1_HOST_ADDRESS=https://example-llm-host
LLM_1_LOCAL_API_KEY_VAR=LOCAL_API_KEY
LLM_1_API_VERSION=v1
LLM_1_CAPABILITIES=['text','tool']

LLM_2_NAME=worker
LLM_2_MODEL=model-b
LLM_2_HOST_ADDRESS=https://example-llm-host
LLM_2_LOCAL_API_KEY_VAR=LOCAL_API_KEY
LLM_2_API_VERSION=v1
LLM_2_CAPABILITIES=['text']
```

As an alternative, set `LLMS_JSON` to a one-line JSON object or list. `LLMS_JSON` takes precedence over indexed `LLM_N_*` entries when set.

```text
LLMS_JSON='{ "main": {"model": "model-a", "host": "https://example-llm-host", "api_key_var": "LOCAL_API_KEY", "api_version": "v1", "capabilities": ["text", "tool"]}, "worker": {"model": "model-b", "host": "https://example-llm-host", "api_key_var": "LOCAL_API_KEY", "api_version": "v1", "capabilities": ["text"]} }'
```
or provide a path to a json file containing configuration for the different llms (refere to sample_llm_config.json) using the variable "LLMS_JSON_FILE"

`LOCAL_API_KEY_VAR` and per-entry `LLM_N_LOCAL_API_KEY_VAR` values should name the environment variable that contains your actual inference API key. For example, if:

```text
LOCAL_API_KEY_VAR=LOCAL_API_KEY
```

then `.env` or your shell environment should also contain:

```text
LOCAL_API_KEY=your_api_key_here
```

Default inference configuration is built in:

```text
config/defaults/inference_engine.py
```

---

### 4. Configure SSL certificates if needed

On Linux systems such as Rocinante, add to your shell RC file:

```bash
export CURL_CA_BUNDLE="/etc/ssl/ca-bundle.pem"
export SSL_CERT_FILE="/etc/ssl/ca-bundle.pem"
```

On macOS, standard system certificates may work with:

```bash
export CURL_CA_BUNDLE="/etc/ssl/cert.pem"
export SSL_CERT_FILE="/etc/ssl/cert.pem"
```

For Homebrew OpenSSL, one of these may be appropriate:

```bash
export CURL_CA_BUNDLE="/usr/local/etc/openssl@3/cert.pem"
export SSL_CERT_FILE="/usr/local/etc/openssl@3/cert.pem"
```

or:

```bash
export CURL_CA_BUNDLE="/usr/local/etc/openssl/cert.pem"
export SSL_CERT_FILE="/usr/local/etc/openssl/cert.pem"
```

---

## Running the Application

The preferred entrypoint is the root `./wolf` executable.

```bash
./wolf
```

Plain `./wolf` preserves the traditional behavior: launch an interactive CLI session using `TurnBasedWorkflow`.

You can also use the newer CLI command tree:

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

The traditional Python launcher still exists:

```bash
python runners/interactive.py
```

---

## Interactive CLI Usage

During an interactive session, user input is routed through the active workflow and infrastructure layer.

Useful built-in commands include:

```text
\>show chat
\>show history
\>show context
\>show ctx
```

Terminal commands can be issued with:

```text
!> ls -la
!> pwd
```

If worker agents are configured, route input to a worker with:

```text
@worker_name your message here
```

Exit commands include:

```text
exit
quit
/exit
/quit
/bye
```

Clear commands include:

```text
clear
cls
/clear
```

---

## Gateway / TUI Workflow Runtime

The gateway path now supports real WOLF structured-action execution over websocket sessions.
Instead of sending websocket chat directly to `OpenAIAgent.get_chat_response_async(...)`, the gateway creates a per-session WOLF runtime and routes chat through `GatewayActionWorkflow`.

Important files:

```text
framework/pack/gateway.py                                  # FastAPI gateway, auth, websocket transport, runtime registry
framework/workflows/custom_workflows/gateway_action_workflow.py  # Async action workflow used by gateway sessions
framework/ui/tui_client.py                                 # Terminal client with workflow-event rendering
```

### Runtime split

The gateway remains a transport/session layer. It owns:

- authentication and account/session ownership;
- websocket connection management and event fanout;
- runtime bundle creation and lookup;
- one `asyncio.Lock` per session to serialize mutable workflow state.

`GatewayActionWorkflow` owns WOLF orchestration semantics:

- user-message ingestion into workflow history;
- prompt composition using compact context, action schema, infrastructure description, behavior, and workflow rules;
- structured action generation using async structured-output APIs when available;
- synchronous JSON-format fallback through an executor/thread when needed;
- action normalization, validation, execution, history updates, and snapshots;
- turn policy for `single_step` and `wolf_loop` modes.

Each gateway session stores a runtime bundle roughly shaped as:

```python
{
    "agent": main_agent,
    "wf": GatewayActionWorkflow(...),
    "infra": BaseInfrastructure(...),
    "managers": {"chat": ..., "memory": ..., "context": ...},
    "config": agent_config,
    "session_dir": "wf_workspace/gateway/<account>/session_<id>",
    "lock": asyncio.Lock(),
}
```

### Gateway action policies

Gateway action exposure is policy-driven. The default policy is intentionally conservative:

- `safe` — `send_message`, `read_file`, `check_context_utilization`, `list_memory_categories`
- `write` — safe policy plus `write_file`
- `dev` — write policy plus guarded `run_syscall`

You can update a gateway session from the TUI with commands such as:

```text
/config agent params action_policy='dev'
/config agent params action_policy='write'
/config agent params enable_syscall=true syscall_max_timeout=5
```

`run_syscall` is still guarded at execution time. By default dev mode blocks `shell=True`, shell composition/metacharacters, dangerous commands, and commands outside a small allowlist such as `pwd`, `ls`, `cat`, `head`, `tail`, `grep`, `find`, `wc`, and `echo`.

### Websocket workflow events

For websocket chat messages, the gateway now emits:

- `user_echo` — immediate UI echo of user input;
- `workflow_status` — lifecycle updates such as `received`, `thinking`, and `done`;
- `workflow_action` — normalized action payload selected by the agent;
- `workflow_result` — action result summary and any appended history delta;
- `workflow_error` — validation or execution errors safe for transport;
- legacy `system`, `error`, `ping`, and `pong` events remain supported.

The TUI renders `send_message` workflow results as assistant chat bubbles and renders non-chat actions/results as action/result panels.

### Join-session participant scaffold

The gateway websocket layer now supports multiple participants attached to the same session. Each connection may provide a `participant_id`, `participant_role`, and `client_type`; the gateway emits `presence` events and can broadcast `participant_message` events.

A first CLI join command is available:

```bash
./wolf join-session   --gateway http://127.0.0.1:8000   --account-id <account_id>   --session-id <session_id>   --token <token>   --participant-id sad_chaplygin_clone
```

This is currently a message-level participant bridge. It is the foundation for later agent-only joined workflows, clone testing, benchmarking, and context-transfer/self-evolution experiments.

### Gateway hardening and diagnostics

Recent gateway hardening adds several operational safeguards:

- **Policy visibility:** the gateway emits a `policy_resolved` event before workflow execution so clients can see the effective policy and action allowlist.
- **Policy introspection:** `GET /sessions/{session_id}/policy` returns configured policy values plus resolved action names and execution guardrails.
- **Participant introspection:** `GET /sessions/{session_id}/participants` returns known participant metadata for a gateway session.
- **Secret redaction:** gateway parameter/config responses and TUI displays redact sensitive fields such as `api_key`, `token`, `password`, `secret`, and `authorization`.
- **Fallback schema hygiene:** `GatewayActionWorkflow` avoids appending the action schema twice in non-structured-output fallback paths; the workflow owns the effective restricted schema.
- **Smoke testing:** `scripts/gateway_smoke.py` can log in, configure a session, inspect policy, connect by websocket, send a message, and assert workflow events.

Example smoke-test shape:

```bash
python scripts/gateway_smoke.py \
  --username max \
  --password '' \
  --policy dev \
  --host-address https://example-llm-host \
  --api-key "$LOCAL_API_KEY" \
  --model gpt-5.4-nano \
  --api-version v1 \
  --message "What is the current working directory? Use run_syscall with command pwd, shell false, timeout 5."
```

## Architecture Overview

### Startup path

The current interactive startup path is:

```text
./wolf
  -> python -m framework.cli.wolf_app
  -> framework/cli launchers
  -> framework/utils/config_tools.py
  -> CliSession.create_session(...)
  -> setup_cli_session(...)
  -> TurnBasedWorkflow.run(...)
```

The traditional runner follows a similar path:

```text
runners/interactive.py
  -> load default session params
  -> load default LLM config
  -> CliSession(...)
  -> create_session(...)
  -> session['wf'].run(user_name='user')
```

The websocket gateway path is now:

```text
framework/pack/gateway.py
  -> authenticate/connect websocket session
  -> create runtime bundle once per session
  -> setup_cli_session(..., workflow_cls=GatewayActionWorkflow)
  -> on chat: acquire session asyncio.Lock
  -> GatewayActionWorkflow.process_user_message(...)
  -> emit workflow_status/workflow_action/workflow_result/workflow_error events
```

### Session construction

Session construction is handled primarily by:

```text
framework/utils/config_tools.py
```

A new CLI session typically creates:

- Main agent and optional worker agents.
- Configured universes/actionboxes.
- Chroma-backed vector stores for summaries and traces.
- `BaseChatManager`.
- `MemoryManager`.
- `ContextManager`.
- `BaseInfrastructure`.
- Active workflow, usually `TurnBasedWorkflow`.

A session dictionary contains roughly:

```python
{
    "agents": {
        "main": main_agent,
        "workers": workers,
    },
    "objects": {
        "universes": universes,
        "kbs": knowledgebases,
        "tbs": toolboxes,
    },
    "managers": {
        "chat": chat_manager,
        "memory": memory_manager,
        "context": context_manager,
    },
    "session_dir": session_dir,
    "db_client": db_client,
    "wf": workflow,
}
```

### Active workflow

The active interactive workflow is:

```text
framework/workflows/custom_workflows/turn_based_workflow.py
```

It provides the main turn-based loop. On each agent turn, the workflow:

1. Shows updated history.
2. Gets compacted context from the context manager.
3. Builds a prompt containing role instructions, context, workflow rules, behavior guidance, infrastructure description, and the allowed action schema.
4. Calls the selected agent.
5. Validates the response as a JSON action.
6. Appends the action to history.
7. Executes the action through the infrastructure layer.
8. Routes the next turn using `yield_motion_to`, `receiver`, or a system fallback.

---

## Structured Actions

Agents do not respond with arbitrary free text. They respond with JSON actions.

Action models live under:

```text
framework/workflows/agent_actions/
```

Actions are dynamically discovered by:

```text
framework/workflows/workflow_models.py
```

The framework imports action modules, walks subclasses of `AgentAction`, and builds a discriminated Pydantic union. This union becomes the schema shown to agents at runtime.

Common action areas include:

- Messaging actions, such as `send_message`.
- File IO actions, such as `read_file` and `write_file`.
- System actions, such as `run_syscall`.
- Memory and context actions.
- Universe discovery and health actions.
- Knowledgebase actions.
- Toolbox actions.
- Deployment actions.
- Playbook/workplan actions.

### Adding a new action

To add a new workflow action:

1. Add or edit a module in:

   ```text
   framework/workflows/agent_actions/
   ```

2. Define a class inheriting from `AgentAction`.
3. Give it a unique action discriminator.
4. Define a Pydantic payload model if needed.
5. Override `execute(self, infra)` if it has side effects.
6. Restart the application so dynamic discovery rebuilds the action union and schema.

Important schema detail: `write_file` expects `file_path`, not `filename`.

---

## Infrastructure Layer

The infrastructure layer is the local runtime interface used by workflows and actions.

Primary file:

```text
framework/infrastructure/base_infrastructure.py
```

`BaseInfrastructure` owns or coordinates:

- Main agent.
- Worker agents.
- Runtime objects such as universes, KBs, and TBs.
- Chat history.
- Full structured context.
- Console/log display state.
- Memory manager.
- Context manager.
- Chat manager.
- Local command parsing.
- Multimodal user input preparation.

The default infrastructure description is loaded from:

```text
framework/infrastructure/config/base_infra_description.md
```

---

## Memory and Context Management

The current memory/context system separates durable history from active LLM context.

Design principles:

- Full chat/session history is durable and authoritative.
- Active context is a curated working view.
- Context compression removes information from active context, not from durable history.
- Deletion and forgetting should be explicit and confirmation-gated where actions expose it.

Important files:

```text
framework/infrastructure/base_memory_manager.py
framework/infrastructure/base_context_manager.py
framework/workflows/agent_actions/ctx_mem_management_actions.py
framework/workflows/agent_actions/ctx_window_advanced_actions.py
```

The context manager supports:

- Context utilization diagnostics.
- Automatic rebuild thresholds.
- Context manifest / ledger generation.
- Pinned context entries.
- Working-memory packets.
- Context policy metadata.
- Snapshot and restore.

The working-memory packet may track:

- Current objective.
- Current plan.
- Current step.
- Active files.
- Modified files.
- Open tasks and questions.
- Decisions.
- Known bugs or warnings.
- Last successful action.
- Next recommended action.

---

## Universes / ActionBoxes

Universes, also called ActionBoxes, are sandbox environments connected to the system. They can host knowledgebases, toolboxes, and executable actions. They may be local, remote, containerized, or otherwise isolated.

Relevant files include:

```text
framework/universes/base_universe.py
framework/universes/data_models.py
framework/universes/universe_tools.py
framework/universes/run_universe.py
framework/universes/remote_deployment.py
```

A typical universe configuration shape is:

```python
{
    "host": "0.0.0.0",
    "port": 8115,
    "scheme": "http",
}
```

The framework can discover known universes, inspect universe health, list tools, and interact with KBs/TBs hosted inside a universe.

---

## Knowledgebases, Toolboxes, and Vector Stores

The infrastructure stack is compositional:

```text
VectorStore
  -> KnowledgeBase
  -> Tool
  -> ToolBox
  -> Universe / ActionBox
```

### VectorStore

```text
framework/data_store/vstore.py
```

Chroma-backed vector storage for embeddings, retrieval, and ingestion.

### KnowledgeBase

```text
framework/knowledgebase/knowledge_base.py
framework/knowledgebase/base_multimodal_knowledgebase.py
framework/knowledgebase/data_models.py
```

Knowledgebases combine vector storage with document inventory and metadata.

### Tool and ToolBox

```text
framework/tooling/tools.py
framework/tooling/toolbox.py
framework/tooling/tool_models.py
```

A `Tool` describes and executes a language-agnostic tool. A `ToolBox` manages multiple tools, supports discovery, documentation search, and execution.

---

## Sessions, Persistence, and Resume

New sessions are created under:

```text
wf_workspace/session_YYYYMMDD_HHMMSS
```

Workflow snapshots are saved to:

```text
wf_workspace/session_YYYYMMDD_HHMMSS/session.snapshot.json
```

The workflow saves state after history updates. Snapshots include infrastructure state, workflow state, manager state, context, memory, roles, and session metadata.

Resume identifiers may include:

```text
last
latest
recent
session_YYYYMMDD_HHMMSS
wf_workspace/session_YYYYMMDD_HHMMSS
/path/to/session.snapshot.json
```

Example:

```bash
./wolf cli --resume last
```

You can inspect sessions with:

```bash
./wolf sessions list
./wolf sessions inspect last
```

---

## Configuration

Default session inputs live in:

```text
config/session/default/params/inputs.py
```

Current active defaults include:

- `tiktoken_cache_dir`
- `banner_image_file`
- `banner_image_color`
- `banner_image_width`
- `universes`
- `kbs`
- `tbs`
- `verbose`

Default inference settings live in:

```text
config/defaults/inference_engine.py
```

The root CLI supports config inspection and validation:

```bash
./wolf config print --config sessions/example_cli_session.json
./wolf config validate --config sessions/example_cli_session.json
```

---

## Prompt, Rules, and Behavior Files

The base workflow loads user-editable preference files from:

```text
config/preferences/rules/workflow/basewf.md
config/preferences/behaviour/workflow/basewf.md
config/preferences/prompts/workflow/basewf_default_assistant_sys_prompt.md
```

The infrastructure description is loaded from:

```text
framework/infrastructure/config/base_infra_description.md
```

These files are embedded into the runtime prompt sent to agents.

---

## Developer Notes

### Important implementation files

| Area | File |
| --- | --- |
| CLI app entrypoint | `framework/cli/wolf_app.py` |
| CLI config loading | `framework/cli/config_loader.py` |
| CLI launch dispatch | `framework/cli/launchers.py` |
| Workflow discovery | `framework/cli/discovery.py` |
| Session commands | `framework/cli/session_commands.py` |
| Session construction | `framework/utils/config_tools.py` |
| Base workflow | `framework/workflows/base_workflow.py` |
| Active CLI workflow | `framework/workflows/custom_workflows/turn_based_workflow.py` |
| Gateway workflow | `framework/workflows/custom_workflows/gateway_action_workflow.py` |
| Action discovery | `framework/workflows/workflow_models.py` |
| Action base class | `framework/workflows/base_agent_action.py` |
| Infrastructure | `framework/infrastructure/base_infrastructure.py` |
| Chat manager | `framework/infrastructure/base_chat_manager.py` |
| Memory manager | `framework/infrastructure/base_memory_manager.py` |
| Context manager | `framework/infrastructure/base_context_manager.py` |

### Current known cleanup / investigation areas

The living architecture notes identify several areas worth reviewing as the codebase evolves:

- `framework/workflows/workflow_models.py:get_actions_subset()` may need correction for subset union construction.
- Resume/session loading exists in more than one place and may need consolidation.
- `framework/orchestration/` appears adjacent to, or an evolution of, the workflow system and needs deeper tracing.
- Older launchers remain in `runners/` and may be historical, experimental, or compatibility paths.

---

## Living Documentation

This repository includes living documentation intended for future humans, agents, and public contributors:

```text
philo.md
app.md
wisdom.md
wisdom_nuggets/
```

- `philo.md` is the public WOLF philosophy and invitation to contribute ideas, critiques, failure modes, capability levels, safety concerns, and standardization proposals.
- `app.md` is the broad architectural overview and current working model.
- `wisdom.md` is the registry of focused source-grounded discoveries.
- `wisdom_nuggets/*.nug` files contain narrower, reusable pieces of development knowledge.

When adding a new nugget:

1. Place it under `wisdom_nuggets/` unless another location is clearly better.
2. Include YAML front matter.
3. List source files inspected.
4. Add a registry entry to `wisdom.md`.
5. Cross-reference it from `app.md` if it changes the high-level model.

---

## License
Notice of Copyright Assertion (O5088).

Triad National Security, LLC. All rights reserved. This program was produced under U.S. Government contract 89233218CNA000001 for Los Alamos National Laboratory (LANL), which is operated by Triad National Security, LLC for the U.S. Department of Energy/National Nuclear Security Administration. All rights in the program are reserved by Triad National Security, LLC, and the U.S. Department of Energy/National Nuclear Security Administration. The Government is granted for itself and others acting on its behalf a nonexclusive, paid-up, irrevocable worldwide license in this material to reproduce, prepare derivative works, distribute copies to the public, perform publicly and display publicly, and to permit others to do so.

Modified BSD 3-Clause License 

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

4. Redistributions or derivative works must give appropriate credit to the
   original authors, including citation of the original publication or
   repository.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT INCLUDING NEGLIGENCE OR OTHERWISE ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

