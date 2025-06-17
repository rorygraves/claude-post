"""MCP Framework - A general-purpose framework for building MCP servers with annotations.

This framework allows you to create MCP servers by simply defining a class with
annotated methods, similar to how PydanticAI handles tool definitions.
"""

from .base import BaseMCPServer
from .decorators import mcp_tool

__all__ = ["BaseMCPServer", "mcp_tool"]