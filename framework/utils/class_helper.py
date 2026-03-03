from __future__ import annotations

import importlib
import pkgutil
import warnings
from typing import Any, Dict, List, Tuple, Type, TypeVar, Union, Annotated, get_args

from pydantic import Field

# ---------------------------------------------------------------------
# Utility for dynamically collecting concrete subclasses and building
# Pydantic discriminated unions.
# ---------------------------------------------------------------------

T = TypeVar("T")

# Simple in‑memory cache to avoid repeated imports and walks.
# Keyed by (package_name, base_class_name).
_CACHE: Dict[Tuple[str, str], List[Type[Any]]] = {}


def _collect_action_classes(
    base_cls: Type[T],
    sub_dir: str,
    base_package: str | None = None,
) -> List[Type[T]]:
    """Return every concrete subclass of *base_cls* found under a package.

    Parameters
    ----------
    base_cls:
        The base class whose concrete subclasses should be discovered.
    sub_dir:
        The sub‑directory (module) that contains the concrete implementations.
    base_package:
        Optional explicit package name to start the search from.  If omitted the
        package of this helper (``__package__``) is used.  Supplying an explicit
        package makes the helper portable from any location in the code base.

    Returns
    -------
    List[Type[T]]
        A list of concrete subclasses (including indirect descendants).
    """
    # Resolve the full package name.
    package_name = f"{base_package or __package__}.{sub_dir}".strip(".")
    cache_key = (package_name, base_cls.__name__)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    try:
        package = importlib.import_module(package_name)
    except ImportError as exc:
        raise ImportError(f"Unable to import package '{package_name}' for dynamic class collection") from exc

    # Import all sub‑modules so that their classes are registered with ``__subclasses__``.
    if hasattr(package, "__path__"):
        for _, module_name, _ in pkgutil.iter_modules(package.__path__):
            full_name = f"{package_name}.{module_name}"
            try:
                importlib.import_module(full_name)
            except Exception as exc:  # pragma: no cover
                warnings.warn(f"Failed to import module '{full_name}': {exc}")

    def _walk(cls: Type[Any]) -> List[Type[Any]]:
        found: List[Type[Any]] = []
        for sub in cls.__subclasses__():
            found.append(sub)
            found.extend(_walk(sub))
        return found

    result: List[Type[T]] = _walk(base_cls)
    _CACHE[cache_key] = result
    return result


def pydantic_discriminated_union_builder(
    base_cls: Type[Any],
    sub_dir: str,
    discriminator: str,
    base_package: str | None = None,
) -> Any:
    """Build a ``Annotated[Union[...], Field(discriminator=...) ]`` for Pydantic.

    The function collects all subclasses via :func:`_collect_action_classes`,
    validates that each class defines the discriminator field, sorts them by the
    field's default value (if present), and returns the annotated union type.
    """
    classes = _collect_action_classes(base_cls, sub_dir, base_package)

    # Ensure every class defines the discriminator field.
    missing = [cls.__name__ for cls in classes if discriminator not in getattr(cls, "model_fields", {})]
    if missing:
        raise ValueError(
            f"The following classes do not define the discriminator field '{discriminator}': {', '.join(missing)}"
        )

    # Sort by the default value of the discriminator field for deterministic ordering.
    sorted_classes = sorted(
        classes,
        key=lambda cls: getattr(cls.model_fields.get(discriminator), "default", ""),
    )

    # Build the Union type. ``typing.Union`` expects __args__ tuple; using ``Union[tuple]`` is safe.
    union_type = Union[tuple(sorted_classes)]  # type: ignore[arg-type]
    return Annotated[union_type, Field(discriminator=discriminator)]


def get_class_by_discriminator(
    discriminated_union: Any,
    discriminator_value: str,
) -> Type[Any]:
    """Return the concrete subclass from a discriminated union matching *discriminator_value*.

    This helper works with unions created by :func:`pydantic_discriminated_union_builder`.
    It extracts the underlying ``Union`` type from the ``Annotated`` wrapper and
    searches for a class whose ``name`` (or other discriminator field) attribute
    equals *discriminator_value*.

    Parameters
    ----------
    discriminated_union:
        The ``Annotated`` union returned by ``pydantic_discriminated_union_builder``.
    discriminator_value:
        The value of the discriminator field (e.g., ``"openai"``).

    Returns
    -------
    Type[Any]
        The matching concrete provider class.

    Raises
    ------
    ValueError
        If no matching class is found or the supplied type is not a valid
        discriminated union.
    """
    # Extract the Union type from the Annotated wrapper.
    try:
        union_type = get_args(discriminated_union)[0]
    except Exception as exc:
        raise ValueError("Provided type is not an Annotated discriminated union") from exc

    # Iterate over concrete classes in the Union.
    for cls in getattr(union_type, "__args__", []):
        # Most providers expose the discriminator via a ``name`` attribute.
        if getattr(cls, "name", None) == discriminator_value:
            return cls

    raise ValueError(f"Unknown discriminator value: {discriminator_value}")

