from framework.utils.io_tools import clone_dict

DEFAULT_AIPORTAL_LLM_PARAMS = {"provider_name": "litellm",
                               "provider_host":"",
                               "provider_port":None,
                               "provider_endpoints": [],
                               "model":"",
                               "api_version":"v1",
                               "api_key_var":"LANL_AIPORTAL_API_KEY",
                               "verbose": 2,
                               "capabilities":[] #""capabilities":['structured_output']
                               }

INSTRUCT_CAPABLE_MODELS = ["gpt-oss-120b", "phi4"]
LANL_AIPORTAL_MODELS = ["NVIDIA-Nemotron-3-Super-120B-A12B-FP8", "gpt-oss-120b", "phi4"]

LANL_AIPORTAL_LLMs = {}

for model in LANL_AIPORTAL_MODELS:
    model_dict = {"model":model}
    LANL_AIPORTAL_LLMs[model] = clone_dict(template_dict=DEFAULT_AIPORTAL_LLM_PARAMS, replace=model_dict)
    if model in INSTRUCT_CAPABLE_MODELS: LANL_AIPORTAL_LLMs[model]['capabilities'].append('instructor')
