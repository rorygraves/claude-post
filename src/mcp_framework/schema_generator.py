"""Generate JSON schemas from Python type annotations."""

import inspect
from typing import Any, Dict, List, Optional, Union, get_args, get_origin
from datetime import datetime, date
import typing
from enum import Enum


def python_type_to_json_schema(type_hint: Any) -> Dict[str, Any]:
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
    if origin is Union:
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
                return {
                    "oneOf": [python_type_to_json_schema(arg) for arg in non_none_args]
                }
        else:
            # Regular Union without None
            return {
                "oneOf": [python_type_to_json_schema(arg) for arg in args]
            }
    
    # Handle List[T]
    elif origin is list:
        if args:
            return {
                "type": "array",
                "items": python_type_to_json_schema(args[0])
            }
        else:
            return {"type": "array"}
    
    # Handle Dict[K, V]
    elif origin is dict:
        if args and len(args) >= 2:
            # For Dict[str, T], we can use additionalProperties
            if args[0] is str:
                return {
                    "type": "object",
                    "additionalProperties": python_type_to_json_schema(args[1])
                }
        return {"type": "object"}
    
    # Handle Literal types
    elif hasattr(typing, 'Literal') and origin is getattr(typing, 'Literal'):
        return {"enum": list(args)}
    
    # Handle Enum types
    elif inspect.isclass(type_hint) and issubclass(type_hint, Enum):
        return {"enum": [item.value for item in type_hint]}
    
    # Default to string for unknown types
    return {"type": "string"}


def extract_parameter_schema(func: Any) -> Dict[str, Any]:
    """Extract parameter schema from a function's type annotations.
    
    Args:
        func: The function to extract schema from
        
    Returns:
        JSON schema for the function's parameters
    """
    signature = inspect.signature(func)
    properties = {}
    required = []
    
    for param_name, param in signature.parameters.items():
        # Skip 'self' parameter for methods
        if param_name == 'self':
            continue
            
        # Get type annotation
        if param.annotation != inspect.Parameter.empty:
            param_schema = python_type_to_json_schema(param.annotation)
            
            # Extract description from parameter docstring if available
            # (This would require parsing the docstring - simplified for now)
            param_schema["description"] = f"Parameter: {param_name}"
            
            properties[param_name] = param_schema
            
            # Check if parameter is required (no default value)
            if param.default == inspect.Parameter.empty:
                # Check if it's Optional in the type hint
                origin = get_origin(param.annotation)
                args = get_args(param.annotation)
                is_optional = origin is Union and type(None) in args
                
                if not is_optional:
                    required.append(param_name)
    
    schema = {
        "type": "object",
        "properties": properties
    }
    
    if required:
        schema["required"] = required
        
    return schema


def parse_docstring_params(docstring: Optional[str]) -> Dict[str, str]:
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
    lines = docstring.strip().split('\n')
    
    in_params_section = False
    current_param = None
    current_desc = []
    
    for line in lines:
        line = line.strip()
        
        # Check for parameter section headers
        if line in ['Args:', 'Arguments:', 'Parameters:', 'Params:']:
            in_params_section = True
            continue
        elif line and line.endswith(':') and not line.startswith(' '):
            # Another section started
            in_params_section = False
            
        if in_params_section:
            # Check if this is a parameter definition
            if line and not line.startswith(' ') and ':' in line:
                # Save previous parameter if any
                if current_param and current_desc:
                    params[current_param] = ' '.join(current_desc).strip()
                
                # Parse new parameter
                param_part, desc_part = line.split(':', 1)
                current_param = param_part.strip()
                current_desc = [desc_part.strip()] if desc_part.strip() else []
            elif line.startswith(' ') and current_param:
                # Continuation of current parameter description
                current_desc.append(line.strip())
    
    # Save last parameter
    if current_param and current_desc:
        params[current_param] = ' '.join(current_desc).strip()
    
    return params