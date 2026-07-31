# Agentic Module

## Overview

The `agentic` module provides AI agent definitions and management capabilities for the WOLF framework. It offers a unified interface for working with OpenAI-compatible LLM services, supporting both synchronous and asynchronous operations, streaming responses, and structured outputs.

## Core Components

### OpenAIAgent (`agents.py`)

A comprehensive wrapper for OpenAI-compatible agents with the following capabilities:

#### Features

- **Multiple Response Modes**:
 - Standard chat completions
 - Streaming responses with real-time output
 - Structured JSON outputs (using OpenAI's beta API)
 - Instructor-based structured outputs (when available)

- **Sync and Async Support**: Full support for both synchronous and asynchronous operations

- **Flexible Configuration**:
 - Configurable base URLs for custom endpoints
 - API version and authentication support
 - Custom system prompts
 - Context window length tracking
 - Agent capability flags

- **Context Management**:
 - Automatic context preservation across requests
 - Context reset functionality
 - Configurable history caching

#### Initialization

