from typing import Any, Type
from framework.utils.class_helper import pydantic_discriminated_union_builder, get_class_by_discriminator
from config.llm.base_provider import Base_LLM_Provider

KNOWN_LLM_Providers = pydantic_discriminated_union_builder(
    base_cls=Base_LLM_Provider,
    sub_dir="known_providers",
    discriminator="name",
    base_package="config.llm",
)

def get_provider_class(discriminated_union: Any, name: str) -> Type[Any]:
    """Convenient alias for :func:`get_class_by_discriminator`.

    Parameters
    ----------
    discriminated_union:
        The ``Annotated`` union created by ``pydantic_discriminated_union_builder``.
    name:
        The discriminator value (e.g., provider name).

    Returns
    -------
    Type[Any]
        The matching concrete class.
    """
    return get_class_by_discriminator(discriminated_union, name)
