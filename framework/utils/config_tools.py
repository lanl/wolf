import os, copy
import asyncio
from datetime import datetime
from pathlib import Path
import tiktoken
import json
import glob
from typing import Optional
import chromadb
from chromadb.config import Settings

# UTILs
from framework.utils.io_tools import console, load_env_vars, image_to_ascii
from framework.utils.machines_ssl_config import conform_machine_ssl_certs

# VStore
from framework.data_store.vstore import VectorStore
from framework.data_store.default.params.vstore_params import (Default_summaries_vs_params as SUMMARIES_PARAMS,
                                                               Default_traces_vs_params as TRACES_PARAMS)
# KBs
#

# TBs
#

# UNIVERSEs (a.k.a ActionBoxes)
from framework.universes.data_models import BaseUniverseModel, BaseUniverseParams
from framework.universes.universe_tools import get_base_universe_params

# Agents
from framework.agentic.agents import OpenAIAgent

# WORKFLOWS
from framework.workflows.workflow_models import Actions
from framework.infrastructure.base_chat_manager import BaseChatManager
from framework.infrastructure.base_memory_manager import MemoryManager
from framework.infrastructure.base_context_manager import ContextManager
from framework.infrastructure.base_infrastructure import BaseInfrastructure 
#from framework.workflows.base_workflow import BaseWorkflow
from framework.workflows.custom_workflows.turn_based_workflow import TurnBasedWorkflow 


def create_session_dir():
    """Create a unique session directory under wf_workspace."""
    session_dir = "wf_workspace/session_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(session_dir, exist_ok=True)
    return session_dir


def set_llm_api_key(llm, env_vars=None, env_path=".env", 
                    api_key_handle="api_key",           # Making this flexible 
                    api_key_var_handle="api_key_var"):  # for changing the keys
    if api_key_handle in llm.keys():
        if llm[api_key_handle] is not None:
            console.print(f"[!][utils][io_tools][set_llm_api_key][DANGER]:")
            console.print(f"     * API KEY for LLM{llm['model']} on {llm['host']}:{llm['port']}")
            console.print(f"     provided unsafly. use the 'api_key_var' approach insted")
            return # API ke 
    keys = list(llm.keys())
    # No need to relod .env file if provided
    if env_vars is None:
        ENV_VARs = load_env_vars(env_path)
    else:
        ENV_VARs = copy.deepcopy(env_vars)
    # Now let's get the env var keys
    try:
        ENV_VAR_KEYs = ENV_VARs.keys()
    except:
        ENV_VAR_KEYs = [] # Empty
    # OK now let set the api_key:
    if (api_key_var_handle in keys ):
        if llm[api_key_var_handle] in ENV_VAR_KEYs: 
            llm[api_key_handle] = ENV_VARs[llm[api_key_var_handle]]
    return


def load_session_certs(session_params):
    cache_dir = session_params.get('tiktoken_cache_dir', (Path.cwd() / ".tiktoken_cache").resolve())
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(cache_dir))
    cache_dir.mkdir(parents=True, exist_ok=True)

def show_banner(session_params):
    console.print("|=================================================================================|")
    image_to_ascii(session_params.get('banner_image_file','config/preferences/banner/WOLF.png'),
                   width=session_params.get('banner_image_width', 100),
                   flag=session_params.get('banner_image_color', 'purple')
                   )
    console.print("|=================================================================================|")

def build_list_agents(session_params):
    params = list(session_params.keys())
    assert 'LLMs' in params, "[ERROR][CLI SESSION SETUP]: 'LLMs' are required parameters for building agents"
    LLMs = session_params['LLMs']
    ENV_VARs = load_env_vars()
    AGENTs = {}
    # Construct the Agent list based on the provided LLMs
    for k in LLMs.keys():
        llm = LLMs[k]
        set_llm_api_key(llm, env_vars=ENV_VARs)
        try:
            max_ctx = llm["ctx_window_length"]
        except:
            max_ctx = None
        AGENTs[k] = OpenAIAgent(
            model=llm["model"],
            host_address=llm["host"],
            host_port=llm["port"],
            api_version=llm["api_version"],
            api_key=llm["api_key"],
            verbose=llm["verbose"],
            capabilities=llm["capabilities"],
            ctx_window_length = max_ctx
        )
    return AGENTs

