#!/usr/bin/env python3
"""Launcher script for WOLF Orchestration Web UI.

This script initializes the orchestration runtime with the existing WOLF infrastructure
and starts the Flask web application.
"""

import asyncio
import sys
import os
from pathlib import Path
import copy

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.resolve()))

from config.session.default.params.inputs import session_params
from framework.utils.config_tools import setup_cli_session
from framework.agentic.default.params.llm_params import LANL_AIPORTAL_LLMs as LLMs
from framework.orchestration.web_ui.app import app, socketio, init_runtime
from framework.orchestration.agent_pool import AgentPool
from framework.orchestration.task_infra import TaskInfrastructureFactory, SharedResources
from framework.orchestration.models import EngineConfig


def create_agent_pool_from_session(session):
    """Create an AgentPool from the existing session infrastructure."""
    agents = []
    
    # Extract agents from the session dictionary
    # The session['agents'] has {'main': agent, 'workers': [worker1, worker2, ...]}
    if 'agents' in session:
        agents_dict = session['agents']
        if 'main' in agents_dict and agents_dict['main'] is not None:
            agents.append(agents_dict['main'])
        if 'workers' in agents_dict and agents_dict['workers']:
            agents.extend(agents_dict['workers'])
    
    # Fallback: try to extract from workflow infrastructure
    if not agents and 'wf' in session:
        wf = session['wf']
        if hasattr(wf, 'agent') and wf.agent is not None:
            agents.append(wf.agent)
        if hasattr(wf, 'workers') and wf.workers:
            agents.extend(wf.workers.values())
    
    if not agents:
        print("[!] Warning: No agents found in session. Agent pool will be empty.")
    
    # Create AgentPool with the list of agents
    agent_pool = AgentPool(agents=agents)
    
    return agent_pool


def create_infra_factory_from_session(session):
    """Create a TaskInfrastructureFactory from the existing session."""
    # Get the workflow's infrastructure
    base_infra = None
    if 'wf' in session and hasattr(session['wf'], 'infra'):
        base_infra = session['wf'].infra
    
    # Extract universes and other resources from base infrastructure
    shared_resources = SharedResources()
    if base_infra:
        if hasattr(base_infra, 'objects'):
            shared_resources.objects = list(base_infra.objects)
        if hasattr(base_infra, 'UNIVs'):
            shared_resources.universes = dict(base_infra.UNIVs)
        if hasattr(base_infra, 'KBs'):
            shared_resources.knowledge_bases = dict(base_infra.KBs)
        if hasattr(base_infra, 'TBs'):
            shared_resources.toolboxes = dict(base_infra.TBs)
    
    # Create factory with shared resources and compatibility builder
    def compat_builder(**kwargs):
        # Return the base infrastructure for compatibility
        return base_infra
    
    factory = TaskInfrastructureFactory(
        shared_resources=shared_resources,
        compat_builder=compat_builder,
        session_root=session.get('session_dir', '.gateway_sessions')
    )
    
    return factory


def main():
    """Main entry point for the orchestration web UI."""
    
    # Configuration
    user_name = "user"
    params = {
        'banner_image_width': 100,
        'banner_image_color': 'purple',
        'verbose': 0
    }
    
    print("[*] Initializing WOLF session...")
    
    # Setup session (same as interactive.py)
    session_inputs = copy.deepcopy(session_params)
    s_params = list(session_inputs.keys())
    for k in params.keys():
        if k in s_params:
            session_inputs[k] = params[k]
        else:
            raise Exception(f"[!] {k} is not a recognised session input")
    
    session_inputs['LLMs'] = LLMs
    
    # Initialize session
    session = setup_cli_session(session_inputs, resume_session=None)
    # To resume a session: session = setup_cli_session(session_inputs, resume_session="last")
    
    print("[*] Creating orchestration runtime components...")
    
    # Create agent pool and infrastructure factory from session
    agent_pool = create_agent_pool_from_session(session)
    infra_factory = create_infra_factory_from_session(session)
    
    print(f"[+] Agent pool initialized with {len(agent_pool._agents)} agents")
    
    # Initialize the web UI runtime
    init_runtime(agent_pool, infra_factory)
    
    print("[*] Starting web server...")
    print("[+] WOLF Orchestration Dashboard available at: http://0.0.0.0:5000")
    print("[+] Press Ctrl+C to stop")
    
    # Run the Flask-SocketIO server
    try:
        socketio.run(app, debug=False, host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        print("\n[*] Shutting down...")
        sys.exit(0)


if __name__ == '__main__':
    main()
