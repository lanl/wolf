import copy

LocalInferenceEngine = {"host":"http://localhost", "port":4444, "api_version":"v1","api_key_var":"LOCAL_API_KEY", "verbose": 2, "capabilities":[]}
AvailableLocalModels = ["gpt-oss:20b-128k","gpt-oss:120b-128k","nemotron-3-nano:30b-256k","nemotron-3-nano-Instruct","nemotron-3-nano-tool"]


AvailableLLMs = {}
for llm in AvailableLocalModels: AvailableLLMs[llm]=copy.deepcopy(LocalInferenceEngine)

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

Local_20b_parallel = {}
for i in range(4):
    Local_20b_parallel[f"llm{i}"]=copy.deepcopy(LocalInferenceEngine)
    Local_20b_parallel[f"llm{i}"]["model"]="gpt-oss:20b-128k"


Cyberwolf01LLMs = copy.deepcopy(LocalLLMs)
DeltaLabsLLMs   = copy.deepcopy(LocalLLMs)
ZiaLabsLLMs     = copy.deepcopy(LocalLLMs)

