from framework.utils.io_tools import clone_dict

DEFAULT_AIPORTAL_LLM_PARAMS = {"provider_name": "litellm",
                               "provider_host":"https://aiportal-api.aws.lanl.gov",
                               "provider_port":None,
                               "provider_endpoints": [],
                               "model":"",
                               "api_version":"v1",
                               "api_key_var":"LANL_AIPORTAL_API_KEY",
                               "verbose": 2,
                               "capabilities":[] #""capabilities":['structured_output']
                               }

#LANL_AIPORTAL_MODELS = ["gpt-oss-20b","gpt-oss-120b",
#                        "mistral7b", "darwin.Magistral-Small-2509", "darwin.Mistral-Small-3.2-24B-Instruct-2506",
#                        "darwin.Llama-4-Scout-17B-16E-Instruct", "tenstorrent.Llama-3.3-70B-Instruct",
#                        "meta.llama3-70b-instruct-v1:0", "meta.llama3-8b-instruct-v1:0",
#                        "phi4","darwin.gemma-3-27b-it", "llama3bv32instr"
#                        "anthropic.claude-sonnet-4-5-20250929-v1:0"]
INSTRUCT_CAPABLE_MODELS = ["gpt-oss-120b", "phi4"]
LANL_AIPORTAL_MODELS = ["NVIDIA-Nemotron-3-Super-120B-A12B-FP8", "gpt-oss-120b", "phi4"]

LANL_AIPORTAL_LLMs = {}

for model in LANL_AIPORTAL_MODELS:
    model_dict = {"model":model}
    LANL_AIPORTAL_LLMs[model] = clone_dict(template_dict=DEFAULT_AIPORTAL_LLM_PARAMS, replace=model_dict)
    if model in INSTRUCT_CAPABLE_MODELS: LANL_AIPORTAL_LLMs[model]['capabilities'].append('instructor')
