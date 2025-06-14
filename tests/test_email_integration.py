"""Integration tests for EmailClient - Manual Testing Suite.

This test suite validates EmailClient functionality against a real email server.
These tests are designed to be run manually on-demand, not in CI/CD.

Requirements:
1. Valid .env file with email credentials
2. Email account that can send/receive emails to itself
3. Network access to IMAP/SMTP servers

Test Strategy:
- Send test emails to self with unique identifiers
- Search for and verify those test emails
- Read email content and validate it matches what was sent
- Count emails for specific dates
- Report results clearly with pass/fail status

Safety Features:
- All test emails include "[TEST-EMAIL]" prefix for easy identification
- Unique timestamp ID in each test email to avoid conflicts
- Clear output showing what tests are running and their results
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Optional, Tuple

from src.email_client.config import EMAIL_ADDRESS
from src.email_client.email_client import EmailClient, EmailDeletionError, EmailMessage, SearchCriteria


class EmailIntegrationTester:
    """Integration test runner for EmailClient functionality."""

    def __init__(self):
        """Initialize tester with EmailClient and test tracking."""
        self.client = EmailClient()
        self.test_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.test_emails_sent: List[str] = []  # Track test emails for cleanup info
        self.results: List[Tuple[str, bool, str]] = []  # (test_name, passed, details)

    def log_result(self, test_name: str, passed: bool, details: str = "") -> None:
        """Log test result for final report."""
        self.results.append((test_name, passed, details))
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {test_name}")
        if details:
            print(f"    {details}")

    async def test_send_email(self) -> bool:
        """Test 1: Send a test email to self."""
        print("\n🔄 Testing: Send email to self...")
        
        try:
            test_subject = f"[TEST-EMAIL] Integration Test {self.test_id}"
            test_content = f"""This is an automated test email sent by the EmailClient integration test suite.

Test ID: {self.test_id}
Timestamp: {datetime.now().isoformat()}
Purpose: Validate email sending functionality

=== TEST CONTENT VALIDATION MARKERS ===
UNIQUE_MARKER_START: test-{self.test_id}-content
Test Data: 
- Number sequence: 1, 2, 3, 4, 5
- Special characters: !@#$%^&*()
- Unicode: 🚀📧✅❌🔍
- Multi-line content with various formatting

Test validation points:
1. Email sending capability ✓
2. Content preservation ✓  
3. Subject line handling ✓
4. Self-delivery confirmation ✓
UNIQUE_MARKER_END: test-{self.test_id}-content
=== END TEST CONTENT ===

