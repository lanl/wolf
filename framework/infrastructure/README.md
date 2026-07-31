# Infrastructure Module

## Overview

The `infrastructure` module provides the core management components that enable the WOLF framework to orchestrate agent-user interactions, manage conversation history, handle memory operations, and maintain context windows. These components work together to create a cohesive, stateful workflow environment.

## Architecture

The infrastructure consists of three interconnected managers that work together:

```
┌─────────────────────────────────────────────────────────────┐
│                   BaseInfrastructure                        │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
│  │ ChatManager  │  │MemoryManager │  │ ContextManager  │  │
│  │              │  │              │  │                 │  │
│  │ - History    │  │ - Facts      │  │ - Context       │  │
│  │ - Logging    │  │ - Prefs      │  │   Window        │  │
│  │ - Persistence│  │ - Summaries  │  │ - Token Mgmt    │  │
│  │              │  │ - Semantic   │  │ - Optimization  │  │
│  │              │  │   Search     │  │                 │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬────────┘  │
│         │                 │                    │            │
│         └─────────────────┼────────────────────┘            │
│                           │                                 │
│                  Coordinated State                          │
└─────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. BaseChatManager (`base_chat_manager.py`)

Manages chat history storage, retrieval, and timestamping.

#### Features

- **Chat History Management**:
  - Persistent storage of all chat entries
  - Timestamping with configurable formats
  - Entry normalization and validation
  - Token counting for context management

- **Logging Integration**:
  - Automatic logging to timestamped files
  - Multiple log levels (info, warning, error, debug, critical)
  - Session-based log organization

- **State Persistence**:
  - Pickle-based serialization for chat history
  - Snapshot and restore capabilities
  - Session resumption support

- **ChatEntry Model**: Uses Pydantic models for type-safe chat entries with:
  - `sender`: Message originator
  - `content`: Message content
  - `timestamp`: Creation timestamp

#### Key Methods

```python
# Add chat entries
chat_manager.add_chat_entries([{
    "sender": "user",
    "content": "Hello!",
    "timestamp": "2026-05-16 22:00:00"
}])

# Access chat history
history = chat_manager.CHAT_HISTORY

# Snapshot and restore
snapshot = chat_manager.snapshot()
chat_manager.restore(snapshot)
```

### 2. BaseMemoryManager (`base_memory_manager.py`)

Manages structured and vector-enhanced memory for workflows.

#### Features

- **Structured Memory Storage**:
  - Key-value storage organized by categories
  - Pre-defined categories: facts, user_prefs, warnings, strategies, decisions, conclusions, solutions, task_state, summaries
  - Custom categories can be added dynamically

- **Vector-Enhanced Recall**:
  - Integration with vector stores for semantic search
  - Separate stores for chat traces and summaries
  - Context-aware retrieval

- **Summarization**:
  - Automatic summarization of chat segments
  - Summary storage and indexing
  - Configurable summary token limits

- **Persistence**:
  - JSON-based storage for structured memory
  - Vector store persistence for embeddings
  - Snapshot and restore support

#### Key Methods

```python
# Store memories
memory_manager.remember("user_language", "Python", category="user_prefs")
memory_manager.remember("max_iterations", 10, category="task_state")

# Recall memories
language = memory_manager.recall("user_language", category="user_prefs")
all_prefs = memory_manager.recall(category="user_prefs")

# Semantic search
results = memory_manager.semantic_recall(
    query="What are the user's preferences?",
    source="traces",
    n_results=5
)

# Forget memories
memory_manager.forget("old_key", category="task_state")
memory_manager.clear(category="warnings")  # Clear entire category

# Process new chat entries for indexing
memory_manager.process_new_entries(new_chat_entries)
```

### 3. BaseContextManager (`base_context_manager.py`)

Builds and manages context windows for agent prompts with intelligent token management.

#### Features

- **Context Window Management**:
  - Configurable maximum token limits
  - Allocation strategies with customizable ratios:
    - Recent chat ratio (~30%)
    - Memory ratio (~50%)
    - Trace ratio (~20%)

- **Incremental Updates**:
  - Maintains persistent current_ctx buffer
  - Append new entries without full rebuilds
  - Automatic rebuild when threshold exceeded

- **Smart Optimization**:
  - Critical entry preservation
  - Sliding window approach
  - Multiple optimization strategies (aggressive, balanced, conservative)
  - Version tracking and rollback capability

- **Monitoring and Diagnostics**:
  - Real-time utilization tracking
  - Configurable thresholds for warnings
  - Comprehensive diagnostics reporting

#### Key Methods

```python
# Append new chat entries incrementally
context_manager.append_to_current_ctx({
    "sender": "user",
    "content": "Hello!",
    "timestamp": "2026-05-16 22:00:00"
})

# Check if rebuild needed
if context_manager.should_rebuild():
    context_manager.rebuild_current_ctx(
        chat_history=chat_manager.CHAT_HISTORY,
        memory_manager=memory_manager,
        target_utilization=0.6
    )

# Get current context for LLM
context_str = context_manager.get_compacted_context()

# Get diagnostics
diagnostics = context_manager.get_context_diagnostics()
print(f"Utilization: {diagnostics['utilization_pct']:.1f}%")
print(f"Tokens: {diagnostics['current_ctx_tokens']}/{diagnostics['max_ctx_tokens']}")

