"""Tests for arrival-date-ordered email search pagination.

These cover the fix for positional pagination that previously ordered by raw IMAP
UID (folder-append order) rather than message arrival date, making ``direction``
unreliable as "the Nth most-recent email". When the server advertises SORT we now
order via ``UID SORT (ARRIVAL)``; otherwise we fall back to UID order.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from email_client.config import EmailConfig
from email_client.email_client import EmailClient, SearchCriteria


@pytest.fixture
def client() -> EmailClient:
    return EmailClient(EmailConfig(email_address="person@example.com", email_password="secret"))


def _sort_capable_mail(
    *,
    arrival_order: bytes,
    uid_order: bytes,
    capabilities: bytes = b"IMAP4rev1 UIDPLUS MOVE SORT X-GM-EXT-1",
) -> MagicMock:
    """Mock an IMAP connection advertising SORT with a distinct arrival order.

    ``arrival_order`` is what the server returns for ``UID SORT (ARRIVAL)`` — ascending
    by received date. It deliberately differs from ``uid_order`` (``UID SEARCH``) so a
    test can prove the code used the server's date order, not reversed UID order.
    """
    mail = MagicMock()
    mail.capability.return_value = ("OK", [capabilities])

    def uid(command: str, *_args: object) -> tuple[str, list[bytes]]:
        if command == "SORT":
            return ("OK", [arrival_order])
        if command == "SEARCH":
            return ("OK", [uid_order])
        raise AssertionError(f"unexpected UID {command}")

    mail.uid.side_effect = uid
    return mail


# Arrival (received) order ascending is UIDs [30, 10, 40, 20]: note it is NOT the UID
# order, so any test asserting on it proves the date sort was used.
_ARRIVAL_ASC = b"30 10 40 20"
_UID_ASC = b"10 20 30 40"


@pytest.mark.asyncio
async def test_newest_uses_server_arrival_order(client: EmailClient) -> None:
    mail = _sort_capable_mail(arrival_order=_ARRIVAL_ASC, uid_order=_UID_ASC)
    ids, total = await client._search_with_pagination(mail, "ALL", SearchCriteria(max_results=2, direction="newest"))
    # newest = reversed arrival order [20, 40, 10, 30] -> first two
    assert ids == [b"20", b"40"]
    assert total == 4
    # The server SORT command was issued with the arrival key...
    sort_calls = [c for c in mail.uid.call_args_list if c.args[0] == "SORT"]
    assert sort_calls
    assert sort_calls[0].args == ("SORT", "(ARRIVAL)", "UTF-8", "ALL")
    # ...and the result is genuinely date-ordered, not reversed-UID ([40, 30]).
    assert ids != [b"40", b"30"]


@pytest.mark.asyncio
async def test_oldest_uses_server_arrival_order(client: EmailClient) -> None:
    mail = _sort_capable_mail(arrival_order=_ARRIVAL_ASC, uid_order=_UID_ASC)
    ids, _ = await client._search_with_pagination(mail, "ALL", SearchCriteria(max_results=2, direction="oldest"))
    # oldest = arrival order as-is
    assert ids == [b"30", b"10"]


@pytest.mark.asyncio
async def test_windows_are_deterministic_and_non_overlapping(client: EmailClient) -> None:
    mail = _sort_capable_mail(arrival_order=_ARRIVAL_ASC, uid_order=_UID_ASC)
    page1, _ = await client._search_with_pagination(
        mail, "ALL", SearchCriteria(max_results=2, start_from=0, direction="newest")
    )
    page2, _ = await client._search_with_pagination(
        mail, "ALL", SearchCriteria(max_results=2, start_from=2, direction="newest")
    )
    assert page1 == [b"20", b"40"]
    assert page2 == [b"10", b"30"]
    # No re-served window; the two pages tile the full result set contiguously.
    assert set(page1).isdisjoint(page2)
    assert page1 + page2 == [b"20", b"40", b"10", b"30"]


@pytest.mark.asyncio
async def test_past_end_returns_empty_without_error(client: EmailClient) -> None:
    mail = _sort_capable_mail(arrival_order=_ARRIVAL_ASC, uid_order=_UID_ASC)
    ids, total = await client._search_with_pagination(
        mail, "ALL", SearchCriteria(max_results=2, start_from=10, direction="newest")
    )
    assert ids == []
    assert total == 4


@pytest.mark.asyncio
async def test_fallback_without_sort_uses_uid_order(client: EmailClient) -> None:
    mail = MagicMock()
    mail.capability.return_value = ("OK", [b"IMAP4rev1 UIDPLUS MOVE"])  # no SORT

    def uid(command: str, *_args: object) -> tuple[str, list[bytes]]:
        if command == "SEARCH":
            return ("OK", [_UID_ASC])
        raise AssertionError(f"unexpected UID {command}")

    mail.uid.side_effect = uid
    ids, total = await client._search_with_pagination(mail, "ALL", SearchCriteria(max_results=2, direction="newest"))
    # No SORT support: newest falls back to reversed UID order.
    assert ids == [b"40", b"30"]
    assert total == 4
    assert all(c.args[0] != "SORT" for c in mail.uid.call_args_list)


@pytest.mark.asyncio
async def test_supports_sort_detects_capability(client: EmailClient) -> None:
    with_sort = MagicMock()
    with_sort.capability.return_value = ("OK", [b"IMAP4rev1 SORT"])
    without_sort = MagicMock()
    without_sort.capability.return_value = ("OK", [b"IMAP4rev1 UIDPLUS"])
    assert await client._supports_sort(with_sort) is True
    assert await client._supports_sort(without_sort) is False
