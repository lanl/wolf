#!/usr/bin/env python3
"""Fixed launcher that properly bridges old infrastructure with new orchestration."""

import sys
import os
from pathlib import Path
import copy

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.resolve()))

from config.session.default.params.inputs import session_params
from framework.utils.config_tools import setup_cli_session
from framework.agentic.default.params.llm_params import LANL_AIPORTAL_LLMs as LLMs
from framework.orchestration.web_ui.app import app, socketio, init_runtime
from framework.orchestration.agent_pool import AgentPool, AgentDescriptor
from framework.orchestration.task_infra import TaskInfrastructureFactory, SharedResources
from framework.orchestration.models import EngineConfig
from framework.workflows.workflow_models import Actions


def create_agent_pool_from_session(session):
    """Extract agents properly from session."""
    agents = []
    
    # Session structure: {'agents': {'main': agent_obj, 'workers': [worker1, ...]}, ...}
    if 'agents' in session:
        agents_dict = session['agents']
        if 'main' in agents_dict and agents_dict['main'] is not None:
            agents.append(agents_dict['main'])
        if 'workers' in agents_dict and agents_dict['workers']:
            agents.extend(agents_dict['workers'])
    
    if not agents:
        # Fallback: try workflow infrastructure
        wf = session.get('wf')
        if wf:
            if hasattr(wf, 'agent') and wf.agent:
                agents.append(wf.agent)
            if hasattr(wf, 'workers') and wf.workers:
                agents.extend(wf.workers.values())
    
    if not agents:
        raise Exception("[!][ERROR] No agents found in session. Cannot create agent pool.")
    
    print(f"[+] Found {len(agents)} agent(s): {[getattr(a, 'name', 'unnamed') for a in agents]}")
    return AgentPool(agents=agents)


def create_infra_factory_from_session(session):
    """Create factory that provides full infrastructure access to tasks."""
    wf = session.get('wf')
    if not wf or not hasattr(wf, 'infra'):
        raise Exception("[!][ERROR] No workflow infrastructure found in session.")
    
    base_infra = wf.infra
    
    # Build shared resources from infrastructure
    shared = SharedResources(
        objects=list(getattr(base_infra, 'objects', [])),
        universes={u.name: u for u in getattr(base_infra, 'UNIVs', {}).values()} if hasattr(base_infra, 'UNIVs') else {},
        knowledge_bases=dict(getattr(base_infra, 'KBs', {})),
        toolboxes=dict(getattr(base_infra, 'TBs', {}))
    )
    
    # Compatibility builder that returns the full base infrastructure
    def compat_builder(**kwargs):
        return base_infra
    
    factory = TaskInfrastructureFactory(
        shared_resources=shared,
        compat_builder=compat_builder,
        session_root=session.get('session_dir', '.gateway_sessions')
    )
    
    print(f"[+] Infrastructure factory created with {len(shared.universes)} universes")
    return factory


def main():
    params = {
        'banner_image_width': 100,
        'banner_image_color': 'purple',
        'verbose': 0
    }
    
    print("[*] Initializing WOLF session...")
    session_inputs = copy.deepcopy(session_params)
    for k, v in params.items():
        if k in session_inputs:
            session_inputs[k] = v
        else:
            raise Exception(f"[!] {k} is not a recognized session input")
    
    session_inputs['LLMs'] = LLMs
    session = setup_cli_session(session_inputs, resume_session=None)
    
    print("[*] Creating orchestration runtime components...")
    agent_pool = create_agent_pool_from_session(session)
    infra_factory = create_infra_factory_from_session(session)
    
    print(f"[+] Agent pool initialized with {len(agent_pool._agents)} agents")
    init_runtime(agent_pool, infra_factory)
    
    print("[*] Starting web server...")
    print("[+] WOLF Orchestration Dashboard: http://0.0.0.0:5000")
    print("[+] Press Ctrl+C to stop")
    
    try:
        socketio.run(app, debug=False, host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        print("\n[*] Shutting down...")
        sys.exit(0)


if __name__ == '__main__':
    main()
