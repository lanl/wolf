# WOLF - Workflow Orchestration Language Framework

**WOLF** is an agentic AI framework that orchestrates users and AI agents as they perform tasks collaboratively. It provides an extensive modular and composable infrastructure to help AI agents perform complex workflows with memory management, context optimization, and remote execution capabilities.

---

## Table of Contents

1. [Overview](#overview)
2. [Getting Started](#getting-started)
3. [Architecture](#architecture)
4. [Core Components](#core-components)
5. [Session Management](#session-management)
6. [Infrastructure Layer](#infrastructure-layer)
7. [Workflow Execution](#workflow-execution)
8. [Configuration](#configuration)
9. [Development Guide](#development-guide)
10. [Best Practices](#best-practices)

---

## Overview

WOLF enables sophisticated human-AI collaboration through:

- **Multi-Agent Orchestration**: Coordinate multiple AI agents with specialized roles
- **Memory Management**: Persistent, categorized memory with vector store integration
- **Context Optimization**: Intelligent context window management with summarization
- **Infrastructure Management**: Deploy and manage KnowledgeBases, ToolBoxes, and Universes
- **Session Persistence**: Save and resume workflow sessions with full state restoration
- **Playbook Execution**: Deploy complex multi-step workflows with tracking

---

## Getting started

# Welcome to the Workflow Orchestration Language Framework (WOLF)!

Follow these steps to use WOLF. 

# 1. Clone the WOLF repo
```sh
  ssh://git@re-git.lanl.gov:10022/mada/wolf.git
```

# 2. Set up the environment (Only the first time)
## 2.1 Set up your conda env:
Setup the correct conda environement.
NOTE: Make sure you are in a bash session

```sh
conda create -n wolf python=3.13  # Python version >=3.13
conda activate wolf
pip install dotenv searxng_wrapper rich openai funkybob tiktoken pdfplumber nbformat alive_progress prompt_toolkit chromadb fastapi dill

```

## 2.2 Set up your .env

A sample of .env file, sample.env,is provided to help you get started: 
### 2.2.1 Make a copy
```sh
cp sample.env .env
```
### 2.2.1 Insert you inference API Key
Open .env and paste your inference API key obtained from [LANL AI Portal](https://aiportal-api.aws.lanl.gov/ui/) or Venadao,
into the appropriate variable.

## 2.3 Set up your SSL and CURL certificats:
inside your RC-file
### 2.3.1 On linux (i.e rocinante)
```sh
vi ~/.bashrc
```
add the following lines (at the bottom or whereever works better for you):
export CURL_CA_BUNDLE="/etc/ssl/ca-bundle.pem"
export SSL_CERT_FILE="/etc/ssl/ca-bundle.pem"
### 2.3.2 On OSX (Macbook):
```sh
vi ~/.zshrc
```
#### For Standard macOS System Certificates add the following lines:
export CURL_CA_BUNDLE="/etc/ssl/cert.pem" \
export SSL_CERT_FILE="/etc/ssl/cert.pem"
#### For OpenSSL installed via Homebrew add the following lines:
export CURL_CA_BUNDLE="/usr/local/etc/openssl@3/cert.pem" \
export SSL_CERT_FILE="/usr/local/etc/openssl@3/cert.pem"
#### Or for older OpenSSL versions installed via Homebrew add the following lines:
export CURL_CA_BUNDLE="/usr/local/etc/openssl/cert.pem" \
export SSL_CERT_FILE="/usr/local/etc/openssl/cert.pem"

# 3. Run WOLF interactively

## 3.1 CLI interactive:
```sh
./wolf
```

## License
Modified BSD 3-Clause License

Copyright (c) 2025, Los Alamos National Laboratory

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
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
