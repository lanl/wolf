API_KEY_VAR = "LANL_AIPORTAL_API_KEY" # Variable holding your API key in your .env file

import time
import instructor
from litellm import completion
from pydantic import BaseModel
# from config.defaults.deployment.llm_params import LANL_AIPORTAL_LLMs as LLMs
from config.defaults.llm_params import LANL_AIPORTAL_LLMs as LLMs
from config.llm.providers import KNOWN_LLM_Providers
from config.llm.base_llm import Base_LLM
from framework.utils.class_helper import get_class_by_discriminator
from framework.utils.config_tools import set_llm_api_key

class User(BaseModel):
    name: str
    age: int

for MODEL in LLMs.keys():
    try:
        # Retrieve LLM parameters
        llm_params = LLMs[MODEL] #LLMs[list(LLMs.keys())[2]]
        
        # Set API key for the LLM
        set_llm_api_key(llm_params)
        
        # Initialize Base_LLM and obtain provider
        LLM = Base_LLM(llm_params)
        llm = LLM.params
        llm_provider = LLM.get_provider()
        
        # Build base URL for the provider
        base_url = f"{llm['provider_host']}"
        if llm['provider_port'] is not None:
            base_url = f"{base_url}:{llm['provider_port']}"
        if llm['api_version'] is not None:
            base_url = f"{base_url}/{llm['api_version']}"
        
        # Create Instructor client
        client = instructor.from_provider(
            f"openai/{llm['model']}",
            base_url=base_url,
            api_key=llm['api_key'],
            async_client=False
        )
        
        def extract_user(text: str):
            """Extract a ``User`` instance from *text* using the LLM client.
            """
            return client.chat.completions.create(
                model=llm['model'],
                response_model=User,
                messages=[{"role": "user", "content": text}],
                max_retries=3,
            )
        
        # Test the extraction logic
        user = extract_user("Jason is 25 years old")
        
        assert isinstance(user, User)
        assert user.name == "Jason"
        assert user.age == 25
        print(f"{user=}")
        print(f"[+] Model {MODEL} is capable of instructor structured output")
    except Exception as TestERR:
        print(f"[!] Model {MODEL} is NOT capable of instructor structured output:\n   ERROR = {TestERR}")
    time.sleep(5)


