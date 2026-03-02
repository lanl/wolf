import os, copy
from datetime import datetime
from framework.utils.io_tools import console, load_env_vars

def create_session_dir():
    """Create a unique session directory under wf_workspace."""
    session_dir = "wf_workspace/session_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(session_dir, exist_ok=True)
    return session_dir


def set_llm_api_key(llm, env_vars=None, env_path=".env", 
                    api_key_handle="api_key",           # Making this flexible 
                    api_key_var_handle="api_key_var"):  # for changing the keys
    #console.print(f"[!][utils][io_tools][set_llm_api_key]: processing {llm}")
    if api_key_handle in llm.keys():
        console.print(f"[!][utils][io_tools][set_llm_api_key][DANGER]:")
        console.print(f"     * API KEY for LLM{llm['model']} on {llm['host']}:{llm['port']}")
        console.print(f"     provided unsafly. use the 'api_key_var' approach insted")
        return # API ke 
    keys = list(llm.keys())
    #if api_key_var_handle in keys: #llm.keys():
    #    console.print(f"[!][utils][io_tools][set_llm_api_key][API-KEY]: setting the api key")
    #else:
    #    console.print(f"[!][utils][io_tools][set_llm_api_key][API-KEY]: api_key_var_handle[{api_key_var_handle}] not found in keys[{keys}]")
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
    # And the LLM parameter keys
    #keys = list(llm.keys())
    # OK now let set the api_key:
    if (api_key_var_handle in keys ):
        #console.print(f"[!][utils][io_tools][set_llm_api_key][API-KEY]: {api_key_var_handle} found in llm keys")
        if llm[api_key_var_handle] in ENV_VAR_KEYs: 
            #console.print(f"[!][utils][io_tools][set_llm_api_key][API-KEY]: {llm[api_key_var_handle]} found in ENV_VAR_KEYs keys")
            ##llm["api_key"] = ENV_VARs[llm[api_key_var_handle]]
            llm[api_key_handle] = ENV_VARs[llm[api_key_var_handle]]
            #console.print(f"[!][utils][io_tools][set_llm_api_key][API-KEY]: llm[{api_key_handle}] set to {ENV_VARs[llm[api_key_var_handle]]}")
    return
