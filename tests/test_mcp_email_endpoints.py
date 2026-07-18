"""Tests for the current annotation-based MCP email server."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from email_client.email_client import MailboxOperationResult, PaginationInfo
from email_client.server import EmailMCPServer


@pytest.fixture
def fake_client() -> MagicMock:
    client = MagicMock()
    client.search_emails = AsyncMock(
        return_value=(
            [{"id": "42", "from": "a@example.com", "date": "2024-01-01", "subject": "Test"}],
            PaginationInfo(1, 1, 0, False, None),
        )
    )
    client.get_email_content = AsyncMock(return_value={"subject": "Test", "content": "Body"})
    client.get_email_contents_bulk = AsyncMock(return_value={"emails": [], "fetched": 0, "errors": []})
    client.list_folders = AsyncMock(return_value=[{"name": "Archive", "display_name": "Archive", "attributes": ""}])
    client.count_daily_emails = AsyncMock(return_value={"2024-01-01": 1})
    client.send_email = AsyncMock()
    client.move_email = AsyncMock(return_value=MailboxOperationResult.from_request(["10"], ["10"]))
    client.delete_email = AsyncMock(return_value=MailboxOperationResult.from_request(["11"], ["11"]))
    return client


def test_default_tool_set_is_read_only(fake_client: MagicMock) -> None:
    server = EmailMCPServer(email_client=fake_client)
    assert "mail-send" not in server._tools
    assert "mail-download-attachment" not in server._tools
    assert "mail-export" not in server._tools
    assert "mail-move" not in server._tools
    assert "mail-delete" not in server._tools


def test_capability_flags_expose_only_requested_tools(fake_client: MagicMock) -> None:
    send = EmailMCPServer(enable_send_operations=True, email_client=fake_client)
    files = EmailMCPServer(enable_file_operations=True, email_client=fake_client)
    mailbox = EmailMCPServer(enable_write_operations=True, email_client=fake_client)
    assert "mail-send" in send._tools
    assert {"mail-download-attachment", "mail-export"} <= files._tools.keys()
    assert {"mail-move", "mail-delete"} <= mailbox._tools.keys()


@pytest.mark.asyncio
async def test_search_supports_arbitrary_folder(fake_client: MagicMock) -> None:
    server = EmailMCPServer(email_client=fake_client)
    result = await server.search(folder="Archive", max_results=10)
    criteria = fake_client.search_emails.await_args.args[0]
    assert criteria.folder == "Archive"
    assert result["pagination"]["total_available"] == 1


@pytest.mark.asyncio
async def test_get_content_bulk(fake_client: MagicMock) -> None:
    server = EmailMCPServer(email_client=fake_client)
    await server.get_content(email_ids=["1", "2"])
    fake_client.get_email_contents_bulk.assert_awaited_once_with(["1", "2"], "inbox")


@pytest.mark.asyncio
async def test_enabled_side_effect_tools_delegate(fake_client: MagicMock) -> None:
    server = EmailMCPServer(
        enable_send_operations=True,
        enable_write_operations=True,
        email_client=fake_client,
    )
    await server.send(["to@example.com"], "Subject", "Body")
    await server.move_emails(["10"], "Archive")
    await server.delete_emails(["11"], permanent=True)
    fake_client.send_email.assert_awaited_once()
    fake_client.move_email.assert_awaited_once_with(["10"], "inbox", "Archive")
    fake_client.delete_email.assert_awaited_once_with(["11"], "inbox", True)


@pytest.mark.asyncio
async def test_move_reports_partial_result_not_the_request(fake_client: MagicMock) -> None:
    # Requested 3, but only "10" existed in the source folder.
    fake_client.move_email = AsyncMock(return_value=MailboxOperationResult.from_request(["10", "11", "12"], ["10"]))
    server = EmailMCPServer(enable_write_operations=True, email_client=fake_client)
    message = await server.move_emails(["10", "11", "12"], "Archive", source_folder="[Gmail]/Bin")
    assert "Moved 1 email from '[Gmail]/Bin'" in message
    assert "Moved IDs: 10" in message
    assert "Not moved (2 not found in '[Gmail]/Bin'): 11, 12" in message


@pytest.mark.asyncio
async def test_delete_reports_partial_result(fake_client: MagicMock) -> None:
    fake_client.delete_email = AsyncMock(return_value=MailboxOperationResult.from_request(["1", "2"], ["1"]))
    server = EmailMCPServer(enable_write_operations=True, email_client=fake_client)
    message = await server.delete_emails(["1", "2"], permanent=False)
    assert "Moved 1 email to trash" in message
    assert "Not deleted (1 not found in 'inbox'): 2" in message
