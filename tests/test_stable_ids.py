"""Tests for targeting emails by stable Gmail message id (X-GM-MSGID)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from email_client.config import EmailConfig
from email_client.email_client import EmailClient


def _config() -> EmailConfig:
    return EmailConfig(email_address="p@example.com", email_password="secret")


def _client_with_mail(mail: MagicMock, *, gmail_ext: bool = True) -> EmailClient:
    client = EmailClient(_config())
    client.connect_imap = AsyncMock(return_value=mail)  # type: ignore[method-assign]
    client.close_imap_connection = AsyncMock()  # type: ignore[method-assign]
    client._select_folder = AsyncMock()  # type: ignore[method-assign]
    client._validate_destination_folder = AsyncMock()  # type: ignore[method-assign]
    client._get_capability_set = AsyncMock(return_value={"MOVE", "UIDPLUS"})  # type: ignore[method-assign]
    client._supports_gmail_extensions = AsyncMock(return_value=gmail_ext)  # type: ignore[method-assign]
    client._get_trash_folder_name = AsyncMock(return_value='"[Gmail]/Trash"')  # type: ignore[method-assign]
    return client


def _gmail_search_mail(msgid_to_uid: dict[str, bytes]) -> MagicMock:
    """A mail mock that answers X-GM-MSGID and UID searches from a mapping."""
    mail = MagicMock()

    def uid_side_effect(command: str, *args: object):
        if command == "SEARCH":
            criteria = str(args[-1])
            if criteria.startswith("X-GM-MSGID"):
                msgid = criteria.split()[-1]
                uid = msgid_to_uid.get(msgid)
                return ("OK", [uid]) if uid else ("OK", [b""])
            if criteria.startswith("UID"):
                # _filter_existing_uids: echo back the requested uids as existing.
                requested = criteria.split(" ", 1)[1].replace(",", " ").encode()
                return ("OK", [requested])
        return ("OK", [b""])

    mail.uid.side_effect = uid_side_effect
    return mail


@pytest.mark.asyncio
async def test_resolve_gmail_msgids_reports_resolved_and_unresolved() -> None:
    mail = _gmail_search_mail({"111": b"55", "222": b""})
    client = _client_with_mail(mail)
    resolved, unresolved = await client._resolve_gmail_msgids(mail, ["111", "222"])
    assert resolved == {"111": "55"}
    assert unresolved == ["222"]


@pytest.mark.asyncio
async def test_resolve_requires_gmail_extensions() -> None:
    mail = _gmail_search_mail({"111": b"55"})
    client = _client_with_mail(mail, gmail_ext=False)
    with pytest.raises(Exception, match="Gmail extensions"):
        await client._resolve_gmail_msgids(mail, ["111"])


@pytest.mark.asyncio
async def test_move_by_gmail_msgid_reports_in_gmail_id_space() -> None:
    mail = _gmail_search_mail({"111": b"55"})
    client = _client_with_mail(mail)
    result = await client.move_email(source_folder="[Gmail]/Bin", destination_folder="INBOX", gmail_msgids=["111"])
    # Reported against the caller's identifier (the gmail id), not the resolved UID.
    assert result.affected == ["111"]
    assert result.not_found == []
    move_calls = [c for c in mail.uid.call_args_list if c.args[0] == "MOVE"]
    assert move_calls and move_calls[0].args[1] == "55"  # resolved UID was moved


@pytest.mark.asyncio
async def test_move_by_gmail_msgid_unresolved_is_not_found() -> None:
    mail = _gmail_search_mail({"111": b""})  # resolves to nothing
    client = _client_with_mail(mail)
    with pytest.raises(Exception, match="were found"):
        await client.move_email(source_folder="inbox", destination_folder="Archive", gmail_msgids=["111"])


@pytest.mark.asyncio
async def test_delete_by_gmail_msgid_to_trash() -> None:
    mail = _gmail_search_mail({"999": b"70"})
    client = _client_with_mail(mail)
    result = await client.delete_email(folder="inbox", permanent=False, gmail_msgids=["999"])
    assert result.affected == ["999"]
    move_calls = [c for c in mail.uid.call_args_list if c.args[0] == "MOVE"]
    assert move_calls and move_calls[0].args[1] == "70"


@pytest.mark.asyncio
async def test_move_rejects_both_identifier_kinds() -> None:
    client = _client_with_mail(_gmail_search_mail({}))
    with pytest.raises(Exception, match="not both"):
        await client.move_email(email_ids=["1"], destination_folder="Archive", gmail_msgids=["2"])


@pytest.mark.asyncio
async def test_get_content_requires_exactly_one_identifier() -> None:
    client = _client_with_mail(_gmail_search_mail({}))
    with pytest.raises(ValueError, match="exactly one"):
        await client.get_email_content(email_id="1", gmail_msgid="2")


@pytest.mark.asyncio
async def test_get_content_by_gmail_msgid_not_found_raises() -> None:
    mail = _gmail_search_mail({"5": b""})  # does not resolve
    client = _client_with_mail(mail)
    with pytest.raises(Exception, match="not found"):
        await client.get_email_content(folder="inbox", gmail_msgid="5")


def test_validate_gmail_msgids_rejects_non_numeric() -> None:
    with pytest.raises(ValueError, match="X-GM-MSGID"):
        EmailClient._validate_gmail_msgids(["not-a-number"])
