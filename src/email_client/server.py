from typing import Any, Dict, List, Optional, Tuple, Union
import asyncio
from datetime import datetime, timedelta
import email
import imaplib
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from dotenv import load_dotenv
from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
import mcp.server.stdio

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

server = Server("email")


def format_email_summary(msg_data: Tuple[Any, ...]) -> Dict[str, str]:
    """Format an email message into a summary dict with basic information."""
    email_body = email.message_from_bytes(msg_data[0][1])

    return {
        "id": msg_data[0][0].split()[0].decode(),  # Get the email ID
        "from": email_body.get("From", "Unknown"),
        "date": email_body.get("Date", "Unknown"),
        "subject": email_body.get("Subject", "No Subject"),
    }


def format_email_content(msg_data: Tuple[Any, ...]) -> Dict[str, str]:
    """Format an email message into a dict with full content."""
    email_body = email.message_from_bytes(msg_data[0][1])

    # Extract body content
    body = ""
    if email_body.is_multipart():
        # Handle multipart messages
        for part in email_body.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    body = payload.decode()
                break
            elif part.get_content_type() == "text/html":
                # If no plain text found, use HTML content
                if not body:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        body = payload.decode()
    else:
        # Handle non-multipart messages
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


async def search_emails_async(mail: imaplib.IMAP4_SSL, search_criteria: str) -> List[Dict[str, str]]:
    """Asynchronously search emails with timeout."""
    loop = asyncio.get_event_loop()
    try:
        logging.debug(f"Executing IMAP search with criteria: {search_criteria}")
        _, messages = await loop.run_in_executor(None, lambda: mail.search(None, search_criteria))
        logging.debug(f"Search result: {messages}")

        if not messages[0]:
            logging.info("No messages found matching criteria")
            return []

        message_ids = messages[0].split()
        logging.info(f"Found {len(message_ids)} messages, fetching up to {MAX_EMAILS}")

        email_list = []
        for i, num in enumerate(message_ids[:MAX_EMAILS]):  # Limit to MAX_EMAILS
            logging.debug(f"Fetching email {i+1}/{min(len(message_ids), MAX_EMAILS)}, ID: {num}")

            def fetch_email() -> Any:
                return mail.fetch(num, "(RFC822)")

            _, msg_data = await loop.run_in_executor(None, fetch_email)
            if msg_data and msg_data[0]:
                email_list.append(format_email_summary((msg_data[0],)))

        logging.info(f"Successfully fetched {len(email_list)} emails")
        return email_list
    except Exception as e:
        logging.error(f"Error in search_emails_async: {str(e)}", exc_info=True)
        raise Exception(f"Error searching emails: {str(e)}")


async def get_email_content_async(mail: imaplib.IMAP4_SSL, email_id: str) -> Dict[str, str]:
    """Asynchronously get full content of a specific email."""
    loop = asyncio.get_event_loop()
    try:
        _, msg_data = await loop.run_in_executor(None, lambda: mail.fetch(email_id, "(RFC822)"))
        if msg_data and msg_data[0]:
            return format_email_content((msg_data[0],))
        else:
            raise Exception("No email data returned")
    except Exception as e:
        raise Exception(f"Error fetching email content: {str(e)}")


async def count_emails_async(mail: imaplib.IMAP4_SSL, search_criteria: str) -> int:
    """Asynchronously count emails matching the search criteria."""
    loop = asyncio.get_event_loop()
    try:
        _, messages = await loop.run_in_executor(None, lambda: mail.search(None, search_criteria))
        return len(messages[0].split()) if messages[0] else 0
    except Exception as e:
        raise Exception(f"Error counting emails: {str(e)}")


