user_name = "user"
memory_db_persist_sub_dir = "memory_vector_stores"
banner_image_file = "config/preferences/banner/WOLF.png"
banner_image_width  = 100
banner_image_color  = 'purple' #'red', 'america', raimbow
Universes = [{"host":"0.0.0.0", "port":8115}]

from framework.utils.io_tools import image_to_ascii
from framework.utils.machines_ssl_config import conform_machine_ssl_certs

import asyncio
import os, copy, time, gc

from framework.utils.io_tools import load_env_vars, expand_dict, image_to_ascii
from framework.utils.config_tools import create_session_dir, set_llm_api_key
from framework.utils.machines_ssl_config import conform_machine_ssl_certs

from framework.agentic.agents import OpenAIAgent
from framework.workflows.workflow_models import Actions
from framework.workflows.chat_manager import BaseChatManager
from framework.workflows.memory_manager import MemoryManager
from framework.workflows.context_manager import ContextManager
from framework.workflows.workflow_infrastructure import BaseInfrastructure
from framework.workflows.agentic_workflows import BaseWorkflow
from framework.data_store.vstore import VectorStore

from config.defaults.deployment.embedding_params import Default_summaries_vs_params as SUMMARIES_PARAMS
from config.defaults.deployment.embedding_params import Default_traces_vs_params as TRACES_PARAMS
from config.defaults.deployment.llm_params import LANL_AIPORTAL_LLMs as LLMs

from framework.universes.data_models import BaseUniverseModel, BaseUniverseParams
from framework.universes.universe_tools import get_base_universe_params

import tiktoken, os
from pathlib import Path
from rich.console import Console
console = Console()

cache_dir = (Path.cwd() / ".tiktoken_cache").resolve()
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(cache_dir))
cache_dir.mkdir(parents=True, exist_ok=True)
os.environ['CURL_CA_BUNDLE'] = '/etc/ssl/ca-bundle.pem'

console.print("|=================================================================================|")
image_to_ascii(banner_image_file, width=banner_image_width, flag=banner_image_color)
console.print("|=================================================================================|")


if __name__ == "__main__":
    AGENTs = {}
    ENV_VARs = load_env_vars()
    # Construct the LLMs
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
    agents = list(AGENTs.keys())
    main_agent = AGENTs[agents[0]]
    workers = [AGENTs[worker] for worker in agents[1:]]

    # Create session directory
    session_dir = create_session_dir()
    print(f"[INFO] Session directory: {session_dir}")

    # Setup vector stores under session_dir
    ## Summaries
    summaries_vs_params = copy.deepcopy(SUMMARIES_PARAMS)
    #embedding_function_params = summaries_vs_params["embedding_function_params"]
    #set_llm_api_key(summaries_vs_params["embedding_function_params"], env_vars=ENV_VARs)
    summaries_vs_params["persist_directory"] = f"{session_dir}/{memory_db_persist_sub_dir.strip().lstrip('./').rstrip('/')}"
    #print(f"[INFO][MEMORY] Summaries vstore Params: {expand_dict(summaries_vs_params,dept=2)}")
    ## Traces
    traces_vs_params = copy.deepcopy(TRACES_PARAMS)
    #set_llm_api_key(traces_vs_params["embedding_function_params"], env_vars=ENV_VARs)
    traces_vs_params["persist_directory"] = f"{session_dir}/{memory_db_persist_sub_dir.strip().lstrip('./').rstrip('/')}"
    #print(f"[INFO][MEMORY] Traces vstore Params: {expand_dict(traces_vs_params,dept=2)}")

    # Use shared persist_directory: same client, separate collections
    traces_vs = VectorStore(traces_vs_params)
    summaries_vs = VectorStore(summaries_vs_params)

    # Setup managers with session_dir
    chat_manager = BaseChatManager(session_dir=session_dir)
    memory_manager = MemoryManager(
        memory_path=os.path.join(session_dir, "memory.json"),
        traces_vector_store=traces_vs,
        summaries_vector_store=summaries_vs
    )
    context_manager = ContextManager(
        max_ctx_tokens=10000,
        recent_chat_ratio=0.30,
        memory_ratio=0.50,
        trace_ratio=0.20,
        traces_vector_store=traces_vs
    )
    # UNIVERSES
    UNIVs = []
    for univ in Universes:
        univ_param = get_base_universe_params(host=univ["host"], port=univ["port"], verbose=1)
        if univ_param is not None:
            UNIVs.append(univ_param)
        else:
            print(f"[!][WOLF][INTERACTIVE][WARN] Unable to read universe [{univ}]'s parameters")
    # Build infrastructure with new managers and session_dir
    INFRA = BaseInfrastructure(
        agent=main_agent,
        workers=workers,
        objects=UNIVs,
        max_ctx_tokens=10000,
        wf_log_dir=session_dir,
        chat_manager=chat_manager,
        memory_manager=memory_manager,
        context_manager=context_manager
    )

    # Build workflow with new infrastructure
    WF = BaseWorkflow(infra=INFRA, actions_union=Actions)

    # Run workflow
    WF.run(user_name=user_name)
