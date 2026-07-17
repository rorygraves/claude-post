"""Email MCP Server using the annotation-based framework.

This is an example of how to refactor the email server using the new
annotation-based MCP framework.
"""

import argparse
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

import pandas as pd

from mcp_framework import BaseMCPServer, mcp_tool

from .data_processing import DataStore
from .email_client import (
    EmailClient,
    EmailMessage,
    SearchCriteria,
)


class EmailMCPServer(BaseMCPServer):
    """Email server implemented using the annotation-based MCP framework."""

    def __init__(
        self,
        enable_write_operations: bool = False,
        enable_send_operations: bool = False,
        enable_file_operations: bool = False,
        *,
        email_client: EmailClient | None = None,
        datastore: DataStore | None = None,
    ) -> None:
        """Initialize the email MCP server.

        Args:
            enable_write_operations: Whether to enable move/delete operations
        """
        self.write_operations_enabled = enable_write_operations
        self.send_operations_enabled = enable_send_operations
        self.file_operations_enabled = enable_file_operations
        self.email_client = email_client or EmailClient()
        self.datastore = datastore or DataStore()
        super().__init__("email", "0.3.0", tool_prefix="mail-")

    def _is_tool_enabled(self, method: Any) -> bool:
        capability = getattr(method, "_mcp_tool_capability", None)
        if capability is None:
            return True
        return {
            "mailbox_write": self.write_operations_enabled,
            "send": self.send_operations_enabled,
            "filesystem": self.file_operations_enabled,
        }.get(capability, False)

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        """Add email-specific command line arguments."""
        parser.add_argument(
            "--enable-write-operations",
            action="store_true",
            help="Enable write operations (move and delete emails). "
            "By default, only read operations are available for safety.",
        )
        parser.add_argument(
            "--enable-send-operations",
            action="store_true",
            help="Enable the tool that sends email. Disabled by default.",
        )
        parser.add_argument(
            "--enable-file-operations",
            action="store_true",
            help="Enable attachment download and export tools. Disabled by default.",
        )

    @mcp_tool(name="search")
    async def search(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        subject: Optional[str] = None,
        sender: Optional[str] = None,
        to: Optional[str] = None,
        body: Optional[str] = None,
        folder: str = "inbox",
        max_results: int = 100,
        start_from: int = 0,
        collection_name: Optional[str] = None,
        direction: Literal["newest", "oldest"] = "newest",
    ) -> Dict[str, Any]:
        """Search emails and automatically create a data collection from the results.

        Returns collection metadata only (ID, name, shape, columns, creation time) - NOT individual email data.

        Args:
            start_date: Start date in YYYY-MM-DD format (optional). Filters emails received on or after this date.
            end_date: End date in YYYY-MM-DD format (optional). Filters emails received on or before this date.
            subject: Text to search for in email subject line (optional). Case-insensitive substring match.
            sender: Text to search for in sender email address or display name (optional). Case-insensitive substring match.
            to: Text to search for in recipient email address or display name (optional). Case-insensitive substring match.
            body: Text to search for in email body content (optional). Case-insensitive substring match.
            folder: Email folder to search in (optional, defaults to 'inbox'). Use 'list-folders' to see available folders.
            max_results: Maximum number of emails to include in the collection (optional, defaults to 100).
            start_from: Starting position for pagination (optional, defaults to 0).
            collection_name: Optional descriptive name for the created collection.
            direction: Sort direction for email results (optional, defaults to 'newest').

        Returns:
            Collection metadata dictionary

        Raises:
            ValueError: If folder or direction parameters are invalid
        """
        # Validate direction parameter (should be guaranteed by signature, but double-check)
        if direction not in ("newest", "oldest"):
            return {
                "error": f"Invalid direction: '{direction}'. Must be 'newest' or 'oldest'.",
                "valid_directions": ["newest", "oldest"],
            }

        criteria = SearchCriteria(
            folder=folder,
            start_date=start_date,
            end_date=end_date,
            subject=subject,
            sender=sender,
            to=to,
            body=body,
            max_results=max_results,
            start_from=start_from,
            direction=direction,
        )

        email_list, pagination = await self.email_client.search_emails(criteria)

        if not email_list:
            return {
                "error": "No emails found matching the criteria.",
                "pagination": pagination.to_dict(),
            }

        # Create collection from search results
        df = pd.DataFrame(email_list)
        collection_metadata = self.datastore.create(df, collection_name, source_folder=folder)

        return {
            **collection_metadata,
            "pagination": pagination.to_dict(),
        }

    @mcp_tool(name="get-content")
    async def get_content(
        self,
        email_id: Optional[str] = None,
        email_ids: Optional[List[str]] = None,
        folder: str = "inbox",
    ) -> Dict[str, Any]:
        """Get the full content of one or more emails by ID.

        Supports both single email retrieval (for backwards compatibility) and
        bulk retrieval for efficiency.

        Args:
            email_id: Single email ID to retrieve (for backwards compatibility)
            email_ids: List of email IDs for bulk retrieval (max 50)
            folder: Folder containing the email(s) (defaults to 'inbox')

        Returns:
            For single ID: Dictionary with email details, Message-ID, stable Gmail IDs, and permalink
            For multiple IDs: Dictionary with 'emails' list, 'fetched' count, and 'errors' list
        """
        # Collect all IDs to fetch
        ids_to_fetch: List[str] = []
        if email_id:
            ids_to_fetch.append(email_id)
        if email_ids:
            ids_to_fetch.extend(email_ids)

        if not ids_to_fetch:
            return {"error": "Either email_id or email_ids is required"}

        # Single email - maintain backwards compatible response
        if len(ids_to_fetch) == 1:
            email_content = await self.email_client.get_email_content(ids_to_fetch[0], folder)
            if email_content:
                return email_content
            else:
                return {"error": "No email content found."}

        # Bulk retrieval
        result = await self.email_client.get_email_contents_bulk(ids_to_fetch, folder)
        return result

    @mcp_tool(name="download-attachment", capability="filesystem")
    async def download_attachment(
        self,
        email_id: str,
        attachment_index: int,
        output_dir: str,
        folder: str = "inbox",
    ) -> Dict[str, Any]:
        """Download a specific attachment from an email and save to disk.

        First use mail-get-content to see available attachments (with index, filename,
        content_type, and size), then use this tool to download the desired attachment.

        Args:
            email_id: The ID of the email containing the attachment
            attachment_index: Zero-based index of the attachment to download (from attachments list)
            output_dir: Absolute path to directory where file should be saved (e.g., '/Users/name/Downloads')
            folder: Folder containing the email (defaults to 'inbox')

        Returns:
            Dictionary containing:
            - filename: Original name of the attachment file
            - saved_as: Actual saved filename (may differ if collision occurred)
            - filepath: Full absolute path to the saved file
            - content_type: MIME type of the attachment
            - size: Size in bytes
            - email_id: Source email ID

        Raises:
            Error if attachment not found, output_dir is invalid, or exceeds 25MB size limit
        """
        try:
            result = await self.email_client.download_attachment(email_id, attachment_index, output_dir, folder)
            if result:
                return result
            else:
                return {"error": f"Attachment at index {attachment_index} not found in email {email_id}"}
        except ValueError as e:
            return {"error": str(e)}

    @mcp_tool(name="send", capability="send")
    async def send(self, to: List[str], subject: str, content: str, cc: Optional[List[str]] = None) -> str:
        """Send an email after user confirms the details.

        Before calling this, first show the email details to the user for confirmation.

        Args:
            to: List of recipient email addresses (confirmed)
            subject: Confirmed email subject
            content: Confirmed email content
            cc: List of CC recipient email addresses (optional, confirmed)

        Returns:
            Success message or error details
        """
        message = EmailMessage(
            to_addresses=to,
            subject=subject,
            content=content,
            cc_addresses=cc or [],
        )

        await self.email_client.send_email(message)
        return "Email sent successfully."

    @mcp_tool(name="folders")
    async def folders(self) -> List[Dict[str, str]]:
        """List all available email folders that can be used with other tools.

        Returns:
            List of folder dictionaries with name, display_name, and attributes
        """
        return await self.email_client.list_folders()

    @mcp_tool(name="count-daily")
    async def count_daily(self, start_date: str, end_date: str) -> Dict[str, int]:
        """Count emails received for each day in a date range.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            Dictionary mapping dates to email counts
        """
        # Validate date formats
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")

        return await self.email_client.count_daily_emails(start_date, end_date)

    @mcp_tool(name="transform")
    async def transform(
        self,
        collection_id: str,
        operation: Literal[
            "select_columns",
            "drop_columns",
            "rename_columns",
            "sort",
            "filter",
            "head",
            "tail",
            "drop_duplicates",
            "convert_datetime",
            "group_count",
        ],
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Apply one declarative, allowlisted transformation to a collection.

        Args:
            collection_id: ID of the collection to transform
            operation: Named transformation to apply
            parameters: Operation-specific parameters; no Python expressions are accepted

        Returns:
            Updated collection metadata
        """
        metadata = self.datastore.update(collection_id, operation, parameters)
        return metadata

    @mcp_tool(name="fetch")
    async def fetch(
        self, collection_id: str, limit: int = 100, format: Literal["records", "dict", "csv", "json"] = "records"
    ) -> Dict[str, Any]:
        """Retrieve email data from a collection created by search-emails.

        Args:
            collection_id: ID of the collection to fetch
            limit: Maximum number of email records to return
            format: Output format - 'records', 'dict', 'csv', or 'json'

        Returns:
            Dictionary containing metadata and data
        """
        return self.datastore.fetch(collection_id, limit, format)

    @mcp_tool(name="list")
    async def list_collections(self) -> List[Dict[str, Any]]:
        """List all available email data collections with their metadata.

        Returns:
            List of collection metadata dictionaries
        """
        return self.datastore.list_collections()

    @mcp_tool(name="drop")
    async def drop_collection(self, collection_id: str) -> Dict[str, Any]:
        """Delete a single email data collection, freeing its slot in the store.

        Args:
            collection_id: ID of the collection to delete

        Returns:
            Dictionary confirming the deletion and remaining collection count
        """
        self.datastore.delete(collection_id)
        return {
            "deleted": collection_id,
            "remaining": len(self.datastore.list_collections()),
        }

    @mcp_tool(name="clear")
    async def clear_collections(self) -> Dict[str, Any]:
        """Delete all email data collections, freeing the entire in-memory store.

        Use this to reclaim space when the collection store is full. Collections
        are derived search results, not mailbox data, so this never touches email.

        Returns:
            Dictionary with the number of collections cleared
        """
        cleared = self.datastore.clear()
        return {"cleared": cleared, "remaining": 0}

    @mcp_tool(name="preview")
    async def preview(self, collection_id: str, rows: int = 5) -> Dict[str, Any]:
        """Preview an email collection's structure, data types, and sample records.

        Args:
            collection_id: ID of the collection to preview
            rows: Number of sample email records to show

        Returns:
            Dictionary with metadata, data types, and preview data
        """
        return self.datastore.preview(collection_id, rows)

    @mcp_tool(name="combine")
    async def combine(self, target_collection_id: str, source_collection_id: str) -> Dict[str, Any]:
        """Combine two email collections by appending source to target.

        Collections must have identical column structure.

        Args:
            target_collection_id: ID of the collection to append to (will be modified)
            source_collection_id: ID of the collection to append from (unchanged)

        Returns:
            Updated target collection metadata
        """
        return self.datastore.combine(target_collection_id, source_collection_id)

    @mcp_tool(name="export", capability="filesystem")
    async def export(
        self,
        collection_id: str,
        output_dir: str,
        include_attachments: bool = True,
    ) -> Dict[str, Any]:
        """Export emails from a collection to markdown files with optional attachments.

        Writes files directly to disk without passing content through LLM context.
        Perfect for bulk email export operations where you need files on disk.

        Typical workflow:
        1. Use mail-search to create a collection of emails
        2. Use mail-export to write all emails to markdown files

        Args:
            collection_id: ID of the collection to export (from mail-search)
            output_dir: Absolute path to directory where files should be saved
                        (e.g., '/Users/name/Downloads/emails')
            include_attachments: If true (default), download attachments alongside
                                 markdown files

        Returns:
            Dictionary containing:
            - output_dir: Path where files were saved
            - emails_exported: Number of emails successfully exported
            - files_created: List of {email_id, filepath} for each markdown file
            - attachments_downloaded: List of {email_id, filepath, size} for attachments
            - errors: List of {email_id, error} for any failures

        Raises:
            ValueError: If collection_id not found or output_dir is invalid
        """
        # Get collection metadata from DataStore
        collection = self.datastore.get_collection(collection_id)
        if collection is None:
            return {"error": f"Collection '{collection_id}' not found"}

        # Get the DataFrame and source folder
        df = collection["df"]
        source_folder = collection.get("source_folder", "inbox")

        # Extract email IDs from the collection
        if "id" not in df.columns:
            return {"error": "Collection does not contain email IDs (missing 'id' column)"}

        email_ids = df["id"].tolist()

        if not email_ids:
            return {"error": "Collection is empty, no emails to export"}

        # Export emails using the bulk method
        try:
            result = await self.email_client.export_emails_bulk(
                email_ids=email_ids,
                output_dir=output_dir,
                folder=source_folder,
                include_attachments=include_attachments,
            )
            return result
        except ValueError as e:
            return {"error": str(e)}

    @mcp_tool(name="move", capability="mailbox_write")
    async def move_emails(self, email_ids: List[str], destination_folder: str, source_folder: str = "inbox") -> str:
        """Move one or more emails from one folder to another.

        Args:
            email_ids: Array of email IDs to move
            destination_folder: Destination folder to move the emails to
            source_folder: Source folder containing the emails (defaults to 'inbox')

        Returns:
            Success message with details
        """
        await self.email_client.move_email(email_ids, source_folder, destination_folder)

        count = len(email_ids)
        email_word = "email" if count == 1 else "emails"

        return (
            f"Successfully moved {count} {email_word} from '{source_folder}' to '{destination_folder}'.\n"
            f"Moved IDs: {', '.join(email_ids)}"
        )

    @mcp_tool(name="delete", capability="mailbox_write")
    async def delete_emails(self, email_ids: List[str], folder: str = "inbox", permanent: bool = False) -> str:
        """Delete one or more emails (move to trash by default, or permanently).

        Args:
            email_ids: Array of email IDs to delete
            folder: Folder containing the email(s) (defaults to 'inbox')
            permanent: If true, permanently delete. If false (default), move to trash

        Returns:
            Success message with details
        """
        await self.email_client.delete_email(email_ids, folder, permanent)

        count = len(email_ids)
        email_word = "email" if count == 1 else "emails"

        if permanent:
            return (
                f"Successfully permanently deleted {count} {email_word} from '{folder}'.\n"
                f"This action cannot be undone.\n"
                f"Deleted IDs: {', '.join(email_ids)}"
            )
        else:
            return (
                f"Successfully moved {count} {email_word} to trash from '{folder}'.\n"
                f"The {email_word} can be restored from the trash folder if needed.\n"
                f"Moved IDs: {', '.join(email_ids)}"
            )


def main() -> None:
    """Main entry point for the annotated email MCP server."""
    parser = argparse.ArgumentParser(prog="email", description="Email MCP server")
    parser.add_argument("--describe", action="store_true", help="Show available tools and their parameters")
    EmailMCPServer.add_arguments(parser)
    parsed_args = parser.parse_args()
    server = EmailMCPServer(
        enable_write_operations=parsed_args.enable_write_operations,
        enable_send_operations=parsed_args.enable_send_operations,
        enable_file_operations=parsed_args.enable_file_operations,
    )
    if parsed_args.describe:
        server.describe_tools()
        return
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