This email can be safely deleted after the integration test completes.
"""
            
            message = EmailMessage(
                to_addresses=[EMAIL_ADDRESS],
                subject=test_subject,
                content=test_content
            )
            
            await self.client.send_email(message)
            self.test_emails_sent.append(test_subject)
            self.log_result("Send email to self", True, f"Subject: {test_subject}")
            return True
            
        except Exception as e:
            self.log_result("Send email to self", False, f"Error: {e}")
            return False

    async def test_search_today_emails(self) -> bool:
        """Test 2: Search for emails received today."""
        print("\n🔄 Testing: Search emails for today...")
        
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            criteria = SearchCriteria(
                folder="inbox",
                start_date=today,
                end_date=today
            )
            
            emails = await self.client.search_emails(criteria)
            found_count = len(emails)
            
            self.log_result("Search today's emails", True, f"Found {found_count} emails for {today}")
            return True
            
        except Exception as e:
            self.log_result("Search today's emails", False, f"Error: {e}")
            return False

    async def test_search_test_email(self) -> Optional[str]:
        """Test 3: Search for our specific test email."""
        print("\n🔄 Testing: Search for test email with keyword...")
        
        try:
            # Wait a few seconds for email to be delivered
            print("    Waiting 5 seconds for email delivery...")
            await asyncio.sleep(5)
            
            criteria = SearchCriteria(
                folder="inbox",
                keyword=f"TEST-EMAIL] Integration Test {self.test_id}"
            )
            
            emails = await self.client.search_emails(criteria)
            
            if emails:
                test_email_id = emails[0]['id']
                self.log_result("Search test email by keyword", True, 
                              f"Found test email with ID: {test_email_id}")
                return test_email_id
            else:
                self.log_result("Search test email by keyword", False, 
                              "Test email not found (may need more time for delivery)")
                return None
                
        except Exception as e:
            self.log_result("Search test email by keyword", False, f"Error: {e}")
            return None

    async def test_get_email_content(self, email_id: str) -> bool:
        """Test 4: Get full content of a specific email with comprehensive validation."""
        print("\n🔄 Testing: Get email content with validation...")
        
        try:
            content = await self.client.get_email_content(email_id)
            
            if content:
                email_content = content.get('content', '')
                email_subject = content.get('subject', '')
                
                # Comprehensive content validation
                validation_checks = {
                    'test_id_in_content': self.test_id in email_content,
                    'test_id_in_subject': f"Integration Test {self.test_id}" in email_subject,
                    'unique_marker_start': f"UNIQUE_MARKER_START: test-{self.test_id}-content" in email_content,
                    'unique_marker_end': f"UNIQUE_MARKER_END: test-{self.test_id}-content" in email_content,
                    'number_sequence': "1, 2, 3, 4, 5" in email_content,
                    'special_characters': "!@#$%^&*()" in email_content,
                    'unicode_emojis': "🚀📧✅❌🔍" in email_content,
                    'validation_points': "Test validation points:" in email_content,
                    'from_field': content.get('from', '') != 'Unknown',
                    'to_field': content.get('to', '') != 'Unknown'
                }
                
                passed_checks = sum(validation_checks.values())
                total_checks = len(validation_checks)
                
                if passed_checks == total_checks:
                    self.log_result("Get email content", True, 
                                  f"All {total_checks} validation checks passed")
                    return True
                else:
                    failed_checks = [k for k, v in validation_checks.items() if not v]
                    self.log_result("Get email content", False, 
                                  f"Only {passed_checks}/{total_checks} checks passed. Failed: {failed_checks}")
                    return False
            else:
                self.log_result("Get email content", False, "No content returned")
                return False
                
        except Exception as e:
            self.log_result("Get email content", False, f"Error: {e}")
            return False

    async def test_count_daily_emails(self) -> bool:
        """Test 5: Count emails for today."""
        print("\n🔄 Testing: Count daily emails...")
        
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            counts = await self.client.count_daily_emails(today, today)
            
            if today in counts:
                count = counts[today]
                if count >= 0:  # -1 indicates timeout
                    self.log_result("Count daily emails", True, 
                                  f"Today ({today}): {count} emails")
                else:
                    self.log_result("Count daily emails", False, 
                                  f"Timeout counting emails for {today}")
                return count >= 0
            else:
                self.log_result("Count daily emails", False, 
                              f"No count returned for {today}")
                return False
                
        except Exception as e:
            self.log_result("Count daily emails", False, f"Error: {e}")
            return False

    async def test_search_sent_emails(self) -> bool:
        """Test 6: Search sent emails folder."""
        print("\n🔄 Testing: Search sent emails...")
        
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            criteria = SearchCriteria(
                folder="sent",
                start_date=today,
                end_date=today
            )
            
            emails = await self.client.search_emails(criteria)
            found_count = len(emails)
            
            # Should find at least our test email
            self.log_result("Search sent emails", True, 
                          f"Found {found_count} sent emails for {today}")
            return True
            
        except Exception as e:
            self.log_result("Search sent emails", False, f"Error: {e}")
            return False

    async def test_delete_email_to_trash(self, email_id: str) -> bool:
        """Test 7a: Move test email to trash (default deletion behavior)."""
        print("\n🔄 Testing: Move test email to trash...")
        
        try:
            # Delete the test email (default: move to trash)
            await self.client.delete_email(email_id, folder="inbox", permanent=False)
            
            # Wait a moment for operation to complete
            await asyncio.sleep(2)
            
            # Verify email is no longer in inbox by trying to get content
            try:
                content = await self.client.get_email_content(email_id)
                if content:
                    self.log_result("Move email to trash", False, 
                                  "Email still exists in inbox after moving to trash")
                    return False
                else:
                    self.log_result("Move email to trash", True, 
                                  "Email successfully moved to trash (no longer in inbox)")
                    return True
                    
            except Exception:
                # If getting content fails, that's expected after moving to trash
                self.log_result("Move email to trash", True, 
                              "Email successfully moved to trash (no longer accessible in inbox)")
                return True
                
        except EmailDeletionError as e:
            self.log_result("Move email to trash", False, f"Error moving to trash: {e}")
            return False
        except Exception as e:
            self.log_result("Move email to trash", False, f"Unexpected error: {e}")
            return False

    async def test_delete_email_permanent(self, email_id: str) -> bool:
        """Test 7b: Permanently delete test email (if still accessible)."""
        print("\n🔄 Testing: Permanently delete test email...")
        
        try:
            # Try permanent deletion (this might fail if email was already moved to trash)
            await self.client.delete_email(email_id, folder="inbox", permanent=True)
            
            # Wait a moment for operation to complete
            await asyncio.sleep(2)
            
            self.log_result("Permanent delete email", True, 
                          "Email permanently deleted (if it was still accessible)")
            return True
                
        except EmailDeletionError as e:
            # This might fail if email was already moved to trash, which is expected
            if "not found" in str(e).lower() or "no such message" in str(e).lower():
                self.log_result("Permanent delete email", True, 
                              "Email was already moved to trash (expected)")
                return True
            else:
                self.log_result("Permanent delete email", False, f"Unexpected deletion error: {e}")
                return False
        except Exception as e:
            self.log_result("Permanent delete email", False, f"Unexpected error: {e}")
            return False

    def print_summary(self) -> None:
        """Print final test summary."""
        print(f"\n" + "="*60)
        print(f"EMAIL CLIENT INTEGRATION TEST SUMMARY")
        print(f"Test ID: {self.test_id}")
        print(f"Email Account: {EMAIL_ADDRESS}")
        print(f"="*60)
        
        total_tests = len(self.results)
        passed_tests = sum(1 for _, passed, _ in self.results if passed)
        
        print(f"\nResults: {passed_tests}/{total_tests} tests passed")
        
        for test_name, passed, details in self.results:
            status = "✅" if passed else "❌"
            print(f"{status} {test_name}")
            if details and not passed:
                print(f"    {details}")
        
        if self.test_emails_sent:
            print(f"\n📧 Test emails sent:")
            for subject in self.test_emails_sent:
                print(f"    • {subject}")
            print(f"    Note: Test emails are moved to trash if tests pass (can be restored from trash).")
        
        print(f"\n{'🎉 ALL TESTS PASSED!' if passed_tests == total_tests else '⚠️  SOME TESTS FAILED'}")
        print(f"="*60)

    async def run_all_tests(self) -> None:
        """Run the complete test suite."""
        print(f"🚀 Starting EmailClient Integration Tests")
        print(f"Test ID: {self.test_id}")
        print(f"Email Account: {EMAIL_ADDRESS}")
        print(f"Time: {datetime.now().isoformat()}")
        
        # Test 1: Send email
        await self.test_send_email()
        
        # Test 2: Search today's emails
        await self.test_search_today_emails()
        
        # Test 3: Search for our test email specifically
        test_email_id = await self.test_search_test_email()
        
        # Test 4: Get content (only if we found the test email)
        content_test_passed = False
        if test_email_id:
            content_test_passed = await self.test_get_email_content(test_email_id)
        else:
            self.log_result("Get email content", False, "Skipped - test email not found")
        
        # Test 5: Count daily emails
        await self.test_count_daily_emails()
        
        # Test 6: Search sent emails
        await self.test_search_sent_emails()
        
        # Test 7a: Move test email to trash (only if we found it and content test passed)
        if test_email_id and content_test_passed:
            trash_test_passed = await self.test_delete_email_to_trash(test_email_id)
        else:
            reason = "test email not found" if not test_email_id else "content validation failed"
            self.log_result("Move email to trash", False, f"Skipped - {reason}")
            trash_test_passed = False
        
        # Test 7b: Try permanent deletion (only if previous tests passed)
        if test_email_id and content_test_passed and not trash_test_passed:
            # Only test permanent deletion if trash move failed (email still in inbox)
            await self.test_delete_email_permanent(test_email_id)
        else:
            if not test_email_id:
                reason = "test email not found"
            elif not content_test_passed:
                reason = "content validation failed"
            else:
                reason = "email already moved to trash"
            self.log_result("Permanent delete email", False, f"Skipped - {reason}")
        
        # Print final summary
        self.print_summary()


async def main():
    """Run the integration test suite."""
    # Setup logging to suppress debug noise during tests
    logging.getLogger().setLevel(logging.WARNING)
    
    print("📧 EmailClient Integration Test Suite")
    print("=====================================")
    print("This test suite validates EmailClient functionality against real email servers.")
    print("Make sure your .env file is configured with valid email credentials.\n")
    
    tester = EmailIntegrationTester()
    await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())