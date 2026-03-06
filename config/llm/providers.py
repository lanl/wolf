from typing import Any, Type
from framework.utils.class_helper import pydantic_discriminated_union_builder, get_class_by_discriminator
from config.llm.base_provider import Base_LLM_Provider

KNOWN_LLM_Providers = pydantic_discriminated_union_builder(
    base_cls=Base_LLM_Provider,
    sub_dir="known_providers",
    discriminator="name",
    base_package="config.llm",
)