# Rollback to previous version if needed
context_manager.rollback_context()
```

### 4. BaseInfrastructure (`base_infrastructure.py`)

Provides the unified interface that coordinates all managers and provides workflow-level functionality.

#### Features

- **Manager Coordination**:
  - Integrates ChatManager, MemoryManager, and ContextManager
  - Ensures consistent state across all managers
  - Handles inter-manager communication

- **Workflow Support**:
  - Role and member tracking (users, agents, workers)
  - Object registry (KBs, TBs, Universes)
  - Action processing and routing

- **State Management**:
  - Complete workflow state snapshots
  - Session resumption capabilities
  - Persistent storage across sessions

- **User Input Processing**:
  - Command parsing and routing
  - Turn management between users and agents
  - Input validation and error handling

#### Key Methods

```python
# Append to chat with automatic context management
infra.append_chat_history(
    actor="user",
    content="Hello!",
    action={"action": "user_input"},
    log_console=True
)

# Process user input
BREAK, IS_CMD, ERROR, INTERLOCUTOR, PROMPT = infra.process_user_input(user_input)

# Get partial context
ctx = infra.get_partial_ctx(idx0=10, idx1=20)

# Snapshot entire infrastructure
snapshot = infra.snapshot()
infra.save_snapshot("session_backup.pkl")

# Restore from snapshot
infra.load_snapshot("session_backup.pkl")
```

## Component Interaction Flow

### 1. New Chat Entry Flow

```
User/Agent Input
       ↓
BaseInfrastructure.append_chat_history()
       ↓
   ┌───┴───┐
   │       │
   ↓       ↓
ChatMgr  ContextMgr
- Store  - Append to buffer
- Log    - Check threshold
   │       │
   └───┬───┘
       ↓
  MemoryMgr
  - Index for semantic search
  - Check if summarization needed
```

### 2. Context Window Rebuild Flow

```
Context Threshold Exceeded
       ↓
ContextManager.should_rebuild() → True
       ↓
ContextManager.rebuild_current_ctx()
       ↓
   ┌───┴────────────────┐
   │                    │
   ↓                    ↓
ChatMgr            MemoryMgr
- Get full history - Get summaries
                   - Semantic recall
   │                    │
   └────────┬───────────┘
            ↓
   Build optimized context
   - Recent chat (30%)
   - Memory (50%)
   - Semantic traces (20%)
            ↓
   Update current_ctx buffer
```

### 3. Session Persistence Flow

```
Session Save Request
       ↓
BaseInfrastructure.snapshot()
       ↓
   ┌───┴───────┬──────────┐
   │           │          │
   ↓           ↓          ↓
ChatMgr    MemoryMgr  ContextMgr
.snapshot() .snapshot() .snapshot()
   │           │          │
   └───┬───────┴──────────┘
       ↓
  Combined Snapshot
       ↓
  JSON Serialization
       ↓
  session.snapshot.json
```

## Usage Example

```python
from framework.infrastructure.base_infrastructure import BaseInfrastructure
from framework.agentic.agents import OpenAIAgent

# Initialize agent
agent = OpenAIAgent(
    model="gpt-4",
    host_address="https://api.openai.com",
    api_key="your-key"
)

# Create infrastructure
infra = BaseInfrastructure(
    agent=agent,
    max_ctx_tokens=100000,
    session_dir="./my_session"
)

# Use in workflow
infra.append_chat_history(
    actor="user",
    content="Help me write Python code",
    action={"action": "user_input"}
)

# Get context for agent prompt
context = infra.context_manager.get_compacted_context()
diagnostics = infra.context_manager.get_context_diagnostics()

print(f"Context size: {diagnostics['current_ctx_tokens']} tokens")
print(f"Utilization: {diagnostics['utilization_pct']:.1f}%")

# Save session
infra.save_snapshot(f"{infra.session_dir}/infrastructure.pkl")
```

## Best Practices

1. **Token Management**:
   - Monitor context utilization regularly
   - Set appropriate rebuild thresholds (default: 0.85)
   - Adjust allocation ratios based on workflow needs

2. **Memory Organization**:
   - Use appropriate categories for different memory types
   - Leverage semantic recall for relevant context retrieval
   - Clear old memories periodically to avoid clutter

3. **State Persistence**:
   - Take snapshots at regular intervals
   - Save before critical operations
   - Test restore procedures regularly

4. **Performance Optimization**:
   - Use incremental updates instead of full rebuilds
   - Configure appropriate context window sizes
   - Monitor memory usage for large chat histories

## Configuration

Key parameters for infrastructure components:

```python
# Context Manager
max_ctx_tokens: int = 20000        # Maximum context window size
recent_chat_ratio: float = 0.30    # Portion for recent chat
memory_ratio: float = 0.50         # Portion for structured memory
trace_ratio: float = 0.20          # Portion for semantic traces
rebuild_threshold: float = 0.85    # When to trigger rebuild

# Memory Manager
max_summary_tokens: int = 2000     # Max tokens per summary
memory_fragment_types: List[str]   # Custom memory categories

# Chat Manager
time_stamp_format: str = "%Y%m%d_%H%M%S"  # Timestamp format
chat_block_divider: str = "/" * 120       # Visual separator
```

## Integration with Workflows

The infrastructure module is designed to integrate seamlessly with the `workflows` module:

- **BaseWorkflow** uses BaseInfrastructure as its core
- Managers provide state for workflow resumption
- Context management ensures efficient LLM interactions
- Memory enables long-term conversation awareness

See `./workflows/README.md` for workflow integration details.