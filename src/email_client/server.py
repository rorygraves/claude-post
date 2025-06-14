import asyncio
import email
import imaplib
import logging
import os
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Literal, Optional, Tuple, Union, cast

import mcp.server.stdio
from dotenv import load_dotenv
from mcp import types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
    filename="email_client.log",
)

# Load environment variables from .env file
load_dotenv()

# Email configuration - extract and type cast values once
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "your.email@gmail.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "your-app-specific-password")
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

logging.info("=== Email Client Server Starting ===")
logging.info(f"Email configured: {EMAIL_ADDRESS}")
logging.info(f"IMAP server: {IMAP_SERVER}")
logging.info(f"SMTP server: {SMTP_SERVER}:{SMTP_PORT}")
logging.info(f"Password configured: {'Yes' if EMAIL_PASSWORD != 'your-app-specific-password' else 'No'}")

# Constants
SEARCH_TIMEOUT = 60  # seconds
MAX_EMAILS = 100


# Custom Exceptions
class EmailConnectionError(Exception):
    """Raised when email connection fails."""

    pass


class EmailSearchError(Exception):
    """Raised when email search fails."""

    pass


class EmailSendError(Exception):
    """Raised when email sending fails."""

    pass


# Data Classes for Validation
@dataclass
class SearchCriteria:
    folder: Literal["inbox", "sent"] = "inbox"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    keyword: Optional[str] = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate search criteria."""
        if self.start_date:
            try:
                datetime.strptime(self.start_date, "%Y-%m-%d")
            except ValueError as e:
                raise ValueError(f"Invalid start_date format: {self.start_date}. Expected YYYY-MM-DD") from e

        if self.end_date:
            try:
                datetime.strptime(self.end_date, "%Y-%m-%d")
            except ValueError as e:
                raise ValueError(f"Invalid end_date format: {self.end_date}. Expected YYYY-MM-DD") from e


@dataclass
class EmailMessage:
    to_addresses: List[str]
    subject: str
    content: str
    cc_addresses: Optional[List[str]] = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate email message."""
        if not self.to_addresses:
            raise ValueError("At least one recipient email address is required")
        if not self.subject.strip():
            raise ValueError("Email subject cannot be empty")
        if not self.content.strip():
            raise ValueError("Email content cannot be empty")


server: Any = Server("email")


class EmailClient:
    """Manages email operations and connections."""

    def __init__(self) -> None:
        self.email_address = EMAIL_ADDRESS
        self.email_password = EMAIL_PASSWORD
        self.imap_server = IMAP_SERVER
        self.smtp_server = SMTP_SERVER
        self.smtp_port = SMTP_PORT

    async def connect_imap(self) -> imaplib.IMAP4_SSL:
        """Create and return authenticated IMAP connection."""
        try:
            logging.info(f"Connecting to IMAP server: {self.imap_server}")
            mail = imaplib.IMAP4_SSL(self.imap_server)
            logging.info("IMAP SSL connection established")

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
        """Search emails with specified criteria."""
        mail = None
        try:
            mail = await self.connect_imap()

            # Select folder
            logging.info(f"Selecting folder: {criteria.folder}")
            if criteria.folder == "sent":
                result = mail.select('"[Gmail]/Sent Mail"')
                logging.info(f"Selected sent folder, result: {result}")
            else:
                result = mail.select("inbox")
                logging.info(f"Selected inbox, result: {result}")

            # Build search criteria
            search_criteria = await self._build_search_criteria(criteria)
            logging.info(f"Final search criteria: {search_criteria}")

            # Execute search
            email_list = await self._execute_search(mail, search_criteria)
            logging.info(f"Successfully fetched {len(email_list)} emails")

        except Exception as e:
            logging.error(f"Error in search_emails: {e!s}", exc_info=True)
            raise EmailSearchError(f"Email search failed: {e!s}") from e
        else:
            return email_list
        finally:
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
        """Count emails for each day in a date range."""
        mail = None
        try:
            mail = await self.connect_imap()
            mail.select("inbox")

            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")

            daily_counts = {}
            current_date = start_dt

            while current_date <= end_dt:
                date_str = current_date.strftime("%d-%b-%Y")
                search_criteria = f'(ON "{date_str}")'

                try:
                    async with asyncio.timeout(SEARCH_TIMEOUT):
                        count = await self._count_emails(mail, search_criteria)
                        daily_counts[current_date.strftime("%Y-%m-%d")] = count
                except asyncio.TimeoutError:
                    daily_counts[current_date.strftime("%Y-%m-%d")] = -1  # Indicate timeout

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
        """Build IMAP search criteria from SearchCriteria object."""
        # Set default dates if not provided
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

        # Format dates for IMAP search
        imap_start_date = start_date_dt.strftime("%d-%b-%Y")
        imap_end_date = end_date_dt.strftime("%d-%b-%Y")
        logging.info(f"IMAP formatted dates - start: {imap_start_date}, end: {imap_end_date}")

        # Build search criteria
        if start_date_dt.date() == end_date_dt.date():
            search_criteria = f'ON "{imap_start_date}"'
            logging.info(f"Single day search: {search_criteria}")
        else:
            imap_next_day_after_end = (end_date_dt + timedelta(days=1)).strftime("%d-%b-%Y")
            search_criteria = f'SINCE "{imap_start_date}" BEFORE "{imap_next_day_after_end}"'
            logging.info(f"Date range search: {search_criteria}")

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


