"""Tests that mail-fetch never truncates silently.

Previously the fetch tool defaulted to limit=100 and dropped row 101 with only a
subtle 'truncated' boolean. It now returns all rows by default, bounded by a loud
FETCH_ROW_CAP, and reports any truncation via an explicit 'warning'.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from email_client.data_processing import DataStore
from email_client.server import FETCH_ROW_CAP, EmailMCPServer


def _server_with_collection(rows: int) -> tuple[EmailMCPServer, str]:
    ds = DataStore()
    df = pd.DataFrame({"id": [str(i) for i in range(rows)], "subject": [f"s{i}" for i in range(rows)]})
    metadata = ds.create(df, "c")
    server = EmailMCPServer(clients={"default": MagicMock()}, primary_alias="default", datastore=ds)
    return server, metadata["id"]


@pytest.mark.asyncio
async def test_fetch_returns_all_rows_by_default_when_under_cap() -> None:
    server, cid = _server_with_collection(150)
    result = await server.fetch(cid)
    # The old default of 100 would have dropped 50 rows silently; now all 150 come back.
    assert result["returned"] == 150
    assert result["total_rows"] == 150
    assert result["truncated"] is False
    assert "warning" not in result
    assert len(result["data"]) == 150


@pytest.mark.asyncio
async def test_fetch_applies_loud_cap_when_over_limit() -> None:
    server, cid = _server_with_collection(FETCH_ROW_CAP + 25)
    result = await server.fetch(cid)
    assert result["returned"] == FETCH_ROW_CAP
    assert result["total_rows"] == FETCH_ROW_CAP + 25
    assert result["truncated"] is True
    # The truncation is loud, not silent.
    assert "warning" in result
    assert str(FETCH_ROW_CAP) in result["warning"]
    assert "25 not shown" in result["warning"]


@pytest.mark.asyncio
async def test_explicit_limit_truncation_is_reported() -> None:
    server, cid = _server_with_collection(10)
    result = await server.fetch(cid, limit=4)
    assert result["returned"] == 4
    assert result["total_rows"] == 10
    assert result["truncated"] is True
    assert "warning" in result
    assert "6 more not shown" in result["warning"]


@pytest.mark.asyncio
async def test_explicit_limit_covering_all_rows_has_no_warning() -> None:
    server, cid = _server_with_collection(10)
    result = await server.fetch(cid, limit=10)
    assert result["returned"] == 10
    assert result["truncated"] is False
    assert "warning" not in result
