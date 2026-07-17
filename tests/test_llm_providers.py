from framework.utils.class_helper import get_class_by_discriminator
from config.llm.providers import KNOWN_LLM_Providers

# 1. Use the union and the helper to get the specific provider class
# Replace "openai" with whatever the 'name' value is in your concrete provider classes
provider_name = "openai"
try:
    ProviderClass = get_class_by_discriminator(KNOWN_LLM_Providers, provider_name)
    print(f"Successfully retrieved class for {provider_name}: {ProviderClass}")

    # 2. Now you can instantiate the class with the required fields
    # Note: endpoints must match the T_Endpoints type defined in the provider
    provider_instance = ProviderClass(
        name=provider_name,
        host="localhost",
        port=8000,
        endpoints=["/v1/chat/completions"], # Example endpoints
        capabilities=["tools", "vision"]
    )

    print(f"Provider instance created: {provider_instance}")
    # print(provider_instance.get_client())

except ValueError as e:
    print(f"Error: {e}")
