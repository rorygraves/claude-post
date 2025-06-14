"""Unit tests for email search pagination functionality.

This module tests the pagination features added to the EmailClient,
including SearchCriteria validation, ESEARCH support, and fallback mechanisms.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import List, Tuple

from src.email_client.email_client import EmailClient, SearchCriteria, EmailSearchError


class TestSearchCriteriaPagination(unittest.TestCase):
    """Test SearchCriteria pagination parameter validation."""

    def test_default_pagination_values(self):
        """Test that default pagination values are set correctly."""
        criteria = SearchCriteria()
        self.assertEqual(criteria.max_results, 100)
        self.assertEqual(criteria.start_from, 0)

    def test_custom_pagination_values(self):
        """Test that custom pagination values are accepted."""
        criteria = SearchCriteria(max_results=50, start_from=25)
        self.assertEqual(criteria.max_results, 50)
        self.assertEqual(criteria.start_from, 25)

    def test_max_results_validation(self):
        """Test max_results parameter validation."""
        # Valid values
        SearchCriteria(max_results=1)  # Minimum
        SearchCriteria(max_results=1000)  # Maximum
        SearchCriteria(max_results=100)  # Default
        
        # Invalid values
        with self.assertRaises(ValueError, msg="max_results must be positive"):
            SearchCriteria(max_results=0)
        
        with self.assertRaises(ValueError, msg="max_results must be positive"):
            SearchCriteria(max_results=-1)
            
        with self.assertRaises(ValueError, msg="max_results cannot exceed 1000"):
            SearchCriteria(max_results=1001)

    def test_start_from_validation(self):
        """Test start_from parameter validation."""
        # Valid values
        SearchCriteria(start_from=0)  # Minimum
        SearchCriteria(start_from=1000)  # Large value
        
        # Invalid values
        with self.assertRaises(ValueError, msg="start_from must be non-negative"):
            SearchCriteria(start_from=-1)

    def test_pagination_with_other_criteria(self):
        """Test pagination parameters work with other search criteria."""
        criteria = SearchCriteria(
            folder="inbox",
            start_date="2024-01-01",
            end_date="2024-01-31", 
            keyword="test",
            max_results=25,
            start_from=50
        )
        
        self.assertEqual(criteria.folder, "inbox")
        self.assertEqual(criteria.start_date, "2024-01-01")
        self.assertEqual(criteria.end_date, "2024-01-31")
        self.assertEqual(criteria.keyword, "test")
        self.assertEqual(criteria.max_results, 25)
        self.assertEqual(criteria.start_from, 50)


class TestEmailClientPagination(unittest.IsolatedAsyncioTestCase):
    """Test EmailClient pagination functionality with mocked IMAP responses."""

    def setUp(self):
        """Set up test fixtures."""
        self.client = EmailClient()

    @patch('src.email_client.email_client.imaplib.IMAP4_SSL')
    async def test_search_with_pagination_regular_search(self, mock_imap_class):
        """Test pagination using regular SEARCH (no ESEARCH support)."""
        # Mock IMAP connection and responses
        mock_mail = MagicMock()
        mock_imap_class.return_value = mock_mail
        
        # Mock capability response (no ESEARCH)
        mock_mail.capability.return_value = ('OK', [b'IMAP4REV1 UIDPLUS'])
        
        # Mock search response with 150 message IDs
        message_ids = [str(i).encode() for i in range(1, 151)]  # 1-150
        mock_mail.search.return_value = ('OK', [b' '.join(message_ids)])
        
        # Mock select
        mock_mail.select.return_value = ('OK', [b'150'])
        
        # Mock fetch responses for first 50 messages
        mock_fetch_responses = []
        for i in range(1, 51):
            mock_fetch_responses.append((
                f'{i} (RFC822 {{100}}'.encode(),
                f'From: test{i}@example.com\r\nSubject: Test {i}\r\nDate: 2024-01-01\r\n\r\nTest message {i}'.encode()
            ))
        mock_mail.fetch.return_value = ('OK', mock_fetch_responses)
        
        # Test first page
        criteria = SearchCriteria(max_results=50, start_from=0)
        results = await self.client._search_with_pagination(mock_mail, 'ALL', criteria)
        
        # Should return first 50 message IDs
        self.assertEqual(len(results), 50)
        self.assertEqual(results[0], b'1')
        self.assertEqual(results[49], b'50')

    @patch('src.email_client.email_client.imaplib.IMAP4_SSL')
    async def test_search_with_pagination_second_page(self, mock_imap_class):
        """Test pagination for second page of results."""
        mock_mail = MagicMock()
        mock_imap_class.return_value = mock_mail
        
        # Mock capability response (no ESEARCH)
        mock_mail.capability.return_value = ('OK', [b'IMAP4REV1 UIDPLUS'])
        
        # Mock search response with 150 message IDs
        message_ids = [str(i).encode() for i in range(1, 151)]
        mock_mail.search.return_value = ('OK', [b' '.join(message_ids)])
        
        # Test second page (start from 50, get 50 more)
        criteria = SearchCriteria(max_results=50, start_from=50)
        results = await self.client._search_with_pagination(mock_mail, 'ALL', criteria)
        
        # Should return message IDs 51-100
        self.assertEqual(len(results), 50)
        self.assertEqual(results[0], b'51')
        self.assertEqual(results[49], b'100')

    @patch('src.email_client.email_client.imaplib.IMAP4_SSL')
    async def test_search_with_pagination_partial_last_page(self, mock_imap_class):
        """Test pagination when last page has fewer results than max_results."""
        mock_mail = MagicMock()
        mock_imap_class.return_value = mock_mail
        
        # Mock capability response (no ESEARCH)
        mock_mail.capability.return_value = ('OK', [b'IMAP4REV1 UIDPLUS'])
        
        # Mock search response with 125 message IDs
        message_ids = [str(i).encode() for i in range(1, 126)]
        mock_mail.search.return_value = ('OK', [b' '.join(message_ids)])
        
        # Test last page (start from 100, get 50 but only 25 available)
        criteria = SearchCriteria(max_results=50, start_from=100)
        results = await self.client._search_with_pagination(mock_mail, 'ALL', criteria)
        
        # Should return only remaining 25 message IDs (101-125)
        self.assertEqual(len(results), 25)
        self.assertEqual(results[0], b'101')
        self.assertEqual(results[24], b'125')

    @patch('src.email_client.email_client.imaplib.IMAP4_SSL')
    async def test_search_with_pagination_out_of_bounds(self, mock_imap_class):
        """Test pagination when start_from is beyond available results."""
        mock_mail = MagicMock()
        mock_imap_class.return_value = mock_mail
        
        # Mock capability response (no ESEARCH)
        mock_mail.capability.return_value = ('OK', [b'IMAP4REV1 UIDPLUS'])
        
        # Mock search response with 100 message IDs
        message_ids = [str(i).encode() for i in range(1, 101)]
        mock_mail.search.return_value = ('OK', [b' '.join(message_ids)])
        
        # Test out of bounds (start from 150, but only 100 messages exist)
        criteria = SearchCriteria(max_results=50, start_from=150)
        results = await self.client._search_with_pagination(mock_mail, 'ALL', criteria)
        
        # Should return empty list
        self.assertEqual(len(results), 0)

    @patch('src.email_client.email_client.imaplib.IMAP4_SSL')
    async def test_search_with_pagination_no_results(self, mock_imap_class):
        """Test pagination when search returns no results."""
        mock_mail = MagicMock()
        mock_imap_class.return_value = mock_mail
        
        # Mock capability response (no ESEARCH)
        mock_mail.capability.return_value = ('OK', [b'IMAP4REV1 UIDPLUS'])
        
        # Mock search response with no results
        mock_mail.search.return_value = ('OK', [b''])
        
        criteria = SearchCriteria(max_results=50, start_from=0)
        results = await self.client._search_with_pagination(mock_mail, 'ALL', criteria)
        
        # Should return empty list
        self.assertEqual(len(results), 0)

    @patch('src.email_client.email_client.imaplib.IMAP4_SSL')
    async def test_search_with_esearch_support(self, mock_imap_class):
        """Test pagination using ESEARCH when server supports it."""
        mock_mail = MagicMock()
        mock_imap_class.return_value = mock_mail
        
        # Mock capability response with ESEARCH support
        mock_mail.capability.return_value = ('OK', [b'IMAP4REV1 UIDPLUS ESEARCH'])
        
        # Mock ESEARCH response for pagination
        mock_mail._simple_command.return_value = ('OK', [b'* ESEARCH (TAG "A1") PARTIAL (26:50 101 102 103 104 105)'])
        
        criteria = SearchCriteria(max_results=25, start_from=25)
        results = await self.client._search_with_pagination(mock_mail, 'ALL', criteria)
        
        # Should return message IDs from ESEARCH response
        expected_ids = [b'101', b'102', b'103', b'104', b'105']
        self.assertEqual(results, expected_ids)

    @patch('src.email_client.email_client.imaplib.IMAP4_SSL')
    async def test_search_with_esearch_fallback(self, mock_imap_class):
        """Test fallback to regular search when ESEARCH fails."""
        mock_mail = MagicMock()
        mock_imap_class.return_value = mock_mail
        
        # Mock capability response with ESEARCH support
        mock_mail.capability.return_value = ('OK', [b'IMAP4REV1 UIDPLUS ESEARCH'])
        
        # Mock ESEARCH failure
        mock_mail._simple_command.side_effect = Exception("ESEARCH not supported")
        
        # Mock fallback to regular search
        message_ids = [str(i).encode() for i in range(1, 101)]
        mock_mail.search.return_value = ('OK', [b' '.join(message_ids)])
        
        criteria = SearchCriteria(max_results=25, start_from=25)
        results = await self.client._search_with_pagination(mock_mail, 'ALL', criteria)
        
        # Should fallback and return IDs 26-50
        self.assertEqual(len(results), 25)
        self.assertEqual(results[0], b'26')
        self.assertEqual(results[24], b'50')


class TestPaginationIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration tests for pagination through the full search_emails flow."""

    def setUp(self):
        """Set up test fixtures."""
        self.client = EmailClient()

    @patch('src.email_client.email_client.EmailClient.connect_imap')
    @patch('src.email_client.email_client.EmailClient._select_folder')
    @patch('src.email_client.email_client.EmailClient._build_search_criteria')
    async def test_search_emails_with_pagination(self, mock_build_criteria, mock_select_folder, mock_connect_imap):
        """Test complete search_emails flow with pagination."""
        # Mock IMAP connection
        mock_mail = MagicMock()
        mock_connect_imap.return_value = mock_mail
        
        # Mock search criteria building
        mock_build_criteria.return_value = 'ALL'
        
        # Mock capability and search responses
        mock_mail.capability.return_value = ('OK', [b'IMAP4REV1 UIDPLUS'])
        mock_mail.search.return_value = ('OK', [b'1 2 3 4 5 6 7 8 9 10'])
        
        # Mock fetch responses
        mock_fetch_responses = []
        for i in range(6, 9):  # For IDs 6, 7, 8 (start_from=5, max_results=3)
            mock_fetch_responses.append((
                f'{i} (RFC822 {{200}}'.encode(),
                f'From: sender{i}@example.com\r\nSubject: Email {i}\r\nDate: Mon, 01 Jan 2024 12:00:00 +0000\r\n\r\nContent {i}'.encode()
            ))
        mock_mail.fetch.return_value = ('OK', mock_fetch_responses)
        
        # Test pagination
        criteria = SearchCriteria(max_results=3, start_from=5)
        results = await self.client.search_emails(criteria)
        
        # Should return 3 results (IDs 6, 7, 8)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]['id'], '6')
        self.assertEqual(results[1]['id'], '7') 
        self.assertEqual(results[2]['id'], '8')


if __name__ == '__main__':
    # Run tests
    unittest.main()