def build_list_universes(session_params):
    UNIVs = []
    console.print("|=================================================================================|")
    console.print("  --------------------------------[  UNIVERSES  ]-------------------------------- ")
    console.print("|=================================================================================|")
    Universes = session_params.get('universes', [])
    for univ in Universes:
        univ_param = get_base_universe_params(host=univ["host"], port=univ["port"], verbose=session_params['verbose'])
        if univ_param is not None:
            univ_info = univ_param.info
            print(f""" --  [{univ_info.name}] --:
              | > host = {univ_info.host}
              | > port = {univ_info.port}
              | > description = '{univ_info.description}
              | > api_version = {univ_info.api_version}
              | > api_token = {univ_info.api_token}
                  """
                  )
            UNIVs.append(univ_param)
        else:
            console.print(f"[!][WOLF][INTERACTIVE][WARN] Unable to read universe [{univ}]'s parameters")
    return UNIVs


def load_existing_session(session_identifier: str, session_params: dict) -> dict:
    """Load an existing session from snapshots.
    
    Args:
        session_identifier: Path, date, keyword, or session ID to load
        session_params: Session parameters for reconstructing components
        
    Returns:
        Session dictionary with reconstructed workflow
    """
    # Resolve session path
    LATEST_KEYWORDS = ['latest', 'last', 'recent', 'most_recent', 'newest']
    session_lower = session_identifier.strip().lower()
    
    snapshot_path = None
    
    if session_lower in LATEST_KEYWORDS:
        pattern = "wf_workspace/session_*/session.snapshot.json"
        all_sessions = sorted(glob.glob(pattern), reverse=True)
        if all_sessions:
            snapshot_path = all_sessions[0]
            console.print(f"[cyan]Loading most recent session: {os.path.dirname(snapshot_path)}[/cyan]")
    elif session_identifier.endswith('.snapshot.json') and os.path.exists(session_identifier):
        snapshot_path = session_identifier
    elif session_identifier.startswith('wf_workspace'):
        if session_identifier.endswith('.snapshot.json'):
            snapshot_path = session_identifier
        else:
            snapshot_path = os.path.join(session_identifier, 'session.snapshot.json')
    elif session_identifier.startswith('session_'):
        snapshot_path = f"wf_workspace/{session_identifier}/session.snapshot.json"
    elif len(session_identifier) == 8 and session_identifier.isdigit():
        pattern = f"wf_workspace/session_{session_identifier}_*/session.snapshot.json"
        matching_sessions = sorted(glob.glob(pattern), reverse=True)
        if matching_sessions:
            snapshot_path = matching_sessions[0]
            console.print(f"[cyan]Loading latest session from {session_identifier}[/cyan]")
    else:
        pattern = f"wf_workspace/session_*{session_identifier}*/session.snapshot.json"
        matching_sessions = sorted(glob.glob(pattern), reverse=True)
        if matching_sessions:
            snapshot_path = matching_sessions[0]
            console.print(f"[cyan]Found matching session: {os.path.dirname(snapshot_path)}[/cyan]")
    
    if not snapshot_path or not os.path.exists(snapshot_path):
        raise Exception(f"[!][ERROR] Unable to find session matching '{session_identifier}'")
    
    # Load snapshot
    with open(snapshot_path, 'r') as f:
        snapshot_data = json.load(f)
    
    console.print(f"[green]Successfully loaded snapshot from {snapshot_path}[/green]")
    
    # Extract session directory
    session_dir = snapshot_data.get('session_dir', os.path.dirname(snapshot_path))
    
    # Reconstruct agents (these can't be serialized, must be rebuilt)
    AGENTs = build_list_agents(session_params)
    agents = list(AGENTs.keys())
    main_agent = AGENTs[agents[0]]
    workers = [AGENTs[worker] for worker in agents[1:]] if len(agents) > 1 else []
    
    # Reconstruct universes
    UNIVs = build_list_universes(session_params)

    # Setup vector stores
    memory_db_persist_sub_dir = session_params.get('memory_db_persist_sub_dir', 'memory')
    
    summaries_vs_params = copy.deepcopy(SUMMARIES_PARAMS)
    summaries_vs = VectorStore(summaries_vs_params)
    
    traces_vs_params = copy.deepcopy(TRACES_PARAMS)
    traces_vs = VectorStore(traces_vs_params)
    
    # Reconstruct managers
    chat_manager = BaseChatManager(session_dir=session_dir)
    memory_manager = MemoryManager(
        memory_path=os.path.join(session_dir, "memory.json"),
        traces_vector_store=traces_vs,
        summaries_vector_store=summaries_vs
    )
    context_manager = ContextManager(
        max_ctx_tokens=100000,
        traces_vector_store=traces_vs,
        session_dir=session_dir
    )
    
    # Restore manager states from snapshots
    infra_snapshot = snapshot_data.get('infrastructure', {})
    if 'chat_manager' in infra_snapshot:
        chat_manager.restore(infra_snapshot['chat_manager'])
    if 'context_manager' in infra_snapshot:
        context_manager.restore(infra_snapshot['context_manager'])
    if 'memory_manager' in infra_snapshot:
        memory_manager.restore(infra_snapshot['memory_manager'])
    
    # Reconstruct infrastructure
    INFRA = BaseInfrastructure(
        agent=main_agent,
        workers=workers,
        objects=UNIVs,
        max_ctx_tokens=snapshot_data.get('max_ctx_tokens', 50000),
        wf_log_dir=session_dir,
        session_dir=session_dir,
        chat_manager=chat_manager,
        memory_manager=memory_manager,
        context_manager=context_manager
    )
    
    # Restore infrastructure state
    INFRA.restore(infra_snapshot)
    
    # Create workflow with restored infrastructure
    #WF = BaseWorkflow(
    WF = TurnBasedWorkflow(
        session=None,  # Will be set during load_session_state
        infra=INFRA,
        actions_union=Actions,
        wf_rules_file=snapshot_data.get('wf_rules_file'),
        wf_agent_behaviour_file=snapshot_data.get('wf_agent_behaviour_file'),
        wf_agent_sys_prompt_file=snapshot_data.get('wf_agent_sys_prompt_file'),
        wf_user=snapshot_data.get('WORKFLOW_USER', 'user'),
        wf_turn=snapshot_data.get('WORKFLOW_TURN')
    )
    
    console.print(f"[green]Session restored successfully from {session_dir}[/green]")
    
    return {
        'agents': {'main': main_agent, 'workers': workers},
        'objects': {'universes': UNIVs, 'kbs': [], 'tbs': []},
        'managers': {'chat': chat_manager, 'memory': memory_manager, 'context': context_manager},
        'session_dir': session_dir,
        'wf': WF
    }


