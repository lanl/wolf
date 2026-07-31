# Wisdom Index

`wisdom.md` is the root index for WOLF wisdom nuggets.

A **wisdom nugget** is a source-grounded, reusable piece of understanding discovered while working in this repository. Nuggets are stored as `.nug` files and indexed here so future humans and agents can quickly find relevant knowledge without rediscovering it from scratch.

This document co-lives with `app.md`:

- `app.md` remains the broad living overview of the application architecture and current working model.
- `wisdom.md` is the searchable registry of narrower, deeper discoveries.
- Each `.nug` file should be referenced here with enough metadata and context to support discovery.

---

## Nugget conventions

Unless a more specific location is clearly better, store nuggets under:

```text
./wisdom_nuggets/
```

Recommended `.nug` format:

- Markdown-compatible text.
- YAML front matter at the top.
- A concise title and clear scope.
- Source files inspected.
- Confidence/status metadata.
- Search keywords.
- Practical implications and cleanup opportunities when useful.

Recommended front matter fields:

```yaml
---
nugget_id: short_unique_id
title: "Human-readable title"
author_agent: "agent_name"
created_at: "YYYY-MM-DD"
last_reviewed_at: "YYYY-MM-DD"
status: "active"
confidence: "source-inspected"
source_files:
  - "./path/to/source.py"
related_docs:
  - "./app.md"
  - "./wisdom.md"
tags:
  - tag
---
```

Suggested confidence vocabulary:

- `source-inspected`: confirmed by reading implementation files.
- `runtime-observed`: confirmed by actually running behavior.
- `inferred`: reasoned from code but not directly executed.
- `stale-risk`: likely true but needs review after changes.

---

## Nugget registry

### 1. Interactive CLI Session Startup Flow

- **Nugget ID:** `session_startup_flow`
- **File:** [`./wisdom_nuggets/session_startup_flow.nug`](./wisdom_nuggets/session_startup_flow.nug)
- **Author agent:** `silly_saha`
- **Created:** 2026-07-22
- **Status:** active
- **Confidence:** source-inspected
- **Primary source files:**
  - [`./wolf`](./wolf)
  - [`./runners/interactive.py`](./runners/interactive.py)
  - [`./framework/utils/config_tools.py`](./framework/utils/config_tools.py)
- **Tags:** startup, cli, session, workflow, config, runtime-construction, resume

#### Context

Captures the exact current flow when starting WOLF with the root `./wolf` wrapper. It explains how `./wolf` delegates to `runners/interactive.py`, how `CliSession` acts as a thin session wrapper, and how `setup_cli_session()` builds agents, universes, vector stores, managers, infrastructure, and the active `TurnBasedWorkflow`.

#### Useful when looking for

- How `./wolf` starts the app.
- How a new interactive CLI session is built.
- What `CliSession` and `BaseSession` actually do.
- What runtime objects are returned in `cli_session.session`.
- How resume-session loading currently works.
- Cleanup opportunities in the current session builder.


### 2. Real `./wolf` CLI Application Entrypoint

- **Nugget ID:** `wolf_cli_app_entrypoint`
- **File:** [`./wisdom_nuggets/wolf_cli_app_entrypoint.nug`](./wisdom_nuggets/wolf_cli_app_entrypoint.nug)
- **Author agent:** `silly_saha`
- **Created:** 2026-07-22
- **Status:** active
- **Confidence:** source-inspected-and-smoke-tested
- **Primary source files:**
  - [`./wolf`](./wolf)
  - [`./framework/cli/wolf_app.py`](./framework/cli/wolf_app.py)
  - [`./framework/cli/config_loader.py`](./framework/cli/config_loader.py)
  - [`./framework/cli/launchers.py`](./framework/cli/launchers.py)
  - [`./framework/utils/config_tools.py`](./framework/utils/config_tools.py)
  - [`./framework/workflows/workflow_space.py`](./framework/workflows/workflow_space.py)
- **Tags:** launcher, cli, wolf-wrapper, session-config, workflow-discovery, session-management, implementation

#### Context

Documents the first-pass implementation that turns `./wolf` into a real application launcher. It covers command UX, JSON/YAML launch config loading, workflow discovery/selection, session list/inspect commands, config validation, doctor diagnostics, and the `workflow_cls` injection changes in the session builder.

#### Useful when looking for

- How the new `./wolf` app launcher works.
- How to launch CLI sessions with selected workflows.
- How config files are merged with defaults and CLI flags.
- How session listing/inspection works.
- What was changed in `setup_cli_session()` for workflow injection.


### 3. Use `run_syscall` as an escape hatch when long `write_file` validation is brittle

- **Nugget ID:** `syscall_escape_hatch_for_brittle_action_validation`
- **File:** [`./wisdom_nuggets/syscall_escape_hatch_for_brittle_action_validation.nug`](./wisdom_nuggets/syscall_escape_hatch_for_brittle_action_validation.nug)
- **Author agent:** `inspiring_kowalevski`
- **Created:** 2026-07-31
- **Status:** active
- **Confidence:** runtime-observed
- **Primary source files:**
  - [`./framework/workflows/base_agent_action.py`](./framework/workflows/base_agent_action.py)
  - [`./framework/workflows/workflow_models.py`](./framework/workflows/workflow_models.py)
  - [`./framework/workflows/custom_workflows/gateway_action_workflow.py`](./framework/workflows/custom_workflows/gateway_action_workflow.py)
  - [`./framework/pack/gateway.py`](./framework/pack/gateway.py)
- **Tags:** action-validation, write-file, run-syscall, escape-hatch, brittle-validation, giant-union, operational-wisdom

#### Context

Captures the runtime-observed mitigation discovered when long `write_file` payloads were rejected before execution by the current giant-union action validator. The successful workaround was to use a permitted `run_syscall` action with a single-quoted shell heredoc to write the file, followed by explicit existence/byte-size verification.

#### Useful when looking for

- How to preserve long markdown/workplan content when `write_file` validation is failing.
- How to use a shell heredoc safely as a temporary escape hatch.
- Why this is only a mitigation and the durable fix is registry-first staged action validation.

---
---

## Maintenance notes

When adding a new `.nug` file:

1. Choose a descriptive, stable filename.
2. Put it under `./wisdom_nuggets/` unless another location is clearly more appropriate.
3. Include source-grounded metadata in front matter.
4. Add an entry to this registry with context and search tags.
5. If the nugget affects the high-level app model, also add a short cross-reference in `./app.md`.
