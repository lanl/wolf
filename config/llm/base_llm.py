from framework.utils.io_tools import clone_dict
from typing import Type, NewType


from config.llm.base_provider import Base_LLM_Provider, base_llm_provider_type 
from config.llm.providers import KNOWN_LLM_Providers
from framework.utils.class_helper import get_class_by_discriminator



class Base_LLM:
    def __init__(self, llm_params: dict|base_llm_provider_type, model: str|None=None):
        """Base class for every LLM model.
        """
        MODEL_IS_NONE = True
        if model is None:
            if isinstance(llm_params, Base_LLM_Provider):
                MODEL_IS_NONE = True
            elif isinstance(llm_params, dict): 
                if "model" in self.params.keys():
                    self.model = self.params["model"]
                    MODEL_IS_NONE = False
                else:
                    MODEL_IS_NONE = True
            else:
                raise NotImplementedError( f"""Base_LLM() is not implemented for {type(llm_params)} types of parameters.""")
        else:
            self.model = model
            MODEL_IS_NONE = False
        
        if MODEL_IS_NONE: raise Exception( f"""[Base_LLM()] a model name was not provided for the instance of BaseLLM.""")

        self.params = None
        self.set_params(llm_params)
    def set_params(self, llm_params: dict|base_llm_provider_type):
        if isinstance(llm_params, Base_LLM_Provider):
            self.params = clone_dict(llm_params.model_dump())
            self.params["model"] = self.model
        elif isinstance(llm_params, dict):
            self.params = clone_dict(llm_params)
        else:
            raise NotImplementedError( f"""Base_LLM() is not implemented for {type(llm_params)} types of parameters.""")
    def get_provider(self):
        llm_provider_cls = get_class_by_discriminator(discriminated_union=KNOWN_LLM_Providers,
                                                  discriminator_value=self.params['provider_name'])
        blank_llm_provider = llm_provider_cls()
        provider_params_list = blank_llm_provider.model_dump()
        provider_params = list(provider_params_list.keys())
        model_params = list(self.params.keys())
        params = {}
        for pr in model_params:
            if pr in provider_params: params[pr] = self.params[pr]
        return llm_provider_cls(**clone_dict(params)) # Return the real provider

    def get_provider_params(self):
        provider = self.get_provider()
        return provider.model_dump()

base_llm_type = NewType('base_llm_type', Type[Base_LLM])
