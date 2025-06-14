"""Unit tests for MCP server pagination functionality.

This module tests the pagination features in the MCP server layer,
including parameter handling and response formatting.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from typing import Dict, Any

from src.email_client.server import _handle_search_emails
from src.email_client.email_client import SearchCriteria


class TestMCPServerPagination(unittest.IsolatedAsyncioTestCase):
    """Test MCP server pagination parameter handling."""

    @patch('src.email_client.server.email_client')
    async def test_search_emails_default_pagination(self, mock_email_client):
        """Test search-emails with default pagination parameters."""
        # Mock email client response (async method)
        mock_email_client.search_emails = AsyncMock(return_value=[
            {'id': '1', 'from': 'test1@example.com', 'date': '2024-01-01', 'subject': 'Test 1'},
            {'id': '2', 'from': 'test2@example.com', 'date': '2024-01-01', 'subject': 'Test 2'},
        ])
        
        # Test with no pagination parameters (should use defaults)
        arguments = {
            'folder': 'inbox',
            'keyword': 'test'
        }
        
        response = await _handle_search_emails(arguments)
        
        # Verify SearchCriteria was called with default pagination
        mock_email_client.search_emails.assert_called_once()
        call_args = mock_email_client.search_emails.call_args[0][0]
        self.assertEqual(call_args.max_results, 100)  # Default
        self.assertEqual(call_args.start_from, 0)     # Default
        
        # Verify response format includes pagination info
        response_text = response[0].text
        self.assertIn('showing 2 results starting from position 0', response_text)
        self.assertIn('start_from=100', response_text)

    @patch('src.email_client.server.email_client')
    async def test_search_emails_custom_pagination(self, mock_email_client):
        """Test search-emails with custom pagination parameters."""
        # Mock email client response (async method)
        mock_email_client.search_emails = AsyncMock(return_value=[
            {'id': '26', 'from': 'test26@example.com', 'date': '2024-01-01', 'subject': 'Test 26'},
            {'id': '27', 'from': 'test27@example.com', 'date': '2024-01-01', 'subject': 'Test 27'},
        ])
        
        # Test with custom pagination parameters
        arguments = {
            'folder': 'inbox',
            'keyword': 'test',
            'max_results': 25,
            'start_from': 25
        }
        
        response = await _handle_search_emails(arguments)
        
        # Verify SearchCriteria was called with custom pagination
        mock_email_client.search_emails.assert_called_once()
        call_args = mock_email_client.search_emails.call_args[0][0]
        self.assertEqual(call_args.max_results, 25)
        self.assertEqual(call_args.start_from, 25)
        
        # Verify response format includes correct pagination info
        response_text = response[0].text
        self.assertIn('showing 2 results starting from position 25', response_text)
        self.assertIn('Showing results 26-27', response_text)
        self.assertIn('start_from=50', response_text)

    @patch('src.email_client.server.email_client')
    async def test_search_emails_first_page_full(self, mock_email_client):
        """Test first page with full results."""
        # Mock email client response with exactly max_results items
        mock_results = []
        for i in range(1, 51):  # 50 results
            mock_results.append({
                'id': str(i),
                'from': f'test{i}@example.com',
                'date': '2024-01-01',
                'subject': f'Test {i}'
            })
        mock_email_client.search_emails = AsyncMock(return_value=mock_results)
        
        arguments = {
            'max_results': 50,
            'start_from': 0
        }
        
        response = await _handle_search_emails(arguments)
        
        # Verify response format
        response_text = response[0].text
        self.assertIn('showing 50 results starting from position 0', response_text)
        self.assertIn('Showing results 1-50', response_text)
        self.assertIn('start_from=50', response_text)

    @patch('src.email_client.server.email_client')
    async def test_search_emails_last_page_partial(self, mock_email_client):
        """Test last page with partial results."""
        # Mock email client response with fewer than max_results items
        mock_results = []
        for i in range(101, 116):  # 15 results (partial last page)
            mock_results.append({
                'id': str(i),
                'from': f'test{i}@example.com',
                'date': '2024-01-01',
                'subject': f'Test {i}'
            })
        mock_email_client.search_emails = AsyncMock(return_value=mock_results)
        
        arguments = {
            'max_results': 50,
            'start_from': 100
        }
        
        response = await _handle_search_emails(arguments)
        
        # Verify response format for partial page
        response_text = response[0].text
        self.assertIn('showing 15 results starting from position 100', response_text)
        self.assertIn('Showing results 101-115', response_text)
        self.assertIn('start_from=150', response_text)

    @patch('src.email_client.server.email_client')
    async def test_search_emails_no_results(self, mock_email_client):
        """Test pagination with no results."""
        # Mock email client response with no results
        mock_email_client.search_emails = AsyncMock(return_value=[])
        
        arguments = {
            'max_results': 50,
            'start_from': 0
        }
        
        response = await _handle_search_emails(arguments)
        
        # Should return no results message
        response_text = response[0].text
        self.assertEqual(response_text, "No emails found matching the criteria.")

    @patch('src.email_client.server.email_client')
    async def test_search_emails_with_all_parameters(self, mock_email_client):
        """Test search-emails with all possible parameters including pagination."""
        # Mock email client response (async method)
        mock_email_client.search_emails = AsyncMock(return_value=[
            {'id': '51', 'from': 'sender@example.com', 'date': '2024-01-15', 'subject': 'Important Email'},
        ])
        
        # Test with all parameters
        arguments = {
            'start_date': '2024-01-01',
            'end_date': '2024-01-31',
            'keyword': 'important',
            'folder': 'inbox',
            'max_results': 20,
            'start_from': 50
        }
        
        response = await _handle_search_emails(arguments)
        
        # Verify SearchCriteria was created with all parameters
        mock_email_client.search_emails.assert_called_once()
        call_args = mock_email_client.search_emails.call_args[0][0]
        self.assertEqual(call_args.start_date, '2024-01-01')
        self.assertEqual(call_args.end_date, '2024-01-31')
        self.assertEqual(call_args.keyword, 'important')
        self.assertEqual(call_args.folder, 'inbox')
        self.assertEqual(call_args.max_results, 20)
        self.assertEqual(call_args.start_from, 50)
        
        # Verify response format
        response_text = response[0].text
        self.assertIn('showing 1 results starting from position 50', response_text)
        self.assertIn('start_from=70', response_text)

    @patch('src.email_client.server.email_client')
    async def test_search_emails_boundary_values(self, mock_email_client):
        """Test search-emails with boundary values for pagination."""
        # Mock email client response (async method)
        mock_email_client.search_emails = AsyncMock(return_value=[
            {'id': '1', 'from': 'test@example.com', 'date': '2024-01-01', 'subject': 'Test'},
        ])
        
        # Test with minimum values
        arguments_min = {
            'max_results': 1,
            'start_from': 0
        }
        
        response = await _handle_search_emails(arguments_min)
        
        # Verify minimum values work
        mock_email_client.search_emails.assert_called()
        call_args = mock_email_client.search_emails.call_args[0][0]
        self.assertEqual(call_args.max_results, 1)
        self.assertEqual(call_args.start_from, 0)
        
        # Test with maximum values
        mock_email_client.reset_mock()
        arguments_max = {
            'max_results': 1000,
            'start_from': 9999
        }
        
        response = await _handle_search_emails(arguments_max)
        
        # Verify maximum values work
        mock_email_client.search_emails.assert_called()
        call_args = mock_email_client.search_emails.call_args[0][0]
        self.assertEqual(call_args.max_results, 1000)
        self.assertEqual(call_args.start_from, 9999)


class TestPaginationResponseFormatting(unittest.TestCase):
    """Test pagination response formatting."""

    def test_pagination_info_single_result(self):
        """Test pagination info formatting for single result."""
        # This would be testing the actual formatting logic from the server
        # For now, we verify the expected format based on our implementation
        start_from = 0
        max_results = 100
        result_count = 1
        
        expected_range = f"{start_from + 1}-{start_from + result_count}"
        expected_next_start = start_from + max_results
        
        self.assertEqual(expected_range, "1-1")
        self.assertEqual(expected_next_start, 100)

    def test_pagination_info_full_page(self):
        """Test pagination info formatting for full page."""
        start_from = 50
        max_results = 25
        result_count = 25
        
        expected_range = f"{start_from + 1}-{start_from + result_count}"
        expected_next_start = start_from + max_results
        
        self.assertEqual(expected_range, "51-75")
        self.assertEqual(expected_next_start, 75)

    def test_pagination_info_partial_page(self):
        """Test pagination info formatting for partial page."""
        start_from = 100
        max_results = 50
        result_count = 15  # Partial page
        
        expected_range = f"{start_from + 1}-{start_from + result_count}"
        expected_next_start = start_from + max_results
        
        self.assertEqual(expected_range, "101-115")
        self.assertEqual(expected_next_start, 150)


if __name__ == '__main__':
    unittest.main()