def setup_cli_session(session_params, resume_session: Optional[str] = None, db_client: Optional[chromadb.Client] = None):
    """Setup CLI session - either new or resumed.
    
    Args:
        session_params: Session parameters
        resume_session: If provided, resume from this session (path/date/keyword)
        
    Returns:
        Session dictionary
    """
    params = list(session_params.keys())
    assert 'LLMs' in params, "[ERROR][CLI SESSION SETUP]: 'LLMs' are required parameters for building agents"
    
    # INIT
    load_session_certs(session_params)
    show_banner(session_params)
    
    # Check if resuming existing session
    if resume_session:
        return load_existing_session(resume_session, session_params)

    # SESSION
    session_dir = session_params.get('session_dir', create_session_dir())
    console.print(f"[INFO] Session directory: {session_dir}")

    # Initialize ChromaDB client
    vs_persist_dir = f"{session_dir}/VStore"
    if db_client is None: 
        db_client = chromadb.Client( Settings(persist_directory=vs_persist_dir, 
                                                     anonymized_telemetry=False) 
                                           )
    # Otherwise create new session
    LLMs = session_params['LLMs']
    verbose = session_params.get('verbose', 0)
    
    # AGENTS
    AGENTs = build_list_agents(session_params)
    agents = list(AGENTs.keys())
    main_agent = AGENTs[agents[0]]
    workers = [AGENTs[worker] for worker in agents[1:]] if len(agents) > 1 else []
    
    # KBs
    KBs = []
    # TBs
    TBs = []
    # UNIVERSES
    UNIVs = build_list_universes(session_params)
    
    ## Memory VS persist subdirectory
    memory_db_persist_sub_dir = session_params.get('memory_db_persist_sub_dir', 'memory')
    
    ## Summaries
    summaries_vs_params = session_params.get('summaries_params', None)
    if summaries_vs_params is None:
        summaries_vs_params = copy.deepcopy(SUMMARIES_PARAMS)
    if verbose > 0: console.print(f"[INFO][MEMORY] Summaries params: {summaries_vs_params}")
    summaries_vs = VectorStore(summaries_vs_params, client=db_client)
    
    ## Traces
    traces_vs_params = session_params.get('traces_params', None)
    if traces_vs_params is None:
        traces_vs_params = copy.deepcopy(TRACES_PARAMS)
    if verbose > 0: console.print(f"[INFO][MEMORY] Traces params: {traces_vs_params}")
    traces_vs = VectorStore(traces_vs_params, client=db_client)
    
    # MANAGERs
    chat_manager = session_params.get('chat_manager', BaseChatManager(session_dir=session_dir))
    memory_manager = session_params.get('memory_manager', 
                                       MemoryManager(memory_path=os.path.join(session_dir, "memory.json"),
                                                    traces_vector_store=traces_vs,
                                                    summaries_vector_store=summaries_vs))
    context_manager = session_params.get('context_manager', 
                                        ContextManager(max_ctx_tokens=100000,
                                                      recent_chat_ratio=0.50,
                                                      memory_ratio=0.30,
                                                      trace_ratio=0.20,
                                                      traces_vector_store=traces_vs))
    
    # INFRA
    INFRA = session_params.get('infra', 
                              BaseInfrastructure(agent=main_agent,
                                               workers=workers,
                                               objects=UNIVs,
                                               max_ctx_tokens=session_params.get('max_ctx_tokens', 50000),
                                               wf_log_dir=session_dir,
                                               session_dir=session_dir,
                                               chat_manager=chat_manager,
                                               memory_manager=memory_manager,
                                               context_manager=context_manager,
                                               db_client=db_client)
                               )
    
    # WORKFLOW
    #WF = session_params.get('wf', BaseWorkflow(infra=INFRA, actions_union=session_params.get('actions', Actions)))
    WF = session_params.get('wf', TurnBasedWorkflow(infra=INFRA, actions_union=session_params.get('actions', Actions)))
    
    return {
        'agents': {'main': main_agent, 'workers': workers},
        'objects': {'universes': UNIVs, 'kbs': KBs, 'tbs': TBs},
        'managers': {'chat': chat_manager, 'memory': memory_manager, 'context': context_manager},
        'session_dir': session_dir,
        'db_client': db_client,
        'wf': WF
    }


class BaseSession:
    """A Basic class to manage a wolf session.
    """
    def __init__(self, session_params, db_client: Optional[chromadb.Client] = None):
        """Initialize the Session with configuration parameters."""
        self.session_params = session_params
        self.db_client = db_client
    def create_session(self, resume_session: Optional[str] = None, db_client=None):
        """Initialize the Session with configuration parameters."""
        self.session = None

class CliSession(BaseSession):
    def __init__(self, session_params, db_client: Optional[chromadb.Client] = None):
        super().__init__(session_params=session_params, db_client=db_client)
    def create_session(self, resume_session: Optional[str] = None, db_client=None):
        if db_client is None: db_client = self.db_client
        self.session = setup_cli_session(session_params=self.session_params, 
                                         resume_session=resume_session, 
                                         db_client=db_client)

