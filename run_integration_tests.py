#!/usr/bin/env python3
"""Integration test runner for EmailClient.

This script runs the EmailClient integration tests which validate functionality
against a real email server. These tests should be run manually, not in CI.

Requirements:
- Valid .env file with email credentials
- Network access to email servers
- Email account that can send emails to itself

Usage:
    python run_integration_tests.py
    
The tests will:
1. Send a test email to the configured email address
2. Search for emails using various criteria
3. Retrieve and validate email content (comprehensive validation)
4. Count daily emails
5. List available email folders
6. Test moving emails between folders
7. Test sent folder functionality
8. Move test email to trash (safe cleanup)
9. Test permanent deletion (if needed)

All test emails are marked with "[TEST-EMAIL]" and moved to trash if tests pass.
"""

import asyncio
import sys
from pathlib import Path

# Add src to path so we can import email_client modules
sys.path.insert(0, str(Path(__file__).parent / "src"))

from tests.test_email_integration import main as run_tests


def check_environment():
    """Check if environment is properly configured for testing."""
    print("🔍 Checking environment configuration...")

    # Check if .env file exists
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        print("❌ Error: .env file not found!")
        print("Please create a .env file with your email configuration:")
        print("EMAIL_ADDRESS=your.email@gmail.com")
        print("EMAIL_PASSWORD=your-app-specific-password")
        print("IMAP_SERVER=imap.gmail.com")
        print("SMTP_SERVER=smtp.gmail.com")
        print("SMTP_PORT=587")
        return False

    # Try to import config to validate it loads properly
    try:
        from src.email_client.config import EMAIL_ADDRESS, EMAIL_PASSWORD

        if EMAIL_ADDRESS == "your.email@gmail.com" or EMAIL_PASSWORD == "your-app-specific-password":
            print("❌ Error: Default placeholder values detected in .env file!")
            print("Please update .env with your actual email credentials.")
            return False

        print("✅ Configuration loaded successfully")
        print(f"   Email: {EMAIL_ADDRESS}")
        print(f"   .env file: {env_file}")
        return True

    except ImportError as e:
        print(f"❌ Error importing email configuration: {e}")
        return False


def print_banner():
    """Print welcome banner."""
    print("=" * 70)
    print("📧 EmailClient Integration Test Suite")
    print("=" * 70)
    print()
    print("This test suite validates EmailClient functionality by:")
    print("• Sending test emails to your configured email address")
    print("• Searching for emails using various criteria")
    print("• Retrieving and validating email content (comprehensive validation)")
    print("• Testing daily email counting")
    print("• Listing available email folders")
    print("• Testing email moving between folders")
    print("• Validating sent folder functionality")
    print("• Moving test emails to trash (safe cleanup)")
    print()
    print("⚠️  Important Notes:")
    print("• Test emails will be sent to your configured email address")
    print("• All test emails are marked with '[TEST-EMAIL]' prefix")
    print("• Test emails are moved to trash if tests pass (can be restored)")
    print("• Tests require network access to your email servers")
    print()


def print_footer():
    """Print completion footer."""
    print()
    print("=" * 70)
    print("📧 Integration tests completed!")
    print("=" * 70)
    print()
    print("Next steps:")
    print("• Review any failed tests above")
    print("• Check email_client.log for detailed logs")
    print("• Test emails are moved to trash if tests pass (can be restored)")
    print("• If tests failed, you may need to manually clean up test emails")
    print()


async def main():
    """Main entry point for test runner."""
    print_banner()

    # Check environment configuration
    if not check_environment():
        print("\n❌ Environment check failed. Please fix the issues above and try again.")
        sys.exit(1)

    print("\n🚀 Starting integration tests...\n")

    try:
        # Run the integration tests
        await run_tests()

    except KeyboardInterrupt:
        print("\n\n⏹️  Tests interrupted by user")
        sys.exit(1)

    except Exception as e:
        print(f"\n\n❌ Unexpected error running tests: {e}")
        print("Check email_client.log for detailed error information")
        sys.exit(1)

    finally:
        print_footer()


if __name__ == "__main__":
    asyncio.run(main())
