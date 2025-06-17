#!/usr/bin/env python3
"""
Test suite for MCP data processing tools.
Tests the MCP server's data processing functionality without requiring email connectivity.
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import pandas as pd
import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.email_client.server import (
    _handle_create_collection,
    _handle_update_collection,
    _handle_fetch_collection,
    _handle_list_collections,
    _handle_preview_collection,
    datastore,
)
from mcp import types


class TestMCPDataProcessing(unittest.TestCase):
    """Test suite for MCP data processing tools."""

    def setUp(self):
        """Set up test fixtures."""
        # Clear the datastore before each test
        datastore._collections.clear()
        datastore._metadata.clear()
        datastore._execution_history.clear()

    def tearDown(self):
        """Clean up after each test."""
        # Clear the datastore after each test
        datastore._collections.clear()
        datastore._metadata.clear()
        datastore._execution_history.clear()

    @patch('src.email_client.server.email_client')
    async def test_create_collection_success(self, mock_email_client):
        """Test successful collection creation from email search."""
        # Mock email search results
        mock_search_results = [
            {"id": "1", "from": "alice@test.com", "subject": "Test 1", "date": "2024-01-01"},
            {"id": "2", "from": "bob@test.com", "subject": "Test 2", "date": "2024-01-02"},
            {"id": "3", "from": "alice@test.com", "subject": "Test 3", "date": "2024-01-03"},
        ]
        mock_email_client.search_emails = AsyncMock(return_value=mock_search_results)

        # Test arguments
        arguments = {
            "search_criteria": {
                "start_date": "2024-01-01",
                "end_date": "2024-01-03",
                "folder": "inbox"
            },
            "name": "test_collection"
        }

        # Call the handler
        result = await _handle_create_collection(arguments)

        # Verify result
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], types.TextContent)
        
        result_text = result[0].text
        self.assertIn("Created collection 'test_collection'", result_text)
        self.assertIn("3 rows, 4 columns", result_text)
        self.assertIn("id, from, subject, date", result_text)

        # Verify collection was created in datastore
        collections = datastore.list_collections()
        self.assertEqual(len(collections), 1)
        self.assertEqual(collections[0]['name'], 'test_collection')

    @patch('src.email_client.server.email_client')
    async def test_create_collection_no_results(self, mock_email_client):
        """Test collection creation when no emails are found."""
        mock_email_client.search_emails = AsyncMock(return_value=[])

        arguments = {
            "search_criteria": {
                "sender": "nonexistent@test.com"
            }
        }

        result = await _handle_create_collection(arguments)

        self.assertEqual(len(result), 1)
        self.assertIn("No emails found", result[0].text)

    async def test_update_collection_success(self):
        """Test successful collection update with pandas operation."""
        # Create a test collection first
        test_data = pd.DataFrame([
            {"id": "1", "from": "alice@test.com", "subject": "Test 1"},
            {"id": "2", "from": "bob@test.com", "subject": "Test 2"},
            {"id": "3", "from": "alice@test.com", "subject": "Test 3"},
        ])
        metadata = datastore.create(test_data, "test_collection")
        collection_id = metadata["id"]

        # Test update operation
        arguments = {
            "collection_id": collection_id,
            "operation": "df.drop(columns=['id'])"
        }

        result = await _handle_update_collection(arguments)

        # Verify result
        self.assertEqual(len(result), 1)
        result_text = result[0].text
        self.assertIn("Updated collection 'test_collection'", result_text)
        self.assertIn("3 rows, 2 columns", result_text)
        self.assertIn("from, subject", result_text)

    async def test_update_collection_groupby_operation(self):
        """Test collection update with groupby operation."""
        # Create test data with duplicate senders
        test_data = pd.DataFrame([
            {"from": "alice@test.com", "subject": "Test 1"},
            {"from": "bob@test.com", "subject": "Test 2"},
            {"from": "alice@test.com", "subject": "Test 3"},
            {"from": "alice@test.com", "subject": "Test 4"},
        ])
        metadata = datastore.create(test_data, "email_counts")
        collection_id = metadata["id"]

        # Group by sender and count
        arguments = {
            "collection_id": collection_id,
            "operation": "df.groupby('from').size().reset_index(name='count').sort_values('count', ascending=False)"
        }

        result = await _handle_update_collection(arguments)

        # Verify result
        self.assertEqual(len(result), 1)
        result_text = result[0].text
        self.assertIn("Updated collection 'email_counts'", result_text)
        self.assertIn("2 rows, 2 columns", result_text)  # 2 unique senders
        self.assertIn("from, count", result_text)

    async def test_update_collection_invalid_operation(self):
        """Test collection update with invalid operation."""
        # Create a test collection
        test_data = pd.DataFrame([{"col1": "value1"}])
        metadata = datastore.create(test_data, "test_collection")
        collection_id = metadata["id"]

        # Test invalid operation
        arguments = {
            "collection_id": collection_id,
            "operation": "invalid_operation()"
        }

        result = await _handle_update_collection(arguments)

        # Verify error handling
        self.assertEqual(len(result), 1)
        self.assertIn("Operation failed:", result[0].text)

    async def test_fetch_collection_records_format(self):
        """Test fetching collection data in records format."""
        # Create test data
        test_data = pd.DataFrame([
            {"sender": "alice@test.com", "count": 3},
            {"sender": "bob@test.com", "count": 1},
        ])
        metadata = datastore.create(test_data, "sender_counts")
        collection_id = metadata["id"]

        # Fetch collection
        arguments = {
            "collection_id": collection_id,
            "format": "records",
            "limit": 10
        }

        result = await _handle_fetch_collection(arguments)

        # Verify result
        self.assertEqual(len(result), 1)
        result_text = result[0].text
        self.assertIn("Collection 'sender_counts'", result_text)
        self.assertIn("2 rows, 2 columns", result_text)
        self.assertIn("alice@test.com", result_text)
        self.assertIn("bob@test.com", result_text)

    async def test_fetch_collection_csv_format(self):
        """Test fetching collection data in CSV format."""
        # Create test data
        test_data = pd.DataFrame([
            {"sender": "alice@test.com", "count": 3},
            {"sender": "bob@test.com", "count": 1},
        ])
        metadata = datastore.create(test_data, "test_csv")
        collection_id = metadata["id"]

        # Fetch as CSV
        arguments = {
            "collection_id": collection_id,
            "format": "csv"
        }

        result = await _handle_fetch_collection(arguments)

        # Verify CSV format
        self.assertEqual(len(result), 1)
        result_text = result[0].text
        self.assertIn("sender,count", result_text)  # CSV header
        self.assertIn("alice@test.com,3", result_text)

    async def test_list_collections_empty(self):
        """Test listing collections when none exist."""
        arguments = {}
        result = await _handle_list_collections(arguments)

        self.assertEqual(len(result), 1)
        self.assertIn("No collections found", result[0].text)

    async def test_list_collections_with_data(self):
        """Test listing collections with existing data."""
        # Create multiple collections
        test_data1 = pd.DataFrame([{"col1": "value1"}])
        test_data2 = pd.DataFrame([{"col2": "value2"}, {"col2": "value3"}])
        
        datastore.create(test_data1, "collection_1")
        datastore.create(test_data2, "collection_2")

        arguments = {}
        result = await _handle_list_collections(arguments)

        # Verify result
        self.assertEqual(len(result), 1)
        result_text = result[0].text
        self.assertIn("Available collections:", result_text)
        self.assertIn("collection_1", result_text)
        self.assertIn("collection_2", result_text)
        self.assertIn("1x1", result_text)  # collection_1 shape
        self.assertIn("2x1", result_text)  # collection_2 shape

    async def test_preview_collection(self):
        """Test previewing a collection."""
        # Create test data
        test_data = pd.DataFrame([
            {"sender": "alice@test.com", "count": 3},
            {"sender": "bob@test.com", "count": 1},
            {"sender": "charlie@test.com", "count": 2},
        ])
        metadata = datastore.create(test_data, "preview_test")
        collection_id = metadata["id"]

        # Preview collection
        arguments = {
            "collection_id": collection_id,
            "rows": 2
        }

        result = await _handle_preview_collection(arguments)

        # Verify result
        self.assertEqual(len(result), 1)
        result_text = result[0].text
        self.assertIn("Collection 'preview_test'", result_text)
        self.assertIn("3 rows, 2 columns", result_text)
        self.assertIn("Data types:", result_text)
        self.assertIn("sender: object", result_text)
        self.assertIn("count: int64", result_text)
        self.assertIn("First 2 rows:", result_text)

    async def test_error_handling_missing_collection(self):
        """Test error handling for operations on non-existent collections."""
        fake_id = "nonexistent-collection-id"

        # Test update on missing collection
        update_args = {
            "collection_id": fake_id,
            "operation": "df.head()"
        }
        result = await _handle_update_collection(update_args)
        self.assertIn("Collection", result[0].text)
        self.assertIn("not found", result[0].text)

        # Test fetch on missing collection
        fetch_args = {"collection_id": fake_id}
        result = await _handle_fetch_collection(fetch_args)
        self.assertIn("Collection", result[0].text)
        self.assertIn("not found", result[0].text)

        # Test preview on missing collection
        preview_args = {"collection_id": fake_id}
        result = await _handle_preview_collection(preview_args)
        self.assertIn("Collection", result[0].text)
        self.assertIn("not found", result[0].text)

    async def test_missing_required_parameters(self):
        """Test error handling for missing required parameters."""
        # Test update without collection_id
        result = await _handle_update_collection({})
        self.assertIn("Collection ID is required", result[0].text)

        # Test update without operation
        result = await _handle_update_collection({"collection_id": "test"})
        self.assertIn("Operation is required", result[0].text)

        # Test fetch without collection_id
        result = await _handle_fetch_collection({})
        self.assertIn("Collection ID is required", result[0].text)

        # Test preview without collection_id
        result = await _handle_preview_collection({})
        self.assertIn("Collection ID is required", result[0].text)


class TestMCPIntegrationWorkflow(unittest.TestCase):
    """Integration tests for complete MCP workflow."""

    def setUp(self):
        """Set up test fixtures."""
        datastore._collections.clear()
        datastore._metadata.clear()
        datastore._execution_history.clear()

    def tearDown(self):
        """Clean up after each test."""
        datastore._collections.clear()
        datastore._metadata.clear()
        datastore._execution_history.clear()

    @patch('src.email_client.server.email_client')
    async def test_complete_email_analysis_workflow(self, mock_email_client):
        """Test the complete workflow: search -> create -> manipulate -> fetch."""
        # Mock email search results
        mock_search_results = [
            {"id": "1", "from": "alice@company.com", "subject": "Weekly report", "date": "2024-01-01"},
            {"id": "2", "from": "bob@company.com", "subject": "Project update", "date": "2024-01-01"},
            {"id": "3", "from": "alice@company.com", "subject": "Meeting notes", "date": "2024-01-02"},
            {"id": "4", "from": "charlie@company.com", "subject": "Budget review", "date": "2024-01-02"},
            {"id": "5", "from": "alice@company.com", "subject": "Follow up", "date": "2024-01-03"},
        ]
        mock_email_client.search_emails = AsyncMock(return_value=mock_search_results)

        # Step 1: Create collection from email search
        create_args = {
            "search_criteria": {
                "start_date": "2024-01-01",
                "end_date": "2024-01-03",
                "folder": "inbox"
            },
            "name": "weekly_emails"
        }
        
        result = await _handle_create_collection(create_args)
        self.assertIn("Created collection 'weekly_emails'", result[0].text)
        
        # Extract collection ID from the created collections
        collections = datastore.list_collections()
        collection_id = collections[0]["id"]

        # Step 2: Drop unnecessary columns
        update_args = {
            "collection_id": collection_id,
            "operation": "df.drop(columns=['id', 'subject', 'date'])"
        }
        
        result = await _handle_update_collection(update_args)
        self.assertIn("5 rows, 1 columns", result[0].text)
        self.assertIn("from", result[0].text)

        # Step 3: Group by sender and count
        count_args = {
            "collection_id": collection_id,
            "operation": "df.groupby('from').size().reset_index(name='count').sort_values('count', ascending=False)"
        }
        
        result = await _handle_update_collection(count_args)
        self.assertIn("3 rows, 2 columns", result[0].text)  # 3 unique senders
        self.assertIn("from, count", result[0].text)

        # Step 4: Fetch final results
        fetch_args = {
            "collection_id": collection_id,
            "format": "records"
        }
        
        result = await _handle_fetch_collection(fetch_args)
        result_text = result[0].text
        
        # Verify the final results contain expected sender counts
        # Alice should have 3 emails, Bob and Charlie should have 1 each
        self.assertIn("alice@company.com", result_text)
        self.assertIn("bob@company.com", result_text)
        self.assertIn("charlie@company.com", result_text)


async def run_async_tests():
    """Run all async tests."""
    test_cases = [
        TestMCPDataProcessing(),
        TestMCPIntegrationWorkflow()
    ]
    
    for test_case in test_cases:        
        # Get all test methods
        test_methods = [method for method in dir(test_case) if method.startswith('test_')]
        
        for test_method_name in test_methods:
            test_method = getattr(test_case, test_method_name)
            if asyncio.iscoroutinefunction(test_method):
                print(f"Running {test_case.__class__.__name__}.{test_method_name}...")
                test_case.setUp()  # Set up before each test
                try:
                    await test_method()
                    print(f"✓ {test_method_name} passed")
                except Exception as e:
                    print(f"✗ {test_method_name} failed: {e}")
                    import traceback
                    traceback.print_exc()
                finally:
                    test_case.tearDown()  # Clean up after each test


if __name__ == "__main__":
    print("Running MCP Data Processing Tests...")
    asyncio.run(run_async_tests())
    print("Test run completed!")