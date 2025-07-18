"""MCP Server implementation for email operations.

This module provides the Model Context Protocol server that exposes
email functionality through standardized tools.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union, cast

import mcp.server.stdio
from mcp import types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

# Import configuration and email client
from .config import EMAIL_ADDRESS, EMAIL_PASSWORD, IMAP_SERVER, SMTP_PORT, SMTP_SERVER
from .email_client import (
    SEARCH_TIMEOUT,
    EmailClient,
    EmailDeletionError,
    EmailMessage,
    EmailSearchError,
    EmailSendError,
    SearchCriteria,
)

# Configure comprehensive logging for debugging and monitoring
# Logs include function name and line numbers for precise error tracking
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
    filename="email_client.log",
)

logging.info("=== Email Client Server Starting ===")
logging.info(f"Email configured: {EMAIL_ADDRESS}")
logging.info(f"IMAP server: {IMAP_SERVER}")
logging.info(f"SMTP server: {SMTP_SERVER}:{SMTP_PORT}")
logging.info(f"Password configured: {'Yes' if EMAIL_PASSWORD != 'your-app-specific-password' else 'No'}")

# MCP Server Instance
# Creates the Model Context Protocol server that handles tool registration
# and request routing. The server name "email" identifies this MCP server.
server: Any = Server("email")

# Global Email Client Instance
# Single instance used by all MCP tool handlers to maintain connection
# state and share configuration across email operations.
email_client = EmailClient()

# Global flag to control whether write operations (move/delete) are enabled
# This is set via command line argument --enable-write-operations
WRITE_OPERATIONS_ENABLED = False


# MCP Tool Handler Functions
# These functions process MCP tool calls and return formatted responses

async def _handle_send_email(
    arguments: Dict[str, Any],
) -> List[Union[types.TextContent, types.ImageContent, types.EmbeddedResource]]:
    """Handle the send-email MCP tool with comprehensive validation.

    Validates email parameters, creates an EmailMessage object, and attempts
    to send the email via SMTP. Provides detailed error messages for different
    failure scenarios to help users troubleshoot issues.

    Args:
        arguments: Dictionary containing tool arguments:
                  - to: List of recipient email addresses
                  - subject: Email subject line
                  - content: Email body content
                  - cc: Optional list of CC recipients

    Returns:
        List containing a single TextContent with success message or error details

    Error Handling:
        - ValueError: For invalid input parameters (missing fields, etc.)
        - TimeoutError: For operations that exceed timeout limit
        - EmailSendError: For SMTP-related failures with troubleshooting tips
    """
    try:
        # Create and validate email message from tool arguments
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
            subject=arguments.get("subject"),
            sender=arguments.get("sender"),
            body=arguments.get("body"),
            max_results=arguments.get("max_results", 100),
            start_from=arguments.get("start_from", 0),
        )

        email_list = await email_client.search_emails(criteria)

        if not email_list:
            logging.info("No emails found matching the criteria")
            return [types.TextContent(type="text", text="No emails found matching the criteria.")]

        logging.info(f"Formatting {len(email_list)} emails for display")

        # Format the results as a table
        start_from = criteria.start_from
        max_results = criteria.max_results
        result_text = f"Found emails (showing {len(email_list)} results starting from position {start_from}):\n\n"
        result_text += "ID | From | Date | Subject\n"
        result_text += "-" * 80 + "\n"

        for email_item in email_list:
            result_text += (
                f"{email_item['id']} | {email_item['from']} | {email_item['date']} | {email_item['subject']}\n"
            )

        result_text += f"\nShowing results {start_from + 1}-{start_from + len(email_list)}. "
        result_text += f"Use start_from={start_from + max_results} to get the next page.\n"
        result_text += "Use get-email-content with an email ID to view the full content of a specific email."

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


async def _handle_list_folders(
    arguments: Dict[str, Any],
) -> List[Union[types.TextContent, types.ImageContent, types.EmbeddedResource]]:
    """Handle list-folders tool to discover available email folders."""
    try:
        folders = await email_client.list_folders()

        if not folders:
            return [types.TextContent(type="text", text="No folders found.")]

        # Format the results as a table
        result_text = "Available email folders:\n\n"
        result_text += "Folder Name | Display Name | Attributes\n"
        result_text += "-" * 60 + "\n"

        for folder in folders:
            result_text += f"{folder['name']} | {folder['display_name']} | {folder['attributes']}\n"

        result_text += f"\nFound {len(folders)} folders total.\n"
        result_text += "Use any 'Folder Name' value in the search-emails tool."

        logging.info(f"Successfully returned {len(folders)} folders")
        return [types.TextContent(type="text", text=result_text)]

    except EmailSearchError as e:
        return [types.TextContent(type="text", text=f"Failed to list folders: {e!s}")]
    except Exception as e:
        logging.error(f"Unexpected error in list folders: {e!s}", exc_info=True)
        return [types.TextContent(type="text", text=f"Unexpected error: {e!s}")]


async def _handle_move_email(
    arguments: Dict[str, Any],
) -> List[Union[types.TextContent, types.ImageContent, types.EmbeddedResource]]:
    """Handle move-email tool to move one or more emails between folders."""
    email_ids = arguments.get("email_ids")
    source_folder = arguments.get("source_folder", "inbox")
    destination_folder = arguments.get("destination_folder")

    # Validate required parameters
    if not email_ids:
        return [types.TextContent(type="text", text="Email IDs are required.")]

    if not destination_folder:
        return [types.TextContent(type="text", text="Destination folder is required.")]

    # Handle array inputs (primary) and maintain backward compatibility for single strings
    if isinstance(email_ids, list):
        ids_to_move = email_ids
    elif isinstance(email_ids, str):
        # Backward compatibility: convert single string to array
        ids_to_move = [email_ids]
    else:
        return [types.TextContent(type="text", text="Email IDs must be an array of strings.")]

    if not ids_to_move:
        return [types.TextContent(type="text", text="At least one email ID is required.")]

    try:
        await email_client.move_email(ids_to_move, source_folder, destination_folder)

        count = len(ids_to_move)
        email_word = "email" if count == 1 else "emails"

        result_text = (
            f"Successfully moved {count} {email_word} from '{source_folder}' to '{destination_folder}'.\n"
            f"The {email_word} {('is' if count == 1 else 'are')} no longer in the source folder and can now be found in the destination folder.\n"
            f"Moved IDs: {', '.join(ids_to_move)}"
        )

        logging.info(f"Successfully moved {count} emails via MCP tool")
        return [types.TextContent(type="text", text=result_text)]

    except EmailDeletionError as e:
        return [types.TextContent(type="text", text=f"Failed to move emails: {e!s}")]
    except Exception as e:
        logging.error(f"Unexpected error in move emails: {e!s}", exc_info=True)
        return [types.TextContent(type="text", text=f"Unexpected error: {e!s}")]


async def _handle_delete_email(
    arguments: Dict[str, Any],
) -> List[Union[types.TextContent, types.ImageContent, types.EmbeddedResource]]:
    """Handle delete-emails tool to delete one or more emails with optional permanent flag."""
    email_ids = arguments.get("email_ids")
    folder = arguments.get("folder", "inbox")
    permanent = arguments.get("permanent", False)

    # Validate required parameters
    if not email_ids:
        return [types.TextContent(type="text", text="Email IDs are required.")]

    # Handle array inputs (primary) and maintain backward compatibility for single strings
    if isinstance(email_ids, list):
        ids_to_delete = email_ids
    elif isinstance(email_ids, str):
        # Backward compatibility: convert single string to array
        ids_to_delete = [email_ids]
    else:
        return [types.TextContent(type="text", text="Email IDs must be an array of strings.")]

    if not ids_to_delete:
        return [types.TextContent(type="text", text="At least one email ID is required.")]

    try:
        await email_client.delete_email(ids_to_delete, folder, permanent)

        count = len(ids_to_delete)
        email_word = "email" if count == 1 else "emails"

        if permanent:
            result_text = (
                f"Successfully permanently deleted {count} {email_word} from '{folder}'.\n"
                f"This action cannot be undone.\n"
                f"Deleted IDs: {', '.join(ids_to_delete)}"
            )
        else:
            result_text = (
                f"Successfully moved {count} {email_word} to trash from '{folder}'.\n"
                f"The {email_word} can be restored from the trash folder if needed.\n"
                f"Moved IDs: {', '.join(ids_to_delete)}"
            )

        logging.info(f"Successfully deleted {count} emails via MCP tool (permanent={permanent})")
        return [types.TextContent(type="text", text=result_text)]

    except EmailDeletionError as e:
        return [types.TextContent(type="text", text=f"Failed to delete emails: {e!s}")]
    except Exception as e:
        logging.error(f"Unexpected error in delete emails: {e!s}", exc_info=True)
        return [types.TextContent(type="text", text=f"Unexpected error: {e!s}")]


def _handle_unknown_tool(name: str) -> List[Union[types.TextContent, types.ImageContent, types.EmbeddedResource]]:
    """Handle unknown tool error."""
    raise ValueError(f"Unknown tool: {name}")


@server.list_tools()  # type: ignore
async def handle_list_tools() -> List[types.Tool]:
    """
    List available tools.
    Each tool specifies its arguments using JSON Schema validation.
    Write operations (move-emails, delete-emails) are only included if enabled via --enable-write-operations flag.
    """
    # Core read-only tools that are always available
    tools = [
        types.Tool(
            name="search-emails",
            description="Search emails within a date range and/or with specific field filters with pagination support",
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
                    "subject": {
                        "type": "string",
                        "description": "Text to search for in email subject line - will match emails where the subject contains this specific string (optional)",
                    },
                    "sender": {
                        "type": "string",
                        "description": "Text to search for in sender email address or name - will match emails where the sender contains this specific string (optional)",
                    },
                    "body": {
                        "type": "string",
                        "description": "Text to search for in email body content - will match emails where the body contains this specific string (optional)",
                    },
                    "folder": {
                        "type": "string",
                        "description": "Folder to search in (use 'list-folders' tool to see available folders, defaults to 'inbox')",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of emails to return (1-1000, defaults to 100)",
                        "minimum": 1,
                        "maximum": 1000,
                        "default": 100,
                    },
                    "start_from": {
                        "type": "integer",
                        "description": "Starting position for pagination (0-based index, defaults to 0)",
                        "minimum": 0,
                        "default": 0,
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
        types.Tool(
            name="list-folders",
            description="List all available email folders that can be used with other tools",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]

    # Add write operations (move/delete) only if enabled
    if WRITE_OPERATIONS_ENABLED:
        tools.extend([
            types.Tool(
                name="move-emails",
                description="Move one or more emails from one folder to another. Use an array of email IDs, even for single emails.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "email_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Array of email IDs to move. For a single email, use an array with one element.",
                        },
                        "source_folder": {
                            "type": "string",
                            "description": "Source folder containing the emails (defaults to 'inbox')",
                        },
                        "destination_folder": {
                            "type": "string",
                            "description": "Destination folder to move the emails to (use 'list-folders' to see options)",
                        },
                    },
                    "required": ["email_ids", "destination_folder"],
                },
            ),
            types.Tool(
                name="delete-emails",
                description="Delete one or more emails (move to trash by default, or permanently with 'permanent' flag). Use an array of email IDs, even for single emails.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "email_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Array of email IDs to delete. For a single email, use an array with one element.",
                        },
                        "folder": {
                            "type": "string",
                            "description": "Folder containing the email(s) (defaults to 'inbox')",
                        },
                        "permanent": {
                            "type": "boolean",
                            "description": "If true, permanently delete. If false (default), move to trash",
                        },
                    },
                    "required": ["email_ids"],
                },
            ),
        ])

    return tools


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
        elif name == "list-folders":
            return await _handle_list_folders(arguments)
        elif name == "move-emails":
            if not WRITE_OPERATIONS_ENABLED:
                return [types.TextContent(type="text", text="Move operations are disabled. Use --enable-write-operations flag to enable.")]
            return await _handle_move_email(arguments)
        elif name == "delete-emails":
            if not WRITE_OPERATIONS_ENABLED:
                return [types.TextContent(type="text", text="Delete operations are disabled. Use --enable-write-operations flag to enable.")]
            return await _handle_delete_email(arguments)
        else:
            return _handle_unknown_tool(name)
    except Exception as e:
        logging.error(f"Error in handle_call_tool for {name}: {e!s}", exc_info=True)
        return [types.TextContent(type="text", text=f"Error: {e!s}")]


async def main(enable_write_operations: bool = False) -> None:
    """Main entry point for the MCP email server.

    Sets up and runs the Model Context Protocol server using stdio transport.
    The server communicates with Claude Desktop via stdin/stdout streams.

    Args:
        enable_write_operations: If True, enables move-emails and delete-emails tools

    This function:
    1. Creates stdio communication streams
    2. Configures server capabilities and metadata
    3. Starts the main server event loop
    4. Handles graceful shutdown on completion
    """
    global WRITE_OPERATIONS_ENABLED
    WRITE_OPERATIONS_ENABLED = enable_write_operations

    logging.info("Starting MCP server main function")
    logging.info(f"Write operations enabled: {WRITE_OPERATIONS_ENABLED}")
    logging.info(f"Available tools: {'read/write' if WRITE_OPERATIONS_ENABLED else 'read-only'}")

    # Query and log IMAP server capabilities for debugging
    try:
        await email_client.query_server_capabilities()
    except Exception as e:
        logging.warning(f"Failed to query server capabilities (server will still work): {e}")

    # Create stdin/stdout communication streams for MCP protocol
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        logging.info("stdio server created, starting server.run")

        # Start the MCP server with configuration
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


