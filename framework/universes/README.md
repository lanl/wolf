# Universes Module (ActionBoxes)

## Overview

The `universes` module provides self-contained execution environments (also called ActionBoxes) that manage KnowledgeBases and ToolBoxes. Universes act as sandboxed environments where agents can perform tasks with isolated resources, tools, and knowledge stores.

## Core Components

### BaseUniverse (`base_universe.py`)

A universe is a complete execution environment that provides:

#### Features

- **Environment Management**:
 - Self-contained sandbox for task execution
 - Isolated from the local system environment
 - Can run on remote machines or containers
 - Supports different architectures and hardware (GPU, sensors, actuators)

- **Resource Hosting**:
 - Hosts multiple KnowledgeBases (KBs)
 - Hosts multiple ToolBoxes (TBs)
 - Provides unified API for resource access
 - Discovery and listing of available resources

- **KB Operations** (Knowledge Management):
 - `kb_search`: Semantic search with context window support
 - `kb_append_texts`: Add text documents
 - `kb_add_url/kb_add_urls`: Add documents from URLs
 - `kb_upload_dir`: Bulk upload from directories
 - `kb_stats`: Get KB statistics
 - `kb_sources`: List document sources
 - `kb_purge`: Clear all content
 - `kb_get_document_by_id`: Retrieve specific documents

- **TB Operations** (Tool Management):
 - `tb_search_tools`: Search for tools by query
 - `tb_execute`: Execute tools with parameters
 - `tb_tool_info`: Get detailed tool information
 - `tb_list_tools`: List all available tools
 - `tb_append_docs`: Add documentation to tools
 - `tb_search_tool_docs`: Search tool documentation
 - `tb_add_tool_from_meta`: Add tools from metadata
 - `tb_recursive_upload_tools`: Bulk tool discovery and upload
 - `tb_get_stats`: Get toolbox statistics

- **Async Support**:
 - All KB and TB operations have async variants (prefixed with "a")
 - Efficient concurrent operations
 - Non-blocking resource access

- **REST API**:
 - FastAPI-based HTTP interface
 - Complete CRUD operations for KBs and TBs
 - Discovery endpoints
 - Health checks and statistics
 - CORS support for web clients

#### Universe Configuration

Universes are configured using `BaseUniverseParams` from `data_models.py`:

