API_KEY_VAR = "LANL_AIPORTAL_API_KEY" # Variable holding your API key in your .env file

import time, copy
import instructor
from litellm import completion
# from config.defaults.deployment.llm_params import LANL_AIPORTAL_LLMs as LLMs
from config.defaults.llm_params import LANL_AIPORTAL_LLMs as LLMs
from config.llm.providers import KNOWN_LLM_Providers
from config.llm.base_llm import Base_LLM
from framework.utils.class_helper import get_class_by_discriminator
from framework.utils.config_tools import set_llm_api_key


def get_know_llms(models:[str], api_key_var=None):
    KNOW_MODELS = list(LLMs.keys())
    MATCHED_LLMS = {}
    for model in models:
        MATCHED_LLMS[model] = {'model':model}
        # Retrieve LLM parameters
        try:
            llm_params = LLMs[model]
        except Exception as UnkModelErr:
            print(f"[!] Model {model} is NOT KNOWN:\n   ERROR = {TestERR}")
            MATCHED_LLMS[model]['params'] = None
            MATCHED_LLMS[model]['provider'] = None
            MATCHED_LLMS[model]['base_url'] = None
            MATCHED_LLMS[model]['client'] = None
            continue
        MATCHED_LLMS[model]['params'] = llm_params

        # Set API key for the LLM
        if api_key_var is not None: set_llm_api_key(llm_params)

        # Initialize Base_LLM
        LLM = Base_LLM(llm_params)
        try:
            llm = LLM.params
        except Exception as LLM_PARAMS_ERR:
            MATCHED_LLMS[model]['params'] = None
            MATCHED_LLMS[model]['provider'] = None
            MATCHED_LLMS[model]['base_url'] = None
            MATCHED_LLMS[model]['client'] = None
            print(f"[!] Unable to set model {model} params:\n   ERROR ={LLM_PARAMS_ERR}")
            continue
        MATCHED_LLMS[model]['params'] = llm

        # Obtain provider
        try:
            llm_provider = LLM.get_provider()
        except Exception as LLM_PROVIDER_ERR:
            MATCHED_LLMS[model]['provider'] = None
            MATCHED_LLMS[model]['base_url'] = None
            MATCHED_LLMS[model]['client'] = None
            print(f"[!] Unable to get modelmodel {model}'s provider:\n   ERROR ={LLM_PARAMS_ERR}")
            continue
        MATCHED_LLMS[model]['provider'] = llm_provider


        # Build base URL for the provider
        try:
            base_url = f"{llm['provider_host']}"
            if llm['provider_port'] is not None:
                base_url = f"{base_url}:{llm['provider_port']}"
            if llm['api_version'] is not None:
                base_url = f"{base_url}/{llm['api_version']}"
        except Exception as BaseURL_ERR:
            MATCHED_LLMS[model]['base_url'] = None
            MATCHED_LLMS[model]['client'] = None
            print(f"[!] Unable to derive {model}'s rbase url:\n   ERROR ={BaseURL_ERR}")
            continue
        MATCHED_LLMS[model]['base_url'] = base_url 
        

        # Create Instructor client
        try:
            client = instructor.from_provider(
                f"openai/{llm['model']}",
                base_url=base_url,
                api_key=llm['api_key'],
                async_client=False
            )
        except Exception as CLIENT_ERR:
            MATCHED_LLMS[model]['client'] = None
            print(f"[!] Unable to configure {model}'s client:\n   ERROR ={BaseURL_ERR}")
            continue
        MATCHED_LLMS[model]['client'] = client

    return MATCHED_LLMS



def get_llms(LLMs, api_key_var=None):
    MATCHED_LLMS = {}
    for MODEL in LLMs.keys():
        MATCHED_LLMS[model] = {'model':model}
        try:
            # Retrieve LLM parameters
            llm_params = LLMs[MODEL]
            
            # Set API key for the LLM
            set_llm_api_key(llm_params)
            
            # Initialize Base_LLM and obtain provider
            LLM = Base_LLM(llm_params)
            MATCHED_LLMS[model]['LLM'] = LLM
        except Exception as LLM_ERROR:
            MATCHED_LLMS[model]['LLM'] = None
            print(f"[!] Unable to set model {model} params:\n   ERROR ={LLM_PARAMS_ERR}")
            continue
    return MATCHED_LLMS

