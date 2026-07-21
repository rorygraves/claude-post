"""Generate JSON schemas from Python type annotations."""

import inspect
import re
import types
import typing
from datetime import date, datetime
from enum import Enum
from typing import Any, Union, get_args, get_origin


def python_type_to_json_schema(type_hint: Any) -> dict[str, Any]:
    """Convert a Python type hint to a JSON schema definition.

    Args:
        type_hint: The Python type annotation

    Returns:
        JSON schema dictionary
    """
    # Handle None/NoneType
    if type_hint is type(None):
        return {"type": "null"}

    # Handle basic types
    if type_hint is str:
        return {"type": "string"}
    elif type_hint is int:
        return {"type": "integer"}
    elif type_hint is float:
        return {"type": "number"}
    elif type_hint is bool:
        return {"type": "boolean"}
    elif type_hint is datetime:
        return {"type": "string", "format": "date-time"}
    elif type_hint is date:
        return {"type": "string", "format": "date"}

    # Get the origin and args for generic types
    origin = get_origin(type_hint)
    args = get_args(type_hint)

    # Handle Optional[T] (Union[T, None])
    if origin in (Union, types.UnionType):
        # Check if it's Optional (has None)
        if type(None) in args:
            # Get the non-None type
            non_none_args = [arg for arg in args if arg is not type(None)]
            if len(non_none_args) == 1:
                # It's Optional[T]
                schema = python_type_to_json_schema(non_none_args[0])
                # Don't add null to type, just mark as not required in parent
                return schema
            else:
                # It's a Union of multiple non-None types
                return {"oneOf": [python_type_to_json_schema(arg) for arg in non_none_args]}
        else:
            # Regular Union without None
            return {"oneOf": [python_type_to_json_schema(arg) for arg in args]}

    # Handle List[T]
    elif origin is list:
        if args:
            return {"type": "array", "items": python_type_to_json_schema(args[0])}
        else:
            return {"type": "array"}

    # Handle Dict[K, V]
    elif origin is dict:
        if args and len(args) >= 2 and args[0] is str:
            return {"type": "object", "additionalProperties": python_type_to_json_schema(args[1])}
        return {"type": "object"}

    # Handle Literal types
    elif hasattr(typing, "Literal") and origin is typing.Literal:
        schema = python_type_to_json_schema(type(args[0])) if args else {}
        return {**schema, "enum": list(args)}

    # Handle Enum types
    elif inspect.isclass(type_hint) and issubclass(type_hint, Enum):
        values = [item.value for item in type_hint]
        schema = python_type_to_json_schema(type(values[0])) if values else {}
        return {**schema, "enum": values}

    # Default to string for unknown types
    return {"type": "string"}


def _is_optional_annotation(annotation: Any) -> bool:
    """Return whether an annotation is Optional (a Union that includes None)."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    return origin in (Union, types.UnionType) and type(None) in args


def extract_parameter_schema(func: Any) -> dict[str, Any]:
    """Extract a JSON schema for a function's parameters.

    Parameter descriptions are read from the function's own docstring (see
    ``parse_docstring_params``), so the returned schema is complete on its own
    and needs no further enhancement by the caller.

    Args:
        func: The function to extract schema from

    Returns:
        JSON schema for the function's parameters
    """
    signature = inspect.signature(func)
    param_descriptions = parse_docstring_params(getattr(func, "__doc__", None))
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in signature.parameters.items():
        # Skip 'self' parameter for methods
        if param_name == "self":
            continue

        # Only annotated parameters can be described by a schema
        if param.annotation is inspect.Parameter.empty:
            continue

        param_schema = python_type_to_json_schema(param.annotation)

        description = param_descriptions.get(param_name)
        if description:
            param_schema["description"] = description

        properties[param_name] = param_schema

        # A parameter is required when it has no default and is not Optional
        if param.default is inspect.Parameter.empty and not _is_optional_annotation(param.annotation):
            required.append(param_name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}

    if required:
        schema["required"] = required

    return schema


def parse_docstring_params(docstring: str | None) -> dict[str, str]:
    """Parse parameter descriptions from a docstring.

    Supports Google-style and NumPy-style docstrings.

    Args:
        docstring: The function's docstring

    Returns:
        Dictionary mapping parameter names to descriptions
    """
    if not docstring:
        return {}

    params = {}
    lines = inspect.cleandoc(docstring).splitlines()

    in_params_section = False
    current_param = None
    current_desc = []

    for raw_line in lines:
        line = raw_line.strip()

        # Check for parameter section headers
        if line in ["Args:", "Arguments:", "Parameters:", "Params:"]:
            in_params_section = True
            continue
        elif line and line.endswith(":") and raw_line == line:
            # Another section started
            in_params_section = False

        if in_params_section:
            # Check if this is a parameter definition
            match = re.match(r"^\s*([*]*\w+)(?:\s*\([^)]*\))?\s*:\s*(.*)$", raw_line)
            if match:
                # Save previous parameter if any
                if current_param and current_desc:
                    params[current_param] = " ".join(current_desc).strip()

                # Parse new parameter
                current_param = match.group(1)
                description = match.group(2).strip()
                current_desc = [description] if description else []
            elif line and current_param:
                # Continuation of current parameter description
                current_desc.append(line.strip())

    # Save last parameter
    if current_param and current_desc:
        params[current_param] = " ".join(current_desc).strip()

    return params
