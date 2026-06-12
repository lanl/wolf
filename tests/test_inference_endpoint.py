ENDPOINT    = "https://"
#PORT        = 8000 # Comment out if no specific  port on the endpoint
API_VERSION = "v1"  # Comment out if no specific api version on the endpoint
MODEL       = "gpt-oss-120b"
#########
API_KEY_VAR = "LANL_AIPORTAL_API_KEY" # Variable holding your API key in your .env file
############################################################################
import openai
from framework.utils.config_tools import set_llm_api_key
try:
    ADDREESS=f"{ENDPOINT}:{PORT}"
except:
    ADDREESS=f"{ENDPOINT}"
try:
    BASE_URL = f"{ADDREESS}/{API_VERSION}"
except:
    BASE_URL = ADDREESS

endpoint_params = {"base_url": BASE_URL,
                   "api_key_var": API_KEY_VAR}

set_llm_api_key(endpoint_params)
client = openai.OpenAI(
    api_key=endpoint_params["api_key"],
    base_url=endpoint_params["base_url"] # LiteLLM Proxy is OpenAI compatible, Read More: https://docs.litellm.ai/docs/proxy/user_keys
)

response = client.chat.completions.create(
        model=MODEL, # model to send to the proxy
    messages = [
        {
            "role": "user",
            "content": "Hello"
        }
    ]
)

print(response)
