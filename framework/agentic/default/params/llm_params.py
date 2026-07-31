import copy
from framework.agentic.default.params.known_llm_ctx_win_len import know_ctx_win_length
from framework.agentic.default.params.ctx_config_utils import set_ctx_win_len

# Local
LocalInferenceEngineParams = {"host":"http://localhost",
                              "port":None, 
                              "api_version":"v1",
                              "api_key_var":"LOCAL_API_KEY", 
                              "verbose": 2, 
                              "capabilities":[]
                             }
AvailableLocalModels = ["gpt-oss:20b-128k","gpt-oss:120b-128k","nemotron-3-nano:30b-256k","nemotron-3-nano-Instruct","nemotron-3-nano-tool"]
LocalLLMs = {}
for id, model in enumerate(AvailableLocalModels):
    LocalLLMs[f"llm{id}"] = copy.deepcopy(LocalInferenceEngineParams)
    LocalLLMs[f"llm{id}"]["model"]=model