# Create global email client instance
email_client = EmailClient()


# Tool Handler Functions
async def _handle_send_email(
    arguments: Dict[str, Any],
) -> List[Union[types.TextContent, types.ImageContent, types.EmbeddedResource]]:
    """Handle send-email tool with proper validation."""
    try:
        message = EmailMessage(
            to_addresses=arguments.get("to", []),
            subject=arguments.get("subject", ""),
            content=arguments.get("content", ""),
            cc_addresses=arguments.get("cc", []),
        )

        logging.info("Attempting to send email")
        logging.info(f"To: {message.to_addresses}")
        logging.info(f"Subject: {message.subject}")
        logging.info(f"CC: {message.cc_addresses}")

        async with asyncio.timeout(SEARCH_TIMEOUT):
            await email_client.send_email(message)
            return [
                types.TextContent(
                    type="text", text="Email sent successfully! Check email_client.log for detailed logs."
                )
            ]

    except ValueError as e:
        return [types.TextContent(type="text", text=f"Invalid input: {e!s}")]
    except asyncio.TimeoutError:
        logging.exception("Operation timed out while sending email")
        return [types.TextContent(type="text", text="Operation timed out while sending email.")]
    except EmailSendError as e:
        return [
            types.TextContent(
                type="text",
                text=f"Failed to send email: {e!s}\n\nPlease check:\n1. Email and password are correct in .env\n2. SMTP settings are correct\n3. Less secure app access is enabled (for Gmail)\n4. Using App Password if 2FA is enabled",
            )
        ]


async def _handle_search_emails(
    arguments: Dict[str, Any],
) -> List[Union[types.TextContent, types.ImageContent, types.EmbeddedResource]]:
    """Handle search-emails tool with proper validation."""
    try:
        folder = arguments.get("folder", "inbox")
        # Validate folder value
        if folder not in ["inbox", "sent"]:
            folder = "inbox"

        criteria = SearchCriteria(
            folder=cast(Literal["inbox", "sent"], folder),
            start_date=arguments.get("start_date"),
            end_date=arguments.get("end_date"),
            keyword=arguments.get("keyword"),
        )

        email_list = await email_client.search_emails(criteria)

        if not email_list:
            logging.info("No emails found matching the criteria")
            return [types.TextContent(type="text", text="No emails found matching the criteria.")]

        logging.info(f"Formatting {len(email_list)} emails for display")

        # Format the results as a table
        result_text = "Found emails:\n\n"
        result_text += "ID | From | Date | Subject\n"
        result_text += "-" * 80 + "\n"

        for email_item in email_list:
            result_text += (
                f"{email_item['id']} | {email_item['from']} | {email_item['date']} | {email_item['subject']}\n"
            )

        result_text += "\nUse get-email-content with an email ID to view the full content of a specific email."

        logging.info("Successfully returned search results")
        return [types.TextContent(type="text", text=result_text)]

    except ValueError as e:
        return [types.TextContent(type="text", text=f"Invalid input: {e!s}")]
    except asyncio.TimeoutError:
        logging.exception("Search operation timed out")
        return [
            types.TextContent(
                type="text", text="Search operation timed out. Please try with a more specific search criteria."
            )
        ]
    except EmailSearchError as e:
        return [types.TextContent(type="text", text=f"Search failed: {e!s}")]


async def _handle_get_email_content(
    arguments: Dict[str, Any],
) -> List[Union[types.TextContent, types.ImageContent, types.EmbeddedResource]]:
    """Handle get-email-content tool with proper validation."""
    email_id = arguments.get("email_id")
    if not email_id:
        return [types.TextContent(type="text", text="Email ID is required.")]

    try:
        async with asyncio.timeout(SEARCH_TIMEOUT):
            email_content = await email_client.get_email_content(email_id)

        if email_content:
            result_text = (
                f"From: {email_content['from']}\n"
                f"To: {email_content['to']}\n"
                f"Date: {email_content['date']}\n"
                f"Subject: {email_content['subject']}\n"
                f"\nContent:\n{email_content['content']}"
            )
            return [types.TextContent(type="text", text=result_text)]
        else:
            return [types.TextContent(type="text", text="No email content found.")]

    except asyncio.TimeoutError:
        return [types.TextContent(type="text", text="Operation timed out while fetching email content.")]
    except EmailSearchError as e:
        return [types.TextContent(type="text", text=f"Failed to get email content: {e!s}")]


