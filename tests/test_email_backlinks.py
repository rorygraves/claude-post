"""Regression tests for stable Gmail and RFC-822 email backlinks."""

from __future__ import annotations

from email.message import EmailMessage as MIMEEmailMessage
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from email_client.config import EmailConfig
from email_client.email_client import GMAIL_METADATA_FETCH, EmailClient, PaginationInfo, SearchCriteria
from email_client.server import EmailMCPServer


@pytest.fixture
def client() -> EmailClient:
    return EmailClient(EmailConfig(email_address="person@example.com", email_password="secret"))


def _raw_message(message_id: str | None, subject: str = "Backlink test") -> bytes:
    message = MIMEEmailMessage()
    message["From"] = "Sender <sender@example.com>"
    message["To"] = "person@example.com"
    message["Subject"] = subject
    if message_id is not None:
        message["Message-ID"] = message_id
    message.set_content("Body")
    return message.as_bytes()


def _mock_connected_client(client: EmailClient, mail: MagicMock) -> None:
    client.connect_imap = AsyncMock(return_value=mail)  # type: ignore[method-assign]
    client._select_folder = AsyncMock()  # type: ignore[method-assign]
    client.close_imap_connection = AsyncMock()  # type: ignore[method-assign]


def test_permalink_preserves_unsigned_64_bit_ids_as_strings(client: EmailClient) -> None:
    fields = client._build_backlink_fields(
        message_id="<fallback@example.com>",
        gmail_msgid="18446744073709551615",
        gmail_thrid="9007199254740993",
    )

    assert fields["gmail_msgid"] == "18446744073709551615"
    assert fields["gmail_thrid"] == "9007199254740993"
    assert isinstance(fields["gmail_msgid"], str)
    assert fields["gmail_url"] == ("https://mail.google.com/mail/u/0/?authuser=person@example.com#all/ffffffffffffffff")


def test_permalink_is_independent_of_folder_scoped_uid(client: EmailClient) -> None:
    before_move = client._build_gmail_url("12345678901234567890", "<message@example.com>")
    after_move = client._build_gmail_url("12345678901234567890", "<message@example.com>")
    assert before_move == after_move


def test_rfc822_fallback_url_strips_brackets_and_encodes_message_id(client: EmailClient) -> None:
    fields = client._build_backlink_fields(message_id="<abc+tag@example.com>")
    assert fields == {
        "message_id": "<abc+tag@example.com>",
        "gmail_msgid": None,
        "gmail_thrid": None,
        "gmail_url": (
            "https://mail.google.com/mail/u/0/?authuser=person@example.com#search/rfc822msgid:abc%2Btag%40example.com"
        ),
    }


def test_missing_message_id_is_non_fatal(client: EmailClient) -> None:
    content = client._format_email_content(((b"1", _raw_message(None)),))
    assert content["message_id"] is None
    assert content["gmail_msgid"] is None
    assert content["gmail_thrid"] is None
    assert content["gmail_url"] is None


@pytest.mark.asyncio
async def test_single_content_fetch_adds_gmail_metadata_with_peek(client: EmailClient) -> None:
    mail = MagicMock()
    mail.capability.return_value = ("OK", [b"IMAP4REV1 X-GM-EXT-1"])
    mail.uid.side_effect = [
        ("OK", [(b"1 (UID 42 BODY[] {100}", _raw_message("<single@example.com>"))]),
        (
            "OK",
            [
                (
                    b"1 (UID 42 X-GM-MSGID 12345678901234567890 X-GM-THRID 9007199254740993)",
                    b"Message-ID: <single@example.com>\r\n\r\n",
                )
            ],
        ),
    ]
    _mock_connected_client(client, mail)

    content = await client.get_email_content("42")

    assert content is not None
    assert content["message_id"] == "<single@example.com>"
    assert content["gmail_msgid"] == "12345678901234567890"
    assert content["gmail_thrid"] == "9007199254740993"
    assert content["gmail_url"].endswith("#all/ab54a98ceb1f0ad2")
    assert mail.uid.call_args_list == [
        call("FETCH", "42", "(UID BODY.PEEK[])"),
        call("FETCH", "42", GMAIL_METADATA_FETCH),
    ]


