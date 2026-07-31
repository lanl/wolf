user_name = "user"
params = {'banner_image_width': 100,
          'banner_image_color': 'purple', #'red', 'america', raimbow
          #'universes': [{"host":"0.0.0.0", "port":8115, "scheme":"http"}],
          'verbose': 0
          }

import asyncio
import os, copy, time, gc
import tiktoken

from config.session.default.params.inputs import session_params
from framework.utils.config_tools import CliSession #setup_cli_session
# Import LLM params 
#from framework.agentic.default.params.llm_params import LocalLLMs as LLMs
#from framework.agentic.default.params.llm_params_lanl import LANL_AIPORTAL_LLMs as LLMs
from framework.agentic.default.params.llm_params_sandia import ShirtyLLMs as LLMs

""" Seesion inputus can be found in  config/session/default/params/inputs.py: make sure params:
session_params = {"tiktoken_cache_dir": (Path.cwd() / ".tiktoken_cache").resolve(),
                  "curl_ca_bundle_file": None.
                  "banner_image_file": f"{(Path.cwd() / 'config/preferences/banner').resolve()}/WOLF.png".strip(),
                  "banner_image_color": "purple",
                  "banner_image_width": 100,
                  "universes":[],
                  "kbs":[],
                  "tbs":[],
                  "session_dir": None,
                  "memory_db_persist_sub_dir": None,
                  "summaries_params": None,
                  "traces_params": None,
                  "chat_manager": None,
                  "memory_manager": None,
                  "context_manager": None,
                  "infra": None,
                  "actions":  None,
                  "max_ctx_tokens": None,
                  "wf":  None,
                  "verbose":0
                  }

"""
if __name__ == "__main__":

    # Run workflow
    session_inputs = copy.deepcopy(session_params)
    s_params = list(session_inputs.keys())
    for k in params.keys():
        if k in s_params:
            session_inputs[k] = params[k]
        else:
            raise Exception("[!] {k} is not a recognised session input")
    session_inputs['LLMs'] = LLMs # Not well implemented. Need to do better
    #session = setup_cli_session(session_inputs, resume_session=None)
    #session = setup_cli_session(session_inputs, resume_session="20260528_124756")
    #session = setup_cli_session(session_inputs, resume_session="last")
    cli_session = CliSession(session_params=session_inputs, db_client=None)
    cli_session.create_session(resume_session=None)
    #cli_session.create_session(resume_session="20260716_082240")
    cli_session.session['wf'].run(user_name=user_name)
