"""Decorators for marking methods as MCP tools."""

from functools import wraps
from typing import Any, Callable, Optional


def mcp_tool(
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Callable[[Callable], Callable]:
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
    def decorator(func: Callable) -> Callable:
        # Store metadata on the function
        func._mcp_tool = True  # type: ignore
        func._mcp_tool_name = name or func.__name__.replace("_", "-")  # type: ignore
        func._mcp_tool_description = description  # type: ignore
        
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await func(*args, **kwargs)
        
        # Preserve the metadata on the wrapper
        wrapper._mcp_tool = True  # type: ignore
        wrapper._mcp_tool_name = func._mcp_tool_name  # type: ignore
        wrapper._mcp_tool_description = func._mcp_tool_description  # type: ignore
        
        return wrapper
    
    return decorator