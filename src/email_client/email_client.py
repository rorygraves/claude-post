"""Email client implementation for IMAP and SMTP operations.

This module contains the EmailClient class which handles all email-related
operations including reading, searching, and sending emails.
"""

import asyncio
import email
import imaplib
import logging
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Literal, Optional, Tuple

# Constants from environment configuration
from .config import EMAIL_ADDRESS, EMAIL_PASSWORD, IMAP_SERVER, SMTP_PORT, SMTP_SERVER

# Operation Configuration Constants
SEARCH_TIMEOUT = 60  # Maximum time (seconds) for email search operations
MAX_EMAILS = 100     # Maximum number of emails to fetch in a single search


# Custom Exceptions for Email Operations
class EmailConnectionError(Exception):
    """Raised when IMAP/SMTP connection or authentication fails.

    This includes network connectivity issues, invalid credentials,
    server unavailability, or SSL/TLS handshake failures.
    """

    pass


class EmailSearchError(Exception):
    """Raised when email search or retrieval operations fail.

    This includes IMAP search syntax errors, folder selection failures,
    message fetching errors, or email parsing issues.
    """

    pass


class EmailSendError(Exception):
    """Raised when email sending operations fail.

    This includes SMTP connection issues, recipient validation errors,
    message formatting problems, or delivery failures.
    """

    pass


# Data Classes for Input Validation and Type Safety
@dataclass
class SearchCriteria:
    """Encapsulates and validates email search parameters.

    Provides type-safe search criteria with automatic validation of date formats
    and folder names. Used to ensure consistent search parameters across the
    email search functionality.

    Attributes:
        folder: Email folder to search ('inbox' or 'sent')
        start_date: Search start date in YYYY-MM-DD format (optional)
        end_date: Search end date in YYYY-MM-DD format (optional)
        keyword: Text to search for in subject/body (optional)
    """
    folder: Literal["inbox", "sent"] = "inbox"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    keyword: Optional[str] = None

    def __post_init__(self) -> None:
        """Automatically validate criteria after object creation."""
        self.validate()

    def validate(self) -> None:
        """Validate date formats and ensure they follow YYYY-MM-DD pattern.

        Raises:
            ValueError: If date strings don't match YYYY-MM-DD format
        """
        # Validate start_date format if provided
        if self.start_date:
            try:
                datetime.strptime(self.start_date, "%Y-%m-%d")
            except ValueError as e:
                raise ValueError(f"Invalid start_date format: {self.start_date}. Expected YYYY-MM-DD") from e

        # Validate end_date format if provided
        if self.end_date:
            try:
                datetime.strptime(self.end_date, "%Y-%m-%d")
            except ValueError as e:
                raise ValueError(f"Invalid end_date format: {self.end_date}. Expected YYYY-MM-DD") from e


@dataclass
class EmailMessage:
    """Encapsulates and validates email message data before sending.

    Provides type-safe email composition with automatic validation of required
    fields. Ensures all necessary components are present before attempting to
    send the email via SMTP.

    Attributes:
        to_addresses: List of recipient email addresses (required)
        subject: Email subject line (required, cannot be empty)
        content: Email body content (required, cannot be empty)
        cc_addresses: List of CC recipient addresses (optional)
    """
    to_addresses: List[str]
    subject: str
    content: str
    cc_addresses: Optional[List[str]] = None

    def __post_init__(self) -> None:
        """Automatically validate message data after object creation."""
        self.validate()

    def validate(self) -> None:
        """Validate email message components for completeness.

        Ensures all required fields are present and non-empty.

        Raises:
            ValueError: If required fields are missing or empty
        """
        # Ensure at least one recipient is specified
        if not self.to_addresses:
            raise ValueError("At least one recipient email address is required")

        # Ensure subject is not empty or just whitespace
        if not self.subject.strip():
            raise ValueError("Email subject cannot be empty")

        # Ensure content is not empty or just whitespace
        if not self.content.strip():
            raise ValueError("Email content cannot be empty")


