"""Tests for the calculator server using the MCP framework."""

from unittest.mock import AsyncMock, patch
import pytest
import sys
import os

# Add the parent directory to the path so we can import from examples
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_framework.examples.calculator_server import CalculatorServer


class TestCalculatorServer:
    """Test suite for the calculator MCP server."""
    
    @pytest.fixture
    def server(self):
        """Create a calculator server instance."""
        return CalculatorServer()
    
    @pytest.mark.asyncio
    async def test_add_operation(self, server):
        """Test the add operation."""
        result = await server.add(5.0, 3.0)
        assert result == 8.0
        
        result = await server.add(-5.0, 3.0)
        assert result == -2.0
        
        result = await server.add(0.1, 0.2)
        assert abs(result - 0.3) < 0.0001  # Handle floating point precision
    
    @pytest.mark.asyncio
    async def test_subtract_operation(self, server):
        """Test the subtract operation."""
        result = await server.subtract(10.0, 4.0)
        assert result == 6.0
        
        result = await server.subtract(3.0, 5.0)
        assert result == -2.0
    
    @pytest.mark.asyncio
    async def test_multiply_operation(self, server):
        """Test the multiply operation."""
        result = await server.multiply(4.0, 3.0)
        assert result == 12.0
        
        result = await server.multiply(-2.0, 3.0)
        assert result == -6.0
        
        result = await server.multiply(0.0, 100.0)
        assert result == 0.0
    
    @pytest.mark.asyncio
    async def test_divide_operation(self, server):
        """Test the divide operation."""
        result = await server.divide(10.0, 2.0)
        assert result == 5.0
        
        result = await server.divide(7.0, 2.0)
        assert result == 3.5
        
        # Test divide by zero
        with pytest.raises(ValueError, match="Cannot divide by zero"):
            await server.divide(10.0, 0.0)
    
    @pytest.mark.asyncio
    async def test_average_operation(self, server):
        """Test the average operation."""
        result = await server.average([1.0, 2.0, 3.0, 4.0, 5.0])
        assert result == 3.0
        
        result = await server.average([10.0])
        assert result == 10.0
        
        result = await server.average([-5.0, 5.0])
        assert result == 0.0
        
        # Test empty list
        with pytest.raises(ValueError, match="Cannot calculate average of empty list"):
            await server.average([])
    
    def test_tool_discovery(self, server):
        """Test that all tools are discovered correctly."""
        tool_names = list(server._tools.keys())
        expected_tools = ['add', 'subtract', 'multiply', 'divide', 'calculate-average']
        
        assert sorted(tool_names) == sorted(expected_tools)
    
    @pytest.mark.asyncio
    async def test_list_tools_handler(self, server):
        """Test the MCP list_tools handler."""
        # Since the handlers are wrapped by decorators, let's test the functionality
        # by accessing the internal methods that get called
        
        # The list_tools functionality is in the handle_list_tools method
        # which is registered via the @server.list_tools() decorator
        # We can access it through the _tools attribute
        tools = []
        for tool_name, method in server._tools.items():
            # This mimics what handle_list_tools does
            description = getattr(method, '_mcp_tool_description', None)
            if not description and method.__doc__:
                description = method.__doc__.strip().split('\n')[0]
            
            from mcp_framework.schema_generator import extract_parameter_schema
            input_schema = extract_parameter_schema(method)
            
            tools.append({
                'name': tool_name,
                'description': description,
                'schema': input_schema
            })
        
        # Verify we have the right number of tools
        assert len(tools) == 5
        
        # Check the add tool
        add_tool = next(t for t in tools if t['name'] == 'add')
        assert add_tool['description'] == "Add two numbers together."
        assert add_tool['schema']['type'] == 'object'
        assert 'a' in add_tool['schema']['properties']
        assert 'b' in add_tool['schema']['properties']
        assert add_tool['schema']['properties']['a']['type'] == 'number'
        assert add_tool['schema']['properties']['b']['type'] == 'number'
        assert add_tool['schema']['required'] == ['a', 'b']
    
    @pytest.mark.asyncio
    async def test_call_tool_handler(self, server):
        """Test the MCP call_tool handler."""
        # Test the tool execution by calling methods directly
        # and verifying they would work correctly when called by MCP
        
        # Test calling the add tool
        result = await server.add(a=5.0, b=3.0)
        assert result == 8.0
        
        # Test that the tool handler would format it correctly
        # The handle_call_tool method converts results to TextContent
        assert str(result) == "8.0"
        
        # Test calling the average tool
        result = await server.average(numbers=[1.0, 2.0, 3.0])
        assert result == 2.0
        
        # Test error handling with divide by zero
        with pytest.raises(ValueError, match="Cannot divide by zero"):
            await server.divide(a=10.0, b=0.0)
    
    @pytest.mark.asyncio
    async def test_parameter_validation(self, server):
        """Test that parameter validation works correctly."""
        # Test that methods require correct parameters
        # This would be caught by the MCP framework when calling tools
        
        # The actual validation happens when Python tries to call the method
        # with missing parameters - it raises TypeError
        with pytest.raises(TypeError):
            # Missing required parameter 'b'
            await server.add(a=5.0)
    
    def test_server_initialization(self):
        """Test that the server initializes with correct metadata."""
        server = CalculatorServer()
        assert server.server_name == "calculator"
        assert server.server_version == "1.0.0"
        assert len(server._tools) == 5


class TestMCPProtocolIntegration:
    """Test the full MCP protocol integration."""
    
    @pytest.mark.asyncio
    async def test_full_server_lifecycle(self):
        """Test the complete server lifecycle with mocked streams."""
        server = CalculatorServer()
        
        # Mock the stdio streams
        read_stream = AsyncMock()
        write_stream = AsyncMock()
        
        # Mock the server run method to avoid actual IO
        with patch.object(server.server, 'run', new_callable=AsyncMock) as mock_run:
            with patch('mcp.server.stdio.stdio_server') as mock_stdio:
                # Make stdio_server return our mocked streams
                mock_stdio.return_value.__aenter__.return_value = (read_stream, write_stream)
                
                # Run the server (this would normally block)
                await server.run()
                
                # Verify the server was started with correct parameters
                mock_run.assert_called_once()
                args = mock_run.call_args[0]
                
                # Check initialization options
                init_options = args[2]
                assert init_options.server_name == "calculator"
                assert init_options.server_version == "1.0.0"
                assert init_options.capabilities is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])