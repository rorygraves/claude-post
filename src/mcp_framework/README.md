# MCP Framework

A general-purpose framework for building MCP (Model Context Protocol) servers using Python annotations, inspired by PydanticAI's approach to tool definitions.

## Features

- **Annotation-based tool definition**: Define MCP tools using Python type hints
- **Automatic schema generation**: JSON schemas are generated from type annotations
- **Docstring parsing**: Tool descriptions and parameter descriptions extracted from docstrings
- **Simple inheritance model**: Just inherit from `BaseMCPServer` and add methods
- **Type safety**: Full type hint support for better IDE integration
- **Built-in CLI**: Automatic `--help` and `--describe` command line options
- **Tool discovery**: List all available tools and their parameters

## Quick Start

```python
from typing import List
from mcp_framework import BaseMCPServer, mcp_tool

class MyServer(BaseMCPServer):
    def __init__(self):
        super().__init__("my-server", "1.0.0")
    
    @mcp_tool()
    async def greet(self, name: str, formal: bool = False) -> str:
        """Greet someone by name.
        
        Args:
            name: The person's name
            formal: Whether to use formal greeting
        """
        if formal:
            return f"Good day, {name}."
        return f"Hello, {name}!"

if __name__ == "__main__":
    server = MyServer()
    server.main()
```

## Command Line Usage

Every MCP server built with this framework automatically supports these command line options:

```bash
# Show help and available options
uv run my-server --help

# List all tools with descriptions and parameters
uv run my-server --describe

# Start the server (default)
uv run my-server
```

The `--describe` option provides a human-readable list of all available tools, their descriptions, and parameter details including types and whether they're required or optional.

## How It Works

1. **Inherit from BaseMCPServer**: Your server class inherits all MCP protocol handling
2. **Use @mcp_tool decorator**: Mark methods that should be exposed as MCP tools
3. **Add type annotations**: Parameter types are used to generate JSON schemas
4. **Write docstrings**: Descriptions are extracted from docstrings automatically
5. **Run the server**: Call `server.main()` to start the MCP server

## Advanced Features

### Custom Command Line Arguments

You can add custom command line arguments by overriding the `add_arguments` method:

```python
class MyServer(BaseMCPServer):
    def __init__(self, api_key: Optional[str] = None):
        super().__init__("my-server", "1.0.0")
        self.api_key = api_key
    
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Add custom command line arguments."""
        parser.add_argument(
            "--api-key",
            help="API key for external services"
        )
    
    def main(self, args: Optional[List[str]] = None) -> None:
        """Override to handle custom arguments."""
        parsed_args = self.parse_args(args)
        
        # Handle custom args before running
        if hasattr(parsed_args, 'api_key') and parsed_args.api_key:
            self.api_key = parsed_args.api_key
        
        # Call parent main with parsed args
        super().main(args)
```

### Custom Tool Names

```python
@mcp_tool(name="search-emails")
async def search_emails(self, query: str) -> List[str]:
    """Search for emails matching the query."""
    ...
```

### Optional Parameters

```python
@mcp_tool()
async def send_message(
    self, 
    to: str, 
    content: str, 
    cc: Optional[List[str]] = None
) -> str:
    """Send a message with optional CC recipients."""
    ...
```

### Complex Types

```python
from typing import Dict, List, Literal

@mcp_tool()
async def process_data(
    self,
    data: List[Dict[str, Any]],
    format: Literal["json", "csv", "xml"] = "json"
) -> Dict[str, Any]:
    """Process data in various formats."""
    ...
```

## Type Mapping

| Python Type | JSON Schema Type |
|-------------|------------------|
| `str` | `string` |
| `int` | `integer` |
| `float` | `number` |
| `bool` | `boolean` |
| `List[T]` | `array` with items of type T |
| `Dict[str, T]` | `object` with additionalProperties of type T |
| `Optional[T]` | Type T, not in required list |
| `Literal["a", "b"]` | `enum: ["a", "b"]` |
| `datetime` | `string` with format `date-time` |

## Integration with Claude Desktop

Add your server to Claude Desktop's configuration:

```json
{
  "mcpServers": {
    "my-server": {
      "command": "/path/to/uv",
      "args": [
        "--directory",
        "/path/to/your/project",
        "run",
        "my-server"
      ]
    }
  }
}
```

**Note**: Always use `uv` to run MCP servers to ensure dependencies are properly loaded from your project's virtual environment.

## Benefits Over Traditional MCP Server Implementation

1. **Less Boilerplate**: No need to manually define tool schemas
2. **Type Safety**: IDE support for parameter types and return values
3. **Self-Documenting**: Docstrings become tool descriptions automatically
4. **Maintainable**: Changes to method signatures automatically update schemas
5. **Pythonic**: Uses familiar Python patterns and decorators