from __future__ import annotations

import importlib
import pkgutil
import warnings
from typing import Any, Dict, List, Tuple, Type, TypeVar, Union, Annotated, get_args

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------
# Utility for dynamically collecting concrete subclasses and building
# Pydantic discriminated unions.
# ---------------------------------------------------------------------

T = TypeVar("T")

# Simple in‑memory cache to avoid repeated imports and walks.
# Keyed by (package_name, base_class_name).
_CACHE: Dict[Tuple[str, str], List[Type[Any]]] = {}


def _collect_classes(
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
    print(f"[++] Dynamic import on package: {package_name}")
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

    The function collects all subclasses via :func:`_collect_classes`,
    validates that each class defines the discriminator field, sorts them by the
    field's default value (if present), and returns the annotated union type.
    """
    classes = _collect_classes(base_cls, sub_dir, base_package)

    # Ensure every class defines the discriminator field.
    missing = [cls.__name__ for cls in classes if discriminator not in getattr(cls, "model_fields", {})]
    if missing:
        raise ValueError(
            f"The following classes do not define the discriminator field '{discriminator}': {', '.join(missing)}"
        )

    # Sort by the default value of the discriminator field for deterministic ordering.
    # Handle PydanticUndefined by falling back to class name to avoid TypeError
    def get_sort_key(cls):
        field = cls.model_fields.get(discriminator)
        val = getattr(field, "default", "")
        if type(val).__name__ == "PydanticUndefined":
            return cls.__name__
        return str(val)

    sorted_classes = sorted(classes, key=get_sort_key)

    # Build the Union type. ``typing.Union`` expects __args__ tuple; using ``Union[tuple]`` is safe.
    union_type = Union[tuple(sorted_classes)]  # type: ignore[arg-type]
    return Annotated[union_type, Field(discriminator=discriminator)]


def get_class_by_discriminator(
    discriminated_union: Any,
    discriminator_value: str,
) -> Type[Any]:
    """Return the concrete subclass from a discriminated union matching *discriminator_value*.

    The function extracts the underlying ``Union`` type from the ``Annotated`` wrapper
    and searches for a class whose discriminator field (defined via Pydantic) has the
    default value equal to ``discriminator_value``.

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
    # 1. Extract the Union type and the metadata from Annotated
    try:
        args = get_args(discriminated_union)
        if len(args) < 2:
            raise ValueError("Provided type is not a valid Annotated discriminated union")
        
        union_type = args[0]
        metadata = args[1]
    except Exception as exc:
        raise ValueError(f"Provided type is not an Annotated discriminated union: {exc}") from exc

    # 2. Extract the discriminator field name from the Field metadata
    discriminator_field = getattr(metadata, "discriminator", None)
    if not discriminator_field:
        discriminator_field = "name"

    # 3. Iterate over concrete classes in the Union.
    for cls in getattr(union_type, "__args__", []):
        model_fields = getattr(cls, "model_fields", {})
        field_info = model_fields.get(discriminator_field)
        
        if field_info is not None:
            default_val = getattr(field_info, "default", None)
            if default_val == discriminator_value:
                return cls
        
        if getattr(cls, discriminator_field, None) == discriminator_value:
            return cls

    raise ValueError(f"Unknown discriminator value '{discriminator_value}' for field '{discriminator_field}'")


def get_class_by_name(
    base_cls: Type[T],
    sub_dir: str,
    name: str,
    discriminator: str = "name",
    base_package: str | None = None,
) -> Type[T]:
    """
    Unified utility to retrieve a concrete subclass by a name/tag.
    Automatically selects between Pydantic-style discovery and standard class discovery.
    """
    # 1. Discover all subclasses
    classes = _collect_classes(base_cls, sub_dir, base_package)

    # 2. Determine strategy based on whether the base class is a Pydantic model
    is_pydantic = issubclass(base_cls, BaseModel)

    if is_pydantic:
        # Use Pydantic logic: search model_fields for the discriminator's default value
        for cls in classes:
            model_fields = getattr(cls, "model_fields", {})
            field_info = model_fields.get(discriminator)
            if field_info is not None:
                if getattr(field_info, "default", None) == name:
                    return cls
            # Fallback to plain attribute
            if getattr(cls, discriminator, None) == name:
                return cls
    else:
        # Use Lightweight logic: search for a specific TAG attribute or the class name
        # We prioritize a 'WF_TAG' or 'name' attribute if it exists on the class
        for cls in classes:
            # Try discriminator name (e.g., 'WF_TAG' or 'name') first, then the class name
            if getattr(cls, discriminator, None) == name or cls.__name__ == name:
                return cls

    raise ValueError(f"Class matching '{name}' not found among subclasses of {base_cls.__name__} in {sub_dir}")
