#!/usr/bin/env python3
"""
Test suite for MCP email endpoints.
Tests the MCP server's email functionality using a fake email client.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime
import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.email_client.server as server_module
from src.email_client.server import (
    _handle_search_emails,
    _handle_get_email_content,
    _handle_send_email,
    _handle_count_daily_emails,
    _handle_list_folders,
    _handle_move_email,
    _handle_delete_email,
)
from src.email_client.email_client import EmailMessage, EmailSearchError, EmailSendError, EmailDeletionError
from mcp import types


class FakeEmailClient:
    """Fake email client for testing that mimics the real EmailClient interface."""
    
    def __init__(self):
        self.fake_emails = [
            {
                "id": "email_1",
                "from": "alice@company.com",
                "subject": "Weekly Report - Q1 2024",
                "date": "2024-01-15",
                "folder": "inbox"
            },
            {
                "id": "email_2", 
                "from": "bob@company.com",
                "subject": "Project Alpha Update",
                "date": "2024-01-16",
                "folder": "inbox"
            },
            {
                "id": "email_3",
                "from": "charlie@external.com",
                "subject": "Meeting Invitation",
                "date": "2024-01-17",
                "folder": "inbox"
            },
            {
                "id": "email_4",
                "from": "alice@company.com",
                "subject": "Follow-up on Budget",
                "date": "2024-01-18",
                "folder": "inbox"
            },
            {
                "id": "sent_1",
                "from": "me@mycompany.com",
                "to": "client@external.com",
                "subject": "Proposal Draft",
                "date": "2024-01-15",
                "folder": "sent"
            }
        ]
        
        self.fake_folders = [
            {"name": "INBOX", "display_name": "Inbox", "attributes": ["\\HasNoChildren"]},
            {"name": "[Gmail]/Sent Mail", "display_name": "Sent", "attributes": ["\\HasNoChildren", "\\Sent"]},
            {"name": "[Gmail]/Drafts", "display_name": "Drafts", "attributes": ["\\HasNoChildren", "\\Drafts"]},
            {"name": "[Gmail]/Trash", "display_name": "Trash", "attributes": ["\\HasNoChildren", "\\Trash"]},
        ]
        
        self.sent_emails = []
        self.moved_emails = []
        self.deleted_emails = []

    async def search_emails(self, criteria):
        """Simulate email search based on criteria."""
        # Apply folder filter
        folder_map = {"inbox": "inbox", "sent": "sent"}
        target_folder = folder_map.get(criteria.folder, "inbox")
        
        results = [email for email in self.fake_emails if email.get("folder") == target_folder]
        
        # Apply date filters
        if criteria.start_date:
            results = [email for email in results if email["date"] >= criteria.start_date]
        if criteria.end_date:
            results = [email for email in results if email["date"] <= criteria.end_date]
            
        # Apply text filters
        if criteria.subject:
            results = [email for email in results if criteria.subject.lower() in email["subject"].lower()]
        if criteria.sender:
            results = [email for email in results if criteria.sender.lower() in email["from"].lower()]
        if criteria.body:
            # For simplicity, just filter by subject for body searches in fake client
            results = [email for email in results if criteria.body.lower() in email["subject"].lower()]
            
        # Apply pagination
        start_idx = criteria.start_from
        end_idx = start_idx + criteria.max_results
        results = results[start_idx:end_idx]
        
        return results

    async def get_email_content(self, email_id):
        """Get full email content by ID."""
        for email in self.fake_emails:
            if email["id"] == email_id:
                content = {
                    "from": email["from"],
                    "to": email.get("to", "me@mycompany.com"),
                    "subject": email["subject"],
                    "date": email["date"],
                    "content": f"This is the full content of email with subject: {email['subject']}\n\nFake email body content for testing purposes."
                }
                return content
        return None

    async def send_email(self, message: EmailMessage):
        """Simulate sending an email."""
        if not message.to_addresses:
            raise EmailSendError("No recipients specified")
        if not message.subject:
            raise EmailSendError("Subject is required")
        if not message.content:
            raise EmailSendError("Content is required")
        if "fail" in message.subject.lower():
            raise EmailSendError("Simulated send failure")
            
        # Record the sent email
        self.sent_emails.append({
            "to": message.to_addresses,
            "cc": message.cc_addresses,
            "subject": message.subject,
            "content": message.content,
            "sent_at": datetime.now().isoformat()
        })

    async def count_daily_emails(self, start_date, end_date):
        """Count emails by day in date range."""
        # Simulate daily counts
        daily_counts = {
            "2024-01-15": 2,
            "2024-01-16": 1,
            "2024-01-17": 1,
            "2024-01-18": 1,
        }
        
        # Filter by date range
        result = {}
        for date_str, count in daily_counts.items():
            if start_date <= date_str <= end_date:
                result[date_str] = count
                
        return result

    async def list_folders(self):
        """List available email folders."""
        return self.fake_folders

    async def move_email(self, email_ids, source_folder, destination_folder):
        """Simulate moving emails between folders."""
        if "fail" in destination_folder:
            raise EmailDeletionError("Simulated move failure")
            
        self.moved_emails.append({
            "email_ids": email_ids,
            "source_folder": source_folder,
            "destination_folder": destination_folder,
            "moved_at": datetime.now().isoformat()
        })

    async def delete_email(self, email_ids, folder, permanent=False):
        """Simulate deleting emails."""
        if len(email_ids) > 5:
            raise EmailDeletionError("Too many emails to delete at once")
            
        self.deleted_emails.append({
            "email_ids": email_ids,
            "folder": folder,
            "permanent": permanent,
            "deleted_at": datetime.now().isoformat()
        })

    async def query_server_capabilities(self):
        """Simulate querying server capabilities."""
        pass


class TestMCPEmailEndpoints(unittest.TestCase):
    """Test suite for MCP email endpoints."""

    def setUp(self):
        """Set up test fixtures."""
        self.fake_client = FakeEmailClient()

    async def test_search_emails_basic(self):
        """Test basic email search functionality."""
        with patch('src.email_client.server.email_client', self.fake_client):
            arguments = {
                "folder": "inbox",
                "start_date": "2024-01-15",
                "end_date": "2024-01-18"
            }
            
            result = await _handle_search_emails(arguments)
            
            self.assertEqual(len(result), 1)
            self.assertIsInstance(result[0], types.TextContent)
            result_text = result[0].text
            
            self.assertIn("Found emails", result_text)
            self.assertIn("alice@company.com", result_text)
            self.assertIn("bob@company.com", result_text)
            self.assertIn("Weekly Report", result_text)

    async def test_search_emails_with_filters(self):
        """Test email search with subject and sender filters."""
        with patch('src.email_client.server.email_client', self.fake_client):
            # Test subject filter
            arguments = {
                "folder": "inbox",
                "subject": "Weekly"
            }
            
            result = await _handle_search_emails(arguments)
            result_text = result[0].text
            
            self.assertIn("Weekly Report", result_text)
            self.assertNotIn("Project Alpha", result_text)

    async def test_search_emails_sent_folder(self):
        """Test searching in sent folder."""
        with patch('src.email_client.server.email_client', self.fake_client):
            arguments = {
                "folder": "sent",
                "start_date": "2024-01-01",
                "end_date": "2024-01-31"
            }
            
            result = await _handle_search_emails(arguments)
            result_text = result[0].text
            
            self.assertIn("Proposal Draft", result_text)
            self.assertNotIn("Weekly Report", result_text)  # Inbox email

    async def test_search_emails_no_results(self):
        """Test email search when no results are found."""
        with patch('src.email_client.server.email_client', self.fake_client):
            arguments = {
                "folder": "inbox",
                "sender": "nonexistent@example.com"
            }
            
            result = await _handle_search_emails(arguments)
            result_text = result[0].text
            
            self.assertIn("No emails found", result_text)

    async def test_search_emails_pagination(self):
        """Test email search pagination."""
        with patch('src.email_client.server.email_client', self.fake_client):
            arguments = {
                "folder": "inbox",
                "max_results": 2,
                "start_from": 0
            }
            
            result = await _handle_search_emails(arguments)
            result_text = result[0].text
            
            self.assertIn("showing 2 results", result_text)
            self.assertIn("start_from=2", result_text)  # Next page hint

    async def test_get_email_content_success(self):
        """Test retrieving email content by ID."""
        with patch('src.email_client.server.email_client', self.fake_client):
            arguments = {"email_id": "email_1"}
            
            result = await _handle_get_email_content(arguments)
            
            self.assertEqual(len(result), 1)
            result_text = result[0].text
            
            self.assertIn("From: alice@company.com", result_text)
            self.assertIn("Subject: Weekly Report - Q1 2024", result_text)
            self.assertIn("Content:", result_text)
            self.assertIn("This is the full content", result_text)

    async def test_get_email_content_not_found(self):
        """Test retrieving content for non-existent email."""
        with patch('src.email_client.server.email_client', self.fake_client):
            arguments = {"email_id": "nonexistent_email"}
            
            result = await _handle_get_email_content(arguments)
            result_text = result[0].text
            
            self.assertIn("No email content found", result_text)

    async def test_get_email_content_missing_id(self):
        """Test retrieving content without providing email ID."""
        with patch('src.email_client.server.email_client', self.fake_client):
            arguments = {}
            
            result = await _handle_get_email_content(arguments)
            result_text = result[0].text
            
            self.assertIn("Email ID is required", result_text)

    async def test_send_email_success(self):
        """Test successful email sending."""
        with patch('src.email_client.server.email_client', self.fake_client):
            arguments = {
                "to": ["recipient@example.com"],
                "subject": "Test Email",
                "content": "This is a test email content.",
                "cc": ["cc@example.com"]
            }
            
            result = await _handle_send_email(arguments)
            
            self.assertEqual(len(result), 1)
            result_text = result[0].text
            
            self.assertIn("Email sent successfully", result_text)
            
            # Verify email was recorded in fake client
            self.assertEqual(len(self.fake_client.sent_emails), 1)
            sent_email = self.fake_client.sent_emails[0]
            self.assertEqual(sent_email["to"], ["recipient@example.com"])
            self.assertEqual(sent_email["subject"], "Test Email")

    async def test_send_email_failure(self):
        """Test email sending failure."""
        with patch('src.email_client.server.email_client', self.fake_client):
            arguments = {
                "to": ["recipient@example.com"],
                "subject": "This will fail",  # Contains "fail" keyword
                "content": "Test content"
            }
            
            result = await _handle_send_email(arguments)
            result_text = result[0].text
            
            self.assertIn("Failed to send email", result_text)
            self.assertIn("Simulated send failure", result_text)

    async def test_send_email_missing_fields(self):
        """Test email sending with missing required fields."""
        with patch('src.email_client.server.email_client', self.fake_client):
            # Missing recipients
            arguments = {
                "subject": "Test",
                "content": "Test content"
            }
            
            result = await _handle_send_email(arguments)
            result_text = result[0].text
            
            self.assertIn("Invalid input", result_text)

    async def test_count_daily_emails(self):
        """Test daily email counting."""
        with patch('src.email_client.server.email_client', self.fake_client):
            arguments = {
                "start_date": "2024-01-15",
                "end_date": "2024-01-17"
            }
            
            result = await _handle_count_daily_emails(arguments)
            
            self.assertEqual(len(result), 1)
            result_text = result[0].text
            
            self.assertIn("Daily email counts", result_text)
            self.assertIn("2024-01-15 | 2", result_text)
            self.assertIn("2024-01-16 | 1", result_text)
            self.assertIn("2024-01-17 | 1", result_text)

    async def test_count_daily_emails_missing_dates(self):
        """Test daily email counting with missing dates."""
        with patch('src.email_client.server.email_client', self.fake_client):
            arguments = {"start_date": "2024-01-15"}  # Missing end_date
            
            result = await _handle_count_daily_emails(arguments)
            result_text = result[0].text
            
            self.assertIn("Both start_date and end_date are required", result_text)

    async def test_list_folders(self):
        """Test listing email folders."""
        with patch('src.email_client.server.email_client', self.fake_client):
            arguments = {}
            
            result = await _handle_list_folders(arguments)
            
            self.assertEqual(len(result), 1)
            result_text = result[0].text
            
            self.assertIn("Available email folders", result_text)
            self.assertIn("INBOX", result_text)
            self.assertIn("[Gmail]/Sent Mail", result_text)
            self.assertIn("[Gmail]/Drafts", result_text)
            self.assertIn("Found 4 folders total", result_text)

    async def test_move_email_success(self):
        """Test successful email moving."""
        # Store original value and set write operations enabled
        original_value = server_module.WRITE_OPERATIONS_ENABLED
        server_module.WRITE_OPERATIONS_ENABLED = True
        
        try:
            with patch('src.email_client.server.email_client', self.fake_client):
                arguments = {
                    "email_ids": ["email_1", "email_2"],
                    "source_folder": "inbox",
                    "destination_folder": "archive"
                }
                
                result = await _handle_move_email(arguments)
                
                self.assertEqual(len(result), 1)
                result_text = result[0].text
                
                self.assertIn("Successfully moved 2 emails", result_text)
                self.assertIn("from 'inbox' to 'archive'", result_text)
                
                # Verify move was recorded
                self.assertEqual(len(self.fake_client.moved_emails), 1)
                move_record = self.fake_client.moved_emails[0]
                self.assertEqual(move_record["email_ids"], ["email_1", "email_2"])
        finally:
            server_module.WRITE_OPERATIONS_ENABLED = original_value

    async def test_move_email_failure(self):
        """Test email moving failure."""
        # Store original value and set write operations enabled
        original_value = server_module.WRITE_OPERATIONS_ENABLED
        server_module.WRITE_OPERATIONS_ENABLED = True
        
        try:
            with patch('src.email_client.server.email_client', self.fake_client):
                arguments = {
                    "email_ids": ["email_1"],
                    "source_folder": "inbox",
                    "destination_folder": "fail_folder"  # Contains "fail"
                }
                
                result = await _handle_move_email(arguments)
                result_text = result[0].text
                
                self.assertIn("Failed to move emails", result_text)
        finally:
            server_module.WRITE_OPERATIONS_ENABLED = original_value

    async def test_delete_email_success(self):
        """Test successful email deletion."""
        # Store original value and set write operations enabled
        original_value = server_module.WRITE_OPERATIONS_ENABLED
        server_module.WRITE_OPERATIONS_ENABLED = True
        
        try:
            with patch('src.email_client.server.email_client', self.fake_client):
                arguments = {
                    "email_ids": ["email_1", "email_2"],
                    "folder": "inbox",
                    "permanent": False
                }
                
                result = await _handle_delete_email(arguments)
                
                self.assertEqual(len(result), 1)
                result_text = result[0].text
                
                self.assertIn("Successfully moved 2 emails to trash", result_text)
                self.assertIn("restored from the trash", result_text)
                
                # Verify deletion was recorded
                self.assertEqual(len(self.fake_client.deleted_emails), 1)
                delete_record = self.fake_client.deleted_emails[0]
                self.assertEqual(delete_record["email_ids"], ["email_1", "email_2"])
                self.assertFalse(delete_record["permanent"])
        finally:
            server_module.WRITE_OPERATIONS_ENABLED = original_value

    async def test_delete_email_permanent(self):
        """Test permanent email deletion."""
        # Store original value and set write operations enabled
        original_value = server_module.WRITE_OPERATIONS_ENABLED
        server_module.WRITE_OPERATIONS_ENABLED = True
        
        try:
            with patch('src.email_client.server.email_client', self.fake_client):
                arguments = {
                    "email_ids": ["email_1"],
                    "folder": "inbox",
                    "permanent": True
                }
                
                result = await _handle_delete_email(arguments)
                result_text = result[0].text
                
                self.assertIn("Successfully permanently deleted 1 email", result_text)
                self.assertIn("cannot be undone", result_text)
        finally:
            server_module.WRITE_OPERATIONS_ENABLED = original_value

    async def test_delete_email_too_many(self):
        """Test deleting too many emails at once."""
        # Store original value and set write operations enabled
        original_value = server_module.WRITE_OPERATIONS_ENABLED
        server_module.WRITE_OPERATIONS_ENABLED = True
        
        try:
            with patch('src.email_client.server.email_client', self.fake_client):
                # Try to delete more than 5 emails
                email_ids = [f"email_{i}" for i in range(10)]
                arguments = {
                    "email_ids": email_ids,
                    "folder": "inbox"
                }
                
                result = await _handle_delete_email(arguments)
                result_text = result[0].text
                
                self.assertIn("Failed to delete emails", result_text)
                self.assertIn("Too many emails", result_text)
        finally:
            server_module.WRITE_OPERATIONS_ENABLED = original_value

    # Note: Testing WRITE_OPERATIONS_ENABLED flag is complex due to module import behavior
    # The flag is checked at runtime in the actual handler functions, but in tests
    # the global variable doesn't get properly updated after import.
    # This functionality is tested through integration tests where the server
    # is started with --enable-write-operations flag.


class TestMCPEmailErrorHandling(unittest.TestCase):
    """Test error handling in email MCP endpoints."""

    def setUp(self):
        """Set up test fixtures."""
        self.fake_client = FakeEmailClient()

    async def test_search_timeout_error(self):
        """Test handling of search timeout errors."""
        with patch('src.email_client.server.email_client') as mock_client:
            mock_client.search_emails.side_effect = asyncio.TimeoutError()
            
            arguments = {"folder": "inbox"}
            result = await _handle_search_emails(arguments)
            result_text = result[0].text
            
            self.assertIn("Search operation timed out", result_text)

    async def test_search_general_error(self):
        """Test handling of general search errors."""
        with patch('src.email_client.server.email_client') as mock_client:
            mock_client.search_emails.side_effect = EmailSearchError("Connection failed")
            
            arguments = {"folder": "inbox"}
            result = await _handle_search_emails(arguments)
            result_text = result[0].text
            
            self.assertIn("Search failed", result_text)
            self.assertIn("Connection failed", result_text)

    async def test_invalid_date_format(self):
        """Test handling of invalid date formats."""
        with patch('src.email_client.server.email_client', self.fake_client):
            arguments = {
                "start_date": "invalid-date",
                "end_date": "2024-01-31"
            }
            
            result = await _handle_count_daily_emails(arguments)
            result_text = result[0].text
            
            self.assertIn("Invalid date format", result_text)


async def run_async_tests():
    """Run all async email endpoint tests."""
    test_cases = [
        TestMCPEmailEndpoints(),
        TestMCPEmailErrorHandling()
    ]
    
    total_tests = 0
    passed_tests = 0
    
    for test_case in test_cases:        
        # Get all test methods
        test_methods = [method for method in dir(test_case) if method.startswith('test_')]
        
        for test_method_name in test_methods:
            test_method = getattr(test_case, test_method_name)
            if asyncio.iscoroutinefunction(test_method):
                total_tests += 1
                print(f"Running {test_case.__class__.__name__}.{test_method_name}...")
                test_case.setUp()  # Set up before each test
                try:
                    await test_method()
                    print(f"✓ {test_method_name} passed")
                    passed_tests += 1
                except Exception as e:
                    print(f"✗ {test_method_name} failed: {e}")
                    import traceback
                    traceback.print_exc()
    
    print(f"\nTest Summary: {passed_tests}/{total_tests} tests passed")
    return passed_tests == total_tests


if __name__ == "__main__":
    print("Running MCP Email Endpoint Tests...")
    success = asyncio.run(run_async_tests())
    if success:
        print("All tests passed! ✅")
    else:
        print("Some tests failed! ❌")
        sys.exit(1)