"""Tests for UID-based email search pagination."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from email_client.config import EmailConfig
from email_client.email_client import EmailClient, SearchCriteria


@pytest.fixture
def client() -> EmailClient:
    return EmailClient(EmailConfig(email_address="person@example.com", email_password="secret"))


def _uid_search_mail(uids: bytes) -> MagicMock:
    mail = MagicMock()
    mail.uid.return_value = ("OK", [uids])
    return mail


@pytest.mark.asyncio
async def test_uid_pagination_oldest(client: EmailClient) -> None:
    mail = _uid_search_mail(b"10 20 30 40")
    ids, total = await client._search_with_pagination(
        mail,
        "ALL",
        SearchCriteria(max_results=2, start_from=1, direction="oldest"),
    )
    assert ids == [b"20", b"30"]
    assert total == 4
    mail.uid.assert_called_once_with("SEARCH", None, "ALL")


@pytest.mark.asyncio
async def test_uid_pagination_newest(client: EmailClient) -> None:
    mail = _uid_search_mail(b"10 20 30 40")
    ids, _ = await client._search_with_pagination(mail, "ALL", SearchCriteria(max_results=2))
    assert ids == [b"40", b"30"]


@pytest.mark.asyncio
async def test_out_of_bounds_retains_total(client: EmailClient) -> None:
    mail = _uid_search_mail(b"10 20")
    _, pagination = await client._execute_search(mail, "ALL", SearchCriteria(start_from=10))
    assert pagination.total_available == 2
    assert pagination.returned == 0
    assert pagination.next_start_from is None


@pytest.mark.asyncio
async def test_header_fetch_returns_uids_and_advances_by_consumed_ids(client: EmailClient) -> None:
    mail = MagicMock()
    mail.uid.side_effect = [
        ("OK", [b"101 102 103"]),
        (
            "OK",
            [
                (
                    b"1 (UID 103 BODY[HEADER.FIELDS (FROM DATE SUBJECT)] {50}",
                    b"From: a@example.com\r\nSubject: A\r\n\r\n",
                ),
                (
                    b"2 (UID 102 BODY[HEADER.FIELDS (FROM DATE SUBJECT)] {50}",
                    b"From: b@example.com\r\nSubject: B\r\n\r\n",
                ),
            ],
        ),
    ]

    emails, pagination = await client._execute_search(mail, "ALL", SearchCriteria(max_results=2))

    assert [item["id"] for item in emails] == ["103", "102"]
    assert pagination.returned == 2
    assert pagination.next_start_from == 2
    fetch_call = mail.uid.call_args_list[1]
    assert fetch_call.args[0] == "FETCH"
    assert "HEADER.FIELDS" in fetch_call.args[2]


@pytest.mark.asyncio
async def test_malformed_header_cannot_stall_pagination(client: EmailClient) -> None:
    mail = MagicMock()
    mail.uid.side_effect = [
        ("OK", [b"1 2 3"]),
        ("OK", [(b"malformed", b"Subject: broken\r\n\r\n")]),
    ]
    emails, pagination = await client._execute_search(mail, "ALL", SearchCriteria(max_results=1))
    assert emails == []
    assert pagination.next_start_from == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_results": 0},
        {"max_results": 501},
        {"start_from": -1},
        {"start_date": "2024-02-01", "end_date": "2024-01-01"},
        {"folder": ""},
    ],
)
def test_search_criteria_validation(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SearchCriteria(**kwargs)  # type: ignore[arg-type]
