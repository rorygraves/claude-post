"""Base class for annotation-based MCP servers."""

import argparse
import asyncio
import inspect
import json
import logging
import sys
from typing import Any, Dict, List, Optional, Union

import mcp.server.stdio
from mcp import types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from .schema_generator import extract_parameter_schema, parse_docstring_params


class BaseMCPServer:
    """Base class for creating MCP servers using annotated methods.
    
    Inherit from this class and use the @mcp_tool decorator to mark methods
    that should be exposed as MCP tools.
    
    Example:
        class EmailServer(BaseMCPServer):
            def __init__(self):
                super().__init__("email-server", "1.0.0")
                
            @mcp_tool(name="send-email")
            async def send_email(self, to: List[str], subject: str, content: str) -> str:
                '''Send an email to the specified recipients.
                
                Args:
                    to: List of recipient email addresses
                    subject: Email subject line
                    content: Email body content
                '''
                # Implementation here
                return "Email sent successfully"
    """
    
    def __init__(self, server_name: str = "mcp-server", server_version: str = "0.1.0", tool_prefix: str = ""):
        """Initialize the MCP server.
        
        Args:
            server_name: Name of the MCP server
            server_version: Version of the MCP server
            tool_prefix: Prefix to add to all tool names (e.g., "mail_" results in "mail_send-email")
        """
        self.server_name = server_name
        self.server_version = server_version
        self.tool_prefix = tool_prefix
        self.server = Server(server_name)
        self._tools: Dict[str, Any] = {}
        
        # Set up logging
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
        )
        
        # Discover and register tools
        self._discover_tools()
        
        # Register MCP handlers
        self._register_handlers()
    
    def _discover_tools(self) -> None:
        """Discover methods decorated with @mcp_tool."""
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if hasattr(method, '_mcp_tool') and method._mcp_tool:
                tool_name = getattr(method, '_mcp_tool_name', name)
                # Apply prefix to tool name
                prefixed_name = f"{self.tool_prefix}{tool_name}" if self.tool_prefix else tool_name
                self._tools[prefixed_name] = method
                logging.info(f"Discovered MCP tool: {prefixed_name}")
    
    def _register_handlers(self) -> None:
        """Register MCP protocol handlers."""
        
        @self.server.list_tools()
        async def handle_list_tools() -> List[types.Tool]:
            """List all available tools."""
            tools = []
            
            for tool_name, method in self._tools.items():
                # Get description from decorator or docstring
                description = getattr(method, '_mcp_tool_description', None)
                if not description and method.__doc__:
                    # Use first line of docstring as description
                    description = method.__doc__.strip().split('\n')[0]
                
                # Extract parameter schema
                input_schema = extract_parameter_schema(method)
                
                # Enhance parameter descriptions from docstring
                if method.__doc__:
                    param_descriptions = parse_docstring_params(method.__doc__)
                    for param_name, param_desc in param_descriptions.items():
                        if param_name in input_schema.get("properties", {}):
                            input_schema["properties"][param_name]["description"] = param_desc
                
                tools.append(types.Tool(
                    name=tool_name,
                    description=description or f"Tool: {tool_name}",
                    inputSchema=input_schema
                ))
            
            logging.info(f"Listed {len(tools)} tools")
            return tools
        
        @self.server.call_tool()
        async def handle_call_tool(
            name: str, arguments: Optional[Dict[str, Any]]
        ) -> List[Union[types.TextContent, types.ImageContent, types.EmbeddedResource]]:
            """Handle tool execution requests."""
            if name not in self._tools:
                raise ValueError(f"Unknown tool: {name}")
            
            method = self._tools[name]
            logging.info(f"Calling tool: {name} with arguments: {arguments}")
            
            try:
                # Call the method with arguments
                if arguments:
                    result = await method(**arguments)
                else:
                    result = await method()
                
                # Convert result to MCP response format
                if isinstance(result, str):
                    return [types.TextContent(type="text", text=result)]
                elif isinstance(result, dict):
                    # For dict results, convert to formatted text
                    import json
                    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
                elif isinstance(result, list):
                    # Handle list results
                    return [types.TextContent(type="text", text=str(result))]
                else:
                    return [types.TextContent(type="text", text=str(result))]
                    
            except Exception as e:
                logging.error(f"Error executing tool {name}: {e}", exc_info=True)
                return [types.TextContent(type="text", text=f"Error: {str(e)}")]
        
        @self.server.list_prompts()
        async def handle_list_prompts() -> List[types.Prompt]:
            """Handle list prompts request."""
            # Override in subclass if prompts are needed
            return []
        
        @self.server.list_resources()
        async def handle_list_resources() -> List[types.Resource]:
            """Handle list resources request."""
            # Override in subclass if resources are needed
            return []
    
    async def run(self) -> None:
        """Run the MCP server."""
        logging.info(f"Starting {self.server_name} v{self.server_version}")
        
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name=self.server_name,
                    server_version=self.server_version,
                    capabilities=self.server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )
    
    def describe_tools(self) -> None:
        """Print human-readable descriptions of all available tools."""
        print(f"\n{self.server_name} v{self.server_version}")
        print("=" * 60)
        print("\nAvailable Tools:\n")
        
        for tool_name, method in sorted(self._tools.items()):
            # Get description
            description = getattr(method, '_mcp_tool_description', None)
            if not description and method.__doc__:
                description = method.__doc__.strip().split('\n')[0]
            
            print(f"Tool: {tool_name}")
            print(f"  Description: {description or 'No description available'}")
            
            # Get parameter schema
            input_schema = extract_parameter_schema(method)
            
            # Enhance parameter descriptions from docstring
            if method.__doc__:
                param_descriptions = parse_docstring_params(method.__doc__)
                for param_name, param_desc in param_descriptions.items():
                    if param_name in input_schema.get("properties", {}):
                        input_schema["properties"][param_name]["description"] = param_desc
            
            # Print parameters
            if "properties" in input_schema:
                print("  Parameters:")
                required_params = input_schema.get("required", [])
                
                for param_name, param_info in input_schema["properties"].items():
                    param_type = param_info.get("type", "any")
                    param_desc = param_info.get("description", "No description")
                    is_required = param_name in required_params
                    
                    # Handle array types
                    if param_type == "array" and "items" in param_info:
                        item_type = param_info["items"].get("type", "any")
                        param_type = f"array[{item_type}]"
                    
                    # Handle enum values
                    if "enum" in param_info:
                        enum_values = ", ".join(f"'{v}'" for v in param_info["enum"])
                        param_type = f"{param_type} ({enum_values})"
                    
                    print(f"    - {param_name}: {param_type} {'(required)' if is_required else '(optional)'}")
                    print(f"      {param_desc}")
            else:
                print("  Parameters: None")
            
            print()  # Empty line between tools
    
    def parse_args(self, args: Optional[List[str]] = None) -> argparse.Namespace:
        """Parse command line arguments.
        
        Args:
            args: Optional list of arguments to parse. If None, uses sys.argv.
            
        Returns:
            Parsed arguments namespace
        """
        parser = argparse.ArgumentParser(
            prog=self.server_name,
            description=f"{self.server_name} - MCP server",
            formatter_class=argparse.RawDescriptionHelpFormatter
        )
        
        parser.add_argument(
            "--describe",
            action="store_true",
            help="Show available tools and their parameters"
        )
        
        # Allow subclasses to add their own arguments
        self.add_arguments(parser)
        
        return parser.parse_args(args)
    
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Override in subclasses to add custom command line arguments.
        
        Args:
            parser: The argument parser to add arguments to
        """
        pass
    
    def main(self, args: Optional[List[str]] = None) -> None:
        """Main entry point for the server.
        
        Args:
            args: Optional list of command line arguments. If None, uses sys.argv.
        """
        parsed_args = self.parse_args(args)
        
        if parsed_args.describe:
            self.describe_tools()
            sys.exit(0)
        
        asyncio.run(self.run())