async def _handle_count_daily_emails(
    arguments: Dict[str, Any],
) -> List[Union[types.TextContent, types.ImageContent, types.EmbeddedResource]]:
    """Handle count-daily-emails tool with proper validation."""
    start_date = arguments.get("start_date")
    end_date = arguments.get("end_date")

    if not start_date or not end_date:
        return [types.TextContent(type="text", text="Both start_date and end_date are required.")]

    try:
        # Validate date formats
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")

        daily_counts = await email_client.count_daily_emails(start_date, end_date)

        result_text = "Daily email counts:\n\n"
        result_text += "Date | Count\n"
        result_text += "-" * 30 + "\n"

        for date_str, count in daily_counts.items():
            if count == -1:
                result_text += f"{date_str} | Timeout\n"
            else:
                result_text += f"{date_str} | {count}\n"

        return [types.TextContent(type="text", text=result_text)]

    except ValueError as e:
        return [types.TextContent(type="text", text=f"Invalid date format: {e!s}")]
    except EmailSearchError as e:
        return [types.TextContent(type="text", text=f"Failed to count emails: {e!s}")]


def _handle_unknown_tool(name: str) -> List[Union[types.TextContent, types.ImageContent, types.EmbeddedResource]]:
    """Handle unknown tool error."""
    raise ValueError(f"Unknown tool: {name}")


@server.list_tools()  # type: ignore
async def handle_list_tools() -> List[types.Tool]:
    """
    List available tools.
    Each tool specifies its arguments using JSON Schema validation.
    """
    return [
        types.Tool(
            name="search-emails",
            description="Search emails within a date range and/or with specific keywords",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format (optional)",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format (optional)",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "Keyword to search in email subject and body (optional)",
                    },
                    "folder": {
                        "type": "string",
                        "description": "Folder to search in ('inbox' or 'sent', defaults to 'inbox')",
                        "enum": ["inbox", "sent"],
                    },
                },
            },
        ),
        types.Tool(
            name="get-email-content",
            description="Get the full content of a specific email by its ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "email_id": {
                        "type": "string",
                        "description": "The ID of the email to retrieve",
                    },
                },
                "required": ["email_id"],
            },
        ),
        types.Tool(
            name="count-daily-emails",
            description="Count emails received for each day in a date range",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format",
                    },
                },
                "required": ["start_date", "end_date"],
            },
        ),
        types.Tool(
            name="send-email",
            description="CONFIRMATION STEP: Actually send the email after user confirms the details. Before calling this, first show the email details to the user for confirmation. Required fields: recipients (to), subject, and content. Optional: CC recipients.",
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of recipient email addresses (confirmed)",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Confirmed email subject",
                    },
                    "content": {
                        "type": "string",
                        "description": "Confirmed email content",
                    },
                    "cc": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of CC recipient email addresses (optional, confirmed)",
                    },
                },
                "required": ["to", "subject", "content"],
            },
        ),
    ]


@server.call_tool()  # type: ignore
async def handle_call_tool(
    name: str, arguments: Optional[Dict[str, Any]]
) -> List[Union[types.TextContent, types.ImageContent, types.EmbeddedResource]]:
    """Handle tool execution requests using focused tool handlers."""
    if not arguments:
        arguments = {}

    logging.info(f"=== Tool Call: {name} ===")
    logging.info(f"Arguments: {arguments}")

    try:
        if name == "send-email":
            return await _handle_send_email(arguments)
        elif name == "search-emails":
            return await _handle_search_emails(arguments)
        elif name == "get-email-content":
            return await _handle_get_email_content(arguments)
        elif name == "count-daily-emails":
            return await _handle_count_daily_emails(arguments)
        else:
            return _handle_unknown_tool(name)
    except Exception as e:
        logging.error(f"Error in handle_call_tool for {name}: {e!s}", exc_info=True)
        return [types.TextContent(type="text", text=f"Error: {e!s}")]


async def main() -> None:
    logging.info("Starting MCP server main function")
    # Run the server using stdin/stdout streams
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        logging.info("stdio server created, starting server.run")
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="email",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server stopped by user")
    except Exception as e:
        logging.error(f"Server crashed: {e!s}", exc_info=True)
