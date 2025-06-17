# MCP Framework

A general-purpose framework for building MCP (Model Context Protocol) servers using Python annotations, inspired by PydanticAI's approach to tool definitions.

## Features

- **Annotation-based tool definition**: Define MCP tools using Python type hints
- **Automatic schema generation**: JSON schemas are generated from type annotations
- **Docstring parsing**: Tool descriptions and parameter descriptions extracted from docstrings
- **Simple inheritance model**: Just inherit from `BaseMCPServer` and add methods
- **Type safety**: Full type hint support for better IDE integration

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

## How It Works

1. **Inherit from BaseMCPServer**: Your server class inherits all MCP protocol handling
2. **Use @mcp_tool decorator**: Mark methods that should be exposed as MCP tools
3. **Add type annotations**: Parameter types are used to generate JSON schemas
4. **Write docstrings**: Descriptions are extracted from docstrings automatically
5. **Run the server**: Call `server.main()` to start the MCP server

## Advanced Features

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
      "command": "python",
      "args": ["-m", "my_server"]
    }
  }
}
```

## Benefits Over Traditional MCP Server Implementation

1. **Less Boilerplate**: No need to manually define tool schemas
2. **Type Safety**: IDE support for parameter types and return values
3. **Self-Documenting**: Docstrings become tool descriptions automatically
4. **Maintainable**: Changes to method signatures automatically update schemas
5. **Pythonic**: Uses familiar Python patterns and decorators