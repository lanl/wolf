import copy

LocalInferenceEngine = {"host":"http://localhost", "port":4444, "api_version":"v1","api_key_var":"LOCAL_API_KEY", "verbose": 2, "capabilities":[]}
AvailableLocalModels = ["gpt-oss:20b-128k","gpt-oss:120b-128k","qwen3-coder-next","nemotron-3-nano:30b-256k","nemotron-3-nano-Instruct","nemotron-3-nano-tool","glm-4.7-flash-tool"]


AvailableLLMs = {}
for llm in AvailableLocalModels: AvailableLLMs[llm]=copy.deepcopy(LocalInferenceEngine)

LANL_AIPORTAL_LLMs = {"llm0": {"host":"https://aiportal-api.aws.lanl.gov",
                               "port":None,
                               #"model":"anthropic.claude-sonnet-4-5-20250929-v1:0",
                               "model":"gpt-oss-120b",
                               "api_version":"v1",
                               "api_key_var":"LANL_AIPORTAL_API_KEY",
                               "verbose": 2,
                               "capabilities":[]
                               },
                      "llm1": {"host":"https://aiportal-api.aws.lanl.gov",
                               "port":None,
                               "model":"gpt-oss-120b",
                               "api_version":"v1",
                               "api_key_var":"LANL_AIPORTAL_API_KEY",
                               "verbose": 2,
                               "capabilities":[]
                               },
                      }

LocalLLMs = {"llm0": {"host":"http://localhost",
                  "port":4444,
                  "model":"gpt-oss:20b-128k",
                  #"model":"gpt-oss:120b-128k",
                  #"model":"qwen3-coder-next",
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

Local_20b_parallel = {}
for i in range(4):
    Local_20b_parallel[f"llm{i}"]=copy.deepcopy(LocalInferenceEngine)
    Local_20b_parallel[f"llm{i}"]["model"]="gpt-oss:20b-128k"


Cyberwolf01LLMs = copy.deepcopy(LocalLLMs)
DeltaLabsLLMs   = copy.deepcopy(LocalLLMs)
ZiaLabsLLMs     = copy.deepcopy(LocalLLMs)

for llm in LocalLLMs.keys(): 
    Cyberwolf01LLMs[llm]["host"] = "http://cyberwolf01"
    DeltaLabsLLMs[llm]["host"]   = "https://inference.deltalabs.cloud"
    ZiaLabsLLMs[llm]["host"]     = "http://cyberwolf01.netbird.selfhosted"   

