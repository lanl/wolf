# WOLF Framework

## Workflow Orchestration Language Framework

WOLF is an agentic AI framework that orchestrates users and AI agents as they perform tasks. It provides an extensive modular and composable infrastructure to help AI agents perform tasks and workflows.

## Architecture Overview

The framework is organized into several key components:

### 🎯 **Core Components**

1. **[Agentic](./agentic/README.md)** - AI agent definitions and management
2. **[Infrastructure](./infrastructure/README.md)** - Core managers (chat, memory, context)
3. **[Workflows](./workflows/README.md)** - Workflow execution and orchestration

### 📦 **Data & Knowledge Layer**

4. **[Data Store](./data_store/README.md)** - Vector stores for embeddings and retrieval
5. **[Knowledgebase](./knowledgebase/README.md)** - RAG-enabled knowledge management

### 🔧 **Tool & Execution Layer**

6. **[Tooling](./tooling/README.md)** - Tool definitions and toolbox management
7. **[Universes](./universes/README.md)** - Sandboxed execution environments (ActionBoxes)

### 🌐 **Integration & Support**

8. **[Gateway](./gateway/README.md)** - API gateway and client-server communication
9. **[Orchestration](./orchestration/README.md)** - Advanced task orchestration and agent pooling
10. **[Utils](./utils/README.md)** - Utility functions and helpers

## Component Hierarchy

The components build on each other to create a full-featured agentic system:

