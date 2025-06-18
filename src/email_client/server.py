"""Email MCP Server using the annotation-based framework.

This is an example of how to refactor the email server using the new
annotation-based MCP framework.
"""

import argparse
from datetime import datetime
from typing import Dict, List, Optional, Literal, Any

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
    
    def __init__(self, enable_write_operations: bool = False):
        """Initialize the email MCP server.
        
        Args:
            enable_write_operations: Whether to enable move/delete operations
        """
        super().__init__("email", "0.1.0", tool_prefix="mail-")
        self.email_client = EmailClient()
        self.datastore = DataStore()
        self.write_operations_enabled = enable_write_operations
    
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Add email-specific command line arguments."""
        parser.add_argument(
            "--enable-write-operations",
            action="store_true",
            help="Enable write operations (move and delete emails). "
                 "By default, only read operations are available for safety."
        )
    
    @mcp_tool(name="search")
    async def search(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        subject: Optional[str] = None,
        sender: Optional[str] = None,
        body: Optional[str] = None,
        folder: str = "inbox",
        max_results: int = 100,
        start_from: int = 0,
        collection_name: Optional[str] = None,
        direction: Literal["newest", "oldest"] = "newest"
    ) -> Dict[str, Any]:
        """Search emails and automatically create a data collection from the results.
        
        Returns collection metadata only (ID, name, shape, columns, creation time) - NOT individual email data.
        
        Args:
            start_date: Start date in YYYY-MM-DD format (optional). Filters emails received on or after this date.
            end_date: End date in YYYY-MM-DD format (optional). Filters emails received on or before this date.
            subject: Text to search for in email subject line (optional). Case-insensitive substring match.
            sender: Text to search for in sender email address or display name (optional). Case-insensitive substring match.
            body: Text to search for in email body content (optional). Case-insensitive substring match.
            folder: Email folder to search in (optional, defaults to 'inbox'). Use 'list-folders' to see available folders.
            max_results: Maximum number of emails to include in the collection (optional, defaults to 100, max 1000).
            start_from: Starting position for pagination (optional, defaults to 0).
            collection_name: Optional descriptive name for the created collection.
            direction: Sort direction for email results (optional, defaults to 'newest').
        
        Returns:
            Collection metadata dictionary
        """
        criteria = SearchCriteria(
            folder=folder,  # type: ignore
            start_date=start_date,
            end_date=end_date,
            subject=subject,
            sender=sender,
            body=body,
            max_results=max_results,
            start_from=start_from,
            direction=direction,  # type: ignore
        )
        
        email_list = await self.email_client.search_emails(criteria)
        
        if not email_list:
            return {"error": "No emails found matching the criteria."}
        
        # Create collection from search results
        df = pd.DataFrame(email_list)
        collection_metadata = self.datastore.create(df, collection_name)
        
        return collection_metadata
    
    @mcp_tool(name="get-content")
    async def get_content(self, email_id: str) -> Dict[str, str]:
        """Get the full content of a specific email by its ID.
        
        Args:
            email_id: The ID of the email to retrieve
            
        Returns:
            Dictionary containing email details (from, to, date, subject, content)
        """
        email_content = await self.email_client.get_email_content(email_id)
        
        if email_content:
            return email_content
        else:
            return {"error": "No email content found."}
    
    @mcp_tool(name="send")
    async def send(
        self,
        to: List[str],
        subject: str,
        content: str,
        cc: Optional[List[str]] = None
    ) -> str:
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
        return "Email sent successfully! Check email_client.log for detailed logs."
    
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
    
    @mcp_tool(name="update")
    async def update(self, collection_id: str, operation: str) -> Dict[str, Any]:
        """Apply pandas operations to transform, filter, or analyze email collections.
        
        Examples:
        - Filter: 'df[df["sender"].str.contains("@company.com")]'
        - Group by sender: 'df.groupby("sender").size().reset_index(name="count")'
        - Date analysis: 'df["date"] = pd.to_datetime(df["date"]); df.resample("D", on="date").size()'
        - Remove columns: 'df.drop(columns=["content"])'
        
        Args:
            collection_id: ID of the collection to update
            operation: Pandas operation to apply. Must return a DataFrame. Use 'df' to reference the data.
            
        Returns:
            Updated collection metadata
        """
        metadata = self.datastore.update(collection_id, operation)
        return metadata
    
    @mcp_tool(name="fetch")
    async def fetch(
        self,
        collection_id: str,
        limit: int = 100,
        format: str = "records"
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
    async def list(self) -> List[Dict[str, Any]]:
        """List all available email data collections with their metadata.
        
        Returns:
            List of collection metadata dictionaries
        """
        return self.datastore.list_collections()
    
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
    async def combine(
        self,
        target_collection_id: str,
        source_collection_id: str
    ) -> Dict[str, Any]:
        """Combine two email collections by appending source to target.
        
        Collections must have identical column structure.
        
        Args:
            target_collection_id: ID of the collection to append to (will be modified)
            source_collection_id: ID of the collection to append from (unchanged)
            
        Returns:
            Updated target collection metadata
        """
        return self.datastore.combine(target_collection_id, source_collection_id)
    
    @mcp_tool(name="move")
    async def move(
        self,
        email_ids: List[str],
        destination_folder: str,
        source_folder: str = "inbox"
    ) -> str:
        """Move one or more emails from one folder to another.
        
        Args:
            email_ids: Array of email IDs to move
            destination_folder: Destination folder to move the emails to
            source_folder: Source folder containing the emails (defaults to 'inbox')
            
        Returns:
            Success message with details
        """
        if not self.write_operations_enabled:
            return "Move operations are disabled. Use --enable-write-operations flag to enable."
        
        await self.email_client.move_email(email_ids, source_folder, destination_folder)
        
        count = len(email_ids)
        email_word = "email" if count == 1 else "emails"
        
        return (
            f"Successfully moved {count} {email_word} from '{source_folder}' to '{destination_folder}'.\n"
            f"Moved IDs: {', '.join(email_ids)}"
        )
    
    @mcp_tool(name="delete")
    async def delete(
        self,
        email_ids: List[str],
        folder: str = "inbox",
        permanent: bool = False
    ) -> str:
        """Delete one or more emails (move to trash by default, or permanently).
        
        Args:
            email_ids: Array of email IDs to delete
            folder: Folder containing the email(s) (defaults to 'inbox')
            permanent: If true, permanently delete. If false (default), move to trash
            
        Returns:
            Success message with details
        """
        if not self.write_operations_enabled:
            return "Delete operations are disabled. Use --enable-write-operations flag to enable."
        
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
    # Create server instance to parse args
    temp_server = EmailMCPServer()
    parsed_args = temp_server.parse_args()
    
    # Create actual server with parsed arguments
    server = EmailMCPServer(enable_write_operations=parsed_args.enable_write_operations)
    server.main()


if __name__ == "__main__":
    main()