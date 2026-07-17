#from framework.utils.io_tools import load_env_vars
from framework.utils.io_tools import copy, USER_ENV_VARs

ENV_VARs = copy.deepcopy(USER_ENV_VARs)
ENV_VAR_KEYs = ENV_VARs.keys()

# 1. Default Local Engine
Provider_params = { 'provider_type': "openai",                    # the type of inference engine i.e both 'openai' and 'open ai compatible server' = 'openai'
                  'host'           : "https://shirty.sandia.gov", # URL of the node hosting the engine
                  'port'           : None,                        # Port on whitch it is listening i.e 8000 for vllm
                  'api_key_var'    : "LOCAL_API_KEY",             # The variable holding your API key in your .env file
                  'api_version'    : "api/v1",                    # The version of the API
                  'verbose'        : 2                            # Inference verbosity
                }
Model_params    = { 'model'        : None,                        # Name of the model as served by the inference engine i.e 'google/gema4'
                  "capabilities"   : []                           # The model capabilities i.e ['vision', 'tool',....]
}

# 2. Overide with parameters from .env
#ENV_VARs = load_env_vars(env_path) # Load ENV VAriable from .env file
#ENV_VAR_KEYs = ENV_VARs.keys()
# 2.1 Engine Params from .env
if "INFERENCE_HOST_ADDRESS" in ENV_VAR_KEYs: Provider_params['host'] = ENV_VARs['INFERENCE_HOST_ADDRESS']
if "INFERENCE_HOST_PORT"    in ENV_VAR_KEYs: Provider_params['port']    = ENV_VARs['INFERENCE_HOST_PORT']
if "LOCAL_API_KEY_VAR"      in ENV_VAR_KEYs: Provider_params['api_key_var'] = ENV_VARs['LOCAL_API_KEY_VAR']
if "API_VERSION"            in ENV_VAR_KEYs: Provider_params['api_version'] = ENV_VARs['API_VERSION']
# 2.2.Model Params from .env
if "LLM_MODEL"              in ENV_VAR_KEYs: Model_params['model'] = ENV_VARs['LLM_MODEL']
if 'LLM_CAPABILITIES'       in ENV_VAR_KEYs: Model_params['capabilities'] = ENV_VARs['LLM_CAPABILITIES']


LLM_params = {'provider': Provider_params, 
              'llm' : Model_params}

# Build LLM
Model_name = "llm0"
if Model_params['model'] is not None: Model_name = Model_params['model']
LLM = {Model_name: copy.deepcopy(Provider_params)}
for k in Model_params.keys(): LLM[Model_name][k] =  Model_params[k]
