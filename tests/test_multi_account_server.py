"""Tests for per-account routing in the MCP email server."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from email_client.data_processing import DataStore
from email_client.email_client import PaginationInfo
from email_client.server import EmailMCPServer


def _make_client(email_address: str, email_id: str) -> MagicMock:
    client = MagicMock()
    client.email_address = email_address
    client.search_emails = AsyncMock(
        return_value=(
            [{"id": email_id, "from": email_address, "date": "2024-01-01", "subject": "Hi"}],
            PaginationInfo(1, 1, 0, False, None),
        )
    )
    client.list_folders = AsyncMock(return_value=[])
    client.send_email = AsyncMock()
    return client


@pytest.fixture
def two_account_server() -> tuple[EmailMCPServer, MagicMock, MagicMock]:
    work = _make_client("work@example.com", "1")
    personal = _make_client("me@gmail.com", "2")
    server = EmailMCPServer(
        enable_send_operations=True,
        clients={"work": work, "personal": personal},
        primary_alias="work",
        datastore=DataStore(),
    )
    return server, work, personal


@pytest.mark.asyncio
async def test_accounts_tool_lists_registry(two_account_server) -> None:
    server, _, _ = two_account_server
    accounts = await server.accounts()
    by_alias = {row["alias"]: row for row in accounts}
    assert by_alias["work"]["primary"] is True
    assert by_alias["personal"]["primary"] is False
    assert by_alias["personal"]["email_address"] == "me@gmail.com"


@pytest.mark.asyncio
async def test_search_defaults_to_primary_account(two_account_server) -> None:
    server, work, personal = two_account_server
    result = await server.search(sender="x")
    work.search_emails.assert_awaited_once()
    personal.search_emails.assert_not_awaited()
    assert result["account"] == "work"


@pytest.mark.asyncio
async def test_search_routes_to_named_account_and_tags_collection(two_account_server) -> None:
    server, work, personal = two_account_server
    result = await server.search(sender="x", account="personal")
    personal.search_emails.assert_awaited_once()
    work.search_emails.assert_not_awaited()
    assert result["account"] == "personal"


@pytest.mark.asyncio
async def test_unknown_account_is_rejected(two_account_server) -> None:
    server, _, _ = two_account_server
    with pytest.raises(ValueError, match="Unknown account 'ghost'"):
        await server.search(sender="x", account="ghost")


@pytest.mark.asyncio
async def test_send_uses_selected_account(two_account_server) -> None:
    server, work, personal = two_account_server
    await server.send(["to@example.com"], "Subj", "Body", account="personal")
    personal.send_email.assert_awaited_once()
    work.send_email.assert_not_awaited()
