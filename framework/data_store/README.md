# Data Store Module

## Overview

The `data_store` module provides vector storage capabilities for the WOLF framework using ChromaDB. It enables efficient storage, retrieval, and management of embeddings for semantic search, RAG (Retrieval-Augmented Generation) systems, and document management.

## Core Components

### VectorStore (`vstore.py`)

The VectorStore class is a comprehensive wrapper around ChromaDB that provides:

#### Features

- **Embedding Management**:
 - Automatic embedding generation using configurable models
 - Support for OpenAI, Sentence Transformers, and custom embedding functions
 - Batch processing and async ingestion capabilities

- **Document Storage**:
 - Text document ingestion with metadata
 - Automatic chunking and preprocessing
 - Persistent storage with SQLite backend

- **Retrieval Operations**:
 - Semantic similarity search
 - K-nearest neighbors queries
 - Metadata filtering and where clauses
 - Configurable number of results

- **Collection Management**:
 - Multiple collection support
 - Collection creation, deletion, and listing
 - Persistence and backup capabilities

#### Configuration

Vector stores are configured using the `BaseVectorStoreParams` model from `data_models.py`:

