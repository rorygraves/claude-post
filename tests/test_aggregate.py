"""Tests for server-side aggregation and counting (mail-aggregate / mail-count)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from email_client.config import EmailConfig
from email_client.email_client import EmailClient, SearchCriteria
from email_client.server import EmailMCPServer


def _config() -> EmailConfig:
    return EmailConfig(email_address="p@example.com", email_password="secret")


def _fetch_tuple(uid: int, header: str) -> tuple[bytes, bytes]:
    """Build a FETCH response tuple like imaplib returns for a header fetch."""
    return (f"{uid} (UID {uid} BODY[HEADER.FIELDS (FROM)] {{40}}".encode(), header.encode())


def _client_with_search(uids: bytes, fetch_response: list) -> EmailClient:
    client = EmailClient(_config())
    mail = MagicMock()

    def uid_side_effect(command: str, *_args: object):
        if command == "SEARCH":
            return ("OK", [uids])
        if command == "FETCH":
            return ("OK", fetch_response)
        return ("OK", [b""])

    mail.uid.side_effect = uid_side_effect
    client.connect_imap = AsyncMock(return_value=mail)  # type: ignore[method-assign]
    client.close_imap_connection = AsyncMock()  # type: ignore[method-assign]
    client._select_folder = AsyncMock()  # type: ignore[method-assign]
    client._build_search_criteria = AsyncMock(return_value="ALL")  # type: ignore[method-assign]
    return client


@pytest.mark.asyncio
async def test_aggregate_by_sender_normalizes_addresses() -> None:
    fetch = [
        _fetch_tuple(1, "From: Alice <alice@example.com>\r\n\r\n"),
        b")",
        _fetch_tuple(2, "From: Bob <bob@other.com>\r\n\r\n"),
        b")",
        _fetch_tuple(3, "From: Alice (work) <ALICE@example.com>\r\n\r\n"),
        b")",
    ]
    client = _client_with_search(b"1 2 3", fetch)
    result = await client.aggregate_emails(SearchCriteria(folder="inbox"), "sender", top_n=10)
    assert result["total_matched"] == 3
    assert result["total_grouped"] == 3
    assert result["distinct_keys"] == 2
    # Alice counted twice despite different display name / casing.
    assert result["groups"][0] == {"key": "alice@example.com", "count": 2}
    assert {"key": "bob@other.com", "count": 1} in result["groups"]
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_aggregate_top_n_truncates() -> None:
    fetch = [
        _fetch_tuple(1, "From: a@x.com\r\n\r\n"),
        b")",
        _fetch_tuple(2, "From: b@x.com\r\n\r\n"),
        b")",
        _fetch_tuple(3, "From: c@x.com\r\n\r\n"),
        b")",
    ]
    client = _client_with_search(b"1 2 3", fetch)
    result = await client.aggregate_emails(SearchCriteria(folder="inbox"), "sender", top_n=2)
    assert result["distinct_keys"] == 3
    assert len(result["groups"]) == 2
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_aggregate_by_date_uses_internaldate() -> None:
    fetch = [
        b'1 (UID 1 INTERNALDATE "17-Jul-2026 10:00:00 +0000")',
        b'2 (UID 2 INTERNALDATE "18-Jul-2026 09:00:00 +0000")',
        b'3 (UID 3 INTERNALDATE "17-Jul-2026 23:30:00 +0000")',
    ]
    client = _client_with_search(b"1 2 3", fetch)
    result = await client.aggregate_emails(SearchCriteria(folder="inbox"), "date", top_n=10)
    groups = {g["key"]: g["count"] for g in result["groups"]}
    assert groups == {"2026-07-17": 2, "2026-07-18": 1}


@pytest.mark.asyncio
async def test_aggregate_empty_folder() -> None:
    client = _client_with_search(b"", [])
    result = await client.aggregate_emails(SearchCriteria(folder="inbox"), "sender", top_n=10)
    assert result["total_matched"] == 0
    assert result["groups"] == []
    assert result["distinct_keys"] == 0


@pytest.mark.asyncio
async def test_aggregate_rejects_bad_group_by() -> None:
    client = _client_with_search(b"1", [])
    with pytest.raises(Exception, match="Unsupported group_by"):
        await client.aggregate_emails(SearchCriteria(folder="inbox"), "label", top_n=10)


@pytest.mark.asyncio
async def test_count_emails_creates_no_fetch() -> None:
    client = _client_with_search(b"1 2 3 4 5", [])
    total = await client.count_emails(SearchCriteria(folder="inbox", sender="x@y.com"))
    assert total == 5


@pytest.mark.asyncio
async def test_aggregate_tool_delegates_to_client() -> None:
    fake = MagicMock()
    fake.aggregate_emails = AsyncMock(return_value={"group_by": "sender", "groups": []})
    with patch.object(EmailMCPServer, "_client_for", return_value=fake):
        server = EmailMCPServer(email_client=fake)
        await server.aggregate(group_by="sender", sender="x", top_n=5)
    criteria = fake.aggregate_emails.await_args.args[0]
    assert criteria.sender == "x"
    assert fake.aggregate_emails.await_args.args[1:] == ("sender", 5)
