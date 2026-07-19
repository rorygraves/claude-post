"""Regression tests for IMAP LIST-response folder parsing (roadmap #8).

Each case here corresponds to a raw response that the previous split('"') parser
either garbled or silently dropped. See parse_list_response_line.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from email_client.config import EmailConfig
from email_client.email_client import EmailClient, decode_imap_utf7, parse_list_response_line


@pytest.fixture
def client() -> EmailClient:
    return EmailClient(EmailConfig(email_address="x@example.com", email_password="pw"))


# --- decode_imap_utf7 (RFC 3501 modified UTF-7) -------------------------------


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ("INBOX", "INBOX"),  # plain ASCII passes through
        ("Travail", "Travail"),
        ("&AOk-tiquette", "étiquette"),  # shifted run
        ("&BEEENQRABDE-", "серб"),  # noqa: RUF001 — Cyrillic is the intended decode output
        ("Money &- Bills", "Money & Bills"),  # &- is a literal ampersand
        ("&", "&"),  # lone, unterminated shift left verbatim
    ],
)
def test_decode_imap_utf7(wire: str, expected: str) -> None:
    assert decode_imap_utf7(wire) == expected


# --- parse_list_response_line: the previously-broken cases --------------------


def test_standard_quoted_line() -> None:
    info = parse_list_response_line(b'(\\All \\HasNoChildren) "/" "[Gmail]/All Mail"')
    assert info == {
        "name": "[Gmail]/All Mail",
        "display_name": "All Mail",
        "attributes": "\\All \\HasNoChildren",
    }


def test_literal_name_is_not_dropped() -> None:
    # imaplib yields a (prefix, literal-name) tuple for {n} literals; the old parser
    # skipped these via isinstance(bytes), losing the folder entirely.
    info = parse_list_response_line((b'(\\All \\HasNoChildren) "/" {16}', b"[Gmail]/All Mail"))
    assert info is not None
    assert info["name"] == "[Gmail]/All Mail"
    assert info["display_name"] == "All Mail"


def test_unquoted_atom_name_and_nil_delimiter() -> None:
    info = parse_list_response_line(b"(\\HasNoChildren) NIL INBOX")
    assert info is not None
    assert info["name"] == "INBOX"


def test_escaped_quote_in_name_is_not_truncated() -> None:
    # Old behaviour: split('"') returned just "Inside".
    info = parse_list_response_line(b'(\\HasNoChildren) "/" "Quote\\"Inside"')
    assert info is not None
    assert info["name"] == 'Quote"Inside'


def test_escaped_backslash_in_name_is_unescaped() -> None:
    info = parse_list_response_line(b'(\\HasNoChildren) "/" "Weird\\\\Name"')
    assert info is not None
    assert info["name"] == "Weird\\Name"


def test_non_ascii_name_decoded_for_display_but_wire_kept_for_name() -> None:
    info = parse_list_response_line(b'(\\HasNoChildren) "/" "[Gmail]/&AOk-tiquette"')
    assert info is not None
    # name stays in wire form so it round-trips through quote_imap_mailbox in commands...
    assert info["name"] == "[Gmail]/&AOk-tiquette"
    # ...while the human-facing display name is decoded and de-prefixed.
    assert info["display_name"] == "étiquette"


def test_non_folder_entries_return_none() -> None:
    assert parse_list_response_line(None) is None
    assert parse_list_response_line(b'"') is None  # stray trailing literal fragment
    assert parse_list_response_line(b"garbage without parens") is None


# --- list_folders end-to-end with the fixed parser ---------------------------


def _client_listing(client: EmailClient, listing: list[object]) -> EmailClient:
    mail = MagicMock()
    mail.list.return_value = ("OK", listing)

    async def fake_connect() -> MagicMock:
        return mail

    async def fake_close(_m: object) -> None:
        return None

    client.connect_imap = fake_connect  # type: ignore[method-assign]
    client.close_imap_connection = fake_close  # type: ignore[method-assign]
    return client


@pytest.mark.asyncio
async def test_list_folders_recovers_literal_and_atom_folders(client: EmailClient) -> None:
    _client_listing(
        client,
        [
            (b'(\\All \\HasNoChildren) "/" {16}', b"[Gmail]/All Mail"),  # literal
            b"(\\HasNoChildren) NIL INBOX",  # unquoted atom
            b'(\\HasNoChildren) "/" "Work"',  # standard
        ],
    )
    folders = await client.list_folders()
    names = {f["name"] for f in folders}
    # All three survive; none silently dropped.
    assert names == {"[Gmail]/All Mail", "INBOX", "Work"}