async def send_email_async(
    to_addresses: List[str], subject: str, content: str, cc_addresses: Optional[List[str]] = None
) -> None:
    """Asynchronously send an email."""
    try:
        # Create message
        msg = MIMEMultipart()
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = ", ".join(to_addresses)
        if cc_addresses:
            msg["Cc"] = ", ".join(cc_addresses)
        msg["Subject"] = subject

        # Add body
        msg.attach(MIMEText(content, "plain", "utf-8"))

        # Connect to SMTP server and send email
        def send_sync() -> None:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp_server:
                smtp_server.set_debuglevel(1)  # Enable debug output
                logging.debug(f"Connecting to {SMTP_SERVER}:{SMTP_PORT}")

                # Start TLS
                logging.debug("Starting TLS")
                smtp_server.starttls()

                # Login
                logging.debug(f"Logging in as {EMAIL_ADDRESS}")
                smtp_server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)

                # Send email
                all_recipients = to_addresses + (cc_addresses or [])
                logging.debug(f"Sending email to: {all_recipients}")
                result = smtp_server.send_message(msg, EMAIL_ADDRESS, all_recipients)

                if result:
                    # send_message returns a dict of failed recipients
                    raise Exception(f"Failed to send to some recipients: {result}")

                logging.debug("Email sent successfully")

        # Run the synchronous send function in the executor
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, send_sync)

    except Exception as e:
        logging.error(f"Error in send_email_async: {str(e)}")
        raise


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
    """
    Handle tool execution requests.
    Tools can search emails and return results.
    """
    if not arguments:
        arguments = {}

    logging.info(f"=== Tool Call: {name} ===")
    logging.info(f"Arguments: {arguments}")

    mail: Optional[imaplib.IMAP4_SSL] = None

    try:
        if name == "send-email":
            to_addresses = arguments.get("to", [])
            subject = arguments.get("subject", "")
            content = arguments.get("content", "")
            cc_addresses = arguments.get("cc", [])

            if not to_addresses:
                return [types.TextContent(type="text", text="At least one recipient email address is required.")]

            try:
                logging.info("Attempting to send email")
                logging.info(f"To: {to_addresses}")
                logging.info(f"Subject: {subject}")
                logging.info(f"CC: {cc_addresses}")

                async with asyncio.timeout(SEARCH_TIMEOUT):
                    await send_email_async(to_addresses, subject, content, cc_addresses)
                    return [
                        types.TextContent(
                            type="text", text="Email sent successfully! Check email_client.log for detailed logs."
                        )
                    ]
            except asyncio.TimeoutError:
                logging.error("Operation timed out while sending email")
                return [types.TextContent(type="text", text="Operation timed out while sending email.")]
            except Exception as e:
                error_msg = str(e)
                logging.error(f"Failed to send email: {error_msg}")
                return [
                    types.TextContent(
                        type="text",
                        text=f"Failed to send email: {error_msg}\n\nPlease check:\n1. Email and password are correct in .env\n2. SMTP settings are correct\n3. Less secure app access is enabled (for Gmail)\n4. Using App Password if 2FA is enabled",
                    )
                ]

        # Connect to IMAP server using predefined credentials
        logging.info(f"Connecting to IMAP server: {IMAP_SERVER}")
        logging.info(f"Using email: {EMAIL_ADDRESS}")

        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER)
            logging.info("IMAP SSL connection established")

            mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            logging.info("IMAP login successful")
        except Exception as e:
            logging.error(f"IMAP connection/login failed: {str(e)}")
            raise

        if name == "search-emails":
            # 选择文件夹
            folder = arguments.get("folder", "inbox")  # 默认选择收件箱
            logging.info(f"Selecting folder: {folder}")

            try:
                if folder == "sent":
                    result = mail.select('"[Gmail]/Sent Mail"')  # 对于 Gmail
                    logging.info(f"Selected sent folder, result: {result}")
                else:
                    result = mail.select("inbox")
                    logging.info(f"Selected inbox, result: {result}")
            except Exception as e:
                logging.error(f"Failed to select folder: {str(e)}")
                raise

            # Get optional parameters
            start_date_str = arguments.get("start_date")
            end_date_str = arguments.get("end_date")
            keyword = arguments.get("keyword")

            logging.info(f"Raw parameters - start_date: {start_date_str}, end_date: {end_date_str}, keyword: {keyword}")

            # Set default dates if not provided
            if not start_date_str:
                start_date_dt = datetime.now() - timedelta(days=7)
                logging.info(f"No start_date provided, using default: {start_date_dt.strftime('%Y-%m-%d')}")
            else:
                start_date_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
                logging.info(f"Parsed start_date: {start_date_str}")

            if not end_date_str:
                end_date_dt = datetime.now()
                logging.info(f"No end_date provided, using today: {end_date_dt.strftime('%Y-%m-%d')}")
            else:
                end_date_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
                logging.info(f"Parsed end_date: {end_date_str}")

            # Format dates for IMAP search (e.g., "01-Jan-2023")
            imap_start_date = start_date_dt.strftime("%d-%b-%Y")
            imap_end_date = end_date_dt.strftime("%d-%b-%Y")
            logging.info(f"IMAP formatted dates - start: {imap_start_date}, end: {imap_end_date}")

            # Build search criteria
            if start_date_dt.date() == end_date_dt.date():
                # If searching for a single day
                search_criteria = f'ON "{imap_start_date}"'
                logging.info(f"Single day search: {search_criteria}")
            else:
                # Include the end date by searching until the day after
                imap_next_day_after_end = (end_date_dt + timedelta(days=1)).strftime("%d-%b-%Y")
                search_criteria = f'SINCE "{imap_start_date}" BEFORE "{imap_next_day_after_end}"'
                logging.info(f"Date range search: {search_criteria}")

            if keyword:
                # Properly combine keyword search with date criteria
                keyword_criteria = f'(OR SUBJECT "{keyword}" BODY "{keyword}")'
                search_criteria = f"({keyword_criteria} {search_criteria})"
                logging.info(f"Added keyword search, final criteria: {search_criteria}")

            logging.info(f"Final search criteria: {search_criteria}")

            try:
                async with asyncio.timeout(SEARCH_TIMEOUT):
                    email_list = await search_emails_async(mail, search_criteria)

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

            except asyncio.TimeoutError:
                logging.error("Search operation timed out")
                return [
                    types.TextContent(
                        type="text", text="Search operation timed out. Please try with a more specific search criteria."
                    )
                ]
            except Exception as e:
                logging.error(f"Unexpected error during search: {str(e)}", exc_info=True)
                raise

        elif name == "get-email-content":
            email_id = arguments.get("email_id")
            if not email_id:
                return [types.TextContent(type="text", text="Email ID is required.")]

            try:
                async with asyncio.timeout(SEARCH_TIMEOUT):
                    email_content = await get_email_content_async(mail, email_id)

                result_text = (
                    f"From: {email_content['from']}\n"
                    f"To: {email_content['to']}\n"
                    f"Date: {email_content['date']}\n"
                    f"Subject: {email_content['subject']}\n"
                    f"\nContent:\n{email_content['content']}"
                )

                return [types.TextContent(type="text", text=result_text)]

            except asyncio.TimeoutError:
                return [types.TextContent(type="text", text="Operation timed out while fetching email content.")]

        elif name == "count-daily-emails":
            start_date = datetime.strptime(arguments["start_date"], "%Y-%m-%d")
            end_date = datetime.strptime(arguments["end_date"], "%Y-%m-%d")

            result_text = "Daily email counts:\n\n"
            result_text += "Date | Count\n"
            result_text += "-" * 30 + "\n"

            current_date = start_date
            while current_date <= end_date:
                date_str = current_date.strftime("%d-%b-%Y")
                search_criteria = f'(ON "{date_str}")'

                try:
                    async with asyncio.timeout(SEARCH_TIMEOUT):
                        count = await count_emails_async(mail, search_criteria)
                        result_text += f"{current_date.strftime('%Y-%m-%d')} | {count}\n"
                except asyncio.TimeoutError:
                    result_text += f"{current_date.strftime('%Y-%m-%d')} | Timeout\n"

                current_date += timedelta(days=1)

            return [types.TextContent(type="text", text=result_text)]

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        logging.error(f"Error in handle_call_tool for {name}: {str(e)}", exc_info=True)
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
    finally:
        if mail is not None:
            try:
                mail.close()
                mail.logout()
                logging.info("IMAP connection closed")
            except Exception as cleanup_e:
                logging.warning(f"Error closing IMAP connection: {str(cleanup_e)}")


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
        logging.error(f"Server crashed: {str(e)}", exc_info=True)
