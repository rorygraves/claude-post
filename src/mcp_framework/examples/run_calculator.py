#!/usr/bin/env python
"""Run the calculator MCP server.

This script demonstrates how to run an MCP server built with the framework.
You can test it with Claude Desktop by adding this to your config:

{
  "mcpServers": {
    "calculator": {
      "command": "python",
      "args": ["/path/to/examples/run_calculator.py"]
    }
  }
}
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_framework.examples.calculator_server import CalculatorServer

if __name__ == "__main__":
    # Create and run the server
    server = CalculatorServer()
    print("Starting Calculator MCP Server...", file=sys.stderr)
    server.main()