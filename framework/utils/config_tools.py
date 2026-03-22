import os, copy
import asyncio
from datetime import datetime
from pathlib import Path
import tiktoken

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
from framework.agentic.default.params.llm_params import LANL_AIPORTAL_LLMs as LLMs

# WORKFLOWS
from framework.workflows.workflow_models import Actions
from framework.workflows.chat_manager import BaseChatManager
from framework.workflows.memory_manager import MemoryManager
from framework.workflows.context_manager import ContextManager
from framework.workflows.workflow_infrastructure import BaseInfrastructure
from framework.workflows.agentic_workflows import BaseWorkflow


def create_session_dir():
    """Create a unique session directory under wf_workspace."""
    session_dir = "wf_workspace/session_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(session_dir, exist_ok=True)
    return session_dir


def set_llm_api_key(llm, env_vars=None, env_path=".env", 
                    api_key_handle="api_key",           # Making this flexible 
                    api_key_var_handle="api_key_var"):  # for changing the keys
    if api_key_handle in llm.keys():
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
    #os.environ['CURL_CA_BUNDLE'] = session_params.get('curl_ca_bundle_file','/etc/ssl/ca-bundle.pem')

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
        AGENTs[k] = OpenAIAgent(
            model=llm["model"],
            host_address=llm["host"],
            host_port=llm["port"],
            api_version=llm["api_version"],
            api_key=llm["api_key"],
            verbose=llm["verbose"],
            capabilities=llm["capabilities"]
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


def setup_cli_session(session_params):
    params = list(session_params.keys())
    assert 'LLMs' in params, "[ERROR][CLI SESSION SETUP]: 'LLMs' are required parameters for building agents"
    LLMs = session_params['LLMs']
    verbose = session_params.get('verbose', 0)
    # INIT
    load_session_certs(session_params)
    show_banner(session_params)
    # AGENTS
    AGENTs = build_list_agents(session_params)
    agents = list(AGENTs.keys())
    main_agent = AGENTs[agents[0]]
    # Workers
    if len(agents)> 1: 
        workers = [AGENTs[worker] for worker in agents[1:]]
    else:
        workers = []
    # KBs
    KBs = []
    # TBs
    TBs = []
    # UNIVERSES
    UNIVs = build_list_universes(session_params)
    # SESSION
    session_dir = session_params.get('session_dir', create_session_dir())
    console.print(f"[INFO] Session directory: {session_dir}")
    ## Memory VS persist subdirectory
    memory_db_persist_sub_dir = session_params.get('memory_db_persist_sub_dir', 'memory')
    ## Summaries
    summaries_vs_params = session_params.get('summaries_params', None)
    if summaries_vs_params is None: # Defaults
        summaries_vs_params = copy.deepcopy(SUMMARIES_PARAMS)
        summaries_vs_params["persist_directory"] = f"{session_dir}/{memory_db_persist_sub_dir.strip().lstrip('./').rstrip('/')}"
    if verbose> 0: console.print(f"[INFO][MEMORY] Summaries params: {summaries_vs_params}")
    summaries_vs = VectorStore(summaries_vs_params)
    ## Traces
    traces_vs_params = session_params.get('traces_params', None)
    if traces_vs_params is None: # Defaults
        traces_vs_params = copy.deepcopy(TRACES_PARAMS)
        traces_vs_params["persist_directory"] = f"{session_dir}/{memory_db_persist_sub_dir.strip().lstrip('./').rstrip('/')}"
    if verbose> 0: console.print(f"[INFO][MEMORY] Traces params: {traces_vs_params}")
    traces_vs = VectorStore(traces_vs_params)
    # MANAGERs
    chat_manager = session_params.get('chat_manager', BaseChatManager(session_dir=session_dir) )
    memory_manager = session_params.get('memory_manager', MemoryManager(memory_path = os.path.join(session_dir, "memory.json"),
                                                                        traces_vector_store=traces_vs,
                                                                        summaries_vector_store=summaries_vs)
                                        )
    context_manager = session_params.get('context_manager', ContextManager(max_ctx_tokens=100000,
                                                                           recent_chat_ratio=0.50,
                                                                           memory_ratio=0.30,
                                                                           trace_ratio=0.20,
                                                                           traces_vector_store=traces_vs)
                                         )
    # INFRA
    INFRA = session_params.get('infra', BaseInfrastructure(agent=main_agent,
                                                           workers=workers,
                                                           objects=UNIVs,
                                                           max_ctx_tokens=session_params.get('max_ctx_tokens',50000),
                                                           wf_log_dir=session_dir,
                                                           chat_manager=chat_manager,
                                                           memory_manager=memory_manager,
                                                           context_manager=context_manager
                                                           )
                               )

    # WORKFLOW
    WF = session_params.get('wf', BaseWorkflow(infra=INFRA, 
                                               actions_union=session_params.get('actions', Actions) )
                            )
    session = {"agents":{'main':main_agent, 'workers':workers},
               'objects':{'universes':UNIVs,
                          'kbs':KBs,
                          'tbs':TBs},
               'managers':{'chat':chat_manager,
                           'memory':memory_manager,
                           'context':context_manager},
               'session_dir': session_dir,
               'wf': WF
               }
    return session
