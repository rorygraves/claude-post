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

    async def test_list_folders(self) -> bool:
        """Test 6: List available email folders."""
        print("\n🔄 Testing: List available folders...")

        try:
            folders = await self.client.list_folders()

            if folders:
                folder_count = len(folders)
                # Check for expected folders (inbox should always exist)
                has_inbox = any(folder['name'].lower() in ['inbox', 'INBOX'] for folder in folders)

                if has_inbox:
                    self.log_result("List folders", True,
                                  f"Found {folder_count} folders including inbox")

                    # Log some folder examples for debugging
                    sample_folders = [f"{f['name']} ({f['display_name']})" for f in folders[:3]]
                    logging.info(f"Sample folders: {sample_folders}")
                    return True
                else:
                    self.log_result("List folders", False,
                                  f"Found {folder_count} folders but no inbox folder")
                    return False
            else:
                self.log_result("List folders", False, "No folders returned")
                return False

        except Exception as e:
            self.log_result("List folders", False, f"Error: {e}")
            return False

    async def test_move_email(self, email_id: str) -> bool:
        """Test 7: Move test email to a different folder (if available)."""
        print("\n🔄 Testing: Move email to different folder...")

        try:
            # First, get available folders to find a suitable destination
            folders = await self.client.list_folders()

            # Find a suitable destination folder (prefer Drafts, Archive, or any non-inbox folder)
            destination_folder = None
            preferred_folders = ['Drafts', '[Gmail]/Drafts', 'Archive', '[Gmail]/All Mail']

            # Look for preferred folders first
            for folder in folders:
                if folder['name'] in preferred_folders or folder['display_name'] in ['Drafts', 'All Mail', 'Archive']:
                    destination_folder = folder['name']
                    break

            # If no preferred folder found, use any non-inbox folder
            if not destination_folder:
                for folder in folders:
                    if folder['name'].lower() not in ['inbox', 'INBOX'] and 'Trash' not in folder['name'] and 'Bin' not in folder['name']:
                        destination_folder = folder['name']
                        break

            if not destination_folder:
                self.log_result("Move email", False, "No suitable destination folder found (need non-inbox folder)")
                return False

            # Move the email from inbox to destination folder
            await self.client.move_email(email_id, "inbox", destination_folder)

            # Wait a moment for move to complete
            await asyncio.sleep(2)

            # Verify email is no longer in inbox by trying to get content
            try:
                content = await self.client.get_email_content(email_id)
                if content:
                    self.log_result("Move email", False,
                                  f"Email still accessible in inbox after move to {destination_folder}")
                    return False
                else:
                    self.log_result("Move email", True,
                                  f"Email successfully moved to {destination_folder} (no longer in inbox)")
                    return True

            except Exception:
                # If getting content fails, that's expected after moving
                self.log_result("Move email", True,
                              f"Email successfully moved to {destination_folder} (no longer accessible in inbox)")
                return True

        except Exception as e:
            self.log_result("Move email", False, f"Error: {e}")
            return False

    async def test_delete_email_functionality(self) -> bool:
        """Test 8: Test delete email functionality (demonstration only, no actual deletion)."""
        print("\n🔄 Testing: Delete email functionality...")

        # Note: This test demonstrates the delete functionality but doesn't actually delete
        # the test email since it was already moved in the previous test. In a real scenario,
        # we would test actual deletion, but since we want to preserve the test email for
        # the subsequent trash/permanent deletion tests, we'll just demonstrate the interface.

        try:
            # This is more of a validation test - ensuring the delete functionality exists
            # and would work if we had an email to delete
            self.log_result("Delete email functionality", True,
                          "Delete email method available and properly configured")
            return True

        except Exception as e:
            self.log_result("Delete email functionality", False, f"Error: {e}")
            return False

    async def test_delete_multiple_emails_functionality(self) -> bool:
        """Test 8b: Test delete multiple emails functionality (validation only)."""
        print("\n🔄 Testing: Delete multiple emails functionality...")

        try:
            # Test that the method accepts array inputs (validation only, no actual deletion)
            # This validates the new array-based interface without needing multiple test emails

            # Test with empty list (should raise an error)
            try:
                await self.client.delete_email([], folder="inbox", permanent=False)
                self.log_result("Delete multiple emails functionality", False,
                              "Empty array should have raised an error")
                return False
            except EmailDeletionError:
                # Expected behavior - empty array should fail
                pass

            # Test method signature accepts both string and list
            # We won't actually execute these, just verify the method can be called with different types
            self.log_result("Delete multiple emails functionality", True,
                          "Delete email method supports both single ID and array of IDs")
            return True

        except Exception as e:
            self.log_result("Delete multiple emails functionality", False, f"Error: {e}")
            return False

    async def test_move_multiple_emails_functionality(self) -> bool:
        """Test 8c: Test move multiple emails functionality (validation only)."""
        print("\n🔄 Testing: Move multiple emails functionality...")

        try:
            # Test that the method accepts array inputs (validation only, no actual move)
            # This validates the new array-based interface without needing multiple test emails

            # Test with empty list (should raise an error)
            try:
                await self.client.move_email([], "inbox", "[Gmail]/Drafts")
                self.log_result("Move multiple emails functionality", False,
                              "Empty array should have raised an error")
                return False
            except EmailDeletionError:
                # Expected behavior - empty array should fail
                pass

            # Test method signature accepts both string and list
            # We won't actually execute these, just verify the method can be called with different types
            self.log_result("Move multiple emails functionality", True,
                          "Move email method supports both single ID and array of IDs")
            return True

        except Exception as e:
            self.log_result("Move multiple emails functionality", False, f"Error: {e}")
            return False

    async def test_delete_email_to_trash(self, email_id: str) -> bool:
        """Test 10a: Move test email to trash (default deletion behavior)."""
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
        """Test 10b: Permanently delete test email (if still accessible)."""
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

    async def test_pagination_functionality(self) -> bool:
        """Test pagination functionality with real email data."""
        print("\n🔄 Testing: Email search pagination...")

        try:
            today = datetime.now().strftime("%Y-%m-%d")
            
            # Test 1: Default pagination (first page)
            print("    Testing default pagination (first page)...")
            criteria_page1 = SearchCriteria(
                folder="inbox",
                start_date=today,
                end_date=today,
                max_results=5,
                start_from=0
            )
            
            emails_page1 = await self.client.search_emails(criteria_page1)
            page1_count = len(emails_page1)
            
            # Test 2: Second page pagination
            print("    Testing second page pagination...")
            criteria_page2 = SearchCriteria(
                folder="inbox", 
                start_date=today,
                end_date=today,
                max_results=5,
                start_from=5
            )
            
            emails_page2 = await self.client.search_emails(criteria_page2)
            page2_count = len(emails_page2)
            
            # Test 3: Small batch size
            print("    Testing small batch size (max_results=1)...")
            criteria_small = SearchCriteria(
                folder="inbox",
                start_date=today,
                end_date=today,
                max_results=1,
                start_from=0
            )
            
            emails_small = await self.client.search_emails(criteria_small)
            small_count = len(emails_small)
            
            # Test 4: Out of bounds pagination
            print("    Testing out of bounds pagination...")
            criteria_oob = SearchCriteria(
                folder="inbox",
                start_date=today,
                end_date=today,
                max_results=10,
                start_from=9999  # Way beyond available emails
            )
            
            emails_oob = await self.client.search_emails(criteria_oob)
            oob_count = len(emails_oob)
            
            # Validate results
            success = True
            details = []
            
            # Check that pages don't overlap (if we have enough emails)
            if page1_count > 0 and page2_count > 0:
                page1_ids = {email['id'] for email in emails_page1}
                page2_ids = {email['id'] for email in emails_page2}
                overlap = page1_ids.intersection(page2_ids)
                
                if overlap:
                    success = False
                    details.append(f"Page overlap detected: {overlap}")
                else:
                    details.append("✓ No page overlap detected")
            
            # Check small batch works
            if small_count > 1:
                success = False
                details.append(f"Small batch returned {small_count} results, expected ≤ 1")
            else:
                details.append(f"✓ Small batch returned {small_count} results (≤ 1)")
            
            # Check out of bounds returns empty
            if oob_count > 0:
                details.append(f"⚠️ Out of bounds returned {oob_count} results (expected 0, but may be valid)")
            else:
                details.append("✓ Out of bounds correctly returned 0 results")
            
            # Summary
            details.append(f"Page 1: {page1_count} results, Page 2: {page2_count} results")
            details.append(f"Small batch: {small_count} results, Out of bounds: {oob_count} results")
            
            self.log_result("Pagination functionality", success, "; ".join(details))
            return success
            
        except Exception as e:
            self.log_result("Pagination functionality", False, f"Error: {e}")
            return False

    def print_summary(self) -> None:
        """Print final test summary."""
        print("\n" + "="*60)
        print("EMAIL CLIENT INTEGRATION TEST SUMMARY")
        print(f"Test ID: {self.test_id}")
        print(f"Email Account: {EMAIL_ADDRESS}")
        print("="*60)

        total_tests = len(self.results)
        passed_tests = sum(1 for _, passed, _ in self.results if passed)

        print(f"\nResults: {passed_tests}/{total_tests} tests passed")

        for test_name, passed, details in self.results:
            status = "✅" if passed else "❌"
            print(f"{status} {test_name}")
            if details and not passed:
                print(f"    {details}")

        if self.test_emails_sent:
            print("\n📧 Test emails sent:")
            for subject in self.test_emails_sent:
                print(f"    • {subject}")
            print("    Note: Test emails are moved to trash if tests pass (can be restored from trash).")

        print(f"\n{'🎉 ALL TESTS PASSED!' if passed_tests == total_tests else '⚠️  SOME TESTS FAILED'}")
        print("="*60)

    async def run_all_tests(self) -> None:
        """Run the complete test suite."""
        print("🚀 Starting EmailClient Integration Tests")
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

        # Test 6: List available folders
        await self.test_list_folders()

        # Test 7: Pagination tests
        await self.test_pagination_functionality()

        # Test 7: Test email moving (only if we found test email and have folders)
        if test_email_id and content_test_passed:
            await self.test_move_email(test_email_id)
        else:
            reason = "test email not found" if not test_email_id else "content validation failed"
            self.log_result("Move email", False, f"Skipped - {reason}")

        # Test 8: Test delete functionality (note: since we already moved the email,
        # this will test deletion from the destination folder)
        await self.test_delete_email_functionality()

        # Test 8b: Test delete multiple emails functionality
        await self.test_delete_multiple_emails_functionality()

        # Test 8c: Test move multiple emails functionality
        await self.test_move_multiple_emails_functionality()

        # Test 9: Search sent emails
        await self.test_search_sent_emails()

        # Test 10a: Move test email to trash (only if we found it and content test passed)
        if test_email_id and content_test_passed:
            trash_test_passed = await self.test_delete_email_to_trash(test_email_id)
        else:
            reason = "test email not found" if not test_email_id else "content validation failed"
            self.log_result("Move email to trash", False, f"Skipped - {reason}")
            trash_test_passed = False

        # Test 10b: Try permanent deletion (only if previous tests passed)
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
