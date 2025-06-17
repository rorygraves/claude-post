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
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

# Constants from environment configuration
from .config import EMAIL_ADDRESS, EMAIL_PASSWORD, IMAP_SERVER, SMTP_PORT, SMTP_SERVER

# Operation Configuration Constants
SEARCH_TIMEOUT = 60  # Maximum time (seconds) for email search operations
MAX_EMAILS = 100     # Maximum number of emails to fetch in a single search

# Server capabilities we're interested in logging
INTERESTING_CAPABILITIES = [
    'IDLE', 'MOVE', 'QUOTA', 'NAMESPACE', 'UNSELECT',
    'UIDPLUS', 'CONDSTORE', 'QRESYNC', 'SORT', 'THREAD',
    'COMPRESS', 'ENABLE', 'LIST-EXTENDED', 'SPECIAL-USE'
]


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


class EmailDeletionError(Exception):
    """Raised when email deletion operations fail.

    This includes IMAP folder selection failures, message not found errors,
    or deletion permission issues.
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
        subject: Text to search for in email subject line (optional)
        sender: Text to search for in sender email address or name (optional)
        body: Text to search for in email body content (optional)
        max_results: Maximum number of emails to return (default: 100)
        start_from: Starting position for pagination (default: 0)
        direction: Sort direction for emails ('newest' or 'oldest', default: 'newest')
    """
    folder: Literal["inbox", "sent"] = "inbox"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    subject: Optional[str] = None
    sender: Optional[str] = None
    body: Optional[str] = None
    max_results: int = 100
    start_from: int = 0
    direction: Literal["newest", "oldest"] = "newest"

    def __post_init__(self) -> None:
        """Automatically validate criteria after object creation."""
        self.validate()

    def validate(self) -> None:
        """Validate date formats and pagination parameters.

        Raises:
            ValueError: If date strings don't match YYYY-MM-DD format or pagination params are invalid
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

        # Validate pagination parameters
        if self.max_results <= 0:
            raise ValueError(f"max_results must be positive, got: {self.max_results}")
        if self.max_results > 1000:
            raise ValueError(f"max_results cannot exceed 1000, got: {self.max_results}")
        if self.start_from < 0:
            raise ValueError(f"start_from must be non-negative, got: {self.start_from}")


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
            # Only call close() if we're in SELECTED state (folder is selected)
            if hasattr(mail, 'state') and mail.state == 'SELECTED':
                mail.close()
            mail.logout()
            logging.info("IMAP connection closed")
        except Exception as e:
            logging.warning(f"Error closing IMAP connection: {e!s}")

    async def query_server_capabilities(self) -> None:
        """Query and log IMAP server capabilities for debugging and feature discovery."""
        mail = None
        try:
            mail = await self.connect_imap()
            logging.info("=== IMAP Server Capabilities ===")

            await self._query_capabilities(mail)
            await self._query_namespace(mail)
            await self._query_server_id(mail)

            logging.info("=== End Server Capabilities ===")
        except Exception as e:
            logging.error(f"Error querying server capabilities: {e!s}", exc_info=True)
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def _query_capabilities(self, mail: imaplib.IMAP4_SSL) -> None:
        """Query and log server capabilities."""
        typ, capability_data = mail.capability()
        if typ != 'OK' or not capability_data:
            logging.warning(f"Failed to query capabilities: {typ}")
            return

        capabilities = capability_data[0].decode('utf-8')
        logging.info(f"Server capabilities: {capabilities}")

        cap_list = capabilities.split()
        found_caps = [cap for cap in INTERESTING_CAPABILITIES if cap in cap_list]

        if found_caps:
            logging.info(f"Notable capabilities: {', '.join(found_caps)}")
        else:
            logging.info("No notable extended capabilities found")

    async def _query_namespace(self, mail: imaplib.IMAP4_SSL) -> None:
        """Query namespace information if supported."""
        if not hasattr(mail, 'namespace'):
            return

        try:
            typ, namespace_data = mail.namespace()
            if typ == 'OK' and namespace_data:
                namespace_info = namespace_data[0].decode('utf-8') if namespace_data[0] else 'None'
                logging.info(f"Namespace info: {namespace_info}")
        except Exception as e:
            logging.debug(f"Namespace query failed (not supported): {e}")

    async def _query_server_id(self, mail: imaplib.IMAP4_SSL) -> None:
        """Query server ID if supported."""
        try:
            mail.send(b'ID NIL')
            typ, id_data = mail.response('ID')
            if typ != 'OK':
                return

            while True:
                response = mail.response('ID')
                if not response or len(response) != 2:
                    break

                typ, data = response
                if typ != 'OK' or not data or not data[0]:
                    break

                server_id = data[0].decode('utf-8')
                logging.info(f"Server ID: {server_id}")
                break
        except Exception as e:
            logging.debug(f"Server ID query failed (not supported): {e}")

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
            email_list = await self._execute_search(mail, search_criteria, criteria)
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

    async def delete_email(self, email_ids: Union[str, List[str]], folder: str = "inbox", permanent: bool = False) -> None:
        """Delete one or more emails by moving to trash or permanently deleting.

        Args:
            email_ids: The ID(s) of the email(s) to delete. Can be a single string or list of strings.
            folder: The folder containing the email(s) ('inbox' or 'sent')
            permanent: If True, permanently delete (mark + expunge).
                      If False (default), move to trash folder.

        Raises:
            EmailDeletionError: If the deletion operation fails
            EmailConnectionError: If IMAP connection fails
        """
        # Convert single email ID to list for uniform processing
        ids_to_process = [email_ids] if isinstance(email_ids, str) else email_ids

        if not ids_to_process:
            raise EmailDeletionError("No email IDs provided for deletion")

        # Process all emails in a single connection
        if permanent:
            await self._permanent_delete_emails(ids_to_process, folder)
        else:
            await self._move_emails_to_trash(ids_to_process, folder)

    async def _permanent_delete_emails(self, email_ids: List[str], folder: str) -> None:
        """Permanently delete multiple emails by marking as deleted and expunging."""
        mail = None
        try:
            mail = await self.connect_imap()
            await self._select_folder(mail, folder)

            logging.info(f"Permanently deleting {len(email_ids)} emails")
            loop = asyncio.get_event_loop()

            # Mark all emails as deleted in a single batch operation
            # IMAP accepts comma-separated message IDs for batch operations
            message_set = ','.join(email_ids)

            logging.debug(f"Batch marking {len(email_ids)} emails as deleted")
            def batch_mark_deleted() -> tuple[str, list[bytes]]:
                return mail.store(message_set, '+FLAGS', '\\Deleted')
            def handle_store_failure(result):
                if result[0] != 'OK':
                    raise EmailDeletionError(f"Failed to mark emails as deleted: {result}")
            
            store_result = await loop.run_in_executor(None, batch_mark_deleted)
            handle_store_failure(store_result)

            # Single expunge operation to remove all marked emails
            logging.info(f"Expunging {len(email_ids)} deleted emails")
            await loop.run_in_executor(None, mail.expunge)

            logging.info(f"Successfully permanently deleted {len(email_ids)} emails")

        except Exception as e:
            logging.error(f"Error in permanent delete: {e!s}", exc_info=True)
            raise EmailDeletionError(f"Failed to permanently delete emails {email_ids}: {e!s}") from e
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def _move_emails_to_trash(self, email_ids: List[str], folder: str) -> None:
        """Move multiple emails to the trash folder."""
        mail = None
        try:
            mail = await self.connect_imap()
            await self._select_folder(mail, folder)

            # Determine trash folder name
            trash_folder = await self._get_trash_folder_name(mail)

            logging.info(f"Moving {len(email_ids)} emails to trash folder: {trash_folder}")
            loop = asyncio.get_event_loop()

            # Process all emails in batch: copy to trash, mark as deleted
            # IMAP accepts comma-separated message IDs for batch operations
            message_set = ','.join(email_ids)

            logging.debug(f"Batch copying {len(email_ids)} emails to trash")
            # Copy all emails to trash folder in one operation
            def batch_copy_to_trash() -> tuple[str, list[bytes | None]]:
                return mail.copy(message_set, trash_folder)
            def handle_copy_failure(result):
                if result[0] != 'OK':
                    raise EmailDeletionError(f"Failed to copy emails to trash: {result}")
            
            copy_result = await loop.run_in_executor(None, batch_copy_to_trash)
            handle_copy_failure(copy_result)

            logging.debug(f"Batch marking {len(email_ids)} emails as deleted")
            # Mark all original emails as deleted in one operation
            def batch_mark_deleted() -> tuple[str, list[bytes]]:
                return mail.store(message_set, '+FLAGS', '\\Deleted')
            store_result = await loop.run_in_executor(None, batch_mark_deleted)
            if store_result[0] != 'OK':
                raise EmailDeletionError(f"Failed to mark emails as deleted: {store_result}")

            # Single expunge operation to remove all emails from current folder
            await loop.run_in_executor(None, mail.expunge)

            logging.info(f"Successfully moved {len(email_ids)} emails to trash")

        except Exception as e:
            logging.error(f"Error moving emails to trash: {e!s}", exc_info=True)
            raise EmailDeletionError(f"Failed to move emails {email_ids} to trash: {e!s}") from e
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def _select_folder(self, mail: imaplib.IMAP4_SSL, folder: str) -> None:
        """Select the appropriate email folder by name.

        Args:
            mail: Active IMAP connection
            folder: Folder name to select. Can be:
                   - 'inbox' or 'INBOX' (case insensitive)
                   - 'sent' (maps to Gmail sent folder)
                   - Any exact folder name from list_folders()

        Raises:
            EmailSearchError: If folder selection fails
        """
        logging.info(f"Selecting folder: {folder}")

        # Handle special folder mappings for backwards compatibility
        folder_to_select = folder
        if folder.lower() == "inbox":
            folder_to_select = "INBOX"
        elif folder.lower() == "sent":
            # Map 'sent' to Gmail's sent folder for backwards compatibility
            folder_to_select = "[Gmail]/Sent Mail"

        try:
            # Select the folder (add quotes if not already quoted)
            if not (folder_to_select.startswith('"') and folder_to_select.endswith('"')):
                quoted_folder = f'"{folder_to_select}"'
            else:
                quoted_folder = folder_to_select

            result = mail.select(quoted_folder)
            if result[0] != 'OK':
                raise EmailSearchError(f"Failed to select folder {quoted_folder}: {result[1]}")

            logging.info(f"Successfully selected folder {quoted_folder}, result: {result}")

        except Exception as e:
            logging.exception(f"Error selecting folder {folder}")
            raise EmailSearchError(f"Failed to select folder '{folder}': {e!s}") from e

    async def _get_trash_folder_name(self, mail: imaplib.IMAP4_SSL) -> str:
        """Determine the correct trash folder name for this email provider."""
        # Try Gmail's common trash folder names

        loop = asyncio.get_event_loop()

        # List all folders to find the correct trash folder
        _, folders = await loop.run_in_executor(None, mail.list)
        folder_names = []
        if folders:
            for folder in folders:
                if isinstance(folder, bytes) and (b'\\Trash' in folder or b'Bin' in folder):
                    try:
                        folder_name = folder.decode().split('"')[-2]
                        folder_names.append(folder_name)
                    except (UnicodeDecodeError, IndexError):
                        continue

        # Use the first trash folder found, or default to Gmail Bin
        if folder_names:
            trash_folder = f'"{folder_names[0]}"'
            logging.info(f"Found trash folder: {trash_folder}")
            return trash_folder

        # Default fallback
        default_trash = '"[Gmail]/Bin"'
        logging.info(f"Using default trash folder: {default_trash}")
        return default_trash

    async def list_folders(self) -> List[Dict[str, str]]:
        """List all available IMAP folders with their attributes.

        Returns:
            List of dictionaries containing folder information:
            [{'name': str, 'display_name': str, 'attributes': str}, ...]

        Raises:
            EmailConnectionError: If IMAP connection fails
            EmailSearchError: If folder listing fails
        """
        mail = None
        try:
            mail = await self.connect_imap()

            # List all folders
            logging.info("Listing all available IMAP folders")
            loop = asyncio.get_event_loop()
            _, folders = await loop.run_in_executor(None, mail.list)

            folder_list = []
            if folders:
                for folder_bytes in folders:
                    if not isinstance(folder_bytes, bytes):
                        continue
                    try:
                        folder_str = folder_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        continue
                    # Parse IMAP LIST response: (attributes) "delimiter" "folder_name"
                    parts = folder_str.split('"')
                    if len(parts) >= 3:
                        attributes = parts[0].strip('() ')
                        folder_name = parts[-2]  # The quoted folder name

                        # Create display name (remove Gmail prefixes for readability)
                        display_name = folder_name
                        if folder_name.startswith('[Gmail]/'):
                            display_name = folder_name.replace('[Gmail]/', '')

                        folder_info = {
                            'name': folder_name,
                            'display_name': display_name,
                            'attributes': attributes
                        }
                        folder_list.append(folder_info)
                        logging.debug(f"Found folder: {folder_info}")

            # Sort folders for consistent ordering (inbox first, then alphabetical)
            folder_list.sort(key=lambda x: (
                x['name'].lower() != 'inbox',  # inbox first
                x['display_name'].lower()
            ))

            logging.info(f"Successfully listed {len(folder_list)} folders")
        except Exception as e:
            logging.error(f"Error listing folders: {e!s}", exc_info=True)
            raise EmailSearchError(f"Failed to list folders: {e!s}") from e
        else:
            return folder_list
            logging.error(f"Error listing folders: {e!s}", exc_info=True)
            raise EmailSearchError(f"Failed to list folders: {e!s}") from e
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def move_email(self, email_ids: Union[str, List[str]], source_folder: str, destination_folder: str) -> None:
        """Move one or more emails from one folder to another.

        Args:
            email_ids: The ID(s) of the email(s) to move. Can be a single string or list of strings.
            source_folder: The folder containing the email(s) (e.g., 'inbox', 'INBOX')
            destination_folder: The destination folder (e.g., 'Archive', '[Gmail]/Important')

        Raises:
            EmailDeletionError: If the move operation fails (reusing existing exception)
            EmailConnectionError: If IMAP connection fails
        """
        # Convert single email ID to list for uniform processing
        ids_to_process = [email_ids] if isinstance(email_ids, str) else email_ids

        if not ids_to_process:
            raise EmailDeletionError("No email IDs provided for moving")

        await self._move_emails_batch(ids_to_process, source_folder, destination_folder)

    async def _move_emails_batch(self, email_ids: List[str], source_folder: str, destination_folder: str) -> None:
        """Move multiple emails in a single IMAP connection."""
        mail = None
        try:
            mail = await self.connect_imap()

            # Select the source folder
            await self._select_folder(mail, source_folder)

            # Validate destination folder by checking if it exists
            await self._validate_destination_folder(mail, destination_folder)

            # Ensure destination folder is properly quoted
            quoted_dest = destination_folder
            if not (destination_folder.startswith('"') and destination_folder.endswith('"')):
                quoted_dest = f'"{destination_folder}"'

            logging.info(f"Moving {len(email_ids)} emails from '{source_folder}' to '{destination_folder}'")
            loop = asyncio.get_event_loop()

            # Process all emails in batch: copy to destination, mark as deleted
            # IMAP accepts comma-separated message IDs for batch operations
            message_set = ','.join(email_ids)

            logging.debug(f"Batch copying {len(email_ids)} emails to destination")
            # Copy all emails to destination folder in one operation
            def batch_copy_to_dest() -> tuple[str, list[bytes | None]]:
                return mail.copy(message_set, quoted_dest)
            copy_result = await loop.run_in_executor(None, batch_copy_to_dest)
            if copy_result[0] != 'OK':
                raise EmailDeletionError(f"Failed to copy emails to destination: {copy_result}")

            logging.debug(f"Batch marking {len(email_ids)} emails as deleted")
            # Mark all original emails as deleted in one operation
            def batch_mark_deleted() -> tuple[str, list[bytes]]:
                return mail.store(message_set, '+FLAGS', '\\Deleted')
            store_result = await loop.run_in_executor(None, batch_mark_deleted)
            if store_result[0] != 'OK':
                raise EmailDeletionError(f"Failed to mark emails as deleted: {store_result}")

            # Single expunge operation to remove all moved emails from source folder
            await loop.run_in_executor(None, mail.expunge)

            logging.info(f"Successfully moved {len(email_ids)} emails from '{source_folder}' to '{destination_folder}'")

        except Exception as e:
            logging.error(f"Error moving emails: {e!s}", exc_info=True)
            raise EmailDeletionError(f"Failed to move emails {email_ids} from '{source_folder}' to '{destination_folder}': {e!s}") from e
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def _validate_destination_folder(self, mail: imaplib.IMAP4_SSL, folder_name: str) -> None:
        """Validate that a destination folder exists.

        Args:
            mail: Active IMAP connection
            folder_name: Folder name to validate

        Raises:
            EmailDeletionError: If folder doesn't exist
        """
        try:
            # List all folders to check if destination exists
            loop = asyncio.get_event_loop()
            _, folders = await loop.run_in_executor(None, mail.list)

            # Check if folder exists (handle both quoted and unquoted names)
            folder_exists = False
            if folders:
                for folder_bytes in folders:
                    if not isinstance(folder_bytes, bytes):
                        continue
                    try:
                        folder_str = folder_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        continue
                    # Extract folder name from IMAP LIST response
                    if '"' in folder_str:
                        listed_folder = folder_str.split('"')[-2]
                        if folder_name in (listed_folder, f'"{listed_folder}"'):
                            folder_exists = True
                            break

            if not folder_exists:
                raise EmailDeletionError(f"Destination folder '{folder_name}' does not exist")

            logging.info(f"Validated destination folder: {folder_name}")

        except EmailDeletionError:
            raise  # Re-raise our custom error
        except Exception as e:
            logging.exception(f"Error validating folder {folder_name}")
            raise EmailDeletionError(f"Failed to validate destination folder '{folder_name}': {e!s}") from e

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
            - No default dates applied - searches all emails if no dates provided
            - Single day searches use ON command for efficiency
            - Date ranges use SINCE + BEFORE with exclusive end date
            - Supports separate filtering by subject, sender, and body fields
            - Supports partial date ranges (start only, end only, or both)
            - Multiple search criteria are combined with AND logic
        """
        search_criteria_parts = []

        # Add date criteria if provided
        date_criteria = self._build_date_criteria(criteria)
        if date_criteria:
            search_criteria_parts.append(date_criteria)

        # Add field-specific searches
        field_criteria = self._build_field_criteria(criteria)
        search_criteria_parts.extend(field_criteria)

        # Combine all criteria parts
        return self._combine_criteria_parts(search_criteria_parts)

    def _build_date_criteria(self, criteria: SearchCriteria) -> str:
        """Build date-based search criteria from SearchCriteria."""
        if not (criteria.start_date or criteria.end_date):
            logging.info("No date criteria provided - searching all emails")
            return ""

        if criteria.start_date and criteria.end_date:
            return self._build_date_range_criteria(criteria.start_date, criteria.end_date)
        elif criteria.start_date:
            return self._build_start_date_criteria(criteria.start_date)
        elif criteria.end_date:  # criteria.end_date only
            return self._build_end_date_criteria(criteria.end_date)
        else:
            return ""

    def _build_date_range_criteria(self, start_date: str, end_date: str) -> str:
        """Build criteria for date range or single day."""
        start_date_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_date_dt = datetime.strptime(end_date, "%Y-%m-%d")
        logging.info(f"Date range specified - start: {start_date}, end: {end_date}")

        imap_start_date = start_date_dt.strftime("%d-%b-%Y")

        if start_date_dt.date() == end_date_dt.date():
            # Single day search - more efficient with ON command
            date_criteria = f'ON "{imap_start_date}"'
            logging.info(f"Single day search: {date_criteria}")
            return date_criteria
        else:
            # Date range search - BEFORE is exclusive, so add 1 day to end date
            imap_next_day_after_end = (end_date_dt + timedelta(days=1)).strftime("%d-%b-%Y")
            date_criteria = f'SINCE "{imap_start_date}" BEFORE "{imap_next_day_after_end}"'
            logging.info(f"Date range search: {date_criteria}")
            return date_criteria

    def _build_start_date_criteria(self, start_date: str) -> str:
        """Build criteria for start date only."""
        start_date_dt = datetime.strptime(start_date, "%Y-%m-%d")
        imap_start_date = start_date_dt.strftime("%d-%b-%Y")
        date_criteria = f'SINCE "{imap_start_date}"'
        logging.info(f"Start date only: {date_criteria}")
        return date_criteria

    def _build_end_date_criteria(self, end_date: str) -> str:
        """Build criteria for end date only."""
        end_date_dt = datetime.strptime(end_date, "%Y-%m-%d")
        # BEFORE is exclusive, so add 1 day to end date
        imap_next_day_after_end = (end_date_dt + timedelta(days=1)).strftime("%d-%b-%Y")
        date_criteria = f'BEFORE "{imap_next_day_after_end}"'
        logging.info(f"End date only: {date_criteria}")
        return date_criteria

    def _build_field_criteria(self, criteria: SearchCriteria) -> List[str]:
        """Build field-specific search criteria."""
        field_criteria = []

        if criteria.subject:
            subject_criteria = f'SUBJECT "{criteria.subject}"'
            field_criteria.append(subject_criteria)
            logging.info(f"Added subject search: {subject_criteria}")

        if criteria.sender:
            sender_criteria = f'FROM "{criteria.sender}"'
            field_criteria.append(sender_criteria)
            logging.info(f"Added sender search: {sender_criteria}")

        if criteria.body:
            body_criteria = f'BODY "{criteria.body}"'
            field_criteria.append(body_criteria)
            logging.info(f"Added body search: {body_criteria}")

        return field_criteria

    def _combine_criteria_parts(self, search_criteria_parts: List[str]) -> str:
        """Combine search criteria parts into final search string."""
        if not search_criteria_parts:
            search_criteria = 'ALL'
        elif len(search_criteria_parts) == 1:
            search_criteria = search_criteria_parts[0]
        else:
            # Multiple criteria - combine with AND logic
            search_criteria = '(' + ' '.join(search_criteria_parts) + ')'

        logging.info(f"Final search criteria: {search_criteria}")
        return search_criteria

    async def _search_with_pagination(self, mail: imaplib.IMAP4_SSL, search_criteria: str, criteria: SearchCriteria) -> List[bytes]:
        """Execute IMAP search with pagination support using ESEARCH if available.

        Args:
            mail: Active IMAP4_SSL connection
            search_criteria: IMAP search criteria string
            criteria: SearchCriteria object with pagination parameters

        Returns:
            List of message ID bytes, already paginated according to criteria
        """
        loop = asyncio.get_event_loop()

        # Check if server supports ESEARCH extension
        try:
            # Get server capabilities to check for ESEARCH support
            typ, capability_data = mail.capability()
            has_esearch = (typ == 'OK' and capability_data and
                          b'ESEARCH' in capability_data[0])

            if has_esearch and criteria.start_from > 0:
                # Try to use ESEARCH with PARTIAL for server-side pagination
                # Format: ESEARCH RETURN (PARTIAL start:count) search_criteria
                start_pos = criteria.start_from + 1  # IMAP uses 1-based indexing
                count = criteria.max_results
                esearch_query = f'RETURN (PARTIAL {start_pos}:{count}) {search_criteria}'

                try:
                    logging.debug(f"Attempting ESEARCH with query: {esearch_query}")
                    # Use extended search command if available
                    if hasattr(mail, '_simple_command'):
                        typ, data = await loop.run_in_executor(None,
                            lambda: mail._simple_command('SEARCH', esearch_query))
                        if typ == 'OK' and data:
                            # Parse ESEARCH response
                            response = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
                            if 'PARTIAL' in response:
                                # Extract message IDs from ESEARCH response
                                import re
                                match = re.search(r'PARTIAL \(\d+:\d+ ([\d\s]+)\)', response)
                                if match:
                                    message_ids = [id_str.encode() for id_str in match.group(1).split()]
                                    logging.info(f"ESEARCH returned {len(message_ids)} messages")
                                    return message_ids
                except Exception as e:
                    logging.debug(f"ESEARCH failed, falling back to regular search: {e}")

        except Exception as e:
            logging.debug(f"Capability check failed: {e}")

        # Fallback to regular SEARCH with client-side pagination
        logging.debug("Using regular SEARCH with client-side pagination")
        _, messages = await loop.run_in_executor(None, lambda: mail.search(None, search_criteria))

        if not messages[0]:
            return []

        all_message_ids = messages[0].split()
        logging.info(f"Found {len(all_message_ids)} total messages, applying pagination")

        # Apply sorting based on direction (newest=reverse, oldest=normal)
        if criteria.direction == "newest":
            # Reverse the message IDs to get newest first (highest IDs first)
            all_message_ids = list(reversed(all_message_ids))
            logging.debug("Applied newest-first sorting (reversed message IDs)")
        else:
            # Keep original order for oldest first (lowest IDs first)
            logging.debug("Applied oldest-first sorting (original message ID order)")

        # Apply client-side pagination
        start_idx = criteria.start_from
        end_idx = start_idx + criteria.max_results
        paginated_ids: List[bytes] = all_message_ids[start_idx:end_idx]

        logging.info(f"Returning {len(paginated_ids)} messages after pagination (direction: {criteria.direction})")
        return paginated_ids

    async def _execute_search(self, mail: imaplib.IMAP4_SSL, search_criteria: str, criteria: SearchCriteria) -> List[Dict[str, str]]:
        """Execute IMAP search with pagination support and return formatted email summaries.

        Performs an IMAP SEARCH command with the given criteria, supports pagination
        using either ESEARCH (if available) or regular SEARCH with client-side pagination.
        Fetches email headers using efficient batch fetching.

        Args:
            mail: Active IMAP4_SSL connection with a folder already selected
            search_criteria: IMAP search criteria string (e.g., 'SINCE "01-Jan-2024"')
            criteria: SearchCriteria object containing pagination parameters

        Returns:
            List of email summary dictionaries, each containing:
            - 'id': Email message ID (string) - used for fetching full content later
            - 'from': Sender email address and name (string)
            - 'date': Email date header (string) - as received from server
            - 'subject': Email subject line (string) - "No Subject" if missing

            Returns empty list if no emails match the search criteria.
            Limited by criteria.max_results for performance.

        Performance:
            Uses batch FETCH with comma-separated message IDs for efficiency,
            reducing network round-trips compared to individual fetch operations.
            Supports ESEARCH for server-side pagination when available.
        """
        loop = asyncio.get_event_loop()

        # Check if server supports ESEARCH for efficient pagination
        message_ids = await self._search_with_pagination(mail, search_criteria, criteria)

        if not message_ids:
            logging.info("No messages found matching criteria")
            return []

        logging.info(f"Found {len(message_ids)} messages for pagination range {criteria.start_from}-{criteria.start_from + criteria.max_results}")

        if not message_ids:
            return []

        # Create comma-separated list of message IDs for batch fetch
        message_set = b','.join(message_ids).decode()
        logging.debug(f"Batch fetching {len(message_ids)} emails with message set: {message_set}")

        # Fetch all emails in a single IMAP command for efficiency
        _, msg_data_list = await loop.run_in_executor(None, lambda: mail.fetch(message_set, "(RFC822)"))

        # Process the batch response into email summaries
        email_list = []
        if msg_data_list:
            for msg_data in msg_data_list:
                if msg_data and len(msg_data) >= 2:  # Ensure we have both ID and content
                    try:
                        email_list.append(self._format_email_summary((msg_data,)))
                    except Exception as e:
                        logging.warning(f"Failed to format email summary: {e!s}")
                        continue

        logging.info(f"Successfully processed {len(email_list)} emails from batch fetch")
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
