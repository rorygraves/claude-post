"""Tests that paging past the end of a result set is reported honestly.

Paging past the end of a non-empty result set must not look like a failed search:
returning an error there risks the caller treating a valid end-of-pagination as a
failure and retrying.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from email_client.data_processing import DataStore
from email_client.email_client import PaginationInfo
from email_client.server import EmailMCPServer


def _server_with_result(email_list: list[dict], pagination: PaginationInfo) -> EmailMCPServer:
    client = MagicMock()
    client.email_address = "me@example.com"
    client.search_emails = AsyncMock(return_value=(email_list, pagination))
    return EmailMCPServer(
        clients={"default": client},
        primary_alias="default",
        datastore=DataStore(),
    )


@pytest.mark.asyncio
async def test_paging_past_end_is_not_an_error() -> None:
    server = _server_with_result(
        [], PaginationInfo(total_available=50, returned=0, start_from=100, has_more=False, next_start_from=None)
    )
    result = await server.search(start_from=100)
    assert "error" not in result
    assert "message" in result
    assert result["pagination"]["total_available"] == 50
    assert result["pagination"]["has_more"] is False


@pytest.mark.asyncio
async def test_no_matches_is_still_an_error() -> None:
    server = _server_with_result(
        [], PaginationInfo(total_available=0, returned=0, start_from=0, has_more=False, next_start_from=None)
    )
    result = await server.search()
    assert "error" in result
    assert result["pagination"]["total_available"] == 0
