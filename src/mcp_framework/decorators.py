"""Decorators for marking methods as MCP tools."""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar, cast

P = ParamSpec("P")
R = TypeVar("R")


def mcp_tool(
    *,
    name: str | None = None,
    description: str | None = None,
    capability: str | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorator to mark a method as an MCP tool.

    Args:
        name: Optional custom name for the tool. If not provided, uses the method name.
        description: Optional description override. If not provided, uses the method's docstring.

    Example:
        @mcp_tool(name="search-emails")
        async def search_emails(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
            '''Search for emails within a date range.'''
            ...
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        # Store metadata on the function
        metadata_func = cast(Any, func)
        metadata_func._mcp_tool = True
        metadata_func._mcp_tool_name = name or func.__name__.replace("_", "-")
        metadata_func._mcp_tool_description = description
        metadata_func._mcp_tool_capability = capability

        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            return await func(*args, **kwargs)

        # Preserve the metadata on the wrapper
        metadata_wrapper = cast(Any, wrapper)
        metadata_wrapper._mcp_tool = True
        metadata_wrapper._mcp_tool_name = metadata_func._mcp_tool_name
        metadata_wrapper._mcp_tool_description = metadata_func._mcp_tool_description
        metadata_wrapper._mcp_tool_capability = capability

        return wrapper

    return decorator