@pytest.mark.asyncio
async def test_bulk_content_fetch_adds_metadata_to_each_message(client: EmailClient) -> None:
    mail = MagicMock()
    mail.capability.return_value = ("OK", [b"IMAP4REV1 X-GM-EXT-1"])
    mail.uid.side_effect = [
        (
            "OK",
            [
                (b"1 (UID 10 BODY[] {100}", _raw_message("<ten@example.com>")),
                (b"2 (UID 11 BODY[] {100}", _raw_message("<eleven@example.com>")),
            ],
        ),
        (
            "OK",
            [
                (
                    b"1 (UID 10 X-GM-MSGID 9007199254740995 X-GM-THRID 9007199254740997)",
                    b"Message-ID: <ten@example.com>\r\n\r\n",
                ),
                (
                    b"2 (UID 11 X-GM-MSGID 9007199254740996 X-GM-THRID 9007199254740998)",
                    b"Message-ID: <eleven@example.com>\r\n\r\n",
                ),
            ],
        ),
    ]
    _mock_connected_client(client, mail)

    result = await client.get_email_contents_bulk(["10", "11"])

    assert [item["gmail_msgid"] for item in result["emails"]] == ["9007199254740995", "9007199254740996"]
    assert [item["message_id"] for item in result["emails"]] == ["<ten@example.com>", "<eleven@example.com>"]
    assert mail.uid.call_args_list[-1] == call("FETCH", "10,11", GMAIL_METADATA_FETCH)


@pytest.mark.asyncio
async def test_non_gmail_content_keeps_message_id_and_uses_fallback(client: EmailClient) -> None:
    mail = MagicMock()
    mail.capability.return_value = ("OK", [b"IMAP4REV1 UIDPLUS"])
    mail.uid.return_value = (
        "OK",
        [(b"1 (UID 42 BODY[] {100}", _raw_message("<portable@example.com>"))],
    )
    _mock_connected_client(client, mail)

    content = await client.get_email_content("42")

    assert content is not None
    assert content["message_id"] == "<portable@example.com>"
    assert content["gmail_msgid"] is None
    assert content["gmail_thrid"] is None
    assert content["gmail_url"].endswith("#search/rfc822msgid:portable%40example.com")
    mail.uid.assert_called_once_with("FETCH", "42", "(UID BODY.PEEK[])")


@pytest.mark.asyncio
async def test_search_collection_includes_string_gmail_msgid(client: EmailClient) -> None:
    mail = MagicMock()
    mail.capability.return_value = ("OK", [b"IMAP4REV1 X-GM-EXT-1"])
    mail.uid.side_effect = [
        ("OK", [b"77"]),
        (
            "OK",
            [
                (
                    b"1 (UID 77 X-GM-MSGID 9007199254740999 BODY[HEADER.FIELDS (FROM SUBJECT)] {50}",
                    b"From: sender@example.com\r\nSubject: Stable link\r\n\r\n",
                )
            ],
        ),
    ]

    emails, _ = await client._execute_search(mail, "ALL", SearchCriteria(max_results=1))

    assert emails[0]["gmail_msgid"] == "9007199254740999"
    assert isinstance(emails[0]["gmail_msgid"], str)
    assert "X-GM-MSGID" in mail.uid.call_args_list[-1].args[2]
    assert "BODY.PEEK" in mail.uid.call_args_list[-1].args[2]


@pytest.mark.asyncio
async def test_mail_search_collection_serializes_gmail_msgid_as_string() -> None:
    fake_client = MagicMock()
    fake_client.search_emails = AsyncMock(
        return_value=(
            [
                {
                    "id": "77",
                    "from": "sender@example.com",
                    "date": "2026-07-16",
                    "subject": "Stable link",
                    "gmail_msgid": "9007199254740999",
                }
            ],
            PaginationInfo(1, 1, 0, False, None),
        )
    )
    server = EmailMCPServer(email_client=fake_client)

    collection = await server.search(max_results=1)
    fetched = await server.fetch(collection["id"])

    assert fetched["data"][0]["gmail_msgid"] == "9007199254740999"
    assert isinstance(fetched["data"][0]["gmail_msgid"], str)
