"""Example: Simple calculator MCP server using the annotation framework.

This demonstrates how easy it is to create an MCP server with the new framework.
Just inherit from BaseMCPServer and add @mcp_tool decorated methods!
"""

from typing import List
from mcp_framework import BaseMCPServer, mcp_tool


class CalculatorServer(BaseMCPServer):
    """A simple calculator exposed as an MCP server."""
    
    def __init__(self):
        super().__init__("calculator", "1.0.0")
    
    @mcp_tool()
    async def add(self, a: float, b: float) -> float:
        """Add two numbers together.
        
        Args:
            a: First number
            b: Second number
            
        Returns:
            Sum of a and b
        """
        return a + b
    
    @mcp_tool()
    async def subtract(self, a: float, b: float) -> float:
        """Subtract b from a.
        
        Args:
            a: Number to subtract from
            b: Number to subtract
            
        Returns:
            Result of a - b
        """
        return a - b
    
    @mcp_tool()
    async def multiply(self, a: float, b: float) -> float:
        """Multiply two numbers.
        
        Args:
            a: First number
            b: Second number
            
        Returns:
            Product of a and b
        """
        return a * b
    
    @mcp_tool()
    async def divide(self, a: float, b: float) -> float:
        """Divide a by b.
        
        Args:
            a: Dividend
            b: Divisor (must not be zero)
            
        Returns:
            Result of a / b
        """
        if b == 0:
            raise ValueError("Cannot divide by zero")
        return a / b
    
    @mcp_tool(name="calculate-average")
    async def average(self, numbers: List[float]) -> float:
        """Calculate the average of a list of numbers.
        
        Args:
            numbers: List of numbers to average
            
        Returns:
            Average value
        """
        if not numbers:
            raise ValueError("Cannot calculate average of empty list")
        return sum(numbers) / len(numbers)


if __name__ == "__main__":
    # Run the calculator server
    server = CalculatorServer()
    server.main()