class EmailClient:
    """Centralized email operations manager for IMAP and SMTP functionality.

    This class encapsulates all email-related operations including:
    - IMAP connection management for reading emails
    - SMTP connection management for sending emails
    - Email searching with various criteria
    - Email content retrieval and parsing
    - Daily email counting and statistics

    The client handles connection lifecycle, error handling, and provides
    a consistent interface for all email operations used by the MCP server.

    Attributes:
        email_address: Email account address from environment config
        email_password: Email account password/app-password from environment
        imap_server: IMAP server hostname for reading emails
        smtp_server: SMTP server hostname for sending emails
        smtp_port: SMTP server port number
    """

    def __init__(self) -> None:
        """Initialize EmailClient with configuration from environment variables.

        Loads email server settings from the global configuration constants
        that were extracted from environment variables at startup.
        """
        self.email_address = EMAIL_ADDRESS
        self.email_password = EMAIL_PASSWORD
        self.imap_server = IMAP_SERVER
        self.smtp_server = SMTP_SERVER
        self.smtp_port = SMTP_PORT

    async def connect_imap(self) -> imaplib.IMAP4_SSL:
        """Establish an authenticated SSL IMAP connection.

        Creates a secure connection to the IMAP server and authenticates
        using the configured email credentials. The connection is ready
        for folder selection and email operations.

        Returns:
            Authenticated IMAP4_SSL connection object ready for use

        Raises:
            EmailConnectionError: If connection fails, authentication fails,
                                or SSL handshake encounters issues
        """
        try:
            # Establish SSL connection to IMAP server
            logging.info(f"Connecting to IMAP server: {self.imap_server}")
            mail = imaplib.IMAP4_SSL(self.imap_server)
            logging.info("IMAP SSL connection established")

            # Authenticate with email credentials
            mail.login(self.email_address, self.email_password)
            logging.info("IMAP login successful")
        except Exception as e:
            logging.exception("IMAP connection/login failed")
            raise EmailConnectionError(f"Failed to connect to IMAP server: {e!s}") from e
        else:
            return mail

    async def close_imap_connection(self, mail: imaplib.IMAP4_SSL) -> None:
        """Safely close IMAP connection."""
        try:
            mail.close()
            mail.logout()
            logging.info("IMAP connection closed")
        except Exception as e:
            logging.warning(f"Error closing IMAP connection: {e!s}")

    async def search_emails(self, criteria: SearchCriteria) -> List[Dict[str, str]]:
        """Search for emails matching the specified criteria.

        Connects to IMAP, selects the appropriate folder, and searches for emails
        based on the provided criteria (date range, keywords, folder). Returns
        a list of email summaries with basic metadata.

        Args:
            criteria: SearchCriteria object containing search parameters

        Returns:
            List of dictionaries containing email metadata:
            [{'id': str, 'from': str, 'date': str, 'subject': str}, ...]

        Raises:
            EmailSearchError: If folder selection, search execution, or
                            email parsing fails
            EmailConnectionError: If IMAP connection fails
        """
        mail = None
        try:
            # Establish IMAP connection
            mail = await self.connect_imap()

            # Select the appropriate email folder
            logging.info(f"Selecting folder: {criteria.folder}")
            if criteria.folder == "sent":
                # Gmail uses a specific folder name for sent mail
                result = mail.select('"[Gmail]/Sent Mail"')
                logging.info(f"Selected sent folder, result: {result}")
            else:
                # Default to inbox for all other cases
                result = mail.select("inbox")
                logging.info(f"Selected inbox, result: {result}")

            # Convert search criteria to IMAP search syntax
            search_criteria = await self._build_search_criteria(criteria)
            logging.info(f"Final search criteria: {search_criteria}")

            # Execute the search and fetch email summaries
            email_list = await self._execute_search(mail, search_criteria)
            logging.info(f"Successfully fetched {len(email_list)} emails")

        except Exception as e:
            logging.error(f"Error in search_emails: {e!s}", exc_info=True)
            raise EmailSearchError(f"Email search failed: {e!s}") from e
        else:
            return email_list
        finally:
            # Always clean up the IMAP connection
            if mail:
                await self.close_imap_connection(mail)

    async def get_email_content(self, email_id: str) -> Optional[Dict[str, str]]:
        """Get full content of a specific email."""
        mail = None
        try:
            mail = await self.connect_imap()
            mail.select("inbox")

            loop = asyncio.get_event_loop()
            _, msg_data = await loop.run_in_executor(None, lambda: mail.fetch(email_id, "(RFC822)"))

            if msg_data and msg_data[0]:
                return self._format_email_content((msg_data[0],))

        except Exception as e:
            logging.error(f"Error fetching email content: {e!s}", exc_info=True)
            raise EmailSearchError(f"Failed to get email content: {e!s}") from e
        else:
            self._raise_no_email_data_error()
            return None  # This line will never be reached but satisfies mypy
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def send_email(self, message: EmailMessage) -> None:
        """Send email with specified parameters."""
        try:
            # Create message
            msg = MIMEMultipart()
            msg["From"] = self.email_address
            msg["To"] = ", ".join(message.to_addresses)
            if message.cc_addresses:
                msg["Cc"] = ", ".join(message.cc_addresses)
            msg["Subject"] = message.subject

            # Add body
            msg.attach(MIMEText(message.content, "plain", "utf-8"))

            # Send email
            await self._send_via_smtp(msg, message.to_addresses, message.cc_addresses)
            logging.info("Email sent successfully")

        except Exception as e:
            logging.error(f"Error in send_email: {e!s}", exc_info=True)
            raise EmailSendError(f"Failed to send email: {e!s}") from e

    async def count_daily_emails(self, start_date: str, end_date: str) -> Dict[str, int]:
        """Count emails received for each day in the specified date range.

        Iterates through each day between start_date and end_date (inclusive)
        and counts the number of emails received on that specific day.

        Args:
            start_date: Start date in YYYY-MM-DD format (inclusive)
            end_date: End date in YYYY-MM-DD format (inclusive)

        Returns:
            Dictionary mapping date strings (YYYY-MM-DD) to email counts.
            Returns -1 for dates where the count operation timed out.

        Raises:
            EmailSearchError: If IMAP connection fails or search operation fails
            ValueError: If date format is invalid
        """
        mail = None
        try:
            # Connect to IMAP server and select inbox
            mail = await self.connect_imap()
            mail.select("inbox")

            # Parse input date strings into datetime objects for iteration
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")  # Start of date range
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")      # End of date range (inclusive)

            # Dictionary to store daily email counts: {"YYYY-MM-DD": count}
            daily_counts = {}
            current_date = start_dt  # Iterator starting from start date

            # Iterate through each day in the range (inclusive of end date)
            while current_date <= end_dt:
                # Convert to IMAP date format: "DD-MMM-YYYY" (e.g., "15-Dec-2024")
                date_str = current_date.strftime("%d-%b-%Y")
                # Build IMAP search criteria for emails received on this specific date
                search_criteria = f'(ON "{date_str}")'

                try:
                    # Count emails for this date with timeout protection
                    async with asyncio.timeout(SEARCH_TIMEOUT):
                        count = await self._count_emails(mail, search_criteria)
                        # Store result using ISO format for consistency
                        daily_counts[current_date.strftime("%Y-%m-%d")] = count
                except asyncio.TimeoutError:
                    # Mark timeout with -1 to distinguish from zero emails
                    daily_counts[current_date.strftime("%Y-%m-%d")] = -1

                # Move to next day
                current_date += timedelta(days=1)

        except Exception as e:
            logging.error(f"Error in count_daily_emails: {e!s}", exc_info=True)
            raise EmailSearchError(f"Failed to count emails: {e!s}") from e
        else:
            return daily_counts
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def _build_search_criteria(self, criteria: SearchCriteria) -> str:
        """Convert SearchCriteria object into IMAP search syntax.

        Transforms user-friendly search parameters into the specific syntax
        required by IMAP SEARCH command. Handles date range logic, keyword
        searching, and applies sensible defaults.

        Args:
            criteria: SearchCriteria containing user search parameters

        Returns:
            IMAP search criteria string ready for mail.search() command

        Note:
            - Default date range is last 7 days if no dates provided
            - Single day searches use ON command for efficiency
            - Date ranges use SINCE + BEFORE with exclusive end date
            - Keywords search both subject and body fields
        """
        # Apply default date range if not specified (last 7 days)
        if not criteria.start_date:
            start_date_dt = datetime.now() - timedelta(days=7)
            logging.info(f"No start_date provided, using default: {start_date_dt.strftime('%Y-%m-%d')}")
        else:
            start_date_dt = datetime.strptime(criteria.start_date, "%Y-%m-%d")
            logging.info(f"Parsed start_date: {criteria.start_date}")

        if not criteria.end_date:
            end_date_dt = datetime.now()
            logging.info(f"No end_date provided, using today: {end_date_dt.strftime('%Y-%m-%d')}")
        else:
            end_date_dt = datetime.strptime(criteria.end_date, "%Y-%m-%d")
            logging.info(f"Parsed end_date: {criteria.end_date}")

        # Convert to IMAP date format: "DD-MMM-YYYY" (e.g., "15-Dec-2024")
        imap_start_date = start_date_dt.strftime("%d-%b-%Y")
        imap_end_date = end_date_dt.strftime("%d-%b-%Y")
        logging.info(f"IMAP formatted dates - start: {imap_start_date}, end: {imap_end_date}")

        # Build date-based search criteria
        if start_date_dt.date() == end_date_dt.date():
            # Single day search - more efficient with ON command
            search_criteria = f'ON "{imap_start_date}"'
            logging.info(f"Single day search: {search_criteria}")
        else:
            # Date range search - BEFORE is exclusive, so add 1 day to end date
            imap_next_day_after_end = (end_date_dt + timedelta(days=1)).strftime("%d-%b-%Y")
            search_criteria = f'SINCE "{imap_start_date}" BEFORE "{imap_next_day_after_end}"'
            logging.info(f"Date range search: {search_criteria}")

        # Add keyword search if specified (searches both subject and body)
        if criteria.keyword:
            keyword_criteria = f'(OR SUBJECT "{criteria.keyword}" BODY "{criteria.keyword}")'
            search_criteria = f"({keyword_criteria} {search_criteria})"
            logging.info(f"Added keyword search, final criteria: {search_criteria}")

        return search_criteria

    async def _execute_search(self, mail: imaplib.IMAP4_SSL, search_criteria: str) -> List[Dict[str, str]]:
        """Execute IMAP search and return formatted results."""
        loop = asyncio.get_event_loop()

        logging.debug(f"Executing IMAP search with criteria: {search_criteria}")
        _, messages = await loop.run_in_executor(None, lambda: mail.search(None, search_criteria))
        logging.debug(f"Search result: {messages}")

        if not messages[0]:
            logging.info("No messages found matching criteria")
            return []

        message_ids = messages[0].split()
        logging.info(f"Found {len(message_ids)} messages, fetching up to {MAX_EMAILS}")

        email_list = []
        for i, num in enumerate(message_ids[:MAX_EMAILS]):
            logging.debug(f"Fetching email {i+1}/{min(len(message_ids), MAX_EMAILS)}, ID: {num}")

            def fetch_email(email_id: bytes = num) -> Any:
                return mail.fetch(email_id.decode(), "(RFC822)")

            _, msg_data = await loop.run_in_executor(None, fetch_email)
            if msg_data and msg_data[0]:
                email_list.append(self._format_email_summary((msg_data[0],)))

        return email_list

    async def _send_via_smtp(
        self, msg: MIMEMultipart, to_addresses: List[str], cc_addresses: Optional[List[str]]
    ) -> None:
        """Send email via SMTP."""

        def send_sync() -> None:
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as smtp_server:
                smtp_server.set_debuglevel(1)
                logging.debug(f"Connecting to {self.smtp_server}:{self.smtp_port}")

                smtp_server.starttls()
                logging.debug("Starting TLS")

                smtp_server.login(self.email_address, self.email_password)
                logging.debug(f"Logging in as {self.email_address}")

                all_recipients = to_addresses + (cc_addresses or [])
                logging.debug(f"Sending email to: {all_recipients}")
                result = smtp_server.send_message(msg, self.email_address, all_recipients)

                if result:
                    raise EmailSendError(f"Failed to send to some recipients: {result}")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, send_sync)

    async def _count_emails(self, mail: imaplib.IMAP4_SSL, search_criteria: str) -> int:
        """Count emails matching search criteria."""
        loop = asyncio.get_event_loop()
        _, messages = await loop.run_in_executor(None, lambda: mail.search(None, search_criteria))
        return len(messages[0].split()) if messages[0] else 0

    def _format_email_summary(self, msg_data: Tuple[Any, ...]) -> Dict[str, str]:
        """Format an email message into a summary dict with basic information."""
        email_body = email.message_from_bytes(msg_data[0][1])

        return {
            "id": msg_data[0][0].split()[0].decode(),
            "from": email_body.get("From", "Unknown"),
            "date": email_body.get("Date", "Unknown"),
            "subject": email_body.get("Subject", "No Subject"),
        }

    def _format_email_content(self, msg_data: Tuple[Any, ...]) -> Dict[str, str]:
        """Format an email message into a dict with full content."""
        email_body = email.message_from_bytes(msg_data[0][1])

        # Extract body content
        body = ""
        if email_body.is_multipart():
            for part in email_body.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        body = payload.decode()
                    break
                elif part.get_content_type() == "text/html":
                    if not body:
                        payload = part.get_payload(decode=True)
                        if isinstance(payload, bytes):
                            body = payload.decode()
        else:
            payload = email_body.get_payload(decode=True)
            if isinstance(payload, bytes):
                body = payload.decode()

        return {
            "from": email_body.get("From", "Unknown"),
            "to": email_body.get("To", "Unknown"),
            "date": email_body.get("Date", "Unknown"),
            "subject": email_body.get("Subject", "No Subject"),
            "content": body,
        }

    def _raise_no_email_data_error(self) -> None:
        """Raise error for no email data."""
        raise EmailSearchError("No email data returned")
