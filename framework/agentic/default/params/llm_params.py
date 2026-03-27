import copy

LocalInferenceEngine = {"host":"http://localhost", "port":4444, "api_version":"v1","api_key_var":"LOCAL_API_KEY", "verbose": 2, "capabilities":[]}
AvailableLocalModels = ["gpt-oss:20b-128k","gpt-oss:120b-128k","nemotron-3-nano:30b-256k","nemotron-3-nano-Instruct","nemotron-3-nano-tool"]
know_ctx_win_length  = {"gpt-oss-120b":131072,
                        "gpt-oss-20b":131072,
                        "NVIDIA-Nemotron-3-Super-120B-A12B-FP8":1048576
                        }

def set_ctx_win_len(llms, llm_max_ctx=know_ctx_win_length):
    llms_with_known_ctx_len = list(llm_max_ctx.keys())
    for llm in list(llms.keys()):
        model = llms[llm]["model"]
        if model in llms_with_known_ctx_len:
            llms[llm] ["ctx_window_length"] = llm_max_ctx[model]



AvailableLLMs = {}
for llm in AvailableLocalModels: AvailableLLMs[llm]=copy.deepcopy(LocalInferenceEngine)

LANL_AIPORTAL_LLMs = {"llm0": {"host":"https://aiportal-api.aws.lanl.gov",
                               "port":None,
                               #"model":"anthropic.claude-sonnet-4-5-20250929-v1:0",
                               #"model":"NVIDIA-Nemotron-3-Super-120B-A12B-FP8",
                               "model":"gpt-oss-120b",
                               "api_version":"v1",
                               "api_key_var":"LANL_AIPORTAL_API_KEY",
                               "verbose": 2,
                               "capabilities":[] #""capabilities":['structured_output']
                               },
                      "llm1": {"host":"https://aiportal-api.aws.lanl.gov",
                               "port":None,
                               "model":"gpt-oss-120b",
                               "api_version":"v1",
                               "api_key_var":"LANL_AIPORTAL_API_KEY",
                               "verbose": 2,
                               "capabilities":[] #"capabilities":['structured_output']
                               },
                      }

set_ctx_win_len(LANL_AIPORTAL_LLMs, llm_max_ctx=know_ctx_win_length)

LocalLLMs = {"llm0": {"host":"http://localhost",
                  "port":4444,
                  "model":"gpt-oss:20b-128k",
                  #"model":"gpt-oss:120b-128k",
                  "api_version":"v1",
                  "api_key_var":"LOCAL_API_KEY",
                  "verbose": 2,
                  "capabilities":[]
                  },
            "llm1": {"host":"http://localhost",
                  "port":4444,
                  #"model":"gpt-oss:20b-128k",
                  "model":"gpt-oss:120b-128k",
                  "api_version":"v1",
                  "api_key_var":"LOCAL_API_KEY",
                  "verbose": 2,
                  "capabilities":[]
                  }
        }
set_ctx_win_len(LocalLLMs, llm_max_ctx=know_ctx_win_length)
Local_20b_parallel = {}
for i in range(4):
    Local_20b_parallel[f"llm{i}"]=copy.deepcopy(LocalInferenceEngine)
    Local_20b_parallel[f"llm{i}"]["model"]="gpt-oss:20b-128k"

set_ctx_win_len(Local_20b_parallel, llm_max_ctx=know_ctx_win_length)
