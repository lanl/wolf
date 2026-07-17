from framework.utils.class_helper import get_class_by_discriminator
from framework.utils.config_tools import set_llm_api_key

from config.llm.providers import KNOWN_LLM_Providers
from config.llm.base_llm import Base_LLM 

from framework.agentic.agents import OpenAIAgent 

# 1. Use the union and the helper to get the specific provider class
# Replace "openai" with whatever the 'name' value is in your concrete provider classes
OpenAIProvider = get_class_by_discriminator(KNOWN_LLM_Providers, "openai")
print(f"[+] Successfully retrieved class for 'openai': {OpenAIProvider}")

# 2 Build the specialized provider from the default OpenAI compatible provider params
ShirtyProvider = OpenAIProvider(
        name="openai",
        host="https://shirty.sandia.gov",
        port=None,
        api_key_var="SHIRTY_API_KEY",
        api_version = "api/v1",
        endpoints=["/v1/chat/completions"], # Example endpoints
        capabilities=["tools", "vision"]
    )
print(f"[+] Successfully built 'Sandia/Shirty' Provider: {ShirtyProvider}")

# 3 Build the LLM from the provider
Gemma4 = Base_LLM(ShirtyProvider, model="google/gemma-4-31B-it" )
print(f"[+] Successfully built 'google/gemma-4-31B-it' Model: {Gemma4}")

# 4 Let's not set the super secret API KEY by reading it out of our local .env file
set_llm_api_key(Gemma4.params, env_path=".env",
                api_key_handle="api_key",           # Key to store the API key in
                api_key_var_handle="api_key_var"  # ENV VAR carying the API KEY
               )
print(f"[+] Infereence Engine API KEY Setup OK")
#print(f"[+]     API KEY = {Gemma4.params['api_key']}") # if it is safe to do so

# Now Let's create an Agent and inference the endpoint
agent = OpenAIAgent( model = Gemma4.params['model'],
        host_address=Gemma4.params['host'],
        host_port = Gemma4.params['port'],
        api_key = Gemma4.params['api_key'],
        api_version=Gemma4.params['api_version'],
        capabilities=Gemma4.params['capabilities']
        )
print(f"[+] Successfully built Gemma agent: {agent}")
RSP = agent.get_chat_response("Hello")
print(f"[+] Agent's response = {RSP}")
