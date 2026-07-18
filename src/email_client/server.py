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

from .config import DEFAULT_ACCOUNT_ALIAS, load_accounts_config
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
        clients: dict[str, EmailClient] | None = None,
        primary_alias: str | None = None,
    ) -> None:
        """Initialize the email MCP server.

        Args:
            enable_write_operations: Whether to enable move/delete operations
            email_client: A single pre-built client (registered as the primary account).
            clients: A pre-built alias -> client registry (takes precedence over email_client).
            primary_alias: Which alias in ``clients`` is primary (defaults to the first).
        """
        self.write_operations_enabled = enable_write_operations
        self.send_operations_enabled = enable_send_operations
        self.file_operations_enabled = enable_file_operations
        self.datastore = datastore or DataStore()

        # Client registry. Injected clients take precedence and keep credential
        # loading lazy (so `--describe` needs no .env); otherwise the accounts
        # configuration is loaded on first mailbox use.
        if clients is not None:
            self._clients: dict[str, EmailClient] = dict(clients)
            self._primary_alias = primary_alias or next(iter(self._clients))
            self._accounts_ready = True
        elif email_client is not None:
            self._clients = {DEFAULT_ACCOUNT_ALIAS: email_client}
            self._primary_alias = DEFAULT_ACCOUNT_ALIAS
            self._accounts_ready = True
        else:
            self._clients = {}
            self._primary_alias = DEFAULT_ACCOUNT_ALIAS
            self._accounts_ready = False
        super().__init__("email", "0.4.0", tool_prefix="mail-")

    def _ensure_accounts_ready(self) -> None:
        """Load configured accounts into the client registry on first use."""
        if self._accounts_ready:
            return
        accounts = load_accounts_config()
        self._clients = {alias: EmailClient(config) for alias, config in accounts.accounts.items()}
        self._primary_alias = accounts.primary_alias
        self._accounts_ready = True

    def _client_for(self, account: str | None) -> EmailClient:
        """Return the email client for ``account``, or the primary when None."""
        self._ensure_accounts_ready()
        alias = account or self._primary_alias
        client = self._clients.get(alias)
        if client is None:
            available = ", ".join(sorted(self._clients))
            raise ValueError(f"Unknown account '{alias}'. Configured accounts: {available}")
        return client

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

    @mcp_tool(name="accounts")
    async def accounts(self) -> List[Dict[str, Any]]:
        """List the configured email accounts that can be targeted with the `account` parameter.

        Returns:
            List of dicts with alias, email_address, and whether the account is primary.
            The primary account is used by any tool call that omits `account`.
        """
        self._ensure_accounts_ready()
        return [
            {
                "alias": alias,
                "email_address": self._clients[alias].email_address,
                "primary": alias == self._primary_alias,
            }
            for alias in sorted(self._clients)
        ]

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
        account: Optional[str] = None,
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
            account: Account alias to search (optional). Defaults to the primary account.
                Use 'mail-accounts' to list configured accounts.

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

        client = self._client_for(account)
        email_list, pagination = await client.search_emails(criteria)

        if not email_list:
            return {
                "error": "No emails found matching the criteria.",
                "pagination": pagination.to_dict(),
            }

        # Create collection from search results
        df = pd.DataFrame(email_list)
        collection_metadata = self.datastore.create(
            df, collection_name, source_folder=folder, account=account or self._primary_alias
        )

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
        account: Optional[str] = None,
        gmail_msgid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get the full content of one or more emails by ID.

        Supports single retrieval (by IMAP UID or stable Gmail id) and bulk retrieval.

        Args:
            email_id: Single IMAP UID to retrieve.
            email_ids: List of IMAP UIDs for bulk retrieval (max 50).
            folder: Folder containing the email(s) (defaults to 'inbox').
            account: Account alias to read from (optional). Defaults to the primary account.
            gmail_msgid: Stable Gmail message id (X-GM-MSGID) for a single email; resolved
                to the current UID in `folder`. Survives folder moves, unlike email_id.

        Returns:
            For single ID: Dictionary with email details, Message-ID, stable Gmail IDs, and permalink
            For multiple IDs: Dictionary with 'emails' list, 'fetched' count, and 'errors' list
        """
        client = self._client_for(account)

        # Stable Gmail id path (single email only).
        if gmail_msgid:
            if email_id or email_ids:
                return {"error": "Provide gmail_msgid alone, not with email_id/email_ids"}
            email_content = await client.get_email_content(folder=folder, gmail_msgid=gmail_msgid)
            return email_content or {"error": "No email content found."}

        # Collect all IDs to fetch
        ids_to_fetch: List[str] = []
        if email_id:
            ids_to_fetch.append(email_id)
        if email_ids:
            ids_to_fetch.extend(email_ids)

        if not ids_to_fetch:
            return {"error": "One of email_id, email_ids, or gmail_msgid is required"}

        # Single email - maintain backwards compatible response
        if len(ids_to_fetch) == 1:
            email_content = await client.get_email_content(ids_to_fetch[0], folder)
            if email_content:
                return email_content
            else:
                return {"error": "No email content found."}

        # Bulk retrieval
        result = await client.get_email_contents_bulk(ids_to_fetch, folder)
        return result

    @mcp_tool(name="download-attachment", capability="filesystem")
    async def download_attachment(
        self,
        email_id: str,
        attachment_index: int,
        output_dir: str,
        folder: str = "inbox",
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Download a specific attachment from an email and save to disk.

        First use mail-get-content to see available attachments (with index, filename,
        content_type, and size), then use this tool to download the desired attachment.

        Args:
            email_id: The ID of the email containing the attachment
            attachment_index: Zero-based index of the attachment to download (from attachments list)
            output_dir: Absolute path to directory where file should be saved (e.g., '/Users/name/Downloads')
            folder: Folder containing the email (defaults to 'inbox')
            account: Account alias to read from (optional). Defaults to the primary account.

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
            client = self._client_for(account)
            result = await client.download_attachment(email_id, attachment_index, output_dir, folder)
            if result:
                return result
            else:
                return {"error": f"Attachment at index {attachment_index} not found in email {email_id}"}
        except ValueError as e:
            return {"error": str(e)}

    @mcp_tool(name="send", capability="send")
    async def send(
        self,
        to: List[str],
        subject: str,
        content: str,
        cc: Optional[List[str]] = None,
        account: Optional[str] = None,
    ) -> str:
        """Send an email after user confirms the details.

        Before calling this, first show the email details to the user for confirmation.

        Args:
            to: List of recipient email addresses (confirmed)
            subject: Confirmed email subject
            content: Confirmed email content
            cc: List of CC recipient email addresses (optional, confirmed)
            account: Account alias to send from (optional). Defaults to the primary account.

        Returns:
            Success message or error details
        """
        message = EmailMessage(
            to_addresses=to,
            subject=subject,
            content=content,
            cc_addresses=cc or [],
        )

        await self._client_for(account).send_email(message)
        return "Email sent successfully."

    @mcp_tool(name="folders")
    async def folders(self, account: Optional[str] = None) -> List[Dict[str, str]]:
        """List all available email folders that can be used with other tools.

        Args:
            account: Account alias to list folders for (optional). Defaults to the primary account.

        Returns:
            List of folder dictionaries with name, display_name, and attributes
        """
        return await self._client_for(account).list_folders()

    @mcp_tool(name="count-daily")
    async def count_daily(self, start_date: str, end_date: str, account: Optional[str] = None) -> Dict[str, int]:
        """Count emails received for each day in a date range.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            account: Account alias to count in (optional). Defaults to the primary account.

        Returns:
            Dictionary mapping dates to email counts
        """
        # Validate date formats
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")

        return await self._client_for(account).count_daily_emails(start_date, end_date)

    @mcp_tool(name="count")
    async def count(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        subject: Optional[str] = None,
        sender: Optional[str] = None,
        to: Optional[str] = None,
        body: Optional[str] = None,
        folder: str = "inbox",
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Count emails matching a filter WITHOUT creating a collection or fetching rows.

        Use this instead of mail-search when you only need a total (e.g. "how many from
        X?"). It creates no collection, so it never consumes a collection slot.

        Args:
            start_date: Start date YYYY-MM-DD (optional).
            end_date: End date YYYY-MM-DD (optional).
            subject: Substring to match in the subject (optional).
            sender: Substring to match in the sender (optional).
            to: Substring to match in the recipient (optional).
            body: Substring to match in the body (optional).
            folder: Folder to count in (defaults to 'inbox').
            account: Account alias (optional). Defaults to the primary account.

        Returns:
            {folder, count}
        """
        criteria = SearchCriteria(
            folder=folder,
            start_date=start_date,
            end_date=end_date,
            subject=subject,
            sender=sender,
            to=to,
            body=body,
        )
        total = await self._client_for(account).count_emails(criteria)
        return {"folder": folder, "count": total}

    @mcp_tool(name="aggregate")
    async def aggregate(
        self,
        group_by: Literal["sender", "recipient", "date"],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        subject: Optional[str] = None,
        sender: Optional[str] = None,
        to: Optional[str] = None,
        body: Optional[str] = None,
        folder: str = "inbox",
        top_n: int = 20,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Group matching emails server-side and return a small top-N frequency table.

        Answers "who emails me most?", "how many per day?", etc. in ONE call, without
        paging thousands of rows into context and without creating a collection. Only the
        grouping-key header is fetched; bodies are never retrieved.

        Args:
            group_by: Grouping dimension - 'sender', 'recipient', or 'date' (by day).
                'sender'/'recipient' are grouped by normalized (lowercased) email address.
            start_date: Start date YYYY-MM-DD (optional).
            end_date: End date YYYY-MM-DD (optional).
            subject: Substring to match in the subject (optional).
            sender: Substring to match in the sender (optional).
            to: Substring to match in the recipient (optional).
            body: Substring to match in the body (optional).
            folder: Folder to aggregate over (defaults to 'inbox').
            top_n: Number of most-frequent groups to return (defaults to 20).
            account: Account alias (optional). Defaults to the primary account.

        Returns:
            {group_by, folder, total_matched, total_grouped, distinct_keys, top_n,
             groups: [{key, count}, ...], truncated}
        """
        criteria = SearchCriteria(
            folder=folder,
            start_date=start_date,
            end_date=end_date,
            subject=subject,
            sender=sender,
            to=to,
            body=body,
        )
        return await self._client_for(account).aggregate_emails(criteria, group_by, top_n)

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
        # Export from whichever account the collection was searched in.
        account = collection["metadata"].get("account")

        # Extract email IDs from the collection
        if "id" not in df.columns:
            return {"error": "Collection does not contain email IDs (missing 'id' column)"}

        email_ids = df["id"].tolist()

        if not email_ids:
            return {"error": "Collection is empty, no emails to export"}

        # Export emails using the bulk method
        try:
            result = await self._client_for(account).export_emails_bulk(
                email_ids=email_ids,
                output_dir=output_dir,
                folder=source_folder,
                include_attachments=include_attachments,
            )
            return result
        except ValueError as e:
            return {"error": str(e)}

    @mcp_tool(name="move", capability="mailbox_write")
    async def move_emails(
        self,
        email_ids: Optional[List[str]] = None,
        destination_folder: str = "",
        source_folder: str = "inbox",
        account: Optional[str] = None,
        gmail_msgids: Optional[List[str]] = None,
    ) -> str:
        """Move one or more emails from one folder to another.

        Target messages by IMAP UID (`email_ids`) or by stable Gmail id (`gmail_msgids`,
        X-GM-MSGID, which survives folder moves). Provide exactly one. Prefer gmail_msgids
        when moving messages that may have been moved before — UIDs go stale across folders.

        Args:
            email_ids: IMAP UIDs to move
            destination_folder: Destination folder to move the emails to (required)
            source_folder: Source folder containing the emails (defaults to 'inbox')
            account: Account alias the emails belong to (optional). Defaults to the primary account.
            gmail_msgids: Stable Gmail message ids to move, resolved to current UIDs in source_folder.

        Returns:
            Message reporting how many emails were actually moved, plus any IDs that
            were not found in the source folder.
        """
        if not destination_folder:
            return "Error: destination_folder is required."
        result = await self._client_for(account).move_email(
            email_ids, source_folder, destination_folder, gmail_msgids=gmail_msgids
        )

        moved = len(result.affected)
        email_word = "email" if moved == 1 else "emails"
        lines = [
            f"Moved {moved} {email_word} from '{source_folder}' to '{destination_folder}'.",
            f"Moved IDs: {', '.join(result.affected)}",
        ]
        if result.not_found:
            lines.append(
                f"Not moved ({len(result.not_found)} not found in '{source_folder}'): " f"{', '.join(result.not_found)}"
            )
        return "\n".join(lines)

    @mcp_tool(name="delete", capability="mailbox_write")
    async def delete_emails(
        self,
        email_ids: Optional[List[str]] = None,
        folder: str = "inbox",
        permanent: bool = False,
        account: Optional[str] = None,
        gmail_msgids: Optional[List[str]] = None,
    ) -> str:
        """Delete one or more emails (move to trash by default, or permanently).

        Target messages by IMAP UID (`email_ids`) or by stable Gmail id (`gmail_msgids`,
        X-GM-MSGID, which survives folder moves). Provide exactly one.

        Args:
            email_ids: IMAP UIDs to delete
            folder: Folder containing the email(s) (defaults to 'inbox')
            permanent: If true, permanently delete. If false (default), move to trash
            account: Account alias the emails belong to (optional). Defaults to the primary account.
            gmail_msgids: Stable Gmail message ids to delete, resolved to current UIDs in folder.

        Returns:
            Message reporting how many emails were actually deleted, plus any IDs that
            were not found in the folder.
        """
        result = await self._client_for(account).delete_email(email_ids, folder, permanent, gmail_msgids=gmail_msgids)

        affected = len(result.affected)
        email_word = "email" if affected == 1 else "emails"

        if permanent:
            lines = [
                f"Permanently deleted {affected} {email_word} from '{folder}'.",
                "This action cannot be undone.",
                f"Deleted IDs: {', '.join(result.affected)}",
            ]
        else:
            lines = [
                f"Moved {affected} {email_word} to trash from '{folder}'.",
                f"The {email_word} can be restored from the trash folder if needed.",
                f"Moved IDs: {', '.join(result.affected)}",
            ]
        if result.not_found:
            lines.append(
                f"Not deleted ({len(result.not_found)} not found in '{folder}'): {', '.join(result.not_found)}"
            )
        return "\n".join(lines)


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
